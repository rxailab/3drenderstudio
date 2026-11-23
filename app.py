"""物影 Studio: local-only Blender product photography application."""
import io
import json
import os
import re
import shutil
import struct
import time
import uuid
import zipfile
from urllib.parse import unquote
from pathlib import Path, PurePosixPath
from flask import Flask,request,jsonify,send_from_directory,send_file,abort,g
from werkzeug.exceptions import HTTPException
from studio_store import ROOT,DATA,LOCK,get,put,all_records,atomic_json
import app_log

env=ROOT/'.env'
if env.exists():
    for line in env.read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"'))

import engine
import scene_exports
import ai_config
import project_lifecycle
import viewport_service
import ecommerce
import ecommerce_platforms
from planner import default_plan,validate,instruct,PRESETS
app=Flask(__name__,static_folder='static')
app.config['MAX_CONTENT_LENGTH']=512*1024*1024
ALLOWED={'.obj','.mtl','.glb','.png','.jpg','.jpeg','.webp','.tga','.bmp','.tif','.tiff','.exr','.hdr','.json','.txt'}

@app.errorhandler(Exception)
def error(exc):
    if isinstance(exc,HTTPException): return jsonify(error=exc.description),exc.code
    if isinstance(exc,KeyError): return jsonify(error='记录不存在'),404
    if isinstance(exc,(ValueError,TypeError)): return jsonify(error=str(exc)),400
    # Only the method and path: the headers (Authorization) and the body are never logged; app_log scrubs key-like text.
    app_log.get('http').exception('%s %s failed',request.method,request.path)
    return jsonify(error=str(exc)),500

@app.before_request
def local_guard():
    # Bound to loopback; additionally reject cross-site browser writes / DNS rebinding.
    if request.host.split(':')[0] not in ['localhost','127.0.0.1','[::1]']: abort(403)
    origin=request.headers.get('Origin')
    if request.method not in ['GET','HEAD','OPTIONS'] and origin and origin not in ['http://'+request.host,'https://'+request.host]: abort(403)

YIELDING_ENDPOINTS={'plan','undo','instruction','instruction_confirm','feedback_apply','review_propose','review_apply','hero_optimize',
                    'material_import','selected_material_apply','material_agent_create','ecommerce_adapt'}

@app.before_request
def yield_background_polish():
    """A request that changes or renders the project takes the renderer from background polish."""
    deleting=request.method=='DELETE' and request.endpoint=='project_delete'
    if not deleting and (request.method!='POST' or request.endpoint not in YIELDING_ENDPOINTS): return
    ident=(request.view_args or {}).get('ident')
    if isinstance(ident,str):
        try: stopped=engine.yield_to_user(ident)
        except KeyError: return
        if stopped and deleting:
            # Deletion needs the renderer to have let go of the project's files.
            deadline=time.time()+5
            while time.time()<deadline and any(job.get('project_id')==ident and job['id'] in engine.RUNNING_JOBS for job in all_records('job')):
                time.sleep(.1)

@app.before_request
def reserve_project_operation():
    """A file/AI request can outlive its first DB read; deletion must wait for it."""
    if request.endpoint=='project_delete' or request.url_rule is None: return
    rule=request.url_rule.rule
    writes=request.method not in ('GET','HEAD','OPTIONS')
    needs_reservation=writes or request.endpoint in ('bundle','material_agent_list','material_agent_read')
    if not needs_reservation: return
    args=request.view_args or {};ident=args.get('ident');project_id=None
    with LOCK:
        if rule.startswith('/api/projects/<ident>'): project_id=ident
        elif rule.startswith('/api/jobs/<ident>'): project_id=get('job',ident).get('project_id')
        elif rule.startswith('/api/exports/<ident>'): project_id=get('export',ident).get('project_id')
        elif rule.startswith('/api/ecommerce/exports/<ident>'): project_id=get(ecommerce.KIND,ident).get('project_id')
        if project_id:
            project_lifecycle.begin_operation(project_id)
            g.project_operation=project_id

@app.teardown_request
def release_project_operation(error=None):
    project_id=g.pop('project_operation',None)
    if project_id: project_lifecycle.end_operation(project_id)

@app.get('/')
def index(): return send_from_directory(ROOT/'static','index.html')

@app.get('/api/system')
def system():
    exe=engine.blender_path()
    runtime=engine.runtime_status()
    ai=ai_config.public_config()
    return jsonify(blender=exe,available=runtime['available'],blender_available=runtime['blender_available'],runtime=runtime,ai_mode='ai' if ai.get('request_allowed',False) else 'rules',ai=ai,device=engine.DEVICE,presets=PRESETS,scene_exports=scene_exports.capabilities(),viewport=viewport_service.capabilities())

@app.route('/api/ai/settings',methods=['GET','PUT'])
def ai_settings():
    value=ai_config.save_config(request.get_json(force=True)) if request.method=='PUT' else ai_config.public_config()
    response=jsonify(value);response.headers['Cache-Control']='no-store'
    return response

@app.post('/api/ai/test')
def ai_test():
    from ai_client import test_connection
    config=ai_config.get_config()
    if not config['api_key'] or not config['model']:
        return jsonify(success=False,message='请先保存 Access Token 和模型 ID',settings=ai_config.public_config())
    try:
        result=test_connection(config)
        ai_config.record_result(config,True,result['message'])
        return jsonify(success=True,**result,settings=ai_config.public_config())
    except ValueError as exc:
        ai_config.record_result(config,False,str(exc))
        return jsonify(success=False,message=str(exc),settings=ai_config.public_config())

@app.post('/api/ai/vision/test')
def ai_vision_test():
    """Test visual understanding with an existing real preview, never an invented image."""
    from ai_client import test_vision_connection
    config=ai_config.get_vision_config()
    if not config.get('enabled') or not config.get('api_key') or not config.get('model'):
        return jsonify(success=False,message='请先配置视觉模型和可用的 Access Token',settings=ai_config.public_config())
    candidates=[]
    for project in all_records('project'):
        for output in project.get('outputs',[]):
            url=output.get('png_url','')
            if output.get('quality')!='preview' or not isinstance(url,str) or not url.startswith('/files/'): continue
            path=(DATA/url[7:]).resolve()
            if (DATA/project['id']).resolve() not in path.parents or not path.is_file(): continue
            candidates.append((output.get('created_at',0),path))
    if not candidates:
        return jsonify(success=False,message='请先生成一张真实预览，再测试视觉模型',settings=ai_config.public_config())
    try:
        path=max(candidates,key=lambda item:item[0])[1]
        engine.verify_image(path,'PNG')
        result=test_vision_connection(config,path)
        ai_config.record_vision_result(config,True,result['message'])
        return jsonify(success=True,**result,settings=ai_config.public_config())
    except (ValueError,RuntimeError,OSError) as exc:
        ai_config.record_vision_result(config,False,str(exc))
        return jsonify(success=False,message=str(exc),settings=ai_config.public_config())

@app.get('/api/projects')
def projects():
    from instruction_service import public_project
    # Home cards show background work without loading every project's job list.
    active={}
    for job in all_records('job'):
        if job.get('status') not in ('queued','running'): continue
        known=active.get(job.get('project_id'))
        if known is None or (known['status']=='queued' and job['status']=='running'):
            active[job.get('project_id')]={key:job.get(key) for key in ('id','kind','quality','status','progress','origin')}
    if request.args.get('fields')=='summary':
        # The home poll needs only what the cards and the sidebar draw; full records ran to megabytes.
        return conditional(jsonify([{**project_summary(item),'active_job':active.get(item.get('id'))} for item in all_records('project')]))
    return jsonify([{**public_project(item),'active_job':active.get(item.get('id'))} for item in all_records('project')])

def project_summary(project):
    """A library card: the image to show, the status line and the counts, nothing the card does not read."""
    initial=project.get('initial_preview')
    return {'id':project.get('id'),'name':project.get('name'),'status':project.get('status'),'version':project.get('version'),
            'created_at':project.get('created_at'),'analyzed':bool(project.get('analysis')),
            'initial_preview':{'status':initial.get('status')} if isinstance(initial,dict) else None,
            'shot_count':len(((project.get('plan') or {}).get('shots')) or []),
            # Every output stays (the sidebar counts thumbnails too), each cut to what picks and draws the card image.
            'outputs':[{key:output.get(key) for key in ('shot_id','quality','jpg_url','png_url')} for output in project.get('outputs') or []]}

def conditional(response):
    """An ETag over the exact body: a poll that sends it back gets an empty 304 while nothing changed."""
    response.headers['Cache-Control']='no-cache'
    response.add_etag()
    return response.make_conditional(request)

@app.get('/api/projects/<ident>')
def project(ident): return conditional(jsonify(engine.project_view(ident)))

@app.delete('/api/projects/<ident>')
def project_delete(ident):
    try:
        result=project_lifecycle.delete_project(ident)
    except project_lifecycle.ProjectBusyError as exc:
        return jsonify(error=str(exc)),409
    except OSError:
        return jsonify(error='项目文件暂时无法删除，请关闭正在使用该项目文件的程序后重试。'),409
    return jsonify(result)

def safe_path(base,name):
    name=name.replace('\\','/')
    parts=PurePosixPath(name)
    if parts.is_absolute() or any(x in ('..','') for x in parts.parts) or ':' in name: raise ValueError('资源路径必须位于上传目录内')
    dest=(base/name).resolve()
    if base.resolve() not in dest.parents: raise ValueError('资源路径无效')
    if dest.suffix.lower() not in ALLOWED: raise ValueError('不支持资源格式：'+dest.suffix)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists(): raise ValueError('资源路径重复：'+name)
    return dest

def extract_archive(content,base):
    with zipfile.ZipFile(content) as z:
        entries=[i for i in z.infolist() if not i.is_dir()]
        if len(entries)>2000 or sum(i.file_size for i in entries)>1024**3: raise ValueError('ZIP 解压内容超过限制')
        for i in entries:
            if (i.external_attr >> 16) & 0o170000 == 0o120000: raise ValueError('不接受符号链接')
            safe_path(base,i.filename).write_bytes(z.read(i))

def check_resources(base):
    # Reject local absolute / parent references before Blender can read them.
    for f in list(base.rglob('*.obj'))+list(base.rglob('*.mtl')):
        for line in f.read_text(encoding='utf-8',errors='replace').splitlines():
            parts=line.strip().split(maxsplit=1)
            if len(parts)<2: continue
            key,value=parts
            if key.lower()=='mtllib' or key.lower().startswith('map_') or key.lower() in ['bump','disp','decal','norm']:
                value=value.replace('\\','/').strip('"')
                # Texture options remain legal; check every token for external paths.
                for token in value.split():
                    if token.startswith('/') or ':' in token: raise ValueError('OBJ/MTL 包含绝对资源路径，请改成相对路径')
                    if '..' in PurePosixPath(token).parts and base.resolve() not in (f.parent/token).resolve().parents: raise ValueError('贴图引用超出模型资源目录')
                if '..' in PurePosixPath(value).parts:
                    resolved=(f.parent/value).resolve()
                    if base.resolve() not in resolved.parents: raise ValueError('贴图引用超出模型资源目录')
    for f in base.rglob('*'):
        if f.suffix.lower()!='.glb': continue
        with f.open('rb') as stream:
            header=stream.read(20)
            if len(header)!=20 or header[:4]!=b'glTF': raise ValueError('GLB 文件头无效')
            length,kind=struct.unpack('<II',header[12:20])
            if kind!=0x4e4f534a or length>64*1024*1024: raise ValueError('GLB JSON 数据块无效或过大')
            document=json.loads(stream.read(length).decode('utf-8'))
            for item in document.get('images',[])+document.get('buffers',[]):
                uri=unquote(item.get('uri',''))
                if uri and not uri.startswith('data:'):
                    resolved=(f.parent/uri).resolve()
                    if ':' in uri or uri.startswith(('/', '\\')) or base.resolve() not in resolved.parents: raise ValueError('GLB 外部资源引用不安全')

def create_project(base,name='',metadata=None,platform=None):
    models=list(base.rglob('*.glb'))+list(base.rglob('*.obj'))
    # Case-insensitive extension handling on Windows and Linux.
    models=list(dict.fromkeys(models+[p for p in base.rglob('*') if p.suffix.lower() in ['.glb','.obj']]))
    if len(models)!=1: raise ValueError('每个项目请上传一个 OBJ 或 GLB，配套贴图可放在同一 ZIP 中')
    check_resources(base)
    ident=base.parent.name;name=name.strip() or models[0].stem
    slug=re.sub(r'[^\w\-]+','_',name)[:60] or 'product'
    plan=default_plan()
    # New projects get the showcase look: look-developed finishes, a photographic
    # light set, and a background high-quality pass of each previewed image.
    plan.update(look='showcase',auto_polish=True)
    metadata=metadata or {}
    if metadata.get('dimensions'):
        value=metadata['dimensions']
        if isinstance(value,str):
            try: value=json.loads(value)
            except ValueError: value=[float(x) for x in re.split('[x×,， ]+',value) if x]
        if not isinstance(value,list) or len(value)!=3 or any(not isinstance(x,(int,float)) or x<=0 or x>100000 for x in value): raise ValueError('真实尺寸应为三个正数（毫米）')
        plan['dimensions_mm']=value
    p=dict(id=ident,name=name[:100],slug=slug,model_path=str(models[0]),status='analyzing',analysis=None,plan=plan,version=1,history=[],outputs=[],metadata=metadata,initial_preview={'status':'pending'},created_at=time.time())
    if platform:
        # The first preview is already framed for the marketplace the seller named.
        preset=ecommerce_platforms.platform(platform)
        plan.update(ecommerce_platforms.plan_recommendation(preset,plan,new_project=True));validate(plan)
        p['ecommerce']={'platform':preset['id']}
    with LOCK:
        put('project',p)
        try:
            import listing
            listing.adopt_shared_brand(ident)          # a new project starts with the account's default (else last saved) brand
        except Exception:
            pass                                       # a brand that cannot be copied never stops an upload
        engine.submit(ident,'analyze');return engine.project_view(ident)

def _component_uploads():
    """The components sent with a new project: [(file, fields)], checked before anything is saved."""
    import components
    files=request.files.getlist('component_files')
    try: described=json.loads(request.form.get('components','[]') or '[]')
    except ValueError: raise ValueError('组件信息格式无效') from None
    if not isinstance(described,list): raise ValueError('组件信息格式无效')
    if len(files)>components.MAX_COMPONENTS: raise ValueError('一个项目最多 %d 个组件'%components.MAX_COMPONENTS)
    rows=[]
    for index,upload in enumerate(files):
        name=upload.filename or ''
        if Path(name).suffix.lower() not in components.MODEL_SUFFIXES+components.PICTURE_SUFFIXES+('.zip',):
            raise ValueError('组件文件格式不受支持：'+Path(name).name+'；每个组件请上传一个 GLB / OBJ / ZIP，或一张 PNG / JPG / WebP 图片')
        info=described[index] if index<len(described) and isinstance(described[index],dict) else {}
        fields={key:info.get(key) for key in ('name','role','sku','caption','quantity','size_mm') if info.get(key) not in (None,'')}
        components.fields(fields)                      # a bad name, type or quantity is refused before the project is made
        rows.append((upload,fields))
    return rows

def _register_components(ident,rows):
    import components
    added=[];errors=[]
    for upload,fields in rows:
        component_id,base=components.reserve(ident)
        try:
            if Path(upload.filename).suffix.lower()=='.zip': extract_archive(upload.stream,base)
            else: upload.save(safe_path(base,Path(upload.filename.replace('\\','/')).name))
            added.append(components.register(ident,component_id,fields,check_resources))
        except (ValueError,zipfile.BadZipFile,OSError) as exc:
            components.discard(ident,component_id)
            errors.append({'file':Path(upload.filename).name,'error':str(exc)[:200] if isinstance(exc,ValueError) else '文件无法读取'})
    return added,errors

@app.post('/api/projects')
def upload():
    files=request.files.getlist('files')
    if not files: raise ValueError('请选择模型文件')
    engine.require_runtime('render')
    component_rows=_component_uploads()
    platform=request.form.get('platform','').strip() or None
    if platform: ecommerce_platforms.platform(platform)
    ident=uuid.uuid4().hex;base=DATA/ident/'originals';base.mkdir(parents=True)
    paths=json.loads(request.form.get('paths','[]'))
    for i,f in enumerate(files):
        name=paths[i] if i<len(paths) else f.filename
        if Path(name).suffix.lower()=='.zip': extract_archive(f.stream,base)
        else: f.save(safe_path(base,name))
    reference=request.files.get('reference')
    metadata={k:request.form.get(k,'') for k in ['dimensions','material_note','product_category']}
    if reference:
        refdir=base.parent/'references';refdir.mkdir()
        path=safe_path(refdir,Path(reference.filename).name);reference.save(path);metadata['reference']=str(path)
        metadata['reference_status']='已保存；明确要求对照参考图时，智能调整会读取这张图片'
    view=create_project(base,request.form.get('name',''),metadata,platform)
    if component_rows:
        # Processed in the background once the product's own analysis has run; see components.process.
        added,errors=_register_components(ident,component_rows)
        view={**view,'components':added,'component_errors':errors}
    return jsonify(view),201

@app.post('/api/projects/<ident>/plan')
def plan(ident):
    patch=request.get_json(force=True)
    if not isinstance(patch,dict): raise ValueError('摄影方案必须是参数对象')
    expected=patch.get('expected_version') if isinstance(patch.get('plan'),dict) else None
    header=request.headers.get('X-Plan-Version')
    if header is not None:
        if expected is not None and str(expected)!=header: raise ValueError('方案版本不一致，请刷新后重试')
        expected=header
    if expected is not None:
        if isinstance(expected,bool) or not re.fullmatch(r'[0-9]+',str(expected)): raise ValueError('方案版本无效，请刷新后重试')
        expected=int(expected)
    if 'plan' in patch and isinstance(patch['plan'],dict): patch=patch['plan']
    with LOCK:
        p=get('project',ident)
        if expected is not None and p['version']!=expected:
            abort(409,description='方案已被自动优化或其他页面更新。当前调整未保存；请先查看最新方案，再重新应用本次修改。')
        old=p['plan'];new={**old,**patch,'source':'user'};new=validate_project_plan(ident,new)
        if (new.get('look')=='showcase' and old.get('look')!='showcase' and p.get('analysis')
                and not new.get('material_overrides') and not new.get('object_material_overrides')):
            # Switching an older project to the showcase look develops its
            # placeholder materials once, exactly as a new upload would.
            import lookdev
            new=validate_project_plan(ident,lookdev.apply(new,p['analysis'],p.get('model_path',''))[0])
        p['history'].append({'version':p['version'],'plan':old,'at':time.time(),'reason':'界面参数修改'})
        p['plan']=new;p['version']+=1;put('project',p)
    return jsonify(engine.project_view(ident))

def validate_project_plan(ident,plan):
    plan=validate(plan)
    if plan.get('scene_checkpoints'):
        import hero_optimizer
        hero_optimizer.validate_checkpoints(ident,plan)
    root=(DATA/ident/'materials').resolve()
    for m in plan.get('material_overrides',[])+plan.get('object_material_overrides',[]):
        for channel,path in m.get('textures',{}).items():
            if channel not in ['base_color','roughness','metallic','normal','transmission']: raise ValueError('未知 PBR 通道')
            resolved=Path(path).resolve()
            if root not in resolved.parents or not resolved.is_file(): raise ValueError('PBR 贴图必须先导入到当前项目')
    if plan.get('scene_set'):
        import scene_set
        props=(DATA/ident/'props').resolve()
        for path in scene_set.files(plan['scene_set']):
            resolved=Path(path).resolve()
            if props not in resolved.parents or not resolved.is_file(): raise ValueError('场景道具必须先下载到当前项目')
    return plan

@app.post('/api/projects/<ident>/undo')
def undo(ident):
    with LOCK:
        p=get('project',ident)
        if not p['history']: raise ValueError('暂无可回退操作')
        event=p['history'].pop();p['plan']=event['plan'];p['version']+=1
        if event.get('hero_restore_output'):
            import hero_optimizer
            hero_optimizer.restore_history_output(p,event)
        put('project',p)
    return jsonify(engine.project_view(ident))

@app.post('/api/projects/<ident>/materials/import')
def material_import(ident):
    p=get('project',ident)
    source=request.form.get('source','').strip();license_name=request.form.get('license','').strip()
    if not source or not license_name: raise ValueError('请填写材质来源和许可')
    name=request.form.get('name','').strip()
    if not name: raise ValueError('请选择要替换的材质槽')
    folder=DATA/ident/'materials'/uuid.uuid4().hex;folder.mkdir(parents=True)
    textures={}
    for channel in ['base_color','roughness','metallic','normal','transmission']:
        f=request.files.get(channel)
        if f:
            dest=safe_path(folder,channel+Path(f.filename).suffix.lower());f.save(dest)
            engine.verify_image(dest);textures[channel]=str(dest)
    if not textures: raise ValueError('请至少选择一张 PBR 贴图')
    previous=[m for m in p['plan']['material_overrides'] if m['name']==name]
    replacement={'name':name,'preset':request.form.get('preset','matte_plastic'),'textures':textures,'source':source,'license':license_name}
    with LOCK:
        p=get('project',ident)
        if [m for m in p['plan']['material_overrides'] if m['name']==name]!=previous:
            shutil.rmtree(folder,ignore_errors=True)
            abort(409,description='该材质在上传期间已被修改。本次贴图未应用，请查看最新材质后重试。')
        new={**p['plan'],'material_overrides':[m for m in p['plan']['material_overrides'] if m['name']!=name]+[replacement]}
        new=validate(new)
        p['history'].append({'version':p['version'],'plan':p['plan'],'at':time.time(),'reason':'导入外部 PBR 材质'})
        p['plan']=new;p['version']+=1;put('project',p)
    return jsonify(engine.project_view(ident))

@app.post('/api/projects/<ident>/instruct')
def instruction(ident):
    from instruction_service import apply_instruction,InstructionError
    from intelligence_rules import InstructionOutcome
    from ai_client import ResponseContractError
    try:
        result=apply_instruction(ident,request.get_json(force=True),request.headers.get('Idempotency-Key'),validate_plan=validate_project_plan)
    except (ResponseContractError,InstructionError,InstructionOutcome) as exc:
        return instruction_error_response(exc)
    return jsonify(result)

@app.get('/api/projects/<ident>/instruction-progress')
def instruction_progress_status(ident):
    from instruction_service import instruction_progress_view
    return jsonify(activity=instruction_progress_view(ident))


_INSTRUCTION_RESPONSE_REASONS={
    'generic':'修改结果未通过校验',
    'conflict':'返回的修改项互相冲突',
    'scope':'要求与修改范围未能对应',
    'coverage':'返回结果遗漏了部分要求',
    'material':'材质参数与要求不一致',
    'parameters':'修改参数格式不正确',
    'unsupported_parameter':'返回了当前不支持的修改参数',
    'range':'返回的参数超出可用范围',
    'target':'返回的部件、材质或镜头无效',
    'constraints':'修改违反了需要保留的设置',
    'interpretation':'要求尚未转成可执行的修改',
    'verification':'返回结果未验证要求的画面效果',
}
_INSTRUCTION_RESPONSE_CODES={
    'conflict':{'decision_conflict','decision_status_patch','partial_top_level_patch','blocked_requirement_patch',
                'unexpected_requirements','missing_pending_requirement','semantic_parameter_conflict',
                'semantic_context_execution_conflict','semantic_status_effect','semantic_dependency_invalid',
                'semantic_dependency_cycle'},
    'scope':{'semantic_scope_conflict','semantic_patch_scope','semantic_kind_conflict','semantic_constraint_scope'},
    'coverage':{'requirement_coverage','omitted_unsupported_requirement','semantic_source_coverage'},
    'material':{'material_property_conflict','material_family'},
    'parameters':{'response_parse','type','missing_fields','array_length','text_length','text_pattern',
                   'semantic_requirement_shape','semantic_row_execution','semantic_execution_invalid',
                   'semantic_duplicate_requirement','semantic_duplicate_scope','semantic_pending_patch'},
    'unsupported_parameter':{'unknown_fields'},
    'range':{'number_range'},
    'target':{'enum','material_target','semantic_binding_invalid','semantic_binding_coverage',
               'semantic_evidence_missing','semantic_count_unverified','semantic_count_mismatch',
               'semantic_active_shot_invalid'},
    'constraints':{'semantic_literal_conflict','semantic_protection_conflict','semantic_protection_invalid',
                    'semantic_preservation_patch','semantic_authorization_invalid','semantic_authorization_missing'},
    'interpretation':{'semantic_interpretation_required','semantic_interpretation_invalid',
                       'semantic_preservation_unresolved','unrequested_blocker','known_unsupported_requirement',
                       'unsupported_supported_requirement','unsupported_photography_blocker'},
    'verification':{'semantic_visual_unverified','semantic_reference_stale','semantic_reference_unavailable',
                    'semantic_context_unavailable','semantic_decision_missing'},
}
_INSTRUCTION_RESPONSE_FIELDS={
    'roughness','metallic','transmission','color','preset','exposure','light_softness','margin','aspect',
    'key_power','fill_power','rim_power','environment','key_azimuth','key_elevation','fill_azimuth','fill_elevation',
    'rim_azimuth','rim_elevation','azimuth','elevation','focal_length',
    'projection','ortho_scale','camera_distance','target_offset',
}


def instruction_response_issue(exc):
    """Return only app-owned categories and schema field names, never model text."""
    feedback=getattr(exc,'feedback',None)
    if not isinstance(feedback,dict):feedback={}
    code=feedback.get('code')
    kind=next((kind for kind,codes in _INSTRUCTION_RESPONSE_CODES.items()
               if isinstance(code,str) and code in codes),'generic')
    issue={'kind':kind}
    path=feedback.get('path')
    if isinstance(path,str) and len(path)<=300:
        # A schema path can include unknown upstream keys. Accept only the
        # exact structures used by our validator, and expose one known field.
        match=re.fullmatch(r'\$(?:\.requirement_results\[\d{1,3}\])?\.patch'
                           r'(?:\.(?:object_material_overrides|material_overrides|shot_updates)\[\d{1,3}\]|\.lighting)?'
                           r'\.([a-z_]+)',path)
        if match and match[1] in _INSTRUCTION_RESPONSE_FIELDS:issue['field']=match[1]
    return issue


def instruction_error_response(exc):
    detail={'error':str(exc)}
    if (getattr(exc,'error_code',None)=='instruction_response_invalid'
            and getattr(exc,'applied',None) is False):
        # Model protocol failures are known pre-commit rejections, not requests
        # for the user to identify parts or rewrite an already clear sentence.
        issue=instruction_response_issue(exc)
        detail.update(error='本次未应用：'+_INSTRUCTION_RESPONSE_REASONS[issue['kind']]+'。原要求已保留。',
                      error_code='instruction_response_invalid',retryable=True,applied=False,response_issue=issue)
        ident=getattr(exc,'diagnostic_id',None)
        if isinstance(ident,str) and re.fullmatch(r'[0-9a-f]{32}',ident):
            detail['diagnostic_id']=ident
        return jsonify(detail),getattr(exc,'status_code',422)
    if getattr(exc,'error_code',None):
        detail.update(error_code=exc.error_code,retryable=exc.retryable)
    pending=getattr(exc,'pending',None)
    if isinstance(pending,list) and pending:
        detail['pending']=[{key:item[key] for key in ('id','quote','reason','label','target_label','status','action') if key in item}
                           for item in pending if isinstance(item,dict)]
        detail['remaining_text']=getattr(exc,'remaining_text','')
        detail.setdefault('error_code','instruction_targets_unresolved')
    trace=getattr(exc,'agent_trace',None)
    if isinstance(trace,list) and trace:
        # Fixed codes and counts only: which agents worked on the request.
        detail['agent_trace']=[step for step in trace if isinstance(step,dict)][:20]
    return jsonify(detail),getattr(exc,'status_code',400)


@app.post('/api/projects/<ident>/instruct/confirm')
def instruction_confirm(ident):
    from instruction_service import confirm_instruction,InstructionError
    try:
        result=confirm_instruction(ident,request.get_json(force=True),request.headers.get('Idempotency-Key'),validate_plan=validate_project_plan)
    except InstructionError as exc:
        return instruction_error_response(exc)
    return jsonify(result)

@app.post('/api/projects/<ident>/feedback/pick')
def feedback_pick(ident):
    return jsonify(engine.pick_output(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/materials/selection/apply')
def selected_material_apply(ident):
    from material_edit import apply_selection
    return jsonify(apply_selection(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/material-agent/tasks')
def material_agent_create(ident):
    import material_agent
    return jsonify(material_agent.submit(ident,request.get_json(force=True))),202

@app.get('/api/projects/<ident>/scene')
def scene_state(ident):
    """The project's earlier usage scene, if any. New scenes are made by the listing set (POST .../listing/build)."""
    import scene_stylist
    project=get('project',ident)
    return jsonify(task=scene_stylist.latest(ident),scene_set=project['plan'].get('scene_set'),
                   scene_brief=project['plan'].get('scene_brief'),proposal=project.get('scene_proposal'),
                   available=engine.runtime_status().get('capabilities',{}).get('scene_set',False),moved_to='listing')

@app.get('/api/scene/audiences')
def scene_audiences():
    """The audience reference pack and whether scenes with people can be generated."""
    import scene_audience
    return jsonify(**scene_audience.public(),image=ai_config.public_image_config())

@app.get('/references/<pack>/<name>')
def scene_reference_pack(pack,name):
    """The pack's own index and terms. Links and words only: it holds no images."""
    import scene_audience
    if pack!=scene_audience.PACK_ID or name not in ('index.html','README.md'): abort(404)
    response=send_from_directory(scene_audience.PACK,name,mimetype='text/html' if name.endswith('.html') else 'text/plain; charset=utf-8')
    response.headers['Content-Security-Policy']="default-src 'none'; style-src 'unsafe-inline'";response.headers['X-Content-Type-Options']='nosniff'
    return response

@app.route('/api/projects/<ident>/orientation',methods=['GET','POST'])
def orientation_check(ident):
    """Which way up the model stands: the candidate poses, and a new look at them."""
    import orientation
    if request.method=='GET':
        project=get('project',ident)
        return jsonify(orientation=orientation.public(project.get('orientation')),task=orientation.latest(ident),
                       rotation=project['plan'].get('rotation',[0,0,0]),
                       available=engine.runtime_status().get('capabilities',{}).get('orientation',False))
    try:return jsonify(orientation.submit(ident,request.get_json(force=True))),202
    except engine.JobConflictError as exc:abort(409,description=str(exc))

@app.post('/api/projects/<ident>/orientation/apply')
def orientation_apply(ident):
    """Put the product in another pose: a candidate, a flip or a quarter turn."""
    import orientation
    try:return jsonify(orientation.apply(ident,request.get_json(force=True)))
    except engine.JobConflictError as exc:abort(409,description=str(exc))

@app.post('/api/projects/<ident>/orientation/<task_id>/cancel')
def orientation_cancel(ident,task_id):
    import orientation
    return jsonify(orientation.cancel(ident,task_id))

@app.route('/api/projects/<ident>/components',methods=['GET','POST'])
def component_list(ident):
    """The other things in the box: each a model or a picture, processed into a cut-out."""
    import components
    if request.method=='GET':
        return jsonify(components=[components.public(row) for row in components.components(get('project',ident))],
                       roles={key:{'zh':zh,'en':en} for key,(zh,en) in components.ROLES.items()},limit=components.MAX_COMPONENTS)
    files=request.files.getlist('files')
    if not files: raise ValueError('请选择组件的模型或图片文件')
    if any(Path(f.filename or '').suffix.lower() in components.MODEL_SUFFIXES+('.zip',) for f in files): engine.require_runtime('render')
    component_id,base=components.reserve(ident)
    try:
        paths=json.loads(request.form.get('paths','[]'))
        for i,f in enumerate(files):
            name=paths[i] if i<len(paths) and isinstance(paths[i],str) else f.filename
            if Path(name).suffix.lower()=='.zip': extract_archive(f.stream,base)
            else: f.save(safe_path(base,name))
    except Exception:
        components.discard(ident,component_id);raise
    body={key:request.form.get(key) for key in ('name','role','sku','caption','quantity','size_mm') if request.form.get(key) not in (None,'')}
    return jsonify(components.register(ident,component_id,body,check_resources)),201

@app.route('/api/projects/<ident>/components/<component_id>',methods=['PUT','DELETE'])
def component_item(ident,component_id):
    import components
    if request.method=='DELETE': return jsonify(components.remove(ident,component_id))
    return jsonify(components.update(ident,component_id,request.get_json(force=True)))

@app.post('/api/projects/<ident>/components/<component_id>/refresh')
def component_refresh(ident,component_id):
    import components
    engine.require_runtime('render')
    return jsonify(components.refresh(ident,component_id)),202

@app.get('/api/projects/<ident>/listing')
def listing_state(ident):
    """The listing workspace: product record, brand, components, planned and built images."""
    import listing
    return jsonify(listing.state(ident))

@app.put('/api/projects/<ident>/listing/product')
def listing_product(ident):
    import listing
    return jsonify(listing.save_product(ident,request.get_json(force=True)))

@app.put('/api/projects/<ident>/listing/brand')
def listing_brand(ident):
    import listing
    return jsonify(listing.save_brand(ident,request.get_json(force=True)))

@app.get('/api/projects/<ident>/listing/brands')
def listing_brand_sources(ident):
    """The brands this project can take: other projects' saved brands, the account's default and last one."""
    import listing
    get('project',ident)
    return jsonify(listing.brand_sources(ident))

@app.post('/api/projects/<ident>/listing/brand/copy')
def listing_brand_copy(ident):
    import listing
    body=request.get_json(force=True)
    if not isinstance(body,dict): raise ValueError('请选择要复制的品牌')
    return jsonify(listing.copy_brand(ident,body.get('source')))

@app.post('/api/projects/<ident>/listing/brand/default')
def listing_brand_default(ident):
    """This project's saved brand becomes the one new projects start with ({clear:true}: they take the last one saved again)."""
    import listing
    body=request.get_json(silent=True) or {}
    return jsonify(listing.set_default_brand(ident,clear=isinstance(body,dict) and body.get('clear') is True))

@app.put('/api/projects/<ident>/listing/mounting')
def listing_mounting(ident):
    """How the product is installed: '' (decided from its name and parts), 'wall' or 'free'. A change remakes the scenes."""
    import listing
    body=request.get_json(force=True)
    if not isinstance(body,dict): raise ValueError('安装方式格式无效')
    return jsonify(listing.save_mounting(ident,body.get('mounting')))

@app.route('/api/projects/<ident>/listing/logo',methods=['POST','DELETE'])
def listing_logo(ident):
    import listing
    if request.method=='DELETE': return jsonify(listing.save_brand(ident,{'logo':None}))
    upload=request.files.get('file')
    if not upload: raise ValueError('请选择标志图片')
    return jsonify(listing.save_logo(ident,upload.stream,upload.filename))

@app.post('/api/projects/<ident>/listing/draft')
def listing_draft(ident):
    """Fill the product record's words from the model and the user's notes."""
    import listing
    return jsonify(listing.draft(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/listing/extract')
def listing_extract(ident):
    """Sort a pasted product description into the product record (by the text model, keeping only what it says)."""
    import listing
    return jsonify(listing.extract(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/listing/build')
def listing_build(ident):
    import listing
    try:return jsonify(listing.submit(ident,request.get_json(silent=True) or {})),202
    except engine.JobConflictError as exc:abort(409,description=str(exc))

@app.post('/api/projects/<ident>/listing/scenes/<int:index>/redraw')
def listing_scene_redraw(ident,index):
    """Make one scene picture again: it leaves the kept set and a build starts, which makes only that one (the others are kept)."""
    import listing
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):raise ValueError('请求内容无效')
    try:return jsonify(listing.redraw_scene(ident,index,body.get('url') if isinstance(body.get('url'),str) else None)),202
    except engine.JobConflictError as exc:abort(409,description=str(exc))

@app.post('/api/projects/<ident>/listing/<task_id>/cancel')
def listing_cancel(ident,task_id):
    import listing
    return jsonify(listing.cancel(ident,task_id))

@app.get('/api/projects/<ident>/listing/download')
def listing_download(ident):
    import listing
    # ?reviewed=1: only the pages that passed the layout checks and the visual review; either way the ZIP says what was found of each page
    data,name=listing.archive(ident,reviewed=request.args.get('reviewed') in ('1','true'))
    return send_file(io.BytesIO(data),mimetype='application/zip',as_attachment=True,download_name=name)

# ---- the pose agent: which parts of the model move, and the states the listing shows (articulation.py) ----
ARTICULATION_FILE=re.compile(r'(parts|states[12])\.png')

def _articulation_url(ident,path):
    """The address of one of the pose agent's pictures, or None: only parts.png / states1-2.png in its own folder."""
    if not path: return None
    path=Path(path)
    if not ARTICULATION_FILE.fullmatch(path.name) or not re.fullmatch(r'[0-9a-f]{16}',path.parent.name): return None
    if path.resolve().parent.parent!=(DATA/ident/'articulation').resolve() or not path.is_file(): return None
    return '/api/projects/%s/articulation/files/%s/%s'%(ident,path.parent.name,path.name)

def _articulation_view(ident):
    import articulation
    project=get('project',ident)
    record=project.get('articulation') if isinstance(project.get('articulation'),dict) else {}
    view=articulation.public(project)
    view['running']=ident in articulation.RUNNING
    view['parts_sheet']=_articulation_url(ident,record.get('parts_sheet'))
    view['previews']=_articulation_url(ident,record.get('previews'))
    view['at']=record.get('at')
    return view

@app.get('/api/projects/<ident>/articulation')
def articulation_state(ident):
    return jsonify(_articulation_view(ident))

@app.post('/api/projects/<ident>/articulation')
def articulation_run(ident):
    """Ask the pose agent again (a vision model looks at the model's parts; Blender poses and checks them), in the background."""
    import threading
    import articulation
    import listing
    project=get('project',ident)
    if not project.get('analysis') or project.get('status')!='ready': raise ValueError('请先等待模型分析完成')
    if ident in articulation.RUNNING: return jsonify(_articulation_view(ident)),202
    product,_,_=listing.stored(project)
    size_mm,_=listing.product_size(project,product)
    articulation.RUNNING[ident]=True

    def run():
        try: articulation.analyse(ident,product,size_mm,force=True)
        except Exception as exc:
            app.logger.warning('articulation failed: %s',exc)
            with LOCK:
                try:
                    current=get('project',ident)
                    record=current.get('articulation') if isinstance(current.get('articulation'),dict) else {'status':'failed','states':[],'joints':[]}
                    if record.get('status') not in ('ready','none'): record['status']='failed'
                    record['notes']=(list(record.get('notes') or [])+['识别失败：'+str(exc)[:200]])[-6:]
                    current['articulation']=record; put('project',current)
                except KeyError: pass
        finally: articulation.RUNNING.pop(ident,None)
    threading.Thread(target=run,name='articulation',daemon=True).start()
    return jsonify(_articulation_view(ident)),202

@app.get('/api/projects/<ident>/articulation/files/<key>/<name>')
def articulation_file(ident,key,name):
    get('project',ident)
    if not re.fullmatch(r'[0-9a-f]{16}',key) or not ARTICULATION_FILE.fullmatch(name): abort(404)
    path=DATA/ident/'articulation'/key/name
    if not path.is_file(): abort(404)
    return send_file(path,mimetype='image/png',max_age=0)

@app.post('/api/projects/<ident>/scene/<task_id>/cancel')
def scene_cancel(ident,task_id):
    import scene_stylist
    return jsonify(scene_stylist.cancel(ident,task_id))

@app.post('/api/projects/<ident>/scene/remove')
def scene_remove(ident):
    import scene_stylist
    body=request.get_json(silent=True) or {}
    scene_stylist.remove(ident,body.get('expected_version'))
    return jsonify(engine.project_view(ident))

@app.get('/api/projects/<ident>/material-agent/tasks')
def material_agent_list(ident):
    import material_agent
    return jsonify(material_agent.list_tasks(ident))

@app.get('/api/projects/<ident>/material-agent/tasks/<task_id>')
def material_agent_read(ident,task_id):
    import material_agent
    return jsonify(material_agent.get_task(ident,task_id))

@app.post('/api/projects/<ident>/material-agent/tasks/<task_id>/cancel')
def material_agent_cancel(ident,task_id):
    import material_agent
    return jsonify(material_agent.cancel(ident,task_id))

@app.post('/api/projects/<ident>/review')
def review_output(ident):
    return jsonify(engine.review_output(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/review/propose')
def review_propose(ident):
    from review_actions import propose
    return jsonify(propose(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/review/apply')
def review_apply(ident):
    from review_actions import apply
    return jsonify(apply(ident,request.get_json(force=True)))

@app.post('/api/projects/<ident>/feedback')
def feedback_apply(ident):
    body=request.get_json(force=True)
    text=body.get('text','')
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise ValueError('请用不超过 2000 字描述选中部位的问题')
    config=ai_config.get_config()
    if not config['enabled'] or not config['api_key'] or not config['model']:
        raise ValueError('多点视觉反馈需要先在“AI 设置”中填写 Access Token 和模型')
    p=get('project',ident)
    if body.get('version')!=p['version']:
        raise ValueError('这张图片不是当前方案版本，请先生成当前版本预览再点选修改')
    if any(j['project_id']==ident and j['status'] in ('queued','running') for j in all_records('job')):
        raise ValueError('请等待当前渲染结束，或先取消任务，再提交局部修改')
    matches=[o for o in p.get('outputs',[]) if all(o.get(k)==body.get(k) for k in ('shot_id','quality','version')) and o.get('job_id')==body.get('output_id')]
    if len(matches)!=1:
        raise ValueError('请选择一个已生成的镜头版本')
    output=matches[0]
    image_path=(DATA/output['png_url'][7:]).resolve()
    if (DATA/ident).resolve() not in image_path.parents or not image_path.is_file():
        raise ValueError('预览图片不存在，请重新生成')
    picked=engine.pick_output(ident,body)
    hits=picked['hits']
    if not hits or any(not hit.get('hit') for hit in hits):
        raise ValueError('部分位置没有命中产品，请移除背景上的点，再提交修改')
    new=validate_project_plan(ident,instruct(p['plan'],text.strip(),p.get('analysis'),image_path=image_path,selections=hits))
    old_local={(entry['object_name'],entry['material_name']):entry for entry in p['plan'].get('object_material_overrides',[])}
    new_local={(entry['object_name'],entry['material_name']):entry for entry in new.get('object_material_overrides',[])}
    for hit in hits:
        pair=(hit['object_name'],hit['material_name'])
        entry=new_local.get(pair)
        if hit.get('material_info',{}).get('editable') is False and entry!=old_local.get(pair):
            if entry and (not entry.get('preset') or entry.get('preserve_original',True)):
                raise ValueError('所选部件使用复杂材质节点，请先明确选择内置材质预设，再修改其表面参数')
    with LOCK:
        current=get('project',ident)
        if current['version']!=p['version']:
            raise ValueError('方案已被其他操作修改，未应用此次反馈，请刷新后重试')
        if any(j['project_id']==ident and j['status'] in ('queued','running') for j in all_records('job')):
            raise ValueError('已有渲染任务开始执行，未应用此次反馈，请稍后重试')
        p=current
        p['history'].append({'version':p['version'],'plan':p['plan'],'at':time.time(),'reason':'多点反馈：'+text.strip()})
        p['plan']=new;p['version']+=1
        event={'id':uuid.uuid4().hex,'source':{k:body[k] for k in ('output_id','shot_id','quality','version')},
               'text':text.strip(),'hits':hits,'version':p['version'],'at':time.time(),'scope':'选中网格的材质槽'}
        p['feedback']=(p.get('feedback',[])+[event])[-50:]
        put('project',p)
        job=engine.submit(ident,'preview','preview',[output['shot_id']],request_key='feedback:'+event['id'])
        event['job_id']=job['id'];put('project',p)
    return jsonify(engine.project_view(ident))

@app.post('/api/projects/<ident>/jobs')
def job_create(ident):
    body=request.get_json(force=True)
    if not isinstance(body,dict): raise ValueError('渲染请求必须是参数对象')
    engine.require_render_runtime(get('project',ident)['plan'],body.get('shot_ids'))
    kind=body.get('kind','preview')
    try:
        if body.get('queue') not in (None,True,False): raise ValueError('排队参数无效')
        result=engine.submit(ident,kind,body.get('quality','standard' if kind=='render' else 'preview'),body.get('shot_ids'),request.headers.get('Idempotency-Key'),
                             expected_version=body.get('expected_version'),queue=body.get('queue') is True)
    except engine.JobConflictError as exc:
        abort(409,description=str(exc))
    return jsonify(result),202

@app.post('/api/projects/<ident>/thumbnails')
def project_thumbnails(ident):
    try:
        body=request.get_json(force=True)
        if not isinstance(body,dict) or set(body)!={'expected_version'}:raise ValueError('缩略图请求格式无效。')
        job=engine.schedule_thumbnails(ident,body['expected_version'])
        return jsonify(job=job),202 if job else 200
    except (ValueError,OSError) as exc:return jsonify(error=str(exc)),409 if isinstance(exc,engine.JobConflictError) else 400

@app.post('/api/projects/<ident>/viewport')
def viewport_create(ident):
    try:result=viewport_service.submit(ident,request.get_json(force=True))
    except viewport_service.ViewportError as exc:return jsonify(error=str(exc)),exc.status_code
    response=jsonify(result);response.headers['Cache-Control']='no-store'
    return response,200 if result['status']=='ready' else 202

@app.get('/api/projects/<ident>/viewport/<task_id>')
def viewport_read(ident,task_id):
    response=jsonify(viewport_service.read(ident,task_id));response.headers['Cache-Control']='no-store'
    return response

@app.post('/api/projects/<ident>/hero-optimize')
def hero_optimize(ident):
    import hero_optimizer
    try:
        result=hero_optimizer.submit(ident,request.get_json(force=True),request.headers.get('Idempotency-Key'))
    except engine.JobConflictError as exc:
        abort(409,description=str(exc))
    return jsonify(result),202

@app.get('/api/jobs/<ident>')
def job_read(ident): return jsonify(get('job',ident))

@app.post('/api/jobs/<ident>/cancel')
def job_cancel(ident): return jsonify(engine.cancel(ident))

@app.post('/api/jobs/<ident>/retry')
def job_retry(ident):
    old=get('job',ident)
    if old['status'] in ['running','queued']: return jsonify(old)
    if old.get('kind')=='hero_optimize':
        import hero_optimizer
        try:return jsonify(hero_optimizer.retry_job(old)),202
        except engine.JobConflictError as exc:abort(409,description=str(exc))
    # Retry reproduces the failed task, not subsequent edited settings.
    with LOCK:
        for existing in all_records('job'):
            if existing['project_id']==old['project_id'] and existing['status'] in ['running','queued']: return jsonify(existing)
        engine.require_render_runtime(old['plan'],old.get('shot_ids'))
        new={**old,'id':uuid.uuid4().hex,'status':'queued','stage':'等待继续','error':None,'progress':0,'completed':[],'reused':[],'log_tail':[],'current_shot':None,'created_at':time.time(),'retry_of':ident}
        for key in ['finished_at','started_at','sample_progress','fallback_reason']: new.pop(key,None)
        put('job',new);engine.QUEUE.put(new['id'])
        if new['kind']=='analyze':
            p=get('project',new['project_id'])
            if not p.get('analysis'): p['status']='analyzing';put('project',p)
    return jsonify(new),202

@app.get('/brandkit/fonts.css')
def brandkit_fonts_css():
    # The font library's faces on this machine, for the brand panel's pairing previews.
    import brandkit_assets
    rules=[]
    for family in brandkit_assets.FAMILIES.values():
        for face in family['files']:
            if brandkit_assets._font_file(face['file']):
                rules.append("@font-face{font-family:'%s';src:url('/brandkit/font/%s') format('truetype');%s;font-display:swap}"%(family['family'],face['file'],face['css']))
    response=app.response_class('\n'.join(rules)+'\n',mimetype='text/css')
    response.headers['Cache-Control']='no-cache'
    return response

@app.get('/brandkit/font/<name>')
def brandkit_font(name):
    import brandkit_assets
    known={face['file'] for family in brandkit_assets.FAMILIES.values() for face in family['files']}
    path=brandkit_assets._font_file(name) if name in known else None
    if not path: abort(404)
    return send_file(path,mimetype='font/ttf',max_age=86400)

@app.get('/files/<path:filename>')
def files(filename):
    path=(DATA/filename).resolve()
    if DATA.resolve() not in path.parents: abort(404)
    parts=path.relative_to(DATA.resolve()).parts
    if parts[0]==project_lifecycle.TRASH_NAME: abort(404)
    # Expose only the bounded, generated realtime asset, not uploaded models or
    # arbitrary GLBs elsewhere in the workspace.
    viewport_glb=(len(parts)==4 and re.fullmatch(r'[a-f0-9]{32}',parts[0]) is not None
                  and parts[1]=='viewport' and re.fullmatch(r'[a-f0-9]{64}',parts[2]) is not None
                  and parts[3]=='scene.glb')
    # Editable listing pages (SVG) are only ever downloaded, never shown inline from this origin.
    if path.suffix.lower()=='.svg' and len(parts)>2 and parts[1]=='listing':
        return send_from_directory(DATA,filename,as_attachment=True,mimetype='image/svg+xml')
    if path.suffix.lower() not in ['.png','.jpg','.blend','.c4d','.zip','.json','.fbx'] and not viewport_glb: abort(404)
    return send_from_directory(DATA,filename,as_attachment=request.args.get('download')=='1')

@app.post('/api/projects/<ident>/exports')
def scene_export_create(ident):return jsonify(scene_exports.submit(ident,request.get_json(force=True))),202

@app.get('/api/exports/<ident>')
def scene_export_read(ident):return jsonify(get('export',ident))

@app.post('/api/exports/<ident>/cancel')
def scene_export_cancel(ident):return jsonify(scene_exports.stop(ident))

@app.get('/api/projects/<ident>/bundle')
def bundle(ident):
    p=get('project',ident);buf=io.BytesIO()
    quality=request.args.get('quality','all');include_blend=request.args.get('blend','1')=='1'
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('project.json',json.dumps(p,ensure_ascii=False,indent=2))
        seen=set()
        for o in p['outputs']:
            if quality!='all' and o['quality']!=quality: continue
            for key in ['png_url','jpg_url']+(['blend_url'] if include_blend else []):
                if not o.get(key): continue
                path=DATA/o[key][7:]
                if path.exists():
                    arc='%s/v%s/%s/%s'%(o['quality'],o['version'],o['shot_id'],path.name)
                    if arc not in seen: z.write(path,arc);seen.add(arc)
        for j in all_records('job'):
            if j['project_id']==ident: z.writestr('jobs/'+j['id']+'.json',json.dumps(j,ensure_ascii=False,indent=2))
        from instruction_service import error_diagnostics
        for name,record in error_diagnostics(ident):
            z.writestr('instruction-errors/'+name,json.dumps(record,ensure_ascii=False,indent=2))
        # Keep downloaded PBR sources and their license manifests beside packed
        # Blender scenes so the external material choices remain reproducible.
        material_root=(DATA/ident/'materials').resolve()
        for override in p['plan'].get('material_overrides',[])+p['plan'].get('object_material_overrides',[]):
            for filename in override.get('textures',{}).values():
                path=Path(filename).resolve()
                if material_root not in path.parents or not path.is_file(): continue
                for resource in [path,path.parent/'manifest.json']:
                    if not resource.is_file(): continue
                    arc='materials/'+resource.relative_to(material_root).as_posix()
                    if arc not in seen: z.write(resource,arc);seen.add(arc)
        for task in all_records('material_task'):
            if task.get('project_id')==ident:
                z.writestr('material-tasks/'+task['id']+'.json',json.dumps(task,ensure_ascii=False,indent=2))
    buf.seek(0)
    return send_file(buf,mimetype='application/zip',as_attachment=True,download_name=p['slug']+'_images.zip')

@app.get('/api/ecommerce/platforms')
def ecommerce_platform_list():
    return jsonify(ecommerce_platforms.catalog())

@app.route('/api/projects/<ident>/ecommerce',methods=['GET','PUT'])
def ecommerce_overview(ident):
    """Marketplace readiness of the real renders; PUT also remembers the choices."""
    if request.method=='PUT': ecommerce.save_settings(ident,request.get_json(force=True))
    response=jsonify(ecommerce.overview(get('project',ident),request.args.get('platform') if request.method=='GET' else None))
    response.headers['Cache-Control']='no-store'
    return response

@app.post('/api/projects/<ident>/ecommerce/adapt')
def ecommerce_adapt(ident):
    """Apply the marketplace's ratio and framing to the plan as one undoable step."""
    body=request.get_json(force=True)
    if not isinstance(body,dict): raise ValueError('平台适配请求必须是参数对象')
    with LOCK:
        p=get('project',ident)
        chosen,preset=ecommerce.resolve_options(body,p)
        if 'expected_version' in body and body['expected_version']!=p['version']:
            abort(409,description='方案已在后台更新。本次没有调整画幅与留白，请查看最新方案后重试。')
        if any(j['project_id']==ident and j['status'] in ('queued','running') for j in all_records('job')):
            abort(409,description='请等待当前任务完成，或先取消任务，再调整画幅与留白。')
        patch=ecommerce_platforms.plan_recommendation(preset,p['plan'])
        if patch:
            new=validate_project_plan(ident,{**p['plan'],**patch,'source':'user'})
            p['history'].append({'version':p['version'],'plan':p['plan'],'at':time.time(),'reason':'按「%s」调整画幅与留白'%preset['name']})
            p['plan']=new;p['version']+=1
        p['ecommerce']=chosen;put('project',p)
    return jsonify(engine.project_view(ident))

@app.post('/api/projects/<ident>/ecommerce/exports')
def ecommerce_export_create(ident):return jsonify(ecommerce.submit(ident,request.get_json(force=True))),202

@app.get('/api/ecommerce/exports/<ident>')
def ecommerce_export_read(ident):return jsonify(ecommerce.public(get(ecommerce.KIND,ident)))

@app.post('/api/ecommerce/exports/<ident>/cancel')
def ecommerce_export_cancel(ident):return jsonify(ecommerce.cancel(ident))

@app.get('/api/examples')
def examples():
    path=ROOT/'samples'/'index.json'
    return jsonify(json.loads(path.read_text(encoding='utf-8')) if path.exists() else [])

@app.post('/api/examples/<ident>/import')
def example_import(ident):
    listing_path=ROOT/'samples'/'index.json'
    if not listing_path.is_file(): raise ValueError('当前应用包没有附带示例模型，请上传自己的模型，或从完整应用目录补齐 samples 文件夹。')
    engine.require_runtime('render')
    listing=json.loads(listing_path.read_text(encoding='utf-8'))
    entry=next((x for x in listing if x['id']==ident),None)
    if not entry: abort(404)
    base=DATA/uuid.uuid4().hex/'originals';base.mkdir(parents=True)
    src=ROOT/'samples'/entry['file']
    if src.suffix.lower()=='.zip': extract_archive(src,base)
    else: shutil.copy2(src,base/src.name)
    return jsonify(create_project(base,entry['name'],{'license':entry.get('license'),'example_id':ident})),201

if __name__=='__main__':
    app_log.configure()     # first: it also creates the data folder the instance lock below lives in
    # A second launch must not mark a currently running renderer as interrupted.
    instance_lock=(DATA/'studio.lock').open('a+b')
    instance_lock.seek(0,2)
    if instance_lock.tell()==0: instance_lock.write(b'0');instance_lock.flush()
    instance_lock.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(instance_lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(instance_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('物影 Studio 已在运行，请打开已有本地页面。')
    project_lifecycle.cleanup_deleted_projects()
    engine.start()
    scene_exports.start()
    ecommerce.start()
    import material_agent
    material_agent.recover_interrupted()
    import scene_stylist
    scene_stylist.recover_interrupted()
    import orientation
    orientation.recover_interrupted()
    import components
    components.start();components.recover_interrupted()
    import listing
    listing.recover_interrupted()
    import brandkit_assets
    brandkit_assets.prefetch()      # fonts and common icons for the listing templates, in the background
    port=int(os.getenv('STUDIO_PORT','8765'))
    print('物影 Studio  http://127.0.0.1:%s'%port,flush=True)
    app.run(host='127.0.0.1',port=port,debug=False,threaded=True,use_reloader=False)

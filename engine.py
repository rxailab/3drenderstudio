import copy
import atexit
import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tempfile
import uuid
from pathlib import Path
from PIL import Image, ImageStat
from studio_store import ROOT,DATA,LOCK,get,put,all_records,atomic_json
from planner import enrich,apply_material_note
from quality_gate import EVALUATION_PROTOCOL, compare_candidates, instruction_protected_roots
from blender.light_direction import CONTRACT_VERSION as LIGHT_DIRECTION_CONTRACT_VERSION, file_digest as light_scene_digest, requested_angles, validate_report as validate_light_direction_report
from camera_controls import CONTRACT_VERSION as CAMERA_CONTRACT_VERSION,shot_request,validate_report as validate_camera_report,matches_result as camera_matches_result

QUEUE=queue.Queue()
PROCESSES={}
RUNNING_JOBS=set()
DEVICE={'status':'尚未执行设备探测'}

def shutdown():
    for proc in list(PROCESSES.values()):
        if proc.poll() is None:
            try: proc.terminate()
            except OSError: pass

atexit.register(shutdown)

def blender_path():
    candidates=[os.getenv('BLENDER_PATH'),shutil.which('blender'),r'D:\blender\blender.exe',r'D:\FORM-LAB Studio\runtime\blender\blender.exe']
    candidates += [str(p) for p in Path('C:/Program Files/Blender Foundation').glob('*/blender.exe')]
    return next((p for p in candidates if p and Path(p).is_file()),None)

RUNTIME_COMPONENTS={
    'render':('blender/worker.py',),
    'camera_projection':('blender/worker.py','blender/camera_entry.py','blender/camera_projection.py','camera_controls.py'),
    'light_direction':('blender/worker.py','blender/render_entry.py','blender/light_direction.py'),
    'inspect_lighting':('blender/inspect_lighting.py','blender/light_direction.py'),
    'hero_optimize':('blender/hero_scene.py','hero_recipe.py','hero_state.py','blender/camera_projection.py','camera_controls.py'),
    'pick':('blender/pick_surface.py',),
    'inspect_parts':('blender/inspect_parts.py',),
    'export_blend':('blender/export_scene.py',),
    'export_c4d':('blender/export_scene.py','c4d/convert_scene.py'),
}

def runtime_status():
    """Report actual feature readiness, including the application's own scripts."""
    executable=blender_path()
    missing=list(dict.fromkeys(name for names in RUNTIME_COMPONENTS.values() for name in names if not (ROOT/name).is_file()))
    capabilities={name:bool(executable) and all((ROOT/path).is_file() for path in paths) for name,paths in RUNTIME_COMPONENTS.items()}
    reasons=[]
    if not executable: reasons.append('未找到 Blender，请在 .env 中配置 BLENDER_PATH 后刷新页面。')
    if 'blender/worker.py' in missing: reasons.append('应用组件不完整：缺少 blender/worker.py，请从完整应用目录补齐 blender 文件夹。')
    if capabilities['render'] and missing: reasons.append('渲染引擎已就绪；部分扩展组件不完整：'+ '、'.join(missing)+'。')
    return {'available':capabilities['render'],'blender_available':bool(executable),'missing_components':missing,
            'message':' '.join(reasons) or '本机渲染引擎已就绪。','capabilities':capabilities,
            'samples_available':(ROOT/'samples/index.json').is_file()}

def require_runtime(capability='render'):
    """Reject unavailable work before creating project files or queue records."""
    paths=RUNTIME_COMPONENTS[capability]
    missing=[name for name in paths if not (ROOT/name).is_file()]
    reasons=[]
    if missing: reasons.append('应用组件不完整，缺少 '+ '、'.join(missing)+'；请从完整应用目录补齐对应文件夹。')
    if not blender_path(): reasons.append('未找到 Blender，请在 .env 中配置 BLENDER_PATH。')
    if reasons: raise ValueError(' '.join(reasons))


def require_render_runtime(plan,shot_ids=None):
    """A saved native checkpoint is its own explicit render capability."""
    ids=shot_ids or [shot['id'] for shot in plan.get('shots',[])]
    checkpoints=plan.get('scene_checkpoints',{})
    if any(shot_id in checkpoints for shot_id in ids):require_runtime('hero_optimize')
    if not ids or any(shot_id not in checkpoints for shot_id in ids):require_runtime('render')

def project_view(ident):
    p=get('project',ident)
    # Pending candidates and evidence digests are service-private. Public
    # receipts expose only the reviewed diff and the unresolved user requests.
    p.pop('_instruction_proposals',None)
    p['jobs']=[j for j in all_records('job') if j['project_id']==ident]
    p['exports']=[e for e in all_records('export') if e['project_id']==ident]
    return p

class JobConflictError(ValueError):
    """A strict client must not mistake another render for its own request."""


def submit(ident,kind='preview',quality='preview',shot_ids=None,request_key=None,retry_of=None,force_review=False,review_question=None,review_source=None,expected_version=None):
    if kind not in ['analyze','preview','render']: raise ValueError('任务类型无效')
    if quality not in ['preview','standard','fine']: raise ValueError('画质无效')
    if kind=='render' and quality=='preview': quality='standard'
    if shot_ids is not None and (not isinstance(shot_ids,list) or not shot_ids or any(not isinstance(shot,str) for shot in shot_ids)):
        raise ValueError('请至少选择一个有效镜头')
    if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
        raise ValueError('预览方案版本必须为正整数')
    with LOCK:
        p=get('project',ident)
        project_jobs=[j for j in all_records('job') if j['project_id']==ident]
        ids=list(dict.fromkeys([s['id'] for s in p['plan']['shots']] if shot_ids is None else shot_ids))
        desired_quality='preview' if kind=='preview' else quality
        for j in project_jobs:
            if request_key and j.get('request_key')==request_key:
                if expected_version is not None and (j.get('version')!=expected_version or j.get('kind')!=kind or j.get('quality')!=desired_quality or j.get('shot_ids')!=ids):
                    raise JobConflictError('此预览请求标识已用于不同的方案或镜头，请查看原任务后重试。')
                return j
        if expected_version is not None and p['version']!=expected_version:
            raise JobConflictError('方案已在预览提交前更新。本次没有启动新预览，请查看最新方案。')
        for j in project_jobs:
            if j['status'] in ['queued','running']:
                if expected_version is not None:
                    raise JobConflictError('已有另一项渲染任务，请等待或取消它后重试本次预览。')
                return j
        if kind!='analyze' and not p.get('analysis'): raise ValueError('请先等待模型分析完成')
        ids=[s['id'] for s in p['plan']['shots']] if shot_ids is None else shot_ids
        if not isinstance(ids,list) or not ids or any(not isinstance(shot,str) for shot in ids): raise ValueError('请至少选择一个有效镜头')
        ids=list(dict.fromkeys(ids))
        if not set(ids).issubset({s['id'] for s in p['plan']['shots']}): raise ValueError('包含未知镜头')
        job=dict(id=uuid.uuid4().hex,project_id=ident,kind=kind,quality='preview' if kind=='preview' else quality,
            shot_ids=ids,status='queued',stage='等待执行',progress=0,current_shot=None,error=None,log_tail=[],
            plan=copy.deepcopy(p['plan']),version=p['version'],created_at=time.time(),request_key=request_key,retry_of=retry_of,completed=[])
        if force_review: job['force_review']=True
        if review_question: job['review_question']=review_question
        if review_source:
            job['automatic_previous_preview']={k:review_source.get(k) for k in ('job_id','shot_id','version','png_url')}
            job['review_notice']='新版灯光从新预览开始比较，未自动比较旧引擎图；旧图保留，可查看前后结果。'
        put('job',job);QUEUE.put(job['id'])
        return job

def cancel(ident):
    with LOCK:
        j=get('job',ident)
        if j['status'] in ['queued','running']:
            j['status']='cancelled';j['stage']='已取消';put('job',j)
            _record_initial_preview_state(j)
            if j.get('kind')=='analyze':
                project=get('project',j['project_id'])
                if not project.get('analysis'): project['status']='cancelled';put('project',project)
            proc=PROCESSES.get(ident)
            if proc: proc.terminate()
        return j

def update_job(ident,**fields):
    with LOCK:
        j=get('job',ident)
        if j['status']=='cancelled': return j
        previous_status=j['status']
        j.update(fields);put('job',j)
        if j['status']!=previous_status: _record_initial_preview_state(j)
        return j


def _initial_preview_key(project_id):
    return 'initial-white-hero:'+project_id


def _record_initial_preview_state(job):
    """Keep the upload's one-off preview state in step with its actual job."""
    if job.get('origin')!='initial_preview': return
    try: project=get('project',job['project_id'])
    except KeyError: return
    state=project.get('initial_preview')
    if not isinstance(state,dict): return
    if state.get('job_id') not in (None,job['id'],job.get('retry_of')): return
    state.update(status=job['status'],job_id=job['id'],version=job['version'],
                 shot_id='hero',quality='preview',error=job.get('error'))
    if job.get('analysis_job_id'): state['analysis_job_id']=job['analysis_job_id']
    put('project',project)


def schedule_initial_preview(project_id, analysis_job_id=None):
    """Complete the new-upload flow once, independently of an open browser.

    Only explicitly opted-in new projects participate. A persisted request key
    reconciles a crash between creating the render and recording its identity.
    Existing cancelled/failed/interrupted jobs remain user-controlled retries.
    """
    with LOCK:
        project=get('project',project_id)
        state=project.get('initial_preview')
        if not isinstance(state,dict): return None
        jobs=[job for job in all_records('job') if job['project_id']==project_id]
        key=_initial_preview_key(project_id)
        existing=next((job for job in jobs if job.get('request_key')==key),None)
        if existing:
            if existing.get('kind')!='preview' or existing.get('quality')!='preview' or existing.get('shot_ids')!=['hero']:
                state.update(status='failed',error='白底主图任务标识与原任务不一致，请使用预览当前镜头重新生成。')
                put('project',project);return None
            # A process could stop after submit saved the job but before this
            # origin marker was written. Reconcile, never submit it again.
            existing['origin']='initial_preview'
            if analysis_job_id: existing['analysis_job_id']=analysis_job_id
            put('job',existing)
            state['job_id']=existing['id'];put('project',project)
            _record_initial_preview_state(existing)
            return existing
        if state.get('status')!='pending' or not project.get('analysis') or project.get('status')!='ready': return None
        analysis=next((job for job in jobs if job.get('kind')=='analyze' and job.get('status')=='completed'
                       and (analysis_job_id is None or job['id']==analysis_job_id)),None)
        if analysis is None: return None
        if any(job.get('status') in ('queued','running') for job in jobs): return None
        state['analysis_job_id']=analysis['id']
        try:
            require_runtime('render')
            hero=next((shot for shot in project['plan']['shots'] if shot['id']=='hero'),None)
            if hero is None or hero.get('style')!='white':
                raise ValueError('当前方案缺少白底主图镜头，请检查摄影方案后生成预览。')
            job=submit(project_id,'preview','preview',['hero'],request_key=key,expected_version=project['version'])
            job.update(origin='initial_preview',analysis_job_id=analysis['id'],stage='等待生成白底主图')
            put('job',job)
            state['job_id']=job['id'];put('project',project)
            _record_initial_preview_state(job)
            return job
        except (ValueError,OSError) as exc:
            state.update(status='failed',error='模型已分析，但白底主图未启动：'+str(exc))
            put('project',project)
            update_job(analysis['id'],initial_preview_notice=state['error'])
            return None

def execute(job,request,out):
    if (request.get('action')=='render'
            and request.get('shot',{}).get('id') in request.get('plan',{}).get('scene_checkpoints',{})):
        import hero_optimizer
        return hero_optimizer.render_checkpoint(job,request,out)
    require_runtime('render')
    exe=blender_path()
    if not exe: raise RuntimeError('未找到 Blender。请在 .env 配置 BLENDER_PATH。')
    out.mkdir(parents=True,exist_ok=True)
    request['output_dir']=str(out)
    direction_contract=None;camera_contract=None
    if request.get('action')=='render':
        require_runtime('camera_projection')
        project=get('project',job['project_id'])
        product_names=[item['name'] for item in (project.get('analysis') or {}).get('objects',[])
                       if isinstance(item,dict) and isinstance(item.get('name'),str) and item.get('type','MESH')=='MESH']
        camera_contract={'version':CAMERA_CONTRACT_VERSION,'request_id':uuid.uuid4().hex,
                         'shot':shot_request(request.get('shot',{})),
                         'margin':request['plan'].get('margin',.12),'aspect':request['plan'].get('aspect','1:1'),
                         'product_object_names':list(dict.fromkeys(product_names))}
        request['camera_projection_contract']=camera_contract
        angles=requested_angles(effective_plan(request['plan'],request.get('shot',{}).get('id')).get('lighting',{}))
        if angles:
            require_runtime('light_direction')
            direction_contract={'version':LIGHT_DIRECTION_CONTRACT_VERSION,'request_id':uuid.uuid4().hex,
                                'requested':angles,'product_object_names':list(dict.fromkeys(product_names))}
            request['light_direction_contract']=direction_contract
        else:request.pop('light_direction_contract',None)
    atomic_json(out/'request.json',request)
    entry='camera_entry.py' if camera_contract else 'worker.py'
    command=[exe,'-b','--factory-startup','--disable-autoexec','-t','8','--python-exit-code','1','--python',str(ROOT/'blender'/entry),'--',str(out/'request.json')]
    with (out/'blender.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with LOCK:
            PROCESSES[job['id']]=proc
            if get('job',job['id'])['status']=='cancelled': proc.terminate()
        tail=[]
        for line in proc.stdout:
            log.write(line);log.flush();tail=(tail+[line.strip()])[-15:]
            fields={'log_tail':tail}
            if line.startswith('EVENT:'):
                try:
                    event=json.loads(line[6:]);fields['stage']=event.get('stage',event.get('event','执行中'))
                    if 'device' in event: DEVICE.update(event['device'] if isinstance(event['device'],dict) else {'name':event['device']})
                except (ValueError,TypeError): pass
            match=re.search(r'(?:Sample|Rendered)\s+(\d+)\s*/\s*(\d+)',line)
            if match: fields['sample_progress']=round(int(match[1])/max(1,int(match[2]))*100,1)
            update_job(job['id'],**fields)
        code=proc.wait()
        PROCESSES.pop(job['id'],None)
    if get('job',job['id'])['status']=='cancelled': raise InterruptedError('用户已取消')
    result_path=out/'result.json'
    if not result_path.exists(): raise RuntimeError('Blender 未生成执行结果（退出码 %s）：%s'%(code,'\n'.join(tail[-8:])))
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('error') or result.get('success') is False: raise RuntimeError(str(result.get('error','Blender 执行失败')))
    if code!=0: raise RuntimeError('Blender 异常退出 %s'%code)
    if direction_contract:validate_light_direction_report(result.get('light_direction_verification'),direction_contract,out)
    if camera_contract:validate_camera_report(result.get('camera_projection_verification'),camera_contract,out)
    if result.get('device'): DEVICE.update(result['device'] if isinstance(result['device'],dict) else {'name':result['device']})
    return result

def verify_image(path,expected_format=None):
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        im.load()
        if expected_format and im.format!=expected_format: raise RuntimeError('导出格式不匹配')
        return im.size


def effective_plan(plan,shot_id):
    result=copy.deepcopy(plan);adjustment=result.get('shot_adjustments',{}).get(shot_id,{})
    result['exposure']=max(-4,min(4,result.get('exposure',0)+adjustment.get('exposure_delta',0)))
    result['light_softness']=max(.25,min(4,result.get('light_softness',1)*adjustment.get('softness_multiplier',1)))
    if 'lighting' in result or 'lighting' in adjustment:
        result['lighting']={**result.get('lighting',{}),**adjustment.get('lighting',{})}
    result['shot_adjustments']={}
    return result


class LightingInspectionStale(RuntimeError):
    """The source changed while its actual light directions were inspected."""


def inspect_lighting(project,output):
    """Read a current saved scene once; never guess roles or include the floor.

    The caller retains source.sha256/stat in its commit guard. Unknown roles
    are reported separately; source changes are a hard stale-input failure.
    """
    if output.get('version')!=project.get('version'):
        raise LightingInspectionStale('灯光检查需要当前版本的场景快照。')
    url=output.get('blend_url','')
    source=(DATA/url[7:]).resolve() if isinstance(url,str) and url.startswith('/files/') else None
    root=(DATA/project['id']).resolve()
    if source is None or root not in source.parents or source.suffix.lower()!='.blend' or not source.is_file():
        raise RuntimeError('当前预览缺少完整场景快照，尚未测量实际灯光方向。')
    require_runtime('inspect_lighting')
    shot=next((item for item in output.get('effective_plan',project.get('plan',{})).get('shots',[])
               if item.get('id')==output.get('shot_id')),None)
    if shot is None:raise RuntimeError('当前图片没有明确的镜头快照，尚未测量灯光方向。')
    product_names=[item['name'] for item in (project.get('analysis') or {}).get('objects',[])
                   if isinstance(item,dict) and isinstance(item.get('name'),str) and item.get('type','MESH')=='MESH']
    before=source.stat();digest=light_scene_digest(source)
    folder=root/'lighting-inspections';folder.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='inspect-',dir=folder) as temporary:
        directory=Path(temporary);result_path=directory/'result.json'
        request={'blend_path':str(source),'shot':copy.deepcopy(shot),
                 'product_object_names':list(dict.fromkeys(product_names)),'result_path':str(result_path)}
        atomic_json(directory/'request.json',request)
        command=[blender_path(),'-b','--factory-startup','--disable-autoexec','-t','2','--python-exit-code','1',
                 '--python',str(ROOT/'blender/inspect_lighting.py'),'--',str(directory/'request.json')]
        try:
            with (directory/'blender.log').open('w',encoding='utf-8') as log:
                ran=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                                   timeout=30,cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired as exc:raise RuntimeError('读取实际灯光方向超时，当前场景未修改。') from exc
        try:
            after=source.stat();after_digest=light_scene_digest(source)
        except OSError as exc:raise LightingInspectionStale('灯光检查期间来源场景已变化，请重新提交。') from exc
        if before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns or digest!=after_digest:
            raise LightingInspectionStale('灯光检查期间来源场景已变化，请重新提交。')
        if not result_path.is_file():raise RuntimeError('未能读取当前场景的实际灯光方向。')
        result=json.loads(result_path.read_text(encoding='utf-8'))
        if ran.returncode or result.get('success') is not True:
            raise RuntimeError('未能检查实际灯光方向：'+str(result.get('error','Blender 检查未完成')))
        roles=result.get('available_roles');directions=result.get('directions');unavailable=result.get('unavailable_roles')
        if (not isinstance(roles,list) or len(roles)!=len(set(roles)) or set(roles)-{'key','fill','rim'}
                or not isinstance(directions,list) or len(directions)!=len(roles)
                or not isinstance(unavailable,dict) or set(unavailable)!=({'key','fill','rim'}-set(roles))):
            raise RuntimeError('灯光检查没有返回完整的真实角色结果。')
        result.pop('success',None)
        result['source']={**{key:output.get(key) for key in ('job_id','shot_id','version','quality','blend_url')},
                          'sha256':digest,'size_bytes':after.st_size,'mtime_ns':after.st_mtime_ns}
        return result


def pick_output(project_id,body):
    p=get('project',project_id)
    matches=[o for o in p.get('outputs',[]) if o.get('job_id')==body.get('output_id') and all(o.get(k)==body.get(k) for k in ['shot_id','quality','version'])]
    if len(matches)!=1: raise ValueError('请选择一个已生成的明确镜头版本')
    selected=matches[0]
    points=body.get('points')
    if not isinstance(points,list) or not 1<=len(points)<=20: raise ValueError('请选择 1–20 个产品表面位置')
    if len({str(point.get('id')) for point in points if isinstance(point,dict)})!=len(points): raise ValueError('点选标识不能重复')
    for point in points:
        if not isinstance(point,dict) or not isinstance(point.get('id'),str) or len(point['id'])>64: raise ValueError('点选标识无效')
        for key in ['x','y']:
            value=point.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1: raise ValueError('点选位置必须在图片内')
    source=(DATA/selected.get('blend_url','')[7:]).resolve()
    if (DATA/project_id).resolve() not in source.parents or source.suffix!='.blend' or not source.is_file(): raise ValueError('该镜头工程不存在，无法定位部件')
    require_runtime('pick')
    exe=blender_path()
    if not exe: raise ValueError('未找到 Blender，无法定位部件')
    folder=DATA/project_id/'feedback-picks'/uuid.uuid4().hex;folder.mkdir(parents=True)
    req={'blend_path':str(source),'points':points,'result_path':str(folder/'result.json')}
    atomic_json(folder/'request.json',req)
    command=[exe,'-b','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'blender/pick_surface.py'),'--',str(folder/'request.json')]
    try:
        with (folder/'blender.log').open('w',encoding='utf-8') as log:
            proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(ROOT))
    except subprocess.TimeoutExpired: raise ValueError('定位部件超时，请减少选点或稍后重试')
    result_path=folder/'result.json'
    if proc.returncode or not result_path.exists(): raise ValueError('Blender 未能完成部件定位，请查看本机 feedback-picks 日志')
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if not result.get('success'): raise ValueError(result.get('error','部件定位失败'))
    return {'snapshot':{k:selected.get(k) for k in ['shot_id','quality','version','job_id']},'hits':result['hits']}


def product_metrics(png,result):
    """Measure real product samples only; white background never votes as clipping."""
    surfaces={m['name']:m for m in result.get('studio',{}).get('material_features',[])}
    eligible=[];all_pixels=[]
    with Image.open(png) as image:
        image=image.convert('RGB');width,height=image.size
        for sample in result.get('product_samples',[]):
            pixel=image.getpixel((min(width-1,int(sample['x']*width)),min(height-1,int(sample['y']*height))))
            all_pixels.append(pixel)
            material=surfaces.get(sample.get('material_render_name',sample.get('material_name')))
            # Intentional emission, transparent edges, metal highlights and white
            # surfaces require a visual or human judgment rather than auto darkening.
            if material and material.get('transmission',0)<.1 and material.get('emission',0)<.1 and material.get('metallic',0)<.5 and material.get('luminance',1)<.8:
                eligible.append(pixel)
    clipped=sum(min(pixel)>=248 for pixel in eligible)
    return {'method':'camera-ray product samples, background excluded','product_samples':len(all_pixels),
            'eligible_opaque_samples':len(eligible),'opaque_clipped_fraction':round(clipped/max(1,len(eligible)),5),
            'product_mean':round(sum(sum(p)/3 for p in all_pixels)/max(1,len(all_pixels)),2)}


def diagnose_render(record,result,plan,analysis,question=None,progress=None):
    """Review a real saved image, with measured fallback; never mutates a plan."""
    metrics=product_metrics(DATA/record['png_url'][7:],result)
    record.setdefault('metrics',{})['product']=metrics
    checks=result.get('checks',{})
    metrics['framing']={k:checks[k] for k in ['method','kind','clipped','occupancy','margin','detail_target'] if k in checks}
    metrics['render']={k:result[k] for k in ['width','height','quality','render_settings','effective_settings','camera'] if k in result}
    studio=result.get('studio',{})
    metrics['studio']={k:studio[k] for k in ['style','lights','light_directions','material_features','environment_strength','color_management'] if k in studio}
    visible=list(dict.fromkeys((s.get('object_name'),s.get('material_name')) for s in result.get('product_samples',[])
        if isinstance(s.get('object_name'),str) and isinstance(s.get('material_name'),str)))
    material_details={(m.get('object_name'),m.get('material_name')):m for m in result.get('visible_materials',[]) if isinstance(m,dict)}
    metrics['visible_material_targets']=[copy.deepcopy(material_details.get((obj,mat),{'object_name':obj,'material_name':mat})) for obj,mat in visible[:200]]
    fraction=metrics['opaque_clipped_fraction'];eligible=metrics['eligible_opaque_samples']
    unknown='基础规则没有视觉判断能力；光照层次、微表面纹理、倒角、接地阴影、透射厚度和材质真实感仍需视觉模型或人工检查。'
    review={'source':'rules','issues':[],'patch':{},'score':round(1-fraction,5) if eligible>=30 else 0,'needs_user':[unknown],
            'realism':{'summary':'已检查可用的曝光与构图测量；这些数据不能单独证明照片级真实感。','causes':[],'priority_actions':[]}}
    if eligible>=30 and fraction>.12:
        review['issues'].append({'code':'opaque_highlight_clipping','severity':'warning','message':'产品不透明表面存在较大面积高光剪切','confidence':.95})
        review['realism']['causes'].append({'aspect':'产品曝光','evidence':'%s 个适用产品表面采样中，%.1f%% 的像素三个颜色通道均达到248/255以上；背景不参与统计。'%(eligible,fraction*100),
            'confidence':.95,'suggestion':'降低该镜头曝光，再检查表面层次与标签可读性。','requires_user':False})
        review['realism']['priority_actions'].append('优先降低曝光并重新渲染，比较产品高光中的细节。')
        if plan['exposure']>-4: review['patch']={'exposure':max(-4,round(plan['exposure']-.7,3))}
    elif eligible<30:
        review['needs_user'].append('该图缺少足够适用的产品表面采样，未据此判断产品是否过曝；白背景、金属、透明和发光表面不能用此规则判定。')
    if checks.get('kind')=='complete_product' and checks.get('clipped') is True:
        review['issues'].append({'code':'projected_product_clipping','severity':'warning','message':'完整图的模型包围盒投影超出画幅','confidence':1})
        review['realism']['causes'].append({'aspect':'完整图构图','evidence':'已保存场景的包围盒投影检查报告clipped=true；它不是逐像素轮廓分割。',
            'confidence':1,'suggestion':'增加留白或调整镜头方向后重新预览，检查突出部件。','requires_user':True})
        review['realism']['priority_actions'].append('检查画面边缘的产品部件，并调整留白。')
    if not review['realism']['causes']:
        review['realism']['priority_actions'].append('连接视觉模型后分析实际图像，或人工检查光照、材质和接地关系。')
    measured_issues=copy.deepcopy(review['issues'])
    measured_causes=copy.deepcopy(review['realism']['causes'])
    measured_actions=copy.deepcopy(review['realism']['priority_actions']) if measured_issues else []
    try:
        import ai_config,ai_client
        config=ai_config.get_vision_config()
        if config.get('enabled') and config.get('api_key') and config.get('model'):
            if progress: progress('视觉模型正在诊断真实预览的逼真度')
            review=ai_client.review_preview(plan,analysis,DATA/record['png_url'][7:],metrics,config,question=question)
            review['source']='ai'
            review['provider']=config['provider'];review['model']=config['model']
            ai_config.record_vision_result(config,True,'视觉模型已返回通过校验的逼真度诊断。')
    except (ValueError,RuntimeError,ImportError) as exc:
        review['needs_user'].append('视觉模型检查不可用：'+str(exc));review['ai_unavailable']=True
        if 'config' in locals() and config.get('api_key'):
            ai_config.record_vision_result(config,False,str(exc))
    # Measurements are independent evidence. A model's optimistic description
    # must not erase confirmed clipping or framing warnings.
    review['evaluation_protocol']=EVALUATION_PROTOCOL
    review['measured_issues']=measured_issues
    review['measurement_evidence']={k:copy.deepcopy(metrics[k]) for k in ('method','eligible_opaque_samples','opaque_clipped_fraction','framing') if k in metrics}
    if review.get('source')=='ai' and measured_issues:
        codes={issue.get('code') for issue in review.get('issues',[]) if isinstance(issue,dict)}
        review.setdefault('issues',[]).extend(issue for issue in measured_issues if issue['code'] not in codes)
        realism=review.setdefault('realism',{'summary':'','causes':[],'priority_actions':[]})
        causes=realism.setdefault('causes',[])
        realism['causes']=measured_causes+[cause for cause in causes if cause not in measured_causes]
        actions=realism.setdefault('priority_actions',[])
        realism['priority_actions']=list(dict.fromkeys(measured_actions+actions))
        measured_summary='；'.join(issue['message'] for issue in measured_issues)
        realism['summary']='测量发现：'+measured_summary+'。视觉诊断：'+realism.get('summary','请检查具体依据。')
    review['status']='manual_review' if review.get('issues') or review.get('needs_user') else 'passed'
    review['question']=question or '为什么这张商品渲染可能不够逼真？'
    review['checked_at']=time.time()
    # A version marker independent of AI availability makes upgrading an old
    # preview a one-time render, including offline/failed-AI fallback results.
    review.setdefault('material_requests',[])
    review['material_check_version']=1 if 'visible_materials' in result or any(s.get('object_name') for s in result.get('product_samples',[])) else 0
    record['review']=review
    return review


def inspect_preview(job,record,result,plan,analysis):
    update_job(job['id'],stage='检查预览曝光与逼真度')
    return diagnose_render(record,result,plan,analysis,question=job.get('review_question'),
        progress=lambda stage:update_job(job['id'],stage=stage))


def automatic_reflection_patch(review, result, plan, question=''):
    """Bounded per-shot lighting trial based on actual metal and an AI diagnosis.

    This never changes a surface, color, geometry or unverified user intent.
    Real previews and comparable visual scores determine whether it is adopted.
    """
    # A specific user's metal-reflection request is usable even if visual AI
    # is temporarily unavailable; a generic question is never inferred.
    explicit=bool(re.search(r'金属|不锈钢|铝|metal|steel|aluminium|aluminum',question or '',re.I)
                  and re.search(r'反射|反光|高光|reflection|highlight',question or '',re.I))
    if review.get('source')!='ai' and not explicit: return {}
    if re.search(r'不要|不修改|不调整|只分析|仅诊断|不改',question or ''): return {}
    surfaces=result.get('studio',{}).get('material_features',[])
    if not any(type(s.get('metallic')) in (int,float) and s['metallic']>=.5 and s.get('transmission',0)<.1 for s in surfaces if isinstance(s,dict)): return {}
    causes=review.get('realism',{}).get('causes',[])
    text=' '.join(str(c.get(k,'')) for c in causes if isinstance(c,dict) and c.get('requires_user') is False for k in ('aspect','evidence','suggestion'))
    if not re.search(r'反射|反光|高光|reflection|highlight',text+' '+(question or ''),re.I): return {}
    current=plan.get('lighting',{})
    key=float(current.get('key_power',1));fill=float(current.get('fill_power',1))
    new_key=round(min(3,key*1.2),3);new_fill=round(max(.1,fill*.9),3)
    lighting={}
    if new_key!=key: lighting['key_power']=new_key
    if new_fill!=fill: lighting['fill_power']=new_fill
    return {'lighting':lighting} if lighting else {}


def review_output(project_id,body):
    """Diagnose an exact saved output; optionally forbid all follow-up jobs.

    Older clients may request bounded automatic correction implicitly. The
    explicit diagnostic_only flag makes a manual recheck observational even
    when the current image qualifies for an automatic lighting trial.
    """
    if not isinstance(body,dict): raise ValueError('诊断请求格式无效')
    diagnostic_only=body.get('diagnostic_only',False)
    if type(diagnostic_only) is not bool: raise ValueError('只诊断选项 diagnostic_only 必须为布尔值')
    question=body.get('question','为什么现在渲染得不够逼真？')
    if not isinstance(question,str) or not question.strip() or len(question)>2000:
        raise ValueError('请输入不超过 2000 字的诊断问题')
    question=question.strip()
    project=get('project',project_id)
    def same(record):
        return record.get('job_id')==body.get('output_id') and all(record.get(k)==body.get(k) for k in ['shot_id','quality','version'])
    outputs=[record for record in project.get('outputs',[]) if same(record)]
    if len(outputs)!=1: raise ValueError('请选择一个已经生成的明确镜头版本')
    record=copy.deepcopy(outputs[0]);project_root=(DATA/project_id).resolve()
    def stored_file(url,suffix):
        if not isinstance(url,str) or not url.startswith('/files/'): raise ValueError('渲染文件记录不完整')
        path=(DATA/url[7:]).resolve()
        if project_root not in path.parents or path.suffix.lower()!=suffix or not path.is_file():
            raise ValueError('该镜头的渲染文件不存在或路径无效，请重新预览')
        return path
    image_path=stored_file(record.get('png_url'),'.png')
    result_path=stored_file(record.get('result_url'),'.json')
    verify_image(image_path,'PNG')
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if not result.get('success'): raise ValueError('该镜头没有成功的真实渲染结果')
    plan=record.get('effective_plan')
    if not isinstance(plan,dict):
        request_path=result_path.parent/'request.json'
        if not request_path.is_file(): raise ValueError('该历史图缺少原始参数快照，请重新预览后诊断')
        render_request=json.loads(request_path.read_text(encoding='utf-8'))
        if render_request.get('shot',{}).get('id')!=record['shot_id'] or not isinstance(render_request.get('plan'),dict):
            raise ValueError('渲染参数与所选镜头不一致')
        plan=effective_plan(render_request['plan'],record['shot_id'])
    review=diagnose_render(record,result,plan,project.get('analysis'),question=question)
    review['mode']='diagnostic_only';review['parameter_changes_applied']=False
    scheduled=None
    with LOCK:
        current=get('project',project_id)
        matching=[entry for entry in current.get('outputs',[]) if same(entry)]
        if len(matching)!=1 or matching[0].get('png_url')!=record['png_url']:
            raise ValueError('该镜头记录已变化，请刷新后重新诊断')
        target=matching[0]
        if target.get('review'):
            target['review_history']=(target.get('review_history',[])+[copy.deepcopy(target['review'])])[-10:]
        target['review']=review
        target.setdefault('metrics',{})['product']=record['metrics']['product']
        put('project',current)
        # A requested recheck must remain observational, regardless of what
        # the model recommends. Legacy clients retain their automatic trial.
        # A history-image review also stays read-only, and another active task
        # is never interrupted.
        idle=not any(j['project_id']==project_id and j['status'] in ('queued','running') for j in all_records('job'))
        if (not diagnostic_only and record['version']==current['version'] and idle and current['plan'].get('visual_review',True)
                and current['plan'].get('max_corrections',2)>0 and automatic_reflection_patch(review,result,plan,question)):
            scheduled=submit(project_id,'preview','preview',[record['shot_id']],force_review=True,review_question=question,review_source=record)
            target['review']['automatic_review_job_id']=scheduled['id'];put('project',current)
    return project_view(project_id)


def persist_review_candidate(job,original,best,plan,round_number,attempts):
    """Persist per-shot bests before proceeding, so cancellation/restart can resume."""
    original['corrections']=copy.deepcopy(attempts)
    for item in original['corrections']: item['applied']=item['round']==round_number
    if round_number:
        candidate=copy.deepcopy(best);candidate.pop('review_candidate',None)
        candidate['corrections']=copy.deepcopy(original['corrections'])
        original['review_candidate']={'record':candidate,'adjustment':copy.deepcopy(plan['shot_adjustments'][original['shot_id']]),'selected_round':round_number}
    with LOCK:
        project=get('project',job['project_id'])
        for index,record in enumerate(project['outputs']):
            if all(record.get(k)==original.get(k) for k in ['job_id','shot_id','quality','version']):
                project['outputs'][index]=copy.deepcopy(original);put('project',project);break


def recover_review_candidate(record):
    pending=record.get('review_candidate')
    if not pending: return record,None
    best=pending.get('record',{})
    try:
        verify_image(DATA/best['png_url'][7:]);verify_image(DATA/best['jpg_url'][7:])
        if not (DATA/best['blend_url'][7:]).is_file(): return record,None
        return copy.deepcopy(best),copy.deepcopy(pending['adjustment'])
    except (KeyError,OSError,ValueError,RuntimeError): return record,None


def review_and_correct(job,project,shot,out,record,result):
    plan=copy.deepcopy(job['plan']);current_plan=plan
    protected=instruction_protected_roots(plan)
    best_record=record;best_plan=plan;best_round=0
    review=inspect_preview(job,record,result,effective_plan(plan,shot['id']),project.get('analysis'))
    attempts=[{'round':0,'exposure':effective_plan(plan,shot['id'])['exposure'],'light_softness':effective_plan(plan,shot['id'])['light_softness'],'lighting':copy.deepcopy(effective_plan(plan,shot['id']).get('lighting',{})),'score':review.get('score',0),'png_url':record['png_url'],'decision_reason':'原始预览，作为本轮比较基准。'}]
    for number in range(1,min(2,int(plan.get('max_corrections',2)))+1):
        if get('job',job['id'])['status']=='cancelled': raise InterruptedError()
        patch=review.get('patch') or {}
        current=effective_plan(current_plan,shot['id'])
        wanted={k:patch[k] for k in ['exposure','light_softness'] if k in patch}
        reflection=automatic_reflection_patch(review,result,current,job.get('review_question',''))
        if reflection: wanted.update(reflection)
        skipped=[key for key in wanted if key in protected]
        if skipped:
            review['preserved_instruction_fields']=sorted(protected)
            wanted={key:value for key,value in wanted.items() if key not in protected}
            if not wanted:
                attempts[-1]['decision_reason']+=' 已按本次要求保留相关参数，不再自动调整。'
        if not wanted or all(current.get(k)==v for k,v in wanted.items()): break
        from planner import validate
        candidate=copy.deepcopy(current_plan)
        adjustment=candidate.setdefault('shot_adjustments',{}).setdefault(shot['id'],{})
        if 'exposure' in wanted: adjustment['exposure_delta']=wanted['exposure']-candidate.get('exposure',0)
        if 'light_softness' in wanted: adjustment['softness_multiplier']=wanted['light_softness']/candidate.get('light_softness',1)
        if 'lighting' in wanted: adjustment.setdefault('lighting',{}).update(wanted['lighting'])
        try: candidate=validate(candidate)
        except ValueError:
            review.setdefault('needs_user',[]).append('自动调整超出安全参数范围，已停止');break
        update_job(job['id'],stage=('自动优化灯光反射并重新预览' if reflection else '自动修正后重新渲染预览')+'（第 %s/%s 轮）'%(number,min(2,int(plan.get('max_corrections',2)))),review_round=number)
        candidate_out=out.parent/(out.name+'_review'+str(number))
        candidate_request={'action':'render','model_path':project['model_path'],'plan':candidate,'shot':shot,'quality':'preview'}
        try:
            try: candidate_result=execute(job,candidate_request,candidate_out)
            except RuntimeError as exc:
                if any(word in str(exc).lower() for word in ['cuda','optix','gpu','memory']):
                    update_job(job['id'],stage='自动修正预览 GPU 失败，回退 CPU');candidate_request['force_cpu']=True
                    candidate_result=execute(job,candidate_request,candidate_out)
                else: raise
        except RuntimeError as exc:
            best_record['review'].setdefault('needs_user',[]).append('自动修正渲染失败，已保留此前最佳图：'+str(exc)[:300])
            best_record['review']['status']='manual_review'
            break
        candidate_job={**job,'plan':candidate}
        candidate_record=output_record(candidate_job,shot,candidate_out,candidate_result)
        review=inspect_preview(job,candidate_record,candidate_result,effective_plan(candidate,shot['id']),project.get('analysis'))
        result=candidate_result
        comparison=compare_candidates(best_record,candidate_record)
        attempts.append({'round':number,'exposure':effective_plan(candidate,shot['id'])['exposure'],'light_softness':effective_plan(candidate,shot['id'])['light_softness'],'lighting':copy.deepcopy(effective_plan(candidate,shot['id']).get('lighting',{})),'score':review.get('score',0),'png_url':candidate_record['png_url'],'comparison':comparison,'decision_reason':comparison['reason']})
        if comparison['accepted']:
            best_record=candidate_record;best_plan=candidate;best_round=number
        persist_review_candidate(job,record,best_record,best_plan,best_round,attempts)
        if not comparison['accepted']:
            break
        current_plan=candidate
    for item in attempts: item['applied']=item['round']==best_round
    best_record['corrections']=copy.deepcopy(attempts);record['corrections']=copy.deepcopy(attempts)
    best_review=best_record['review']
    best_effective=best_record.get('effective_plan') or effective_plan(best_plan,shot['id'])
    best_patch=best_review.get('patch') or {}
    if any(best_effective.get(key)!=best_patch[key] for key in ['exposure','light_softness'] if key in best_patch and key not in protected):
        best_review.setdefault('needs_user',[]).append('已结束自动修正，仍有建议未采纳；请比较候选图并人工判断。')
        best_review['status']='manual_review'
    atomic_json(out/'review.json',{'attempts':attempts,'selected_round':best_round,'review':best_record['review']})
    persist_review_candidate(job,record,best_record,best_plan,best_round,attempts)
    return best_record,best_plan,best_round


def commit_review_results(job,selected,adjustments):
    if not adjustments: return
    with LOCK:
        if get('job',job['id'])['status']=='cancelled': return
        project=get('project',job['project_id'])
        if project['version']!=job['version']:
            update_job(job['id'],review_notice='方案已被用户修改，自动修正候选已留档，未覆盖当前方案');return
        project.setdefault('history',[]).append({'version':project['version'],'plan':copy.deepcopy(project['plan']),'at':time.time(),'reason':'真实预览检查后采用逐镜头自动修正'})
        project['plan'].setdefault('shot_adjustments',{}).update(adjustments)
        project['version']+=1
        for record in selected:
            adopted=copy.deepcopy(record);adopted['version']=project['version'];adopted['adopted_from_version']=job['version']
            adopted.pop('review_candidate',None);adopted['review_applied']=True
            project['outputs'].append(adopted)
        put('project',project)
        update_job(job['id'],adopted_version=project['version'],automatic_adjustments=adjustments)

def output_record(job,shot,out,result):
    png=next(out.glob('*.png'),None);jpg=next(out.glob('*.jpg'),None);blend=next(out.glob('*.blend'),None)
    if not png: raise RuntimeError('渲染未生成 PNG')
    size=verify_image(png,'PNG')
    if not jpg:
        jpg=png.with_suffix('.jpg')
        with Image.open(png) as im: im.convert('RGB').save(jpg,quality=95,subsampling=0)
    if verify_image(jpg,'JPEG')!=size: raise RuntimeError('PNG / JPG 尺寸不同')
    if job['quality']=='hero':
        if job.get('final_resolution') not in (2000,4096) or size!=(job['final_resolution'],job['final_resolution']):
            raise RuntimeError('高清主图未按指定正方形尺寸输出')
    elif job['quality']!='preview' and max(size)!=4096: raise RuntimeError('成品长边不是原生 4096 像素')
    with Image.open(png) as im:
        gray=im.convert('L').resize((128,128));hist=gray.histogram()
        metrics={'dark_fraction':round(sum(hist[:6])/16384,4),'bright_fraction':round(sum(hist[250:])/16384,4),'luminance_mean':round(ImageStat.Stat(gray).mean[0],2)}
    if result.get('light_direction_verification',{}).get('saved_scene_verified') is True:
        metrics['studio_light_directions']=copy.deepcopy(result.get('studio',{}).get('light_directions',[]))
    if result.get('camera_projection_verification',{}).get('saved_scene_verified') is True:
        metrics['studio_camera']=copy.deepcopy(result.get('studio',{}).get('camera',{}))
    if result.get('proof',{}).get('saved_scene_verified') is True:
        metrics.update(copy.deepcopy(result.get('metrics',{})))
        metrics['studio_light_directions']=copy.deepcopy(result.get('catalog',{}).get('light_directions',[]))
        metrics['studio_camera']=copy.deepcopy(result.get('catalog',{}).get('camera',{}))
    def url(path): return '/files/'+path.relative_to(DATA).as_posix() if path else None
    return dict(shot_id=shot['id'],shot_label=shot['label'],quality=job['quality'],version=job['version'],job_id=job['id'],png_url=url(png),jpg_url=url(jpg),blend_url=url(blend),blend_sha256=light_scene_digest(blend) if blend else None,width=size[0],height=size[1],checks=result.get('checks',{}),metrics=metrics,device=result.get('device'),created_at=time.time(),result_url=url(out/'result.json'),effective_plan=effective_plan(job['plan'],shot['id']))

def run_job(ident):
    """Track the whole worker lifecycle, including completed-job callbacks.

    Deletion checks this set under LOCK. Cancelled jobs can leave queue entries
    after their records are deleted; those entries are simply consumed.
    """
    with LOCK:
        try: job=get('job',ident)
        except KeyError: return
        if job['status']!='queued' or ident in RUNNING_JOBS: return
        RUNNING_JOBS.add(ident)
    try:
        return _run_job(ident)
    finally:
        with LOCK: RUNNING_JOBS.discard(ident)


def _run_job(ident):
    j=get('job',ident)
    if j['status']!='queued': return
    update_job(ident,status='running',stage='启动 Blender',started_at=time.time())
    p=get('project',j['project_id']);base=DATA/p['id']/'runs'/j['id']
    try:
        if j['kind']=='hero_optimize':
            import hero_optimizer
            hero_optimizer.run_job(j)
            return
        if j['kind']=='analyze':
            r=execute(j,{'action':'analyze','model_path':p['model_path'],'plan':j['plan']},base)
            with LOCK:
                if get('job',ident)['status']=='cancelled': return
                p=get('project',p['id'])
                if not p.get('analysis'):
                    p['plan']=apply_material_note(enrich(p['plan'],r),r,p.get('metadata',{}).get('material_note'))
                p['analysis']=r;p['status']='ready';put('project',p)
        else:
            shots=[s for s in j['plan']['shots'] if s['id'] in j['shot_ids']]
            selected=[];adjustments={}
            for i,shot in enumerate(shots):
                if get('job',ident)['status']=='cancelled': return
                # Resume only verified matching outputs from the same revision and quality.
                existing=next((o for o in reversed(get('project',p['id'])['outputs']) if o['version']==j['version'] and o['quality']==j['quality'] and o['shot_id']==shot['id']),None)
                if existing and j['kind']=='preview' and j.get('force_review'): existing=None
                if existing and requested_angles(effective_plan(j['plan'],shot['id']).get('lighting',{})):
                    try:
                        result_url=existing.get('result_url','')
                        result_path=(DATA/result_url[7:]).resolve() if isinstance(result_url,str) and result_url.startswith('/files/') else None
                        if not result_path or (DATA/p['id']).resolve() not in result_path.parents:
                            raise RuntimeError('缓存结果路径无效')
                        cached=json.loads(result_path.read_text(encoding='utf-8'))
                        proof=cached.get('light_direction_verification',{})
                        validate_light_direction_report(proof,{'version':LIGHT_DIRECTION_CONTRACT_VERSION,'request_id':proof.get('request_id'),
                            'requested':requested_angles(effective_plan(j['plan'],shot['id']).get('lighting',{}))},result_path.parent)
                    except (OSError,ValueError,TypeError,RuntimeError):
                        existing=None
                        update_job(ident,stage='重新核验灯光角度并生成图片')
                if existing:
                    try:
                        result_url=existing.get('result_url','')
                        result_path=(DATA/result_url[7:]).resolve() if isinstance(result_url,str) and result_url.startswith('/files/') else None
                        if not result_path or (DATA/p['id']).resolve() not in result_path.parents or not camera_matches_result(json.loads(result_path.read_text(encoding='utf-8')),shot):
                            existing=None
                            update_job(ident,stage='按当前镜头投影重新生成图片')
                    except (OSError,ValueError,TypeError,KeyError):existing=None
                if existing and j['kind']=='preview':
                    try:
                        result_url=existing.get('result_url','')
                        result_path=(DATA/result_url[7:]).resolve() if isinstance(result_url,str) and result_url.startswith('/files/') else None
                        if not result_path or (DATA/p['id']).resolve() not in result_path.parents or json.loads(result_path.read_text(encoding='utf-8')).get('render_pipeline_version',0)<2:
                            existing=None
                            update_job(ident,stage='更新旧预览的真实灯光与反射')
                    except (OSError,ValueError,TypeError): existing=None
                if existing and j['kind']=='preview' and j['plan'].get('visual_review',True) and existing.get('review',{}).get('material_check_version')!=1:
                    existing=None
                    update_job(ident,stage='升级旧预览的部件识别与自动材质检查')
                if existing:
                    try:
                        verify_image(DATA/existing['png_url'][7:]);verify_image(DATA/existing['jpg_url'][7:])
                        with LOCK:
                            curr=get('job',ident);curr['completed'].append(shot['id']);curr.setdefault('reused',[]).append(shot['id']);curr['progress']=round((i+1)/len(shots)*100);put('job',curr)
                        recovered,adjustment=recover_review_candidate(existing)
                        selected.append(recovered)
                        if adjustment: adjustments[shot['id']]=adjustment
                        continue
                    except Exception: pass
                update_job(ident,current_shot=shot['label'],progress=round(i/len(shots)*100),sample_progress=0,stage='准备镜头')
                out=base/(p['slug']+'_'+shot['id']+'_v'+str(j['version']))
                req={'action':'render','model_path':p['model_path'],'plan':j['plan'],'shot':shot,'quality':j['quality']}
                try: r=execute(j,req,out)
                except RuntimeError as exc:
                    if any(word in str(exc).lower() for word in ['cuda','optix','gpu','memory']):
                        update_job(ident,stage='GPU 失败，回退 CPU',fallback_reason=str(exc));req['force_cpu']=True;r=execute(j,req,out)
                    else: raise
                record=output_record(j,shot,out,r)
                with LOCK:
                    p=get('project',p['id']);p['outputs'].append(record);put('project',p)
                best=record
                if j['kind']=='preview' and j['plan'].get('visual_review',True):
                    best,best_plan,best_round=review_and_correct(j,p,shot,out,record,r)
                    if best_round:
                        adjustments[shot['id']]=best_plan['shot_adjustments'][shot['id']]
                    with LOCK:
                        p=get('project',p['id'])
                        for index,item in enumerate(p['outputs']):
                            if item['job_id']==record['job_id'] and item['shot_id']==record['shot_id'] and item['quality']==record['quality'] and item['version']==record['version']:
                                p['outputs'][index]=record;break
                        put('project',p)
                selected.append(best)
                with LOCK:
                    curr=get('job',ident);curr['completed'].append(shot['id']);put('job',curr)
            commit_review_results(j,selected,adjustments)
        with LOCK:
            completed=update_job(ident,status='completed',stage='已完成',progress=100,finished_at=time.time())
            if j['kind']=='analyze' and completed['status']=='completed':
                schedule_initial_preview(j['project_id'],ident)
        if j['kind']=='preview' and get('job',ident)['status']=='completed':
            # Material agents run only after the original group has finished;
            # their own previews remain in the same bounded automatic chain.
            try:
                import material_agent
                material_agent.schedule_after_preview(j['project_id'],ident)
            except Exception as exc:
                update_job(ident,material_notice='自动材质检查未完成，原渲染结果已保留：'+str(exc)[:300])
    except InterruptedError: pass
    except Exception as exc:
        update_job(ident,status='failed',stage='执行失败',error=str(exc),finished_at=time.time())
        if j['kind']=='analyze':
            with LOCK:
                p=get('project',p['id']);p['status']='failed';put('project',p)
    finally:
        if j['kind']=='preview':
            try:
                import material_agent
                material_agent.finish_failed_preview(j['project_id'],ident)
            except Exception as exc:
                update_job(ident,material_notice='自动材质任务收尾失败：'+str(exc)[:300])

def worker():
    while True:
        ident=QUEUE.get()
        try: run_job(ident)
        finally: QUEUE.task_done()

def start():
    for j in all_records('job'):
        if j['status'] in ['running','queued']:
            j.update(status='interrupted',stage='应用重启，等待继续',error='上次执行被中断，已完成图片保留。请继续未完成镜头。');put('job',j)
            _record_initial_preview_state(j)
            if j.get('kind')=='analyze':
                project=get('project',j['project_id'])
                if not project.get('analysis'): project['status']='interrupted';put('project',project)
    # Recover only the analysis-completed / first-preview-not-enqueued window.
    # Existing preview jobs above stay interrupted and are never re-enqueued.
    for project in all_records('project'):
        initial=project.get('initial_preview')
        if isinstance(initial,dict) and initial.get('status')=='pending' and project.get('analysis') and project.get('status')=='ready':
            schedule_initial_preview(project['id'])
    threading.Thread(target=worker,daemon=True,name='blender-worker').start()

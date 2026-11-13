"""Bounded, snapshot-based product-hero optimization.

Candidates remain task artifacts until the original scene/plan guard and the
native preservation proof pass. No arbitrary code is accepted from a model.
"""
import copy
import hashlib
import json
import math
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from studio_store import ROOT, DATA, LOCK, get, put, all_records, atomic_json
from hero_state import compare_scene_states, difference_summary, PRODUCT_FIELDS

PREVIEW_RESOLUTION=768
QUALITY='hero'
PHASES={'inspecting','planning','preview','evaluating','finalizing'}
PROOF_FLAGS=('geometry_preserved','transforms_preserved','color_preserved',
             'resources_preserved','labels_preserved','saved_scene_verified')
GOAL='优化为干净白底电商主图：完整展示产品，保留真实几何、颜色、标签与纹理；按画面处理构图、主体光影、金属反射、地面投影和细节。'


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _file(project_id,url,suffix):
    if not isinstance(url,str) or not url.startswith('/files/'):
        raise ValueError('当前镜头的场景记录不完整，请先生成预览。')
    root=(DATA/project_id).resolve();path=(DATA/url[7:]).resolve()
    if root not in path.parents or path.suffix.lower()!=suffix or not path.is_file():
        raise ValueError('当前镜头的场景文件不存在，请重新生成预览。')
    return path


def _url(path):return '/files/'+Path(path).relative_to(DATA).as_posix()


def _source(project,source,shot_id):
    if not isinstance(source,dict) or set(source)!={'output_id','shot_id','quality','version'}:
        raise ValueError('请选择当前镜头已经生成的明确场景版本。')
    if (not isinstance(source.get('output_id'),str) or not source['output_id']
            or source.get('quality') not in ('preview','showcase','standard','fine','hero')
            or type(source.get('version')) is not int or source['version']<1):
        raise ValueError('来源场景标识无效。')
    if source.get('shot_id')!=shot_id:
        raise ValueError('优化来源与当前镜头不一致。')
    matches=[item for item in project.get('outputs',[]) if item.get('job_id')==source.get('output_id')
             and all(item.get(key)==source.get(key) for key in ('shot_id','quality','version'))]
    if len(matches)!=1:raise ValueError('所选场景已变化，请刷新后重试。')
    output=copy.deepcopy(matches[0])
    outputs=sorted([item for item in project.get('outputs',[]) if item.get('shot_id')==shot_id and item.get('quality')!='thumbnail'],
                   key=lambda item:(item.get('version',0),item.get('created_at',0)),reverse=True)
    if not outputs or any(output.get(key)!=outputs[0].get(key) for key in ('job_id','shot_id','quality','version','png_url','blend_url')):
        raise ValueError('请使用当前镜头最新一次出图进行主图优化。')
    if type(output.get('version')) is not int or output['version']>project['version']:
        raise ValueError('所选场景版本无效。')
    return output


def _guard(project,output):
    png=_file(project['id'],output.get('png_url'),'.png')
    blend=_file(project['id'],output.get('blend_url'),'.blend')
    import engine
    engine.verify_image(png,'PNG')
    blend_hash=digest(blend)
    if output.get('blend_sha256') and output['blend_sha256']!=blend_hash:
        raise ValueError('当前场景文件已经变化，请重新生成预览。')
    return {'version':project['version'],'plan':fingerprint(project['plan']),
            'analysis':fingerprint(project.get('analysis')),
            'source':{key:output.get(key) for key in ('job_id','shot_id','quality','version','png_url','blend_url')},
            'png_sha256':digest(png),'blend_sha256':blend_hash}


def _check_guard(job):
    project=get('project',job['project_id'])
    wanted=job['hero_guard'];ref=wanted['source']
    output=_source(project,{'output_id':ref['job_id'],**{key:ref[key] for key in ('shot_id','quality','version')}},ref['shot_id'])
    if _guard(project,output)!=wanted:
        raise ValueError('方案或来源场景已在优化期间变化，候选已保留，未覆盖当前项目。')
    return project,output


def _shot(project,shot_id):
    shot=next((item for item in project['plan']['shots'] if item['id']==shot_id),None)
    if not shot or shot.get('kind')!='full' or shot.get('style',project['plan'].get('style'))!='white':
        raise ValueError('请先选择完整产品的白底主图镜头。')
    return shot


def submit(project_id,body,request_key=None):
    if not isinstance(body,dict):raise ValueError('主图优化请求格式无效。')
    if set(body)-{'expected_version','shot_id','source','final_resolution'}:raise ValueError('主图优化包含未知参数。')
    if type(body.get('expected_version')) is not int:raise ValueError('请提供当前方案版本。')
    resolution=body.get('final_resolution',2000)
    if type(resolution) is not int or resolution not in (2000,4096):raise ValueError('主图输出尺寸仅支持 2000 或 4096。')
    if request_key is not None and (not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',request_key)):
        raise ValueError('主图优化请求标识无效。')
    import engine
    normalized={**body,'final_resolution':resolution,'shot_id':body.get('shot_id','hero')}
    request_hash=fingerprint(normalized)
    with LOCK:
        project=get('project',project_id)
        for job in all_records('job'):
            if job.get('project_id')!=project_id:continue
            if request_key and job.get('request_key')==request_key:
                if job.get('kind')!='hero_optimize' or job.get('hero_request_hash')!=request_hash:
                    raise engine.JobConflictError('此请求标识已用于不同的优化任务。')
                return job
        if project['version']!=body['expected_version']:raise engine.JobConflictError('方案已变化，请刷新后再优化主图。')
        if any(job.get('project_id')==project_id and job.get('status') in ('queued','running') for job in all_records('job')):
            raise engine.JobConflictError('已有任务正在执行，请等待完成或取消后重试。')
        shot_id=normalized['shot_id'];shot=_shot(project,shot_id)
        output=_source(project,body.get('source'),shot_id)
        engine.require_runtime('hero_optimize')
        refresh=output['version']!=project['version']
        if refresh:
            try:engine.require_render_runtime(project['plan'],[shot_id])
            except ValueError as exc:
                raise ValueError('最新方案尚未出图，且当前环境无法生成更新后的场景。请先生成当前版本预览。') from exc
        guard=_guard(project,output)
        ident=uuid.uuid4().hex
        job={'id':ident,'project_id':project_id,'kind':'hero_optimize','quality':QUALITY,
             'shot_ids':[shot_id],'current_shot':shot['label'],'version':project['version'],
             'plan':copy.deepcopy(project['plan']),'status':'queued','stage':'等待优化主图','workflow_phase':'inspecting',
             'progress':0,'sample_progress':None,'completed':[],'error':None,'log_tail':[],
             'created_at':time.time(),'request_key':request_key,'hero_request_hash':request_hash,
             'hero_guard':guard,'refresh_baseline':refresh,'final_resolution':resolution,
             'hero_candidates':[],'llm_calls':0,'optimization_summary':'','optimization_decision':None}
        put('job',job);engine.QUEUE.put(ident)
        return job


def cancelled(job):
    if get('job',job['id']).get('status')=='cancelled':raise InterruptedError('主图优化已取消。')


def phase(job,name,text):
    if name not in PHASES:raise ValueError('主图优化阶段无效。')
    import engine
    cancelled(job)
    engine.update_job(job['id'],workflow_phase=name,stage=text,sample_progress=None)


def _native(job,request,out):
    """Run the standalone reader/renderer with ordinary task cancellation."""
    import engine
    engine.require_runtime('hero_optimize')
    cancelled(job);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    request=copy.deepcopy(request);request['output_dir']=str(out)
    atomic_json(out/'request.json',request)
    command=[engine.blender_path(),'-b','--factory-startup','--disable-autoexec','-t','8',
             '--python-exit-code','1','--python',str(ROOT/'blender/hero_scene.py'),'--',str(out/'request.json')]
    timeout=max(30,min(14400,float(job.get('plan',{}).get('time_budget',1200))))
    expired=[]
    with (out/'blender.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
            text=True,encoding='utf-8',errors='replace',cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def expire():
            expired.append(True)
            if proc.poll() is None:proc.terminate()
        timer=threading.Timer(timeout,expire);timer.daemon=True;timer.start()
        with LOCK:
            engine.PROCESSES[job['id']]=proc
            if get('job',job['id'])['status']=='cancelled':proc.terminate()
        tail=[]
        try:
            for line in proc.stdout:
                log.write(line);log.flush();tail=(tail+[line.strip()])[-15:]
                fields={'log_tail':tail}
                match=re.search(r'(?:Sample|Rendered)\s+(\d+)\s*/\s*(\d+)',line)
                if match:fields['sample_progress']=round(int(match[1])/max(1,int(match[2]))*100,1)
                engine.update_job(job['id'],**fields)
            code=proc.wait()
        finally:
            timer.cancel();engine.PROCESSES.pop(job['id'],None)
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
    cancelled(job)
    if expired:raise RuntimeError('本轮场景处理超时，原方案与图片保留。')
    result_path=out/'result.json'
    if code or not result_path.is_file():raise RuntimeError('原生场景处理未完成：'+'\n'.join(tail[-5:]))
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('success') is not True:raise RuntimeError(str(result.get('error','原生场景处理未成功。')))
    return result


def _request(source,source_hash,objects,action,**values):
    return {'action':action,'source_blend':str(source),'source_sha256':source_hash,
            'product_object_names':objects,'request_id':uuid.uuid4().hex,**values}


def _validate_render(result,request,out,allow_authorized_color=False,allow_plan_aspect=False):
    import engine
    proof=result.get('proof')
    flags=list(PROOF_FLAGS)
    authorized_color=False
    if allow_authorized_color and isinstance(proof,dict) and proof.get('color_preserved') is False:
        authorized_color=_authorized_color_change(proof,request)
        if authorized_color:flags.remove('color_preserved')
    if (not isinstance(proof,dict) or any(proof.get(key) is not True for key in flags)
            or proof.get('request_id')!=request['request_id'] or proof.get('source_sha256')!=request['source_sha256']):
        raise RuntimeError('候选缺少完整的几何、颜色、资源与保存场景核验，未采用。')
    output=result.get('outputs',{})
    paths={}
    for key,suffix in (('png','.png'),('blend','.blend')):
        name=output.get(key)
        if not isinstance(name,str) or Path(name).name!=name or Path(name).suffix.lower()!=suffix:
            raise RuntimeError('原生执行器返回的输出文件无效。')
        path=Path(out)/name
        if not path.is_file() or proof.get(key+'_sha256')!=digest(path):raise RuntimeError('场景或图片与执行证明不一致。')
        paths[key]=path
    expected_size=(request['resolution'],request['resolution'])
    if allow_plan_aspect:
        aspect=request.get('plan',{}).get('aspect','1:1')
        if aspect!=request.get('baseline_plan',{}).get('aspect','1:1'):
            if aspect not in ('1:1','4:3','3:4'):raise RuntimeError('后续镜头画幅无效。')
            x,y=map(int,aspect.split(':'));expected_size=tuple(round(request['resolution']*v/max(x,y)) for v in (x,y))
    if engine.verify_image(paths['png'],'PNG')!=expected_size:
        raise RuntimeError('原生主图没有按指定尺寸完成渲染。')
    for key in ('geometry_sha256','transforms_sha256','color_sha256','resources_sha256'):
        if key=='color_sha256' and authorized_color:continue
        before=proof.get('before',{}).get(key);after=proof.get('after',{}).get(key)
        if not isinstance(before,str) or not before or before!=after:
            raise RuntimeError('原生场景的保留数据发生变化，候选未采用。')
    framing=result.get('metrics',{}).get('framing',{})
    if framing.get('kind')!='complete_product' or type(framing.get('clipped')) is not bool:
        raise RuntimeError('候选缺少产品完整构图检查。')
    if digest(request['source_blend'])!=request['source_sha256']:
        raise RuntimeError('原生场景在处理期间变化，本次结果未采用。')
    return paths


def _authorized_color_change(proof,request):
    plan=request.get('plan');baseline=request.get('baseline_plan');changes=proof.get('authorized_color_changes')
    if (not isinstance(plan,dict) or not isinstance(baseline,dict) or proof.get('request_match') is not True
            or proof.get('request_plan_sha256')!=fingerprint(plan)
            or proof.get('request_baseline_plan_sha256')!=fingerprint(baseline)
            or not isinstance(changes,list) or not changes):return False
    for change in changes:
        if not isinstance(change,dict):return False
        match=re.fullmatch(r'(object_material_overrides|material_overrides)\[(\d+)\]\.color',str(change.get('path','')))
        if not match:return False
        kind,index=match.group(1),int(match.group(2));rows=plan.get(kind,[])
        if index>=len(rows) or not isinstance(rows[index],dict) or 'color' not in rows[index] or rows[index]['color']!=change.get('requested_color'):return False
        row=rows[index];keys=('object_name','material_name') if kind=='object_material_overrides' else ('name',)
        old=next((item for item in baseline.get(kind,[]) if all(item.get(key)==row.get(key) for key in keys)),{})
        if old.get('color')==row['color']:return False
        bindings=change.get('bindings')
        if not isinstance(bindings,list) or not bindings:return False
        if kind=='object_material_overrides' and any(binding.get('object_name')!=row['object_name'] for binding in bindings):return False
    return True


def _render(job,source,source_hash,objects,out,recipe,resolution,samples,**values):
    request=_request(source,source_hash,objects,'render',recipe=recipe,resolution=resolution,samples=samples,
                     shot_id=job['shot_ids'][0],target_aspect='1:1',**values)
    result=_native(job,request,out);paths=_validate_render(result,request,out)
    return result,paths


def _record(job,shot,out,result,resolution,quality):
    import engine
    prepared=copy.deepcopy(result)
    prepared['checks']=copy.deepcopy(result.get('metrics',{}).get('framing',{}))
    record=engine.output_record({**job,'quality':quality,'final_resolution':resolution},shot,out,prepared)
    record['metrics'].update(copy.deepcopy(result.get('metrics',{})))
    record['native_scene_verification']=copy.deepcopy(result['proof'])
    record['render_pipeline']='native_hero_v1'
    record['source_snapshot']=copy.deepcopy(job['hero_guard']['source'])
    catalog=result.get('catalog',{})
    directions=catalog.get('light_directions')
    if isinstance(directions,list):record['metrics']['studio_light_directions']=copy.deepcopy(directions)
    return record


def _measurements(before,after):
    return {'before':copy.deepcopy(before.get('metrics',{})), 'candidate':copy.deepcopy(after.get('metrics',{})),
            'preservation_verified':all(after.get('proof',{}).get(key) is True for key in PROOF_FLAGS)}


def _acceptable(before,after,comparison):
    from quality_gate import measurement_guard
    old={'checks':before.get('metrics',{}).get('framing',{}),'metrics':before.get('metrics',{})}
    new={'checks':after.get('metrics',{}).get('framing',{}),'metrics':after.get('metrics',{})}
    passed,reason=measurement_guard(old,new)
    if not passed:return False,reason
    if new['checks'].get('clipped') is True:return False,'候选未完整保留产品边缘，保留原方案。'
    if (comparison.get('source')=='ai' and comparison.get('decision')=='candidate'
            and type(comparison.get('confidence')) in (int,float) and math.isfinite(comparison['confidence'])
            and .65<=comparison['confidence']<=1):
        return True,str(comparison.get('reason','视觉比较支持采用此候选。'))[:1200]
    return False,str(comparison.get('reason') or '本轮缺少可比较的视觉改善证据，保留原方案；候选可供查看。')[:1200]


def _synced_plan(plan,shot_id,catalog):
    """Make displayed absolute camera/direction values match the adopted scene."""
    import planner
    result=copy.deepcopy(plan);shot=next(item for item in result['shots'] if item['id']==shot_id)
    camera=catalog.get('camera',{})
    for key in ('azimuth','elevation','focal_length'):
        value=camera.get(key)
        if type(value) not in (int,float) or not math.isfinite(value):raise RuntimeError('原生场景没有返回可回放的实际镜头参数。')
        shot[key]=value
    if camera.get('type') not in ('PERSP','ORTHO'):raise RuntimeError('原生场景没有返回有效的实际投影。')
    shot['projection']='orthographic' if camera['type']=='ORTHO' else 'perspective'
    for field in ('ortho_scale','camera_distance','target_offset'):shot.pop(field,None)
    if camera['type']=='ORTHO':shot['ortho_scale']=camera['ortho_scale']
    else:shot['camera_distance']=camera['distance']
    shot['target_offset']=copy.deepcopy(camera.get('target_offset',[0,0,0]))
    adjustment=result.setdefault('shot_adjustments',{}).setdefault(shot_id,{})
    lighting=adjustment.setdefault('lighting',{})
    for key in planner.LIGHT_DIRECTION_LIMITS:lighting.pop(key,None)
    for key in ('key_power','fill_power','rim_power','environment'):lighting[key]=1
    for row in catalog.get('light_directions',[]):
        if row.get('role') not in ('key','fill','rim'):continue
        for axis in ('azimuth','elevation'):
            value=row.get(axis);key=row['role']+'_'+axis
            lo,hi=planner.LIGHT_DIRECTION_LIMITS[key]
            if type(value) in (int,float) and math.isfinite(value) and lo<=value<=hi:lighting[key]=value
    exposure=catalog.get('exposure',catalog.get('render',{}).get('exposure'))
    if type(exposure) not in (int,float) or not math.isfinite(exposure):raise RuntimeError('原生场景没有返回实际曝光设置。')
    adjustment['exposure_delta']=exposure-result.get('exposure',0)
    adjustment['softness_multiplier']=1
    result['source']='hero_optimizer'
    return planner.validate(result)


def _checkpoint(project_id,token):
    if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{32}',token):raise ValueError('主图场景标识无效。')
    root=(DATA/project_id).resolve();folder=DATA/project_id/'hero_checkpoints'/token
    if folder.is_symlink() or root not in folder.resolve().parents:raise ValueError('主图场景目录无效。')
    scene=folder/'scene.blend';record=folder/'checkpoint.json'
    if scene.is_symlink() or record.is_symlink() or not scene.is_file() or not record.is_file():
        raise ValueError('已优化的场景快照不存在，请恢复上一版或重新优化。')
    metadata=json.loads(record.read_text(encoding='utf-8'))
    if metadata.get('project_id')!=project_id or metadata.get('token')!=token or metadata.get('blend_sha256')!=digest(scene):
        raise ValueError('已优化的场景快照发生变化，请恢复上一版。')
    return scene,metadata


def validate_checkpoints(project_id,plan):
    for shot_id,token in plan.get('scene_checkpoints',{}).items():
        _,metadata=_checkpoint(project_id,token)
        if metadata.get('shot_id')!=shot_id:raise ValueError('场景快照与镜头不一致。')
    return plan


def restore_history_output(project,event):
    """Restore the verified pre-optimization image/scene along with its plan."""
    output=event.get('hero_restore_output')
    if not isinstance(output,dict):return
    try:
        import engine
        png=_file(project['id'],output.get('png_url'),'.png')
        blend=_file(project['id'],output.get('blend_url'),'.blend')
        engine.verify_image(png,'PNG')
        if output.get('blend_sha256') and output['blend_sha256']!=digest(blend):return
    except (OSError,ValueError,RuntimeError):return
    restored=copy.deepcopy(output)
    restored['restored_from_version']=output.get('version')
    restored['version']=project['version'];restored['created_at']=time.time()
    restored['effective_plan']=engine.effective_plan(project['plan'],output['shot_id'])
    project.setdefault('outputs',[]).append(restored)


def render_checkpoint(job,request,out):
    """Render the adopted native scene; never rebuild it with the old worker."""
    import engine
    plan=request['plan'];shot_id=request.get('shot',{}).get('id')
    token=plan.get('scene_checkpoints',{}).get(shot_id)
    scene,metadata=_checkpoint(job['project_id'],token)
    if metadata.get('shot_id')!=shot_id:raise ValueError('主图快照与所选镜头不一致。')
    quality=request.get('quality','preview');resolution=256 if quality=='thumbnail' else 768 if quality=='preview' else 4096
    native_request=_request(scene,metadata['blend_sha256'],metadata['product_object_names'],'render',
        recipe={},resolution=resolution,samples=8 if quality=='thumbnail' else 32 if quality=='preview' else 128,shot_id=shot_id,
        plan=copy.deepcopy(plan),baseline_plan=metadata['baseline_plan'],force_cpu=bool(request.get('force_cpu')))
    result=_native(job,native_request,out);_validate_render(result,native_request,out,allow_authorized_color=True,allow_plan_aspect=True)
    result['checks']=copy.deepcopy(result.get('metrics',{}).get('framing',{}))
    result['render_pipeline_version']=3
    # Existing downstream consumers can use the proof and actual measurements.
    result['studio']={'light_directions':copy.deepcopy(result.get('catalog',{}).get('light_directions',[]))}
    return result


def _save_checkpoint(project,job,final_result,final_paths,plan):
    token=uuid.uuid4().hex;folder=DATA/project['id']/'hero_checkpoints'/token
    folder.mkdir(parents=True,exist_ok=False)
    shutil.copy2(final_paths['blend'],folder/'scene.blend')
    base=copy.deepcopy(plan);base.pop('scene_checkpoints',None)
    metadata={'version':1,'project_id':project['id'],'token':token,'shot_id':job['shot_ids'][0],
              'baseline_plan':base,'product_object_names':final_result['catalog']['product_object_names'],
              'blend_sha256':digest(folder/'scene.blend'),'proof':copy.deepcopy(final_result['proof']),
              'catalog':copy.deepcopy(final_result['catalog']),'job_id':job['id'],'created_at':time.time()}
    atomic_json(folder/'checkpoint.json',metadata)
    plan.setdefault('scene_checkpoints',{})[job['shot_ids'][0]]=token
    return token


def _final_state(job,candidate,final):
    """A hash records exact bytes; scene equivalence has explicit tolerances."""
    import engine
    comparison=compare_scene_states(candidate.get('catalog'),final.get('catalog'))
    engine.update_job(job['id'],scene_verification=comparison)
    if comparison.get('equivalent') is not True:
        raise RuntimeError('高清输出与已选候选的场景状态不一致，原方案保留。'+difference_summary(comparison))
    return comparison


def _commit(job,shot,baseline_record,final_result,final_paths,final_out,restore_output,planned,reason,calls):
    import engine
    final_frame=final_result.get('metrics',{}).get('framing',{})
    if final_frame.get('clipped') is not False:raise RuntimeError('高清主图未完整保留产品边缘，原方案保留。')
    final_record=_record(job,shot,final_out,final_result,job['final_resolution'],QUALITY)
    state=get('job',job['id'])
    final_record['scene_verification']=copy.deepcopy(state.get('scene_verification',{}))
    if state.get('recovery_status'):
        final_record['recovered_from_job']=state.get('resume_from')
    with LOCK:
        current,_=_check_guard(job);cancelled(job)
        new_plan=_synced_plan(current['plan'],shot['id'],final_result['catalog'])
        _save_checkpoint(current,job,final_result,final_paths,new_plan)
        current.setdefault('history',[]).append({'version':current['version'],'plan':copy.deepcopy(current['plan']),
                                                'at':time.time(),'reason':'自动主图优化，保留前后场景',
                                                'hero_restore_output':copy.deepcopy(restore_output)})
        current['plan']=new_plan;current['version']+=1
        final_record['version']=current['version'];final_record['effective_plan']=engine.effective_plan(new_plan,shot['id'])
        final_record['hero_optimization']={'job_id':job['id'],'summary':planned.get('summary','已完成主图优化'),
                                         'decision_reason':reason,'source':planned.get('source'),'llm_calls':calls}
        current['outputs'].extend([baseline_record,final_record]);put('project',current)
        summary=state.get('recovery_summary') or planned.get('summary') or reason
        engine.update_job(job['id'],status='completed',stage='主图优化完成',workflow_phase='finalizing',
            optimization_decision='adopted',optimization_summary=summary,llm_calls=calls,
            adopted_version=current['version'],progress=100,sample_progress=None,completed=[shot['id']],
            automatic_previous_preview={key:baseline_record.get(key) for key in ('job_id','shot_id','version','png_url')},
            finished_at=time.time())


def _run_folder(project_id,job_id):
    if not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{32}',job_id):
        raise ValueError('恢复任务标识无效。')
    root=(DATA/project_id).resolve();folder=DATA/project_id/'runs'/job_id
    if folder.is_symlink() or root not in folder.resolve().parents:
        raise ValueError('恢复任务目录无效。')
    return folder


def _json_record(folder,name):
    path=Path(folder)/name
    if path.is_symlink() or not path.is_file() or path.stat().st_size>32*1024*1024:
        raise ValueError('缺少可核验的试渲染记录：'+name)
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('试渲染记录格式无效：'+name)
    return value


def _cached_render(out,source,source_hash,recipe,shot_id,resolution=None):
    """Bind saved files to the exact recorded native request, never by name alone."""
    out=Path(out)
    if out.is_symlink():raise ValueError('试渲染目录无效。')
    request=_json_record(out,'request.json');result=_json_record(out,'result.json')
    actual_resolution=request.get('resolution')
    if (request.get('action')!='render' or result.get('success') is not True
            or request.get('shot_id')!=shot_id or request.get('target_aspect')!='1:1'
            or request.get('source_sha256')!=source_hash
            or not isinstance(request.get('source_blend'),str)
            or Path(request['source_blend']).resolve()!=Path(source).resolve()
            or fingerprint(request.get('recipe'))!=fingerprint(recipe)
            or type(actual_resolution) is not int or not 32<=actual_resolution<=4096
            or resolution is not None and actual_resolution!=resolution
            or 'plan' in request or 'baseline_plan' in request):
        raise ValueError('试渲染记录与本次来源、配方或镜头不一致。')
    paths=_validate_render(result,request,out)
    if any(path.is_symlink() for path in paths.values()):raise ValueError('试渲染文件不能使用重定向链接。')
    return result,paths,request


def _inspect_saved(job,source,objects,out):
    request=_request(source,digest(source),objects,'inspect')
    result=_native(job,request,out)
    if (result.get('success') is not True or result.get('request_id')!=request['request_id']
            or result.get('source_sha256')!=request['source_sha256'] or digest(source)!=request['source_sha256']):
        raise RuntimeError('保存场景的重新检查与来源不一致。')
    catalog=result.get('catalog')
    if compare_scene_states(catalog,catalog).get('equivalent') is not True:
        raise RuntimeError('保存场景没有返回完整的可核验状态。')
    return catalog


def _preserved_catalog(source,candidate,proof):
    if (not isinstance(candidate,dict) or source.get('product_object_names')!=candidate.get('product_object_names')
            or any(source.get(key)!=candidate.get(key) or not isinstance(source.get(key),str)
                   or source[key]!=proof.get('before',{}).get(key) or candidate[key]!=proof.get('after',{}).get(key)
                   for key in PRODUCT_FIELDS)):
        raise RuntimeError('已保存场景的产品几何、颜色或资源与原始来源不一致，未恢复。')


def _resume(job,project,output,shot,base):
    """Recover a previously selected candidate without another AI request."""
    import engine
    origin=get('job',job['resume_from'])
    if (origin.get('kind')!='hero_optimize' or origin.get('project_id')!=job['project_id']
            or origin.get('hero_guard')!=job['hero_guard'] or origin.get('plan')!=job['plan']
            or origin.get('shot_ids')!=job['shot_ids'] or origin.get('final_resolution')!=job['final_resolution']):
        raise ValueError('待恢复任务与当前项目来源不一致。')
    original_base=_run_folder(job['project_id'],origin['id'])
    source=_file(project['id'],output['blend_url'],'.blend');restore_output=output
    if origin.get('refresh_baseline'):
        fresh=original_base/'current_plan';fresh_request=_json_record(fresh,'request.json')
        if (fresh_request.get('action')!='render' or fresh_request.get('plan')!=job['plan']
                or fresh_request.get('shot')!=shot or fresh_request.get('quality')!='preview'
                or fresh_request.get('model_path')!=project.get('model_path')):
            raise ValueError('当前方案的已保存基准来源无效。')
        restore_output=engine.output_record({**origin,'quality':'preview'},shot,fresh,_json_record(fresh,'result.json'))
        source=_file(project['id'],restore_output.get('blend_url'),'.blend')
    source_hash=digest(source);planned=origin.get('hero_plan');comparison=origin.get('hero_comparison')
    if not isinstance(planned,dict) or not isinstance(planned.get('recipe'),dict) or not isinstance(comparison,dict):
        raise ValueError('缺少已完成的优化规划与比较记录，请重新运行自动优化主图。')
    recipe=planned['recipe']
    phase(job,'inspecting','核验已完成的试渲染与高清文件')
    baseline,baseline_paths,baseline_request=_cached_render(original_base/'baseline',source,source_hash,{},shot['id'])
    candidate,candidate_paths,candidate_request=_cached_render(original_base/'candidate',source,source_hash,recipe,shot['id'],baseline_request['resolution'])
    if baseline_request['resolution']>1024:raise ValueError('试渲染记录的比较尺寸无效。')
    rows=origin.get('hero_candidates',[])
    if not any(row.get('png_url')==_url(candidate_paths['png']) and row.get('blend_url')==_url(candidate_paths['blend'])
               and row.get('blend_sha256')==digest(candidate_paths['blend']) and row.get('recipe')==recipe
               and row.get('baseline_png_url')==_url(baseline_paths['png']) for row in rows if isinstance(row,dict)):
        raise ValueError('已选候选与原比较记录不一致，请重新运行自动优化主图。')
    accepted,reason=_acceptable(baseline,candidate,comparison)
    if not accepted:raise ValueError('此前候选没有通过采用检查，不能直接恢复。'+reason)
    objects=candidate.get('catalog',{}).get('product_object_names')
    if not isinstance(objects,list) or not objects:raise ValueError('已保存候选没有明确的产品对象。')
    original_catalog=_inspect_saved(job,source,objects,base/'recovery_source')
    candidate_catalog=_inspect_saved(job,candidate_paths['blend'],objects,base/'recovery_candidate')
    _preserved_catalog(original_catalog,candidate_catalog,candidate['proof'])
    candidate['catalog']=candidate_catalog
    baseline_record=_record(job,shot,original_base/'baseline',baseline,baseline_request['resolution'],'preview')
    old_calls=origin.get('llm_calls',0)
    engine.update_job(job['id'],hero_plan=copy.deepcopy(planned),hero_comparison=copy.deepcopy(comparison),
                      hero_candidates=copy.deepcopy(rows),llm_calls=0,reused_llm_calls=old_calls,
                      recovery_status='checking_completed_files')
    cancelled(job);_check_guard(job)
    final=None;paths=None;final_out=None
    final_job_id=job.get('resume_final_from') or origin['id']
    final_folder=_run_folder(job['project_id'],final_job_id)/'final'
    try:
        request=_json_record(final_folder,'request.json')
        from_candidate=(isinstance(request.get('source_blend'),str)
                        and Path(request['source_blend']).resolve()==candidate_paths['blend'].resolve()
                        and request.get('source_sha256')==digest(candidate_paths['blend']))
        cached_source=candidate_paths['blend'] if from_candidate else source
        cached_hash=digest(cached_source);cached_recipe={} if from_candidate else recipe
        final,paths,_=_cached_render(final_folder,cached_source,cached_hash,cached_recipe,shot['id'],job['final_resolution'])
        final_catalog=_inspect_saved(job,paths['blend'],objects,base/'recovery_final')
        proof_source=candidate_catalog if from_candidate else original_catalog
        _preserved_catalog(proof_source,final_catalog,final['proof'])
        final['catalog']=final_catalog
        verified=compare_scene_states(candidate_catalog,final_catalog)
        engine.update_job(job['id'],scene_verification=verified)
        if not verified.get('equivalent') or final_catalog.get('framing',{}).get('clipped') is not False:
            engine.update_job(job['id'],recovery_final_mismatch=verified)
            final=None
        else:final_out=final_folder
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        final=None
        engine.update_job(job['id'],recovery_final_notice=str(exc)[:600])
    cancelled(job);_check_guard(job)
    if final is None:
        phase(job,'finalizing','已复用试渲染，仅重新输出高清主图')
        engine.update_job(job['id'],recovery_status='rerendering_final',resume_final_from=job['id'])
        final_out=base/'final'
        final,paths=_render(job,candidate_paths['blend'],digest(candidate_paths['blend']),objects,
                           final_out,{},job['final_resolution'],128)
        _final_state(job,candidate,final)
        summary='已复用试渲染，重新完成高清输出。'
        engine.update_job(job['id'],recovery_status='rerendered_final',recovery_summary=summary)
    else:
        _final_state(job,candidate,final)
        summary='已恢复已完成的高清结果，无需重新渲染。'
        engine.update_job(job['id'],recovery_status='reused_final',recovery_summary=summary,reused_final_from=final_job_id)
    # Reinspection upgrades the catalog only; the original rendered files and
    # their exact file digests/proofs remain unchanged and retain their paths.
    _commit(job,shot,baseline_record,final,paths,final_out,restore_output,planned,reason,0)


def run_job(job):
    import engine
    import ai_config
    import hero_ai
    cancelled(job);project,output=_check_guard(job);shot=_shot(project,job['shot_ids'][0])
    base=DATA/project['id']/'runs'/job['id'];base.mkdir(parents=True,exist_ok=True)
    engine.update_job(job['id'],status='running',started_at=time.time(),hero_candidates=[],llm_calls=0)
    if job.get('resume_from'):
        return _resume(job,project,output,shot,base)
    objects=[item['name'] for item in (project.get('analysis') or {}).get('objects',[])
             if isinstance(item,dict) and item.get('type','MESH')=='MESH' and isinstance(item.get('name'),str)]
    source=_file(project['id'],output['blend_url'],'.blend');source_hash=job['hero_guard']['blend_sha256']
    planning_png=_file(project['id'],output['png_url'],'.png')
    restore_output=output
    if job.get('refresh_baseline'):
        phase(job,'inspecting','先生成当前方案的场景快照')
        fresh=base/'current_plan'
        result=engine.execute(job,{'action':'render','model_path':project['model_path'],'plan':job['plan'],
                                  'shot':shot,'quality':'preview'},fresh)
        fresh_record=engine.output_record({**job,'quality':'preview'},shot,fresh,result)
        restore_output=fresh_record
        source=_file(project['id'],fresh_record['blend_url'],'.blend');source_hash=digest(source)
        planning_png=_file(project['id'],fresh_record['png_url'],'.png')
    phase(job,'inspecting','读取真实场景、相机与材质')
    inspected=_native(job,_request(source,source_hash,objects,'inspect'),base/'inspect')
    catalog=inspected.get('catalog')
    if not isinstance(catalog,dict) or not isinstance(catalog.get('product_object_names'),list) or not catalog['product_object_names']:
        raise RuntimeError('场景检查没有返回明确的产品对象，原方案保留。')
    cancelled(job);_check_guard(job)
    objects=catalog['product_object_names']
    from camera_controls import projection
    catalog['preferred_projection']=projection(shot)
    catalog['product_context']={'name':str(project.get('name',''))[:100],
        'material_note':str((project.get('metadata') or {}).get('material_note',''))[:2000],
        'source':'user_description_not_measured_properties'}
    config=ai_config.get_vision_config()  # Freeze one provider/model for both calls, never persist secrets.
    protections=(job['plan'].get('last_instruction') or {}).get('protections',[]) if job['plan'].get('source')!='user' else []
    if not isinstance(protections,list):protections=[]
    phase(job,'planning','根据原图与实际场景规划主图优化')
    planned=hero_ai.plan_hero(catalog,planning_png,GOAL,config,protections=protections)
    recipe=planned.get('recipe',{})
    calls=planned.get('llm_calls',0)
    if type(calls) is not int or not 0<=calls<=1:raise RuntimeError('主图规划超过本轮模型预算。')
    engine.update_job(job['id'],llm_calls=calls,hero_plan={key:copy.deepcopy(planned.get(key)) for key in ('recipe','summary','diagnosis','skipped','source')},
                      evaluator={'provider':config.get('provider'),'model':config.get('model')})
    cancelled(job);_check_guard(job)
    phase(job,'preview','渲染原始场景预览（统一方形画幅）')
    baseline_result,baseline_paths=_render(job,source,source_hash,objects,base/'baseline',{},PREVIEW_RESOLUTION,32)
    baseline_record=_record(job,shot,base/'baseline',baseline_result,PREVIEW_RESOLUTION,'preview')
    phase(job,'preview','调整构图、灯光与表面，渲染候选主图')
    candidate_result,candidate_paths=_render(job,source,source_hash,objects,base/'candidate',recipe,PREVIEW_RESOLUTION,32)
    candidate_artifact={'round':1,'label':'优化候选','png_url':_url(candidate_paths['png']),
                        'blend_url':_url(candidate_paths['blend']),'blend_sha256':digest(candidate_paths['blend']),
                        'baseline_png_url':_url(baseline_paths['png']),'recipe':copy.deepcopy(recipe)}
    engine.update_job(job['id'],hero_candidates=[candidate_artifact])
    phase(job,'evaluating','比较原图与候选，检查产品完整性')
    if compare_scene_states(baseline_result.get('catalog'),candidate_result.get('catalog')).get('equivalent'):
        comparison={'decision':'baseline','confidence':1.0,'source':'rules','llm_calls':0,
                    'reason':'可执行设置没有改变实际场景，已保留当前方案。'}
    else:
        comparison=hero_ai.compare_hero(baseline_paths['png'],candidate_paths['png'],catalog,recipe,
                                       _measurements(baseline_result,candidate_result),config)
    extra=comparison.get('llm_calls',0)
    if type(extra) is not int or not 0<=extra<=1 or calls+extra>2:raise RuntimeError('主图比较超过本轮模型预算。')
    adopted,reason=_acceptable(baseline_result,candidate_result,comparison)
    engine.update_job(job['id'],llm_calls=calls+extra,hero_comparison=copy.deepcopy(comparison))
    cancelled(job);_check_guard(job)
    if not adopted:
        with LOCK:
            current,_=_check_guard(job);cancelled(job)
            if job.get('refresh_baseline'):
                current['outputs'].append(baseline_record);put('project',current)
            engine.update_job(job['id'],status='completed',stage='优化检查完成，保留当前方案',workflow_phase='evaluating',
                optimization_decision='kept_original',optimization_summary=reason,progress=100,
                sample_progress=None,completed=[shot['id']],finished_at=time.time())
        return
    phase(job,'finalizing','输出高清主图并验证可编辑工程')
    # The selected snapshot is the photographic source of truth. Increasing
    # resolution must not refit the camera or reapply material/light recipes.
    final_result,final_paths=_render(job,candidate_paths['blend'],digest(candidate_paths['blend']),objects,
                                    base/'final',{},job['final_resolution'],128)
    _final_state(job,candidate_result,final_result)
    _commit(job,shot,baseline_record,final_result,final_paths,base/'final',restore_output,planned,reason,calls+extra)


def retry_job(old):
    import engine
    with LOCK:
        if old.get('status') in ('running','queued'):return old
        for existing in all_records('job'):
            if (existing.get('project_id')==old['project_id'] and existing.get('retry_of')==old['id']
                    and existing.get('hero_guard')==old.get('hero_guard')
                    and existing.get('status') in ('queued','running','completed')):
                return existing
        _check_guard(old);engine.require_runtime('hero_optimize')
        if any(item.get('project_id')==old['project_id'] and item.get('status') in ('running','queued') for item in all_records('job')):
            raise engine.JobConflictError('已有任务执行中，请稍后重试。')
        compared=old.get('hero_comparison',{})
        confidence=compared.get('confidence') if isinstance(compared,dict) else None
        resume=bool(old.get('resume_from') or old.get('status') in ('failed','interrupted','cancelled')
                    and isinstance(old.get('hero_plan'),dict) and isinstance(old['hero_plan'].get('recipe'),dict)
                    and compared.get('source')=='ai' and compared.get('decision')=='candidate'
                    and type(confidence) in (int,float) and math.isfinite(confidence) and .65<=confidence<=1)
        if old.get('refresh_baseline') and not resume:engine.require_render_runtime(old['plan'],old['shot_ids'])
        job={**old,'id':uuid.uuid4().hex,'status':'queued','stage':'等待继续主图优化','workflow_phase':'inspecting',
             'error':None,'progress':0,'sample_progress':None,'completed':[],'log_tail':[],
             'hero_candidates':[],'llm_calls':0,'optimization_summary':'','optimization_decision':None,
             'created_at':time.time(),'retry_of':old['id']}
        for key in ('finished_at','started_at','adopted_version','automatic_previous_preview'):job.pop(key,None)
        if resume:
            job.update(resume_from=old.get('resume_from') or old['id'],
                       resume_final_from=old.get('resume_final_from') or old['id'],
                       recovery_status='pending',stage='等待核验已完成结果')
        else:
            for key in ('resume_from','resume_final_from','recovery_status','recovery_summary','scene_verification'):
                job.pop(key,None)
        put('job',job);engine.QUEUE.put(job['id']);return job

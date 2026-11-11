"""Direct, snapshot-bound material edits; no external AI service is required."""
import copy
import time
import uuid
import engine
from planner import PRESETS, validate
from studio_store import LOCK, get, put, all_records

EDITABLE={'preset','color','roughness','metallic','transmission'}


def ensure_idle(project_id):
    if any(job['project_id']==project_id and job['status'] in ('queued','running') for job in all_records('job')):
        raise ValueError('请等待当前渲染结束，或先取消任务，再修改部件材质')


def apply_selection(project_id, body):
    if not isinstance(body,dict): raise ValueError('材质编辑请求格式无效')
    reset=body.get('reset',False)
    if type(reset) is not bool: raise ValueError('恢复材质选项无效')
    patch=body.get('patch',{})
    if not isinstance(patch,dict) or set(patch)-EDITABLE: raise ValueError('包含不支持的材质参数')
    if reset and patch: raise ValueError('恢复原材质时不能同时提交参数修改')
    if not reset and not patch: raise ValueError('请至少选择一个需要修改的材质参数')
    if 'preset' in patch and patch['preset'] not in PRESETS: raise ValueError('未知材质预设')
    with LOCK:
        project=get('project',project_id)
        if type(body.get('version')) is not int or body['version']!=project['version']:
            raise ValueError('图片不是当前方案版本，请先生成最新预览再编辑材质')
        if not project.get('analysis'): raise ValueError('请先完成模型分析')
        ensure_idle(project_id)
    # Coordinates are resolved again against the saved scene. Caller-supplied
    # object/material names never authorize a write to an unselected part.
    picked=engine.pick_output(project_id,body)
    hits=picked.get('hits',[])
    if not hits or any(not hit.get('hit') for hit in hits):
        raise ValueError('部分位置没有命中产品，请移除背景上的点后重试')
    if any(not isinstance(hit.get('object_name'),str) or not hit['object_name'] or not isinstance(hit.get('material_name'),str) or not hit['material_name'] for hit in hits):
        raise ValueError('无法确认选中部件的材质槽，请换一个位置')
    if not reset and 'preset' not in patch and any(not hit.get('material_info',{}).get('editable',False) for hit in hits):
        raise ValueError('选中材质的着色器结构不支持直接修改参数，请先选择内置材质预设')
    pairs=list(dict.fromkeys((hit['object_name'],hit['material_name']) for hit in hits))
    updated=copy.deepcopy(project['plan'])
    local=updated.setdefault('object_material_overrides',[])
    if reset:
        if not any((entry['object_name'],entry['material_name']) in pairs for entry in local):
            raise ValueError('选中的部件没有局部材质修改，无需恢复')
        local[:]=[entry for entry in local if (entry['object_name'],entry['material_name']) not in pairs]
    else:
        for object_name,material_name in pairs:
            existing=next((entry for entry in local if entry['object_name']==object_name and entry['material_name']==material_name),None)
            entry=copy.deepcopy(existing) if existing else {'object_name':object_name,'material_name':material_name,'preserve_original':True}
            if 'preset' in patch:
                # An explicit preset replaces old local surface settings; the
                # selected color is retained unless the caller changes it too.
                for key in ('roughness','metallic','transmission'):
                    if key not in patch: entry.pop(key,None)
                entry['preserve_original']=False
                for key in ('textures','resource','license','texture_scale','texture_rotation'):
                    entry.pop(key,None)
            elif entry.get('textures'):
                # An explicit scalar replaces that downloaded channel; leaving
                # it connected would silently ignore the user's slider change.
                for key in patch:
                    entry['textures'].pop('base_color' if key=='color' else key,None)
            entry.update(copy.deepcopy(patch));entry['source']='user'
            local[:]=[old for old in local if (old['object_name'],old['material_name'])!=(object_name,material_name)]
            local.append(entry)
    updated=validate(updated)
    if updated==project['plan']: raise ValueError('选中的部件材质没有需要保存的变化')
    updated['source']='user'
    event={'id':uuid.uuid4().hex,'kind':'reset' if reset else 'edit','source':{key:body.get(key) for key in ('output_id','shot_id','quality','version')},
           'targets':[{'object_name':obj,'material_name':mat} for obj,mat in pairs], 'patch':copy.deepcopy(patch),'at':time.time()}
    with LOCK:
        current=get('project',project_id)
        if current['version']!=project['version']:
            raise ValueError('方案已被其他操作修改，本次材质编辑未应用，请刷新后重试')
        ensure_idle(project_id)
        original=copy.deepcopy(current)
        current.setdefault('history',[]).append({'version':current['version'],'plan':copy.deepcopy(current['plan']),'at':time.time(),
            'reason':'恢复选中部件的局部材质' if reset else '直接编辑选中部件材质：'+', '.join(patch)})
        current['plan']=updated;current['version']+=1
        event['version']=current['version']
        current['material_edits']=(current.get('material_edits',[])+[event])[-100:]
        put('project',current)
        try:
            job=engine.submit(project_id,'preview','preview',[body['shot_id']],request_key='material-edit:'+event['id'])
        except Exception:
            # Do not leave a saved-but-not-queued edit when validation fails.
            put('project',original)
            raise
        event['job_id']=job['id'];put('project',current)
    return engine.project_view(project_id)

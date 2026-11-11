"""Snapshot-bound diagnostic proposals: generate, inspect, then explicitly apply."""
import copy
import hashlib
import json
import math
import re
import time
import uuid

import ai_client
import ai_config
import engine
from planner import validate
from studio_store import DATA, LOCK, get, put, all_records

SNAPSHOT=('output_id','shot_id','quality','version','review_checked_at')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def idle(project_id):
    if any(j['project_id']==project_id and j['status'] in ('queued','running') for j in all_records('job')):
        raise ValueError('请等待当前渲染结束或取消任务，再处理诊断建议')


def source_output(project,body):
    if type(body.get('version')) is not int or body['version']!=project['version']:
        raise ValueError('诊断图片不是当前方案版本，请生成最新预览后再处理建议')
    outputs=[o for o in project.get('outputs',[]) if o.get('job_id')==body.get('output_id') and all(o.get(k)==body.get(k) for k in ('shot_id','quality','version'))]
    if len(outputs)!=1: raise ValueError('请选择明确的已生成镜头版本')
    output=outputs[0];review=output.get('review')
    if not isinstance(review,dict): raise ValueError('请先完成这张图的诊断')
    checked=body.get('review_checked_at')
    if type(checked) not in (int,float) or not math.isfinite(checked) or checked!=review.get('checked_at'):
        raise ValueError('诊断已更新，请核对最新问题后重新生成修改方案')
    return output,review


def normalized_input(body,review):
    text=body.get('instruction','')
    if not isinstance(text,str) or len(text)>2000: raise ValueError('补充要求不能超过2000字')
    questions=review.get('needs_user',[]);causes=review.get('realism',{}).get('causes',[])
    answers=body.get('answers',[]);selected=body.get('selected_causes',[])
    if not isinstance(answers,list) or len(answers)>20: raise ValueError('问题回答数量无效')
    cleaned=[];seen=set()
    for answer in answers:
        if not isinstance(answer,dict) or set(answer)-{'index','status','text'}: raise ValueError('问题回答格式无效')
        index=answer.get('index');status=answer.get('status');reply=answer.get('text','')
        if type(index) is not int or not 0<=index<len(questions) or index in seen: raise ValueError('问题引用无效或重复')
        if status not in ('confirmed','answered','skipped') or not isinstance(reply,str) or len(reply)>1000: raise ValueError('回答状态或文字无效')
        if status=='answered' and not reply.strip(): raise ValueError('请填写回复内容')
        seen.add(index);cleaned.append({'index':index,'status':status,'text':reply.strip(),'question':questions[index]})
    if not isinstance(selected,list) or len(selected)>8 or any(type(i) is not int or not 0<=i<len(causes) for i in selected) or len(set(selected))!=len(selected):
        raise ValueError('诊断原因引用无效或重复')
    return text.strip(),cleaned,selected


def candidate_for(project,shot_id,patch):
    """AI sees effective values; map only this shot's light edits back to the plan."""
    before=validate(project['plan']);effective=engine.effective_plan(before,shot_id)
    # Validate all fields/references with the ordinary strict plan executor first.
    ai_client.apply_patch(effective,patch,project.get('analysis'))
    scoped={k:copy.deepcopy(v) for k,v in patch.items() if k not in ('exposure','light_softness','lighting')}
    result=ai_client.apply_patch(before,scoped,project.get('analysis'))
    adjustments=copy.deepcopy(before.get('shot_adjustments',{}))
    item=copy.deepcopy(adjustments.get(shot_id,{}))
    if 'exposure' in patch and patch['exposure']!=effective['exposure']:
        item['exposure_delta']=patch['exposure']-before['exposure']
    if 'light_softness' in patch and patch['light_softness']!=effective['light_softness']:
        item['softness_multiplier']=patch['light_softness']/before['light_softness']
    if 'lighting' in patch:
        lighting=copy.deepcopy(item.get('lighting',{}))
        for key,value in patch['lighting'].items():
            if value!=effective.get('lighting',{}).get(key,1): lighting[key]=value
        if lighting: item['lighting']=lighting
    if item: adjustments[shot_id]=item
    if adjustments: result['shot_adjustments']=adjustments
    result=validate(result)
    changes=ai_client.parameter_diff(before,result)
    if before.get('shot_adjustments',{})!=result.get('shot_adjustments',{}):
        changes.append({'path':'shot_adjustments.'+shot_id,'before':before.get('shot_adjustments',{}).get(shot_id,{}),'after':result.get('shot_adjustments',{}).get(shot_id,{})})
    return result,changes


def selected_metal_changes(project, output, review, instruction, answers, selected):
    """Bounded correction for visible, directly editable and identifiable metal.

    A checked metal diagnosis authorizes a conservative scalar trial. An explicit
    brushed/polished instruction additionally selects the requested preset. No
    diagnosis text may authorize color or geometry edits.
    """
    causes=review.get('realism',{}).get('causes',[])
    selected_text=' '.join(' '.join(str(causes[i].get(k,'')) for k in ('aspect','evidence','suggestion')) for i in selected)
    explicit=' '.join([instruction]+[a.get('text','') for a in answers if a.get('status')=='answered'])
    text=explicit+' '+selected_text
    if not re.search(r'金属|不锈钢|铝|metal|steel|aluminium|aluminum',text,re.I): return []
    if re.search(r'不要|不改|保留原材质|不修改材质|不要修改材质',explicit): return []
    preset='brushed_metal' if re.search(r'拉丝|brushed',explicit,re.I) else 'polished_metal' if re.search(r'抛光|polished',explicit,re.I) else None
    # Name-specific language narrows the correction; never extend to unseen slots.
    target_text=explicit if re.search(r'台面|桌面|countertop|铝条|拉条|抽屉条|handle strip|所有金属|全部金属',explicit,re.I) else selected_text
    top=bool(re.search(r'台面|桌面|countertop',target_text,re.I))
    strips=bool(re.search(r'铝条|拉条|抽屉条|handle strip',target_text,re.I))
    url=output.get('result_url','')
    if not isinstance(url,str) or not url.startswith('/files/'): return []
    path=(DATA/url[7:]).resolve()
    if (DATA/project['id']).resolve() not in path.parents or not path.is_file(): return []
    try: result=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError): return []
    if not result.get('success'): return []
    known={(o.get('name'),m) for o in project.get('analysis',{}).get('objects',[]) if o.get('type')=='MESH' for m in ai_client._object_materials(o)}
    changes=[];seen=set()
    for item in result.get('visible_materials',[]):
        if not isinstance(item,dict): continue
        obj=item.get('object_name');mat=item.get('material_name'); pair=(obj,mat)
        info=item.get('material_info',{})
        if pair in seen or pair not in known or not isinstance(info,dict) or info.get('editable') is not True: continue
        if not isinstance(obj,str) or not isinstance(mat,str): continue
        if not re.search(r'金属|不锈钢|铝|metal|steel|aluminium|aluminum',obj+' '+mat,re.I): continue
        if top or strips:
            if not ((top and re.search(r'台面|桌面|countertop',obj,re.I)) or (strips and re.search(r'铝条|拉条|抽屉条|strip',obj,re.I))): continue
        linked=info.get('linked',{})
        if not isinstance(linked,dict) or linked.get('roughness') is not False or linked.get('metallic') is not False: continue
        if type(info.get('roughness')) not in (int,float) or type(info.get('metallic')) not in (int,float): continue
        if preset:
            change={'object_name':obj,'material_name':mat,'preset':preset}
        elif info['metallic'] < .5:
            change={'object_name':obj,'material_name':mat,'metallic':1.0,'roughness':.35}
        else: continue
        seen.add(pair);changes.append(change)
    return changes


def propose(project_id,body):
    if not isinstance(body,dict): raise ValueError('修改方案请求格式无效')
    with LOCK:
        project=get('project',project_id);output,review=source_output(project,body);idle(project_id)
        instruction,answers,selected=normalized_input(body,review)
        source={key:body[key] for key in SNAPSHOT}
        review_hash=fingerprint(review);plan_hash=fingerprint(project['plan'])
        request_hash=fingerprint({'source':source,'review':review_hash,'plan':plan_hash,'instruction':instruction,'answers':answers,'selected_causes':selected})
        existing=next((p for p in reversed(project.get('review_proposals',[])) if p.get('request_hash')==request_hash),None)
        if existing:return {'proposal':existing}
    image_path=(DATA/output['png_url'][7:]).resolve()
    if (DATA/project_id).resolve() not in image_path.parents or not image_path.is_file(): raise ValueError('诊断图片不存在，请重新预览')
    engine.verify_image(image_path,'PNG')
    config=ai_config.get_config();effective=engine.effective_plan(validate(project['plan']),output['shot_id'])
    if config.get('enabled') and config.get('api_key') and config.get('model'):
        try:
            prior=next((p for p in reversed(project.get('review_proposals',[])) if p.get('review_hash')==review_hash and p.get('source')==source),None)
            previous={k:prior.get(k) for k in ('summary','needs_user','manual_actions')} if prior else None
            suggestion=ai_client.request_review_actions(effective,project.get('analysis'),image_path,review,answers,instruction,selected,output['shot_id'],config,previous=previous)
            ai_config.record_result(config,True,'已生成可检查的诊断修改方案，尚未应用。')
            origin='ai'
        except ValueError as exc:
            ai_config.record_result(config,False,str(exc))
            explicit=' '.join([instruction]+[a.get('text','') for a in answers if a.get('status')=='answered'])
            # This fallback interprets ONLY an explicit built-in metal finish;
            # do not claim that other free-form instructions were understood.
            has_explicit_finish=bool(re.search(r'(?:拉丝|抛光).{0,6}(?:金属|不锈钢|铝)|(?:金属|不锈钢|铝).{0,6}(?:拉丝|抛光)|brushed\s+metal|polished\s+metal',explicit,re.I))
            fallback=selected_metal_changes(project,output,review,instruction,answers,selected) if has_explicit_finish else []
            if not fallback: raise
            suggestion={'patch':{'object_material_overrides':fallback},
                'summary':'AI 修改方案无效或服务不可用；本地规则仅按明确的金属材质要求生成局部方案，其他设置未改。',
                'needs_user':[],'manual_actions':['其他自然语言要求未由本地规则解析；请检查参数变化。']}
            origin='rules'
    else:
        # Offline mode never interprets a material answer as a hidden model call.
        patch={k:v for k,v in (review.get('patch') or {}).items() if k in ('exposure','light_softness')}
        if not selected: patch={}
        suggestion={'patch':patch,'summary':'采用已记录的曝光/柔光建议；回答已保存。','needs_user':[],'manual_actions':[]}
        if instruction or any(a['status']!='skipped' for a in answers): suggestion['needs_user'].append('内置规则不会理解自由回复；配置AI后可据此生成材质和灯光修改方案。')
        if selected: suggestion['manual_actions']=['当前离线模式仅能采用诊断中明确的曝光和柔光参数；其余建议需人工处理。']
        origin='rules'
    # Fall back to a narrowly scoped, evidence-backed metal correction when the
    # model overlooks an explicit material request or a checked metal issue.
    # The final patch still goes through the normal schema and snapshot checks.
    safe_changes=selected_metal_changes(project,output,review,instruction,answers,selected)
    patch=suggestion['patch']
    intent_text=instruction+' '+' '.join(a.get('text','') for a in answers if a.get('status')=='answered')+' '+' '.join(
        ' '.join(str(review['realism']['causes'][i].get(k,'')) for k in ('aspect','evidence','suggestion')) for i in selected)
    if re.search(r'金属|不锈钢|铝|metal|steel|aluminium|aluminum',intent_text,re.I):
        allowed={(m['object_name'],m['material_name']) for m in safe_changes}
        metal=lambda value: bool(re.search(r'金属|不锈钢|铝|metal|steel|aluminium|aluminum',value,re.I))
        # A shared material name may also be used by wheels, fasteners, etc.
        # Convert metal work to the verified object/material pairs only.
        # Never accept any model-proposed global override for a scoped metal
        # task: wildcard/alias names can address unrelated shared materials.
        patch.pop('material_overrides',None)
        if 'object_material_overrides' in patch:
            patch['object_material_overrides']=[m for m in patch['object_material_overrides']
                if not metal(m['object_name']+' '+m['material_name']) or (m['object_name'],m['material_name']) in allowed]
        local=patch.setdefault('object_material_overrides',[])
        for safe in safe_changes:
            old=next((m for m in local if (m['object_name'],m['material_name'])==(safe['object_name'],safe['material_name'])),None)
            if old is None: local.append(safe)
            else: old.update(safe)
        if not local: patch.pop('object_material_overrides')
        if safe_changes: suggestion['summary']+='；已对实际可见、可编辑的金属部件补充材质调整，保留原色。'
    candidate,changes=candidate_for(project,output['shot_id'],suggestion['patch'])
    proposal={'id':uuid.uuid4().hex,'source':source,'base_version':project['version'],'status':'proposed',
        'summary':suggestion['summary'],'needs_user':suggestion['needs_user'],'manual_actions':suggestion['manual_actions'],
        'changes':changes,'can_apply':bool(changes),'answers':answers,'instruction':instruction,'selected_causes':selected,
        'created_at':time.time(),'origin':origin,'candidate_plan':candidate,'patch':copy.deepcopy(suggestion['patch']),
        'review_hash':review_hash,'base_plan_hash':plan_hash,'request_hash':request_hash}
    with LOCK:
        current=get('project',project_id);_,latest=source_output(current,body);idle(project_id)
        if fingerprint(latest)!=review_hash or fingerprint(current['plan'])!=plan_hash: raise ValueError('诊断或方案已变化，请重新生成修改方案')
        existing=next((p for p in current.get('review_proposals',[]) if p.get('request_hash')==request_hash),None)
        if existing:return {'proposal':existing}
        current['review_proposals']=(current.get('review_proposals',[])+[proposal])[-50:]
        put('project',current)
    return {'proposal':proposal}


def apply(project_id,body):
    if not isinstance(body,dict) or set(body)!={'proposal_id'} or not isinstance(body['proposal_id'],str): raise ValueError('请选择已生成的修改方案')
    with LOCK:
        project=get('project',project_id)
        proposal=next((p for p in project.get('review_proposals',[]) if p['id']==body['proposal_id']),None)
        if not proposal: raise ValueError('修改方案不存在，请重新生成')
        # Idempotence is checked before staleness: an already-applied click is safe.
        if proposal['status']=='applied': return engine.project_view(project_id)
        _,review=source_output(project,proposal['source']);idle(project_id)
        if fingerprint(review)!=proposal['review_hash'] or fingerprint(project['plan'])!=proposal['base_plan_hash']:
            raise ValueError('诊断或摄影参数已变化，请重新生成修改方案')
        if not proposal.get('can_apply') or not proposal.get('changes'): raise ValueError('没有可执行的参数变化，请回复待确认项或补充具体要求')
        candidate,changes=candidate_for(project,proposal['source']['shot_id'],proposal['patch'])
        if changes!=proposal['changes'] or candidate!=proposal['candidate_plan']: raise ValueError('修改方案校验失败，请重新生成')
        original=copy.deepcopy(project)
        project.setdefault('history',[]).append({'version':project['version'],'plan':copy.deepcopy(project['plan']),'at':time.time(),'reason':'应用诊断修改方案：'+proposal['summary'],'review_proposal_id':proposal['id']})
        candidate['source']=proposal['origin'];project['plan']=candidate;project['version']+=1
        proposal.update(status='applied',applied_at=time.time(),applied_version=project['version'],can_apply=False)
        put('project',project)
        try:
            job=engine.submit(project_id,'preview','preview',[proposal['source']['shot_id']],request_key='review-proposal:'+proposal['id'])
        except Exception:
            put('project',original);raise
        proposal['job_id']=job['id'];put('project',project)
    return engine.project_view(project_id)

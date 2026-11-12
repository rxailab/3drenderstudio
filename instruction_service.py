"""Snapshot-aware, idempotent human instructions; inference never holds LOCK.

The receipt and the changed plan are committed in the same project record.
Rendering remains a separate, version-bound request made by the client.
"""
import copy
import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path

import ai_client
import ai_config
import engine
import planner
import target_evidence
import scene_parts
from intelligence_rules import extract_protections, PROTECTION_LABELS
from studio_store import DATA, LOCK, get, put, all_records


class InstructionError(ValueError):
    def __init__(self, message, status_code=400, error_code=None, retryable=False):
        super().__init__(message)
        self.status_code=status_code
        self.error_code=error_code
        self.retryable=retryable


_INFLIGHT={}
SOURCE_FIELDS=('output_id','shot_id','quality','version')
PROPOSALS_KEY='_instruction_proposals'
MAX_PENDING_PROPOSALS=8
MAX_ERROR_DIAGNOSTICS=10
MAX_ERROR_DIAGNOSTIC_BYTES=32768
DEFAULT_INSTRUCTION_PROTOCOL='semantic_v2'


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _text(value, limit=1000):
    return value[:limit] if isinstance(value,str) else ''


def _diagnostic_word(value, limit=80):
    return value[:limit] if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_$\[\].:-]{1,500}',value) else ''


def _diagnostic_shape(value):
    """Keep response structure only; never store raw model text or images."""
    if not isinstance(value,dict):return {}
    result={key:_diagnostic_word(value.get(key)) for key in ('format','status') if _diagnostic_word(value.get(key))}
    for key in ('attempt','binding_count'):
        if type(value.get(key)) is int and 0<=value[key]<=1000:result[key]=value[key]
    for key in ('keys','patch_keys'):
        if isinstance(value.get(key),list):
            result[key]=list(dict.fromkeys(_diagnostic_word(item) for item in value[key][:40] if _diagnostic_word(item)))
    rows=value.get('requirement_rows')
    if isinstance(rows,list):
        result['requirement_rows']=[]
        for row in rows[:20]:
            if not isinstance(row,dict):continue
            clean={key:_diagnostic_word(row.get(key)) for key in ('id','status') if _diagnostic_word(row.get(key))}
            if isinstance(row.get('patch_keys'),list):
                clean['patch_keys']=list(dict.fromkeys(_diagnostic_word(item) for item in row['patch_keys'][:40] if _diagnostic_word(item)))
            if type(row.get('binding_count')) is int and 0<=row['binding_count']<=1000:clean['binding_count']=row['binding_count']
            if row.get('intent_kind') in ('visual_goal','adjustment','preservation'):
                clean['intent_kind']=row['intent_kind']
            for key,allowed in (('intent_domains',{'lighting','camera','framing','background','material','pose'}),
                                ('intent_protections',set(PROTECTION_LABELS))):
                if isinstance(row.get(key),list):
                    clean[key]=list(dict.fromkeys(item for item in row[key] if isinstance(item,str) and item in allowed))
            result['requirement_rows'].append(clean)
    return result


def _diagnostic_record(value):
    if (not isinstance(value,dict) or value.get('schema')!=1
            or not isinstance(value.get('id'),str) or not re.fullmatch(r'[0-9a-f]{32}',value['id'])
            or value.get('error_code') not in ('instruction_response_invalid','instruction_requirement_unresolved') or value.get('applied') is not False
            or type(value.get('plan_version')) is not int or value['plan_version']<1):return None
    clean={'schema':1,'id':value['id'],'error_code':value['error_code'],'applied':False,
           'plan_version':value['plan_version'],'text':_text(value.get('text'),2000)}
    if type(value.get('created_at')) in (int,float) and math.isfinite(value['created_at']):clean['created_at']=value['created_at']
    source=value.get('source_snapshot')
    if target_evidence._snapshot(source):clean['source_snapshot']=copy.deepcopy(source)
    feedback=value.get('feedback')
    if isinstance(feedback,dict):clean['feedback']={key:_diagnostic_word(feedback.get(key),500 if key=='path' else 80)
                                                   for key in ('code','path') if _diagnostic_word(feedback.get(key),500 if key=='path' else 80)}
    shapes=value.get('response_shapes')
    if isinstance(shapes,list):clean['response_shapes']=[_diagnostic_shape(shape) for shape in shapes[:2]]
    if value['error_code']=='instruction_requirement_unresolved':
        rows=value.get('failure_details',[])
        clean['failure_details']=[]
        if isinstance(rows,list):
            for row in rows[:20]:
                if not isinstance(row,dict) or row.get('code')!='semantic_status_effect':continue
                entry={'code':'semantic_status_effect'}
                if row.get('effect') in ('known_unchanged','known_change','unverified'):entry['effect']=row['effect']
                if type(row.get('plan_changed')) is bool:entry['plan_changed']=row['plan_changed']
                ids=row.get('target_ids')
                if isinstance(ids,list):
                    entry['target_ids']=list(dict.fromkeys(ident for ident in ids[:160]
                        if isinstance(ident,str) and re.fullmatch(r't[1-9][0-9]{0,3}',ident)))
                clean['failure_details'].append(entry)
        digest=value.get('evidence_digest')
        if isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest):clean['evidence_digest']=digest
    if value.get('structure_truncated') is True:clean['structure_truncated']=True
    # Account for the actual indented UTF-8 file size, including long unknown
    # response keys. Retain the user's original text while trimming structure.
    while len(json.dumps(clean,ensure_ascii=False,indent=2).encode('utf-8'))>MAX_ERROR_DIAGNOSTIC_BYTES:
        shapes=clean.get('response_shapes',[])
        if not shapes:return None
        clean['structure_truncated']=True
        largest=max(shapes,key=lambda shape:len(json.dumps(shape,ensure_ascii=False)))
        if largest.get('requirement_rows'):largest['requirement_rows'].pop()
        else:shapes.remove(largest)
    return clean


def _error_folder(project_id):
    project_root=(DATA/project_id).resolve();folder=project_root/'instruction-errors'
    if project_root not in folder.resolve().parents:raise ValueError('诊断目录无效')
    return folder


def _record_response_error(project,text,source,error,evidence=None):
    """Best-effort local evidence for a definitely uncommitted protocol error."""
    pending=getattr(error,'pending',None)
    public=target_evidence.public_evidence(evidence,project.get('analysis'),project['version']) if evidence else {}
    allowed_ids={target['id'] for target in public.get('targets',[])}
    failures=[{'code':row.get('diagnostic_code'),'effect':row.get('diagnostic_effect'),
               'plan_changed':row.get('diagnostic_plan_changed'),
               'target_ids':[ident for ident in row.get('diagnostic_target_ids',[])[:160]
                             if isinstance(ident,str) and ident in allowed_ids] if isinstance(row.get('diagnostic_target_ids'),list) else []}
              for row in pending if isinstance(row,dict) and row.get('diagnostic_code')=='semantic_status_effect'] if isinstance(pending,list) else []
    code='instruction_requirement_unresolved' if failures else getattr(error,'error_code',None)
    if code!='instruction_requirement_unresolved' and (code!='instruction_response_invalid'
            or getattr(error,'applied',None) is not False):return
    ident=uuid.uuid4().hex
    try:evidence_digest=_digest(public) if public else None
    except (TypeError,ValueError):evidence_digest=None
    record=_diagnostic_record({'schema':1,'id':ident,'error_code':code,'applied':False,
                              'plan_version':project['version'],'text':text,'source_snapshot':source,
                              'created_at':time.time(),'feedback':getattr(error,'feedback',{}),
                              'response_shapes':getattr(error,'response_shapes',[]),
                              'failure_details':failures,'evidence_digest':evidence_digest})
    if record is None:return
    try:
        folder=_error_folder(project['id']);folder.mkdir(parents=True,exist_ok=True)
        path=folder/(ident+'.json')
        if path.exists() or path.is_symlink():return
        from studio_store import atomic_json
        atomic_json(path,record)
        error.diagnostic_id=ident
        files=sorted((item for item in folder.glob('*.json') if re.fullmatch(r'[0-9a-f]{32}\.json',item.name)
                      and not item.is_symlink() and item.is_file()),key=lambda item:item.stat().st_mtime_ns,reverse=True)
        for old in files[MAX_ERROR_DIAGNOSTICS:]:old.unlink(missing_ok=True)
    except (OSError,ValueError,TypeError):
        # A log failure must not turn a known pre-commit rejection into an
        # uncertain save or change the user's instruction outcome.
        return


def error_diagnostics(project_id):
    """Return only bounded, sanitized records for the existing project bundle."""
    try:
        folder=_error_folder(project_id)
        if not folder.is_dir():return []
        files=sorted((item for item in folder.glob('*.json') if re.fullmatch(r'[0-9a-f]{32}\.json',item.name)
                      and not item.is_symlink() and item.is_file()),key=lambda item:item.stat().st_mtime_ns,reverse=True)
    except (OSError,ValueError):return []
    records=[]
    for path in files[:MAX_ERROR_DIAGNOSTICS]:
        try:
            if path.stat().st_size>MAX_ERROR_DIAGNOSTIC_BYTES:continue
            record=_diagnostic_record(json.loads(path.read_text(encoding='utf-8')))
            if record and path.name==record['id']+'.json':records.append((path.name,record))
        except (OSError,ValueError,UnicodeError):continue
    return records


def public_project(project):
    """Unapplied candidates and evidence fingerprints stay on the server."""
    result=copy.deepcopy(project)
    result.pop(PROPOSALS_KEY,None)
    return result


def _key_hash(request_key, required=False):
    if required and request_key is None:
        raise InstructionError('确认修改需要独立的请求标识，请重新点击确认。')
    if request_key is not None and (not isinstance(request_key,str) or not request_key.strip() or len(request_key)>200
                                    or any(ord(char)<32 for char in request_key)):
        raise InstructionError('Idempotency-Key 格式无效。')
    return _digest(request_key) if request_key is not None else None


def _idle(project_id):
    if any(item.get('project_id')==project_id and item.get('status') in ('queued','running') for item in all_records('job')):
        raise InstructionError('当前项目已有渲染任务，请等待完成或取消后再应用调整。',409)
    if any(item.get('project_id')==project_id and item.get('status') in ('queued','running','rendering') for item in all_records('material_task')):
        raise InstructionError('当前项目正在处理材质，请等待完成或取消后再应用调整。',409)


def _parse(body, request_key):
    if not isinstance(body,dict) or set(body)-{'text','expected_version','shot_id','source','target_points','partial_mode','protocol'}:
        raise InstructionError('画面调整请求格式无效。')
    if body.get('protocol',DEFAULT_INSTRUCTION_PROTOCOL) not in ('semantic_v2','legacy_v1'):
        raise InstructionError('画面调整协议版本无效。')
    if 'partial_mode' in body and body['partial_mode'] not in ('apply_ready','review'):
        raise InstructionError('partial_mode 必须为 apply_ready 或 review。')
    text=body.get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise InstructionError('请用不超过 2000 字描述要进行的调整。')
    expected=body.get('expected_version')
    if 'expected_version' in body and (type(expected) is not int or expected<1):
        raise InstructionError('expected_version 必须为有效的方案版本整数。')
    shot_id=body.get('shot_id')
    if 'shot_id' in body and (not isinstance(shot_id,str) or not shot_id or len(shot_id)>32):
        raise InstructionError('请选择一个有效的当前镜头。')
    source=body.get('source')
    if source is not None:
        if not isinstance(source,dict) or set(source)!=set(SOURCE_FIELDS):
            raise InstructionError('预览来源必须是明确的任务、镜头、画质与版本。')
        if (not isinstance(source['output_id'],str) or not source['output_id'] or len(source['output_id'])>128
                or not isinstance(source['shot_id'],str) or not source['shot_id'] or len(source['shot_id'])>32
                or source['quality'] not in ('preview','standard','fine','hero')
                or type(source['version']) is not int or source['version']<1):
            raise InstructionError('预览来源格式无效。')
        if not shot_id or source['shot_id']!=shot_id:
            raise InstructionError('预览来源与当前镜头不一致。')
    if 'target_points' in body:
        points=body['target_points']
        if (source is None or not isinstance(points,list) or not 1<=len(points)<=12
                or any(not isinstance(point,dict) or set(point)!={'id','x','y'}
                       or not isinstance(point['id'],str) or not 1<=len(point['id'])<=64
                       or any(type(point[key]) not in (int,float) or not math.isfinite(point[key])
                              or not 0<=point[key]<=1 for key in ('x','y')) for point in points)
                or len({point['id'] for point in points})!=len(points)):
            raise InstructionError('示例选点必须是当前预览上的 1–12 个有效位置。')
    return text.strip(),expected,shot_id,source,_digest(body),_key_hash(request_key)


def _parse_confirmation(body, request_key):
    if not isinstance(body,dict) or set(body)!={'proposal_id','expected_version'}:
        raise InstructionError('请确认服务器提供的方案，不要另行提交修改参数。')
    ident=body['proposal_id'];expected=body['expected_version']
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident):
        raise InstructionError('待确认方案标识无效，请重新提交调整要求。')
    if type(expected) is not int or expected<1:
        raise InstructionError('expected_version 必须为有效的方案版本整数。')
    return ident,expected,_digest({'action':'confirm_instruction_proposal','body':body}),_key_hash(request_key,True)


def _receipt(project, key_hash, request_digest):
    if key_hash is None: return None
    existing=next((entry for entry in project.get('instruction_receipts',[]) if entry.get('key_hash')==key_hash),None)
    if existing:
        if existing.get('request_digest')!=request_digest:
            raise InstructionError('同一个请求标识不能用于不同的调整内容，请重新提交新操作。',409)
        return copy.deepcopy(existing['receipt'])
    return None


def _response(project_id, receipt, replayed=False):
    project=public_project(engine.project_view(project_id))
    project['instruction_result']={**copy.deepcopy(receipt),'replayed':replayed}
    return project


def _stale_proposal():
    return InstructionError('方案或预览已变化，这份待确认修改已失效。请重新提交当前要求。',409,
                            error_code='instruction_proposal_stale')


def _file_digest(path):
    """Detect replacement at the same URL, including while the file is read."""
    before=path.stat();digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise _stale_proposal()
    return digest.hexdigest()


def _proposal_guard(project, source, reference_path=None, inspection=False, seed_pairs=None, lighting_inspection=False):
    """A confirmation is bound to the exact facts used to prepare its plan."""
    state={'plan':_digest(project['plan']),'analysis':_digest(project.get('analysis')),
           'metadata':_digest(project.get('metadata',{}))}
    if source:
        output,path=_source_output(project,source)
        state['output']={key:output.get(key) for key in ('job_id','shot_id','quality','version','png_url','result_url','blend_url')}
        state['image']=_file_digest(path)
        state['evidence']=_digest(target_evidence.load_evidence(DATA,project,output))
        directions=_current_light_directions(project,output)
        if directions:state['light_directions']=_digest(directions)
        url=output.get('blend_url')
        if isinstance(url,str) and url.startswith('/files/'):
            blend=(DATA/url[7:]).resolve()
            if (DATA/project['id']).resolve() in blend.parents and blend.suffix.lower()=='.blend' and blend.is_file():
                stat=blend.stat()
                state['scene']={'url':url,'size':stat.st_size,'modified_ns':stat.st_mtime_ns}
                if lighting_inspection:state['light_direction_scene_sha256']=_file_digest(blend)
        if inspection:
            state['target_inspection']=scene_parts.guard(DATA,project,output,seeds=seed_pairs)
    if reference_path:
        state['reference']={'path':str(reference_path),'digest':_file_digest(reference_path)}
    return state


def _check_proposal_guard(project, proposal):
    if project['version']!=proposal['base_version']:
        raise _stale_proposal()
    try:
        reference_path=(_available_reference(project) if 'reference' in proposal['guard'] else None) if proposal.get('protocol')=='semantic_v2' else _reference(project,proposal['text'])
        current=_proposal_guard(project,proposal.get('source_snapshot'),reference_path,
                                inspection='target_inspection' in proposal['guard'],
                                seed_pairs=proposal['guard'].get('target_inspection',{}).get('identity',{}).get('target_seeds'),
                                lighting_inspection='light_direction_scene_sha256' in proposal['guard'])
    except (OSError,ValueError) as exc:
        raise _stale_proposal() from exc
    if current!=proposal['guard']:
        raise _stale_proposal()


def _validated_semantic_proposal(outcome, text):
    """Recheck a server-created semantic subset without interpreting language.

    Quoted source spans, requirement identities, declared locks and the exact
    execution/remaining text are data contracts. No wording decides which
    controls a requirement may use here.
    """
    import semantic_planner
    proposal=outcome.proposal
    intents=proposal.get('semantic_intents')
    if not isinstance(intents,list) or not 1<=len(intents)<=20:
        raise ai_client.ResponseContractError(code='semantic_requirement_shape')
    seen=set()
    for intent in intents:
        if (not isinstance(intent,dict) or not isinstance(intent.get('id'),str)
                or not intent['id'] or intent['id'] in seen
                or not isinstance(intent.get('quote'),str) or not intent['quote']
                or intent.get('kind') not in semantic_planner.KINDS
                or not isinstance(intent.get('interpretation'),str)
                or not isinstance(intent.get('depends_on'),list)
                or any(not isinstance(ident,str) for ident in intent['depends_on'])
                or not isinstance(intent.get('domains'),list)
                or any(domain not in semantic_planner.DOMAINS for domain in intent['domains'])
                or not isinstance(intent.get('protections'),list)
                or any(lock not in PROTECTION_LABELS for lock in intent['protections'])):
            raise ai_client.ResponseContractError(code='semantic_requirement_shape')
        seen.add(intent['id'])
    try:
        spans=semantic_planner.source_spans(text,intents)
    except ai_client.SchemaValidationError as exc:
        raise ai_client.ResponseContractError(code='semantic_source_coverage') from exc
    if spans!=proposal.get('source_spans'):
        raise ai_client.ResponseContractError(code='semantic_source_coverage')
    by_id={item['id']:item for item in intents};covered=set()
    visiting=set();visited=set()
    def visit(ident):
        if ident in visiting:
            raise ai_client.ResponseContractError(code='semantic_dependency_cycle')
        if ident in visited:return
        visiting.add(ident);dependencies=by_id[ident]['depends_on']
        if len(dependencies)!=len(set(dependencies)) or ident in dependencies or set(dependencies)-seen:
            raise ai_client.ResponseContractError(code='semantic_dependency_invalid')
        for dependency in dependencies:visit(dependency)
        visiting.remove(ident);visited.add(ident)
    for ident in by_id:visit(ident)
    result={'protocol':'semantic_v2','candidate_plan':copy.deepcopy(proposal['candidate_plan']),
            'source_spans':copy.deepcopy(spans),'semantic_intents':copy.deepcopy(intents)}
    for name,detail_key,statuses in (('ready','summary',('ready','already_satisfied')),
                                     ('pending','reason',('needs_clarification','unsupported'))):
        rows=proposal.get(name)
        if not isinstance(rows,list) or not 1<=len(rows)<=20:
            raise ai_client.ResponseContractError(code='semantic_requirement_shape')
        result[name]=[]
        for row in rows:
            if not isinstance(row,dict):
                raise ai_client.ResponseContractError(code='semantic_requirement_shape')
            ident=row.get('id');intent=by_id.get(ident) if isinstance(ident,str) else None
            if (not intent or ident in covered or row.get('quote')!=intent['quote']
                    or row.get('status') not in statuses
                    or not isinstance(row.get(detail_key),str) or not row[detail_key].strip()):
                raise ai_client.ResponseContractError(code='semantic_requirement_shape')
            if 'action' in row and row['action'] not in ('clarify_parameters','locate_part'):
                raise ai_client.ResponseContractError(code='semantic_requirement_shape')
            covered.add(ident);result[name].append(copy.deepcopy(row))
        if [row['id'] for row in rows]!=[intent['id'] for intent in intents if intent['id'] in {row['id'] for row in rows}]:
            raise ai_client.ResponseContractError(code='semantic_source_coverage')
    if covered!=seen:
        raise ai_client.ResponseContractError(code='semantic_source_coverage')
    ready_ids={row['id'] for row in result['ready']}
    if any(not set(by_id[row['id']]['depends_on'])<=ready_ids for row in result['ready']):
        raise ai_client.ResponseContractError(code='semantic_dependency_invalid')
    locks=getattr(outcome,'protections',[])
    if not isinstance(locks,list) or any(lock not in PROTECTION_LABELS for lock in locks):
        raise ai_client.ResponseContractError(code='semantic_protection_invalid')
    constraints=[]
    for intent in intents:
        if intent['kind']!='preservation':continue
        ready=next((row for row in result['ready'] if row['id']==intent['id']),None)
        if (not ready or ready['status']!='already_satisfied' or not intent['protections']
                or not set(intent['protections'])<=set(locks)):
            raise ai_client.ResponseContractError(code='semantic_preservation_unresolved')
        constraints.append(intent['quote'])
    if constraints!=proposal.get('semantic_constraints'):
        raise ai_client.ResponseContractError(code='semantic_constraint_scope')
    result['semantic_constraints']=constraints
    execution=[row['quote'] for row in result['ready'] if by_id[row['id']]['kind']!='preservation']
    expected={'execution_text':'，'.join(execution+constraints),
              'remaining_text':'，'.join([row['quote'] for row in result['pending']]+constraints)}
    for key,value in expected.items():
        if not value or proposal.get(key)!=value:
            raise ai_client.ResponseContractError(code='semantic_source_coverage')
        result[key]=value
    metadata=result['candidate_plan'].get('last_instruction',{})
    if (metadata.get('protocol')!='semantic_v2' or metadata.get('source_spans')!=spans
            or metadata.get('semantic_intents')!=intents
            or metadata.get('semantic_constraints')!=constraints
            or set(metadata.get('protections',[]))!=set(locks)):
        raise ai_client.ResponseContractError(code='semantic_constraint_scope')
    return result


def _validated_proposal(outcome, text):
    """Accept a planner-validated subset, never a client-supplied candidate."""
    proposal=getattr(outcome,'proposal',None)
    if not isinstance(proposal,dict) or not isinstance(proposal.get('candidate_plan'),dict):
        raise InstructionError('未获得可供确认的完整方案，本次尚未修改。')
    if proposal.get('protocol')=='semantic_v2':
        return _validated_semantic_proposal(outcome,text)
    requirements,constraints=ai_client._instruction_requirements(text)
    # Only the server's deterministic shared-predicate expansion can supply a
    # material clause that is not a literal substring of the original request.
    expanded={item['quote']:item['source_quote'] for item in requirements
              if isinstance(item.get('source_quote'),str) and item['source_quote'] in text}
    result={'candidate_plan':copy.deepcopy(proposal['candidate_plan'])};seen=set()
    for name,description in (('ready','summary'),('pending','reason')):
        rows=proposal.get(name)
        if not isinstance(rows,list) or not rows or len(rows)>32:
            raise InstructionError('待确认方案没有完整列出可完成项与待确认项，本次尚未修改。')
        result[name]=[]
        for row in rows:
            if not isinstance(row,dict): raise InstructionError('待确认方案格式无效，本次尚未修改。')
            ident=row.get('id');quote=row.get('quote');detail=row.get(description)
            if (not isinstance(ident,str) or not ident or len(ident)>100 or ident in seen
                    or not isinstance(quote,str) or not quote.strip() or (quote not in text and quote not in expanded)
                    or not isinstance(detail,str) or not detail.strip()):
                raise InstructionError('待确认项无法对应到这次原始要求，本次尚未修改。')
            seen.add(ident)
            item={'id':ident,'quote':quote,description:_text(detail,600)}
            if quote in expanded:
                if row.get('source_quote',expanded[quote])!=expanded[quote]:
                    raise InstructionError('共同材质要求的来源不一致，本次尚未修改。')
                item['source_quote']=expanded[quote]
            elif 'source_quote' in row and row['source_quote']!=quote:
                raise InstructionError('修改要求的来源不一致，本次尚未修改。')
            for key in ('label','target_label'):
                if isinstance(row.get(key),str) and row[key].strip(): item[key]=_text(row[key],120)
            if name=='ready': item['status']='already_satisfied' if row.get('status')=='already_satisfied' else 'ready'
            else:
                status=row.get('status','needs_clarification')
                if status not in ('needs_clarification','unsupported'):
                    raise InstructionError('待确认项的状态无效，本次尚未修改。')
                item['status']=status
                if 'action' in row:
                    if row['action'] not in ('clarify_parameters','locate_part'):
                        raise InstructionError('待确认项的后续操作无效，本次尚未修改。')
                    item['action']=row['action']
            result[name].append(item)
    semantic=proposal.get('semantic_intents',[])
    semantic_constraints=proposal.get('semantic_constraints',[])
    if (not isinstance(semantic,list) or len(semantic)>32 or any(not isinstance(item,dict) for item in semantic)
            or not isinstance(semantic_constraints,list)
            or len(semantic_constraints)>32):
        raise ai_client.ResponseContractError(code='semantic_constraint_scope',path='$.semantic_constraints')
    for quote in semantic_constraints:
        if not isinstance(quote,str) or not quote or (quote not in text and quote not in expanded):
            raise ai_client.ResponseContractError(code='semantic_constraint_scope',path='$.semantic_constraints')
        intent=next((item for item in semantic if isinstance(item,dict) and item.get('quote')==quote
                     and item.get('kind')=='preservation'),None)
        locks=intent.get('protections') if intent else None
        ready=next((item for item in result['ready'] if item['quote']==quote and item['status']=='already_satisfied'
                    and item['id']==(intent or {}).get('id')),None)
        if (not intent or not ready or not isinstance(locks,list) or not locks
                or any(not isinstance(lock,str) or lock not in PROTECTION_LABELS for lock in locks)
                or not set(locks)<=set(getattr(outcome,'protections',[]))):
            raise ai_client.ResponseContractError(code='semantic_constraint_scope',path='$.semantic_constraints')
    expected_constraints={item.get('quote') for item in semantic if item.get('kind')=='preservation'
                          and isinstance(item.get('quote'),str) and item.get('protections')}
    if set(semantic_constraints)!=expected_constraints:
        raise ai_client.ResponseContractError(code='semantic_constraint_scope',path='$.semantic_constraints')
    if semantic:
        result['semantic_intents']=copy.deepcopy(semantic)
    if semantic_constraints:
        result['semantic_constraints']=list(dict.fromkeys(semantic_constraints))
        constraints=list(dict.fromkeys(constraints+semantic_constraints))
    for field,rows in (('execution_text',result['ready']),('remaining_text',result['pending'])):
        value=proposal.get(field)
        allowed={part.strip() for phrase in [row['quote'] for row in rows]+constraints
                 for part in re.split(r'[，,。；;！？!?\n]',phrase) if part.strip()}
        if (not isinstance(value,str) or not value.strip() or len(value)>4000
                or any(row['quote'] not in value for row in rows)
                or any(part.strip() not in allowed for part in re.split(r'[，,。；;！？!?\n]',value) if part.strip())
                or any(quote not in value for quote in semantic_constraints)
                or set(extract_protections(text))-set(extract_protections(value))):
            raise InstructionError('待确认方案的执行范围或保留条件不完整，本次尚未修改。')
        result[field]=value
    return result


def _nothing_ready(proposal):
    clarifications=[row for row in proposal['pending'] if row.get('status')!='unsupported']
    if clarifications and all(row.get('action')=='clarify_parameters' for row in clarifications):
        message='尚未得到可应用的参数，请补充或重新分析要求。'
    else:
        message=('还有未完成要求，请先处理待确认项。' if clarifications
                 else '这些要求当前无法执行，方案尚未修改。')
    error=InstructionError(message,400,
                           error_code='instruction_targets_unresolved')
    error.pending=copy.deepcopy(proposal['pending'])
    error.remaining_text=proposal['remaining_text']
    return error


def _source_output(project, source):
    matches=[item for item in project.get('outputs',[]) if item.get('job_id')==source['output_id']
             and all(item.get(key)==source[key] for key in ('shot_id','quality','version'))]
    if len(matches)!=1: raise InstructionError('请选择当前项目中唯一、真实存在的预览版本。')
    output=matches[0];url=output.get('png_url')
    if not isinstance(url,str) or not url.startswith('/files/'):
        raise InstructionError('预览图片记录无效，请重新生成预览。')
    path=(DATA/url[7:]).resolve();project_root=(DATA/project['id']).resolve()
    if project_root not in path.parents or path.suffix.lower()!='.png' or not path.is_file():
        raise InstructionError('预览图片不存在或不属于当前项目，请重新生成预览。')
    return output,path


def _verify_bound_targets(project, output, evidence, bindings, text, protocol=None):
    """Reuse saved real rays; re-pick only when the complete capability exists."""
    def resolve():
        if protocol=='semantic_v2':
            # The model already interpreted language. This recheck validates
            # only the exact evidence IDs, material slots and stated counts.
            raw=[{key:copy.deepcopy(row[key]) for key in ('phrase','purpose','target_ids','expected_instances')
                  if key in row and (key!='expected_instances' or row[key]!=0)} for row in bindings]
            resolved=target_evidence.resolve_semantic_bindings(raw,evidence,project.get('analysis'),project['version'])
            if resolved!=bindings:
                raise InstructionError('部件定位结果与当前场景证据不一致，当前方案未修改。',409)
            return resolved
        return target_evidence.resolve_bindings(bindings,evidence,text)
    if evidence.get('method')=='scene_part_catalog':
        if not output or not scene_parts.verify(DATA,project,output,evidence):
            raise InstructionError('已定位部件的场景或检查图已变化，当前方案未修改。请重新分析要求。',409)
        resolve()
        return 'scene_geometry_catalog'
    if not evidence or not output or target_evidence.load_evidence(DATA,project,output)!=evidence:
        raise InstructionError('部件定位所依据的预览采样已失效，整条要求尚未应用。请重新生成当前预览。',409)
    resolved=resolve()
    ids={ident for binding in resolved for ident in binding['target_ids']}
    targets=[row for row in evidence['targets'] if row['id'] in ids]
    if not targets:
        raise InstructionError('没有可核验的部件定位结果，整条要求尚未应用。')
    url=output.get('blend_url')
    if not isinstance(url,str) or not url.startswith('/files/'):
        return 'render_samples'
    path=(DATA/url[7:]).resolve()
    if (DATA/project['id']).resolve() not in path.parents or path.suffix.lower()!='.blend' or not path.is_file():
        return 'render_samples'
    if not engine.runtime_status().get('capabilities',{}).get('pick'):
        return 'render_samples'
    source=evidence['source_snapshot']
    expected_snapshot={'job_id':source['output_id'],**{key:source[key] for key in ('shot_id','quality','version')}}
    for start in range(0,len(targets),20):
        batch=targets[start:start+20]
        points=[{'id':row['id'],**row['samples'][0]} for row in batch]
        try:
            picked=engine.pick_output(project['id'],{**source,'points':points})
        except (ValueError,OSError) as exc:
            raise InstructionError('未能复核已定位的部件，整条要求尚未应用。请重新生成当前预览后重试。') from exc
        hits=picked.get('hits') if isinstance(picked,dict) else None
        if (not isinstance(picked,dict) or picked.get('snapshot')!=expected_snapshot or not isinstance(hits,list)
                or len(hits)!=len(batch) or any(not isinstance(hit,dict) for hit in hits)):
            raise InstructionError('部件复核结果与来源预览不一致，整条要求尚未应用。请重新生成当前预览。',409)
        indexed={hit.get('id'):hit for hit in hits if isinstance(hit.get('id'),str)}
        for row in batch:
            hit=indexed.get(row['id'],{})
            if not hit.get('hit') or any(hit.get(key)!=row[key] for key in ('object_name','material_name')):
                phrase=next(binding['phrase'] for binding in resolved if row['id'] in binding['target_ids'])
                raise InstructionError('“'+phrase+'”的实际命中部件与识别结果不一致，整条要求尚未应用。请补充点选该部位。')
    return 'raycast'


def _target_seeds(project, output, source, points):
    """User points are examples; the server re-picks every one before AI use."""
    if output.get('version')!=project['version']:
        raise InstructionError('示例选点来自旧预览，请在当前版本上重新点选。',409)
    try: picked=engine.pick_output(project['id'],{**source,'points':points})
    except (ValueError,OSError) as exc:
        raise InstructionError('示例部位未能重新定位，请重新点选后继续识别。') from exc
    expected={'job_id':source['output_id'],**{key:source[key] for key in ('shot_id','quality','version')}}
    hits=picked.get('hits') if isinstance(picked,dict) else None
    if (not isinstance(picked,dict) or picked.get('snapshot')!=expected or not isinstance(hits,list)
            or len(hits)!=len(points) or any(not isinstance(hit,dict) for hit in hits)):
        raise InstructionError('示例部位与来源预览不一致，请重新点选。',409)
    indexed={hit.get('id'):hit for hit in hits if isinstance(hit.get('id'),str)}
    known=target_evidence._pairs(project.get('analysis')); result=[]
    for point in points:
        hit=indexed.get(point['id'],{});pair=(hit.get('object_name'),hit.get('material_name'))
        if not hit.get('hit') or not all(isinstance(value,str) for value in pair) or pair not in known:
            raise InstructionError('有选点未落在产品的有效材质表面，请重新点选。')
        value={'object_name':pair[0],'material_name':pair[1]}
        if value not in result:result.append(value)
    return result


def _wants_reference(text):
    """A negative reference instruction never opens the saved reference image."""
    for clause in re.split(r'[，,。；;！？!?\n]',text):
        for match in re.finditer(r'参考图(?:片)?|参考照片|参照图|reference\s+(?:image|photo|picture)',clause,re.I):
            prefix=clause[:match.start()].strip()
            negative=re.search(r'(?:不要|不用|无需|别|不必|不需|不再|不|忽略|排除)(?:再)?(?:使用|采用|读取|上传|加载|参考|按照|按|看|用|比较|对照)?(?:任何|这张|那张|该|我的|提供的|已上传的)?\s*$',prefix)
            negative=negative or re.search(r"(?:do not|don't|never|without|ignore|not|no need to)(?:\s+(?:use|read|load|follow|refer to))?\s*(?:the|a|any|my)?\s*$",prefix,re.I)
            suffix=clause[match.end():].strip()
            negative=negative or re.match(r'^(?:先)?(?:不要|不用|无需|别|不必|不需|不再)(?:再)?(?:用|使用|采用|读取|加载|参考)',suffix)
            negative=negative or re.match(r'^(?:is\s+not\s+needed|not\s+needed|(?:please\s+)?ignore)',suffix,re.I)
            if not negative: return True
    return False


def _reference(project, text):
    if not _wants_reference(text): return None
    filename=project.get('metadata',{}).get('reference')
    if not isinstance(filename,str) or not filename:
        raise InstructionError('请先上传参考图，再提出对照参考图的调整。')
    path=Path(filename).resolve();folder=(DATA/project['id']/'references').resolve()
    if folder not in path.parents or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not path.is_file():
        raise InstructionError('请先上传当前项目可读取的参考图，再进行对照调整。')
    try: engine.verify_image(path)
    except (OSError,ValueError,RuntimeError) as exc:
        raise InstructionError('请先上传有效的参考图，现有文件无法读取。') from exc
    return path


def _available_reference(project):
    """Resolve an optional project attachment without reading or sending it.

    Online v2 lets the model request this context after understanding the whole
    instruction. Merely attaching an image does not force it into every call.
    """
    filename=project.get('metadata',{}).get('reference')
    if not isinstance(filename,str) or not filename:return None
    path=Path(filename).resolve();folder=(DATA/project['id']/'references').resolve()
    if folder not in path.parents or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not path.is_file():
        return None
    return path


def _recent_value(value, depth=0):
    """History is context, not a way to transmit local resource locations."""
    omitted={'textures','resource','manifest_path','model_path','reference','reference_path','source','license',
             'png_url','jpg_url','blend_url','result_url','url','license_url','download_url'}
    if depth>3: return '（深层内容已省略）'
    if isinstance(value,dict):
        return {str(key)[:80]:_recent_value(item,depth+1) for key,item in list(value.items())[:16] if key not in omitted}
    if isinstance(value,list): return [_recent_value(item,depth+1) for item in value[:6]]
    if isinstance(value,str):
        if re.match(r'^(?:[A-Za-z]:[\\/]|/|\\\\|file:|https?://)',value): return '（资源路径已省略）'
        return value[:200]
    if value is None or type(value) in (bool,int): return value
    if isinstance(value,float): return value if math.isfinite(value) else None
    return None


def _recent_changes(changes):
    allowed=set(ai_client.SCALARS)|{'shots','material_overrides','object_material_overrides','pose','shot_adjustments'}
    result=[]
    for change in changes:
        if not isinstance(change,dict): continue
        path=change.get('path')
        if not isinstance(path,str) or path.split('.')[0] not in allowed: continue
        if any(field in path.split('.') for field in ('textures','resource','manifest_path','reference','model_path')): continue
        entry={'path':path[:200],'before':_recent_value(change.get('before')),'after':_recent_value(change.get('after'))}
        for side in ('before','after'):
            if len(json.dumps(entry[side],ensure_ascii=False))>1200: entry[side]='（较大结构已省略，可结合当前方案检查）'
        result.append(entry)
        if len(result)==12: break
    return result


def _recent(project):
    items=[]
    # Only the current plan and its remaining ancestry are active intent.
    # Receipts are an audit log and may describe edits the user already undid.
    for entry in project.get('history',[])+[{'plan':project.get('plan',{})}]:
        value=entry.get('plan',{}).get('last_instruction',{})
        changes=value.get('parameter_diff',value.get('changes',[]))
        if value.get('text') and isinstance(changes,list) and changes and all(isinstance(change,dict) for change in changes):
            bounded=_recent_changes(changes)
            if bounded: items.append({'id':_text(value.get('id'),100),'text':_text(value['text'],1000),'changes':bounded})
    unique=[];seen=set()
    for item in reversed(items):
        signature=item['id'] or _digest(item)
        if signature not in seen: unique.append(item);seen.add(signature)
        if len(unique)==5: break
    return list(reversed(unique))


def _current_light_directions(project, output):
    """Expose only bounded measurements attached to this current saved image."""
    if not isinstance(output,dict) or output.get('version')!=project.get('version'):return []
    rows=(output.get('metrics') or {}).get('studio_light_directions',[])
    if not isinstance(rows,list) or len(rows)>3:return []
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('role') not in ('key','fill','rim') or row['role'] in seen:return []
        if any(type(row.get(key)) not in (int,float) or not math.isfinite(row[key]) for key in ('azimuth','elevation')):return []
        if not -180.001<=row['azimuth']<=180.001 or not -90.001<=row['elevation']<=90.001:return []
        if any(not isinstance(row.get(key),str) or not row[key] or len(row[key])>256 for key in ('object_name','camera')):return []
        item={key:copy.deepcopy(row[key]) for key in ('role','object_name','camera','azimuth','elevation')}
        for key in ('position','target'):
            value=row.get(key)
            if (not isinstance(value,list) or len(value)!=3
                    or any(type(n) not in (int,float) or not math.isfinite(n) for n in value)):return []
            item[key]=list(value)
        for key in ('energy','size','size_y'):
            value=row.get(key)
            if type(value) in (int,float) and math.isfinite(value) and 0<=value<=1e12:
                item[key]=value
        if row.get('type') in ('POINT','SUN','SPOT','AREA'):item['type']=row['type']
        if row.get('shape') in ('SQUARE','RECTANGLE','DISK','ELLIPSE'):item['shape']=row['shape']
        seen.add(row['role']);result.append(item)
    return result


def _current_material_values(project, evidence):
    """Expose actual constant shader values only from a current, checked catalog."""
    public=target_evidence.public_evidence(evidence,project.get('analysis'),project.get('version'))
    if public.get('method')!='scene_part_catalog':return []
    result=[]
    for target in public['targets']:
        values=target.get('surface_values',{});linked=target.get('linked_surface_fields',[])
        if not values and not linked:continue
        result.append({'object_name':target['object_name'],'material_name':target['material_name'],
                       'values':copy.deepcopy(values),'linked_fields':list(linked),
                       'basis':'saved_scene_principled'})
    return result


def _context(project, shot_id, output, notes):
    metadata=project.get('metadata') or {};review=(output or {}).get('review') or {}
    realism=review.get('realism') or {}
    diagnosis={'summary':_text(realism.get('summary'),1000),'source':_text(review.get('source'),40),
               'causes':[{key:_text(cause.get(key),400) for key in ('aspect','evidence','suggestion')}
                         for cause in realism.get('causes',[])[:5] if isinstance(cause,dict)]} if review else {}
    context={'active_shot_id':shot_id,'project_name':_text(project.get('name'),100),
            'current_version':project['version'],
            'product_category':_text(metadata.get('product_category'),200),'material_note':_text(metadata.get('material_note'),2000),
            'recent_instructions':_recent(project),'diagnosis':diagnosis,'evidence_notes':list(notes),'protections':[],
            'current_light_directions':_current_light_directions(project,output)}
    if project.get('plan',{}).get('scene_checkpoints',{}).get(shot_id):
        context['execution_capabilities']={
            'material_fields':['roughness','metallic','transmission','color'],
            'material_preset_replacement':False,
            'lighting_power_mode':'relative_to_saved_scene',
        }
    return context


def _remaining_protections(project, text, shot_id):
    """Carry server-recorded locks only into the exact outstanding retry."""
    receipt=project.get('instruction_result') or {}
    if (receipt.get('protocol')!='semantic_v2' or receipt.get('partial') is not True
            or receipt.get('status') not in ('partially_applied','applied','no_change')
            or receipt.get('applied_version')!=project.get('version')
            or receipt.get('shot_id')!=shot_id
            or not isinstance(receipt.get('remaining_text'),str)
            or receipt['remaining_text'].strip()!=text.strip()):
        return []
    locks=receipt.get('protections')
    if not isinstance(locks,list) or any(lock not in PROTECTION_LABELS for lock in locks):
        return []
    return list(dict.fromkeys(locks))


def _scoped_plan(base, effective, candidate, shot_id):
    """Map effective shot lighting back without changing unrelated shots."""
    if not shot_id: return candidate
    before={shot['id']:shot for shot in base['shots']};after={shot['id']:shot for shot in candidate['shots']}
    if set(before)!=set(after): raise InstructionError('智能调整不能新增或删除已有镜头。')
    for ident,shot in before.items():
        if ident!=shot_id and after[ident]!=shot:
            raise InstructionError('本次调整只能修改当前镜头的相机，其他镜头已保留。')
    result=copy.deepcopy(candidate);result['shots']=[after[shot['id']] for shot in base['shots']]
    adjustments=copy.deepcopy(base.get('shot_adjustments',{}));item=copy.deepcopy(adjustments.get(shot_id,{}))
    if candidate['exposure']!=effective['exposure']: item['exposure_delta']=candidate['exposure']-base['exposure']
    if candidate['light_softness']!=effective['light_softness']: item['softness_multiplier']=candidate['light_softness']/base['light_softness']
    before_lights=effective.get('lighting',{});after_lights=candidate.get('lighting',{})
    if set(before_lights)-set(after_lights): raise InstructionError('智能调整不能移除灯光参数。')
    lighting=copy.deepcopy(item.get('lighting',{}))
    for name,value in after_lights.items():
        if value!=before_lights.get(name,planner.LIGHTING_DEFAULTS.get(name)): lighting[name]=value
    if lighting: item['lighting']=lighting
    if item: adjustments[shot_id]=item
    if adjustments or 'shot_adjustments' in base: result['shot_adjustments']=adjustments
    else: result.pop('shot_adjustments',None)
    result['exposure']=base['exposure'];result['light_softness']=base['light_softness']
    if 'lighting' in base: result['lighting']=copy.deepcopy(base['lighting'])
    else: result.pop('lighting',None)
    return planner.validate(result)


def _changes(before, after):
    changes=ai_client.parameter_diff(before,after)
    def walk(left,right,path):
        if isinstance(left,dict) and right is None: right={}
        elif left is None and isinstance(right,dict): left={}
        if isinstance(left,dict) and isinstance(right,dict):
            for key in sorted(set(left)|set(right)): walk(left.get(key),right.get(key),path+'.'+key)
        elif left!=right: changes.append({'path':path,'before':copy.deepcopy(left),'after':copy.deepcopy(right)})
    walk(before.get('shot_adjustments',{}),after.get('shot_adjustments',{}),'shot_adjustments')
    return changes


def _receipt_changes(changes,before,after,measured_directions=None,measured_materials=None):
    """Keep the stored diff and attach measured before-values for display only."""
    rows=copy.deepcopy(changes);cache={}
    measured={};ambiguous=set()
    for item in measured_materials or []:
        if not isinstance(item,dict) or item.get('basis')!='saved_scene_principled':continue
        pair=(item.get('object_name'),item.get('material_name'))
        if any(not isinstance(value,str) or not value for value in pair):continue
        if pair in measured:ambiguous.add(pair);continue
        values=item.get('values');linked=item.get('linked_fields',[])
        if not isinstance(values,dict) or not isinstance(linked,list):continue
        measured[pair]={field:number for field,number in values.items()
                        if field in ('roughness','metallic','transmission') and field not in linked
                        and type(number) in (int,float) and math.isfinite(number) and 0<=number<=1}
    for pair in ambiguous:measured.pop(pair,None)
    shots={shot['id'] for shot in before.get('shots',[])} & {shot['id'] for shot in after.get('shots',[])}
    for row in rows:
        path=row.get('path','')
        if path=='object_material_overrides' and isinstance(row.get('after'),list):
            previous=row.get('before') if isinstance(row.get('before'),list) else []
            effective=copy.deepcopy(previous);changed=False
            for item in row['after']:
                if not isinstance(item,dict):continue
                pair=(item.get('object_name'),item.get('material_name'))
                values=measured.get(pair,{})
                prior=next((value for value in previous if isinstance(value,dict)
                            and (value.get('object_name'),value.get('material_name'))==pair),{})
                fields=[field for field in values if field in item and item[field]!=prior.get(field)]
                if not fields:continue
                target=next((value for value in effective if isinstance(value,dict)
                             and (value.get('object_name'),value.get('material_name'))==pair),None)
                if target is None:
                    target={'object_name':pair[0],'material_name':pair[1]};effective.append(target)
                for field in fields:target[field]=values[field]
                changed=True
            if changed:row['effective_before']=effective
            continue
        shot_id=next((ident for ident in shots if path.startswith('shot_adjustments.'+ident+'.')),None)
        if not shot_id:continue
        field=path[len('shot_adjustments.'+shot_id+'.'):]
        if field not in ('exposure_delta','softness_multiplier') and not field.startswith('lighting.'):continue
        if shot_id not in cache:cache[shot_id]=(engine.effective_plan(before,shot_id),engine.effective_plan(after,shot_id))
        for side,plan in zip(('before','after'),cache[shot_id]):
            if field=='exposure_delta':value=plan['exposure']
            elif field=='softness_multiplier':value=plan['light_softness']
            else:
                name=field[len('lighting.'):]
                if name not in planner.LIGHTING_LIMITS:continue
                value=plan.get('lighting',{}).get(name,planner.LIGHTING_DEFAULTS.get(name))
                if value is None and side=='before' and name in planner.LIGHT_DIRECTION_LIMITS:
                    role,axis=name.split('_',1)
                    measured=next((item for item in (measured_directions or []) if item.get('role')==role),None)
                    if measured:value=measured[axis]
            row['effective_'+side]=value
    return rows


def apply_instruction(project_id, body, request_key=None, validate_plan=None):
    text,expected,shot_id,source,request_digest,key_hash=_parse(body,request_key)
    protocol=body.get('protocol',DEFAULT_INSTRUCTION_PROTOCOL)
    semantic_mode=protocol=='semantic_v2'
    token=uuid.uuid4().hex
    with LOCK:
        project=get('project',project_id)
        receipt=_receipt(project,key_hash,request_digest)
        if receipt: return _response(project_id,receipt,True)
        if expected is not None and expected!=project['version']:
            raise InstructionError('方案已更新，请载入最新方案后重新提交调整。',409)
        if not project.get('analysis'): raise InstructionError('请先完成模型分析，再应用画面调整。')
        if shot_id and shot_id not in {shot['id'] for shot in project['plan']['shots']}:
            raise InstructionError('当前镜头不属于这个项目。')
        _idle(project_id)
        if project_id in _INFLIGHT:
            pending=_INFLIGHT[project_id]
            if key_hash and pending['key_hash']==key_hash and pending['digest']!=request_digest:
                raise InstructionError('同一个请求标识不能用于不同的调整内容。',409)
            if key_hash and pending['key_hash']==key_hash and pending['digest']==request_digest:
                raise InstructionError('这次调整仍在处理中，请稍后用同一请求核对结果。',409,
                                       error_code='instruction_in_progress',retryable=True)
            raise InstructionError('当前项目的调整仍在处理中，请稍后用同一请求重试。',409)
        _INFLIGHT[project_id]={'token':token,'key_hash':key_hash,'digest':request_digest}
    try:
        notes=[];image_path=None;output=None;source_path=None
        if source:
            output,source_path=_source_output(project,source)
            if source['version']==project['version']:
                try: engine.verify_image(source_path,'PNG')
                except (OSError,ValueError,RuntimeError) as exc:
                    raise InstructionError('当前预览无法读取，请先重新生成预览。') from exc
                image_path=source_path
            else:
                notes.append('所选图片属于不同方案版本，本次未把旧图作为当前画面；仅依据当前参数和文字要求调整。')
        else: notes.append('未提供当前镜头预览，本次依据参数和文字要求调整。')
        available_reference=_available_reference(project) if semantic_mode else None
        if semantic_mode:
            configured=(ai_config.get_vision_config() if image_path or available_reference else ai_config.get_config())
            if (not image_path and available_reference
                    and not all(configured.get(key) for key in ('enabled','api_key','model'))):
                # A disabled independent vision route must not force a text
                # request through legacy AI parsing just because an unused
                # attachment exists. Use the configured text route consistently
                # and do not offer reference bytes that this route cannot read.
                available_reference=None;configured=ai_config.get_config()
            if not all(configured.get(key) for key in ('enabled','api_key','model')):
                # Offline quick controls remain explicitly reported as rules.
                # Their existing reference handling must not mistake a merely
                # attached file for a requested comparison.
                semantic_mode=False;protocol='legacy_v1'
        reference_path=available_reference if semantic_mode else _reference(project,text)
        target_samples=target_evidence.load_evidence(DATA,project,output) if image_path else {}
        seed_pairs=[]
        if body.get('target_points'):
            if not image_path:
                raise InstructionError('示例选点需要当前版本的预览，请重新点选。',409)
            seed_pairs=_target_seeds(project,output,source,body['target_points'])
        inspection=None
        needs_inspection=bool(image_path and (semantic_mode or body.get('target_points') or scene_parts.wants_inspection(text,project.get('analysis'))))
        if needs_inspection and engine.runtime_status().get('capabilities',{}).get('inspect_parts'):
            try:
                inspection=scene_parts.prepare(DATA,project,output,seeds=seed_pairs)
                target_samples=inspection['evidence']
            except scene_parts.InspectionUnavailable as exc:
                notes.append(str(exc))
        elif needs_inspection:
            notes.append('完整部件检查尚未就绪，本次只能使用当前预览的可见采样；请确认已更新部件检查组件。')
        context=_context(project,shot_id,output if image_path else None,notes)
        if semantic_mode:
            context['protections']=_remaining_protections(project,text,shot_id)
        context['instruction_protocol']=protocol
        lighting_inspection=None
        # Read actual saved lights before asking the model to choose direction.
        # This adds no model call and never modifies or re-renders the source.
        light_goal=semantic_mode
        if not semantic_mode:
            for clause in ai_client.instruction_clauses(text):
                domains=ai_client._requirement_domains(clause)
                if domains and 'lighting' not in domains:continue
                if ai_client._explicit_photography_action(clause):
                    literal=ai_client._rule_photography(project['plan'],clause,shot_id,extract_protections(text))
                    if literal and not set(literal.get('lighting',{})) & set(planner.LIGHT_DIRECTION_LIMITS):
                        # Explicit legacy requests keep their existing shortcut.
                        continue
                light_goal=True;break
        light_capabilities=(engine.runtime_status().get('capabilities',{})
                            if image_path and light_goal and output.get('blend_url') else {})
        if (light_capabilities.get('light_direction') or
                (project['plan'].get('scene_checkpoints',{}).get(shot_id) and light_capabilities.get('hero_optimize'))):
            try:
                lighting_inspection=engine.inspect_lighting(project,output)
            except engine.LightingInspectionStale as exc:
                raise _stale_proposal() from exc
            except (RuntimeError,ValueError,OSError) as exc:
                note='当前灯位检查未完成：'+_text(str(exc),300)
                notes.append(note);context['evidence_notes'].append(note)
            else:
                measured={**output,'metrics':{'studio_light_directions':lighting_inspection.get('directions',[])}}
                context['current_light_directions']=_current_light_directions(project,measured)
                context['light_direction_support']={
                    'inspected':True,
                    'available_roles':[role for role in lighting_inspection.get('available_roles',[]) if role in ('key','fill','rim')],
                    'unavailable_roles':{role:_text(reason,300) for role,reason in lighting_inspection.get('unavailable_roles',{}).items()
                                         if role in ('key','fill','rim')},
                }
                context['evidence_notes'].append('已只读检查当前保存场景中的真实灯位；可用光源按实际角色标识确认，未改动场景。')
        if inspection:
            context['_target_image_paths']=inspection['images']
            context['evidence_notes'].append('已独立检查当前预览的三维部件，包括主图中被遮挡的表面；部件图为中性几何示意，不代表当前材质或最终渲染。')
            if target_samples.get('truncated') or target_samples.get('unresolved_count'):
                context['evidence_notes'].append('部件检查存在未收录或无法确认的材质槽。只能使用图中已核验的目标，不能把缺少的目标当作已完成。')
        if target_samples:
            context['target_evidence']=target_evidence.public_evidence(target_samples,project.get('analysis'),project['version'])
        if inspection:
            context['current_material_values']=_current_material_values(project,target_samples)
            if context['current_material_values']:
                context['evidence_notes'].append('材质参数来自当前保存场景的实际表面；连接贴图或节点的参数未当作常数。调整光泽时以这些实际值为基准，保留未要求修改的颜色与材质网络。')
        if seed_pairs:
            catalog={(row['object_name'],row['material_name']) for row in target_samples.get('targets',[])}
            if any((seed['object_name'],seed['material_name']) not in catalog for seed in seed_pairs):
                raise InstructionError('选中的部位尚未进入可核验的部件图，可能超出检查范围或材质槽无法确认。当前方案未修改，请下载项目归档供排查。')
            context['target_seeds']=seed_pairs
        base=copy.deepcopy(project['plan']);effective=engine.effective_plan(base,shot_id) if shot_id else copy.deepcopy(base)
        guard=_proposal_guard(project,source,None if semantic_mode else reference_path,inspection=bool(inspection),seed_pairs=seed_pairs,
                              lighting_inspection=bool(lighting_inspection))
        if lighting_inspection:
            inspected_source=lighting_inspection['source']
            expected_scene={'url':inspected_source.get('blend_url'),'size':inspected_source.get('size_bytes'),
                            'modified_ns':inspected_source.get('mtime_ns')}
            if (guard.get('scene')!=expected_scene
                    or guard.get('light_direction_scene_sha256')!=inspected_source.get('sha256')):
                raise _stale_proposal()
        if source and not inspection and guard.get('evidence')!=_digest(target_samples):
            raise _stale_proposal()
        smart=semantic_mode or shot_id is not None or source is not None or expected is not None or reference_path is not None
        outcome=None;proposal=None
        try:
            if smart:
                candidate=planner.instruct(effective,text,project.get('analysis'),image_path=image_path,reference_path=reference_path,context=context)
            else: candidate=planner.instruct(effective,text,project.get('analysis'))
        except ValueError as exc:
            if getattr(exc,'status',None)=='partial_available':
                outcome=exc;proposal=_validated_proposal(exc,text)
                candidate=proposal['candidate_plan']
            elif getattr(exc,'status',None)=='no_change':
                outcome=exc;candidate=copy.deepcopy(effective)
            else:
                _record_response_error(project,text,source,exc,target_samples)
                if getattr(exc,'pending',None) and notes:
                    exc.notes=list(dict.fromkeys(list(getattr(exc,'notes',[]))+notes))
                    # The visible recovery card receives the actual inspection
                    # blocker, not only an unhelpful generic missing-parts line.
                    for row in exc.pending:
                        if row.get('action')=='locate_part':
                            row['reason']='；'.join([row.get('reason','')]+notes)
                raise
        candidate=_scoped_plan(base,effective,planner.validate(candidate),shot_id)
        if validate_plan:
            original_candidate=copy.deepcopy(candidate)
            candidate=validate_plan(project_id,candidate)
            if proposal and _changes(original_candidate,candidate):
                raise InstructionError('可执行修改在校验期间发生变化，本次调整未应用。',409,
                                       error_code='instruction_proposal_stale')
        changes=_changes(base,candidate)
        if proposal and not changes and any(row['status']!='already_satisfied' for row in proposal['ready']):
            raise _nothing_ready(proposal)
        review_only=bool(proposal and changes and body.get('partial_mode')=='review')
        last=candidate.get('last_instruction') or {}
        if (outcome and not proposal) or last.get('text')!=text: last={}
        decision=last if not outcome or proposal else vars(outcome)
        if semantic_mode:
            # A reference attachment is loaded only after a semantic model
            # request. Bind the exact bytes it saw before committing a plan.
            if decision.get('used_reference'):
                expected_reference=decision.get('reference_evidence')
                if not reference_path or not isinstance(expected_reference,dict):
                    raise _stale_proposal()
                try:
                    stat=reference_path.stat();digest=_file_digest(reference_path)
                except OSError as exc:
                    raise _stale_proposal() from exc
                actual_reference={'sha256':digest,'size_bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}
                if actual_reference!=expected_reference:
                    raise _stale_proposal()
                guard['reference']={'path':str(reference_path),'digest':digest}
            else:
                reference_path=None
        bindings=last.get('target_bindings',[]) if changes else []
        execution_text=proposal['execution_text'] if proposal else text
        verification=_verify_bound_targets(project,output,target_samples,bindings,execution_text,
                                           protocol=last.get('protocol')) if bindings else None
        notes.extend(item for item in last.get('notes',[]) if isinstance(item,str))
        if outcome: notes.extend(item for item in getattr(outcome,'notes',[]) if isinstance(item,str))
        global_labels={'margin':'留白','aspect':'输出比例','style':'整体摄影风格','rotation':'模型朝向','texture_scale':'纹理尺度','texture_rotation':'纹理方向'}
        for key,label in global_labels.items():
            if shot_id and base.get(key)!=candidate.get(key): notes.append(label+'是项目级设置，会影响其他镜头。')
        preview_ids=[shot_id] if shot_id else [project['plan']['shots'][0]['id']]
        if shot_id=='hero' and base.get('style')!=candidate.get('style') and candidate.get('style')!='white':
            if any(shot['id']=='campaign' for shot in candidate['shots']):
                preview_ids=['campaign'];notes.append('白底主图保持白底，本次使用棚拍宣传镜头预览新的背景风格。')
        status='needs_confirmation' if review_only else ('partially_applied' if proposal and changes else 'applied' if changes else 'no_change')
        summary=_text(last.get('summary'),1200) if changes else (_text(str(outcome),1200) if outcome else '当前参数已符合这次要求，没有需要保存的变化。')
        if not summary: summary='调整已保存，将按实际参数变化生成预览。'
        evidence=last if outcome is None or proposal else {key:getattr(outcome,key,None) for key in ('source','used_image','used_reference','provider','model')}
        receipt={'id':uuid.uuid4().hex,'status':status,'text':text,'source':evidence.get('source') or candidate.get('source','rules'),
                 'changes':[] if review_only else _receipt_changes(changes,base,candidate,context.get('current_light_directions'),context.get('current_material_values')),'summary':summary,'notes':list(dict.fromkeys(notes)),
                 'shot_id':shot_id,'preview_shot_ids':preview_ids if changes and not review_only else [],
                 'used_image':bool(image_path and evidence.get('used_image',False)),
                 'used_reference':bool(reference_path and evidence.get('used_reference',False)),
                 'source_snapshot':copy.deepcopy(source),'base_version':project['version'],
                 'applied_version':project['version']+(1 if changes and not review_only else 0),'replayed':False,'created_at':time.time(),
                 'protections':copy.deepcopy(last.get('protections',getattr(outcome,'protections',[]))),
                 'protected_fields':copy.deepcopy(last.get('protected_fields',getattr(outcome,'protected_fields',[]))),
                 'protected_material_fields':copy.deepcopy(last.get('protected_material_fields',getattr(outcome,'protected_material_fields',[])))}
        for key in ('provider','model'):
            if evidence.get(key): receipt[key]=_text(evidence[key],200)
        semantic_source=proposal or last
        if decision.get('protocol')=='semantic_v2':receipt['protocol']='semantic_v2'
        for key in ('semantic_intents','semantic_constraints','source_spans'):
            value=semantic_source.get(key,getattr(outcome,key,None))
            if isinstance(value,list) and value:receipt[key]=copy.deepcopy(value)
        if bindings:
            receipt['target_bindings']=copy.deepcopy(bindings)
            receipt['target_verification']=verification
        if inspection:
            receipt['part_inspection']={'method':'scene_part_catalog','targets':len(target_samples['targets']),
                                        'unresolved':target_samples.get('unresolved_count',0),
                                        'truncated':bool(target_samples.get('truncated'))}
        saved_proposal=None
        if proposal:
            ready_count=sum(row.get('status')!='already_satisfied' for row in proposal['ready'])
            receipt.update(ready=copy.deepcopy(proposal['ready']),pending=copy.deepcopy(proposal['pending']),
                           remaining_text=proposal['remaining_text'])
            if review_only:
                proposal_id=uuid.uuid4().hex
                receipt.update(proposal_id=proposal_id,proposed_changes=copy.deepcopy(changes),
                               summary=f'有 {ready_count} 项可先应用，另有 {len(proposal["pending"])} 项尚未完成。')
                saved_proposal={'id':proposal_id,'text':text,'protocol':protocol,'candidate_plan':copy.deepcopy(candidate),
                                'execution_text':execution_text,'base_version':project['version'],
                                'source_snapshot':copy.deepcopy(source),'guard':guard,
                                'receipt':copy.deepcopy(receipt),'preview_shot_ids':preview_ids,
                                'created_at':time.time()}
            else:
                receipt['partial']=True
                receipt['summary']=(f'已完成 {ready_count} 项调整' if changes else f'已有 {len(proposal["ready"])} 项符合要求')+f'；还有 {len(proposal["pending"])} 项待处理。'
        with LOCK:
            current=get('project',project_id)
            existing=_receipt(current,key_hash,request_digest)
            if existing: return _response(project_id,existing,True)
            if current['version']!=project['version']:
                raise InstructionError('方案在理解要求期间已被修改，本次调整未应用，请检查最新方案。',409)
            _idle(project_id)
            commit_guard={'base_version':project['version'],'text':text,'protocol':protocol,
                          'source_snapshot':source,'guard':guard}
            if proposal:_check_proposal_guard(current,commit_guard)
            if source:
                latest,latest_path=_source_output(current,source)
                if latest.get('png_url')!=output.get('png_url') or latest_path!=source_path:
                    raise InstructionError('来源图片在处理期间已变化，本次调整未应用。',409)
                valid_samples=(scene_parts.verify(DATA,current,latest,target_samples) if inspection
                               else target_evidence.load_evidence(DATA,current,latest)==target_samples)
                if bindings and not valid_samples:
                    raise InstructionError('部件采样在处理期间已变化，整条要求尚未应用。请重新生成当前预览。',409)
            # Keep precise target/source errors while also guarding image-led
            # full lighting decisions, which may have no material bindings.
            if not proposal:_check_proposal_guard(current,commit_guard)
            if saved_proposal:
                active=[entry for entry in current.get(PROPOSALS_KEY,[]) if entry.get('base_version')==current['version']]
                current[PROPOSALS_KEY]=(active+[saved_proposal])[-MAX_PENDING_PROPOSALS:]
            elif changes:
                instruction=candidate.setdefault('last_instruction',{})
                instruction.update(id=receipt['id'],applied_version=receipt['applied_version'],
                                   text=execution_text,changes=copy.deepcopy(changes),parameter_diff=copy.deepcopy(changes),
                                   summary=receipt['summary'])
                if proposal:
                    instruction.update(original_text=text,partial=True,ready=copy.deepcopy(receipt['ready']),
                                       pending=copy.deepcopy(receipt['pending']),remaining_text=receipt['remaining_text'])
                current.setdefault('history',[]).append({'version':current['version'],'plan':copy.deepcopy(current['plan']),
                    'at':time.time(),'reason':execution_text,'instruction_id':receipt['id']})
                current['plan']=candidate;current['version']+=1
                current.pop(PROPOSALS_KEY,None)
            current.setdefault('instruction_receipts',[]).append({'key_hash':key_hash,'request_digest':request_digest,'receipt':copy.deepcopy(receipt)})
            current['instruction_result']=copy.deepcopy(receipt)
            put('project',current)
        return _response(project_id,receipt)
    except ai_client.ResponseContractError as exc:
        if not getattr(exc,'diagnostic_id',None):_record_response_error(project,text,source,exc)
        raise
    finally:
        with LOCK:
            if _INFLIGHT.get(project_id,{}).get('token')==token: _INFLIGHT.pop(project_id,None)


def confirm_instruction(project_id, body, request_key=None, validate_plan=None):
    """Apply exactly the reviewed subset once, with no second AI request.

    The proposal ID is not a patch or an instruction. Its candidate, original
    evidence and remaining requirements are loaded from this project only.
    """
    proposal_id,expected,request_digest,key_hash=_parse_confirmation(body,request_key)
    token=uuid.uuid4().hex
    with LOCK:
        project=get('project',project_id)
        receipt=_receipt(project,key_hash,request_digest)
        if receipt: return _response(project_id,receipt,True)
        confirmed=next((entry['receipt'] for entry in project.get('instruction_receipts',[])
                        if entry.get('receipt',{}).get('proposal_id')==proposal_id
                        and entry['receipt'].get('status')=='applied'),None)
        if confirmed:
            if expected!=confirmed['base_version']: raise _stale_proposal()
            # Even a second click with a new key refers to the first operation.
            # Persist that key's acknowledgement so it cannot be repurposed.
            project.setdefault('instruction_receipts',[]).append({'key_hash':key_hash,
                'request_digest':request_digest,'receipt':copy.deepcopy(confirmed)})
            put('project',project)
            return _response(project_id,confirmed,True)
        proposal=next((entry for entry in project.get(PROPOSALS_KEY,[]) if entry.get('id')==proposal_id),None)
        if not proposal or expected!=proposal['base_version'] or expected!=project['version']:
            raise _stale_proposal()
        _idle(project_id)
        if project_id in _INFLIGHT:
            pending=_INFLIGHT[project_id]
            if key_hash==pending['key_hash'] and request_digest==pending['digest']:
                raise InstructionError('这次确认仍在处理中，请稍后用同一请求核对结果。',409,
                                       error_code='instruction_in_progress',retryable=True)
            raise InstructionError('当前项目的调整仍在处理中，请等待完成后再确认。',409)
        _INFLIGHT[project_id]={'token':token,'key_hash':key_hash,'digest':request_digest}
    try:
        _check_proposal_guard(project,proposal)
        candidate=planner.validate(copy.deepcopy(proposal['candidate_plan']))
        if validate_plan: candidate=validate_plan(project_id,candidate)
        changes=_changes(project['plan'],candidate)
        if not changes or changes!=proposal['receipt']['proposed_changes']:
            raise _stale_proposal()
        receipt=copy.deepcopy(proposal['receipt'])
        receipt.pop('proposed_changes',None)
        count=sum(row.get('status')!='already_satisfied' for row in receipt['ready'])
        receipt.update(id=uuid.uuid4().hex,status='applied',changes=copy.deepcopy(changes),partial=True,
                       summary=f'已应用 {count} 项；{len(receipt["pending"])} 项仍未完成。',
                       preview_shot_ids=copy.deepcopy(proposal['preview_shot_ids']),
                       applied_version=expected+1,replayed=False,created_at=time.time())
        with LOCK:
            current=get('project',project_id)
            existing=_receipt(current,key_hash,request_digest)
            if existing: return _response(project_id,existing,True)
            _idle(project_id)
            # Reusing verified rays is safe only while the exact image, result,
            # model analysis, scene snapshot and base plan are unchanged.
            _check_proposal_guard(current,proposal)
            if not any(entry.get('id')==proposal_id for entry in current.get(PROPOSALS_KEY,[])):
                raise _stale_proposal()
            candidate.setdefault('last_instruction',{}).update(
                id=receipt['id'],applied_version=receipt['applied_version'],
                text=proposal['execution_text'],original_text=proposal['text'],partial=True,
                proposal_id=proposal_id,pending=copy.deepcopy(receipt['pending']),
                remaining_text=receipt['remaining_text'],summary=receipt['summary'],
                changes=copy.deepcopy(changes),parameter_diff=copy.deepcopy(changes))
            current.setdefault('history',[]).append({'version':current['version'],
                'plan':copy.deepcopy(current['plan']),'at':time.time(),
                'reason':'先应用可完成项：'+proposal['execution_text'],'instruction_id':receipt['id']})
            current['plan']=candidate;current['version']+=1
            current.pop(PROPOSALS_KEY,None)
            current.setdefault('instruction_receipts',[]).append({'key_hash':key_hash,
                'request_digest':request_digest,'receipt':copy.deepcopy(receipt)})
            current['instruction_result']=copy.deepcopy(receipt)
            put('project',current)
        return _response(project_id,receipt)
    finally:
        with LOCK:
            if _INFLIGHT.get(project_id,{}).get('token')==token: _INFLIGHT.pop(project_id,None)

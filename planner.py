"""Validated, replayable camera intent. No executable code is accepted from AI."""
import copy
import math
import re
from camera_controls import validate_shot
from lighting_controls import LIGHTING_DEFAULTS, LIGHT_DIRECTION_LIMITS, LIGHTING_LIMITS
from intelligence_rules import (
    InstructionOutcome, extract_protections, check_unsupported_requests,
    preservation_only, rule_patch, enforce_protections, describe_changes,
    instruction_protected_fields, instruction_protected_material_fields, PROTECTION_LABELS,
)

PRESETS = ['matte_plastic','glossy_plastic','painted_metal','brushed_metal','polished_metal','glass','rubber','fabric','leather','wood','ceramic']
TEXTURE_CHANNELS = {'base_color','roughness','metallic','normal','transmission'}

def default_plan():
    return dict(style='soft',aspect='1:1',margin=.12,rotation=[0,0,0],exposure=0,
        light_softness=1,lighting=copy.deepcopy(LIGHTING_DEFAULTS),texture_scale=1,texture_rotation=0,material_overrides=[],object_material_overrides=[],
        repair={'normals':False,'smooth':False},pose={},time_budget=1200,max_corrections=2,
        source='rules',visual_review=True,pending=['实际摆放方向与主展示面请在预览中确认'],shots=[
        dict(id='hero',label='白底主图',kind='full',projection='orthographic',azimuth=35,elevation=18,focal_length=70,style='white'),
        dict(id='campaign',label='棚拍宣传',kind='full',projection='orthographic',azimuth=-38,elevation=22,focal_length=60),
        dict(id='side',label='侧面结构',kind='full',projection='orthographic',azimuth=90,elevation=12,focal_length=70),
        dict(id='back',label='背面展示',kind='full',projection='orthographic',azimuth=155,elevation=22,focal_length=70),
        dict(id='detail1',label='材质细节',kind='detail',projection='orthographic',azimuth=30,elevation=25,focal_length=85),
        dict(id='detail2',label='结构细节',kind='detail',projection='orthographic',azimuth=-60,elevation=35,focal_length=85)])

def number(v, lo, hi, key):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError('%s 必须为 %s–%s 的有限数值' % (key,lo,hi))


def validate_lighting(value, key='lighting'):
    if not isinstance(value,dict) or set(value)-set(LIGHTING_LIMITS):
        raise ValueError(key+' 灯光参数字段无效')
    for name, setting in value.items():
        number(setting,*LIGHTING_LIMITS[name],key+'.'+name)


def validate_texture_override(material):
    """Validate data only; API callers also confine files to the project folder."""
    if 'textures' in material:
        textures=material['textures']
        if not isinstance(textures,dict) or set(textures)-TEXTURE_CHANNELS:
            raise ValueError('外部材质贴图通道无效')
        for path in textures.values():
            if not isinstance(path,str) or not path or len(path)>4096 or any(c in path for c in ('\x00','\n','\r')):
                raise ValueError('外部材质贴图路径无效')
            if path.lower().startswith(('http:','https:','file:','data:')):
                raise ValueError('外部贴图须先下载到当前项目')
    for key,limit in [('source',2048),('license',256)]:
        if key in material and (not isinstance(material[key],str) or len(material[key])>limit):
            raise ValueError('外部材质来源或许可无效')
    for key,lo,hi in [('texture_scale',.01,1000),('texture_rotation',-360,360)]:
        if key in material: number(material[key],lo,hi,key)
    if 'resource' in material:
        resource=material['resource']
        allowed={'provider','asset_id','name','url','license_url','resolution','downloaded_at','manifest_path'}
        if not isinstance(resource,dict) or set(resource)-allowed:
            raise ValueError('外部材质来源记录无效')
        for key,value in resource.items():
            if key=='downloaded_at': number(value,0,1e12,'downloaded_at')
            elif not isinstance(value,str) or len(value)>4096:
                raise ValueError('外部材质来源记录无效')

def validate(plan):
    p = copy.deepcopy(plan)
    for k, options in [('style',['white','soft','dark']),('aspect',['1:1','4:3','3:4'])]:
        if p.get(k) not in options: raise ValueError(k+' 不支持该选项')
    for k,lo,hi in [('margin',.03,.4),('exposure',-4,4),('light_softness',.25,4),('texture_scale',.01,1000),('texture_rotation',-360,360),('time_budget',10,14400),('max_corrections',0,2)]:
        number(p.get(k),lo,hi,k)
    if int(p['max_corrections']) != p['max_corrections']: raise ValueError('修正次数须为整数')
    if 'lighting' in p: validate_lighting(p['lighting'])
    if len(p.get('rotation',[])) != 3: raise ValueError('rotation 需要三个角度')
    for v in p['rotation']: number(v,-360,360,'rotation')
    if 'dimensions_mm' in p:
        if len(p['dimensions_mm'])!=3: raise ValueError('尺寸需要三个数值')
        for v in p['dimensions_mm']: number(v,.001,100000,'dimensions_mm')
    if not isinstance(p.get('repair'),dict) or any(type(v) is not bool for v in p['repair'].values()): raise ValueError('修复参数无效')
    shots = p.get('shots',[])
    if not 1 <= len(shots) <= 12 or len({s['id'] for s in shots}) != len(shots): raise ValueError('镜头数量或标识无效')
    for s in shots:
        validate_shot(s)
        if not str(s['id']).replace('_','').isalnum() or len(s['id'])>32: raise ValueError('镜头标识无效')
        if s['kind'] not in ['full','detail']: raise ValueError('镜头类型无效')
        for k,lo,hi in [('azimuth',-360,360),('elevation',-80,90),('focal_length',20,200)]: number(s.get(k),lo,hi,k)
    checkpoints=p.get('scene_checkpoints',{})
    if (not isinstance(checkpoints,dict) or set(checkpoints)-{s['id'] for s in shots}
            or any(not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{32}',token) for token in checkpoints.values())):
        raise ValueError('已优化场景必须引用当前镜头的有效快照标识')
    if type(p.get('visual_review',True)) is not bool: raise ValueError('视觉检查开关必须为布尔值')
    adjustments=p.get('shot_adjustments',{})
    if not isinstance(adjustments,dict): raise ValueError('镜头自动修正参数无效')
    for shot_id,adjustment in adjustments.items():
        if shot_id not in {s['id'] for s in shots} or not isinstance(adjustment,dict) or set(adjustment)-{'exposure_delta','softness_multiplier','lighting'}: raise ValueError('镜头自动修正引用或参数无效')
        if 'exposure_delta' in adjustment: number(adjustment['exposure_delta'],-4,4,'exposure_delta')
        if 'softness_multiplier' in adjustment: number(adjustment['softness_multiplier'],.0625,16,'softness_multiplier')
        if 'lighting' in adjustment: validate_lighting(adjustment['lighting'],'shot_adjustments.'+shot_id+'.lighting')
    for m in p.get('material_overrides',[]):
        validate_texture_override(m)
        if m.get('preset') not in PRESETS: raise ValueError('未知材质预设')
        if not isinstance(m.get('name'),str): raise ValueError('材质名缺失')
        for k in ['roughness','metallic','transmission']:
            if k in m: number(m[k],0,1,k)
        if 'color' in m:
            color=m['color']
            if isinstance(color,str):
                if len(color)!=7 or color[0]!='#': raise ValueError('颜色应为 #RRGGBB')
                int(color[1:],16)
            elif isinstance(color,list) and len(color) in (3,4):
                for v in color: number(v,0,1,'color')
            else: raise ValueError('颜色无效')
    local_materials=p.get('object_material_overrides',[])
    if not isinstance(local_materials,list) or len(local_materials)>1000: raise ValueError('局部材质参数无效')
    seen=set()
    for m in local_materials:
        if not isinstance(m,dict) or set(m)-{'object_name','material_name','preset','color','roughness','metallic','transmission','source','preserve_original','textures','license','resource','texture_scale','texture_rotation'}: raise ValueError('局部材质字段无效')
        validate_texture_override(m)
        if not all(isinstance(m.get(k),str) and m[k] for k in ['object_name','material_name']): raise ValueError('局部材质必须指定部件和材质名称')
        pair=(m['object_name'],m['material_name'])
        if pair in seen: raise ValueError('局部材质重复')
        seen.add(pair)
        if 'preset' in m and m['preset'] not in PRESETS: raise ValueError('未知局部材质预设')
        for k in ['roughness','metallic','transmission']:
            if k in m: number(m[k],0,1,k)
        if 'preserve_original' in m and type(m['preserve_original']) is not bool: raise ValueError('局部材质保留标记无效')
        if 'color' in m:
            color=m['color']
            if not isinstance(color,str) or len(color)!=7 or color[0]!='#': raise ValueError('局部颜色应为 #RRGGBB')
            int(color[1:],16)
    pose = p.get('pose',{})
    if not isinstance(pose,dict): raise ValueError('pose 无效')
    if 'frame' in pose: number(pose['frame'],0,100000,'frame')
    for k in ['rotation','translation']:
        if k in pose:
            if len(pose[k])!=3: raise ValueError('姿态需要三个坐标')
            for v in pose[k]: number(v,-1000,1000,k)
    return p

def enrich(plan, analysis):
    p=copy.deepcopy(plan)
    objects=[o for o in analysis.get('objects',[]) if o.get('type','MESH')=='MESH']
    if objects:
        p['shots'][-2]['target_object']=objects[0]['name']
        p['shots'][-1]['target_object']=objects[-1]['name']
    p['model_features']=analysis.get('features',{})
    p['material_suggestions']=analysis.get('materials',[])
    p['pending']=list(dict.fromkeys(p.get('pending',[])+analysis.get('warnings',[])))
    return validate(p)

def apply_material_note(plan, analysis, note):
    """Unscoped product metadata is context, not permission to replace all slots."""
    if not note: return plan
    p=copy.deepcopy(plan)
    words={'哑光塑料':'matte_plastic','亮面塑料':'glossy_plastic','喷涂金属':'painted_metal','拉丝金属':'brushed_metal','抛光金属':'polished_metal','玻璃':'glass','橡胶':'rubber','织物':'fabric','皮革':'leather','木材':'wood','陶瓷':'ceramic'}
    matches=[v for k,v in words.items() if k in note]
    whole_expression=r'(?:(?:整个|全部|全)(?:产品|模型|物体)|(?:所有|全部)(?:部件|材质槽))'
    # Merely saying the product "contains glass" does not mean every slot is glass.
    material_expression='(?:'+'|'.join(words)+')'
    uniform=bool(re.fullmatch(whole_expression+r'(?:都|均|全部|统一)?(?:是|为|采用|使用|改为|设为|换成|改成|用)(?:同一种)?(?:蓝色|红色|黑色|白色|绿色)?'+material_expression+r'(?:材质|材料)?[。.!！]?',re.sub(r'\s+','',note)))
    negative=bool(re.search(r'不要|不是|并非|不能|不改|别改|禁止|保持.*(?:材质|材料)',note))
    uncertain=bool(re.search(r'可能|似乎|好像|或许|不确定|大概|应该|是否|吗|[?？]',note))
    local=bool(re.search(r'盖子|瓶盖|手柄|把手|底座|按钮|外壳|内壁|局部|其中|某个|某一',note))
    protections=extract_protections(note)
    if uniform and not negative and not uncertain and not local and len(matches)==1 and not set(protections)&{'preserve_material','preserve_color'}:
        colors={'蓝色':'#366BA6','红色':'#B93832','黑色':'#15191E','白色':'#EAEAEA','绿色':'#366B55'}
        mentioned=[v for k,v in colors.items() if k in note]
        color=mentioned[0] if len(mentioned)==1 and 'preserve_color' not in protections else None
        for material in analysis.get('materials',[]):
            m={'name':material['name'],'preset':matches[0],'source':'user','confidence':1.0,'preserve_original':False}
            if color:m['color']=color
            old=next((entry for entry in p['material_overrides'] if entry['name']==material['name']),None)
            if old is not None:p['material_overrides'].remove(old)
            p['material_overrides'].append(m)
        p['source']='user'
        p.setdefault('pending',[]).append('已按明确的全产品材质说明应用预设；请检查预览。')
    else:p.setdefault('pending',[]).append('材质说明仅作为参考，未批量改动原材质；局部调整请点选部件：'+note[:200])
    return validate(p)


def _instruction_metadata(protections, *records):
    """Retain validated semantic decisions without weakening explicit locks.

    Records come from the validated AI result or InstructionOutcome. The
    planner adds its own preflight restrictions, rather than overwriting the
    protections the semantic decision has resolved from the original request.
    """
    inherited=list(protections)
    for record in records:
        if isinstance(record,dict):
            inherited.extend(record.get('protections',[]))
    resolved=sorted({value for value in inherited if isinstance(value,str) and value in PROTECTION_LABELS})
    metadata={'protections':resolved,'protected_fields':instruction_protected_fields(resolved),
              'protected_material_fields':instruction_protected_material_fields(resolved)}
    for key in ('semantic_intents','semantic_constraints','source_spans'):
        values=[]
        for record in records:
            if isinstance(record,dict):
                for value in record.get(key,[]):
                    if value not in values:values.append(copy.deepcopy(value))
        if values:metadata[key]=values
    for record in records:
        if isinstance(record,dict) and record.get('protocol')=='semantic_v2':
            metadata['protocol']='semantic_v2'
            if isinstance(record.get('reference_evidence'),dict):
                metadata['reference_evidence']=copy.deepcopy(record['reference_evidence'])
    return metadata


def _outcome_metadata(outcome, metadata):
    for key,value in metadata.items():setattr(outcome,key,copy.deepcopy(value))
    return outcome


def instruct(plan,text,analysis=None,image_path=None,selections=None,context=None,reference_path=None):
    import ai_config
    import ai_client
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise InstructionOutcome('needs_clarification','请用不超过 2000 字说明需要调整的效果。')
    text=text.strip()
    supplied=context or {}
    # Legacy part-feedback callers keep their established main-model routing.
    # New context-aware requests use the configured vision route when images exist.
    visual=context is not None and bool(image_path or reference_path)
    config=ai_config.get_vision_config() if visual else ai_config.get_config()
    record=ai_config.record_vision_result if visual else ai_config.record_result
    ai_ready=bool(config.get('enabled') and config.get('api_key') and config.get('model'))
    semantic=ai_ready and supplied.get('instruction_protocol')=='semantic_v2'
    if semantic:
        # Current language belongs to the semantic model. Only already
        # structured, inherited locks are considered before the request.
        protections=sorted({value for value in supplied.get('protections',[]) if isinstance(value,str) and value in PROTECTION_LABELS})
    else:
        # Explicit legacy API compatibility and offline quick controls retain
        # their old deterministic language handling; online v2 never enters it.
        protections=extract_protections(text,supplied.get('protections'))
        if preservation_only(text):
            raise InstructionOutcome('no_change','这次只有保留要求，没有需要执行的参数调整。',protections=protections)
        ai_client.check_instruction_support(text,protections,allow_partial=context is not None,
                                            allow_semantic=ai_ready and context is not None)
    if ai_ready:
        try:
            kwargs={'image_path':image_path,'selections':selections}
            if context is not None:kwargs['context']=context
            if reference_path is not None:kwargs['reference_path']=reference_path
            p=validate(ai_client.request_plan(plan,text,analysis,config,**kwargs))
            diff=ai_client.parameter_diff(plan,p)
            if not diff:
                metadata=_instruction_metadata(protections,p.get('last_instruction',{}) if context is not None else {})
                raise _outcome_metadata(InstructionOutcome('no_change','当前参数已满足这次要求，没有新增修改。',protections=metadata['protections'],
                                         used_image=bool(image_path),used_reference=bool(p.get('last_instruction',{}).get('used_reference')) if semantic else bool(reference_path),source='ai',
                                         provider=config['provider'],model=config['model']),metadata)
        except InstructionOutcome as exc:
            proposal=getattr(exc,'proposal',None)
            metadata=_instruction_metadata(protections,vars(exc))
            _outcome_metadata(exc,metadata)
            if exc.status=='partial_available' and isinstance(proposal,dict):
                candidate=validate(proposal['candidate_plan'])
                explanation=candidate.setdefault('last_instruction',{})
                metadata=_instruction_metadata(protections,vars(exc),explanation)
                _outcome_metadata(exc,metadata)
                diff=ai_client.parameter_diff(plan,candidate)
                if not diff and (not proposal.get('ready') or any(row.get('status')!='already_satisfied' for row in proposal['ready'])):
                    raise _outcome_metadata(InstructionOutcome('needs_clarification','没有可先应用的参数变化，请确认待处理部位。',
                                             protections=metadata['protections'],source='ai',provider=config['provider'],model=config['model']),metadata)
                explanation.update(text=text,changes=copy.deepcopy(diff),parameter_diff=copy.deepcopy(diff),
                                   source='ai',provider=config['provider'],model=config['model'],
                                   used_image=bool(image_path),used_reference=bool(explanation.get('used_reference',getattr(exc,'used_reference',False))) if semantic else bool(reference_path),
                                   selections=copy.deepcopy(selections or []),**metadata)
                if any(change['path'].split('.')[0] in ('margin','aspect','style','texture_scale','texture_rotation','material_overrides') for change in diff):
                    explanation['notes']=list(dict.fromkeys(explanation.get('notes',[])+['留白、画幅、背景风格和全局材质等项目参数会影响其他镜头。']))
                candidate['source']='ai';proposal['candidate_plan']=candidate
            if exc.source=='ai':
                record(config,True,'AI 已解析可执行项与未完成项。' if proposal else 'AI 已返回说明；本次没有修改方案。')
            raise
        except ValueError as exc:
            record(config,False,str(exc))
            raise
        explanation=p.get('last_instruction',{}) if context is not None else {}
        metadata=_instruction_metadata(protections,explanation)
        notes=list(explanation.get('notes',[]))
        if any(change['path'].split('.')[0] in ('margin','aspect','style','texture_scale','texture_rotation','material_overrides') for change in diff):
            notes.append('留白、画幅、背景风格和全局材质等项目参数会影响其他镜头。')
        p['source']='ai'
        p['last_instruction']={'text':text,'changes':diff,'parameter_diff':copy.deepcopy(diff),'summary':describe_changes(diff),
                               'explanation':explanation.get('summary',''),'notes':list(dict.fromkeys(notes)),
                               **metadata,
                               'source':'ai','provider':config['provider'],'model':config['model'],
                               'used_image':bool(image_path),'used_reference':bool(explanation.get('used_reference',False)) if semantic else bool(reference_path),
                               'selections':copy.deepcopy(selections or [])}
        if explanation.get('target_bindings'):
            p['last_instruction']['target_bindings']=copy.deepcopy(explanation['target_bindings'])
        record(config,True,'AI 已返回通过校验的摄影参数。')
        return p
    if reference_path:
        raise InstructionOutcome('unsupported','参考图片调整需要配置可用的视觉 AI；内置规则不会假装比较参考图。',protections=protections)
    patch,notes,protections=rule_patch(plan,text,context=context)
    enforce_protections(patch,protections,supplied.get('active_shot_id'))
    p=validate(ai_client.apply_patch(plan,patch,analysis))
    diff=ai_client.parameter_diff(plan,p)
    if not diff:
        raise InstructionOutcome('no_change','参数没有变化，可能已达到所要求的值或调整上限。',notes=notes,protections=protections)
    if image_path:notes.append('本次使用内置规则，未分析图片内容。')
    p['source']='rules'
    p['last_instruction']={'text':text,'changes':diff,'parameter_diff':copy.deepcopy(diff),'source':'rules',
                           'summary':describe_changes(diff),'notes':notes,'protections':protections,
                           'protected_fields':instruction_protected_fields(protections),
                           'protected_material_fields':instruction_protected_material_fields(protections),
                           'used_image':False,'used_reference':False}
    return p

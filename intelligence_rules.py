"""Deterministic instruction boundaries shared by AI and offline controls.

This module does not read configuration, files, or credentials.  A recognised
fragment never authorises executing only part of an otherwise unknown request.
"""
import copy
import re
from lighting_controls import LIGHTING_DEFAULTS, LIGHT_DIRECTION_LIMITS


class InstructionOutcome(ValueError):
    """A useful non-executing response; callers must not save or render it."""

    def __init__(self, status, message, notes=None, protections=None, used_image=False, used_reference=False, source='rules', provider=None, model=None):
        self.status = status
        self.message = message
        self.notes = list(notes or [])
        self.protections = list(protections or [])
        self.protected_fields = instruction_protected_fields(self.protections)
        self.protected_material_fields = instruction_protected_material_fields(self.protections)
        self.used_image = bool(used_image)
        self.used_reference = bool(used_reference)
        self.source = source
        self.provider = provider
        self.model = model
        super().__init__(message)


PROTECTION_LABELS = {
    'preserve_color': '保留产品原色与图案',
    'preserve_material': '不修改材质',
    'preserve_camera': '保持镜头与产品角度',
    'preserve_geometry': '保持产品结构与姿态',
    'preserve_background': '保持背景风格',
    'preserve_exposure': '保持曝光',
    'preserve_margin': '保持留白',
    'preserve_lighting': '保持灯光与曝光',
    'preserve_light_direction': '保持光源方向',
    'only_exposure': '仅调整曝光',
    'only_lighting': '仅调整灯光',
    'only_margin': '仅调整留白',
    'only_camera': '仅调整当前镜头参数',
}
_LIGHT_DIRECTION_TARGET = r'(?:(?:主光|补光|轮廓光|灯光|光源|光照|光线|布光)(?:的)?(?:方位角|水平角|俯仰角|角度|方向|朝向|位置))'
_TARGETS = {
    'preserve_light_direction': _LIGHT_DIRECTION_TARGET,
    'preserve_color': r'(?:颜色|原色|色彩|图案|标签|标识|logo)',
    'preserve_material': r'(?:材质(?:类型)?|材料)',
    'preserve_camera': r'(?:镜头(?:角度|方位角|水平角|俯仰角|视角)?|相机(?:角度|方位角|水平角|俯仰角|视角)?|拍摄角度|角度|机位|视角)',
    'preserve_geometry': r'(?:结构|几何|形状|姿态)',
    'preserve_background': r'(?:背景(?:风格)?|棚拍风格)',
    'preserve_exposure': r'(?:曝光(?:补偿)?)',
    'preserve_margin': r'(?:留白)',
    'preserve_lighting': r'(?:灯光|光照|光线|布光)',
}
_ADJECTIVES = r'(?:产品|模型|原始|原有|原来|当前|现有|现在|本来|所有|的)*'
_NUMBER = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))'
_PLAN_ROOTS = {'style', 'aspect', 'margin', 'exposure', 'light_softness', 'lighting',
               'texture_scale', 'texture_rotation', 'rotation', 'shots', 'pose',
               'material_overrides', 'object_material_overrides', 'repair'}
_CAMERA_FIELDS = {'id', 'azimuth', 'elevation', 'focal_length', 'target_object'}


def clauses(text):
    # Preserve relative words such as “再柔和一点”; split actual conjunctions.
    parts = re.split(r'[，,。；;！？!?\n]+|并且|而且|同时|以及|但是|但|而是|然后|并|且', text)
    return [part.strip() for part in parts if part.strip()]


_MATERIAL_VALUE_TERMS = (r'材质|颜色|橡胶|金属|镀铬|不锈钢|拉丝|塑料|玻璃|陶瓷|木纹|木材|'
                         r'皮革|织物|布料|粗糙|反光|光泽|哑光|磨砂|透明|[红橙黄绿青蓝紫黑白灰银金棕]色|'
                         r'rubber|metal|plastic|glass|leather|wood')
_MATERIAL_NEGATION = r'不要|别(?:把|将|让|改|换)|无需|不用|不必|保持|保留|维持|不改|不变|不换'
_MATERIAL_JOIN = r'、|，|,|以及|连同|和|与|及|跟'
_PHOTO_SUBJECT = (r'(?:当前(?:画面|镜头|预览|图片)?(?:的)?)?'
                  r'(?:背景(?:颜色|色彩|风格)?|灯光|光线|光照|主光|补光|轮廓光|环境光|曝光(?:补偿)?|'
                  r'镜头(?:角度|焦距|视角)?|相机(?:角度|焦距|视角)?)')


def material_clause(clause):
    """Parse stated subjects and their shared predicate, never identify meshes."""
    if re.search(_MATERIAL_NEGATION,clause):return None
    match=re.match(r'\s*(?P<prefix>(?:请)?(?:把|将|让)?)(?P<part>.{1,240}?)'
                   r'(?P<predicate>(?:(?:全都|全部|都|均|也))?(?:要(?:是)?|应为|是|为|改成|换成|改为|设为)\s*(?P<value>.+))$',clause)
    if not match:
        # A named part's finish is an explicit material request even without
        # the assignment verb 是/改为. Keep the original predicate and subject
        # intact; this recognises scope only and never identifies scene meshes.
        # Brightness alone remains a photography goal, not a material edit.
        match=re.fullmatch(r'\s*(?P<prefix>(?:请)?(?:把|将|让)?)(?P<part>.{1,80}?)'
                           r'(?:的)?(?P<predicate>(?P<value>(?:(?:更|再|稍微|略微|有|增加|增强|提高)\s*)?'
                           r'(?:光泽|反光|粗糙度).+))',clause)
        if not match:return None
        subject=match.group('part').strip().removesuffix('的')
        if not (_bare_material_subject(subject) or target_material_whole_subject(subject)):
            return None
    if not re.search(_MATERIAL_VALUE_TERMS,match.group('value'),re.I):return None
    subjects=[part.strip() for part in re.split(_MATERIAL_JOIN,match.group('part')) if part.strip()]
    if not subjects or any(len(part)>80 or re.fullmatch(_PHOTO_SUBJECT,part) for part in subjects):return None
    return {'subjects':subjects,'prefix':match.group('prefix'),'predicate':match.group('predicate'),
            'value':match.group('value')}


def target_material_whole_subject(value):
    """Syntactic whole-product phrases, kept separate from actual grounding."""
    return bool(re.fullmatch(r'(?:整个(?:产品|模型|物体)|(?:所有|全部)(?:部件|材质|产品)|(?:产品|模型)整体)(?:的)?(?:材质|颜色|表面)?',value))


def _bare_material_subject(value):
    """Only noun-like list members may inherit a following material predicate."""
    value=re.sub(r'^(?:请)?(?:把|将|让)?','',value.strip())
    if (not value or len(value)>80 or re.search(_MATERIAL_NEGATION,value)
            or re.search(r'调整|改善|优化|增加|减少|提高|降低|增强|减弱|修改|设置|设为|改为|是|要|希望|好看|美观|真实|逼真|质感|构图|曝光|灯光|镜头|相机|背景',value)):
        return False
    parts=[part.strip() for part in re.split(_MATERIAL_JOIN,value) if part.strip()]
    # This is a syntactic guard on comma inheritance, not a semantic catalogue.
    # Exact object names are permitted, but they still need ordinary grounding.
    return bool(parts) and all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]*',part)
        or re.search(r'(?:钥匙|孔|锁|锁芯|支架|安装板|轴|轮|轮子|轮胎|把手|手柄|面|板|门|腿|脚|柜体|箱体|机身|壳|框|架|盖|底座|边框|螺丝|螺母|部件|零件|部位|部分|表面|扶手|抽屉|铰链|铰链座)$',part)
        for part in parts)


def instruction_clauses(text):
    """Keep a true material list together before expanding its shared meaning.

    Commas only join adjacent bare nouns to an explicit material predicate.
    Sentence boundaries, preservation clauses and independently stated actions
    cannot acquire the following subject's material by proximity.
    """
    result=[]
    for block in re.split(r'[。；;！？!?\n]+|并且|而且|同时|但是|但|而是|然后|并|且',text):
        pending=[]
        for match in re.finditer(r'[^，,]+',block):
            value=match.group().strip()
            if not value:continue
            stated=material_clause(value)
            if stated:
                joined=block[pending[0].start():match.end()].strip() if pending else value
                if pending and material_clause(joined):
                    result.append(joined);pending=[]
                else:
                    for part in pending:result.extend(clauses(part.group()))
                    pending=[];result.append(value)
            elif _bare_material_subject(value):pending.append(match)
            else:
                for part in pending:result.extend(clauses(part.group()))
                pending=[];result.extend(clauses(value))
        for part in pending:result.extend(clauses(part.group()))
    return result


def general_image_goal(text):
    """A broad visual goal permits a reversible photography attempt, not proof."""
    value=re.sub(r'\s+','',text).strip('。.!！?？')
    value=re.sub(r'^(?:请|帮我|帮忙|麻烦|我想|希望|需要)+','',value)
    subject=r'(?:(?:整个|整体|这个|当前|这张)(?:的)?)?(?:物体|产品|模型|商品|画面|图片|照片|图像|图|整体)'
    return bool(re.fullmatch(r'(?:让|使|把|将)?'+subject+r'(?:整体)?(?:看起来)?(?:更|再)?'
                            r'(?:好看|美观|自然|真实|逼真|有质感|专业)(?:一点|一些|点|些)?',value)
                or re.fullmatch(r'(?:改善|优化|提升)'+subject+r'(?:的)?(?:整体)?(?:观感|视觉效果|效果|质感)',value))


def _compact(text):
    return re.sub(r'\s+', '', text).strip('。.!！?？')


def _negative(clause):
    return bool(re.match(r'^(?:请)?(?:不要|别|不必|无需|不需要|不用|禁止|不再|不是)', _compact(clause)))


def _shared_constraint_targets(value):
    """Recognise coordinated known subjects sharing one preservation verb."""
    target = '(?:' + '|'.join(_TARGETS.values()) + ')'
    subject = _ADJECTIVES + target
    subjects = subject + r'(?:(?:和|与|及|、|/|&)' + subject + r')*'
    patterns = [
        r'^(?:请)?(?:保持|保留|维持)(' + subjects + r')(?:都|均)?(?:不变|不动|原样)?$',
        r'^(?:请)?(?:不要|别|禁止|不)(?:再)?(?:改变|修改|改动|调整|更换|改|动|换)(' + subjects + r')(?:了)?$',
        r'^(' + subjects + r')(?:都|均)?(?:保持|维持)?(?:不变|不动|别动|不要改|不能改|不要修改)$',
    ]
    for pattern in patterns:
        matched = re.fullmatch(pattern, value, re.I)
        if matched:
            subject=matched.group(1)
            return {name for name, expression in _TARGETS.items()
                    if re.search(expression,subject if name=='preserve_light_direction'
                                 else re.sub(_LIGHT_DIRECTION_TARGET,'',subject),re.I)}
    return set()


def _negative_direction_targets(value):
    action = r'(?:增加|减少|提高|降低|调高|调低|增强|减弱|改变|修改|改动|调整|更换|改|动|换)'
    matches = set()
    for name, target in _TARGETS.items():
        before = r'^(?:请)?(?:不要|别|不|禁止)(?:再)?' + action + _ADJECTIVES + target + r'(?:了)?$'
        after = r'^' + _ADJECTIVES + target + r'(?:都|均)?(?:不要|别|不能|不)(?:再)?' + action + r'(?:了)?$'
        if re.fullmatch(before, value, re.I) or re.fullmatch(after, value, re.I):
            matches.add(name)
    return matches


def extract_protections(text, inherited=None):
    """Only explicit user statements become enforceable restrictions.

    Historical instructions are never implicitly inherited.  The service can
    supply an explicit resolved list through context.protections.
    """
    protections = {p for p in (inherited or []) if isinstance(p, str) and p in PROTECTION_LABELS}
    for clause in clauses(text):
        value = _compact(clause)
        protections.update(_shared_constraint_targets(value))
        protections.update(_negative_direction_targets(value))
        for name, target in _TARGETS.items():
            candidate=value if name=='preserve_light_direction' else re.sub(_LIGHT_DIRECTION_TARGET,'',value)
            release = re.search(r'(?:这次|本次)?(?:可以|允许|同意)(?:改变|修改|改动|调整|更换|改|动|换)' + _ADJECTIVES + target, candidate, re.I)
            if release:
                protections.discard(name)
                continue
            before = re.search(r'(?:不要|别|禁止|不)(?:再)?(?:改变|修改|改动|调整|更换|改|动|换)' + _ADJECTIVES + target, candidate, re.I)
            # A desired appearance after a property (e.g. “保持灯光自然”) is
            # semantic work, not an instruction to freeze that control. Only
            # an actual no-change form becomes a deterministic hard lock.
            keep = re.search(r'(?:保持|保留|维持)' + _ADJECTIVES + target
                             + r'(?:(?:都|均)?(?:不变|不动|原样))?$', candidate, re.I)
            after = re.search(target + r'(?:保持|维持)?(?:不变|别动|不动|不要改|不能改)', candidate, re.I)
            if before or keep or after:
                protections.add(name)
        # A rejected direction is conservatively locked, never interpreted as
        # its positive counterpart. A conflicting positive request is explained.
        if re.search(r'(?:不要|别|不)(?:增加|减少|调整|改变)(?:画面)?留白', value):
            protections.add('preserve_margin')
        if re.search(r'(?:只|仅)(?:需要|想|要)?(?:修改|改动|调整|改变|改|动)?(?:一下)?曝光', value):
            protections.add('only_exposure')
        if re.search(r'(?:只|仅)(?:需要|想|要)?(?:修改|改动|调整|改变|改|动)?(?:一下)?(?:灯光|光照|光线|布光)', value):
            protections.add('only_lighting')
        if re.search(r'(?:只|仅)(?:需要|想|要)?(?:修改|改动|调整|改变|增加|减少|改|动)?(?:一下)?(?:画面)?留白', value):
            protections.add('only_margin')
        if re.search(r'^(?:请)?(?:只|仅)(?:让|把|将)?当前镜头(?:换成|改成|设为|调整为)?(?:俯拍|正面|侧面|右侧面|左侧面|背面|三分之四(?:视角)?)$', value):
            protections.add('only_camera')
        if re.search(r'(?:只|仅)(?:修改|改动|调整|改变|改|动)(?:当前镜头|镜头参数|相机参数|机位|角度|视角|焦距)', value):
            protections.add('only_camera')
    return sorted(protections)


def instruction_protected_fields(protections):
    locks = set(protections)
    fields = set()
    for lock, permitted in [('only_exposure', {'exposure'}), ('only_lighting', {'lighting', 'light_softness'}), ('only_margin', {'margin'}), ('only_camera', {'shots'})]:
        if lock in locks:
            fields.update(_PLAN_ROOTS - permitted)
    for lock, forbidden in [
        ('preserve_material', {'material_overrides', 'object_material_overrides', 'texture_scale', 'texture_rotation'}),
        ('preserve_camera', {'shots', 'rotation', 'pose'}),
        ('preserve_geometry', {'rotation', 'pose', 'repair'}),
        ('preserve_background', {'style'}), ('preserve_exposure', {'exposure'}),
        ('preserve_margin', {'margin'}), ('preserve_lighting', {'exposure', 'light_softness', 'lighting'}),
    ]:
        if lock in locks:
            fields.update(forbidden)
    if 'preserve_light_direction' in locks:
        fields.update('lighting.'+key for key in LIGHT_DIRECTION_LIMITS)
    return sorted(fields)


def instruction_protected_material_fields(protections):
    return ['color', 'preset'] if 'preserve_color' in protections else []


def check_unsupported_requests(text, protections=None, *, allow_semantic=False):
    """Enforce capability boundaries, optionally defer language to the LLM.

    An unrecognised negative clause can express an outcome such as avoiding
    backlighting, rather than a prohibition on editing a control. The LLM path
    must interpret that original clause; the offline path still refuses an
    unknown compound instead of applying just its recognised positive part.
    """
    unsupported = [
        (r'(?:加|增加|添加|做|生成|修复|调整).*?倒角|倒角(?:半径|大小)', '自动添加或重建倒角'),
        (r'(?:添加|增加|生成|删除|移除|重建|补齐|补上|修复)(?:一个|一些|缺失的|缺失|新的|新)?(?:零件|部件|几何|网格|模型结构|孔洞)', '创建、删除或重建产品几何'),
        (r'(?:重新)?(?:建模|拓扑重建|重拓扑|UV展开|重新展开UV)', '重新建模、拓扑或 UV 展开'),
        (r'(?:更换|替换|重写|重新设计|修改|改)(?:产品)?(?:文字|字体|商标|logo|标签内容)', '重绘产品文字或标识'),
        (r'(?:背景|场景).*(?:换成|改成|设为|变成).*(?:厨房|街道|森林|办公室|海滩|卧室|房间)', '生成实景背景或新场景'),
    ]
    for clause in clauses(text):
        if _negative(clause):
            continue
        for pattern, label in unsupported:
            if re.search(pattern, clause, re.I):
                raise InstructionOutcome('unsupported', '当前不支持' + label + '。此次要求尚未执行，请移除这部分要求或在建模软件中处理。', protections=protections)
    parts = clauses(text)
    if not allow_semantic and any(not _negative(part) and not _constraint_only(_clean_clause(part)) for part in parts):
        for part in parts:
            if _negative(part) and not _constraint_only(_clean_clause(part)):
                raise InstructionOutcome('needs_clarification', '无法确认“' + part[:160] + '”具体限制哪些参数，尚未执行。请明确需要保持的颜色、材质、角度、灯光、曝光或留白。', protections=protections)


def restrict_patch_schema(schema, protections, active_shot_id=None):
    """Create an executable whitelist, not merely a natural-language promise."""
    result = copy.deepcopy(schema)
    properties = result['properties']
    locks = set(protections)
    if 'only_exposure' in locks:
        properties = {k: v for k, v in properties.items() if k == 'exposure'}
    if 'only_lighting' in locks:
        properties = {k: v for k, v in properties.items() if k in ('lighting', 'light_softness')}
    if 'only_margin' in locks:
        properties = {k: v for k, v in properties.items() if k == 'margin'}
    if 'only_camera' in locks:
        properties = {k: v for k, v in properties.items() if k == 'shots'}
    result['properties'] = properties
    forbidden = set()
    if 'preserve_material' in locks:
        forbidden.update(('material_overrides', 'object_material_overrides', 'texture_scale', 'texture_rotation'))
    if 'preserve_camera' in locks or 'preserve_geometry' in locks:
        forbidden.update(('rotation', 'pose'))
    if 'preserve_camera' in locks:
        forbidden.add('shots')
    if 'preserve_background' in locks:
        forbidden.add('style')
    if 'preserve_exposure' in locks:
        forbidden.add('exposure')
    if 'preserve_margin' in locks:
        forbidden.add('margin')
    if 'preserve_lighting' in locks:
        forbidden.update(('exposure', 'light_softness', 'lighting'))
    for key in forbidden:
        properties.pop(key, None)
    if 'preserve_light_direction' in locks and 'lighting' in properties:
        for key in LIGHT_DIRECTION_LIMITS:properties['lighting']['properties'].pop(key,None)
    if 'preserve_color' in locks:
        for key in ('material_overrides', 'object_material_overrides'):
            if key in properties:
                fields = properties[key]['items']['properties']
                fields.pop('color', None)
                # Preset replacement can replace the base-color node as well.
                fields.pop('preset', None)
    if 'shots' in properties:
        shot = properties['shots']['items']['properties']
        if 'only_camera' in locks:
            shot = {key: value for key, value in shot.items() if key in _CAMERA_FIELDS}
            properties['shots']['items']['properties'] = shot
        if active_shot_id:
            shot['id']['enum'] = [active_shot_id]
            properties['shots']['maxItems'] = 1
        if 'preserve_camera' in locks:
            for key in ('azimuth', 'elevation', 'focal_length', 'target_object'):
                shot.pop(key, None)
        if 'preserve_background' in locks:
            shot.pop('style', None)
        if set(shot) == {'id'}:
            properties.pop('shots')
    return result


def enforce_protections(patch, protections, active_shot_id=None):
    """Readable fail-closed diagnostics in addition to schema validation."""
    if not isinstance(patch, dict):
        return
    locks = set(protections)
    violations = []
    if 'only_exposure' in locks and set(patch) - {'exposure'}:
        violations.append('only_exposure')
    if 'only_lighting' in locks and set(patch) - {'lighting', 'light_softness'}:
        violations.append('only_lighting')
    if 'only_margin' in locks and set(patch) - {'margin'}:
        violations.append('only_margin')
    if 'only_camera' in locks and set(patch) - {'shots'}:
        violations.append('only_camera')
    material_fields = ('material_overrides', 'object_material_overrides')
    if 'preserve_material' in locks and any(k in patch for k in material_fields + ('texture_scale', 'texture_rotation')):
        violations.append('preserve_material')
    if 'preserve_color' in locks:
        if any(isinstance(m, dict) and set(m) & {'color', 'preset'} for k in material_fields if isinstance(patch.get(k), list) for m in patch[k]):
            violations.append('preserve_color')
    for name, fields in [('preserve_camera', ('rotation', 'pose')), ('preserve_geometry', ('rotation', 'pose')), ('preserve_background', ('style',)), ('preserve_exposure', ('exposure',)), ('preserve_margin', ('margin',))]:
        if name in locks and any(k in patch for k in fields):
            violations.append(name)
    if 'preserve_lighting' in locks and set(patch) & {'exposure', 'light_softness', 'lighting'}:
        violations.append('preserve_lighting')
    if ('preserve_light_direction' in locks and isinstance(patch.get('lighting'),dict)
            and set(patch['lighting']) & set(LIGHT_DIRECTION_LIMITS)):
        violations.append('preserve_light_direction')
    if 'preserve_camera' in locks and 'shots' in patch:
        violations.append('preserve_camera')
    for shot in patch.get('shots', []) if isinstance(patch.get('shots', []), list) else []:
        if not isinstance(shot, dict):
            continue
        if active_shot_id and shot.get('id') != active_shot_id:
            raise InstructionOutcome('needs_clarification', '此次只允许调整当前镜头，未修改其他镜头。请明确选择需要调整的画面。', protections=protections)
        if 'only_camera' in locks and set(shot) - _CAMERA_FIELDS:
            violations.append('only_camera')
        if 'preserve_camera' in locks and set(shot) & {'azimuth', 'elevation', 'focal_length', 'target_object'}:
            violations.append('preserve_camera')
        if 'preserve_background' in locks and 'style' in shot:
            violations.append('preserve_background')
    if violations:
        labels = '、'.join(PROTECTION_LABELS[p] for p in dict.fromkeys(violations))
        raise InstructionOutcome('needs_clarification', '修改与“' + labels + '”冲突，因此没有应用。请明确需要改变还是保留这些设置。', protections=protections)


def _clean_clause(clause):
    text = _compact(clause)
    text = re.sub(r'^(?:请|帮我|帮忙|麻烦|我想|希望|需要|请把|请将)+', '', text)
    text = re.sub(r'^(?:只|仅)(?=把|将|让|增加|减少|改|调整|修改|改变)', '', text)
    text = re.sub(r'^(?:把|将|让)?(?:当前镜头|当前画面|这张图|这一张|这张|本张)(?:的)?', '', text)
    text = re.sub(r'^(?:把|将|让)', '', text)
    return text


def _constraint_only(clause):
    if _shared_constraint_targets(clause) or _negative_direction_targets(clause):
        return True
    if re.match(r'^(?:保持|保留|维持)', clause):
        remainder = re.sub(r'^(?:保持|保留|维持)', '', clause)
        recognized = False
        for target in _TARGETS.values():
            recognized = recognized or bool(re.search(target, remainder, flags=re.I))
            remainder = re.sub(target, '', remainder, flags=re.I)
        remainder = re.sub(r'产品|模型|原始|原有|原来|当前|现有|现在|本来|所有|不变|不动|原样|和|与|及|都|的', '', remainder)
        if recognized and not remainder:
            return True
    return bool(re.fullmatch(r'(?:保持|保留|维持)' + _ADJECTIVES + r'(?:颜色|原色|色彩|图案|标签|标识|logo|材质|材料|角度|机位|镜头|视角|结构|几何|形状|姿态|背景|曝光|留白)(?:不变|不动|原样)?', clause, re.I)
                or re.fullmatch(r'(?:颜色|原色|色彩|图案|标签|材质|材料|角度|机位|镜头|视角|结构|姿态|背景|曝光|留白)(?:保持)?(?:不变|不动)', clause)
                or re.fullmatch(r'(?:只|仅)(?:修改|改动|调整|改变|改|动)?(?:曝光|灯光|光照|光线|布光|留白)', clause))


def preservation_only(text):
    parts = clauses(text)
    return bool(parts) and all(_constraint_only(_compact(part)) or _constraint_only(_clean_clause(part)) for part in parts)


def unresolved_negative_clause(text):
    """Whether negative language needs interpretation instead of a guessed lock.

    This is only a routing signal. It never assigns the clause a meaning or
    permits a patch; a semantic decision must still explain the original text.
    Prefix negation deliberately does not include material words like 不锈钢.
    """
    return any(_negative(part) and not _constraint_only(_clean_clause(part))
               for part in clauses(text))


def light_direction_request(text):
    """Identify an explicitly named light direction, never a camera angle."""
    return bool(re.search(_LIGHT_DIRECTION_TARGET,text))


def rule_patch(plan, text, context=None):
    """Parse a small complete grammar; refuse the whole unknown compound."""
    context = context or {}
    protections = extract_protections(text, context.get('protections'))
    check_unsupported_requests(text, protections)
    active = context.get('active_shot_id')
    shots = [s for s in plan['shots'] if not active or s['id'] == active]
    if active and not shots:
        raise InstructionOutcome('needs_clarification', '当前镜头不存在，请重新选择镜头。', protections=protections)
    result = {}
    operations = {}
    notes = []

    def put(path, value):
        if isinstance(value, float):
            value = round(value, 10)
        if path in operations and operations[path] != value:
            raise InstructionOutcome('needs_clarification', '同一参数有互相冲突的要求，尚未执行。请保留一个明确的调整方向。', protections=protections)
        operations[path] = value
        if path.startswith('lighting.'):
            result.setdefault('lighting', {})[path.split('.', 1)[1]] = value
        else:
            result[path] = value

    def camera(values, full_only=False):
        for shot in shots:
            if full_only and not active and shot['kind'] != 'full':
                continue
            for key, value in values.items():
                token = 'shot:' + shot['id'] + ':' + key
                if token in operations and operations[token] != value:
                    raise InstructionOutcome('needs_clarification', '同一镜头有冲突角度要求，尚未执行。请指定一个角度。', protections=protections)
                operations[token] = value
            entries = result.setdefault('shots', [])
            target = next((entry for entry in entries if entry['id'] == shot['id']), None)
            if target is None:
                target = {'id': shot['id']}
                entries.append(target)
            target.update(values)

    def relative(base, action, number, percent=False):
        n = float(number)
        if action in ('增加', '提高', '调高', '增强', '加'):
            return base * (1 + n / 100) if percent else base + n
        if action in ('减少', '降低', '调低', '减弱', '减'):
            return base * (1 - n / 100) if percent else base - n
        return n / 100 if percent else n

    unknown = []
    for raw in clauses(text):
        clause = _clean_clause(raw)
        if _constraint_only(_compact(raw)) or _constraint_only(clause):
            continue
        if clause in ('其他不变', '其余不变', '其他保持不变', '其余保持不变') and any(p.startswith('only_') for p in protections):
            continue
        # Strict full matches avoid treating “不要…” or unrelated text as an action.
        if re.fullmatch(r'(?:让|使)?(?:反光|光线|灯光|光源)?(?:再|更)?(?:柔和|柔)(?:一点|一些|点|些)?', clause):
            put('light_softness', min(4, plan['light_softness'] * 1.4)); continue
        if clause in ('增加留白', '多点留白', '多一点留白', '留白多一点'):
            put('margin', min(.4, plan['margin'] + .05)); continue
        if clause in ('减少留白', '放大产品', '留白少一点'):
            put('margin', max(.03, plan['margin'] - .04)); continue
        if clause in ('亮一点', '亮一些', '提亮', '提亮一点', '再亮一点'):
            put('exposure', min(4, plan['exposure'] + .35)); continue
        if clause in ('暗一点', '暗一些', '再暗一点'):
            put('exposure', max(-4, plan['exposure'] - .35)); continue
        if clause in ('突出表面纹理', '纹理明显', '纹理明显一点'):
            put('light_softness', .65); continue
        style = re.fullmatch(r'(?:(?:背景|风格|棚拍风格)(?:改成|换成|设为|调整为|设成)?|(?:换成|改成|设为))?(白底(?:商品)?|黑底|深色(?:棚拍|质感)?|浅色(?:柔光)?)', clause)
        if style:
            value = style.group(1)
            put('style', 'white' if value.startswith('白') else 'dark' if value.startswith(('黑', '深')) else 'soft')
            notes.append('背景风格作用于项目；白底主图始终保持白色。'); continue
        aspect = re.fullmatch(r'(?:(?:画幅|比例|画幅比例)(?:设为|改成|改为|调整为)?|(?:改成|换成))?(1[:：]1|4[:：]3|3[:：]4|方图|横图|竖图)', clause)
        if aspect:
            value = aspect.group(1).replace('：', ':')
            put('aspect', {'方图': '1:1', '横图': '4:3', '竖图': '3:4'}.get(value, value)); continue
        exposure = re.fullmatch(r'(?:只|仅)?(?:改|调整)?(?:曝光(?:补偿)?)?(设为|设成|改为|调整到|到|增加|提高|调高|加|减少|降低|调低|减)?' + _NUMBER + r'(?:EV|ev)', clause)
        if not exposure:
            exposure = re.fullmatch(r'(?:只|仅)?(?:改|调整)?曝光(?:补偿)?(设为|设成|改为|调整到|到|增加|提高|调高|加|减少|降低|调低|减)?' + _NUMBER, clause)
        if exposure:
            put('exposure', relative(plan['exposure'], exposure.group(1), exposure.group(2))); continue
        margin = re.fullmatch(r'(?:画面)?留白(设为|设成|改为|调整到|到|增加|减少|加|减)?' + _NUMBER + r'(?:%|％|个百分点)', clause)
        if not margin:
            margin = re.fullmatch(r'(增加|减少)(?:画面)?留白' + _NUMBER + r'(?:%|％|个百分点)', clause)
        if margin:
            action, number = margin.groups()
            n = float(number) / 100
            put('margin', plan['margin'] + n if action in ('增加', '加') else plan['margin'] - n if action in ('减少', '减') else n)
            notes.append('留白与画幅设置作用于整个项目。'); continue
        soft = re.fullmatch(r'(?:反光|光源|灯光)?柔和度(?:设为|设成|改为|调整到|到)?' + _NUMBER + r'(?:倍)?', clause)
        if soft:
            put('light_softness', float(soft.group(1))); continue
        direction=re.fullmatch(r'(主光|补光|轮廓光)(?:的)?(方位角|水平角|俯仰角)(设为|设成|改为|调整到|到|增加|提高|调高|加|减少|降低|调低|减)?'+_NUMBER+r'(?:°|度)?',clause)
        if direction:
            label,axis,action,number=direction.groups()
            key={'主光':'key','补光':'fill','轮廓光':'rim'}[label]+('_elevation' if axis=='俯仰角' else '_azimuth')
            before=plan.get('lighting',{}).get(key)
            if action in ('增加','提高','调高','加','减少','降低','调低','减') and before is None:
                before=context.get('light_direction_reference',{}).get(key)
                if before is None:
                    raise InstructionOutcome('needs_clarification','当前光源方向尚未测得，无法核验相对角度变化。请先生成预览，或直接指定光源方位角／俯仰角。',protections=protections)
            value=relative(before,action,number)
            lo,hi=LIGHT_DIRECTION_LIMITS[key]
            if not lo<=value<=hi:
                raise InstructionOutcome('needs_clarification',label+axis+'超出支持范围，请指定 '+str(lo)+' 至 '+str(hi)+' 度。',protections=protections)
            put('lighting.'+key,value);continue
        light = re.fullmatch(r'(主光|补光|轮廓光|环境光)(?:强度|倍率|亮度)?(设为|设成|改为|调整到|到|增加|提高|调高|增强|加|减少|降低|调低|减弱|减)?' + _NUMBER + r'(倍|%|％)?', clause)
        if light:
            label, action, number, unit = light.groups()
            key = {'主光': 'key_power', '补光': 'fill_power', '轮廓光': 'rim_power', '环境光': 'environment'}[label]
            put('lighting.' + key, relative(plan.get('lighting', {}).get(key, LIGHTING_DEFAULTS[key]), action, number, unit in ('%', '％'))); continue
        angle = re.fullmatch(r'(?:镜头|相机)?(水平角|方位角|俯仰角|焦距)(?:设为|设成|改为|调整到|到)?' + _NUMBER + r'(?:°|度|mm|毫米)?', clause)
        if angle:
            label, number = angle.groups()
            camera({{'水平角': 'azimuth', '方位角': 'azimuth', '俯仰角': 'elevation', '焦距': 'focal_length'}[label]: float(number)}); continue
        view = re.fullmatch(r'(?:(?:镜头|视角)(?:换成|改成|设为)?|(?:换成|改成|设为))?(俯拍|正面|侧面|右侧面|左侧面|背面|三分之四(?:视角)?)', clause)
        if view:
            values = {'俯拍': {'elevation': 80}, '正面': {'azimuth': 0, 'elevation': 8}, '侧面': {'azimuth': 90, 'elevation': 12}, '右侧面': {'azimuth': 90, 'elevation': 12}, '左侧面': {'azimuth': -90, 'elevation': 12}, '背面': {'azimuth': 180, 'elevation': 12}, '三分之四': {'azimuth': 35, 'elevation': 18}, '三分之四视角': {'azimuth': 35, 'elevation': 18}}
            camera(values[view.group(1)], full_only=view.group(1) == '正面'); continue
        unknown.append(raw)
    if unknown:
        raise InstructionOutcome('unsupported', '内置规则无法完整处理“' + '；'.join(unknown)[:240] + '”，此次未修改任何参数。可描述明确的曝光 EV、留白百分比、光源强度、光源方位角／俯仰角或镜头角度，或配置可用 AI。', protections=protections)
    enforce_protections(result, protections, active)
    if not result:
        raise InstructionOutcome('no_change', '这次只有保留要求，没有需要执行的参数调整。', protections=protections)
    return result, list(dict.fromkeys(notes)), protections


def describe_changes(changes):
    labels = {'style': '背景风格', 'aspect': '画幅', 'margin': '留白', 'exposure': '曝光', 'light_softness': '反光柔和度', 'lighting': '灯光', 'shots': '镜头参数', 'rotation': '产品方向', 'pose': '部件姿态', 'texture_scale': '纹理尺度', 'texture_rotation': '纹理方向', 'material_overrides': '全局材质', 'object_material_overrides': '所选部件材质'}
    names = list(dict.fromkeys(labels.get(change['path'].split('.')[0], change['path']) for change in changes))
    return '已调整' + '、'.join(names) + '；画面效果以新预览为准。' if names else '参数没有变化。'

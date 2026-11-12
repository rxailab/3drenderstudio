"""Look development for the first image: real finishes for placeholder materials.

Many CAD exports arrive without usable shading: an OBJ whose MTL only carries a
flat colour, or meshes with no material at all. Rendered as-is they look like a
matte plastic toy. Their *names* usually say what they are ("亮光金属", "铝",
"不锈钢台面", "2.5寸轮子"), so this module turns those names into physically
plausible finishes for the first render.

Rules, in order of authority:
  * a material that already carries authored shading (textures, metalness,
    transmission, or any glTF material) is never touched;
  * a part name overrides its material name when it is more specific
    (a "不锈钢台面" sharing "亮光金属" with screws becomes brushed stainless);
  * the original colour is always kept, only the finish changes;
  * a part left on the importer's grey default takes the colour and finish of
    its twin (左拉手 / 右拉手, 加强筋_1 / 加强筋_2) when every twin agrees;
  * every decision is recorded with its evidence so the user can see and undo it.

Nothing here looks at pixels or calls a model. An art-director agent can refine
the result later; this is the offline baseline.
"""
import re

LOOKDEV_VERSION = 1

# finish -> worker preset plus explicit shading values (colour is added per material)
FINISHES = {
    'polished_metal': {'preset': 'polished_metal', 'roughness': .1, 'metallic': 1, 'label': '抛光金属'},
    'bright_metal': {'preset': 'polished_metal', 'roughness': .2, 'metallic': 1, 'label': '亮面金属'},
    'brushed_metal': {'preset': 'brushed_metal', 'roughness': .3, 'metallic': 1, 'label': '拉丝金属'},
    'gloss_paint': {'preset': 'painted_metal', 'roughness': .24, 'metallic': 0, 'coat': .6, 'label': '亮面喷涂'},
    'gloss_plastic': {'preset': 'glossy_plastic', 'roughness': .22, 'metallic': 0, 'coat': .3, 'label': '亮面塑料'},
    'satin_plastic': {'preset': 'matte_plastic', 'roughness': .42, 'metallic': 0, 'label': '缎面塑料'},
    'rubber': {'preset': 'rubber', 'roughness': .78, 'metallic': 0, 'label': '橡胶'},
    'glass': {'preset': 'glass', 'roughness': .04, 'metallic': 0, 'transmission': 1, 'label': '玻璃'},
    'wood': {'preset': 'wood', 'roughness': .42, 'metallic': 0, 'label': '木材'},
    'leather': {'preset': 'leather', 'roughness': .5, 'metallic': 0, 'label': '皮革'},
    'fabric': {'preset': 'fabric', 'roughness': .85, 'metallic': 0, 'label': '织物'},
    'ceramic': {'preset': 'ceramic', 'roughness': .16, 'metallic': 0, 'coat': .3, 'label': '陶瓷'},
    # Nothing in the names says what it is: a quiet sheen instead of flat clay.
    'satin': {'preset': 'matte_plastic', 'roughness': .46, 'metallic': 0, 'label': '中性缎面'},
}

# (finish, keywords). Earlier rows win; ASCII keywords must match whole words.
MATERIAL_RULES = [
    ('polished_metal', ('镜面不锈钢', '抛光不锈钢', '镜面', '抛光', '镀铬', '电镀', 'chrome', 'mirror', 'polished')),
    ('brushed_metal', ('不锈钢', '拉丝', 'stainless', 'inox', 'brushed', '铝', 'aluminium', 'aluminum', 'alu')),
    ('bright_metal', ('亮光金属', '亮面金属', '镀锌', '锌', 'zinc', 'galvanized')),
    ('gloss_paint', ('喷漆', '烤漆', '喷塑', '喷涂', '油漆', 'painted', 'paint', 'lacquer', 'powder coat', 'powdercoat')),
    ('rubber', ('橡胶', '硅胶', '轮胎', 'rubber', 'silicone', 'tire', 'tyre')),
    ('glass', ('玻璃', '亚克力', 'glass', 'acrylic')),
    ('satin_plastic', ('哑光塑料', '磨砂塑料', '哑光', '磨砂', 'matte plastic', 'matte')),
    ('gloss_plastic', ('塑料', '塑胶', '尼龙', 'plastic', 'abs', 'pvc', 'nylon', 'polycarbonate')),
    ('wood', ('木纹', '木材', '实木', '木', 'wood', 'oak', 'walnut')),
    ('leather', ('皮革', '真皮', 'leather')),
    ('fabric', ('织物', '布料', '帆布', '布', 'fabric', 'canvas', 'cloth', 'textile')),
    ('ceramic', ('陶瓷', '瓷', 'ceramic', 'porcelain')),
    ('bright_metal', ('金属', '钢', '铁', '铜', 'metal', 'steel', 'iron', 'copper', 'brass')),
]

# Part names that settle the finish whatever placeholder material they carry.
PART_RULES = [
    ('brushed_metal', ('不锈钢', 'stainless', 'inox', '铝条', '铝板', '铝型材')),
    ('rubber', ('轮子', '轮胎', '脚轮', '胶轮', '橡胶', 'wheel', 'caster', 'tire', 'tyre')),
    ('bright_metal', ('螺丝', '螺钉', '螺栓', '螺母', '拉帽', '铆钉', '垫片', '铰链', '合页', '滑轨', '内轨', '外轨', '导轨',
                      '锁舌', '锁芯', '转舌锁', '插锁', '内锁', '钥匙', '插销', '弹簧', '轴承', '支架', '刹车片',
                      'screw', 'bolt', 'nut', 'rivet', 'washer', 'hinge', 'rail', 'lock', 'key', 'latch', 'spring',
                      'bearing', 'bracket')),
]

# Wheel hardware is metal even though its name contains the wheel keyword.
PART_EXCEPTIONS = {'rubber': ('支架', '刹车', '轴', 'bracket', 'brake', 'axle', 'fork')}
_COLOUR_ONLY = re.compile(r'^(?:default|material|mat|none|黑色|白色|灰色|红色|蓝色|绿色|黄色|橙色|black|white|gr[ae]y|red|blue|green|yellow|orange)?[\s._\-\d]*$')


def _match(name, keywords):
    text = name.casefold()
    for keyword in keywords:
        if keyword.isascii():
            if re.search(r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])', text):
                return keyword
        elif keyword in text:
            return keyword
    return None


def _rule(name, rules):
    for finish, keywords in rules:
        hit = _match(name, keywords)
        if hit:
            if any(_match(name, (word,)) for word in PART_EXCEPTIONS.get(finish, ()) if rules is PART_RULES):
                continue
            return finish, hit
    return None, None


def _hex(color):
    """Linear scene colour to the sRGB hex string the plan stores."""
    values = []
    for channel in list(color or [])[:3]:
        channel = min(1., max(0., float(channel)))
        encoded = channel * 12.92 if channel <= .0031308 else 1.055 * channel ** (1 / 2.4) - .055
        values.append(round(min(1., max(0., encoded)) * 255))
    while len(values) < 3:
        values.append(140)
    return '#%02X%02X%02X' % tuple(values)


def _saturation(color):
    channels = [min(1., max(0., float(value))) for value in list(color or [.5, .5, .5])[:3]]
    high, low = max(channels), min(channels)
    return 0 if high <= 1e-6 else (high - low) / high


_UNASSIGNED = re.compile(r'^(?:default|defaultmaterial|default material|material|mat|none|无材质|默认材质)?[\s._\-\d]*$')
_MIRROR = ('左', '右', 'left', 'right')


def _family(name):
    """Part name without copy numbering or side, so twins share one key."""
    text = re.sub(r'(?:[._\s]+\d+|[(（]\d+[)）])+$', '', name.casefold())
    for word in _MIRROR:
        text = text.replace(word, '\x00')
    return text


def is_placeholder(material, model_format):
    """True when a material carries no authored shading worth preserving."""
    if material.get('textures'):
        return False
    if material.get('source') in ('fallback', 'default'):
        return True
    if model_format != 'obj':
        # glTF materials are physically based by specification: trust them.
        return False
    return (material.get('metallic', 0) or 0) < .05 and (material.get('transmission', 0) or 0) < .05


def _entry(finish, color, reason):
    values = {key: value for key, value in FINISHES[finish].items() if key != 'label'}
    values.update(color=_hex(color), source='lookdev', preserve_original=False, lookdev_finish=finish, lookdev_reason=reason)
    return values


def propose(analysis, model_path=''):
    """Finishes for placeholder materials, with the evidence for each decision.

    Returns {'material_overrides': [...], 'object_material_overrides': [...],
    'decisions': [...]} without touching any plan.
    """
    analysis = analysis or {}
    model_format = 'obj' if str(model_path).lower().endswith('.obj') else 'glb' if str(model_path).lower().endswith('.glb') else ''
    materials = {material['name']: material for material in analysis.get('materials', [])
                 if isinstance(material, dict) and isinstance(material.get('name'), str)}
    users = {}
    for obj in analysis.get('objects', []):
        if not isinstance(obj, dict) or obj.get('type', 'MESH') != 'MESH' or not isinstance(obj.get('name'), str):
            continue
        for name in obj.get('materials') or []:
            if isinstance(name, str):
                users.setdefault(name, []).append(obj)
    result = {'material_overrides': [], 'object_material_overrides': [], 'decisions': []}
    decided, finishes = set(), {}
    for name, material in materials.items():
        if not is_placeholder(material, model_format):
            continue
        color = material.get('color') or [.55, .58, .62, 1]
        finish, hit = (None, None) if _COLOUR_ONLY.match(name.casefold()) else _rule(name, MATERIAL_RULES)
        if finish == 'bright_metal' and hit in ('金属', '钢', '铁', 'metal', 'steel', 'iron') and _saturation(color) > .45:
            # A strongly coloured "metal" is a painted panel, not bare metal.
            finish = 'gloss_paint'
        reason = '材质名「%s」含「%s」' % (name, hit) if finish else ''
        parts = users.get(name, [])
        part_finishes = {}
        for obj in parts:
            part_finish, part_hit = _rule(obj['name'], PART_RULES)
            if part_finish:
                part_finishes[obj['name']] = (part_finish, part_hit)
        if finish is None and parts and len(part_finishes) == len(parts) and len({value[0] for value in part_finishes.values()}) == 1:
            # Every part using this nameless material agrees: decide the whole slot.
            finish = next(iter(part_finishes.values()))[0]
            reason = '使用它的 %d 个部件名称一致指向%s' % (len(parts), FINISHES[finish]['label'])
        if finish:
            result['material_overrides'].append({'name': name, **_entry(finish, color, reason)})
            result['decisions'].append({'scope': 'material', 'material': name, 'finish': finish,
                                        'label': FINISHES[finish]['label'], 'reason': reason, 'parts': len(parts)})
        for obj in parts:
            part_finish, part_hit = part_finishes.get(obj['name'], (None, None))
            if not part_finish or part_finish == finish:
                continue
            reason_part = '部件名「%s」含「%s」' % (obj['name'], part_hit)
            result['object_material_overrides'].append({'object_name': obj['name'], 'material_name': name,
                                                        **_entry(part_finish, color, reason_part)})
            result['decisions'].append({'scope': 'part', 'object': obj['name'], 'material': name, 'finish': part_finish,
                                        'label': FINISHES[part_finish]['label'], 'reason': reason_part})
            decided.add((obj['name'], name))
        if finish:
            decided.update((obj['name'], name) for obj in parts)
            finishes[name] = finish
    # Twins: a part the importer left on its grey default copies the look of
    # same-named parts that do carry a material, when all of them agree.
    families = {}
    for obj in analysis.get('objects', []):
        if isinstance(obj, dict) and isinstance(obj.get('name'), str) and obj.get('type', 'MESH') == 'MESH':
            families.setdefault(_family(obj['name']), []).append(obj)
    for name, material in materials.items():
        if not _UNASSIGNED.match(name.casefold()) or not is_placeholder(material, model_format):
            continue
        for obj in users.get(name, []):
            if (obj['name'], name) in decided or len(obj.get('materials') or []) != 1:
                continue
            family = _family(obj['name'])
            if len(family) < 2:
                continue
            twins = {tuple(other.get('materials') or []) for other in families.get(family, []) if other is not obj}
            twins = {value for value in twins if len(value) == 1 and value[0] in materials
                     and not _UNASSIGNED.match(value[0].casefold())}
            if len(twins) != 1:
                continue
            source = next(iter(twins))[0]
            twin_finish = finishes.get(source) or 'satin'
            reason_part = '与同名部件一致，沿用材质「%s」' % source
            result['object_material_overrides'].append({'object_name': obj['name'], 'material_name': name,
                                                        **_entry(twin_finish, materials[source].get('color') or [.55, .58, .62, 1], reason_part)})
            result['decisions'].append({'scope': 'part', 'object': obj['name'], 'material': name, 'finish': twin_finish,
                                        'label': FINISHES[twin_finish]['label'], 'reason': reason_part, 'twin_of': source})
            decided.add((obj['name'], name))
    # Whatever is still undecided keeps its colour and loses the clay look.
    for name, material in materials.items():
        if name in finishes or not is_placeholder(material, model_format) or not users.get(name):
            continue
        if (material.get('roughness') if material.get('roughness') is not None else 1) < .6:
            continue
        reason = '名称未说明材质，保留颜色并使用中性缎面'
        result['material_overrides'].append({'name': name, **_entry('satin', material.get('color') or [.55, .58, .62, 1], reason)})
        result['decisions'].append({'scope': 'material', 'material': name, 'finish': 'satin', 'label': FINISHES['satin']['label'],
                                    'reason': reason, 'parts': len(users.get(name, []))})
    return result


def apply(plan, analysis, model_path=''):
    """Add proposed finishes to a plan that has no material decisions yet."""
    if plan.get('material_overrides') or plan.get('object_material_overrides'):
        return plan, None
    proposal = propose(analysis, model_path)
    if not proposal['decisions']:
        return plan, None
    plan['material_overrides'] = proposal['material_overrides']
    plan['object_material_overrides'] = proposal['object_material_overrides']
    summary = {}
    for decision in proposal['decisions']:
        summary[decision['label']] = summary.get(decision['label'], 0) + 1
    plan['lookdev'] = {'version': LOOKDEV_VERSION, 'source': 'names',
                       'summary': '、'.join('%s ×%d' % item for item in summary.items()),
                       'decisions': proposal['decisions'][:200]}
    return plan, plan['lookdev']

"""The text and icon layer of a shot: editable vector annotations over the picture.

A layer is plain data in picture coordinates (0..1 across the frame, sizes as
shares of the picture's shorter side), so it stays sharp at any resolution and
every item remains editable. It is stored per shot beside the plan, not in it:
writing a headline never makes a render stale.

    validate()      the data contract, for compositions and for the overlay
                    layers saved before them (v23/v24), which still load
    content_for()   what the image says: a vision model when one is configured,
                    name rules and the user's own lines otherwise; a number is
                    only shown when the user gave it
    probe()         what each part of the picture shows, from the saved scene
    suggest()       content, icons (layer_icons) and a composition
                    (layer_compose) as one proposal the user then edits
    recompose()     the same words in another composition, without a model

The browser draws and exports the layer (static/layer.js); this module never
rasterises text.
"""
import colorsys
import copy
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image

from studio_store import DATA, LOCK, atomic_json, get, put

ROOT = Path(__file__).resolve().parent
PROBE_SCRIPT = ROOT / 'blender' / 'layer_probe.py'
PROBE_TIMEOUT = 120
GRID = 96
ICONS = ('check', 'star', 'shield', 'lock', 'drawer', 'wheel', 'layers', 'ruler', 'drop', 'leaf', 'bolt', 'sparkle', 'heart',
         'handle', 'door', 'box', 'truck', 'clock', 'weight', 'tool', 'sun', 'wave', 'flame', 'bulb', 'screen', 'power', 'bag',
         'eye', 'award', 'tag', 'info', 'size', 'gear', 'dot', 'arrow', 'plus', 'home', 'cup')
TYPES = ('text', 'icon', 'callout', 'figure', 'panel', 'rule', 'chips')
THEMES = ('clean', 'bold', 'card', 'dark', 'tag')          # how a layer is dressed; none means the plain drawing
ROLES = ('headline', 'subline', 'kicker', 'body', 'caption')
CALLOUT_STYLES = ('card', 'row', 'line')        # card: the v23/v24 drawing; row and line belong to compositions
FADES = ('up', 'down', 'left', 'right')
MAX_ITEMS = 40
MAX_HINT = 400
DARK = '#141414'
BASIS_KEYS = ('azimuth', 'elevation', 'focal_length', 'projection', 'ortho_scale', 'camera_distance', 'target_offset')


# ------------------------------------------------------------------ contract

def _number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('图文图层的%s无效' % label)
    return float(value)


def _text(value, limit, label, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError('图文图层的%s无效' % label)
    if any(ord(char) < 32 and char != '\n' for char in value):
        raise ValueError('图文图层的%s含有不支持的字符' % label)
    return value


def _color(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('图文图层的%s应为 #RRGGBB' % label)
    return value.lower()


def _plate(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'color', 'opacity'}:
        raise ValueError('图文图层的底板无效')
    return {'color': _color(value.get('color'), '底板颜色'), 'opacity': _number(value.get('opacity', .85), 0, 1, '底板透明度')}


def _shape(kind, item, row):
    """The composition items: a figure, a panel, a hairline or a row of size chips."""
    if kind == 'panel':
        row.update(w=_number(item.get('w'), 0, 1.5, '宽度'), h=_number(item.get('h'), 0, 1.5, '高度'),
                   opacity=_number(item.get('opacity', 1), 0, 1, '透明度'), radius=_number(item.get('radius', 0), 0, .5, '圆角'))
        if item.get('fade') is not None:
            if item['fade'] not in FADES:
                raise ValueError('图文图层的渐变方向无效')
            row['fade'] = item['fade']
    elif kind == 'rule':
        row.update(x2=_number(item.get('x2'), -.5, 1.5, '位置'), y2=_number(item.get('y2'), -.5, 1.5, '位置'),
                   width=_number(item.get('width', .0016), .0004, .05, '线宽'), opacity=_number(item.get('opacity', 1), 0, 1, '透明度'))
    elif kind == 'figure':
        row.update(w=_number(item.get('w'), .02, 1.5, '宽度'), value=_text(item.get('value'), 24, '数值'), unit=_text(item.get('unit', ''), 12, '单位', empty=True),
                   label=_text(item.get('label', ''), 40, '说明', empty=True))
        if item.get('align', 'left') not in ('left', 'center', 'right'):
            raise ValueError('图文图层的对齐方式无效')
        row['align'] = item.get('align', 'left')
    else:
        values = item.get('values')
        if not isinstance(values, list) or not 1 <= len(values) <= 16:
            raise ValueError('图文图层的规格数量无效')
        row.update(w=_number(item.get('w'), .02, 1.5, '宽度'), values=[_text(value, 10, '规格') for value in values],
                   unit=_text(item.get('unit', ''), 12, '单位', empty=True), label=_text(item.get('label', ''), 40, '说明', empty=True),
                   fill=_color(item.get('fill', '#ffffff'), '底色'))
    return row


def _canvas(value):
    if not isinstance(value, dict) or set(value) - {'color', 'photo'}:
        raise ValueError('图文图层的画布无效')
    canvas = {'color': _color(value.get('color'), '画布颜色')}
    photo = value.get('photo')
    if photo is not None:
        if not isinstance(photo, dict) or set(photo) - {'x', 'y', 'w', 'h', 'crop', 'feather', 'radius', 'fill'}:
            raise ValueError('图文图层的图片位置无效')
        crop = photo.get('crop')
        if not isinstance(crop, list) or len(crop) != 4:
            raise ValueError('图文图层的图片裁切无效')
        crop = [_number(number, -5, 6, '图片裁切') for number in crop]
        if crop[2] - crop[0] < .01 or crop[3] - crop[1] < .01:
            raise ValueError('图文图层的图片裁切无效')
        frame = {key: _number(photo.get(key), -.5, 1.5, '图片位置') for key in ('x', 'y', 'w', 'h')}
        if frame['w'] <= .01 or frame['h'] <= .01:
            raise ValueError('图文图层的图片位置无效')
        canvas['photo'] = {**frame, 'crop': crop, 'feather': bool(photo.get('feather', False)),
                           'radius': _number(photo.get('radius', 0), 0, .5, '图片圆角')}
        if photo.get('fill') is not None:
            canvas['photo']['fill'] = _color(photo['fill'], '图片底色')
    return canvas


def _content(value, glyphs):
    """What a composition says, kept so the same words can be laid out again in another composition."""
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 20000:
        raise ValueError('图文图层的内容无效')
    clean = {}
    for key, limit in (('kicker', 30), ('headline', 60), ('subline', 80)):
        clean[key] = _text(value.get(key, ''), limit, '内容', empty=True)
    if value.get('free_area') in ('left', 'right', 'top', 'bottom', 'none'):
        clean['free_area'] = value['free_area']
    if value.get('language') is not None:
        if value['language'] not in LANGUAGES:
            raise ValueError('图文图层的文字语言无效')
        clean['language'] = value['language']
    box = value.get('product_box')
    if box is not None:
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError('图文图层的产品位置无效')
        box = [_number(number, 0, 1, '产品位置') for number in box]
        if box[2] - box[0] < .05 or box[3] - box[1] < .05:
            raise ValueError('图文图层的产品位置无效')
        clean['product_box'] = box
    def entry(row, fields, label):
        if not isinstance(row, dict):
            raise ValueError('图文图层的%s无效' % label)
        out = {}
        for key, limit in fields:
            out[key] = _text(row.get(key, ''), limit, label, empty=True)
        if row.get('glyph') is not None:
            if row['glyph'] not in glyphs:
                raise ValueError('图文图层引用了不存在的图标')
            out['glyph'] = row['glyph']
        return out
    points = value.get('points', [])
    if not isinstance(points, list) or len(points) > 6:
        raise ValueError('图文图层的要点无效')
    clean['points'] = []
    for row in points:
        point = entry(row, (('title', 40), ('detail', 80), ('icon', 60)), '要点')
        parts = row.get('parts', [])
        if not isinstance(parts, list) or len(parts) > 40 or any(not isinstance(name, str) or len(name) > 300 for name in parts):
            raise ValueError('图文图层的要点部件无效')
        point['parts'] = list(parts)
        anchor = row.get('anchor')
        if anchor is not None:
            if not isinstance(anchor, list) or len(anchor) != 2:
                raise ValueError('图文图层的要点位置无效')
            anchor = [_number(anchor[0], 0, 1, '要点位置'), _number(anchor[1], 0, 1, '要点位置')]
        point['anchor'] = anchor
        clean['points'].append(point)
    for key, label in (('figures', '数据'), ('specs', '规格')):
        rows = value.get(key, [])
        if not isinstance(rows, list) or len(rows) > 6:
            raise ValueError('图文图层的%s无效' % label)
        clean[key] = []
        for row in rows:
            item = entry(row, (('value', 24), ('unit', 12), ('label', 40), ('icon', 60)), label)
            values = row.get('values') or []
            if not isinstance(values, list) or len(values) > 16 or any(not isinstance(number, str) or len(number) > 10 for number in values):
                raise ValueError('图文图层的%s列表无效' % label)
            if values:
                item['values'] = list(values)
            clean[key].append(item)
    return clean


def validate(value):
    """A clean copy of a layer, or ValueError. Only known fields survive."""
    if not isinstance(value, dict) or value.get('version', 1) != 1:
        raise ValueError('图文图层数据无效')
    glyphs = {}
    if value.get('glyphs') is not None:
        import layer_icons
        stored = value['glyphs']
        if not isinstance(stored, dict) or len(stored) > layer_icons.MAX_GLYPHS:
            raise ValueError('图文图层的图标数量无效')
        for key, glyph in stored.items():
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', key):
                raise ValueError('图文图层的图标标识无效')
            glyphs[key] = layer_icons.validate_glyph(glyph)
    items = value.get('items')
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError('图文图层最多 %d 个元素' % MAX_ITEMS)
    clean = []; seen = set()
    for item in items:
        if not isinstance(item, dict) or item.get('type') not in TYPES:
            raise ValueError('图文图层含有未知的元素类型')
        ident = item.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', ident) or ident in seen:
            raise ValueError('图文图层的元素标识无效')
        seen.add(ident)
        kind = item['type']
        row = {'id': ident, 'type': kind, 'x': _number(item.get('x'), -.5, 1.5, '位置'), 'y': _number(item.get('y'), -.5, 1.5, '位置'),
               'size': _number(item.get('size'), .008, .5, '大小'), 'color': _color(item.get('color', DARK), '颜色')}
        if item.get('glyph') is not None:
            if kind not in ('icon', 'callout', 'figure') or not isinstance(item['glyph'], str) or item['glyph'] not in glyphs:
                raise ValueError('图文图层引用了不存在的图标')
            row['glyph'] = item['glyph']
        if item.get('soft') is not None:
            row['soft'] = _color(item['soft'], '次要颜色')
        if item.get('slot') is not None:
            if not isinstance(item['slot'], str) or not re.fullmatch(r'(kicker|headline|subline|(points|figures|specs)\.[0-5](\.(title|detail))?)', item['slot']):
                raise ValueError('图文图层的内容位置无效')
            row['slot'] = item['slot']
        if kind in ('panel', 'rule', 'chips', 'figure'):
            clean.append(_shape(kind, item, row)); continue
        if kind == 'icon':
            if item.get('icon') not in ICONS:
                raise ValueError('图文图层含有未知的图标')
            row['icon'] = item['icon']
        else:
            row['w'] = _number(item.get('w'), .02, 1.5, '宽度')
            row['plate'] = _plate(item.get('plate'))
        if kind == 'text':
            row['text'] = _text(item.get('text'), 400, '文字')
            if item.get('weight', 400) not in (400, 600, 700) or item.get('align', 'left') not in ('left', 'center', 'right'):
                raise ValueError('图文图层的文字样式无效')
            row['weight'] = item.get('weight', 400); row['align'] = item.get('align', 'left')
            if item.get('role', 'body') not in ROLES:
                raise ValueError('图文图层的文字角色无效')
            if item.get('role', 'body') != 'body':
                row['role'] = item['role']
            if item.get('mark'):
                row['mark'] = _text(item['mark'], 60, '强调词')
            if item.get('leading') is not None:
                row['leading'] = _number(item['leading'], .8, 2.5, '行距')
        if kind == 'callout':
            row['title'] = _text(item.get('title'), 80, '标注标题')
            row['detail'] = _text(item.get('detail', ''), 160, '标注说明', empty=True)
            if item.get('icon') is not None and item['icon'] not in ICONS:
                raise ValueError('图文图层含有未知的图标')
            row['icon'] = item.get('icon')
            anchor = item.get('anchor')
            if anchor is not None:
                if not isinstance(anchor, list) or len(anchor) != 2:
                    raise ValueError('图文图层的标注指向无效')
                anchor = [_number(anchor[0], 0, 1, '标注指向'), _number(anchor[1], 0, 1, '标注指向')]
            row['anchor'] = anchor
            if item.get('style', 'card') not in CALLOUT_STYLES:
                raise ValueError('图文图层的标注样式无效')
            if item.get('style', 'card') != 'card':
                row['style'] = item['style']
            if item.get('elbow') is not None:
                row['elbow'] = _number(item['elbow'], -.5, 1.5, '引线拐点')
        clean.append(row)
    layer = {'version': 1, 'items': clean}
    if glyphs:
        layer['glyphs'] = glyphs
    if value.get('layout') is not None:
        import layer_compose
        if value['layout'] not in layer_compose.LAYOUTS:
            raise ValueError('图文图层的版式无效')
        layer['layout'] = value['layout']
    if value.get('canvas') is not None:
        layer['canvas'] = _canvas(value['canvas'])
    if value.get('content') is not None:
        layer['content'] = _content(value['content'], glyphs)
    if value.get('theme') is not None:
        if value['theme'] not in THEMES:
            raise ValueError('图文图层的风格无效')
        layer['theme'] = value['theme']
    if value.get('accent') is not None:
        layer['accent'] = _color(value['accent'], '主色')
    basis = value.get('basis')
    if basis is not None:
        if not isinstance(basis, dict) or set(basis) - set(BASIS_KEYS) - {'aspect'} or len(json.dumps(basis)) > 2000:
            raise ValueError('图文图层的镜头依据无效')
        layer['basis'] = copy.deepcopy(basis)
    if value.get('source') in ('rules', 'ai', 'manual'):
        layer['source'] = value['source']
    return layer


def basis(plan, shot):
    """What a suggested layout depends on: a changed camera means the callouts may point astray."""
    return {**{key: copy.deepcopy(shot[key]) for key in BASIS_KEYS if key in shot}, 'aspect': plan.get('aspect', '1:1')}


def _shot(project, shot_id):
    shot = next((item for item in project['plan']['shots'] if item['id'] == shot_id), None)
    if shot is None:
        raise KeyError(shot_id)
    return shot


def layers(project):
    stored = project.get('layers')
    return stored if isinstance(stored, dict) else {}


def save(project_id, shot_id, body):
    if not isinstance(body, dict):
        raise ValueError('图文图层请求格式无效')
    layer = validate(body.get('layer'))
    with LOCK:
        project = get('project', project_id); _shot(project, shot_id)
        stored = layers(project); previous = stored.get(shot_id) or {}
        expected = body.get('expected_revision')
        if expected is not None and expected != previous.get('revision', 0):
            raise LayerConflict('图文图层已在别处修改，请重新载入后再编辑。')
        layer.update(revision=previous.get('revision', 0) + 1, updated_at=time.time())
        project['layers'] = {**stored, shot_id: layer}; put('project', project)
        return layer


def remove(project_id, shot_id):
    with LOCK:
        project = get('project', project_id)
        stored = dict(layers(project))
        if stored.pop(shot_id, None) is not None:
            project['layers'] = stored; put('project', project)
        return {'removed': True, 'shot_id': shot_id}


class LayerConflict(ValueError):
    pass


# ------------------------------------------------------------------ what to say

# Name cues: what a part is called -> a neutral point about it. A point only names
# what is there; it never claims a number, a grade or a performance.
CUES = (
    (('抽屉', 'drawer'), '抽屉收纳', '物品分层放置', 'drawer'),
    (('脚轮', '万向轮', '滚轮', '轮子', 'caster', 'wheel'), '带轮移动', '可推到手边使用', 'wheel'),
    (('锁芯', '钥匙', '锁', 'lock', 'key'), '带锁', '可上锁存放', 'lock'),
    (('台面', '桌面', 'worktop', 'countertop', 'tabletop'), ('{material}台面', '工作台面'), '可放置常用物品', 'layers'),
    (('拉手', '把手', '提手', '手柄', 'handle'), ('{material}拉手', '拉手'), '便于开合与提拿', 'handle'),
    (('柜门', 'door'), '柜门储物', '大件收在门内', 'door'),
    (('泵头', '按压', '喷头', 'pump', 'nozzle'), '按压泵头', '单手取用', 'drop'),
    (('瓶盖', '盖子', 'cap', 'lid'), '带盖', '', 'check'),
    (('屏幕', '显示屏', 'screen', 'display'), '显示屏', '', 'screen'),
    (('按钮', '按键', '旋钮', 'button', 'knob', 'switch'), '按键操作', '', 'power'),
    (('灯罩', '灯头', 'led', 'lamp'), '灯光', '', 'bulb'),
    (('口袋', '内袋', 'pocket'), '口袋', '随身小物分开放', 'bag'),
    (('肩带', '背带', '提带', 'strap'), ('{material}提带', '提带'), '', 'bag'),
    (('搁板', '层板', '隔板', 'shelf'), '层板分隔', '', 'layers'),
    (('脚垫', '垫脚', 'foot pad', 'feet'), '脚垫', '', 'dot'),
    # No point of their own: they let a point the user typed find the part it names.
    (('标签', 'label', 'logo'), '', '', 'tag'),
    (('瓶身', '机身', '壳体', '外壳', 'body', 'shell', 'housing'), '', '', 'check'),
    (('柜体', '箱体', '框架', 'frame', 'cabinet'), '', '', 'box'),
)
MATERIAL_WORDS = ((('不锈钢', 'stainless'), '不锈钢'), (('玻璃', 'glass'), '玻璃'), (('实木', '木', 'wood', 'oak'), '木质'),
                  (('真皮', '皮革', 'leather'), '皮革'), (('铝', 'alumin'), '铝制'), (('帆布', 'canvas'), '帆布'))
# The language the words of a layer are written in. English unless the user picks Chinese.
LANGUAGES = ('en', 'zh')
DEFAULT_LANGUAGE = 'en'
CJK = re.compile(r'[\u3400-\u9fff]')
# The same points in English: (title, detail) for each Chinese title above, and the material words.
CUES_EN = {'抽屉收纳': ('Drawer storage', 'Tools sorted by drawer'), '带轮移动': ('On casters', 'Rolls to where you work'),
           '带锁': ('Lockable', 'Keeps tools secure'), ('{material}台面', '工作台面'): ('{material} worktop', 'Worktop'),
           '可放置常用物品': 'Room for everyday tools', ('{material}拉手', '拉手'): ('{material} handles', 'Handles'),
           '便于开合与提拿': 'Easy to open and carry', '柜门储物': ('Cabinet storage', 'Large items behind the doors'),
           '按压泵头': ('Pump dispenser', 'One-handed use'), '带盖': ('With lid', ''), '显示屏': ('Display', ''), '按键操作': ('Push-button control', ''),
           '灯光': ('Built-in light', ''), '口袋': ('Pockets', 'Small items kept apart'), ('{material}提带', '提带'): ('{material} strap', 'Carry strap'),
           '层板分隔': ('Shelf dividers', ''), '脚垫': ('Feet pads', '')}
MATERIAL_EN = {'不锈钢': 'Stainless steel', '玻璃': 'Glass', '木质': 'Wooden', '皮革': 'Leather', '铝制': 'Aluminium', '帆布': 'Canvas'}
DIMENSIONS = {'zh': '产品尺寸', 'en': 'Dimensions'}
OUTLINE = {'zh': '外形尺寸', 'en': 'Dimensions'}
def _clip(text, limit):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _material_word(names):
    text = ' '.join(names).casefold()
    return next((word for cues, word in MATERIAL_WORDS if any(cue in text for cue in cues)), '')


def _names(project):
    analysis = project.get('analysis') or {}
    return [str(item.get('name', '')) for item in analysis.get('objects', []) if isinstance(item, dict) and item.get('name')]


def _hint_points(hint):
    """The user's own points, one per line; "title: detail" splits at the colon."""
    points = []
    for line in re.split(r'[\n;；。]+', hint or ''):
        line = line.strip(' \t-·•*、,，')
        if not line:
            continue
        title, _, detail = line.replace('：', ':').partition(':')
        points.append({'title': _clip(title, 16), 'detail': _clip(detail, 28), 'icon': None, 'parts': []})
    return points[:5]


def copy_rules(project, hint='', visible=None, language='zh'):
    """Key points from what the project's own names say, in ``language``. Returns (copy, notes)."""
    names = _names(project); shown = set(visible) if visible is not None else None
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    english = language == 'en'
    cues = []
    for words, title, detail, icon in CUES:
        parts = [name for name in names if any(word in name.casefold() for word in words)]
        if not parts:
            continue
        if english:
            translated = CUES_EN.get(title)
            detail = (translated[1] if isinstance(translated, tuple) and not isinstance(title, tuple) else CUES_EN.get(detail, '')) if detail else ''
            title = translated if isinstance(title, tuple) and translated else translated[0] if translated else ''
        if isinstance(title, tuple):
            material = _material_word(parts)
            if english:
                material = MATERIAL_EN.get(material, '')
            title = title[0].replace('{material}', material) if material else title[1]
        seen = [name for name in parts if shown is None or name in shown]
        cues.append({'title': title, 'detail': detail, 'icon': icon, 'parts': seen, 'words': words})
    points = _hint_points(hint); typed = bool(points)
    for point in points:                       # a typed point borrows the part and icon its words name
        text = (point['title'] + ' ' + point['detail']).casefold()
        match = next((cue for cue in cues if any(word in text for word in cue['words'])), None)
        point['icon'] = match['icon'] if match else 'check'
        point['parts'] = list(match['parts']) if match else []
    if not typed:
        usable = sorted((cue for cue in cues if cue['title']), key=lambda cue: not cue['parts'])     # what this picture shows first
        points = [{key: cue[key] for key in ('title', 'detail', 'icon', 'parts')} for cue in usable[:4]]
        dimensions = project['plan'].get('dimensions_mm')
        if dimensions:
            points.append({'title': DIMENSIONS[language], 'detail': ' × '.join('%g' % round(value, 1) for value in dimensions) + ' mm',
                           'icon': 'ruler', 'parts': []})
    headline = _clip(re.sub(r'\.(obj|glb|gltf|fbx|stl|zip)$', '', str(project.get('name', '')).strip(), flags=re.I) or ('Product' if english else '产品'), 28)
    subline = _clip(metadata.get('product_category') or metadata.get('material_note') or '', 40)       # the callouts carry the points; no line repeats them
    if english and CJK.search(subline):
        subline = ''
    notes = [] if points else ['没有从名称中识别出可介绍的部位；可在上方逐行写下要点，或手动添加文字。']
    kicker, emphasis = _category(project, headline, language)
    if english and (CJK.search(headline) or any(CJK.search(point['title'] + point['detail']) for point in points)):
        notes.append('未配置 AI，无法把中文翻译成英文：标题沿用项目名称，你写的要点保持原文，请在画面上改成英文。')
    return {'kicker': kicker, 'headline': headline, 'emphasis': emphasis, 'subline': subline, 'points': points}, notes


# A category tag per kind of product, and the word of the headline that names the product.
KICKERS = {'tool_storage': '车间收纳', 'personal_care': '日常洗护', 'tableware': '餐厨用品', 'bag': '日常出行', 'garden': '园艺户外',
           'electronics': '数码办公', 'furniture': '家居收纳'}
KICKERS_EN = {'tool_storage': 'Workshop storage', 'personal_care': 'Daily care', 'tableware': 'Kitchen & dining', 'bag': 'Everyday carry',
              'garden': 'Garden & outdoor', 'electronics': 'Desk & digital', 'furniture': 'Home storage'}


def _category(project, headline, language='zh'):
    try:
        import scene_stylist
        recipe = scene_stylist.recipe_for(project)
    except Exception:
        return '', ''
    words = sorted((word for word in recipe.get('words', ()) if len(word) >= 2 and word in headline and word != headline), key=len, reverse=True)
    return (KICKERS_EN if language == 'en' else KICKERS).get(recipe.get('id'), ''), (words[0] if words else '')


def _config(image):
    import ai_config
    config = ai_config.get_vision_config() if image else ai_config.get_config()
    return config if all(config.get(key) for key in ('enabled', 'api_key', 'model')) else None


def _ask(config, system, facts, schema, image_path=None):
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, image_path=image_path, label_images=bool(image_path), max_tokens=2000)
    output = ai._response(ai._post(config, body), config, ai.TOOL_NAME)
    ai._check_schema(output, schema)
    return output


# ------------------------------------------------------------------ what the picture shows

def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _project_file(project_id, url, suffixes):
    if not isinstance(url, str) or not url.startswith('/files/'):
        return None
    path = (DATA / url[7:]).resolve()
    if (DATA / project_id).resolve() not in path.parents or path.suffix.lower() not in suffixes or not path.is_file():
        return None
    return path


def _valid_probe(result):
    return (isinstance(result, dict) and result.get('success') is True and type(result.get('nx')) is int and type(result.get('ny')) is int
            and 8 <= result['nx'] <= 200 and 8 <= result['ny'] <= 200 and isinstance(result.get('cells'), list)
            and len(result['cells']) == result['nx'] * result['ny'] and isinstance(result.get('objects'), list)
            and all(type(cell) is int and -2 <= cell < len(result['objects']) for cell in result['cells'])
            and all(isinstance(name, str) for name in result['objects']))


def probe(project_id, output):
    """The saved scene of this image, cell by cell. Cached per scene file. None when it cannot be read."""
    import engine
    blend = _project_file(project_id, output.get('blend_url'), ('.blend',))
    executable = engine.blender_path()
    if blend is None or not executable or not PROBE_SCRIPT.is_file():
        return None
    identity = hashlib.sha256('|'.join([output.get('blend_sha256') or _digest(blend), _digest(PROBE_SCRIPT), str(GRID)]).encode()).hexdigest()[:32]
    cache = DATA / project_id / 'layers' / 'probes' / (identity + '.json')
    if cache.is_file():
        try:
            stored = json.loads(cache.read_text(encoding='utf-8'))
            if _valid_probe(stored):
                return stored
        except (OSError, ValueError):
            pass
    folder = cache.parent / (identity + '-' + uuid.uuid4().hex[:8]); folder.mkdir(parents=True)
    try:
        atomic_json(folder / 'request.json', {'blend_path': str(blend), 'result_path': str(folder / 'result.json'), 'grid': GRID})
        command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '2', '--python-exit-code', '1',
                   '--python', str(PROBE_SCRIPT), '--', str(folder / 'request.json')]
        try:
            with (folder / 'blender.log').open('w', encoding='utf-8') as log:
                ran = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=PROBE_TIMEOUT,
                                     cwd=str(ROOT), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (subprocess.TimeoutExpired, OSError):
            return None
        try:
            result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            return None
        if ran.returncode or not _valid_probe(result):
            return None
        atomic_json(cache, result)
        return result
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def probe_from_image(path, nx, ny):
    """Without the scene: what differs from the picture's own border is taken to be occupied."""
    with Image.open(path) as image:
        small = image.convert('RGB').resize((nx, ny), Image.Resampling.BOX)
    pixels = list(small.getdata())
    border = [pixels[y * nx + x] for y in range(ny) for x in range(nx) if x in (0, nx - 1) or y in (0, ny - 1)]
    middle = tuple(sorted(pixel[channel] for pixel in border)[len(border) // 2] for channel in range(3))
    cells = [0 if max(abs(pixel[channel] - middle[channel]) for channel in range(3)) > 28 else -1 for pixel in pixels]
    return {'success': True, 'nx': nx, 'ny': ny, 'cells': cells, 'objects': ['product'], 'estimated': True}


class Picture:
    """Occupancy and brightness of the picture on one grid, with fast sums over rectangles."""
    def __init__(self, probe_result, image_path=None):
        self.nx = probe_result['nx']; self.ny = probe_result['ny']; self.cells = probe_result['cells']
        self.objects = probe_result['objects']; self.estimated = bool(probe_result.get('estimated'))
        luminance = [.5] * (self.nx * self.ny); self.colors = None
        if image_path:
            with Image.open(image_path) as image:
                small = image.convert('RGB').resize((self.nx, self.ny), Image.Resampling.BOX)
            self.colors = list(small.getdata())
            luminance = [value / 255 for value in small.convert('L').getdata()]
        self.luminance = luminance
        self._product = self._table([1 if cell >= 0 else 0 for cell in self.cells])
        self._scenery = self._table([1 if cell == -2 else 0 for cell in self.cells])
        self._light = self._table(luminance); self._light2 = self._table([value * value for value in luminance])
        product = [(index % self.nx, index // self.nx) for index, cell in enumerate(self.cells) if cell >= 0]
        self.rect = ((min(x for x, _ in product) / self.nx, min(y for _, y in product) / self.ny,
                      (max(x for x, _ in product) + 1) / self.nx, (max(y for _, y in product) + 1) / self.ny) if product else None)

    def _table(self, values):
        table = [[0.0] * (self.nx + 1) for _ in range(self.ny + 1)]
        for y in range(self.ny):
            row = 0.0
            for x in range(self.nx):
                row += values[y * self.nx + x]; table[y + 1][x + 1] = table[y][x + 1] + row
        return table

    def _span(self, rect):
        x0 = max(0, min(self.nx - 1, math.floor(rect[0] * self.nx))); x1 = max(x0 + 1, min(self.nx, math.ceil(rect[2] * self.nx)))
        y0 = max(0, min(self.ny - 1, math.floor(rect[1] * self.ny))); y1 = max(y0 + 1, min(self.ny, math.ceil(rect[3] * self.ny)))
        return x0, y0, x1, y1

    def _sum(self, table, span):
        x0, y0, x1, y1 = span
        return table[y1][x1] - table[y0][x1] - table[y1][x0] + table[y0][x0]

    def cover(self, rect):
        """(share of the rectangle on the product, share on scenery)."""
        span = self._span(rect); area = (span[2] - span[0]) * (span[3] - span[1])
        return self._sum(self._product, span) / area, self._sum(self._scenery, span) / area

    def tone(self, rect):
        """(mean brightness, how uneven it is) under the rectangle."""
        span = self._span(rect); area = (span[2] - span[0]) * (span[3] - span[1])
        mean = self._sum(self._light, span) / area
        return mean, math.sqrt(max(0.0, self._sum(self._light2, span) / area - mean * mean))

    def backdrop(self):
        """(mean brightness, unevenness) of everything that is neither product nor scenery."""
        values = [light for light, cell in zip(self.luminance, self.cells) if cell == -1] or self.luminance
        mean = sum(values) / len(values)
        return mean, math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    def edge_spread(self):
        """How far the backdrop along the picture's edge strays from its usual colour (85th percentile of the
        brightness difference). Small for a flat sweep, large for a gradient or a scene that runs to the edge."""
        ring = [light for index, (light, cell) in enumerate(zip(self.luminance, self.cells))
                if cell == -1 and (index % self.nx in (0, 1, self.nx - 2, self.nx - 1) or index // self.nx in (0, 1, self.ny - 2, self.ny - 1))]
        if len(ring) < 8:
            return 1.0
        middle = sorted(ring)[len(ring) // 2]
        spread = sorted(abs(light - middle) for light in ring)
        return spread[int(len(spread) * .85)]

    def accent(self):
        """The product's own colour, made vivid, as '#rrggbb'; None when the product has no colour to speak of."""
        if not self.colors:
            return None
        bins = [[0.0, 0.0, 0.0, 0.0] for _ in range(24)]; product = 0
        for (r, g, b), cell in zip(self.colors, self.cells):
            if cell < 0:
                continue
            product += 1
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < .4 or v < .3:
                continue
            row = bins[int(h * 24) % 24]; weight = s * v
            row[0] += weight; row[1] += math.cos(h * math.tau) * weight; row[2] += math.sin(h * math.tau) * weight; row[3] += 1
        best = max(range(24), key=lambda index: bins[index][0] + .5 * (bins[index - 1][0] + bins[(index + 1) % 24][0]))
        near = [bins[best - 1], bins[best], bins[(best + 1) % 24]]
        if not product or sum(row[3] for row in near) < product * .12:
            return None                              # steel, glass, white: no hue covers enough of it
        hue = math.atan2(sum(row[2] for row in near), sum(row[1] for row in near)) / math.tau % 1
        r, g, b = colorsys.hsv_to_rgb(hue, .82, .9)
        return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))

    def set_product_box(self, box):
        """Where the product is in a picture read without its scene, as said by the vision model: (x0, y0, x1, y1)."""
        x0, y0, x1, y1 = [min(1.0, max(0.0, float(value))) for value in box]
        if x1 - x0 < .05 or y1 - y0 < .05:
            return
        self.cells = [0 if x0 <= (index % self.nx + .5) / self.nx <= x1 and y0 <= (index // self.nx + .5) / self.ny <= y1 else -1
                      for index in range(self.nx * self.ny)]
        self._product = self._table([1 if cell >= 0 else 0 for cell in self.cells])
        self.rect = (x0, y0, x1, y1); self.boxed = True

    def border_color(self):
        """The colour of the picture's edge, as '#rrggbb': the canvas a studio render can be moved on without a seam."""
        if not self.colors:
            return None
        border = [self.colors[y * self.nx + x] for y in range(self.ny) for x in range(self.nx) if x in (0, self.nx - 1) or y in (0, self.ny - 1)]
        middle = [sorted(pixel[channel] for pixel in border)[len(border) // 2] for channel in range(3)]
        return '#%02x%02x%02x' % tuple(middle)

    def on_product(self, x, y):
        ix = max(0, min(self.nx - 1, math.floor(x * self.nx))); iy = max(0, min(self.ny - 1, math.floor(y * self.ny)))
        return self.cells[iy * self.nx + ix] >= 0

    def anchor(self, names, facing=None):
        """A visible point of these parts to aim a line at, and how much of the picture they take.

        ``facing`` ('left' or 'right') moves the point towards that side of the part, so a line from a
        column of notes on that side does not have to cross the part to reach it.
        """
        wanted = {index for index, name in enumerate(self.objects) if name in set(names)}
        hits = [(index % self.nx, index // self.nx) for index, cell in enumerate(self.cells) if cell in wanted]
        if len(hits) < 3:
            return None, 0.0
        # The largest connected patch: a line to one drawer front, not to the middle of four casters.
        remaining = set(hits); best = []
        while remaining:
            stack = [remaining.pop()]; patch = []
            while stack:
                x, y = stack.pop(); patch.append((x, y))
                for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if neighbour in remaining:
                        remaining.discard(neighbour); stack.append(neighbour)
            if len(patch) > len(best):
                best = patch
        cx = sum(x for x, _ in best) / len(best); cy = sum(y for _, y in best) / len(best)
        if facing in ('left', 'right'):
            span = max(x for x, _ in best) - min(x for x, _ in best)
            cx = cx + (.3 if facing == 'right' else -.3) * span
        x, y = min(best, key=lambda cell: (cell[0] - cx) ** 2 + (cell[1] - cy) ** 2)
        return [(x + .5) / self.nx, (y + .5) / self.ny], len(hits) / (self.nx * self.ny)


# ------------------------------------------------------------------ measuring text

def text_width(text, size):
    """Estimated width of one line, in the same unit as ``size``. Mirrored by static/layer.js."""
    width = 0.0
    for char in text:
        code = ord(char)
        if code >= 0x2e80:
            width += 1.0
        elif char == ' ':
            width += .3
        elif char.isupper() or char in 'mwMW@%':
            width += .68
        elif char in 'iljtfI.,:;!|\'':
            width += .32
        else:
            width += .56
    return width * size


def wrap(text, size, limit):
    """Break text into lines no wider than ``limit``; never inside a Latin word when it can be helped."""
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for token in re.findall(r'[A-Za-z0-9_.%+\-/]+|\s+|.', paragraph):
            if text_width(line + token, size) <= limit or not line.strip():
                line += token
            else:
                lines.append(line.rstrip()); line = token.lstrip()
        lines.append(line.rstrip())
    return lines or ['']


# ------------------------------------------------------------------ compositions: what to say

# The built-in icon names of the name rules, as words the icon agent can look up.
ICON_WORDS = {'drawer': 'drawer', 'wheel': 'caster wheel', 'lock': 'lock', 'layers': 'worktop', 'handle': 'handle', 'door': 'door', 'drop': 'droplet',
              'check': 'check', 'screen': 'screen', 'power': 'power button', 'bulb': 'lightbulb', 'bag': 'bag', 'dot': 'circle dot', 'tag': 'tag',
              'box': 'box', 'ruler': 'ruler', 'shield': 'shield'}
UNIT_ICONS = (('mm', 'ruler'), ('cm', 'ruler'), ('kg', 'weight'), ('g', 'weight'), ('v', 'battery'), ('ah', 'battery'), ('w', 'zap'),
              ('nm', 'gauge'), ('n·m', 'gauge'), ('rpm', 'gauge'), ('层', 'layers'), ('件', 'package'), ('只', 'package'), ('个', 'package'))
NUMBER_TEXT = r'\d+(?:\.\d+)?'
SPEC_LINE = re.compile(r'^\s*(?P<label>[^\d:：]{1,16}?)\s*[:：]?\s*(?P<value>' + NUMBER_TEXT + r'(?:\s*(?:[-–~～至到×xX*])\s*' + NUMBER_TEXT
                       + r')*)\s*(?P<unit>[A-Za-z%°·]{1,6}|[件个层格只把套位档级挡寸]{1,2})?\s*$')
LIST_LINE = re.compile(r'^\s*(?P<label>[^\d:：]{1,16}?)\s*[:：]?\s*(?P<values>' + NUMBER_TEXT + r'(?:\s*[,，、/\s]\s*' + NUMBER_TEXT
                       + r'){2,})\s*(?P<unit>[A-Za-z%°]{1,6}|[件个层格只把套位档级挡寸]{1,2})?\s*$')


def _icon_for_unit(unit, label):
    """What to look an icon up by: the label when it names something drawable ("扳手尺寸" → a wrench), else the unit."""
    import layer_icons
    if layer_icons.terms_for(label):
        return label
    text = (unit or '').casefold()
    return next((word for key, word in UNIT_ICONS if text == key), label)


def specs_from(hint):
    """The specifications the user wrote, one per line ("扳手尺寸 8-24mm", "规格: 8,10,12,13 mm"), and the other lines."""
    specs = []; rest = []
    for line in re.split(r'[\n;；]+', hint or ''):
        line = line.strip(' \t-·•*')
        if not line:
            continue
        listed = LIST_LINE.match(line); single = SPEC_LINE.match(line) if not listed else None
        if listed:
            values = re.findall(NUMBER_TEXT, listed.group('values'))
            specs.append({'label': listed.group('label').strip(' ：:'), 'value': '%s–%s' % (values[0], values[-1]), 'unit': listed.group('unit') or '',
                          'values': values[:16], 'icon': _icon_for_unit(listed.group('unit'), listed.group('label'))})
        elif single and single.group('label').strip(' ：:'):
            value = re.sub(r'\s*(?:-|~|～|至|到)\s*', '–', single.group('value'))
            value = re.sub(r'\s*[xX*]\s*', '×', value)
            specs.append({'label': single.group('label').strip(' ：:'), 'value': value, 'unit': single.group('unit') or '',
                          'icon': _icon_for_unit(single.group('unit'), single.group('label'))})
        else:
            rest.append(line)
    return specs[:6], rest


def _facts_text(project, hint):
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    dims = project['plan'].get('dimensions_mm') or []
    return ' '.join([hint or '', str(project.get('name', '')), str(metadata.get('product_category', '')), str(metadata.get('material_note', '')),
                     str(metadata.get('dimensions', '')), ' '.join('%g' % round(value, 1) for value in dims)])


def content_rules(project, hint='', visible=None, language=DEFAULT_LANGUAGE):
    """What a composition says, from the user's own lines and the names in the model. Returns (content, notes)."""
    specs, rest = specs_from(hint)
    base, notes = copy_rules(project, '\n'.join(rest), visible, language)
    points = [{'title': point['title'], 'detail': point.get('detail') or '', 'icon': ICON_WORDS.get(point.get('icon') or '', point['title']),
               'parts': point.get('parts', [])} for point in base['points'] if point['title'] not in DIMENSIONS.values()][:4]
    dims = project['plan'].get('dimensions_mm')
    if dims and not any('×' in spec['value'] for spec in specs):
        specs.append({'label': OUTLINE[language], 'value': '×'.join('%g' % round(value) for value in dims), 'unit': 'mm', 'icon': 'ruler'})
    figures = [dict(spec) for spec in specs if not spec.get('values') and '×' not in spec['value']][:2]
    for figure in figures:
        figure.pop('values', None)
    if specs and not points:
        notes = [note for note in notes if '没有从名称中识别' not in note]
    if language == 'en' and any(CJK.search(spec['label'] + spec.get('unit', '')) for spec in specs) and not any('无法把中文翻译' in note for note in notes):
        notes.append('未配置 AI，无法把中文翻译成英文：你写的规格保持原文，请在画面上改成英文。')
    return {'kicker': base['kicker'], 'headline': base['headline'], 'subline': base['subline'], 'points': points, 'figures': figures,
            'specs': specs[:4], 'free_area': 'none', 'language': language}, notes


COMPOSE_PROMPT = '''You write the words of a product image for an online shop, set like a premium tool brand's feature image: very few words, each one earning its place, plain and concrete. You are shown the image and facts about the product.
{language}
Return data only:
- kicker: {kicker} naming the category or where it is used, printed small above the headline; it must not repeat the headline's words; or an empty string.
- headline: the product or its main use in plain words, {headline}. No exclamation marks, no superlatives.
- subline: one short supporting sentence of {subline}, or an empty string.
- points: 2 to 4 key points a shopper should notice. title {title}; detail {detail} or empty; icon: one to three plain English words naming the picture that shows the point ("wrench", "padlock", "caster wheel", "drawer", "ruler"); parts: the visible_parts names the point is about, copied exactly, or [] when it is about the whole product.
- figures: at most 2 numbers worth showing large, each with value (only the number or range, like 8–24), unit ({units}), label (what it measures, {label}) and icon (English words). Only numbers that appear in user_specs, hint or the facts: never estimate, convert, round or invent a number. [] when there is none.
- specs: at most 4 specifications for a row of specifications, with the same fields as figures plus values: for a list of sizes (a set of wrenches, sockets or bits) the sizes as separate numbers in order, otherwise []. The same rule: only numbers that were given. Every entry of user_specs appears here{translate_specs}.
- layout: the composition from layouts that suits this image and these words best.
- free_area: for a photo with a scene, which side is free of the product, people and busy detail and could hold a headline: left, right, top, bottom, or none.
- product_box: where the product itself is in the image, as [left, top, right, bottom], each a share of the image's width or height (0 to 1).
Say only what the image shows or the facts state: what a part is and what it is for. Do not praise. Never claim durability, strength, safety, quietness, smoothness, ease, roominess, comfort or quality (耐磨, 防锈, 稳固, 安全, 结实, 静音, 顺滑, 顺畅, 轻松, 宽敞, 高品质 and the like) unless the facts state it; never compare with other products. hint, when present, is what the user wants said and takes priority; {keep}'''
WRITING = {
    'en': dict(language='Write every word in English, in sentence case, the way a premium tool brand writes for international shoppers. hint, part names and user_specs may be in Chinese or another language: say what they say in natural English.',
               kicker='at most 24 characters', headline='at most 28 characters', subline='at most 48 characters', title='at most 22 characters',
               detail='at most 36 characters', units='mm, kg, V, Nm, pcs, layers and so on', label='at most 20 characters',
               translate_specs=', its label in English', keep='keep its meaning and every figure exactly, in English.'),
    'zh': dict(language='Write every word in Simplified Chinese.',
               kicker='2 to 6 Chinese characters', headline='at most 10 Chinese characters', subline='at most 18 Chinese characters',
               title='at most 6 Chinese characters', detail='at most 12 Chinese characters', units='mm, kg, V, 件, 层 and so on',
               label='at most 8 Chinese characters', translate_specs=', keeping its label', keep="keep the user's own wording and figures."),
}
# How long each part may be once written, per language: Latin words take more letters than Chinese characters.
LIMITS = {'en': {'kicker': 24, 'headline': 30, 'subline': 52, 'title': 24, 'detail': 40, 'label': 22},
          'zh': {'kicker': 14, 'headline': 24, 'subline': 40, 'title': 16, 'detail': 30, 'label': 16}}


def compose_prompt(language=DEFAULT_LANGUAGE):
    return COMPOSE_PROMPT.format(**WRITING[language])


LAYOUT_NOTES = {'split': 'words in a column beside the product: a headline, then up to two key figures or three short points; for a clean product photo',
                'callouts': 'the product on one side, up to four aligned notes on the other with thin lines to the parts they name; for showing structure',
                'scene': 'a headline set in the calm part of a photo of the product in use; for scene and lifestyle photos',
                'cards': 'the product in a large card and up to three small cards with an icon and a point or figure each; an overview',
                'spotlight': 'one buying question: a headline, the product, and a band with one or two key figures; needs a figure',
                'specs': 'the product above a row of specifications or a row of sizes; needs at least two specifications'}


def compose_schema():
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}
    import layer_compose
    text = lambda limit: {'type': 'string', 'maxLength': limit}
    point = obj({'title': text(40), 'detail': text(80), 'icon': text(60), 'parts': {'type': 'array', 'maxItems': 40, 'items': text(300)}})
    figure = obj({'value': text(24), 'unit': text(12), 'label': text(40), 'icon': text(60)})
    spec = obj({'value': text(24), 'unit': text(12), 'label': text(40), 'icon': text(60), 'values': {'type': 'array', 'maxItems': 16, 'items': text(10)}})
    return obj({'kicker': text(30), 'headline': text(60), 'subline': text(80), 'points': {'type': 'array', 'maxItems': 6, 'items': point},
                'figures': {'type': 'array', 'maxItems': 4, 'items': figure}, 'specs': {'type': 'array', 'maxItems': 6, 'items': spec},
                'layout': {'type': 'string', 'enum': list(layer_compose.LAYOUTS)},
                'free_area': {'type': 'string', 'enum': ['left', 'right', 'top', 'bottom', 'none']},
                'product_box': {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}}})


def content_for(project, hint='', image_path=None, visible=None, language=DEFAULT_LANGUAGE):
    """(content, source, notes, suggested layout): the model's words when one is configured, the rules' otherwise; in ``language``."""
    import layer_compose
    rules, notes = content_rules(project, hint, visible, language)
    config = _config(image_path)
    if not config:
        return rules, 'rules', notes, None
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    lookdev = (project['plan'].get('lookdev') or {}).get('decisions', [])
    shown = list(visible if visible is not None else _names(project))[:80]
    facts = {'product_name': str(project.get('name', ''))[:200], 'product_category': str(metadata.get('product_category', ''))[:200],
             'material_note': str(metadata.get('material_note', ''))[:500], 'real_dimensions_mm': project['plan'].get('dimensions_mm'),
             'visible_parts': shown, 'user_specs': rules['specs'], 'layouts': LAYOUT_NOTES,
             'surface_finishes': [{'part': str(row.get('object') or row.get('material', ''))[:80], 'finish': str(row.get('label', ''))[:40]}
                                  for row in lookdev[:30] if isinstance(row, dict)]}
    if hint:
        facts['hint'] = hint
    try:
        answer = _ask(config, compose_prompt(language), facts, compose_schema(), image_path)
    except Exception as exc:
        reason = str(exc)[:120] if isinstance(exc, ValueError) and str(exc) else ''
        return rules, 'rules', notes + ['AI 未返回可用结果%s，已改用名称规则。' % ('（%s）' % reason.rstrip('。') if reason else '')], None
    given = _facts_text(project, hint); notes = []; invented = 0; limit = LIMITS[language]
    allowed = set(shown)
    points = [{'title': _clip(point['title'], limit['title']), 'detail': _clip(point['detail'], limit['detail']), 'icon': _clip(point['icon'], 40),
               'parts': [name for name in point['parts'] if name in allowed]} for point in answer['points'] if point['title'].strip()][:4]

    def numbers(rows, listed):
        nonlocal invented
        kept = []
        for row in rows:
            values = [value for value in (row.get('values') or []) if layer_compose.grounded(value, given)] if listed else []
            if not layer_compose.grounded(row['value'], given):
                invented += 1; continue
            entry = {'label': _clip(row['label'], limit['label']), 'value': _clip(row['value'], 20), 'unit': _clip(row['unit'], 10), 'icon': _clip(row['icon'], 40)}
            if listed and len(values) >= 3:
                entry['values'] = values[:16]
            kept.append(entry)
        return kept
    figures = numbers(answer['figures'], False)[:2]; specs = numbers(answer['specs'], True)[:4]
    if invented:
        notes.append('AI 给出的 %d 个数字不在你提供的信息里，已去掉；数字只采用你写下或填写的规格。' % invented)
    # The user's own specifications come first, with every size they listed; the model's add only numbers those do
    # not already show. Written in another language than the user's, a specification takes the model's words for it.
    def worded(spec):
        if language == 'en' and CJK.search(spec.get('label', '') + spec.get('unit', '')):
            match = next((row for row in specs + figures if layer_compose.numbers_in(spec['value']) <= layer_compose.numbers_in(row['value'])), None)
            if match:
                spec = {**spec, 'label': match['label'], 'icon': match['icon'] or spec.get('icon', ''),
                        **({'unit': match['unit']} if CJK.search(spec.get('unit', '')) and match['unit'] else {})}
        return spec
    merged = [worded(spec) for spec in rules['specs']]
    for row in specs:
        if not any(layer_compose.numbers_in(row['value']) <= layer_compose.numbers_in(spec['value']) for spec in merged):
            merged.append(row)
    own = [worded(figure) for figure in rules['figures']]
    content = {'kicker': _clip(answer['kicker'], limit['kicker']), 'headline': _clip(answer['headline'], limit['headline']) or rules['headline'],
               'subline': _clip(answer['subline'], limit['subline']), 'points': points or rules['points'], 'figures': figures or own,
               'specs': merged[:4], 'free_area': answer['free_area'], 'language': language}
    box = answer['product_box']
    if box[2] - box[0] >= .05 and box[3] - box[1] >= .05:
        content['product_box'] = [round(value, 4) for value in box]
    return content, 'ai', notes, answer['layout']


def _picture_for(project_id, shot_id, wanted=None):
    """(project, shot, output, image path, picture, notes) for the newest picture of a shot."""
    project = get('project', project_id); shot = _shot(project, shot_id)
    if not project.get('analysis'):
        raise ValueError('请先等待模型分析完成')
    wanted = wanted if isinstance(wanted, dict) else {}
    outputs = [o for o in project.get('outputs', []) if o.get('shot_id') == shot_id and o.get('quality') != 'thumbnail'
               and (not wanted or all(o.get(key) == wanted.get(key) for key in ('job_id', 'version', 'quality')))]
    if not outputs:
        raise ValueError('请先生成这个镜头的预览，再排版图文。')
    output = max(outputs, key=lambda o: (o.get('version', 0), o.get('created_at') or 0))
    image = _project_file(project_id, output.get('png_url'), ('.png',))
    if image is None:
        raise ValueError('这张图片的文件不存在，请重新生成预览。')
    scene = probe(project_id, output); notes = []
    if scene is None:
        width, height = output.get('width') or 1, output.get('height') or 1
        scene = probe_from_image(image, max(8, round(GRID * width / max(width, height))), max(8, round(GRID * height / max(width, height))))
        if output.get('quality') != 'generated':
            notes.append('无法读取这张图片保存的场景，按画面颜色估计了产品位置，标注没有指向线。')
    return project, shot, output, image, Picture(scene, image), notes


def _point_anchors(content, picture, facing='right'):
    for point in content['points']:
        point['anchor'] = picture.anchor(point.get('parts') or [], facing)[0] if point.get('parts') and not picture.estimated else None


def _layer_from(project, shot, output, picture, content, glyphs, layout, source):
    import layer_compose
    aspect = '%d:%d' % (output.get('width') or 1, output.get('height') or 1)
    canvas, items, report = layer_compose.compose(layout, content, picture, aspect, output.get('quality') == 'generated')
    layer = validate({'version': 1, 'layout': layout, 'canvas': canvas, 'content': content, 'glyphs': glyphs, 'items': items,
                      'basis': basis(project['plan'], shot), 'source': source})
    return layer, report


def suggest(project_id, shot_id, body):
    """A proposed composition for this image: what to say, its icons and where everything goes. Nothing is saved."""
    import layer_compose
    import layer_icons
    if not isinstance(body, dict):
        raise ValueError('图文图层请求格式无效')
    hint = body.get('hint', '')
    if not isinstance(hint, str) or len(hint) > MAX_HINT:
        raise ValueError('要点说明不能超过 %d 字' % MAX_HINT)
    wanted_layout = body.get('layout')
    if wanted_layout not in (None, 'auto') and wanted_layout not in layer_compose.LAYOUTS:
        raise ValueError('未知的版式')
    language = body.get('language') or DEFAULT_LANGUAGE
    if language not in LANGUAGES:
        raise ValueError('文字语言只能是 en（英文）或 zh（中文）')
    project, shot, output, image, picture, notes = _picture_for(project_id, shot_id, body.get('output'))
    visible = None if picture.estimated else list(picture.objects)
    content, source, copy_notes, proposed = content_for(project, hint.strip(), image, visible, language)
    if picture.estimated and content.get('product_box'):
        picture.set_product_box(content['product_box'])
    _point_anchors(content, picture)
    # One icon per point, figure and specification: from the open sets, online, or drawn.
    wants = [(entry.get('title') or entry.get('label') or '', entry.get('icon') or '') for entry in content['points'] + content['figures'] + content['specs']]
    glyphs, chosen, icon_notes = layer_icons.resolve(wants) if wants else ({}, [], [])
    for entry, key in zip(content['points'] + content['figures'] + content['specs'], chosen):
        if key:
            entry['glyph'] = key
    pal = layer_compose.palette(picture, output.get('quality') == 'generated')
    order = layer_compose.suitable(content, pal, picture)
    layout = wanted_layout if wanted_layout in layer_compose.LAYOUTS else proposed if proposed in order[:3] else order[0]
    layer, report = _layer_from(project, shot, output, picture, content, glyphs, layout, source)
    if report['dropped']:
        notes.append('这个版式没有显示：%s%s；可换用其他版式，或删减文字。' % ('、'.join(report['dropped'][:4]), ' 等' if len(report['dropped']) > 4 else ''))
    return {'layer': layer, 'report': {'source': source, 'probe': 'image' if picture.estimated else 'scene', 'notes': copy_notes + icon_notes + notes,
                                       'anchored': sum(1 for item in layer['items'] if item.get('anchor')), 'layout': layout, 'suitable': order,
                                       'icons': {key: {'source': glyph['source'], 'name': glyph.get('name', ''), 'license': glyph.get('license', '')}
                                                 for key, glyph in layer.get('glyphs', {}).items()},
                                       'output': {key: output.get(key) for key in ('job_id', 'version', 'quality')}}}


def recompose(project_id, shot_id, body):
    """The same words in another composition, or laid out again after an edit. No model is asked."""
    import layer_compose
    if not isinstance(body, dict) or body.get('layout') not in layer_compose.LAYOUTS:
        raise ValueError('请选择一个版式')
    stored = validate({'version': 1, 'items': [], 'glyphs': body.get('glyphs') or {}, 'content': body.get('content') or {}})
    project, shot, output, image, picture, notes = _picture_for(project_id, shot_id, body.get('output'))
    content = stored.get('content') or {'kicker': '', 'headline': '', 'subline': '', 'points': [], 'figures': [], 'specs': []}
    if picture.estimated and content.get('product_box'):
        picture.set_product_box(content['product_box'])
    if any(point.get('parts') and point.get('anchor') is None for point in content['points']):
        _point_anchors(content, picture)
    source = body.get('source') if body.get('source') in ('rules', 'ai', 'manual') else 'manual'
    layer, report = _layer_from(project, shot, output, picture, content, stored.get('glyphs', {}), body['layout'], source)
    if report['dropped']:
        notes.append('这个版式没有显示：%s%s；可换用其他版式，或删减文字。' % ('、'.join(report['dropped'][:4]), ' 等' if len(report['dropped']) > 4 else ''))
    return {'layer': layer, 'report': {'source': source, 'probe': 'image' if picture.estimated else 'scene', 'notes': notes, 'layout': body['layout'],
                                       'anchored': sum(1 for item in layer['items'] if item.get('anchor')),
                                       'output': {key: output.get(key) for key in ('job_id', 'version', 'quality')}}}

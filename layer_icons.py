"""Icons for the text layer: found in open icon sets, or drawn by a model.

Every icon a layer uses is stored in the layer itself as plain path data (a
"glyph"), with where it came from and under which licence, so a saved or
exported layer never depends on a catalogue or a network:

    search()      the vendored line-icon sets (Lucide ISC, Tabler MIT; see
                  static/vendor/icons and scripts/vendor_icons.py), by English
                  keywords; Chinese words are mapped to them
    search_online()  Iconify's public API, limited to sets whose licence lets
                  the icons be used and changed freely
    pick()        a model chooses among the candidates, looking at them
    draw()        a model draws a line icon nobody has; the drawing is
                  validated, rasterised here and checked by the vision model
    resolve()     all of the above for the points of one layer

Glyphs are lists of ["s" | "f" | "e", d]: an SVG path drawn with the set's stroke,
filled, or filled with the even-odd rule (holes cut by overlapping shapes). Only path commands and numbers are accepted, so a glyph can never
carry markup, links or scripts into the page or an exported file.
"""
import gzip
import json
import math
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOGUE = ROOT / 'static' / 'vendor' / 'icons'
LOCAL_SETS = ('lucide', 'tabler')
PATH = re.compile(r'^[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s+-]+$')
MAX_OPS = 32
MAX_D = 4000
MAX_GLYPH = 20000
MAX_GLYPHS = 24
ONLINE_API = 'https://api.iconify.design'
# Sets whose licence allows using and changing the icons, commercially included, with the notice kept.
# The licence the API reports is checked again for every icon taken from it.
ONLINE_SETS = ('iconoir', 'akar-icons', 'mynaui', 'ph', 'material-symbols', 'mdi', 'carbon', 'fluent', 'lucide', 'tabler')
ONLINE_LICENCES = {'MIT', 'ISC', 'Apache-2.0'}
ONLINE_TIMEOUT = 8
_LOCK = threading.Lock()
_SETS = {}


# ------------------------------------------------------------------ glyph data

def valid_d(d):
    return isinstance(d, str) and 0 < len(d) <= MAX_D and bool(PATH.match(d)) and bool(re.match(r'\s*[Mm]', d))


def validate_glyph(value, label='图标'):
    """A clean copy of a glyph record, or ValueError."""
    if not isinstance(value, dict) or set(value) - {'source', 'name', 'license', 'ops', 'stroke', 'grid', 'model', 'check', 'concept'}:
        raise ValueError('%s数据无效' % label)
    ops = value.get('ops')
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_OPS:
        raise ValueError('%s的路径数量无效' % label)
    clean = []
    for op in ops:
        if not isinstance(op, list) or len(op) != 2 or op[0] not in ('s', 'f', 'e') or not valid_d(op[1]):
            raise ValueError('%s的路径无效' % label)
        clean.append([op[0], ' '.join(op[1].split())])
    if sum(len(op[1]) for op in clean) > MAX_GLYPH:
        raise ValueError('%s过于复杂' % label)
    row = {'ops': clean}
    for key, limit in (('source', 60), ('name', 80), ('license', 40), ('model', 120), ('concept', 80)):
        if value.get(key) is not None:
            if not isinstance(value[key], str) or len(value[key]) > limit or any(ord(char) < 32 for char in value[key]):
                raise ValueError('%s的来源信息无效' % label)
            row[key] = value[key]
    stroke = value.get('stroke', 2); grid = value.get('grid', 24)
    if type(stroke) not in (int, float) or not .25 <= stroke <= 64 or type(grid) not in (int, float) or not 8 <= grid <= 1024:
        raise ValueError('%s的尺寸无效' % label)
    row['stroke'] = stroke; row['grid'] = grid
    check = value.get('check')
    if check is not None:
        if not isinstance(check, dict) or set(check) - {'status', 'note'} or check.get('status') not in ('passed', 'failed', 'unchecked') \
                or not isinstance(check.get('note', ''), str) or len(check.get('note', '')) > 300:
            raise ValueError('%s的检查记录无效' % label)
        row['check'] = {'status': check['status'], **({'note': check['note']} if check.get('note') else {})}
    return row


# ------------------------------------------------------------------ SVG bodies to glyphs

SHAPES = {'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon', 'g'}


def _number(value):
    value = float(value)
    return ('%.3f' % value).rstrip('0').rstrip('.') if value % 1 else str(int(value))


def shape_path(element):
    """Path data for one SVG shape element, or None for one that draws nothing."""
    tag = element.tag.split('}')[-1]; get = lambda name, default='0': float(element.get(name, default))
    if tag == 'path':
        return element.get('d')
    if tag in ('circle', 'ellipse'):
        cx, cy = get('cx'), get('cy')
        rx = get('r') if tag == 'circle' else get('rx'); ry = get('r') if tag == 'circle' else get('ry')
        if rx <= 0 or ry <= 0:
            return None
        return 'M%s %sa%s %s 0 1 0 %s 0a%s %s 0 1 0 %s 0' % tuple(map(_number, (cx - rx, cy, rx, ry, 2 * rx, rx, ry, -2 * rx)))
    if tag == 'rect':
        x, y, w, h = get('x'), get('y'), get('width'), get('height')
        rx = element.get('rx'); ry = element.get('ry')
        rx = float(rx) if rx is not None else float(ry) if ry is not None else 0.0
        ry = float(ry) if ry is not None else rx
        rx = min(rx, w / 2); ry = min(ry, h / 2)
        if w <= 0 or h <= 0:
            return None
        if not rx or not ry:
            return 'M%s %sh%sv%sh%sz' % tuple(map(_number, (x, y, w, h, -w)))
        return ('M%s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sh%sa%s %s 0 0 1 %s %sv%sa%s %s 0 0 1 %s %sz'
                % tuple(map(_number, (x + rx, y, w - 2 * rx, rx, ry, rx, ry, h - 2 * ry, rx, ry, -rx, ry, -(w - 2 * rx),
                                      rx, ry, -rx, -ry, -(h - 2 * ry), rx, ry, rx, -ry))))
    if tag == 'line':
        return 'M%s %sL%s %s' % tuple(map(_number, (get('x1'), get('y1'), get('x2'), get('y2'))))
    if tag in ('polyline', 'polygon'):
        values = [float(part) for part in re.split(r'[\s,]+', element.get('points', '').strip()) if part]
        if len(values) < 4 or len(values) % 2:
            return None
        points = ['%s %s' % (_number(values[i]), _number(values[i + 1])) for i in range(0, len(values), 2)]
        return 'M' + 'L'.join(points) + ('z' if tag == 'polygon' else '')
    return None


def convert_body(body, size_limit=60000):
    """[[mode, d], ...] for an Iconify icon body, or None when it uses anything this app does not draw.

    Parsed as data: anything but plain shapes (text, images, links, styles, transforms,
    opacity, references) makes the whole icon unusable rather than partly drawn.
    """
    if not isinstance(body, str) or len(body) > size_limit or '<!' in body or '&' in body:
        return None
    try:
        root = ElementTree.fromstring('<svg xmlns="http://www.w3.org/2000/svg">' + body + '</svg>')
    except ElementTree.ParseError:
        return None
    ops = []

    def walk(element, inherited):
        for child in element:
            tag = child.tag.split('}')[-1]
            if tag not in SHAPES or child.get('transform') or child.get('opacity') or child.get('fill-opacity') or child.get('stroke-opacity') \
                    or child.get('style') or child.get('href') or child.get('{http://www.w3.org/1999/xlink}href'):
                raise ValueError(tag)
            style = dict(inherited)
            for key in ('fill', 'stroke', 'stroke-width', 'fill-rule'):
                if child.get(key) is not None:
                    style[key] = child.get(key)
            if tag == 'g':
                walk(child, style); continue
            d = shape_path(child)
            if not d:
                continue
            d = ' '.join(d.split())
            if not valid_d(d):
                raise ValueError('path')
            if style.get('fill') not in (None, 'none', 'currentColor') or style.get('stroke') not in (None, 'none', 'currentColor'):
                raise ValueError('colour')
            if style.get('fill') == 'currentColor':
                ops.append(['e' if style.get('fill-rule') == 'evenodd' else 'f', d])
            if style.get('stroke') == 'currentColor':
                ops.append(['s', d])
    try:
        walk(root, {'fill': 'currentColor', 'stroke': None})
    except (ValueError, TypeError):
        return None
    if not ops or len(ops) > MAX_OPS or sum(len(op[1]) for op in ops) > MAX_GLYPH:
        return None
    return ops


def stroke_width(body, default=2):
    match = re.search(r'stroke-width="([0-9.]+)"', body or '')
    try:
        return float(match.group(1)) if match else default
    except ValueError:
        return default


# ------------------------------------------------------------------ the vendored sets

def sets():
    """{prefix: catalogue} of the vendored icon sets; an unreadable set is left out."""
    with _LOCK:
        if not _SETS:
            for prefix in LOCAL_SETS:
                path = CATALOGUE / (prefix + '.json.gz')
                try:
                    data = json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))
                except (OSError, ValueError):
                    continue
                if isinstance(data.get('icons'), dict):
                    _SETS[prefix] = data
        return _SETS


# Product words in Chinese, and the English icon names that show them.
CHINESE = (
    (('扳手', '开口扳', '梅花扳', '活动扳'), ('wrench', 'tool')),
    (('套筒', '螺母', '六角'), ('nut', 'hexagon')),
    (('螺丝刀', '起子', '螺丝'), ('screw', 'screwdriver', 'tool')),
    (('钳',), ('pliers', 'tool')),
    (('锤',), ('hammer',)),
    (('电钻', '冲击钻', '钻头', '钻'), ('drill', 'hammer-drill')),
    (('锯',), ('saw', 'axe')),
    (('尺寸', '规格', '长度', '宽度', '高度', '外形', '大小', '刻度', '尺'), ('ruler-dimension-line', 'ruler-measure', 'ruler', 'dimensions')),
    (('重量', '载重', '承重', '净重', '公斤', '千克'), ('weight', 'scale')),
    (('电池', '续航', '电量'), ('battery', 'battery-full')),
    (('充电', '快充', '插头'), ('battery-charging', 'plug-zap', 'plug')),
    (('无刷', '电机', '马达', '动力', '功率'), ('zap', 'bolt', 'engine')),
    (('扭力', '扭矩', '转矩'), ('gauge', 'rotate-cw')),
    (('转速', '档位', '调速'), ('gauge', 'circle-gauge')),
    (('锁', '防盗'), ('lock', 'lock-keyhole')),
    (('钥匙',), ('key',)),
    (('抽屉',), ('archive', 'inbox', 'layout-rows')),
    (('脚轮', '万向轮', '轮子', '滚轮', '刹车', '移动'), ('wheel', 'circle-dot', 'disc', 'move')),
    (('拉手', '把手', '手柄', '握把', '提手'), ('grip-horizontal', 'hand-grab', 'hand')),
    (('柜门', '门'), ('door-closed', 'door')),
    (('台面', '桌面', '工作台'), ('desk', 'square')),
    (('不锈钢', '金属', '钢', '铝', '铁'), ('shield', 'layers', 'sparkles')),
    (('防锈', '耐腐蚀', '防护', '安全', '保护'), ('shield-check', 'shield')),
    (('防水', '防潮'), ('droplet', 'umbrella')),
    (('收纳', '储物', '分层', '层'), ('layers', 'archive', 'package')),
    (('便携', '手提', '携带'), ('briefcase', 'hand')),
    (('套装', '件套', '配件', '附件'), ('package', 'boxes', 'layout-grid')),
    (('工具箱',), ('toolbox', 'briefcase')),
    (('工具柜', '工具车'), ('archive', 'toolbox')),
    (('照明', '灯', '亮度'), ('lightbulb', 'flashlight', 'lamp')),
    (('温度', '散热'), ('thermometer', 'fan')),
    (('精度', '精准', '定位'), ('crosshair', 'target')),
    (('角度', '旋转', '转向'), ('rotate-cw', 'angle', 'refresh-cw')),
    (('防滑',), ('hand-grab', 'grip-horizontal')),
    (('磁', '吸附'), ('magnet',)),
    (('折叠',), ('fold-horizontal', 'minimize-2')),
    (('数量', '件数', '个数'), ('hash', 'list-ordered')),
    (('质保', '保修', '认证', '品质'), ('badge-check', 'award', 'shield-check')),
    (('环保', '天然'), ('leaf', 'sprout')),
    (('家用', '家庭', '居家'), ('home', 'house')),
    (('车载', '汽车', '货车', '运输'), ('car', 'truck')),
    (('工地', '施工', '安装'), ('hard-hat', 'construction', 'hammer')),
    (('园艺', '花园', '庭院', '草坪', '割草'), ('sprout', 'flower', 'leaf', 'trees')),
    (('清洁', '清洗'), ('sparkles', 'spray-can')),
    (('时间', '小时', '分钟'), ('clock', 'timer')),
    (('速度', '快速'), ('gauge', 'zap')),
    (('静音', '噪音'), ('volume-x', 'volume-1')),
)
# English words and the icon names that stand for them.
SYNONYMS = {
    'spanner': ('wrench',), 'wrench': ('wrench', 'tool'), 'size': ('ruler-dimension-line', 'ruler'), 'dimension': ('ruler-dimension-line', 'dimensions'),
    'dimensions': ('ruler-dimension-line', 'dimensions'), 'length': ('ruler', 'move-horizontal'), 'width': ('move-horizontal', 'ruler'),
    'height': ('move-vertical', 'ruler'), 'load': ('weight',), 'capacity': ('weight', 'package'), 'runtime': ('battery', 'clock'),
    'drawer': ('archive', 'inbox'), 'drawers': ('archive', 'layers'), 'caster': ('wheel', 'circle-dot', 'disc'), 'casters': ('wheel', 'circle-dot', 'disc'),
    'handle': ('grip-horizontal', 'hand-grab'), 'storage': ('archive', 'package', 'layers'), 'set': ('package', 'boxes'), 'kit': ('package', 'boxes'),
    'motor': ('zap', 'engine'), 'brushless': ('zap',), 'torque': ('gauge', 'rotate-cw'), 'speed': ('gauge',), 'power': ('zap', 'bolt'),
    'charging': ('battery-charging',), 'worktop': ('desk',), 'surface': ('square', 'layers'), 'steel': ('shield', 'layers'),
    'stainless': ('shield', 'sparkles'), 'rust': ('shield-check',), 'waterproof': ('droplet',), 'portable': ('briefcase', 'hand'),
    'light': ('lightbulb', 'flashlight'), 'lamp': ('lamp', 'lightbulb'), 'precision': ('crosshair', 'target'), 'angle': ('angle', 'rotate-cw'),
    'magnetic': ('magnet',), 'warranty': ('badge-check', 'shield-check'), 'quiet': ('volume-x',), 'socket': ('nut', 'hexagon'),
    'screwdriver': ('screw', 'tool'), 'pliers': ('pliers',), 'saw': ('saw', 'axe'), 'wheel': ('circle-dot', 'disc', 'wheel'),
    'padlock': ('lock',), 'locked': ('lock',), 'keyhole': ('lock-keyhole',), 'kilogram': ('weight',), 'kg': ('weight',), 'heavy': ('weight',),
    'tray': ('layout-panel-top', 'archive'), 'compartment': ('archive', 'layout-grid'), 'cabinet': ('archive', 'toolbox'), 'door': ('door-closed',),
    'castor': ('wheel', 'circle-dot', 'disc'), 'castors': ('wheel', 'circle-dot', 'disc'), 'locking': ('lock',), 'lockable': ('lock',), 'trolley': ('shopping-cart', 'truck'), 'mobile': ('move', 'circle-dot'), 'move': ('move',),
    'top': ('desk', 'square'), 'tabletop': ('desk',), 'table': ('desk',), 'workbench': ('desk',), 'bench': ('desk',), 'shelf': ('layers', 'layout-rows'), 'shelves': ('layers', 'layout-rows'),
    'layers': ('layers',), 'layer': ('layers',), 'tool': ('wrench', 'tool'), 'tools': ('wrench', 'tool', 'toolbox'), 'hex': ('hexagon', 'nut'),
    'bolt': ('bolt', 'nut'), 'nut': ('nut', 'hexagon'), 'voltage': ('zap', 'battery'), 'volt': ('zap', 'battery'),
}
# Which word of a label names the picture. A specific measure does ("台面承重": a weight, not a table);
# a generic measure or a material gives way to the thing it describes ("扳手尺寸": a wrench, "7层抽屉": a drawer,
# "不锈钢台面": a worktop).
SPECIFIC = ('承重', '载重', '重量', '净重', '公斤', '千克', '扭力', '扭矩', '转矩', '转速', '续航', '电量', '电池', '功率', '温度',
            '角度', '精度', '速度', '时间', '小时', '分钟', '亮度', '噪音', '静音')
GENERIC = ('尺寸', '规格', '大小', '数量', '件数', '个数', '长度', '宽度', '高度', '层', '分层', '不锈钢', '金属', '钢', '铝', '铁')
# The same in English ("Top load": a weight; "Wrench sizes": a wrench).
SPECIFIC_EN = ('load', 'weight', 'capacity', 'torque', 'runtime', 'battery', 'speed', 'power', 'voltage', 'volt', 'kg', 'kilogram',
               'heavy', 'temperature', 'angle', 'precision', 'brightness', 'lock', 'locking', 'lockable', 'locked')
GENERIC_EN = ('size', 'sizes', 'dimension', 'dimensions', 'length', 'width', 'height', 'count', 'number', 'layer', 'layers',
              'stainless', 'steel', 'metal', 'aluminium', 'aluminum')
# Names whose picture means something else on a product photo: Lucide's nut is a hazelnut, and the
# "table" icons of both sets are spreadsheets (the worktop is Tabler's desk).
MISLEADING = {'lucide': {'nut', 'nut-off', 'bolt', 'table', 'table-2', 'table-properties'}, 'tabler': {'table'}}
VARIANT = re.compile(r'-(?:\d+|alt|outline|square|circle|off|x|plus|minus|check|cog|search|question|exclamation|dashed|slash|dot)\b')


def terms_for(query):
    """(term, weight) pairs to look a query up by: icon-name phrases first."""
    text = str(query or '').strip().lower()
    found = []
    def kind(words):
        hit = [word for word in words if word in text]
        return 0 if any(word in SPECIFIC for word in hit) else 2 if all(word in GENERIC for word in hit) else 1
    # A specific measure first, then the things named, a generic measure last; among equals the earlier word.
    matched = sorted(((kind(words), min(text.index(word) for word in words if word in text), names) for words, names in CHINESE
                      if any(word in text for word in words)), key=lambda row: row[:2])
    for rank, (_, _, names) in enumerate(matched):
        found += [(name, 1.0 - .12 * rank - .05 * index) for index, name in enumerate(names)]
    latin = re.findall(r'[a-z][a-z0-9]*', text)
    if latin:
        found.append(('-'.join(latin), 1.0))
        for word in latin:
            # "weights", "drawers", "boxes" are looked up as the thing itself.
            forms = [word] + ([word[:-2]] if word.endswith('es') and len(word) > 4 else []) + ([word[:-1]] if word.endswith('s') and len(word) > 3 else [])
            lift = .1 if word in SPECIFIC_EN else -.06 if word in GENERIC_EN and len(latin) > 1 else 0
            for form in forms:
                found.append((form, (.85 if form == word else .83) + lift))
                found += [(name, .8 - .05 * index + lift) for index, name in enumerate(SYNONYMS.get(form, ()))]
    seen = {}; ordered = []
    for term, weight in found:
        if term not in seen or seen[term] < weight:
            seen[term] = weight
    for term, weight in found:
        if term in seen:
            ordered.append((term, seen.pop(term)))
    return ordered


def _match(name, term):
    if name == term:
        return 10.0
    parts = name.split('-'); wanted = term.split('-')
    if all(part in parts for part in wanted):
        return 7.0 - .6 * (len(parts) - len(wanted)) - (.8 if VARIANT.search(name) else 0)
    if name.startswith(term + '-'):
        return 5.0 - .4 * (len(parts) - len(wanted))
    shared = sum(1 for part in wanted if part in parts)
    if shared:
        return 3.0 * shared / len(wanted) - .3 * (len(parts) - shared)
    return 0.0


def search(query, limit=24):
    """Candidates from the vendored sets, best first: [{id, prefix, name, ops, license, stroke}]."""
    terms = terms_for(query)
    if not terms:
        return []
    scored = []
    for prefix, catalogue in sets().items():
        bias = .15 if prefix == 'lucide' else 0                      # one family wins a tie, so a layer's icons look alike
        hidden = MISLEADING.get(prefix, set())
        for name in catalogue['icons']:
            if name in hidden:
                continue
            score = max((_match(name, term) * weight for term, weight in terms), default=0.0)
            if score >= 2.0:
                scored.append((score + bias, prefix, name))
        for alias, parent in catalogue.get('aliases', {}).items():
            score = max((_match(alias, term) * weight for term, weight in terms), default=0.0)
            if score >= 8.0:
                scored.append((score + bias - .1, prefix, parent))
    scored.sort(key=lambda row: (-row[0], row[2]))
    rows = []; seen = set()
    for score, prefix, name in scored:
        if (prefix, name) in seen:
            continue
        seen.add((prefix, name))
        rows.append(glyph_from(prefix, name, score=round(score, 2)))
        if len(rows) >= limit:
            break
    return rows


def glyph_from(prefix, name, score=None):
    catalogue = sets()[prefix]
    row = {'id': prefix + ':' + name, 'prefix': prefix, 'name': name, 'source': prefix, 'license': catalogue['license'].get('spdx', ''),
           'ops': catalogue['icons'][name], 'stroke': catalogue.get('stroke_width', 2), 'grid': catalogue.get('grid', 24)}
    if score is not None:
        row['score'] = score
    return row


def glyph(icon_id):
    """A stored glyph for 'prefix:name' from a vendored set, or None."""
    prefix, _, name = str(icon_id).partition(':')
    catalogue = sets().get(prefix)
    if not catalogue:
        return None
    name = catalogue.get('aliases', {}).get(name, name)
    if name not in catalogue['icons']:
        return None
    row = glyph_from(prefix, name)
    return {key: row[key] for key in ('source', 'name', 'license', 'ops', 'stroke', 'grid')}


# ------------------------------------------------------------------ online

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise urllib.error.URLError('redirect')


def _get_json(path, params):
    url = ONLINE_API + path + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'studio-icon-agent'})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=ONLINE_TIMEOUT) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('too large')
    return json.loads(raw.decode('utf-8'))


def search_online(query, limit=12):
    """Candidates from Iconify's public API, only from sets with a free licence. [] when it cannot be reached."""
    terms = [term for term, _ in terms_for(query)][:3]
    if not terms:
        return []
    rows = []; seen = set()
    try:
        for term in terms:
            found = _get_json('/search', {'query': term.replace('-', ' '), 'limit': 64, 'prefixes': ','.join(ONLINE_SETS)})
            names = [value for value in found.get('icons', []) if isinstance(value, str) and re.fullmatch(r'[a-z0-9-]+:[a-z0-9-]+', value)]
            collections = found.get('collections') if isinstance(found.get('collections'), dict) else {}
            by_prefix = {}
            for value in names:
                prefix, name = value.split(':')
                if prefix in ONLINE_SETS and value not in seen and name not in MISLEADING.get(prefix, ()) and len(by_prefix.get(prefix, [])) < 6:
                    by_prefix.setdefault(prefix, []).append(name); seen.add(value)
            for prefix, wanted in by_prefix.items():
                info = collections.get(prefix) or {}
                licence = (info.get('license') or {}).get('spdx') if isinstance(info.get('license'), dict) else None
                if licence not in ONLINE_LICENCES:
                    continue
                data = _get_json('/%s.json' % prefix, {'icons': ','.join(wanted)})
                icons = data.get('icons') if isinstance(data.get('icons'), dict) else {}
                grid = data.get('height') or data.get('width') or 24
                for name in wanted:
                    body = (icons.get(name) or {}).get('body')
                    ops = convert_body(body) if isinstance(body, str) else None
                    if not ops or type(grid) not in (int, float) or not 8 <= grid <= 1024:
                        continue
                    line = any(op[0] == 's' for op in ops)
                    rows.append({'id': prefix + ':' + name, 'prefix': prefix, 'name': name, 'source': 'iconify:' + prefix, 'license': licence,
                                 'ops': ops, 'stroke': stroke_width(body, 2) if line else 2, 'grid': grid,
                                 # line icons sit with the vendored ones; an outline variant of a filled set comes next
                                 'score': 6.0 + (.5 if line else .3 if name.endswith('-outline') else 0) - len(rows) * .01})
    except (OSError, ValueError, UnicodeError, urllib.error.URLError):
        pass
    rows.sort(key=lambda row: -row['score'])
    return rows[:limit]


# ------------------------------------------------------------------ drawing a glyph into pixels

TOKEN = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?')


def _scan(d):
    """Commands with their numbers; arc flags may be written without separators ("a1 1 0 011 1")."""
    out = []; position = 0; command = None; numbers = []
    flag_slots = lambda count: count % 7 in (3, 4)
    while position < len(d):
        char = d[position]
        if char in ' ,\t\n\r':
            position += 1; continue
        if char.isalpha():
            if command:
                out.append((command, numbers))
            command = char; numbers = []; position += 1; continue
        if command in ('A', 'a') and flag_slots(len(numbers)) and char in '01':
            numbers.append(float(char)); position += 1; continue
        match = TOKEN.match(d, position)
        if not match or match.group(0).isalpha():
            raise ValueError('bad path')
        numbers.append(float(match.group(0))); position = match.end()
    if command:
        out.append((command, numbers))
    return out


def _arc(x1, y1, rx, ry, angle, large, sweep, x2, y2):
    """Points along an SVG elliptical arc (endpoint parameterisation, SVG 1.1 F.6.5)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]
    phi = math.radians(angle % 360); cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cos * dx + sin * dy, -sin * dx + cos * dy
    rx, ry = abs(rx), abs(ry)
    scale = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if scale > 1:
        rx *= math.sqrt(scale); ry *= math.sqrt(scale)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    factor = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        factor = -factor
    cxp, cyp = factor * rx * y1p / ry, -factor * ry * x1p / rx
    cx, cy = cos * cxp - sin * cyp + (x1 + x2) / 2, sin * cxp + cos * cyp + (y1 + y2) / 2

    def angle_of(ux, uy, vx, vy):
        value = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return value
    start = angle_of(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle_of((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= math.tau
    elif sweep and delta < 0:
        delta += math.tau
    steps = max(4, int(abs(delta) / (math.pi / 16)))
    points = []
    for index in range(1, steps + 1):
        theta = start + delta * index / steps
        x, y = rx * math.cos(theta), ry * math.sin(theta)
        points.append((cos * x - sin * y + cx, sin * x + cos * y + cy))
    return points


def flatten(d):
    """The path as polylines in its own coordinates: [[(x, y), ...], ...], one per subpath."""
    lines = []; current = []; x = y = 0.0; start = (0.0, 0.0); last_control = None; last = ''

    def cubic(p0, p1, p2, p3):
        return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t * t * p2[0] + t ** 3 * p3[0],
                 (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t * t * p2[1] + t ** 3 * p3[1])
                for t in (index / 12 for index in range(1, 13))]
    for command, numbers in _scan(d):
        relative = command.islower(); kind = command.upper()
        sizes = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}
        size = sizes[kind]
        if kind == 'Z':
            if current:
                current.append(start); lines.append(current)
            current = [start]; x, y = start; last_control = None; last = 'Z'
            continue
        if not numbers or len(numbers) % size:
            raise ValueError('bad path')
        for index in range(0, len(numbers), size):
            args = numbers[index:index + size]
            ox, oy = (x, y) if relative else (0.0, 0.0)
            if kind == 'M' and index == 0:
                if len(current) > 1:
                    lines.append(current)
                x, y = args[0] + ox, args[1] + oy; start = (x, y); current = [(x, y)]; last_control = None; continue
            if kind in ('M', 'L'):
                x, y = args[0] + ox, args[1] + oy; current.append((x, y)); last_control = None
            elif kind == 'H':
                x = args[0] + (x if relative else 0); current.append((x, y)); last_control = None
            elif kind == 'V':
                y = args[0] + (y if relative else 0); current.append((x, y)); last_control = None
            elif kind in ('C', 'S'):
                if kind == 'C':
                    c1 = (args[0] + ox, args[1] + oy); c2 = (args[2] + ox, args[3] + oy); end = (args[4] + ox, args[5] + oy)
                else:
                    c1 = (2 * x - last_control[0], 2 * y - last_control[1]) if last_control and last in ('C', 'S') else (x, y)
                    c2 = (args[0] + ox, args[1] + oy); end = (args[2] + ox, args[3] + oy)
                current += cubic((x, y), c1, c2, end); x, y = end; last_control = c2
            elif kind in ('Q', 'T'):
                if kind == 'Q':
                    q = (args[0] + ox, args[1] + oy); end = (args[2] + ox, args[3] + oy)
                else:
                    q = (2 * x - last_control[0], 2 * y - last_control[1]) if last_control and last in ('Q', 'T') else (x, y)
                    end = (args[0] + ox, args[1] + oy)
                c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y)); c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
                current += cubic((x, y), c1, c2, end); x, y = end; last_control = q
            elif kind == 'A':
                end = (args[5] + ox, args[6] + oy)
                current += _arc(x, y, args[0], args[1], args[2], bool(args[3]), bool(args[4]), end[0], end[1]); x, y = end; last_control = None
            last = kind
    if len(current) > 1:
        lines.append(current)
    return lines


def rasterise(glyph_value, size=96, color=(20, 20, 20), background=(255, 255, 255)):
    """The glyph drawn as a picture, for a vision model or a contact sheet."""
    from PIL import Image, ImageDraw
    scale = 4; grid = float(glyph_value.get('grid', 24)); unit = size * scale / grid
    canvas = Image.new('RGB', (size * scale, size * scale), background); draw = ImageDraw.Draw(canvas)
    width = max(1, round(float(glyph_value.get('stroke', 2)) * unit))
    for mode, d in glyph_value['ops']:
        try:
            lines = flatten(d)
        except (ValueError, IndexError, TypeError, ZeroDivisionError):
            continue
        if mode in ('f', 'e'):
            from PIL import ImageChops
            mask = Image.new('1', canvas.size, 0)
            for line in lines:
                if len(line) > 2:
                    shape = Image.new('1', canvas.size, 0)
                    ImageDraw.Draw(shape).polygon([(px * unit, py * unit) for px, py in line], fill=1)
                    mask = ImageChops.logical_xor(mask, shape)
            canvas.paste(color, mask=mask)
            continue
        for line in lines:
            points = [(px * unit, py * unit) for px, py in line]
            if len(points) > 1:
                draw.line(points, fill=color, width=width, joint='curve')
                for px, py in (points[0], points[-1]):
                    draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def contact_sheet(rows, cell=96, columns=8, labels=True):
    """Candidates side by side, numbered, as one picture: [(title, [glyph, ...]), ...]."""
    from PIL import Image, ImageDraw
    height = sum((math.ceil(len(glyphs) / columns) or 1) * (cell + 22) + 26 for _, glyphs in rows)
    sheet = Image.new('RGB', (columns * (cell + 8) + 8, height), (255, 255, 255)); draw = ImageDraw.Draw(sheet)
    y = 4
    for title, glyphs in rows:
        draw.text((8, y), title, fill=(0, 0, 0)); y += 22
        for index, value in enumerate(glyphs):
            column = index % columns; row = index // columns
            x0 = 8 + column * (cell + 8); y0 = y + row * (cell + 22)
            sheet.paste(rasterise(value, cell), (x0, y0))
            draw.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), outline=(210, 210, 210))
            if labels:
                draw.text((x0 + 2, y0 + cell + 2), str(index + 1), fill=(200, 0, 0))
        y += (math.ceil(len(glyphs) / columns) or 1) * (cell + 22) + 4
    return sheet


# ------------------------------------------------------------------ the model's part

PICK_PROMPT = '''You choose icons for the key points printed on a product photo. The attached sheet shows, for each point in the order given, numbered candidate icons from open icon sets. For every point return the number of the candidate that a shopper would read as that point at a glance, or 0 when none of them shows it (a vaguely related shape is worse than none: a drawn icon will be made instead). Prefer simple, literal pictures of the thing (a wrench for a wrench size, a ruler for dimensions, a padlock for a lock), and outline icons over solid ones when both fit, so the icons of one picture look alike. Return data only.'''

DRAW_PROMPT = '''You draw one icon in the style of the Lucide line-icon set: a 24 x 24 grid, outlines drawn with a 2-unit stroke that has round caps and joins, no shading, no text, no letters or numbers, at least 2 units of empty margin, and only as much detail as stays clear at 24 pixels. It must show the given concept literally, the way a shopper would recognise it on a product photo: draw the object's characteristic silhouette (its outline as seen from its most telling side) and at most two inner details.
examples are real icons from the same set with their path data; reuse their proportions, curves and stroke style, and adapt or combine them when they are close to the concept. fix, when present, lists what was wrong with your previous drawing.
Return data only:
- plan: one or two English sentences: which view of the object, which few shapes, where on the grid.
- paths: 1 to 12 entries. mode is "stroke" for outlines (almost everything) or "fill" for a small solid dot. d is SVG path data with only M L H V C S Q T A Z commands (upper or lower case) and numbers, coordinates between 2 and 22.
- note: one short English sentence describing what you drew.'''

CHECK_PROMPT = '''You check an icon that was drawn for a product image. The image shows the icon large (left) and at the size it will be printed (right). The icon is always printed beside its label (concept), so it does not have to explain the label on its own: it has to be a fitting picture for it. An object is shown as that object; a measure or a quality is shown by its usual symbol (a weight for a load, a ruler or a dimension line for a size, a gauge for torque, a shield for protection), and plan says what the drawer meant to show.
- recognisable: true when someone reading the label beside it would connect the two at a glance and the picture is clearly what plan says; false when it reads as something else (a bottle for a screwdriver bit) or as nothing in particular.
- clean: true when it is a tidy line icon: even strokes, no stray marks, nothing broken or overlapping by mistake, no text or letters. A small imperfection at the printed size alone is not a reason for false.
Return data only: recognisable, clean (booleans), issues (up to 3 short English notes on what to fix, empty when none), note (one short sentence in Chinese).'''


def _obj(properties):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}


def pick_schema(count, sizes):
    return _obj({'choices': {'type': 'array', 'minItems': count, 'maxItems': count,
                             'items': _obj({'point': {'type': 'integer', 'minimum': 1, 'maximum': count},
                                            'candidate': {'type': 'integer', 'minimum': 0, 'maximum': max(sizes or [0])}})}})


def draw_schema():
    path = _obj({'mode': {'type': 'string', 'enum': ['stroke', 'fill']}, 'd': {'type': 'string', 'minLength': 2, 'maxLength': 1200}})
    return _obj({'plan': {'type': 'string', 'maxLength': 400}, 'paths': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': path},
                 'note': {'type': 'string', 'maxLength': 200}})


def check_schema():
    return _obj({'recognisable': {'type': 'boolean'}, 'clean': {'type': 'boolean'},
                 'issues': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string', 'maxLength': 160}}, 'note': {'type': 'string', 'maxLength': 200}})


def _config(image):
    import ai_config
    config = ai_config.get_vision_config() if image else ai_config.get_config()
    return config if all(config.get(key) for key in ('enabled', 'api_key', 'model')) else None


def _ask(config, system, facts, schema, image_path=None, max_tokens=2500):
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, image_path=image_path, max_tokens=max_tokens)
    output = ai._response(ai._post(config, body), config, ai.TOOL_NAME)
    if isinstance(output, dict):
        # Some models echo the request back beside their answer; only the answer's own fields are read.
        output = {key: value for key, value in output.items() if key in schema['properties']}
    ai._check_schema(output, schema)
    return output


def _temporary_png(image):
    import tempfile
    handle = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    image.save(handle, format='PNG'); handle.close()
    return Path(handle.name)


def pick(points, candidates):
    """The model's choice per point: [index into its candidates, or None for none, or -1 when it was not asked]."""
    config = _config(True)
    asked = [index for index, rows in enumerate(candidates) if rows]
    if not config or not asked:
        return [-1] * len(points)
    sheet = contact_sheet([('%d. %s' % (position + 1, points[index][:40]), candidates[index][:8]) for position, index in enumerate(asked)])
    path = _temporary_png(sheet)
    try:
        facts = {'points': [{'point': position + 1, 'text': points[index], 'candidates': [row['name'] for row in candidates[index][:8]]}
                            for position, index in enumerate(asked)]}
        answer = _ask(config, PICK_PROMPT, facts, pick_schema(len(asked), [len(candidates[index][:8]) for index in asked]), path)
    except Exception:
        return [-1] * len(points)
    finally:
        path.unlink(missing_ok=True)
    result = [-1] * len(points)
    for choice in answer['choices']:
        index = asked[choice['point'] - 1]
        result[index] = choice['candidate'] - 1 if 1 <= choice['candidate'] <= len(candidates[index][:8]) else None
    return result


def _check_drawing(value, concept, config, plan=''):
    from PIL import Image
    big = rasterise(value, 256); small = rasterise(value, 32)
    sheet = Image.new('RGB', (256 + 24 + 64, 256), (255, 255, 255)); sheet.paste(big, (0, 0)); sheet.paste(small.resize((64, 64), Image.Resampling.NEAREST), (280, 96))
    path = _temporary_png(sheet)
    try:
        answer = _ask(config, CHECK_PROMPT, {'concept': concept, 'plan': plan}, check_schema(), path, max_tokens=800)
    finally:
        path.unlink(missing_ok=True)
    ok = answer['recognisable'] and answer['clean']
    return ok, [re.sub(r'\s+', ' ', item).strip()[:160] for item in answer['issues']], re.sub(r'\s+', ' ', answer['note']).strip()[:200]


def draw(concept, attempts=2):
    """A line icon for a concept nobody has, drawn by the text model and checked by the vision model.

    Returns a glyph (with source 'drawn', the model's name and the check) or raises ValueError.
    """
    concept = re.sub(r'\s+', ' ', str(concept or '')).strip()[:80]
    if not concept:
        raise ValueError('请写出要画的图标是什么。')
    config = _config(False)
    if not config:
        raise ValueError('未配置 AI，无法绘制图标；可从图标库中选择。')
    # Drawing asks more of a model than writing does: a stronger one may be named for it alone (same endpoint and key).
    model = drawing_model()
    if model:
        config = {**config, 'model': model}
    checker = _config(True); issues = []; best = None
    # Related icons from the vendored set show the model the style, and often most of the shape.
    examples = [{'name': row['name'], 'paths': [{'mode': 'stroke' if op[0] == 's' else 'fill', 'd': op[1]} for op in row['ops']]}
                for row in search(concept, 8) if row['prefix'] == 'lucide' and all(op[0] == 's' for op in row['ops'])][:3]
    if len(examples) < 2:
        examples += [{'name': name, 'paths': [{'mode': 'stroke', 'd': op[1]} for op in glyph('lucide:' + name)['ops']]}
                     for name in ('wrench', 'hammer', 'ruler') if glyph('lucide:' + name)][:3 - len(examples)]
    for attempt in range(attempts):
        facts = {'concept': concept, 'examples': examples}
        if issues:
            facts['fix'] = issues
        try:
            answer = _ask(config, DRAW_PROMPT, facts, draw_schema(), max_tokens=3000)
        except ValueError as exc:
            if attempt + 1 < attempts and not _refused(exc):
                issues = ['Your answer did not follow the schema: return an object with plan, paths and note.']
                continue
            if best:
                return best
            raise
        ops = [['s' if row['mode'] == 'stroke' else 'f', ' '.join(row['d'].split())] for row in answer['paths']]
        try:
            value = validate_glyph({'source': 'drawn', 'name': concept, 'concept': concept, 'license': 'own', 'ops': ops, 'stroke': 2,
                                    'grid': 24, 'model': config['model']})
            for _, d in value['ops']:
                flatten(d)
        except (ValueError, IndexError, ZeroDivisionError):
            issues = ['The path data was not valid SVG path syntax; use only M L H V C S Q T A Z and numbers.']
            continue
        if not checker:
            value['check'] = {'status': 'unchecked', 'note': '未配置视觉模型，未核对图形。'}
            return value
        try:
            ok, issues, note = _check_drawing(value, concept, checker, re.sub(r'\s+', ' ', str(answer.get('plan', ''))).strip()[:400])
        except Exception:
            value['check'] = {'status': 'unchecked', 'note': '视觉模型未能核对图形。'}
            return value
        value['check'] = {'status': 'passed' if ok else 'failed', 'note': note}
        if ok:
            return value
        best = value
    if best:
        return best
    raise ValueError('模型没有画出有效的图标，请换一种说法或从图标库中选择。')


# ------------------------------------------------------------------ for a whole layer

def _interleave(*lists):
    """The rows of several candidate lists taken in turn, each icon once."""
    rows = []; seen = set()
    for group in zip(*[row + [None] * (max(map(len, lists)) - len(row)) for row in lists]) if lists and any(lists) else []:
        for row in group:
            if row and row['id'] not in seen:
                seen.add(row['id']); rows.append(row)
    return rows


def resolve(points, online=None, allow_draw=True):
    """Glyphs for the points of one layer.

    ``points`` is a list of (text, query) pairs: what the point says, and English
    keywords for its icon. Returns (glyphs, chosen, notes): ``glyphs`` maps an id to
    a glyph record, ``chosen`` gives each point's glyph id (or None).
    """
    if online is None:
        online = online_allowed()
    # A match found online is taken only when the vision model looks at it: a search for "socket wrench" also returns power sockets.
    online = online and _config(True) is not None
    # The same words get the same icon wherever they appear (a figure and the specification it repeats).
    unique = []; position = {}
    for text, query in points:
        key = (text or query or '').strip()
        if key not in position:
            position[key] = len(unique); unique.append((text, query))
    candidates = []; fallback = []
    for text, query in unique:
        # Both the model's keywords and the point's own words are looked up: a wrong keyword ("ruler" for a load)
        # still leaves the right picture among the candidates.
        by_query = search(query, 8) if query else []; by_text = search(text, 8) if text and text != query else []
        rows = _interleave(by_query, by_text)
        if len(rows) < 3 and online:
            rows += [row for row in search_online(query or text, 8) if row['id'] not in {other['id'] for other in rows}]
        candidates.append(rows)
        # Without a model to look, the words of the point decide when they are known words; the keywords otherwise.
        fallback.append((by_text or by_query or rows or [None])[0])
    picked = pick([text for text, _ in unique], candidates)
    glyphs = {}; keys = []; notes = []; drawn = 0
    for index, (text, query) in enumerate(unique):
        rows = candidates[index]; choice = picked[index]
        row = rows[choice] if isinstance(choice, int) and 0 <= choice < len(rows) else fallback[index] if choice == -1 else None
        value = None
        if row:
            value = {key: row[key] for key in ('source', 'name', 'license', 'ops', 'stroke', 'grid')}
        elif allow_draw and drawn < 2:
            try:
                value = draw(text or query); drawn += 1          # drawn from what the point says, not from keywords that found nothing fitting
            except Exception:
                value = None
            status = (value or {}).get('check', {}).get('status')
            if status == 'failed':
                notes.append('「%s」没有合适的现成图标，AI 绘制的图形也未通过核对，已留空；可在「图标」里重新查找或绘制。' % text); value = None
            elif value is not None:
                notes.append('「%s」的图标由 AI 绘制%s。' % (text, '' if status == 'passed' else '，未经视觉模型核对，请检查'))
        if value is None:
            keys.append(None); continue
        key = next((ident for ident, existing in glyphs.items() if existing['ops'] == value['ops']), None)
        if key is None:
            key = 'g%d' % (len(glyphs) + 1); glyphs[key] = validate_glyph(value)
        keys.append(key)
    chosen = [keys[position[(text or query or '').strip()]] for text, query in points]
    return glyphs, chosen, notes


def _refused(exc):
    """An error no second attempt would cure: the service refused the request (no credit, no access, unknown model)."""
    return bool(re.search(r'HTTP (40[0-4]|429)|额度|欠费|密钥|权限|模型不存在', str(exc)))


def drawing_model():
    """The model named for drawing icons (AI_ICON_MODEL), or '' for the configured text model."""
    import os
    try:
        import ai_config
        values = ai_config.read_env()
    except Exception:
        values = {}
    return str(values.get('AI_ICON_MODEL', os.getenv('AI_ICON_MODEL', ''))).strip()[:120]


def online_allowed():
    import os
    try:
        import ai_config
        values = ai_config.read_env()
    except Exception:
        values = {}
    value = values.get('AI_ICON_ONLINE', os.getenv('AI_ICON_ONLINE', '1'))
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


def public(row):
    """What the interface gets for a candidate: enough to show and choose it."""
    return {key: row[key] for key in ('id', 'source', 'name', 'license', 'ops', 'stroke', 'grid') if key in row}

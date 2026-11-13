"""The text and icon layer of a shot: editable vector annotations over the picture.

A layer is plain data in picture coordinates (0..1 across the frame, sizes as
shares of the picture's shorter side), so it stays sharp at any resolution and
every item remains editable. It is stored per shot beside the plan, not in it:
writing a headline never makes a render stale.

    validate()   the data contract
    copy_for()   the key points worth saying: a vision model when one is
                 configured, name-based rules otherwise; never invented specs
    probe()      what each part of the picture shows, from the saved scene
    arrange()    where headline and callouts go: free areas first, callouts
                 beside the product with a line to the part they describe
    suggest()    the three above, as one proposal the user then edits

The browser draws and exports the layer (static/layer.js); this module never
rasterises text.
"""
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
TYPES = ('text', 'icon', 'callout')
MAX_ITEMS = 40
MAX_HINT = 400
MARGIN = .05
DARK, LIGHT = '#141414', '#ffffff'
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


def validate(value):
    """A clean copy of a layer, or ValueError. Only known fields survive."""
    if not isinstance(value, dict) or value.get('version', 1) != 1:
        raise ValueError('图文图层数据无效')
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
        clean.append(row)
    layer = {'version': 1, 'items': clean}
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
    (('脚轮', '万向轮', '轮子', 'caster', 'wheel'), '带轮移动', '可推到手边使用', 'wheel'),
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
COPY_PROMPT = '''You write the short key points that are placed on a product photo for an online shop. You are shown the photo and facts about the product.
Return data only:
- headline: the product in a few words, in the user's language (at most 14 characters for Chinese, 28 for Latin text).
- subline: one short supporting line, or an empty string.
- points: 2 to 5 key points a shopper should notice. title is at most 8 Chinese characters (16 Latin), detail at most 14 Chinese characters (30 Latin) or empty. icon is the closest of the available icons. parts lists the visible_parts names that this point is about, copied exactly, so a line can point at them; leave it empty when the point is about the whole product.
Say only what the photo shows or the facts state. Never give a number, size, weight, capacity, grade, certification, warranty or performance claim unless it appears in the facts, and never compare with other products. Prefer points about parts that are clearly visible in this photo. hint, when present, is what the user wants said and takes priority; keep the user's own wording and figures for it.'''


def copy_schema():
    def obj(properties):
        return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}
    point = obj({'title': {'type': 'string', 'maxLength': 40}, 'detail': {'type': 'string', 'maxLength': 80},
                 'icon': {'type': 'string', 'enum': list(ICONS)}, 'parts': {'type': 'array', 'maxItems': 40, 'items': {'type': 'string', 'maxLength': 300}}})
    return obj({'headline': {'type': 'string', 'maxLength': 60}, 'subline': {'type': 'string', 'maxLength': 80},
                'points': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': point}})


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


def copy_rules(project, hint='', visible=None):
    """Key points from what the project's own names say. Returns (copy, notes)."""
    names = _names(project); shown = set(visible) if visible is not None else None
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    cues = []
    for words, title, detail, icon in CUES:
        parts = [name for name in names if any(word in name.casefold() for word in words)]
        if not parts:
            continue
        if isinstance(title, tuple):
            material = _material_word(parts)
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
            points.append({'title': '产品尺寸', 'detail': ' × '.join('%g' % round(value, 1) for value in dimensions) + ' mm',
                           'icon': 'ruler', 'parts': []})
    headline = _clip(re.sub(r'\.(obj|glb|gltf|fbx|stl|zip)$', '', str(project.get('name', '')).strip(), flags=re.I) or '产品', 28)
    subline = _clip(metadata.get('product_category') or '', 40)
    if not subline and len(points) >= 2 and not typed:
        subline = ' · '.join(point['title'] for point in points[:3])
    notes = [] if points else ['没有从名称中识别出可介绍的部位；可在上方逐行写下要点，或手动添加文字。']
    return {'headline': headline, 'subline': subline, 'points': points}, notes


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


def copy_for(project, hint='', image_path=None, visible=None):
    """(copy, source, notes): the model's key points when there is one, the rules' otherwise."""
    rules, notes = copy_rules(project, hint, visible)
    config = _config(image_path)
    if not config:
        return rules, 'rules', notes
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    lookdev = (project['plan'].get('lookdev') or {}).get('decisions', [])
    shown = list(visible if visible is not None else _names(project))[:80]
    facts = {'product_name': str(project.get('name', ''))[:200], 'product_category': str(metadata.get('product_category', ''))[:200],
             'material_note': str(metadata.get('material_note', ''))[:500], 'real_dimensions_mm': project['plan'].get('dimensions_mm'),
             'visible_parts': shown, 'available_icons': list(ICONS),
             'surface_finishes': [{'part': str(row.get('object') or row.get('material', ''))[:80], 'finish': str(row.get('label', ''))[:40]}
                                  for row in lookdev[:30] if isinstance(row, dict)]}
    if hint:
        facts['hint'] = hint
    try:
        answer = _ask(config, COPY_PROMPT, facts, copy_schema(), image_path)
    except Exception:           # no answer is not a reason to have no layout
        return rules, 'rules', notes + ['AI 未返回可用结果，已改用名称规则。']
    allowed = set(shown)
    points = [{'title': _clip(point['title'], 16), 'detail': _clip(point['detail'], 30), 'icon': point['icon'],
               'parts': [name for name in point['parts'] if name in allowed]} for point in answer['points'] if point['title'].strip()]
    if not points:
        return rules, 'rules', notes
    return {'headline': _clip(answer['headline'], 28) or rules['headline'], 'subline': _clip(answer['subline'], 40), 'points': points[:5]}, 'ai', []


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
        luminance = [.5] * (self.nx * self.ny)
        if image_path:
            with Image.open(image_path) as image:
                luminance = [value / 255 for value in image.convert('L').resize((self.nx, self.ny), Image.Resampling.BOX).getdata()]
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

    def on_product(self, x, y):
        ix = max(0, min(self.nx - 1, math.floor(x * self.nx))); iy = max(0, min(self.ny - 1, math.floor(y * self.ny)))
        return self.cells[iy * self.nx + ix] >= 0

    def anchor(self, names):
        """A visible point of these parts to aim a line at, and how much of the picture they take."""
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
        x, y = min(best, key=lambda cell: (cell[0] - cx) ** 2 + (cell[1] - cy) ** 2)
        return [(x + .5) / self.nx, (y + .5) / self.ny], len(hits) / (self.nx * self.ny)


# ------------------------------------------------------------------ where to put it

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


LINE = 1.28          # line height as a multiple of the type size


def _tone_style(picture, rect, force_plate=False):
    mean, uneven = picture.tone(rect); product, scenery = picture.cover(rect)
    color = DARK if mean >= .56 else LIGHT
    plate = None
    if force_plate or uneven > .13 or product > .3 or .42 < mean < .62:
        plate = {'color': '#ffffff', 'opacity': .9} if color == DARK else {'color': '#111111', 'opacity': .62}
    return color, plate


def _overlap(a, b, gap=0.0):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def arrange(copy_data, picture, aspect='1:1'):
    """Items for a headline, a subline and the points, placed on this picture.

    Returns (items, titles that found no room, whether text had to sit on the product).
    """
    width, height = [float(part) for part in aspect.split(':')]
    unit = min(width, height); sx = unit / width; sy = unit / height        # a length in units, as a share of each side
    items = []; taken = []
    product = picture.rect or (.3, .3, .7, .7)

    # --- headline and subline: one block, in the freest corner or band
    best = None
    headline = copy_data.get('headline') or ''; subline = copy_data.get('subline') or ''
    if headline:
        places = (('left', 'top', 0), ('center', 'top', .03), ('right', 'top', .05), ('left', 'bottom', .09), ('center', 'bottom', .11),
                  ('right', 'bottom', .13))
        for step, size in enumerate((.066, .056, .047, .04)):
            for limit in (.62, .46, .36):
                lines = wrap(headline, size, limit / sx)
                if len(lines) > 3:
                    continue
                sub_size = size * .46
                sub_lines = wrap(subline, sub_size, limit / sx) if subline else []
                block_w = max([text_width(line, size) for line in lines] + [text_width(line, sub_size) for line in sub_lines]) * sx
                block_w = min(limit + .04, block_w * 1.08 + .01)          # slack for the browser's real glyph widths
                head_h = len(lines) * size * LINE * sy; gap = size * .3 * sy if sub_lines else 0
                block_h = head_h + gap + len(sub_lines) * sub_size * LINE * sy
                for align, edge, bias in places:
                    x0 = MARGIN if align == 'left' else 1 - MARGIN - block_w if align == 'right' else (1 - block_w) / 2
                    y0 = MARGIN if edge == 'top' else 1 - MARGIN - block_h
                    rect = (x0, y0, x0 + block_w, y0 + block_h)
                    on_product, on_scenery = picture.cover(rect)
                    score = -(9 * on_product + 2.5 * on_scenery) - picture.tone(rect)[1] * .8 - bias - step * .035 - (len(lines) - 1) * .02
                    if best is None or score > best[0]:
                        best = (score, rect, align, size, sub_size, lines, sub_lines, head_h, gap)
    if best:
        _, rect, align, size, sub_size, lines, sub_lines, head_h, gap = best
        color, plate = _tone_style(picture, rect)
        items.append({'id': 'headline', 'type': 'text', 'x': rect[0], 'y': rect[1], 'w': rect[2] - rect[0], 'text': '\n'.join(lines),
                      'size': size, 'weight': 700, 'align': align, 'color': color, 'plate': plate})
        if sub_lines:
            items.append({'id': 'subline', 'type': 'text', 'x': rect[0], 'y': rect[1] + head_h + gap, 'w': rect[2] - rect[0],
                          'text': '\n'.join(sub_lines), 'size': sub_size, 'weight': 400, 'align': align, 'color': color, 'plate': plate})
        taken.append(rect)

    # --- points: a label each; those that are about a visible part get a line to it
    points = copy_data.get('points', [])[:6]
    anchors = [picture.anchor(point.get('parts') or [])[0] if not picture.estimated else None for point in points]
    centre_x = (product[0] + product[2]) / 2

    def build(scale):
        title_size = .03 * scale; detail_size = title_size * .7; icon_size = title_size * 1.45; pad = title_size * .35
        rows = []
        for index, point in enumerate(points):
            limit = .25 / sx
            title_lines = wrap(point['title'], title_size, limit); detail_lines = wrap(point['detail'], detail_size, limit) if point.get('detail') else []
            # The browser measures real glyphs: a little slack keeps a line that fits here from breaking there.
            text_w = max([text_width(line, title_size) for line in title_lines] + [text_width(line, detail_size) for line in detail_lines]) * 1.1 + .006
            icon_w = icon_size + title_size * .4 if point.get('icon') else 0
            text_h = len(title_lines) * title_size * LINE + len(detail_lines) * detail_size * LINE
            rows.append({'id': 'point%d' % (index + 1), 'point': point, 'anchor': anchors[index], 'size': title_size,
                         'w': (icon_w + text_w + pad * 2) * sx, 'h': (max(icon_size if point.get('icon') else 0, text_h) + pad * 2) * sy})
        return rows

    def stack(side_labels, x0, inner_right, blocked):
        """Top to bottom in the order of their anchors, so no two lines cross. Returns (cost, rects) or None."""
        rects = []; cost = 0.0; floor = MARGIN
        for label in sorted(side_labels, key=lambda item: item['anchor'][1]):
            ax, ay = label['anchor']; chosen = None
            for step in range(81):
                y0 = MARGIN + (1 - 2 * MARGIN - label['h']) * step / 80
                if y0 < floor:
                    continue
                rect = (x0, y0, x0 + label['w'], y0 + label['h'])
                if any(_overlap(rect, other, .012) for other in blocked):
                    continue
                if any(point and rect[0] - .015 < point[0] < rect[2] + .015 and rect[1] - .015 < point[1] < rect[3] + .015 for point in anchors):
                    continue                                  # a label never sits on a spot that a line points at
                edge = rect[2] if inner_right else rect[0]; middle = (rect[1] + rect[3]) / 2
                length = math.hypot((ax - edge) / sx, (ay - middle) / sy)
                # A line that runs a long way across the product to reach its part reads badly: count that stretch twice.
                crossing = sum(picture.on_product(edge + (ax - edge) * t / 10, middle + (ay - middle) * t / 10) for t in range(1, 9)) / 8
                on_product, on_scenery = picture.cover(rect)
                price = length * (1 + crossing * 2.5) + on_product * 2.2 + on_scenery * 1.4
                if chosen is None or price < chosen[0]:
                    chosen = (price, rect)
            if chosen is None:
                return None
            cost += chosen[0]; rects.append((label, chosen[1])); floor = chosen[1][3] + .018
        return cost, rects

    chosen = None; labels = build(1)
    for scale in (1, .86, .74):
        rows = build(scale); anchored = [label for label in rows if label['anchor']]
        if not anchored:
            break
        widest = max(label['w'] for label in anchored)
        left_x = max(MARGIN, min(product[0] - .025 - widest, .5 - widest))
        right_x = min(1 - MARGIN - widest, max(product[2] + .025, .5))
        for mask in range(1 << len(anchored)):
            sides = [(mask >> position) & 1 for position in range(len(anchored))]         # 0 left, 1 right
            left = [label for label, side in zip(anchored, sides) if side == 0]; right = [label for label, side in zip(anchored, sides) if side == 1]
            if abs(len(left) - len(right)) > 2:
                continue
            a = stack(left, left_x, True, taken)
            b = stack(right, right_x, False, taken) if a is not None else None
            if a is None or b is None:
                continue
            # A label belongs on the side its part is on, and larger type is worth a little crowding.
            wrong = sum(1 for label, side in zip(anchored, sides) if (label['anchor'][0] > centre_x) != bool(side))
            cost = a[0] + b[0] + wrong * .35 + abs(len(left) - len(right)) * .03 + (1 - scale) * .45 * len(anchored)
            if chosen is None or cost < chosen[0]:
                chosen = (cost, a[1] + b[1], rows)
    placed = list(chosen[1]) if chosen else []
    if chosen:
        labels = chosen[2]
    pointing = {label['id'] for label, _ in placed}
    loose = [label for label in labels if label['id'] not in pointing]         # no part, or no room for its line: a plain label
    for label, rect in placed:
        taken.append(rect)

    # --- points without a part: a column under the headline, or a row along the freest edge
    if loose:
        options = []
        gap = .02
        column_h = sum(label['h'] for label in loose) + gap * (len(loose) - 1); column_w = max(label['w'] for label in loose)
        head = taken[0] if items else None
        starts = []
        if head:
            starts.append((head[0] if head[0] < .5 else head[2] - column_w, head[3] + .03))
        starts += [(MARGIN, 1 - MARGIN - column_h), (1 - MARGIN - column_w, 1 - MARGIN - column_h), (MARGIN, MARGIN), (1 - MARGIN - column_w, MARGIN)]
        for position, (x0, y0) in enumerate(starts):
            if y0 < MARGIN or y0 + column_h > 1 - MARGIN + 1e-6:
                continue
            rects = []; y = y0
            for label in loose:
                rects.append((label, (x0, y, x0 + label['w'], y + label['h']))); y += label['h'] + gap
            if any(_overlap(rect, other, .01) for _, rect in rects for other in taken):
                continue
            cost = sum(picture.cover(rect)[0] * 3.2 + picture.cover(rect)[1] for _, rect in rects) / len(rects) + position * .03
            options.append((cost, rects))
        row_w = sum(label['w'] for label in loose) + .03 * (len(loose) - 1); row_h = max(label['h'] for label in loose)
        if row_w <= 1 - 2 * MARGIN:
            for position, y0 in enumerate((1 - MARGIN - row_h, MARGIN)):
                rects = []; x = (1 - row_w) / 2
                for label in loose:
                    rects.append((label, (x, y0, x + label['w'], y0 + label['h']))); x += label['w'] + .03
                if any(_overlap(rect, other, .01) for _, rect in rects for other in taken):
                    continue
                cost = sum(picture.cover(rect)[0] * 3.2 + picture.cover(rect)[1] for _, rect in rects) / len(rects) + .02 + position * .05
                options.append((cost, rects))
        if options:
            placed += min(options, key=lambda option: option[0])[1]

    for label, rect in placed:
        point = label['point']; color, plate = _tone_style(picture, rect)
        items.append({'id': label['id'], 'type': 'callout', 'x': rect[0], 'y': rect[1], 'w': rect[2] - rect[0], 'title': point['title'],
                      'detail': point.get('detail') or '', 'icon': point.get('icon'), 'anchor': label['anchor'] if label['id'] in pointing else None,
                      'size': label['size'], 'color': color, 'plate': plate})
    dropped = [label['point']['title'] for label in labels if label['id'] not in {item['id'] for item in items}]
    crowded = any(picture.cover(rect)[0] > .12 for rect in taken) or any(picture.cover(rect)[0] > .12 for _, rect in placed)
    return items, dropped, crowded


# ------------------------------------------------------------------ the proposal

def suggest(project_id, shot_id, body):
    """A proposed layer for this image: what to say and where. Nothing is saved."""
    if not isinstance(body, dict):
        raise ValueError('图文图层请求格式无效')
    hint = body.get('hint', '')
    if not isinstance(hint, str) or len(hint) > MAX_HINT:
        raise ValueError('要点说明不能超过 %d 字' % MAX_HINT)
    project = get('project', project_id); shot = _shot(project, shot_id)
    if not project.get('analysis'):
        raise ValueError('请先等待模型分析完成')
    wanted = body.get('output') if isinstance(body.get('output'), dict) else {}
    outputs = [o for o in project.get('outputs', []) if o.get('shot_id') == shot_id and o.get('quality') != 'thumbnail'
               and (not wanted or all(o.get(key) == wanted.get(key) for key in ('job_id', 'version', 'quality')))]
    if not outputs:
        raise ValueError('请先生成这个镜头的预览，再排版图文。')
    output = max(outputs, key=lambda o: (o.get('version', 0), o.get('created_at') or 0))
    image = _project_file(project_id, output.get('png_url'), ('.png',))
    if image is None:
        raise ValueError('这张图片的文件不存在，请重新生成预览。')
    scene = probe(project_id, output)
    notes = []
    if scene is None:
        width, height = output.get('width') or 1, output.get('height') or 1
        scene = probe_from_image(image, max(8, round(GRID * width / max(width, height))), max(8, round(GRID * height / max(width, height))))
        notes.append('无法读取这张图片保存的场景，按画面颜色估计了产品位置，标注没有指向线。')
    picture = Picture(scene, image)
    visible = None if picture.estimated else list(picture.objects)
    content, source, copy_notes = copy_for(project, hint.strip(), image, visible)
    items, dropped, crowded = arrange(content, picture, '%d:%d' % (output.get('width') or 1, output.get('height') or 1))
    if dropped:
        notes.append('画面放不下：' + '、'.join(dropped[:4]) + '。')
    if crowded:
        notes.append('画面较满，部分文字压在了产品上；需要更多版面时可在「调整」里输入“增加留白”后重新预览。')
    layer = validate({'version': 1, 'items': items, 'basis': basis(project['plan'], shot), 'source': source})
    return {'layer': layer, 'report': {'source': source, 'probe': 'image' if picture.estimated else 'scene', 'notes': copy_notes + notes,
                                       'anchored': sum(1 for item in items if item.get('anchor')),
                                       'output': {key: output.get(key) for key in ('job_id', 'version', 'quality')}}}

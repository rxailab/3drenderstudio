"""The listing set, laid out with the brand-kit templates (``brandkit/templates``).

The listing's pictures come from the product's own 3D model: a cut-out of the whole product, close-ups
of the parts its selling points name, a straight-on view to measure, the kit's components. Its usage
scenes are made by the image model from those renders, with the product locked by a mask
(``scene_lock``), so scene generation is now one step of building the set instead of a tool of its own.

This module turns those pictures and the product record into jobs for the templates, lays every page
out in a headless browser (``brandkit_render``), checks the measured layout, and falls back to a
lighter version of a page (fewer labels, another arrangement) when a check fails.

Templates (each one rule-driven HTML; see ``brandkit/templates/T*.json`` for its reference map):
T12 poster · T01 callouts · T03 product + detail column · T13 poses (the product in the states of its moving parts:
pliers closed / open, a caliper closed / measuring / open, from the pose agent ``articulation``) · T02 dark close-ups ·
T04 scene + figures band · T08 usage scenes · T06 dimensions · T05 in the box. T07, T09, T10 and T11 need evidence a 3D
model does not carry (module levels, test results, connection maps, a model range) and are not built.
"""
import copy
import hashlib
import json
import math
import re
import shutil
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import brandkit_assets
import brandkit_render

# kind: (zh, en)
KINDS = {
    'T12': ('海报主图', 'Hero poster'),
    'T01': ('功能标注', 'Callouts'),
    'T03': ('主体 + 细节栏', 'Product + details'),
    'T13': ('多姿态展示', 'Poses'),
    'T02': ('深色细节特写', 'Close-ups'),
    'T04': ('场景 + 参数带', 'Scene + key figures'),
    'T08': ('使用场景组图', 'Usage scenes'),
    'T06': ('尺寸图', 'Dimensions'),
    'T05': ('包装清单', 'In the box'),
    'T07': ('模块系统', 'Modules'),
    'T09': ('同条件对比', 'Comparison'),
    'T10': ('接口与连接', 'Connections'),
    'T11': ('同系列变体', 'Variants'),
}
ORDER = tuple(KINDS)
AUTO = ('T12', 'T01', 'T03', 'T13', 'T02', 'T04', 'T08', 'T06', 'T05')
# Pages that need facts a model does not carry. They are listed so the user sees why they are not made.
BLOCKED = {'T07': '需要每个模块单独的模型和它们的叠放层级', 'T09': '需要同条件测试的对比证据', 'T10': '需要已确认的连接关系',
           'T11': '需要同系列其他型号的模型'}
CANVAS = {'1:1': (1500, 1500), '4:5': (1200, 1500)}
ASPECTS = tuple(CANVAS)
MAX_SCENES = 3
SCENE_PEOPLE = ('auto', 'hands', 'include', 'exclude')
TEXT = {
    'zh': {'T01_eyebrow': '部件一览', 'T01_headline': '每个关键部件，\n一眼看清', 'T03_eyebrow': '细节', 'T02_eyebrow': '细节特写',
           'T02_headline': '近看细节', 'T04_headline': '核心参数', 'T04_eyebrow': '参数', 'T08_eyebrow': '使用场景', 'T08_headline': '用在需要它的地方',
           'T06_eyebrow': '尺寸', 'T06_headline': '外形尺寸', 'T05_headline': '包装清单', 'T05_note': '图示物品未按实际比例',
           'qty_pending': '数量待确认', 'qty_prefix': '数量 ', 'main': '主件', 'height': '高度', 'width': '宽度', 'length': '长度', 'depth': '深度', 'thickness': '厚度', 'diameter': '直径',
           'table_note': '尺寸为近似值，以实物为准。', 'depth_note': '深度 %s。',
           'T13_eyebrow': '活动部件', 'T13_headline': '一件工具，多种状态', 'T13_from_to': '从%s\n[[到%s]]',
           'scene_titles': ('上手使用', '随时待命', '换个场景')},
    'en': {'T01_eyebrow': 'Part overview', 'T01_headline': 'Every key part,\nclearly shown', 'T03_eyebrow': 'Details', 'T02_eyebrow': 'Up close',
           'T02_headline': 'The details, up close', 'T04_headline': 'Key figures', 'T04_eyebrow': 'Specs', 'T08_eyebrow': 'In use',
           'T08_headline': 'Made for the job', 'T06_eyebrow': 'Dimensions', 'T06_headline': 'Overall size', 'T05_headline': "What's in the box",
           'T05_note': 'Items not shown to scale', 'qty_pending': 'Qty to be confirmed', 'qty_prefix': 'Qty ', 'main': 'Main unit',
           'height': 'Height', 'width': 'Width', 'length': 'Length', 'depth': 'Depth', 'thickness': 'Thickness', 'diameter': 'Diameter',
           'table_note': 'Sizes are approximate; actual product may vary slightly.', 'depth_note': 'Depth %s.',
           'T13_eyebrow': 'Moving parts', 'T13_headline': 'One tool, every position', 'T13_from_to': '%s\n[[to %s]]',
           'scene_titles': ('In use', 'Ready to go', 'On the job')},
}


# ---- the scene settings kept on the product record ----

def default_scenes():
    return {'enabled': True, 'count': MAX_SCENES, 'audience': 'auto', 'people': 'auto', 'hint': ''}


def validate_scenes(value, current=None):
    """The listing's scene settings: whether the image model makes scenes for it, how many, for whom, with or without people."""
    import scene_audience
    merged = {**default_scenes(), **(current or {}), **(value if isinstance(value, dict) else {})}
    if value is not None and not isinstance(value, dict):
        raise ValueError('场景设置格式无效')
    if type(merged['enabled']) is not bool:
        raise ValueError('场景开关必须是 true 或 false')
    count = merged['count']
    if isinstance(count, bool) or not isinstance(count, (int, float)) or int(count) != count or not 1 <= count <= MAX_SCENES:
        raise ValueError('场景图数量必须是 1 到 %d' % MAX_SCENES)
    audience = merged['audience'] or 'auto'
    if audience != 'auto' and not scene_audience.segment(audience):
        raise ValueError('所选人群不在参考库中。')
    if merged['people'] not in SCENE_PEOPLE:
        raise ValueError('人物选项无效')
    hint = merged.get('hint') or ''
    if not isinstance(hint, str):
        raise ValueError('场景说明必须是文字')
    hint = re.sub(r'[\x00-\x1f\x7f]+', ' ', hint).strip()
    if len(hint) > 300:
        raise ValueError('场景说明不能超过 300 字')
    return {'enabled': merged['enabled'], 'count': int(count), 'audience': audience, 'people': merged['people'], 'hint': hint}


def image_model_ready():
    try:
        import scene_imagegen
        return scene_imagegen.config() is not None
    except Exception:
        return False


# ---- which pages ----

def anchored(product, whole=None):
    """Indexes of the selling points that name parts of the model (they can be pointed at and shown close up). A selling
    point that names every part of the model (a model in one piece) is about the whole product, not a part of it."""
    whole = set(whole or product.get('_model_parts') or ())
    return [index for index, feature in enumerate(product['features'])
            if feature.get('parts') and not (whole and set(feature['parts']) >= whole)]


def pointed(product):
    """Indexes of the selling points that were found on the main picture (``point``) without a part of their own."""
    have = set(anchored(product))
    return [index for index, feature in enumerate(product['features']) if index not in have and feature.get('point')]


# ---- words a page may print ----

MODEL_SUFFIXES = r'\.(?:glb|gltf|fbx|obj|stl|usdz?|blend|3ds|dae|ply|zip)$'
# Tokens a file name carries that are not part of the product's name: versions, exports, scan and texture labels.
FILE_TOKENS = re.compile(r'^(?:v\d+|rev\d*|r\d+|final|copy|export(?:ed)?|model|mesh|scan(?:ned)?|baked|low(?:poly)?|high(?:poly)?|lod\d*|untitled|'
                         r'new|draft|test|tmp|temp|\d+k|glb|gltf|fbx|obj|stl|usdz?|blend|zip)$', re.I)
SMALL_WORDS = {'a', 'an', 'and', 'or', 'of', 'for', 'with', 'the', 'to', 'in', 'on', 'by', 'at', 'x'}


def humanised(text):
    """The product's name as a page prints it. A title that is still the uploaded file's stem ("06_diagonal_cutting_pliers")
    loses its numbering, extension and file words (v2, final, lowpoly), its underscores become spaces and an all-lower or
    all-upper name is set in title case ("Diagonal Cutting Pliers"). A name the seller wrote (spaces, mixed case, no
    underscore or leading number) is returned as it is: "DeWalt DCD777" stays."""
    text = ' '.join(str(text or '').split())
    stem = re.sub(MODEL_SUFFIXES, '', text, flags=re.I)
    if stem == text and '_' not in stem and not re.match(r'^\d+[\s._-]+\S', stem):
        return text
    if ' ' not in stem and '_' not in stem and stem.count('-') >= 2:
        stem = stem.replace('-', ' ')            # "06-diagonal-cutting-pliers"
    # The upload's numbering ("06_", "3.", a zero-padded "06 "), not a number that is part of the name ("3-in-1").
    stem = re.sub(r'^0\d*\s+(?=\S)', '', re.sub(r'^(?:\d+[_.]+)+(?=\S)', '', stem))
    words = [word for word in re.split(r'[\s_]+', stem) if word.strip('-.')]
    words = [word for word in words if not FILE_TOKENS.match(word)] or words
    while len(words) > 1 and re.fullmatch(r'0\d*', words[-1]):
        words.pop()                              # a copy number: "pliers_01"
    letters = [word for word in words if re.search(r'[A-Za-z]', word)]
    if letters and (all(word == word.lower() for word in letters) or all(word == word.upper() for word in letters)):
        words = [word.lower() if index and word.lower() in SMALL_WORDS else word[:1].upper() + word[1:].lower() if re.search(r'[A-Za-z]', word) else word
                 for index, word in enumerate(words)]
    return ' '.join(words)


# Names a 3D program gives the shapes it makes: a selling point carrying one was made from the file, not from the seller.
PRIMITIVES = {'cylinder', 'cube', 'plane', 'sphere', 'uvsphere', 'icosphere', 'circle', 'torus', 'cone', 'mesh', 'object', 'polysurface',
              'geometry', 'primitive', 'untitled', 'node', 'null', 'empty', 'bezier', 'nurbs', 'instance'}


def _words(text):
    return re.findall(r'[a-z]+|[一-鿿]+', unicodedata.normalize('NFKC', str(text or '')).lower())


def _mesh_key(name):
    """A part name without its numbering and punctuation: "Cylinder.002" and "cylinder" are one name."""
    tokens = re.split(r'[^0-9a-z一-鿿]+', unicodedata.normalize('NFKC', str(name or '')).lower())
    return ' '.join(token for token in tokens if token and not token.isdigit())


def mesh_named(title, product, strict=True):
    """Why a selling point's title is a name out of the 3D file rather than the seller's words, or None.

    Pliers uploaded without a description were printed with "Cylinder grip" pointing at the rivet: the drafter had made a
    selling point out of the mesh "Cylinder". A title is a mesh name when it carries a file name's marks ("Handle_L",
    "Cube.001"), is one of the model's own part names, or (``strict``) uses a 3D program's shape name ("Cylinder",
    "Plane") that nothing the seller wrote (the pasted description, the headline, the subline) uses. The page builders
    ask without ``strict``: by then the line may be a translation of the seller's own words."""
    raw = ' '.join(str(title or '').split())
    if not raw:
        return None
    # "Cube.001" (a letter before the dot), not a measurement such as "0.01 mm accuracy".
    if '_' in raw or re.search(r'[A-Za-z]\.\d{2,}\b', raw):
        return '带有文件名的写法'
    told = ' '.join(str(product.get(key) or '') for key in ('source', 'headline', 'subline'))
    key = _mesh_key(raw)
    for name in product.get('_model_parts') or ():
        if key and key == _mesh_key(name) and raw.lower() not in told.lower():
            return '是模型里的部件名 %s' % name
    if strict:
        said = set(_words(told))
        shapes = [word for word in _words(raw) if word in PRIMITIVES and word not in said]
        if shapes:
            return '用的是三维软件给形状起的名字 %s' % shapes[0].capitalize()
    return None


NO_FEATURES = '没有填写卖点。请在「产品信息」里写下卖点（或粘贴产品介绍后点「提取到各栏」），这一页才会生成'


def printable(product):
    """(product, notes): the record with only the selling points a page may print. A point whose title is a mesh name
    (``mesh_named``) is left out and said, so the set never labels a part with what the file called it.

    Only a record that was drafted or extracted (``drafted``) can carry a point the seller did not write. Points typed by
    hand into a record that was never drafted are the seller's own: "Hydraulic cylinder" or a part named "Trigger" stays,
    and only a file name's marks ("Handle_L", "Cube.001") are refused there."""
    kept, notes = [], []
    machine = bool(product.get('drafted'))
    for row in product.get('features') or []:
        why = mesh_named(row.get('title'), product) if machine else mesh_named(row.get('title'), {}, strict=False)
        if why:
            notes.append('卖点「%s」%s，不是你写的卖点，没有印到图上；请在「产品信息」里改成产品真正的卖点。' % (row.get('title'), why))
        else:
            kept.append(row)
    if len(kept) == len(product.get('features') or []):
        return product, notes
    return {**product, 'features': kept}, notes


LOCATOR = ('You look at a product photo for an online listing and say where each selling point is on the product. For each '
           'numbered selling point, give the point (x, y as fractions of the picture width and height, from the top left) at the '
           'centre of the part it is about: a display, a button, a jaw, a cutter, a handle, a battery, a guard. A feature that a part '
           'delivers points at that part: measuring modes at the jaws and depth rod, a unit switch or zero at its button, a motor at '
           'the motor housing, a material at the part made of it. Only when no part shows it (an accuracy, a warranty, a speed with no '
           'visible source) or that part cannot be seen from this side, set visible to false.')


def locate(picture_path, picture, product, cache):
    """Where the selling points without a usable part are on the main picture, by the vision model: {index: point}, with
    the mesh under the point as the feature's part when the model has more than one. Only points the part grid confirms are on
    the product are kept. ``cache`` ({key: answer}) keeps the answer with the project. Returns (features, notes)."""
    features = [dict(row) for row in product['features']]
    wanted = [index for index in range(len(features)) if index not in anchored(product) and features[index].get('title')]
    if not wanted or picture is None:
        return features, []
    key = hashlib.sha256(json.dumps([str(picture_path), [features[index]['title'] for index in wanted]], ensure_ascii=False).encode()).hexdigest()[:16]
    answer = cache.get(key)
    if answer is None:
        try:
            import brandkit_review
            _, vision = brandkit_review._configs()
        except Exception:
            vision = None
        if not vision:
            return features, []
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['points'],
                  'properties': {'points': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                                     'required': ['number', 'visible', 'x', 'y'],
                                                                     'properties': {'number': {'type': 'integer'}, 'visible': {'type': 'boolean'},
                                                                                    'x': {'type': 'number'}, 'y': {'type': 'number'}}}}}}
        try:
            answer = brandkit_review._ask(vision, LOCATOR, {'product': product.get('title', ''), 'selling_points': [
                {'number': number + 1, 'title': features[index]['title'], 'detail': features[index].get('detail', '')} for number, index in enumerate(wanted)]},
                schema, image_path=str(picture_path))
        except Exception as exc:
            return features, ['没能在主图上找出卖点的位置（%s），这些卖点不画引线。' % str(exc)[:80]]
        cache[key] = answer
    several = len(set(name for name in picture.objects)) > 1
    found = 0
    for row in answer.get('points') or []:
        number = row.get('number')
        if not isinstance(number, int) or not 1 <= number <= len(wanted) or not row.get('visible'):
            continue
        x, y = float(row.get('x', -1)), float(row.get('y', -1))
        if not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        cell = picture.cells[min(picture.ny - 1, int(y * picture.ny)) * picture.nx + min(picture.nx - 1, int(x * picture.nx))]
        if not isinstance(cell, int) or cell < 0 or cell >= len(picture.objects):
            continue                     # not on the product: a guess the part grid does not back
        index = wanted[number - 1]
        features[index]['point'] = [round(x, 4), round(y, 4)]
        if several:
            features[index]['parts'] = [picture.objects[cell]]
        found += 1
    return features, (['%d 条卖点的位置由视觉模型在主图上找出。' % found] if found else [])


POINT_CHECK = ('You check the callout points of a product photo for an online listing before the lines are drawn. The picture has '
               'numbered markers: a ring whose centre is the point, with its number beside it. For each numbered point look at the '
               'exact spot at the ring centre and say whether it lies on the part of the product the selling point given with that '
               'number is about (on: true), or somewhere else: another part, the joint between two parts, the background (on: false). '
               'Judge by the selling point\'s own words: a cutting edge is the sharpened edge of the jaws, a grip or handle is the '
               'handle\'s covering, a pivot is the rivet or joint, a display is the screen. When a point is wrong, give x, y (fractions '
               'of the picture width and height from the top left, ignoring the markers) at the centre of the right part, or visible '
               'false when that part cannot be seen from this side. When you cannot tell, answer on: false.')


def candidate_points(product, picture):
    """[(feature index, point on the main picture)]: every spot a callout line would be drawn to, as the build draws them
    (a point found on the picture, else the visible middle of the feature's parts)."""
    out = []
    for index in anchored(product):
        feature = product['features'][index]
        if feature.get('point'):
            out.append((index, [float(value) for value in feature['point']]))
            continue
        found, share = picture.anchor(feature['parts']) if picture else (None, 0)
        if found and share > .0003:
            out.append((index, [float(value) for value in found]))
    out += [(index, [float(value) for value in product['features'][index]['point']]) for index in pointed(product)]
    return sorted(out)


def _marked(picture_path, candidates, target):
    """The main picture with each candidate point ringed and numbered, for the vision model (and the user) to check."""
    from PIL import ImageFont
    with Image.open(picture_path) as opened:
        image = opened.convert('RGBA')
    ground = Image.new('RGBA', image.size, (255, 255, 255, 255)); ground.alpha_composite(image); image = ground.convert('RGB')
    image.thumbnail((1280, 1280))
    width, height = image.size; pen = ImageDraw.Draw(image)
    radius = max(10, round(min(width, height) * .022)); size = max(18, round(min(width, height) * .032))
    try:
        font = ImageFont.truetype(str(Path(__file__).resolve().parent / 'brandkit' / 'fonts' / 'Archivo-Variable.ttf'), size)
    except OSError:
        font = ImageFont.load_default(size)
    for number, (_, point) in enumerate(candidates, 1):
        x, y = point[0] * width, point[1] * height
        pen.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(230, 20, 60), width=max(3, radius // 4))
        pen.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(230, 20, 60))
        label = str(number); box = pen.textbbox((0, 0), label, font=font)
        lx = x + radius + 4 if x + radius + 8 + box[2] < width else x - radius - 8 - box[2]
        ly = min(max(0, y - radius - box[3]), height - box[3] - 4)
        pen.rectangle((lx - 4, ly - 2, lx + box[2] + 4, ly + box[3] + 4), fill=(230, 20, 60))
        pen.text((lx, ly), label, fill=(255, 255, 255), font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, 'PNG')
    return target


def check_points(picture_path, picture, product, original, cache, folder):
    """Every callout point checked once by the vision model before a line is drawn to it.

    The pliers' "Angled cutting edges" line ended at the joint of jaw and handle and "Cylinder grip" at the rivet: the
    locator's guess and a mesh's middle were drawn as they came. One call now sees the main picture with all candidate
    points ringed and numbered (``_marked``) and says per point whether it is on the named feature. A point it rejects is
    dropped; the corrected spot it gives is taken only where the feature's own part is under it (the part grid backs it).
    A dropped feature keeps the parts the user chose (its close-ups) but gets no line (``no_leader``); one the locator
    placed goes back to how the user wrote it. The answer is kept in ``cache`` (the listing's ``located``), keyed by the
    picture and the points, so a rebuild asks nothing. ``original`` is the features before ``locate``.
    Returns (features, notes)."""
    features = [dict(row) for row in product['features']]
    candidates = candidate_points(product, picture)
    if not candidates or picture is None:
        return features, []
    key = 'check-' + hashlib.sha256(json.dumps([str(picture_path), [(features[index]['title'], [round(value, 3) for value in point])
                                                                    for index, point in candidates]], ensure_ascii=False).encode()).hexdigest()[:16]
    answer = cache.get(key)
    if answer is None:
        try:
            import brandkit_review
            _, vision = brandkit_review._configs()
        except Exception:
            vision = None
        if not vision:
            return features, []
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['points'],
                  'properties': {'points': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                                     'required': ['number', 'on', 'visible', 'x', 'y'],
                                                                     'properties': {'number': {'type': 'integer'}, 'on': {'type': 'boolean'},
                                                                                    'visible': {'type': 'boolean'}, 'x': {'type': 'number'},
                                                                                    'y': {'type': 'number'}}}}}}
        marked = _marked(picture_path, candidates, Path(folder) / 'points-check.png')
        try:
            answer = brandkit_review._ask(vision, POINT_CHECK, {'product': humanised(product.get('title', '')), 'points': [
                {'number': number, 'selling_point': features[index]['title'], 'detail': features[index].get('detail', '')}
                for number, (index, _) in enumerate(candidates, 1)]}, schema, image_path=str(marked))
        except Exception as exc:
            return features, ['没能核对引线位置（%s），引线未经核对。' % str(exc)[:80]]
        if not isinstance(answer, dict) or not isinstance(answer.get('points'), list):
            return features, ['核对引线位置没有得到可用的回答，引线未经核对。']
        cache[key] = answer
    verdicts = {row.get('number'): row for row in answer.get('points') or [] if isinstance(row, dict)}
    dropped, moved = [], 0
    for number, (index, point) in enumerate(candidates, 1):
        row = verdicts.get(number) or {}
        if row.get('on') is True:
            continue
        own = set((original[index] if index < len(original) else {}).get('parts') or features[index].get('parts') or ())
        try:
            x, y = float(row.get('x', -1)), float(row.get('y', -1))
        except (TypeError, ValueError):
            x = y = -1
        if row.get('visible') and own and 0 <= x <= 1 and 0 <= y <= 1:
            cell = picture.cells[min(picture.ny - 1, int(y * picture.ny)) * picture.nx + min(picture.nx - 1, int(x * picture.nx))]
            if isinstance(cell, int) and 0 <= cell < len(picture.objects) and picture.objects[cell] in own:
                features[index]['point'] = [round(x, 4), round(y, 4)]; moved += 1
                continue
        dropped.append(features[index]['title'])
        before = original[index] if index < len(original) else {}
        features[index].pop('point', None)
        features[index]['parts'] = list(before.get('parts') or [])
        features[index]['no_leader'] = True
    notes = []
    if dropped:
        notes.append('视觉模型核对引线时发现 %d 处没有指在所写的部位上，已去掉这些引线：%s。' % (len(dropped), '、'.join('「%s」' % title for title in dropped[:4])))
    if moved:
        notes.append('%d 条引线按核对结果移到了对应部件上。' % moved)
    return features, notes


def requirements(product, parts, size_known, scene_supply, poses=0):
    """(has, why): whether each page has what it needs, and the reason when it does not. ``poses``: how many states of its
    moving parts the product can be shown in (the pose agent's, ``articulation.states``) or pose pictures there are."""
    points = len(anchored(product))
    callouts = len([index for index in anchored(product) if not product['features'][index].get('no_leader')]) + len(pointed(product))
    figures = bool(product['figures'] or product['specs'] or size_known)
    has = {'T12': True, 'T01': callouts >= 2, 'T03': points >= 2, 'T13': poses >= 2, 'T02': points >= 1, 'T04': scene_supply >= 1 and figures,
           'T08': scene_supply >= 2, 'T06': size_known, 'T05': bool(parts), 'T07': False, 'T09': False, 'T10': False, 'T11': False}
    single = len(set(product.get('_model_parts') or ())) == 1
    why = {'T01': '至少 2 条卖点需要选择对应部件，或配置视觉模型在主图上找出它们的位置',
           'T03': '模型是一个整体，没有可单独特写的部件' if single else '至少 2 条卖点需要选择对应部件',
           'T02': '模型是一个整体，没有可单独特写的部件' if single else '卖点没有选择对应的部件',
           'T04': '需要场景图（开启 AI 场景或工件图），以及规格、大数字指标或真实尺寸' if not figures else '需要场景图：请开启 AI 场景图或工件使用图',
           'T08': '需要至少 2 张场景图：请开启 AI 场景图（2 张以上）或工件使用图', 'T06': '没有填写真实尺寸', 'T05': '还没有处理完成的组件',
           'T13': '没有识别出可以展示的活动部件（需要至少 2 个姿态；可在「活动部件」里查看或重新识别）', **BLOCKED}
    if not any(str(row.get('title') or '').strip() for row in product['features']):
        # Nothing the seller wrote to print: the pages that print selling points wait for them; none is made up.
        why.update(T01=NO_FEATURES, T02=NO_FEATURES, T03=NO_FEATURES)
    return has, why


def scene_supply(product, legacy=0):
    """How many scene pictures a build can count on: from the image model, the workpiece render and earlier scenes."""
    settings = product.get('scenes') or default_scenes()
    made = settings['count'] if settings['enabled'] and image_model_ready() else 0
    return made + (1 if product['usecase']['enabled'] else 0) + (legacy if not made else 0)


# ---- pictures into template assets ----

def _hull(points):
    points = sorted(set(points))
    if len(points) < 3:
        return points
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def shape(mask):
    """Geometry of a product mask (L image, product > 0) for the templates: content box, convex hull,
    long axis and half its thickness, all as fractions of the picture."""
    width, height = mask.size
    step = max(1, round(max(width, height) / 220))
    small = mask.resize((max(1, width // step), max(1, height // step)), Image.Resampling.BOX)
    sw, sh = small.size; data = small.load()
    points = [(x, y) for y in range(sh) for x in range(sw) if data[x, y] > 40]
    box = mask.point(lambda value: 255 if value > 24 else 0).getbbox() or (0, 0, width, height)
    record = {'content_bbox': [round(box[0] / width, 4), round(box[1] / height, 4), round(box[2] / width, 4), round(box[3] / height, 4)]}
    if len(points) < 12:
        return record
    record['hull'] = [[round((x + .5) / sw, 4), round((y + .5) / sh, 4)] for x, y in _hull(points)]
    # The long axis, in pixels so a wide picture is not skewed.
    xs = [(x + .5) * step for x, _ in points]; ys = [(y + .5) * step for _, y in points]
    cx = sum(xs) / len(xs); cy = sum(ys) / len(ys)
    sxx = sum((x - cx) ** 2 for x in xs); syy = sum((y - cy) ** 2 for y in ys); sxy = sum((x - cx) * (y - cy) for x, y in zip(xs, ys))
    angle = .5 * math.atan2(2 * sxy, sxx - syy); ux, uy = math.cos(angle), math.sin(angle)
    along = [(x - cx) * ux + (y - cy) * uy for x, y in zip(xs, ys)]
    across = sorted(abs(-(x - cx) * uy + (y - cy) * ux) for x, y in zip(xs, ys))
    low, high = min(along), max(along)
    record['axis'] = [[round((cx + low * ux) / width, 4), round((cy + low * uy) / height, 4)], [round((cx + high * ux) / width, 4), round((cy + high * uy) / height, 4)]]
    record['half_width'] = round(min(.45, max(.04, across[int(len(across) * .92)] / width)), 4)
    record['axis_deg'] = round(math.degrees(angle), 2)
    return record


def flatten(source, target, pad=.03, ground=(255, 255, 255), dark=False, trim=True, region=None):
    """A render with a transparent background, trimmed to the product and laid on a plain ground, as the
    templates expect (they multiply a light ground away and test text against non-white pixels).

    Returns (asset fields, crop box in source fractions).
    """
    with Image.open(source) as opened:
        image = opened.convert('RGBA')
    alpha = image.getchannel('A')
    box = alpha.point(lambda value: 255 if value > 8 else 0).getbbox() or (0, 0, image.width, image.height)
    px = round(max(box[2] - box[0], box[3] - box[1]) * pad)
    # A close-up is already framed on its part by the camera, and the templates crop it to their frames themselves:
    # it keeps every pixel so a large frame is not filled by enlarging it.
    crop = (max(0, box[0] - px), max(0, box[1] - px), min(image.width, box[2] + px), min(image.height, box[3] + px)) if trim else (0, 0, image.width, image.height)
    if region:          # a close-up: the window round the parts it is about (fractions of the render)
        crop = (round(region[0] * image.width), round(region[1] * image.height), round(region[2] * image.width), round(region[3] * image.height))
    piece = image.crop(crop)
    if dark:
        base = Image.new('RGB', piece.size, ground)
        glow = Image.new('L', piece.size, 0)
        ImageDraw.Draw(glow).ellipse((-piece.width * .2, -piece.height * .25, piece.width * 1.2, piece.height * 1.1), fill=70)
        base = Image.composite(Image.new('RGB', piece.size, (78, 83, 90)), base, glow.filter(ImageFilter.GaussianBlur(max(piece.size) / 6)))
    else:
        base = Image.new('RGB', piece.size, ground)
    base.paste(piece, mask=piece.getchannel('A'))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    base.save(target, 'PNG', optimize=True)
    fields = brandkit_render.asset(target)
    if not dark:
        fields.update(shape(piece.getchannel('A')))
    width, height = image.size
    return fields, (crop[0] / width, crop[1] / height, crop[2] / width, crop[3] / height)


def pose_assets(sources, folder, pad=.03):
    """The renders of one product in its poses (one camera, framed alike by the articulation envelope) as template assets
    cut with ONE window, the union of every pose's product plus a margin: the pictures stay the same size and scale, so
    the parts that do not move sit in the same place in each (T13 lays them out at one shared scale)."""
    boxes = []; size = None
    for source in sources:
        with Image.open(source) as opened:
            image = opened.convert('RGBA')
        size = size or image.size
        if image.size != size:            # not one camera: each pose keeps its own crop
            boxes = None; break
        box = image.getchannel('A').point(lambda value: 255 if value > 8 else 0).getbbox()
        if box:
            boxes.append(box)
    region = None
    if boxes:
        x0, y0 = min(box[0] for box in boxes), min(box[1] for box in boxes)
        x1, y1 = max(box[2] for box in boxes), max(box[3] for box in boxes)
        px = round(max(x1 - x0, y1 - y0) * pad); width, height = size
        region = (max(0, x0 - px) / width, max(0, y0 - px) / height, min(width, x1 + px) / width, min(height, y1 + px) / height)
    out = []
    for index, source in enumerate(sources):
        fields, _ = flatten(source, Path(folder) / ('pose%d.png' % index), pad=pad, trim=region is None, region=region)
        out.append(fields)
    return out


def _visible_patch(probe, names):
    """The box (fractions) of the largest connected patch where these parts are visible in a render, from its probe."""
    if not isinstance(probe, dict) or not probe.get('cells') or not probe.get('nx'):
        return None
    nx, ny = probe['nx'], probe['ny']; wanted = {index for index, name in enumerate(probe.get('objects', [])) if name in set(names)}
    remaining = {(index % nx, index // nx) for index, cell in enumerate(probe['cells']) if cell in wanted}
    best = []
    while remaining:
        stack = [remaining.pop()]; patch = []
        while stack:
            x, y = stack.pop(); patch.append((x, y))
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in remaining:
                    remaining.discard(neighbour); stack.append(neighbour)
        if len(patch) > len(best):
            best = patch
    if len(best) < 2:
        return None
    return [min(x for x, _ in best) / nx, min(y for _, y in best) / ny, (max(x for x, _ in best) + 1) / nx, (max(y for _, y in best) + 1) / ny]


def part_region(result, names, least=.42, margin=.3, inset=0.0):
    """The window of a close-up render that shows these parts: the largest patch where they are visible (one rivet of
    four, the near handle), widened by ``margin`` of its size, made square and at least ``least`` of the picture across
    (so a frame is not filled by enlarging a few pixels). Fractions of the picture; None when the render does not say
    where the parts are."""
    box = _visible_patch(result.get('probe'), names)
    if box is None:
        wanted = set(names)
        rects = [row['rect'] for row in result.get('parts') or [] if row.get('name') in wanted and isinstance(row.get('rect'), list) and len(row['rect']) == 4]
        rects = [[max(0.0, min(1.0, value)) for value in rect] for rect in rects]
        rects = [rect for rect in rects if rect[2] > rect[0] and rect[3] > rect[1]]
        if not rects:
            return None
        box = max(rects, key=lambda rect: (rect[2] - rect[0]) * (rect[3] - rect[1]))
    width, height = result.get('width') or 1, result.get('height') or 1
    x0, y0, x1, y1 = box[0] * width, box[1] * height, box[2] * width, box[3] * height
    side = max(x1 - x0, y1 - y0) * (1 + 2 * margin); side = min(max(side, least * min(width, height)), min(width, height))
    cx = min(max((x0 + x1) / 2, side / 2), width - side / 2); cy = min(max((y0 + y1) / 2, side / 2), height - side / 2)
    window = (round((cx - side / 2) / width, 4), round((cy - side / 2) / height, 4), round((cx + side / 2) / width, 4), round((cy + side / 2) / height, 4))
    return _uncut(window, box, part_map(result, (0, 0, 1, 1)), width, height, inset)


def _cuts(window, parts, inset=0.0):
    """The small parts this window (fractions) slices through: partly in, partly out. With ``inset`` (of the window's
    size) a part must clear that margin too, where a frame fades out or is trimmed to another shape."""
    return [part['name'] for part in _cut_parts(window, parts, inset)]


def _cut_parts(window, parts, inset=0.0):
    dx = (window[2] - window[0]) * inset; dy = (window[3] - window[1]) * inset
    inner = (window[0] + dx, window[1] + dy, window[2] - dx, window[3] - dy)
    found = []
    for part in parts:
        b = part['box']; area = (b[2] - b[0]) * (b[3] - b[1])
        if area <= 0:
            continue
        def share(w):
            return max(0, min(b[2], w[2]) - max(b[0], w[0])) * max(0, min(b[3], w[3]) - max(b[1], w[1])) / area
        if share(window) > .04 and share(inner) < .88:
            found.append(part)
    return found


def _uncut(window, target, parts, width, height, inset=0.0):
    """The nearest square window, from a quarter smaller to a third larger, that still holds nine tenths of the ``target``
    box and slices through as little as it can of the small parts (a badge, a rivet), measured by their area; the window
    as it was when nothing does better. Fractions of a render ``width`` x ``height``. Smaller is tried too: a close-up
    camera may itself frame the render so that its edge cuts a badge."""
    def cut_area(box):
        return sum((b['box'][2] - b['box'][0]) * (b['box'][3] - b['box'][1]) for b in _cut_parts(box, parts, inset))
    start = cut_area(window) if parts else 0
    if not start:
        return window
    side = (window[2] - window[0]) * width; cx = (window[0] + window[2]) / 2 * width; cy = (window[1] + window[3]) / 2 * height
    target_area = (target[2] - target[0]) * (target[3] - target[1])
    best = (start, 0, window)
    for grow in (1, .9, 1.1, .8, 1.2, .75, 1.33):
        size = min(side * grow, width, height)
        for step_x in range(-8, 9):
            for step_y in range(-8, 9):
                x = min(max(cx + step_x * .04 * side, size / 2), width - size / 2); y = min(max(cy + step_y * .04 * side, size / 2), height - size / 2)
                box = ((x - size / 2) / width, (y - size / 2) / height, (x + size / 2) / width, (y + size / 2) / height)
                held = max(0, min(box[2], target[2]) - max(box[0], target[0])) * max(0, min(box[3], target[3]) - max(box[1], target[1]))
                if target_area > 0 and held < .9 * target_area:
                    continue
                cost = abs(grow - 1) * 4 + abs(x - cx) / side + abs(y - cy) / side
                candidate = (round(cut_area(box), 6), cost, box)
                if candidate[:2] < best[:2]:
                    best = candidate
    return tuple(round(value, 4) for value in best[2])


def part_map(result, region, smallest=6, largest=.3, footprint=.05, solid=.4):
    """The separate pieces a close-up shows that a frame edge should not slice through (a badge, a rivet): compact
    objects, each taking at most ``footprint`` of the render, whose patches are at least ``smallest`` probe cells, at most
    ``largest`` of the window across and at least ``solid`` filled (not a thin piping line), as boxes in fractions of
    the window ``region``. A frame may still cut a large or long part (the bag body, a handle, a binding)."""
    probe = result.get('probe') if isinstance(result, dict) else None
    if not isinstance(probe, dict) or not probe.get('cells') or not probe.get('nx') or not region:
        return []
    nx, ny = probe['nx'], probe['ny']; names = probe.get('objects', []); cells = probe['cells']
    rw, rh = region[2] - region[0], region[3] - region[1]
    by_object = {}
    for index, cell in enumerate(cells):
        if isinstance(cell, int) and 0 <= cell < len(names):
            by_object.setdefault(cell, set()).add((index % nx, index // nx))
    out = []
    for value, remaining in by_object.items():
        if len(remaining) > footprint * nx * ny:
            continue
        while remaining:
            stack = [remaining.pop()]; patch = []
            while stack:
                x, y = stack.pop(); patch.append((x, y))
                for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if neighbour in remaining:
                        remaining.discard(neighbour); stack.append(neighbour)
            if len(patch) < smallest:
                continue
            box = [(min(x for x, _ in patch) / nx - region[0]) / rw, (min(y for _, y in patch) / ny - region[1]) / rh,
                   ((max(x for x, _ in patch) + 1) / nx - region[0]) / rw, ((max(y for _, y in patch) + 1) / ny - region[1]) / rh]
            span = (max(x for x, _ in patch) - min(x for x, _ in patch) + 1) * (max(y for _, y in patch) - min(y for _, y in patch) + 1)
            if box[2] - box[0] > largest or box[3] - box[1] > largest or box[2] <= 0 or box[3] <= 0 or box[0] >= 1 or box[1] >= 1 \
                    or len(patch) < solid * span or min(max(x for x, _ in patch) - min(x for x, _ in patch), max(y for _, y in patch) - min(y for _, y in patch)) < 1:
                continue
            out.append({'name': names[value], 'box': [round(v, 4) for v in box]})
    return out


def part_box(result, names, region):
    """The box of the parts a close-up is about, in fractions of the window ``region`` (None when the render does not say)."""
    box = _visible_patch(result.get('probe') if isinstance(result, dict) else None, names)
    if box is None or not region:
        return None
    rw, rh = region[2] - region[0], region[3] - region[1]
    clip = lambda value: round(min(1, max(0, value)), 4)
    return [clip((box[0] - region[0]) / rw), clip((box[1] - region[1]) / rh), clip((box[2] - region[0]) / rw), clip((box[3] - region[1]) / rh)]


def part_focus(result, names, region):
    """Where the parts sit inside a close-up window (fractions of it): the crop centre for a frame of another shape."""
    box = _visible_patch(result.get('probe'), names)
    if box is None or not region:
        return [.5, .5]
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return [round(min(1, max(0, (cx - region[0]) / (region[2] - region[0]))), 3), round(min(1, max(0, (cy - region[1]) / (region[3] - region[1]))), 3)]


def remap(point, crop):
    """A point of a whole render (fractions) in the trimmed picture (fractions)."""
    return [round((point[0] - crop[0]) / (crop[2] - crop[0]), 4), round((point[1] - crop[1]) / (crop[3] - crop[1]), 4)]


def scene_box(row, slack=.15):
    """Where the product sits in a scene picture (fractions): where it was locked when the scene was made (as recorded,
    else read off the scene's ``reference.png``, the product alone on white in its place), widened by ``slack`` of its
    size on each side, since the image model may draw it a little larger (and a frame should leave it some air).
    None when neither is there."""
    box = row.get('box')
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        reference = Path(row.get('path', '')).parent / 'reference.png'
        if not reference.is_file():
            return None
        with Image.open(reference) as opened:
            grey = opened.convert('L')
            found = grey.point(lambda value: 255 if value < 245 else 0).getbbox()
            width, height = grey.size
        if not found:
            return None
        box = [found[0] / width, found[1] / height, found[2] / width, found[3] / height]
    # at most 6 % of the picture each side: a long tool held across the frame (80 % of it) would otherwise grow to the full
    # width, and no tile could then crop the scene without cutting the product
    dx = min((box[2] - box[0]) * slack, .06); dy = min((box[3] - box[1]) * slack, .06)
    return [round(max(0.0, box[0] - dx), 4), round(max(0.0, box[1] - dy), 4), round(min(1.0, box[2] + dx), 4), round(min(1.0, box[3] + dy), 4)]


LOCALISER = ('You fit the words of a product listing to the listing images: lines in another language are translated into the '
             'images\' language, and lines longer than max_chars are rewritten shorter in the same language, keeping their meaning as '
             'a complete phrase (never cut off mid-sentence). Translate faithfully '
             'and concisely, as a seller would write them on an Amazon listing; keep every number, unit, model code and brand name exactly '
             'as written; do not add claims or words that are not there. Each line has max_chars, the space the page gives it: stay within '
             'it, shortening to the essential words rather than cutting a word.')
_HAN = re.compile(r'[\u4e00-\u9fff]')


_NUMERALS = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def _arabic(text):
    """Chinese numerals up to 99 written as figures ("四颗" -> "4颗", "十二" -> "12")."""
    def value(match):
        word = match.group(0)
        if '十' not in word:
            return ''.join(str(_NUMERALS[char]) for char in word)
        tens, _, ones = word.partition('十')
        return str((_NUMERALS.get(tens, 1) if tens else 1) * 10 + (_NUMERALS.get(ones, 0) if ones else 0))
    return re.sub(r'[零一二两三四五六七八九]?十[一二三四五六七八九]?|[零一二两三四五六七八九]+', value, text)


def _strings(product, scenes, parts):
    """Every piece of the record's own words that can reach a page, with the most characters a page gives it."""
    found = [(product.get('title'), 24), (product.get('headline'), 40), (product.get('subline'), 60), ((product.get('usecase') or {}).get('headline'), 40)]
    for row in product.get('features') or []:
        found += [(row.get('title'), 22), (row.get('detail'), 40)]
    for row in product.get('specs') or []:
        found += [(row.get('label'), 18), (row.get('value'), 30)]
    for row in product.get('figures') or []:
        found += [(row.get('label'), 18), (row.get('unit'), 8)]
    for row in scenes or []:
        found += [(row.get('title'), 18), (row.get('body'), 40)]
    for row in parts or []:
        found += [(row.get('name'), 20), (row.get('caption'), 40)]
    limits = {}
    for text, limit in found:
        if isinstance(text, str) and text.strip():
            limits[text] = min(limit, limits.get(text, limit))
    return limits


def localise(product, scenes, parts, cache):
    """The record's words in the language the pages are printed in: on an English set, every line written in Chinese
    (a product name, a selling point, a scene caption made for an earlier Chinese set) is translated by the text model,
    keeping its numbers. ``cache`` ({language: {source: text}}) is updated in place and kept with the project, so a
    line is translated once. Returns (product, scenes, parts, notes); without a text model the lines stay as written
    and a note says so. A Chinese set keeps English words (model codes, units, terms) as they are."""
    language = product.get('language')
    if language not in ('en', 'zh'):
        return product, scenes, parts, []
    known = cache.setdefault(language, {})
    limits = _strings(product, scenes, parts)
    # Lines in the wrong language (Chinese on an English set) and lines longer than the space the pages give them.
    foreign = lambda text: language == 'en' and bool(_HAN.search(text))
    wanted = sorted(text for text in limits if text not in known and (foreign(text) or len(text) > limits[text]))
    notes = []
    if wanted:
        try:
            import brandkit_review
            config, _ = brandkit_review._configs()
        except Exception:
            config = None
        if not config:
            if any(foreign(text) for text in wanted):
                notes.append('产品信息里有中文，但图片文字选的是英文，且没有配置文本模型来翻译：这些中文内容不会印在英文图上，请填写英文或配置文本模型。')
        else:
            schema = {'type': 'object', 'additionalProperties': False, 'required': ['lines'],
                      'properties': {'lines': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['source', 'text'],
                                                                         'properties': {'source': {'type': 'string'}, 'text': {'type': 'string'}}}}}}
            digits = lambda text: set(re.findall(r'\d+(?:\.\d+)?', unicodedata.normalize('NFKC', text)))
            long = {}
            for attempt in (1, 2):              # a line over its space is asked once more, more strictly
                ask = [text for text in wanted if text not in known]
                if not ask:
                    break
                try:
                    answer = brandkit_review._ask(config, LOCALISER, {'language': 'English' if language == 'en' else 'Simplified Chinese', 'product': product.get('title', ''),
                                                                      'strict_length': attempt == 2,
                                                                      'lines': [{'source': text, 'max_chars': limits[text]} for text in ask]}, schema)
                except Exception as exc:
                    notes.append('翻译产品文字时文本模型没有回应（%s）。' % str(exc)[:80]); break
                for row in answer.get('lines') or []:
                    source, text = row.get('source'), ' '.join(str(row.get('text', '')).split())
                    # Every number written in figures stays; a number in the translation must come from the line ("四" may become "4").
                    kept = digits(source) <= digits(text) and digits(text) <= digits(source) | digits(_arabic(source))
                    if source in ask and text and not (language == 'en' and _HAN.search(text)) and kept:
                        if len(text) <= limits[source]:
                            known[source] = text
                        else:
                            long[source] = text
            for source, text in long.items():   # still too long: the translation beats the Chinese line; a long original stays itself
                if foreign(source) or len(text) < len(source):
                    known.setdefault(source, text)
            missing = [text for text in wanted if text not in known and foreign(text)]
            if missing:
                notes.append('有 %d 条产品文字没能译成英文，这些文字不会印在英文图上：%s。' % (len(missing), '、'.join(missing[:3])))
    # A line that could not be translated is left off the English pages (a blank field falls back to the template's own words).
    swap = lambda text: known.get(text, '' if foreign(text) else text) if isinstance(text, str) else text
    product = copy.deepcopy(product)
    for key in ('title', 'headline', 'subline'):
        product[key] = swap(product.get(key))
    if isinstance(product.get('usecase'), dict):
        product['usecase']['headline'] = swap(product['usecase'].get('headline'))
    for key, fields in (('features', ('title', 'detail')), ('specs', ('label', 'value')), ('figures', ('label', 'unit'))):
        product[key] = [{**row, **{field: swap(row.get(field)) for field in fields}} for row in product.get(key) or []]
    scenes = [{**row, 'title': swap(row.get('title')), 'body': swap(row.get('body'))} for row in scenes or []]
    parts = [{**row, 'name': swap(row.get('name')), 'caption': swap(row.get('caption'))} for row in parts or []]
    return product, scenes, parts, notes


def scene_asset(source, target):
    """A scene picture as an opaque PNG for the templates."""
    with Image.open(source) as opened:
        opened.convert('RGB').save(target, 'PNG', optimize=True)
    return brandkit_render.asset(target)


# ---- usage scenes (from the image model, the product locked by its mask) ----

SCENE_PLAN = 4        # how the scenes are planned; a change makes every listing's scenes again (2: a third scene in its own setting;
                      # 3: moving parts set and usage rules from the pose agent; 4: long tools held larger in hand close-ups)


def scene_key(project, product, settings):
    plan = project.get('plan') or {}
    moving = project.get('articulation') if isinstance(project.get('articulation'), dict) else {}
    raw = json.dumps([project.get('model_path'), plan.get('rotation'), plan.get('material_overrides'), product.get('title'), settings, SCENE_PLAN,
                      moving.get('key'), moving.get('status')],
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def scene_briefs(project, settings, count):
    """What each scene shows: the strategist's picture of the product in use, the product set down where it
    is used, and the same for the next most likely buyer. Returns [{'proposal', 'role'}]."""
    import scene_audience
    import scene_stylist
    first = scene_stylist.propose(project, settings['hint'], settings['audience'], None, scene_stylist._source_image(project), settings['people'])
    briefs = [{'proposal': first, 'role': 'use'}]
    segment = first.get('segment_id')
    people_allowed = settings['people'] != 'include'
    if count >= 2:
        if settings['people'] in ('include', 'hands'):
            # Asked to see people in every picture: the second is still the product at work, a different moment of it.
            second = {**first, 'scene': first.get('scene', '') + ' A different moment of the same job, seen from another side.'}
        else:
            second = {**first, 'people': False, 'framing': None, 'scene': scene_audience.fixed_scene(segment, False)}
        briefs.append({'proposal': second, 'role': 'place' if people_allowed else 'use'})
    if count >= 3:
        metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
        text = ' '.join(str(value) for value in (project.get('name', ''), metadata.get('product_category', ''), settings['hint']))
        others = [item for item in scene_audience.rank(text, text) if item != segment]
        if not others and segment:
            # nothing else in the product's words: the neighbouring buyer, never the first scene again
            neighbour = scene_audience.NEIGHBOUR.get(segment)
            others = [neighbour] if neighbour and scene_audience.segment(neighbour) else []
        other = others[0] if others else None
        wants = first.get('people') if settings['people'] == 'auto' else settings['people'] in ('include', 'hands')
        third = {**first, 'segment_id': other or segment, 'people': bool(wants), 'framing': first.get('framing') if wants else None,
                 'scene': scene_audience.fixed_scene(other, bool(wants)) if other else
                          (first.get('scene', '') + ' Another setting where the same buyer would use it.')}
        if other:
            # the third scene's own plan, not the first one's carried over
            third.update(summary=(scene_audience.segment(other) or {}).get('label', ''), reference_id=None, reason='')
        briefs.append({'proposal': third, 'role': 'other'})
    return [_with_moving_parts(project, brief) for brief in briefs[:count]]


def _with_moving_parts(project, brief):
    """A brief with the product's moving parts set for its moment (the pose agent's states: at work the working or
    open state, at rest the rest pose) and the product's own rules for being used correctly."""
    import articulation
    from studio_store import get
    try:
        project = get('project', project['id'])
    except (KeyError, TypeError):
        pass
    states = articulation.states(project)
    proposal = dict(brief['proposal'])
    wanted = ('rest',) if brief['role'] == 'place' else ('working', 'open')
    chosen = next((row for role in wanted for row in states if row.get('role') == role), None)
    plan = articulation.plan_for(project, chosen['id']) if chosen else None
    if plan:
        proposal['articulation'] = {**plan, 'label': chosen.get('label_en') or chosen['id']}
    rules = articulation.usage(project, 'en')
    if rules:
        proposal['usage'] = rules
    return {**brief, 'proposal': proposal}


def _fit_line(text, limit):
    """A line within ``limit`` characters, cut only after a whole clause; '' when even the first clause is too long."""
    if len(text) <= limit:
        return text
    cuts = [index for index, char in enumerate(text[:limit + 1]) if char in '，、；。,;' and index > 0]
    return text[:cuts[-1]].rstrip('，、；,;') + ('。' if re.search(r'[\u4e00-\u9fff]', text) else '') if cuts else ''


def _clause_lines(text, language, limit):
    """A Chinese line set one clause per line (the browser would otherwise wrap it mid-word); '' when a clause is
    longer than ``limit`` characters and so would still wrap. Other languages wrap at spaces and pass unchanged."""
    if not text or language != 'zh':
        return text or ''
    clauses = [part for part in re.split(r'(?<=[，、；。])', text.replace('\n', '')) if part]
    if any(len(part.rstrip('，、；。')) > limit for part in clauses):
        return ''
    return '\n'.join(clauses).rstrip('，、；')


def captions(project, product, briefs):
    """A short title and line for each scene, in the listing's language: by the copy model when one is
    configured, else plain titles by what the scene shows."""
    language = product['language']; fixed = TEXT[language]['scene_titles']
    rows = [{'title': fixed[('use', 'place', 'other').index(item['role'])], 'body': ''} for item in briefs]
    try:
        import brandkit_review
        import scene_stylist
        config = brandkit_review.model_for(scene_stylist._config(None), 'brandkit_set.captions')   # AI_COPY_MODEL when named (v40)
        if not config:
            return rows
        limit = (8, 15) if language == 'zh' else (22, 44)
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['scenes'],
                  'properties': {'scenes': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['title', 'body'],
                                                                       'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}}}}}}
        facts = {'product': product['title'] or project.get('name', ''), 'language': 'Simplified Chinese' if language == 'zh' else 'English',
                 'title_max_chars': limit[0], 'line_max_chars': limit[1],
                 'scenes': [str(item['proposal'].get('scene', ''))[:600] for item in briefs]}
        system = ('You write captions for product listing images. For each scene description, write a short title naming the moment or '
                  'place of use (not words like "展示"/"showcase" that describe the photo) and one short line saying what the buyer sees. '
                  'Describe only what the scene shows; no numbers, no benefits or claims, no prices or brands. Respect the character limits '
                  'strictly; different scenes get clearly different titles. In Chinese, write the line as two short clauses joined by "，", '
                  'each at most 8 characters, so it can be set one clause per line.')
        answer = scene_stylist._ask(config, system, facts, schema)
        for row, made in zip(rows, answer.get('scenes', [])):
            title = ' '.join(str(made.get('title', '')).split()); body = _fit_line(' '.join(str(made.get('body', '')).split()), limit[1])
            title = title if len(title) <= limit[0] else ''
            if title:
                row.update(title=title, body=body)
    except Exception:
        pass
    return rows


SEEN = ('You caption one picture of a product listing set: a scene the image model made with the product in it. Look at the '
        'picture. Title: what `shows` asks for (the task being done, or the place), as a shopper would name it ("Stripping wire", '
        '"At the desk", "Weekend away"), only what is in view (no "office" without office things in view, no "job site" for a '
        'bench). Line: what the product is doing for its owner there, as the picture shows it ("Stands upright, ready to grab"), not '
        'a description of the room. No numbers, prices, brands or claims the picture does not show. The title must name a different '
        'place or task from every title in titles_already_used (workshop, workbench and bench are the same place; so are garage and '
        'shed), and the line must not repeat lines_already_used. Respect the character limits strictly. In Chinese, write the line as '
        'two short clauses joined by "，", each at most 8 characters.')
SHOWS = {'use': 'the product at work: title the task being done', 'place': 'the product at rest where it is kept or used: title the place',
         'other': 'a different setting from the other pictures: title the place or the task'}


PLACE_WORDS = {'workbench': 'shop', 'bench': 'shop', 'workshop': 'shop', 'shop': 'shop', 'garage': 'shed', 'shed': 'shed',
               '工作台': '工坊', '工坊': '工坊', '车间': '工坊', '工作间': '工坊', '台面': '工坊'}
_TITLE_FILLER = {'at', 'in', 'on', 'the', 'a', 'an', 'by', 'for', 'of', 'to', 'and', 'with'}


def _same_place(title, other):
    """Whether two scene titles name the same place ("In the workshop" / "At the workbench", the words for one place counted
    as one) or say the same thing in other filler words."""
    def words(text):
        text = str(text or '').lower()
        found = {PLACE_WORDS.get(word, word) for word in re.findall(r'[a-z]+', text) if word not in _TITLE_FILLER and len(word) > 2}
        found |= {value for key, value in PLACE_WORDS.items() if re.search(r'[\u4e00-\u9fff]', key) and key in text}
        return found
    a, b = words(title), words(other); places = set(PLACE_WORDS.values())
    return bool(a & b & places) or (bool(a) and a == b)


CAPTIONED = 'occasion'          # how the captions were written; older ones are written again once


def seen_captions(product, rows):
    """Each scene captioned from the picture itself by the vision model (the brief it was made from may not be what came
    out). Rows the model does not answer for keep their captions. Returns (rows, how many were rewritten)."""
    try:
        import brandkit_review
        _, vision = brandkit_review._configs()
    except Exception:
        return rows, 0
    if not vision:
        return rows, 0
    language = product['language']; limit = (8, 15) if language == 'zh' else (22, 44)
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['title', 'body'],
              'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}}}
    out = []; done = 0; taken = set(); said = set()
    for row in rows:
        if ((row.get('captions') or {}).get(language) or {}).get('how') == CAPTIONED:
            taken.add(row['title']); said.add(row.get('body') or ''); out.append(row); continue          # already captioned in this language
        try:
            facts = {'product': product['title'], 'language': 'Simplified Chinese' if language == 'zh' else 'English',
                     'title_max_chars': limit[0], 'line_max_chars': limit[1], 'titles_already_used': sorted(taken),
                     'lines_already_used': sorted(said), 'shows': SHOWS.get(row.get('role'), SHOWS['other'])}
            answer = brandkit_review._ask(vision, SEEN, facts, schema, image_path=row['path'])
            title = ' '.join(str(answer.get('title', '')).split()); body = _fit_line(' '.join(str(answer.get('body', '')).split()), limit[1])
            if (title and len(title) <= limit[0] and title not in taken and not any(_same_place(title, other) for other in taken)
                    and (not body or body not in said) and not re.search(r'\d', title + body)):
                captions = {**(row.get('captions') or {}), language: {'title': title, 'body': body, 'how': CAPTIONED}}
                row = {**row, 'title': title, 'body': body, 'caption': CAPTIONED, 'captions': captions}; done += 1
        except Exception:
            pass
        taken.add(row['title']); said.add(row.get('body') or ''); out.append(row)
    return out, done


def make_scenes(project, product, folder, cancelled, say, previous=None):
    """Scene pictures for the listing, made by the image model: [{'path', 'title', 'body', 'focus', 'role', 'record'}].

    A set made with the same settings for the same model is reused (``previous``: the stored rows with their key).
    Returns (rows, key, notes).
    """
    import scene_stylist
    settings = product.get('scenes') or default_scenes()
    notes = []
    if not settings['enabled']:
        return [], None, notes
    if not image_model_ready():
        notes.append('图像模型未配置，没有生成 AI 场景图；海报改用纯色版，需要场景的页面已跳过。')
        return [], None, notes
    key = scene_key(project, product, settings)
    if previous and previous.get('key') == key:
        rows = [row for row in previous.get('items', []) if Path(row.get('path', '')).is_file()]
        if len(rows) == len(previous.get('items', [])) and rows:
            notes = ['场景图沿用上次生成的 %d 张（设置没有变化）。' % len(rows)]
            # Each language keeps its own captions: a set in another language, or captions written the old way, are read again.
            language = product['language']
            own = lambda row: ((row.get('captions') or {}).get(language) or {})
            rows = [{**row, 'title': own(row)['title'], 'body': own(row).get('body', '')} if own(row).get('how') == CAPTIONED else row for row in rows]
            if any(own(row).get('how') != CAPTIONED for row in rows):
                rows, done = seen_captions(product, [row for row in rows])
                if done:
                    notes.append('已按场景图的实际内容重写 %d 条图注。' % done)
            return rows, key, notes
    folder.mkdir(parents=True, exist_ok=True)
    try:
        briefs = scene_briefs(project, settings, settings['count'])
    except ValueError as exc:
        notes.append('没有生成场景图：%s' % exc)
        return [], None, notes
    if briefs[0]['proposal'].get('method') != 'generate' and not image_model_ready():
        return [], None, notes
    titles = captions(project, product, briefs)
    rows = []
    for index, (brief, caption) in enumerate(zip(briefs, titles)):
        if cancelled():
            raise InterruptedError()
        say(index, len(briefs), '生成第 %d / %d 张场景图' % (index + 1, len(briefs)))
        try:
            _, record, picture = scene_stylist.generate(project, brief['proposal'], settings['hint'], cancelled,
                                                        lambda text, index=index: say(index, len(briefs), '场景 %d：%s' % (index + 1, text)),
                                                        folder / ('scene%d' % (index + 1)))
        except InterruptedError:
            raise
        except Exception as exc:
            notes.append('第 %d 张场景图没有生成：%s' % (index + 1, str(exc)[:160]))
            continue
        box = (picture['record'].get('anchor') or {}).get('box') or [.3, .3, .7, .7]
        checked = record.get('check') or {}
        if checked.get('status') == 'failed':
            why = [name for name, ok in (checked.get('flags') or {}).items() if ok is False]
            notes.append('第 %d 张场景图的产品核对没有完全通过%s，请检查后再使用。' % (
                index + 1, '（%s）' % (checked.get('note') or '、'.join(why))[:120] if (checked.get('note') or why) else ''))
        rows.append({'path': str(picture['png']), 'title': caption['title'], 'body': caption['body'], 'role': brief['role'],
                     'focus': [round((box[0] + box[2]) / 2, 3), round((box[1] + box[3]) / 2, 3)], 'box': box,
                     'summary': record.get('summary'), 'model': record.get('model'), 'people': record.get('people'),
                     'check': checked.get('status') or 'unchecked',
                     # why it failed, so the set's notes and the next look can say so
                     'check_issues': [str(item)[:200] for item in (checked.get('issues') or [])[:5]],
                     'check_flags': [name for name, ok in (checked.get('flags') or {}).items() if ok is False],
                     # what the picture cost: the image service's token counts over all attempts, and how many there were
                     'usage': record.get('usage'), 'attempts': record.get('attempts')})
    rows, _ = seen_captions(product, rows)
    return rows, key if rows else None, notes


# ---- jobs ----

def _body(text, limit):
    """A body line that fits ``limit``: whole, or its leading whole clause; never a sentence cut off mid-phrase (None then)."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text or None
    cut = max((match.start() for match in re.finditer(r'[.;,，；。:：]|\s[-–—]\s', text[:limit + 1])), default=-1)
    return text[:cut].rstrip(' ,，;；:：-–—') + ('.' if not re.search(r'[\u4e00-\u9fff]', text) else '。') if cut >= limit * .35 else None


def _keep_together(text):
    """Joined words (CR-V, Li-Ion, 20V-MAX) are not broken at their hyphen."""
    return re.sub(r'(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])', '-\u2060', str(text)) if text else text


def _clip(text, limit):
    """Text that fits ``limit`` characters without an ellipsis (a listing never prints "…"): the leading segment that fits
    (up to a dash, comma, slash, bracket or " with"), else the whole words that fit; line breaks the author put in are kept."""
    lines = [' '.join(line.split()) for line in str(text or '').split('\n')]
    text = '\n'.join(line for line in lines if line)
    if len(text) <= limit:
        return text
    text = text.rstrip('…').rstrip('.').rstrip()
    for match in re.finditer(r'\s[-–—|/]\s|[,，;；:：(（/]|\swith\s|\sfor\s|，', text):
        head = text[:match.start()].rstrip(' ,，;；:：-–—')
        if len(head) > limit:
            break
        best = head
    if 'best' in locals() and len(best) >= 4:
        return best
    cut = text[:limit]
    if re.search(r'[A-Za-z0-9]', cut[-1:]) and len(text) > limit and re.search(r'[A-Za-z0-9]', text[limit:limit + 1]) and ' ' in cut:
        cut = cut[:cut.rindex(' ')]          # a Latin word is not cut in two
    return cut.rstrip(' ,，;；:：-–—\n')


def _headline(text, language, limit):
    """A headline with its line break where a reader pauses: the author's own, else (in Chinese) after the
    clause mark nearest the middle, so a template never breaks a phrase in two."""
    text = _clip(text, limit)
    if language != 'zh' or '\n' in text or len(text) <= 7:
        return text
    marks = [index for index, char in enumerate(text[:-1]) if char in '，、；：,;:' and 2 <= index]
    if not marks:
        return text
    cut = min(marks, key=lambda index: abs(index - len(text) / 2))
    return text[:cut + 1] + '\n' + text[cut + 1:]


# Pages that carry the brand's badge texts (TPL.badge), and how many: the main image and the poster.
BADGED = {'T01': 1, 'T12': 1}


def _palette(template_id, brand):
    tokens = brandkit_render.template_info(template_id)['tokens']
    palette = dict(tokens['palette'])
    if brand is not None:
        dark = palette['bg'].lower() in ('#24272b', '#26292d')
        if dark and brand.record.get('dark'):
            palette['bg'] = brand.dark
        palette['accent'] = brand.readable(brand.accent, palette['bg'])
    # Text on an accent fill (chips, pills, number badges): near-black on a light accent, white on a dark one.
    accent = palette.get('accent', '#D0612A').lstrip('#')
    if len(accent) == 6:
        red, green, blue = (int(accent[i:i + 2], 16) / 255 for i in (0, 2, 4))
        light = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
        luminance = .2126 * light(red) + .7152 * light(green) + .0722 * light(blue)
        palette['accent-ink'] = '#111111' if luminance > .32 else '#FFFFFF'
    return palette


def _job(template_id, ctx, **fields):
    width, height = CANVAS[ctx['aspect']]
    tokens = brandkit_render.template_info(template_id)['tokens']
    palette = _palette(template_id, ctx.get('brand'))
    background = str(palette.get('bg', '')).lstrip('#')
    if ctx.get('light_product') and len(background) == 6 and sum(int(background[i:i + 2], 16) for i in (0, 2, 4)) / 765 > .8:
        palette['bg'] = '#D5D9DD'        # a bright steel or white product on a near-white page would vanish: a cool grey ground
    brand = ctx.get('brand'); record = getattr(brand, 'record', None) or {}
    extra = {}
    badges = [text for text in (record.get('badges') or []) if isinstance(text, str) and text.strip()]
    if badges and template_id in BADGED and 'badges' not in fields:
        extra['badges'] = [{'text': text, 'style': record.get('badge_style') or 'auto'} for text in badges[:BADGED[template_id]]]
    if record.get('texture') and record['texture'] != 'none':
        extra['texture'] = record['texture']
    return {'template_id': template_id, 'variant': 'studio', 'canvas': {'width': width, 'height': height}, 'lang': ctx['language'],
            'safe_margin': tokens.get('safe_margin', 84), 'min_margin': tokens.get('min_margin', 60),
            'palette': palette, 'publishability': 'user_product',
            'fonts': brandkit_assets.job_fonts(record.get('fonts'), ctx['language']), **extra, **fields}


ICON_WORDS = ((('battery', '电池', 'mah'), 'battery-4'), (('volt', '电压'), 'bolt'), (('weight', '重量', '净重', 'kg', ' lb'), 'weight'),
              (('speed', 'rpm', '转速'), 'gauge'), (('torque', '扭矩', 'n·m', 'nm'), 'rotate-clockwise-2'),
              (('capacity', '容量', 'volume', '容积'), 'package'), (('time', 'runtime', '时间', '续航', 'min'), 'clock-hour-4'),
              (('waterproof', 'water', '防水', 'ip6'), 'droplet'), (('warranty', '保修', '质保'), 'shield-check'), (('led', 'light', '照明', '灯'), 'bulb'),
              (('length', 'width', 'height', 'depth', 'size', 'diameter', '长', '宽', '高', '深', '厚', '尺寸', '直径', '管径', '范围'), 'ruler-measure'),
              (('material', 'steel', 'canvas', 'leather', 'aluminium', 'aluminum', 'plastic', '材质', '材料', '钢', '帆布', '皮', '铝'), 'stack-2'))


def _icon_for(label, unit=''):
    """A Tabler line icon for a spec, by the words in its label (then its unit); a height, width or depth gets its own arrow."""
    text = ' %s %s ' % (str(label or '').lower(), str(unit or '').lower())
    for words, icon in ((('height', '高度', '高'), 'arrow-autofit-height'), (('width', 'length', '宽度', '长度', '宽', '长'), 'arrow-autofit-width'),
                        (('depth', 'thickness', '深度', '厚度', '深', '厚'), 'arrows-diagonal')):
        if any(word in str(label or '').lower() for word in words):
            return icon
    return next((icon for words, icon in ICON_WORDS if any(word in text for word in words)), None)


def _chip_rows(ctx, limit=3):
    """The product's figures as chips (icon, value, unit, label): its stated figures and number specs, then the sizes the user stated."""
    t = TEXT[ctx['language']]; product = ctx['product']; rows = []
    for row in product['figures']:
        number, _ = _number_unit(row['value'])
        if number is not None:
            rows.append({'label': _clip(row['label'], 18), 'value': row['value'], 'unit': row.get('unit', ''), 'icon': _icon_for(row['label'], row.get('unit'))})
    for row in product['specs']:
        number, unit = _number_unit(row['value'])
        if number is not None and len(row['value']) <= 14 and not any(item['label'] == _clip(row['label'], 18) for item in rows):
            rows.append({'label': _clip(row['label'], 18), 'value': number, 'unit': unit, 'icon': _icon_for(row['label'], unit)})
    def as_mm(value, unit):
        number, _ = _number_unit(value)
        scale = {'mm': 1, 'cm': 10, 'm': 1000, 'in': 25.4, '"': 25.4, '毫米': 1, '厘米': 10}.get(str(unit or '').strip().lower())
        return float(str(number).replace(',', '')) * scale if number is not None and scale else None
    shown = [as_mm(row['value'], row.get('unit')) for row in rows]
    # Then the overall sizes the user stated (never one measured on the model), unless a figure already gives it (476 mm = 47.6 cm).
    stated = ctx['stated'] if 'stated' in ctx else stated_dimensions(product, ctx.get('dims_mm'), ctx.get('flat'), unit=ctx['unit'])[0]
    for row in stated:
        if len(rows) >= limit:
            break
        if any(value and abs(value - row['mm']) <= max(2.0, row['mm'] * .02) for value in shown) or any(item['label'] == t[row['key']] for item in rows):
            continue
        icon = {'height': 'arrow-autofit-height', 'width': 'arrow-autofit-width', 'length': 'arrow-autofit-width', 'depth': 'arrows-diagonal',
                'thickness': 'arrows-diagonal'}.get(row['key'], 'ruler-measure')
        rows.append({'label': t[row['key']], 'value': '%g' % row['value'], 'unit': row['unit'], 'icon': icon})
    return [{key: value for key, value in row.items() if value is not None} for row in rows[:limit]]


def _number_unit(value):
    match = re.match(r'^\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(.*)$', str(value or ''))
    if not match:
        return None, None
    return match.group(1), match.group(2).strip()


def dimensions_mm(ctx):
    """[(key, mm)] of the product as the straight-on view shows it: across, up, and the depth it hides."""
    dims = ctx.get('dims_mm')
    if not dims:
        return []
    width, depth, height = dims
    across, hidden = (width, depth) if width >= depth else (depth, width)
    if ctx.get('flat'):            # a tool lying flat is shown from above: its length across, its width up, its thickness aside
        return [('length', across), ('width', hidden), ('thickness', height)]
    return [('width', across), ('height', height), ('depth', hidden)]


LENGTH_MM = {'mm': 1, 'cm': 10, 'm': 1000, 'in': 25.4, 'inch': 25.4, 'inches': 25.4, '"': 25.4, '毫米': 1, '厘米': 10, '公分': 10}


def _stated_lengths(product):
    """Every length the seller wrote in the specs and figures, as (mm, number as written, unit): "200 mm (8 in)" gives two."""
    found = []
    texts = [str(row.get('value', '')) for row in product.get('specs') or []]
    texts += ['%s %s' % (row.get('value', ''), row.get('unit', '')) for row in product.get('figures') or []]
    for text in texts:
        for number, unit in re.findall(r'(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|"|毫米|厘米|公分)(?![a-z])', text.lower()):
            found.append((float(number) * LENGTH_MM[unit], number, {'inches': 'in', 'inch': 'in', '"': 'in'}.get(unit, unit)))
    return found


def _stated_measure(mm, unit, product):
    """A measured size as the seller wrote it when they stated it (within 3 %): (value, unit, the other system's stated
    figure or None); else the measured size in ``unit``."""
    near = [row for row in _stated_lengths(product) if abs(row[0] - mm) <= mm * .03]
    metric = next((row for row in near if row[2] in ('mm', 'cm', '毫米', '厘米', '公分')), None)
    imperial = next((row for row in near if row[2] == 'in'), None)
    first, other = (imperial, metric) if unit == 'in' else (metric, imperial)
    if not first:
        first, other = other, None
    if not first:
        value, shown = _measure(mm, unit)
        return value, shown, None
    unit_of = {'毫米': 'mm', '厘米': 'cm', '公分': 'cm'}.get(first[2], first[2])
    alt = {'value': float(other[1]), 'unit': {'毫米': 'mm', '厘米': 'cm', '公分': 'cm'}.get(other[2], other[2])} if other else None
    return float(first[1]), unit_of, alt


def _measure(mm, unit):
    if unit == 'in':
        return round(mm / 25.4, 1), 'in'
    return (round(mm / 10, 1), 'cm') if mm >= 100 else (round(mm, 1), 'mm')



# ---- the sizes the user stated, and where each is drawn ----
# A dimensions page prints only sizes the user gave: the longest side (size_mm), the three real dimensions of the plan, or a
# spec or figure whose label is an overall length, width, height, depth, thickness or diameter. What the 3D model measures
# only places each one on the right axis and checks it is plausible; it is never printed (a screwdriver given as 21 cm long
# once came out with "16.7 mm" twice, the second time as the depth of a made-up box).

DIM_WORDS = (('diameter', ('diameter', 'dia', '直径', '外径')), ('thickness', ('thickness', 'thick', '厚度', '厚')),
             ('depth', ('depth', 'deep', '深度', '深')), ('height', ('height', 'high', 'tall', '高度', '高')),
             ('width', ('width', 'wide', '宽度', '宽')), ('length', ('length', 'long', '长度', '全长', '总长', '长')))
# Words that leave a label about the whole product ("Overall length", "外形尺寸"); anything else left over names a part
# ("Blade length", "手柄直径"), whose size is not the product's.
DIM_GENERIC = ('overall', 'total', 'product', 'outer', 'outside', 'external', 'exterior', 'size', 'sizes', 'dimension', 'dimensions',
               'approx', 'approximately', 'max', 'maximum', 'tool', 'item', 'assembled', 'body', 'net', 'of', 'the', 'and', 'mm', 'cm', 'in',
               'inch', 'inches', 'ft', '整体', '整机', '总', '全', '产品', '外形', '外观', '外部', '尺寸', '规格', '约', '最大', '毫米', '厘米',
               '公分', '英寸', '米', '度')
DIM_LETTERS = {'l': 'length', 'w': 'width', 'h': 'height', 'd': 'depth', 't': 'thickness'}
SIZE_UNITS = {**LENGTH_MM, 'm': 1000, 'ft': 304.8, 'feet': 304.8, '″': 25.4, '米': 1000}
UNIT_SHOWN = {'毫米': 'mm', '厘米': 'cm', '公分': 'cm', 'inches': 'in', 'inch': 'in', '"': 'in', '″': 'in', 'feet': 'ft', '米': 'm'}
_SIZE_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|ft|feet|m|"|″|毫米|厘米|公分|米)?(?![a-z])')
PROPORTION_LIMIT = 1.6           # a stated size this far from the model's proportion is not printed (a typo, or a part's size)


def _word_re(word):
    return (r'(?<![a-z])%s(?![a-z])' % re.escape(word)) if word.isascii() else re.escape(word)


def _label_keys(label):
    """The dimension words of a label in the order they come ("长×宽×高", "L x W x H"), and whether the label is about the
    whole product (nothing but dimension and generic words left)."""
    text = str(label or '').lower()
    letters = re.search(r'(?<![a-z])([lwhdt])\s*[x×*/]\s*([lwhdt])(?:\s*[x×*/]\s*([lwhdt]))?(?![a-z])', text)
    if letters:
        keys = [DIM_LETTERS[letter] for letter in letters.groups() if letter]
        text = text[:letters.start()] + ' ' + text[letters.end():]
    else:
        found = []
        for key, words in DIM_WORDS:
            spots = [match.start() for word in words for match in re.finditer(_word_re(word), text)]
            if spots:
                found.append((min(spots), key))
        keys = [key for _, key in sorted(found)]
    rest = text
    for word in sorted([word for _, words in DIM_WORDS for word in words] + list(DIM_GENERIC), key=len, reverse=True):
        rest = re.sub(_word_re(word), ' ', rest)
    rest = re.sub(r'[\d\s.,:;：、，。()（）\[\]x×*/\-–~_|"″\'’]+', ' ', rest).strip()
    return keys, not rest


def _sizes_in(text, default_unit=''):
    """([(number, unit, mm)] written before any bracket or slash, the same for the rest: the other system's figures in
    "200 mm (8 in)"). A number without a unit takes the next unit after it ("40 x 22 x 20 cm"), else ``default_unit``;
    None when a number has no length unit at all."""
    text = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', str(text or '').lower())
    parts = re.split(r'[(（/≈=]', text, maxsplit=1)

    def read(part):
        found = [[number, unit] for number, unit in _SIZE_RE.findall(part)]
        carry = None
        for row in reversed(found):
            carry = row[1] or carry
            row[1] = row[1] or carry
        out = []
        for number, unit in found:
            unit = unit or str(default_unit or '').strip().lower()
            if unit not in SIZE_UNITS:
                return None
            out.append((float(number), UNIT_SHOWN.get(unit, unit), float(number) * SIZE_UNITS[unit]))
        return out
    return read(parts[0]), (read(parts[1]) if len(parts) > 1 else None)


def view_axes(dims, flat):
    """((across, up): the two axes the main dimension view shows, the axis only the product turned shows). Axes index the
    product's [width, depth, height] as the listing poses it; a tool lying flat is shown from above, anything else face on."""
    across = 0 if dims[0] >= dims[1] else 1
    other = 1 - across
    return ((across, other), 2) if flat else ((across, 2), other)


def stated_dimensions(product, dims_mm, flat, plan_mm=None, unit='mm'):
    """(rows, notes): every overall size the user stated, each on the model axis it measures and with the view that shows it.
    A row: {'key', 'axis', 'view' ('main' | 'end'), 'value', 'unit', 'mm', optional 'alt'}; value and unit as the user wrote
    them (or the stated millimetres in the brand's unit). ``dims_mm`` (the model's [width, depth, height] in mm, scaled to the
    stated size) only maps a label to an axis and flags a figure far from the model's proportion: it is never shown.
    ``notes`` (Chinese) say what was left out and why."""
    notes = []
    if not dims_mm or len(dims_mm) != 3:
        return [], notes
    dims = [float(value) for value in dims_mm]
    (across, up), hidden = view_axes(dims, flat)
    longest = max(range(3), key=lambda index: dims[index])
    taken = {}

    def place(axis, row):
        if axis in taken:
            same = taken[axis]
            if abs(same['mm'] - row['mm']) <= max(1.0, row['mm'] * .03):
                if row.get('written') and not same.get('written'):
                    taken[axis] = {**row, 'key': same['key']}         # the figure as written wins over converted millimetres
            else:
                notes.append('尺寸「%s」和「%s」对应同一条边，只标注了「%s」。' % (row['quote'], same['quote'], same['quote']))
            return
        taken[axis] = row

    # The plan's three real dimensions: the largest on the longest axis, and so on; named as the view shows them.
    plan = [float(value) for value in plan_mm] if isinstance(plan_mm, (list, tuple)) and len(plan_mm) == 3 else None
    if plan and all(value > 0 for value in plan):
        names = dict(zip((across, up, hidden), ('length', 'width', 'thickness') if flat else ('width', 'height', 'depth')))
        for axis, mm in zip(sorted(range(3), key=lambda index: -dims[index]), sorted(plan, reverse=True)):
            value, shown, alt = _stated_measure(mm, unit, product)
            place(axis, {'key': names[axis], 'mm': mm, 'value': value, 'unit': shown, 'alt': alt, 'quote': '%g mm' % mm})
    # Labelled specs and figures, as written.
    labelled = []
    rows = [(row.get('label'), row.get('value'), '') for row in product.get('specs') or []]
    rows += [(row.get('label'), row.get('value'), row.get('unit', '')) for row in product.get('figures') or []]
    title_words = set(re.findall(r'[a-z]+', str(product.get('title') or '').lower()))
    for label, value, default_unit in rows:
        keys, whole = _label_keys(label)
        generic = not keys and any(word in str(label or '').lower() for word in ('size', 'dimension', '尺寸'))
        if not keys and not generic:
            continue
        quote = _clip(('%s %s %s' % (label or '', value or '', default_unit or '')).strip(), 30)
        sizes, others = _sizes_in(value, default_unit)
        if not sizes:
            continue                             # not a length ("High-carbon steel", "Long life: 2 h")
        if not whole:
            # "Screwdriver length": the product's own name is not a part
            rest = re.sub(r'[a-z]+', lambda match: ' ' if match.group(0) in title_words else match.group(0), str(label or '').lower())
            if not _label_keys(rest)[1]:
                notes.append('「%s」是部件的尺寸，尺寸图不把它当作整体尺寸标注。' % quote)
                continue
        keys = keys or ['length', 'width', 'height'][:len(sizes)]
        if len(keys) != len(sizes) or len(sizes) > 3:
            notes.append('「%s」的数值和名称对不上（%d 个名称、%d 个数），尺寸图没有标注。' % (quote, len(keys), len(sizes)))
            continue
        for index, (key, (number, shown, mm)) in enumerate(zip(keys, sizes)):
            other = others[index] if others and len(others) == len(sizes) else None
            alt = {'value': other[0], 'unit': other[1]} if other and other[1] != shown else None
            labelled.append({'key': key, 'mm': mm, 'value': number, 'unit': shown, 'alt': alt, 'quote': quote, 'written': True})
    # The longest side the user gave, unless a labelled length or the plan's dimensions already say it. On a product that
    # stands taller than it is long (a ladder, a floor lamp) that side is its height, so the vertical line is not called "Length".
    size = product.get('size_mm')
    size_key = 'height' if longest == 2 and not flat else 'length'
    if size and not plan and not any(row['key'] in ('length', size_key) for row in labelled):
        value, shown, alt = _stated_measure(float(size), unit, product)
        labelled.insert(0, {'key': size_key, 'mm': float(size), 'value': value, 'unit': shown, 'alt': alt, 'quote': '最长边 %g mm' % float(size)})

    order = ('length', 'height', 'thickness', 'diameter', 'width', 'depth')
    for row in sorted(labelled, key=lambda row: order.index(row['key'])):
        free = [axis for axis in range(3) if axis not in taken or abs(taken[axis]['mm'] - row['mm']) <= max(1.0, row['mm'] * .03)]
        horizontal = [axis for axis in (across, 1 - across) if axis in free]
        key = row['key']
        if key == 'length':
            axis = longest
        elif key == 'height':
            axis = 2
        elif key == 'thickness':
            axis = min(free, key=lambda index: dims[index]) if free else None
        elif key == 'diameter':
            # a round handle or blade: the cross-section the main view already shows, when it is free
            axis = next((axis for axis in (up, across, hidden) if axis in free and axis != longest), None)
        elif key == 'width':
            axis = max(horizontal, key=lambda index: dims[index]) if horizontal else None
        elif flat:                               # a flat tool's depth is how thick it lies
            axis = 2
        else:                                    # depth: the horizontal the width did not take
            axis = min(horizontal, key=lambda index: dims[index]) if horizontal else None
        if axis is None or axis not in free:
            notes.append('尺寸「%s」找不到对应的边（已有别的尺寸），没有标注。' % row['quote'])
            continue
        measured = dims[axis]
        if measured > 0 and not 1 / PROPORTION_LIMIT <= row['mm'] / measured <= PROPORTION_LIMIT:
            notes.append('尺寸「%s」和模型的比例不符（模型这条边约 %g mm），尺寸图没有标注，请核对。' % (row['quote'], round(measured)))
            continue
        place(axis, row)

    out = []
    for axis, row in taken.items():
        out.append({'key': row['key'], 'axis': axis, 'view': 'end' if axis == hidden else 'main', 'value': row['value'], 'unit': row['unit'],
                    'mm': round(row['mm'], 2), **({'alt': row['alt']} if row.get('alt') else {})})
    out.sort(key=lambda row: (row['view'] != 'main', row['axis'] != across))
    return out, notes


def jobs_for(kind, ctx):
    """Versions of one page, best first: when one fails the layout checks the next, lighter one is tried. The lines are
    the copywriter's (when it wrote this page's), then the product's name as headline, then the template's own short line."""
    lists = []
    if (ctx.get('copy') or {}).get(kind):
        lists.append([{**job, '_lines': 'model'} for job in _recropped(_jobs_for(kind, ctx))])
    plain = {**ctx, 'copy': {}}
    lists.append([{**job, '_lines': 'name'} for job in _recropped(_jobs_for(kind, plain))])
    lists.append([{**job, '_lines': 'stock'} for job in _recropped(_jobs_for(kind, {**plain, 'stock_lines': True}))])
    out = []; seen = set(); offset = 0
    for jobs in lists:
        if not jobs:
            continue
        heads = {job.get('headline') for job in jobs}
        if heads <= seen:
            continue                         # the same lines again (a page whose headline is not the name's)
        seen |= heads
        top = 0
        for job in jobs:                    # crop variants keep their group, numbered across the lists
            out.append({**job, '_group': job.get('_group', 0) + offset}); top = max(top, job.get('_group', 0))
        offset += top + 1
    return out


CROP_SHIFTS = ((0, -.2), (0, .2), (-.2, 0), (.2, 0))
SOFT_CHECKS = ('frame_cuts_part',)


def hard_issues(qa):
    """The layout problems that make a page wrong; a close-up that still clips a small part is reported on its own."""
    return [row for row in brandkit_render.layout_issues(qa) if row.get('check') not in SOFT_CHECKS]


def _recropped(jobs):
    """After each version whose close-ups have a part map, the same version with their crops moved a little each way:
    tried in turn when a frame edge cuts through a small part (a badge, a rivet)."""
    out = []
    for group, job in enumerate(jobs):
        job = {**job, '_group': group}
        out.append(job)
        name = next((key for key in ('details', 'features') if isinstance(job.get(key), list) and any(isinstance(row, dict) and row.get('detail') for row in job[key])), None)
        if not name or not any((job.get('assets') or {}).get(row.get('detail'), {}).get('_parts') for row in job[name]):
            continue
        for dx, dy in CROP_SHIFTS:
            # a close-up cut round its feature's spot moves half as far, so the spot stays in the middle of the frame
            scale = lambda row: .5 if (job.get('assets') or {}).get(row.get('detail'), {}).get('_centred') else 1
            rows = [{**row, 'focus': [round(min(1, max(0, (row.get('focus') or [.5, .5])[0] + dx * scale(row))), 3),
                                      round(min(1, max(0, (row.get('focus') or [.5, .5])[1] + dy * scale(row))), 3)]} if row.get('detail') else row for row in job[name]]
            out.append({**job, name: rows})
    return out


def _data_line(kind, ctx):
    """A headline made of the product's own facts for a page the copywriter did not write: the selling line the seller gave,
    the parts a close-up shows, the strongest figure, the occasions the scenes show. None when the data has nothing to say."""
    product = ctx['product']; zh = ctx['language'] == 'zh'; name = _clip(humanised(product['title']), 26)
    features = [row for row in product['features'] if row.get('title') and not _not_printed(row['title'], product)]
    if kind == 'T01':
        line = product.get('subline') or product.get('headline')
        return _clip(line, 44) if line else None
    if kind == 'T02':
        shown = [_clip(product['features'][index]['title'], 22) for index, _ in _printable_rows(ctx, ctx.get('macros', []))[:2]] or [_clip(row['title'], 22) for row in features[:2]]
        if len(shown) >= 2:
            return ('%s\n[[%s]]' % (shown[0], shown[1])) if zh else '%s,\n[[%s]]' % (shown[0], shown[1].lower())
        return None
    if kind == 'T04':
        figure = next((row for row in product['figures'] if row.get('value')), None)
        if figure:
            return ('[[%s%s]] %s' % (figure['value'], figure.get('unit', ''), figure['label'])) if zh else '[[%s %s]]\n%s' % (figure['value'], figure.get('unit', ''), figure['label'].lower())
        return None
    if kind == 'T13':
        # "Closed to fully open" from the states the pose agent named, in the page's language.
        labels = [' '.join(str(row.get('label') or '').split()) for row in ctx.get('poses') or []]
        labels = [label for label in labels if label]
        if len(labels) >= 2 and labels[0].lower() != labels[-1].lower():
            first, last = _clip(labels[0], 18), _clip(labels[-1], 18)
            return TEXT['zh' if zh else 'en']['T13_from_to'] % (first, last if zh else last.lower())
        return None
    if kind == 'T08':
        places = [row['title'] for row in ctx.get('scenes') or [] if row.get('title') and row.get('role') in ('use', 'place', 'other')][:2]
        # "On the job site" and "In the workshop" read "from the job site to the workshop"; zh "在车间" reads "车间".
        places = [re.sub(r'^(on|in|at|by|for|inside|around)\s+', '', place.strip(), flags=re.I) if not zh else re.sub(r'^[在于]', '', place.strip())
                  for place in places]
        if len(places) == 2 and places[0].lower() != places[1].lower():
            return ('从%s\n[[到%s]]' % (places[0], places[1])) if zh else 'From %s\n[[to %s]]' % (places[0].lower(), places[1].lower())
        return None
    return None


def _data_eyebrow(ctx, headline):
    """A kicker that says something: the product's name, or its category when the headline already names it."""
    product = ctx['product']; name = _clip(humanised(product['title']), 24)
    if name and name.lower() not in TPL_PLAIN(headline).lower():
        return name
    category = str(product.get('_category') or '')
    return _clip(category, 24) or None


def TPL_PLAIN(text):
    return re.sub(r'\[\[|\]\]|[{}]', '', str(text or ''))


def _not_printed(title, product):
    """Why a page builder leaves this selling point out, or None: as ``printable`` decides, a record never drafted is
    checked only for a file name's marks (its points were typed by the seller)."""
    return mesh_named(title, product if product.get('drafted') else {}, strict=False)


def _printable_rows(ctx, rows):
    """(feature index, …) rows whose selling point may be printed: never one titled with a mesh name (``mesh_named``)."""
    features = ctx['product']['features']
    return [row for row in rows if row[0] < len(features) and not _not_printed(features[row[0]].get('title'), ctx['product'])]


def _jobs_for(kind, ctx):
    t = dict(TEXT[ctx['language']]); product = ctx['product']; assets = ctx['assets']
    written = (ctx.get('copy') or {}).get(kind) or {}
    for key in ('eyebrow', 'headline'):
        if written.get(key):
            t['%s_%s' % (kind, key)] = written[key]          # the copywriter's line for this product, when it passed its checks
    title = humanised(product['title']); headline = product['headline'] or title      # never the upload's file stem
    if not ctx.get('stock_lines'):
        # No usable line from the copywriter: words from the product's own data, never a stock phrase.
        line = _data_line(kind, ctx)
        if line and not written.get('headline'):
            t['%s_headline' % kind] = line
        if not written.get('eyebrow') and '%s_eyebrow' % kind in t:
            t['%s_eyebrow' % kind] = _data_eyebrow(ctx, t.get('%s_headline' % kind, ''))
    if kind == 'T12':
        # The poster takes a picture the image model made; a studio render of the workpiece stays on the inside pages.
        made = [index for index, row in enumerate(ctx['scenes']) if row['role'] in ('use', 'place', 'other')]
        scene_index = next((index for index in made if ctx['scenes'][index].get('check') != 'failed'), made[0] if made else None)
        scene = ctx['scenes'][scene_index] if scene_index is not None else None
        brand = ctx.get('brand'); slot = bool(brand and brand.name)
        base = {'headline': _headline(headline, ctx['language'], 40), 'support': _clip(product['subline'], 90) or None, 'brand_slot': slot,
                'labels': {'brand_slot': brand.name if slot else ''}}
        out = []
        if scene:
            name = 'scene%d' % (scene_index + 1)
            box = assets[name].get('_product')
            chips = _chip_rows(ctx)
            # Words over the scene where it leaves room (the template falls back to the band itself when it does not).
            out.append(_job(kind, ctx, mode='overlay', scene=name, focus=scene['focus'], product_box=box, figures=chips or None,
                            assets={name: assets[name]}, **base))
            out.append(_job(kind, ctx, mode='band', scene=name, focus=scene['focus'], product_box=box, assets={name: assets[name]}, **base))
        out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, **base))
        out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, compact=True, **base))
        # The solid poster's text column is narrow: as poster setting does, a clause mark ending a line may go.
        bare = re.sub(r'[，、；：,;:]\n', '\n', base['headline'])
        if bare != base['headline']:
            out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, **{**base, 'headline': bare}))
            out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, compact=True, **{**base, 'headline': bare}))
        out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, compact=True,
                        **{**base, 'headline': bare, 'support': None}))
        return out
    if kind == 'T01':
        points = [{'title': _keep_together(_clip(product['features'][index]['title'], 24)), 'body': _body(product['features'][index].get('detail'), 48), 'anchor': point}
                  for index, point in _printable_rows(ctx, ctx['anchors']) if point]
        hero = assets['hero']; wide = hero['width'] / hero['height'] >= .8
        modes = ('band', 'diagonal') if wide else ('diagonal', 'band')
        # A part seen as separate patches (two rivets) may be pointed at on its other patch, if the first line crosses the product.
        other = {index: (ctx.get('alternatives') or {}).get(index) or [] for index, point in _printable_rows(ctx, ctx['anchors']) if point}
        shown = [index for index, point in _printable_rows(ctx, ctx['anchors']) if point]
        points = [{**row, 'other_patch': True} if other[index] else row for row, index in zip(points, shown)]
        swapped = [{**row, 'anchor': other[index][0]} if other[index] else row for row, index in zip(points, shown)]
        out = []
        for count in (4, 3, 2):
            if len(points) >= count:
                for mode in modes:
                    for features in ([points[:count], swapped[:count]] if swapped[:count] != points[:count] else [points[:count]]):
                        out.append(_job(kind, ctx, mode=mode, hero='hero', eyebrow=t['T01_eyebrow'], headline=t['T01_headline'],
                                        features=features, assets={'hero': hero}))
        return out
    if kind == 'T03':
        called = {index for index, _ in ctx['anchors'][:4]} if 'T01' in (ctx.get('kinds') or ()) else set()
        usable = _printable_rows(ctx, ctx['details'])
        fresh = [(index, name) for index, name in usable if index not in called]
        chosen = fresh if len(fresh) >= 2 else usable      # the callouts' points again only when there are not two others
        details = [{'title': _keep_together(_clip(product['features'][index]['title'], 22)), 'body': _body(product['features'][index].get('detail'), 40),
                    'detail': name, 'focus': (ctx.get('focus') or {}).get(name, [.5, .5]), 'part_box': assets[name].get('_part_box')}
                   for index, name in chosen]
        details = [{key: value for key, value in row.items() if value is not None} for row in details]
        out = []
        for count in (3, 2):
            if len(details) >= count:
                use = details[:count]
                fields = dict(mode='split', hero='hero', eyebrow=t['T03_eyebrow'], headline=_headline(title or headline, ctx['language'], 30),
                              assets={'hero': assets['hero'], **{row['detail']: assets[row['detail']] for row in use}}, details=use)
                out.append(_job(kind, ctx, subhead=_clip(product['subline'], 60) or None, **fields))
                out.append(_job(kind, ctx, **fields))
        return out
    if kind == 'T13':
        # The product in the states of its moving parts: one camera, one window, one scale (``pose_assets``); the template
        # sets them side by side or, for a long product, stacked. Three states, else the first and the last.
        poses = [{'asset': row['asset'], 'label': _clip(row.get('label') or '', 18)} for row in ctx.get('poses') or [] if row.get('asset') in assets][:3]
        groups = [poses] + ([[poses[0], poses[-1]]] if len(poses) == 3 else [])
        out = []
        for use in groups:
            if len(use) < 2:
                continue
            for mode in ('auto', 'columns', 'rows'):
                out.append(_job(kind, ctx, mode=mode, eyebrow=t['T13_eyebrow'], headline=_headline(t['T13_headline'], ctx['language'], 40), poses=use,
                                assets={row['asset']: assets[row['asset']] for row in use}))
        return out
    if kind == 'T02':
        rows = [{'title': _keep_together(_clip(product['features'][index]['title'], 22)), 'body': _body(product['features'][index].get('detail'), 48), 'detail': name,
                 'focus': (ctx.get('focus') or {}).get(name, [.5, .5]), 'parts': assets[name].get('_parts') or []} for index, name in _printable_rows(ctx, ctx['macros'])]
        out = []
        for count in (2, 1):
            if len(rows) >= count:
                use = rows[:count]
                out.append(_job(kind, ctx, mode='macro', hero=None, eyebrow=t['T02_eyebrow'], headline=t['T02_headline'], features=use,
                                assets={row['detail']: assets[row['detail']] for row in use}))
        return out
    if kind == 'T04':
        # A scene whose product check passed, other than the poster's when there is one.
        made = [index for index, row in enumerate(ctx['scenes']) if row['role'] in ('use', 'place', 'other')]
        poster = next((index for index in made if ctx['scenes'][index].get('check') != 'failed'), made[0] if made else None)
        # Fidelity first: the poster's scene again rather than one whose product check failed.
        order = sorted(range(len(ctx['scenes'])), key=lambda index: (ctx['scenes'][index].get('check') == 'failed', index == poster, index))
        scene = ctx['scenes'][order[0]]; name = 'scene%d' % (order[0] + 1)
        specs = _chip_rows(ctx, 3)
        if len(specs) < 3:           # a word spec (a material) fills the row after the numbers
            for row in product['specs']:
                number, _ = _number_unit(row['value'])
                if number is None and len(specs) < 3:
                    specs.append({'label': _clip(row['label'], 18), 'text': _clip(row['value'], 30), 'icon': _icon_for(row['label'], row['value'])})
        specs = [{key: value for key, value in row.items() if value is not None} for row in specs]
        counts = sorted({min(3, len(specs)), min(2, len(specs))} - {0}, reverse=True)
        return [_job(kind, ctx, scene=name, focus=scene['focus'], eyebrow=t['T04_eyebrow'], headline=t['T04_headline'],
                     scene_product=assets[name].get('_product'), specs=specs[:count], assets={name: assets[name]}) for count in counts]
    if kind == 'T08':
        good = [index for index in range(len(ctx['scenes'])) if ctx['scenes'][index].get('check') != 'failed']
        # A scene whose product check failed is left out while two others can carry the page.
        ranked = good[:3] if len(good) >= 2 else sorted(range(len(ctx['scenes'])), key=lambda index: (index not in good, index))[:3]
        items = [{'asset': 'scene%d' % (index + 1), 'title': _clip(ctx['scenes'][index]['title'], 20),
                  'body': _clause_lines(_clip(ctx['scenes'][index].get('body'), 44), ctx['language'], 8) or None,
                  'focus': ctx['scenes'][index]['focus'], 'box': assets['scene%d' % (index + 1)].get('_product')} for index in sorted(ranked)]
        if not all(row['body'] for row in items):
            # Captions under some tiles only would break the row's rhythm.
            items = [{**row, 'body': None} for row in items]
        out = []
        for count in (3, 2):
            if len(items) >= count:
                use = items[:count]
                # Big photos first: a mosaic of three or two halves; then one wide row per scene (a long tool held across the
                # picture cannot be cropped into a tall tile); tall columns last.
                layouts = (({'mode': 'stagger'}, {'mode': 'stagger', 'stagger_style': 'rows'}, {'mode': 'columns'}) if count == 3 else
                           ({'mode': 'split'}, {'mode': 'split', 'split_dir': 'rows'}, {'mode': 'columns'}))
                for layout in layouts:
                    out.append(_job(kind, ctx, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'], items=use,
                                    assets={row['asset']: assets[row['asset']] for row in use}, **layout))
                    out.append(_job(kind, ctx, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'],
                                    items=[{**row, 'body': None} for row in use], assets={row['asset']: assets[row['asset']] for row in use}, **layout))
        return out
    if kind == 'T06':
        side = assets['side']; box = side['content_bbox']; end_asset = assets.get('end')
        # Only the sizes the user stated (stated_dimensions), each on the view that shows it: the product face on (or from
        # above when it lies flat), and the same product turned a quarter for the one size that view hides, at the same scale.
        rows = ctx['stated'] if 'stated' in ctx else stated_dimensions(product, ctx.get('dims_mm'), ctx.get('flat'), unit=ctx['unit'])[0]
        if not end_asset:
            rows = [row for row in rows if row['view'] == 'main']
        if not rows or not ctx.get('dims_mm'):
            return []
        (across, _), _ = view_axes(ctx['dims_mm'], ctx.get('flat'))

        def dimension(row, edge, at):
            """A dimension line along one edge of a view's content box: 'below', 'left' or 'right' of the product."""
            b = edge
            start, end, side_dir, place = {'below': ([b[0], b[3]], [b[2], b[3]], [0, 1], b[3] + .07),
                                           'left': ([b[0], b[1]], [b[0], b[3]], [-1, 0], b[0] - .07),
                                           'right': ([b[2], b[1]], [b[2], b[3]], [1, 0], b[2] + .07)}[at]
            letter = t[row['key']][:1].upper() if ctx['language'] == 'en' else row['key'][:1].upper()
            return {'key': row['key'], 'key_letter': letter, 'name': t[row['key']], 'value': row['value'], 'unit': row['unit'], 'from': start,
                    'to': end, 'side': side_dir, 'at': place, **({'alt': row['alt']} if row.get('alt') else {})}
        main = [row for row in rows if row['view'] == 'main']
        dims = [dimension(row, box, 'below' if row['axis'] == across else 'left') for row in sorted(main, key=lambda row: row['axis'] == across)]
        end = None
        turned = [row for row in rows if row['view'] == 'end']
        if turned:
            ebox = end_asset['content_bbox']; flat = bool(ctx.get('flat'))
            # The two views share an edge: the length across (a flat tool, seen from above and then from the front) or the
            # height (anything else, face on and then from the end); its pixels set the turned view's scale.
            if flat:
                scale = (box[2] - box[0]) * side['width'] / max(1e-6, (ebox[2] - ebox[0]) * end_asset['width'])
            else:
                scale = (box[3] - box[1]) * side['height'] / max(1e-6, (ebox[3] - ebox[1]) * end_asset['height'])
            end = {'asset': 'end', 'scale': round(scale, 5), 'at': 'below' if flat else 'right',
                   'dimensions': [dimension(row, ebox, 'right' if flat else 'below') for row in turned]}
        # Figures on the product first (the Amazon dimension pages); a table when the page is too narrow for them.
        modes = ('onproduct', 'table')
        extra = [{'label': _clip(row['label'], 18), 'value': row['value']} for row in product['specs']
                 if not any(word in row['label'].lower() for word in ('高', '宽', '长', '深', '厚', '直径', 'height', 'width', 'length', 'depth',
                                                                      'thick', 'diameter', 'size', 'dimension', '尺寸'))][:3]
        return [_job(kind, ctx, mode=mode, hero='side', eyebrow=_clip(title, 24) or t['T06_eyebrow'], headline=t['T06_headline'], dimensions=dims,
                     end=end, specs=extra or None, disclaimer=t['table_note'].rstrip('。.'), table_note=t['table_note'] if mode == 'table' else None,
                     assets={'side': side, **({'end': end_asset} if end else {})}) for mode in modes]
    if kind == 'T05':
        import components
        items = [{'asset': 'hero', 'name': _clip(title or t['main'], 20), 'code': _clip(product.get('model'), 20) or None, 'qty': 1}]
        for index, part in enumerate(ctx['parts']):
            role = components.ROLES.get(part.get('role'), ('配件', 'Accessory'))
            items.append({'asset': 'part%d' % index, 'name': _clip(part.get('name') or role[ctx['language'] == 'en'], 20),
                          'code': _clip(part.get('sku'), 20) or None, 'qty': part.get('quantity') or None})
        items = [{key: value for key, value in row.items() if value is not None} for row in items]
        counts = sorted({min(len(items), count) for count in (8, 6, 4, 3)}, reverse=True)
        jobs = [_job(kind, ctx, headline=t['T05_headline'], items=items[:count], note=t['T05_note'],
                     assets={row['asset']: assets[row['asset']] for row in items[:count]}) for count in counts]
        if len(items) >= 6:             # many pieces: laid out flat, at their relative sizes
            jobs.insert(1, {**jobs[0], 'mode': 'knolling'})
        return jobs
    return []


def _measured(qa):
    """What the browser measured, for the art director: where each picture lies and whether a whole product is inside the page,
    which frames are close-ups by design, text sizes at phone width. Ground truth it must not contradict."""
    width, height = (qa.get('canvas') or [1500, 1500])[:2]
    elements = qa.get('elements', [])
    texts = [row for row in elements if row.get('kind') == 'text']
    small = min((row.get('equiv_px_at_360', 99) for row in texts if row.get('tier') in ('headline', 'important')), default=None)
    pictures = []; by_id = {row.get('id'): row for row in elements}
    for row in elements:
        if row.get('kind') in ('product_image', 'detail_image') and row.get('w'):
            if row.get('inframe') and by_id.get(row['id'] + '_frame'):
                row = {**by_id[row['id'] + '_frame'], 'id': row['id'], 'crop': row.get('crop')}       # what shows is the frame, not the picture behind it
            bleed = row.get('bleed')
            inside = bleed or (row['x'] >= -1 and row['y'] >= -1 and row['x'] + row['w'] <= width + 1 and row['y'] + row['h'] <= height + 1)
            whole = row.get('crop') == 'contain' and not bleed
            pictures.append({'id': row['id'], 'box_px': [round(row['x']), round(row['y']), round(row['w']), round(row['h'])],
                             'shows': 'the whole product, uncropped' if whole and inside else 'a crop by design (close-up or scene frame)',
                             'fully_inside_page': bool(inside)})
    return {'layout_checks_passed': not hard_issues(qa), 'canvas_px': [width, height], 'pictures': pictures,
            'smallest_important_text_px_at_360': small, 'text': [{'id': row['id'], 'lines': row.get('lines'), 'text': row.get('text', '')[:200]}
                                                                  for row in texts][:14], 'notes': qa.get('notes', [])[:3]}


def _better(a, b):
    """Whether verdict a beats verdict b: fewer vetoes first, then the higher score."""
    if b is None:
        return True
    return (len(a['vetoes']), -a['score']) < (len(b['vetoes']), -b['score'])


def _cut_text(qa, problems):
    """The ids of the texts a page cannot hold (wider than their box, or past the canvas edge): sorted, empty when none."""
    texts = {row.get('id') for row in qa.get('elements', []) if row.get('kind') == 'text'}
    return sorted({str(row.get('id')) for row in problems
                   if row.get('check') == 'text_overflow' or (row.get('check') == 'out_of_canvas' and row.get('id') in texts)})


def lay_out(kind, ctx, out, work):
    """Lay a page out: the first version that passes the layout checks (or the one with the fewest problems), then,
    when a vision model is configured, the art director's review and up to two rounds of fixes.
    Returns (qa, notes, review record)."""
    import brandkit_review
    candidates = jobs_for(kind, ctx)
    if not candidates:
        raise ValueError('「%s」没有可用的内容' % KINDS[kind][0])
    try:            # the versions this page could be laid out in, kept beside it (scripts/brandkit_preview.py re-renders them)
        folder = out.parent / 'jobs'; folder.mkdir(exist_ok=True)
        (folder / (kind + '.json')).write_text(json.dumps(candidates[:12], ensure_ascii=False, default=str), encoding='utf-8')
    except (OSError, TypeError):
        pass
    best = None; best_key = None
    for number, job in enumerate(candidates):
        if best_key and best_key[1] == 0 and job.get('_group', number) != best_key[2]:
            break           # a version without hard problems, and none of its other crops is clean: keep it, not a leaner layout
        target = out if number == 0 else out.with_name('%s.try%d.png' % (out.stem, number))
        qa = brandkit_render.render(kind, job, target, work)
        problems = brandkit_render.layout_issues(qa)
        # A crop that clips a small part only decides between crops of one layout; it never trades away content.
        hard = [row for row in problems if row.get('check') not in SOFT_CHECKS]
        # A version whose words are cut off is never made (below): any version that holds its words wins over it, even one
        # with more of the other problems.
        key = (bool(_cut_text(qa, problems)), len(hard), job.get('_group', number), len(problems) - len(hard))
        if best is None or key < best_key:
            if best and best[2] != out:
                best[2].unlink(missing_ok=True)
            best = (qa, problems, target, number); best_key = key
        elif target != out:
            target.unlink(missing_ok=True)
        if not problems:
            break
    qa, problems, target, number = best
    if target != out:
        shutil.move(str(target), str(out))
    job = candidates[number]
    notes = []
    def layout(row):          # what a reader would call a different version: not where a line points or a crop sits
        plain = {key: value for key, value in row.items() if not key.startswith('_')}
        for key in ('features', 'details'):
            if isinstance(plain.get(key), list):
                plain[key] = [{k: v for k, v in item.items() if k not in ('anchor', 'focus', 'other_patch')} if isinstance(item, dict) else item
                              for item in plain[key]]
        return plain
    if layout(job) != layout(candidates[0]):
        notes.append('为通过版式检查，这张图用了精简版本（第 %d 种排法）。' % (job.get('_group', number) + 1))
    if any(row.get('check') == 'frame_cuts_part' for row in problems):
        notes.append('特写框仍切到了小部件（%s），换几种取景都没能避开。' % '、'.join(sorted({row['id'].split('×')[-1] for row in problems if row.get('check') == 'frame_cuts_part'})))
    record = {'status': 'not_verified', 'rounds': 0, 'applied': []}
    if ctx.get('review') and not ctx.get('review_refused'):
        reference = ctx['assets']['hero']['path']
        facts = ctx.get('facts') or brandkit_review.facts_of(ctx['product'])
        scenes = [('scene%d' % (index + 1), ctx['assets']['scene%d' % (index + 1)], row['focus']) for index, row in enumerate(ctx.get('scenes') or [])]
        try:
            verdict = brandkit_review.checked(kind, job, out, ctx['product'], reference, _measured(qa), ctx.get('facts'))
        except Exception as exc:
            verdict = None; notes.append('视觉审核没有完成（%s），这张图未经审核。' % str(exc)[:80])
            if re.search(r'HTTP 40[123]', str(exc)):
                ctx['review_refused'] = str(exc)[:80]        # quota or key refused: the other pages do not ask again
        if verdict:
            first = verdict; tried = {number}; current = (job, verdict, qa)
            for round_number in range(1, brandkit_review.MAX_REVISIONS + 1):
                if brandkit_review.passed(current[1]):
                    break
                revised, applied = brandkit_review.revise(kind, current[0], current[1], facts, scenes)
                if revised in ('next_layout', 'default_text'):
                    stock = revised == 'default_text'
                    following = next((index for index in range(len(candidates)) if index not in tried
                                      and (not stock or candidates[index].get('_lines') == 'stock')), None)
                    if following is None:
                        break
                    tried.add(following); revised = candidates[following]
                if revised is None:
                    break
                trial = out.with_name('%s.rev%d.png' % (out.stem, round_number))
                trial_qa = brandkit_render.render(kind, revised, trial, work)
                if hard_issues(trial_qa):
                    trial.unlink(missing_ok=True); notes.append('审核建议的修改（%s）没有通过版式检查，未采用。' % '、'.join(applied)); break
                try:
                    trial_verdict = brandkit_review.checked(kind, revised, trial, ctx['product'], reference, _measured(trial_qa), ctx.get('facts'))
                except Exception:
                    trial.unlink(missing_ok=True); break
                record['rounds'] = round_number
                if _better(trial_verdict, current[1]):
                    shutil.move(str(trial), str(out)); current = (revised, trial_verdict, trial_qa); record['applied'] += applied; qa = trial_qa
                else:
                    trial.unlink(missing_ok=True); notes.append('第 %d 轮修改（%s）没有让评分变好，保留原版。' % (round_number, '、'.join(applied)))
                    break
            verdict = current[1]
            failed_scenes = sorted({key for key in current[0].get('assets', {}) if key.startswith('scene')
                                    and (ctx['scenes'][int(key[5:]) - 1] if int(key[5:]) <= len(ctx['scenes']) else {}).get('check') == 'failed'})
            if failed_scenes:          # the scene generator's own product check is a fact, not an opinion
                verdict = {**verdict, 'vetoes': verdict['vetoes'] + [{'kind': 'scene_product_mismatch', 'evidence': '场景图的产品核对未通过：%s' % '、'.join(failed_scenes)}]}
            record.update(status='passed' if brandkit_review.passed(verdict) and not record['applied'] else
                                 'revised' if brandkit_review.passed(verdict) else 'failed',
                          score=verdict['score'], first_score=first['score'], scores=verdict['scores'], model=verdict.get('model'),
                          vetoes=[row['kind'] for row in verdict['vetoes']], summary=verdict['summary'], refuted=verdict.get('refuted', 0),
                          issues=[{'region': row.get('region', ''), 'evidence': row.get('evidence', '')} for row in verdict['issues']])
            if record['applied']:
                notes.append('按视觉审核修改：%s（%d → %d 分）。' % ('、'.join(record['applied']), first['score'], verdict['score']))
            if record['status'] == 'failed':
                reasons = '；'.join(row.get('evidence', '') for row in verdict['vetoes'] + verdict['issues'] if row.get('evidence'))[:220]
                notes.append('视觉审核未通过（%d 分%s）%s' % (verdict['score'], '，否决项 ' + '、'.join(record['vetoes']) if record['vetoes'] else '',
                                                        '：' + reasons if reasons else '。'))
    problems = hard_issues(qa)
    # Text the page cannot hold is cut off at the canvas edge ("06_DIAGONAL_C…" on the poster): the browser measured it
    # (text_overflow), but the page with the fewest problems used to be kept anyway. A page whose words do not fit is not made.
    clipped = _cut_text(qa, problems)
    if clipped:
        out.unlink(missing_ok=True)
        raise ValueError('文字放不下（%s），试过的排法都会把字截断，这张图没有生成；请把这段文字改短' % '、'.join(clipped))
    if problems:
        notes.append('版式检查仍有 %d 处问题：%s。' % (len(problems), '、'.join(sorted({row['check'] for row in problems}))))
    lacking = sorted({str(row.get('id', '')).rsplit(' ', 1)[0] for row in qa.get('issues', []) if row.get('check') == 'font_missing'})
    if lacking:
        chinese = [name for name in lacking if 'SC' in name or 'CJK' in name]
        notes.append(('中文字体 %s 还没有下载成功，这张图暂用系统中文字体（如微软雅黑）；联网后重新生成即可。' % '、'.join(chinese)) if chinese == lacking
                     else '字体 %s 没有找到，已用系统字体代替；请确认 brandkit/fonts 完整，或联网后重新生成。' % '、'.join(lacking))
    for line in qa.get('notes', [])[:2]:
        notes.append(str(line)[:160])
    # The same page as an editable design file: live text, vector shapes and named layers (brandkit/engine/svg_export.js).
    final = current[0] if 'current' in locals() else job
    try:
        brandkit_render.export_svg(kind, final, out.parent / 'svg' / (out.stem + '.svg'), work)
    except Exception as exc:          # the PNG stands without it
        notes.append('可编辑 SVG 没有导出：%s' % str(exc)[:120])
    return qa, notes, record

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
           'qty_pending': '数量待确认', 'qty_prefix': '数量 ', 'main': '主件', 'height': '高度', 'width': '宽度', 'length': '长度', 'depth': '深度', 'thickness': '厚度',
           'table_note': '尺寸为近似值，以实物为准。', 'depth_note': '深度 %s。',
           'T13_eyebrow': '活动部件', 'T13_headline': '一件工具，多种状态', 'T13_from_to': '从%s\n[[到%s]]',
           'scene_titles': ('上手使用', '随时待命', '换个场景')},
    'en': {'T01_eyebrow': 'Part overview', 'T01_headline': 'Every key part,\nclearly shown', 'T03_eyebrow': 'Details', 'T02_eyebrow': 'Up close',
           'T02_headline': 'The details, up close', 'T04_headline': 'Key figures', 'T04_eyebrow': 'Specs', 'T08_eyebrow': 'In use',
           'T08_headline': 'Made for the job', 'T06_eyebrow': 'Dimensions', 'T06_headline': 'Overall size', 'T05_headline': "What's in the box",
           'T05_note': 'Items not shown to scale', 'qty_pending': 'Qty to be confirmed', 'qty_prefix': 'Qty ', 'main': 'Main unit',
           'height': 'Height', 'width': 'Width', 'length': 'Length', 'depth': 'Depth', 'thickness': 'Thickness',
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


def requirements(product, parts, size_known, scene_supply, poses=0):
    """(has, why): whether each page has what it needs, and the reason when it does not. ``poses``: how many states of its
    moving parts the product can be shown in (the pose agent's, ``articulation.states``) or pose pictures there are."""
    points = len(anchored(product))
    callouts = points + len(pointed(product))
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
    dx = (box[2] - box[0]) * slack; dy = (box[3] - box[1]) * slack
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

SCENE_PLAN = 3        # how the scenes are planned; a change makes every listing's scenes again (2: a third scene in its own setting;
                      # 3: moving parts set and usage rules from the pose agent)


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
        import scene_stylist
        config = scene_stylist._config(None)
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
        if record.get('check', {}).get('status') == 'failed':
            notes.append('第 %d 张场景图的产品核对没有完全通过，请检查后再使用。' % (index + 1))
        rows.append({'path': str(picture['png']), 'title': caption['title'], 'body': caption['body'], 'role': brief['role'],
                     'focus': [round((box[0] + box[2]) / 2, 3), round((box[1] + box[3]) / 2, 3)], 'box': box,
                     'summary': record.get('summary'), 'model': record.get('model'), 'people': record.get('people'),
                     'check': (record.get('check') or {}).get('status') or 'unchecked'})
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
    """The product's figures as chips (icon, value, unit, label): its stated figures and number specs, then measured sizes."""
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
    # Measured sizes are written in the unit the stated figures use, so one row never mixes mm and cm.
    stated = next((str(row.get('unit', '')).strip().lower() for row in rows if str(row.get('unit', '')).strip().lower() in ('mm', 'cm', 'in')), None)
    def measure(mm):
        if stated == 'mm':
            return round(mm), 'mm'
        if stated == 'cm':
            return round(mm / 10, 1), 'cm'
        return _measure(mm, 'in' if stated == 'in' else ctx['unit'])
    for key, mm in dimensions_mm(ctx):
        if len(rows) >= limit:
            break
        # A size the stated figures already give (476 mm = 47.6 cm) is not shown twice.
        if any(value and abs(value - mm) <= max(2.0, mm * .02) for value in shown) or any(row['label'] == t[key] for row in rows):
            continue
        value, unit = measure(mm)
        icon = {'height': 'arrow-autofit-height', 'width': 'arrow-autofit-width', 'length': 'arrow-autofit-width', 'depth': 'arrows-diagonal',
                'thickness': 'arrows-diagonal'}.get(key, 'ruler-measure')
        rows.append({'label': t[key], 'value': value, 'unit': unit, 'icon': icon})
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
    product = ctx['product']; zh = ctx['language'] == 'zh'; name = _clip(product['title'] or '', 26)
    features = [row for row in product['features'] if row.get('title')]
    if kind == 'T01':
        line = product.get('subline') or product.get('headline')
        return _clip(line, 44) if line else None
    if kind == 'T02':
        shown = [_clip(product['features'][index]['title'], 22) for index, _ in ctx.get('macros', [])[:2]] or [_clip(row['title'], 22) for row in features[:2]]
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
    product = ctx['product']; name = _clip(product['title'] or '', 24)
    if name and name.lower() not in TPL_PLAIN(headline).lower():
        return name
    category = str(product.get('_category') or '')
    return _clip(category, 24) or None


def TPL_PLAIN(text):
    return re.sub(r'\[\[|\]\]|[{}]', '', str(text or ''))


def _jobs_for(kind, ctx):
    t = dict(TEXT[ctx['language']]); product = ctx['product']; assets = ctx['assets']
    written = (ctx.get('copy') or {}).get(kind) or {}
    for key in ('eyebrow', 'headline'):
        if written.get(key):
            t['%s_%s' % (kind, key)] = written[key]          # the copywriter's line for this product, when it passed its checks
    title = product['title'] or ''; headline = product['headline'] or title
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
                  for index, point in ctx['anchors'] if point]
        hero = assets['hero']; wide = hero['width'] / hero['height'] >= .8
        modes = ('band', 'diagonal') if wide else ('diagonal', 'band')
        # A part seen as separate patches (two rivets) may be pointed at on its other patch, if the first line crosses the product.
        other = {index: (ctx.get('alternatives') or {}).get(index) or [] for index, point in ctx['anchors'] if point}
        shown = [index for index, point in ctx['anchors'] if point]
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
        fresh = [(index, name) for index, name in ctx['details'] if index not in called]
        chosen = fresh if len(fresh) >= 2 else ctx['details']      # the callouts' points again only when there are not two others
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
                 'focus': (ctx.get('focus') or {}).get(name, [.5, .5]), 'parts': assets[name].get('_parts') or []} for index, name in ctx['macros']]
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
                # Big photos first: a mosaic of three or two halves; tall columns only when those cannot hold the product.
                for mode in (('stagger', 'columns') if count == 3 else ('split', 'columns')):
                    out.append(_job(kind, ctx, mode=mode, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'], items=use,
                                    assets={row['asset']: assets[row['asset']] for row in use}))
                    out.append(_job(kind, ctx, mode=mode, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'],
                                    items=[{**row, 'body': None} for row in use], assets={row['asset']: assets[row['asset']] for row in use}))
        return out
    if kind == 'T06':
        side = assets['side']; box = side['content_bbox']
        rows = dimensions_mm(ctx); unit = ctx['unit']
        (across_key, across), (up_key, up), (depth_key, depth) = rows
        dims = []
        for key, letter, mm, start, end, side_dir, at in ((up_key, t[up_key][:1].upper() if ctx['language'] == 'en' else up_key[:1].upper(), up,
                                                           [box[0], box[1]], [box[0], box[3]], [-1, 0], box[0] - .07),
                                                          (across_key, t[across_key][:1].upper() if ctx['language'] == 'en' else across_key[:1].upper(), across,
                                                           [box[0], box[3]], [box[2], box[3]], [0, 1], box[3] + .07)):
            # the seller's own figure ("200 mm (8 in)") wins over the measured one converted (20 cm / 7.9 in)
            value, shown, alt = _stated_measure(mm, unit, product)
            dims.append({'key': key, 'key_letter': letter, 'name': t[key], 'value': value, 'unit': shown, 'from': start, 'to': end,
                         'side': side_dir, 'at': at, **({'alt': alt} if alt else {})})
        depth_value, depth_unit, _ = _stated_measure(depth, unit, product)
        # Each sentence on its own line, so the depth figure never wraps away from its label.
        note = t['table_note'] + ('\n' if ctx['language'] == 'zh' else ' ') + ('%s %g\u00a0%s%s' % (t[depth_key], depth_value, depth_unit, '。' if ctx['language'] == 'zh' else '.'))
        # Figures on the product first (the Amazon dimension pages); a table when the page is too narrow for them.
        modes = ('onproduct', 'table')
        extra = [{'label': _clip(row['label'], 18), 'value': row['value']} for row in product['specs']
                 if not any(word in row['label'].lower() for word in ('高', '宽', '长', '深', 'height', 'width', 'length', 'depth', 'size', '尺寸'))][:3]
        return [_job(kind, ctx, mode=mode, hero='side', eyebrow=_clip(title, 24) or t['T06_eyebrow'], headline=t['T06_headline'], dimensions=dims,
                     depth={'value': depth_value, 'unit': depth_unit, 'name': t[depth_key], 'key': depth_key}, specs=extra or None, disclaimer=t['table_note'].rstrip('。.') ,
                     table_note=note if mode == 'table' else None, assets={'side': side}) for mode in modes]
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
        if best_key and best_key[0] == 0 and job.get('_group', number) != best_key[1]:
            break           # a version without hard problems, and none of its other crops is clean: keep it, not a leaner layout
        target = out if number == 0 else out.with_name('%s.try%d.png' % (out.stem, number))
        qa = brandkit_render.render(kind, job, target, work)
        problems = brandkit_render.layout_issues(qa)
        # A crop that clips a small part only decides between crops of one layout; it never trades away content.
        hard = [row for row in problems if row.get('check') not in SOFT_CHECKS]
        key = (len(hard), job.get('_group', number), len(problems) - len(hard))
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

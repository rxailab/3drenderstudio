"""The listing set, laid out with the brand-kit templates (``brandkit/templates``).

The listing's pictures come from the product's own 3D model: a cut-out of the whole product, close-ups
of the parts its selling points name, a straight-on view to measure, the kit's components. Its usage
scenes are made by the image model from those renders, with the product locked by a mask
(``scene_lock``), so scene generation is now one step of building the set instead of a tool of its own.

This module turns those pictures and the product record into jobs for the templates, lays every page
out in a headless browser (``brandkit_render``), checks the measured layout, and falls back to a
lighter version of a page (fewer labels, another arrangement) when a check fails.

Templates (each one rule-driven HTML; see ``brandkit/templates/T*.json`` for its reference map):
T12 poster · T01 callouts · T03 product + detail column · T02 dark close-ups · T04 scene + figures band ·
T08 usage scenes · T06 dimensions · T05 in the box. T07, T09, T10 and T11 need evidence a 3D model
does not carry (module levels, test results, connection maps, a model range) and are not built.
"""
import hashlib
import json
import math
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import brandkit_render

# kind: (zh, en)
KINDS = {
    'T12': ('海报主图', 'Hero poster'),
    'T01': ('功能标注', 'Callouts'),
    'T03': ('主体 + 细节栏', 'Product + details'),
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
AUTO = ('T12', 'T01', 'T03', 'T02', 'T04', 'T08', 'T06', 'T05')
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
           'qty_pending': '数量待确认', 'qty_prefix': '数量 ', 'main': '主件', 'height': '高度', 'width': '宽度', 'length': '长度', 'depth': '深度',
           'table_note': '按 3D 模型测量。', 'depth_note': '深度 %s。',
           'scene_titles': ('上手使用', '随时待命', '换个场景')},
    'en': {'T01_eyebrow': 'Part overview', 'T01_headline': 'Every key part,\nclearly shown', 'T03_eyebrow': 'Details', 'T02_eyebrow': 'Up close',
           'T02_headline': 'The details, up close', 'T04_headline': 'Key figures', 'T04_eyebrow': 'Specs', 'T08_eyebrow': 'In use',
           'T08_headline': 'Made for the job', 'T06_eyebrow': 'Dimensions', 'T06_headline': 'Overall size', 'T05_headline': "What's in the box",
           'T05_note': 'Items not shown to scale', 'qty_pending': 'Qty to be confirmed', 'qty_prefix': 'Qty ', 'main': 'Main unit',
           'height': 'Height', 'width': 'Width', 'length': 'Length', 'depth': 'Depth',
           'table_note': 'Measured on the 3D model.', 'depth_note': 'Depth %s.',
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

def anchored(product):
    """Indexes of the selling points that name parts of the model (they can be pointed at and shown close up)."""
    return [index for index, feature in enumerate(product['features']) if feature.get('parts')]


def requirements(product, parts, size_known, scene_supply):
    """(has, why): whether each page has what it needs, and the reason when it does not."""
    points = len(anchored(product))
    figures = bool(product['figures'] or product['specs'] or size_known)
    has = {'T12': True, 'T01': points >= 2, 'T03': points >= 2, 'T02': points >= 1, 'T04': scene_supply >= 1 and figures,
           'T08': scene_supply >= 2, 'T06': size_known, 'T05': bool(parts), 'T07': False, 'T09': False, 'T10': False, 'T11': False}
    why = {'T01': '至少 2 条卖点需要选择对应部件', 'T03': '至少 2 条卖点需要选择对应部件', 'T02': '卖点没有选择对应的部件',
           'T04': '需要场景图（开启 AI 场景或工件图），以及规格、大数字指标或真实尺寸' if not figures else '需要场景图：请开启 AI 场景图或工件使用图',
           'T08': '需要至少 2 张场景图：请开启 AI 场景图（2 张以上）或工件使用图', 'T06': '没有填写真实尺寸', 'T05': '还没有处理完成的组件', **BLOCKED}
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


def scene_asset(source, target):
    """A scene picture as an opaque PNG for the templates."""
    with Image.open(source) as opened:
        opened.convert('RGB').save(target, 'PNG', optimize=True)
    return brandkit_render.asset(target)


# ---- usage scenes (from the image model, the product locked by its mask) ----

def scene_key(project, product, settings):
    plan = project.get('plan') or {}
    raw = json.dumps([project.get('model_path'), plan.get('rotation'), plan.get('material_overrides'), product.get('title'), settings],
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
        other = others[0] if others else None
        wants = first.get('people') if settings['people'] == 'auto' else settings['people'] in ('include', 'hands')
        third = {**first, 'segment_id': other or segment, 'people': bool(wants), 'framing': first.get('framing') if wants else None,
                 'scene': scene_audience.fixed_scene(other, bool(wants)) if other else
                          (first.get('scene', '') + ' Another setting where the same buyer would use it.')}
        briefs.append({'proposal': third, 'role': 'other'})
    return briefs[:count]


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
        'picture and name only what it shows: the place or moment you can see (a desk by a window, a café table), never one it only '
        'suggests (no "office" without office things in view, no "commute" on a table at home). Title: the place or moment. Line: what '
        'the buyer sees there. No numbers, claims, benefits, prices or brands. Respect the character limits strictly. In Chinese, write '
        'the line as two short clauses joined by "，", each at most 8 characters.')


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
    out = []; done = 0; taken = set()
    for row in rows:
        try:
            facts = {'product': product['title'], 'language': 'Simplified Chinese' if language == 'zh' else 'English',
                     'title_max_chars': limit[0], 'line_max_chars': limit[1], 'titles_already_used': sorted(taken)}
            answer = brandkit_review._ask(vision, SEEN, facts, schema, image_path=row['path'])
            title = ' '.join(str(answer.get('title', '')).split()); body = _fit_line(' '.join(str(answer.get('body', '')).split()), limit[1])
            if title and len(title) <= limit[0] and title not in taken and not re.search(r'\d', title + body):
                row = {**row, 'title': title, 'body': body, 'caption': 'seen'}; done += 1
        except Exception:
            pass
        taken.add(row['title']); out.append(row)
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
            if any(row.get('caption') != 'seen' for row in rows):          # made before captions were read off the pictures
                rows, done = seen_captions(product, rows)
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

def _clip(text, limit):
    """Text cut to a length at a whole character; line breaks the author put in are kept."""
    lines = [' '.join(line.split()) for line in str(text or '').split('\n')]
    text = '\n'.join(line for line in lines if line)
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if text[limit - 1].isascii() and text[limit - 1].isalnum() and ' ' in cut[limit // 2:]:
        cut = cut[:cut.rindex(' ')]          # a Latin word is not cut in two
    return cut.rstrip(' ,，;；\n') + '…'


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


def _palette(template_id, brand):
    tokens = brandkit_render.template_info(template_id)['tokens']
    palette = dict(tokens['palette'])
    if brand is not None:
        dark = palette['bg'].lower() in ('#24272b', '#26292d')
        if dark and brand.record.get('dark'):
            palette['bg'] = brand.dark
        palette['accent'] = brand.readable(brand.accent, palette['bg'])
    return palette


def _job(template_id, ctx, **fields):
    width, height = CANVAS[ctx['aspect']]
    tokens = brandkit_render.template_info(template_id)['tokens']
    return {'template_id': template_id, 'variant': 'studio', 'canvas': {'width': width, 'height': height}, 'lang': ctx['language'],
            'safe_margin': tokens.get('safe_margin', 84), 'min_margin': tokens.get('min_margin', 60),
            'palette': _palette(template_id, ctx.get('brand')), 'publishability': 'user_product', **fields}


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
    return [('width', across), ('height', height), ('depth', hidden)]


def _measure(mm, unit):
    if unit == 'in':
        return round(mm / 25.4, 1), 'in'
    return (round(mm / 10, 1), 'cm') if mm >= 100 else (round(mm, 1), 'mm')


def jobs_for(kind, ctx):
    """Versions of one page, best first: when one fails the layout checks the next, lighter one is tried. When the
    copywriter wrote this page's lines, the same versions with the template's own lines follow."""
    if not (ctx.get('copy') or {}).get(kind):
        return [{**job, '_lines': 'stock'} for job in _recropped(_jobs_for(kind, ctx))]
    return [{**job, '_lines': 'model'} for job in _recropped(_jobs_for(kind, ctx))] + \
           [{**job, '_lines': 'stock'} for job in _recropped(_jobs_for(kind, {**ctx, 'copy': {}}))]


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
            rows = [{**row, 'focus': [round(min(1, max(0, (row.get('focus') or [.5, .5])[0] + dx)), 3),
                                      round(min(1, max(0, (row.get('focus') or [.5, .5])[1] + dy)), 3)]} if row.get('detail') else row for row in job[name]]
            out.append({**job, name: rows})
    return out


def _jobs_for(kind, ctx):
    t = dict(TEXT[ctx['language']]); product = ctx['product']; assets = ctx['assets']
    written = (ctx.get('copy') or {}).get(kind) or {}
    for key in ('eyebrow', 'headline'):
        if written.get(key):
            t['%s_%s' % (kind, key)] = written[key]          # the copywriter's line for this product, when it passed its checks
    title = product['title'] or ''; headline = product['headline'] or title
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
            out.append(_job(kind, ctx, mode='band', scene=name, focus=scene['focus'], assets={name: assets[name]}, **base))
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
        points = [{'title': _clip(product['features'][index]['title'], 24), 'body': _clip(product['features'][index].get('detail'), 48), 'anchor': point}
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
        details = [{'title': _clip(product['features'][index]['title'], 22), 'body': _clip(product['features'][index].get('detail'), 40),
                    'detail': name, 'focus': (ctx.get('focus') or {}).get(name, [.5, .5])} for index, name in chosen]
        out = []
        for count in (3, 2):
            if len(details) >= count:
                use = details[:count]
                fields = dict(mode='split', hero='hero', eyebrow=t['T03_eyebrow'], headline=_headline(title or headline, ctx['language'], 30),
                              assets={'hero': assets['hero'], **{row['detail']: assets[row['detail']] for row in use}}, details=use)
                out.append(_job(kind, ctx, subhead=_clip(product['subline'], 60) or None, **fields))
                out.append(_job(kind, ctx, **fields))
        return out
    if kind == 'T02':
        rows = [{'title': _clip(product['features'][index]['title'], 22), 'body': _clip(product['features'][index].get('detail'), 48), 'detail': name,
                 'focus': (ctx.get('focus') or {}).get(name, [.5, .5])} for index, name in ctx['macros']]
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
        specs = []
        for row in product['figures']:
            number, _ = _number_unit(row['value'])
            if number is not None:
                specs.append({'label': _clip(row['label'], 18), 'value': row['value'], 'unit': row.get('unit', '')})
        for row in product['specs']:
            number, unit = _number_unit(row['value'])
            if number is not None and len(row['value']) <= 14:
                specs.append({'label': _clip(row['label'], 18), 'value': number, 'unit': unit})
            else:
                specs.append({'label': _clip(row['label'], 18), 'text': _clip(row['value'], 30)})
        if len(specs) < 3:
            for key, mm in dimensions_mm(ctx)[:3 - len(specs)]:
                value, unit = _measure(mm, ctx['unit'])
                specs.append({'label': t[key], 'value': value, 'unit': unit})
        counts = sorted({min(3, len(specs)), min(2, len(specs))} - {0}, reverse=True)
        return [_job(kind, ctx, scene=name, focus=scene['focus'], eyebrow=t['T04_eyebrow'], headline=t['T04_headline'],
                     specs=specs[:count], assets={name: assets[name]}) for count in counts]
    if kind == 'T08':
        good = [index for index in range(len(ctx['scenes'])) if ctx['scenes'][index].get('check') != 'failed']
        # A scene whose product check failed is left out while two others can carry the page.
        ranked = good[:3] if len(good) >= 2 else sorted(range(len(ctx['scenes'])), key=lambda index: (index not in good, index))[:3]
        items = [{'asset': 'scene%d' % (index + 1), 'title': _clip(ctx['scenes'][index]['title'], 20),
                  'body': _clause_lines(_clip(ctx['scenes'][index].get('body'), 44), ctx['language'], 8) or None,
                  'focus': ctx['scenes'][index]['focus']} for index in sorted(ranked)]
        if not all(row['body'] for row in items):
            # Captions under some tiles only would break the row's rhythm.
            items = [{**row, 'body': None} for row in items]
        out = []
        default = 'columns' if CANVAS[ctx['aspect']][0] / CANVAS[ctx['aspect']][1] >= .95 else 'stagger'
        for count in (3, 2):
            if len(items) >= count:
                use = items[:count]
                # Tall columns, then wide rows: a wide product that the columns would cut off fits a row.
                for mode in (default, 'stagger' if default == 'columns' else 'columns'):
                    out.append(_job(kind, ctx, mode=mode, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'], items=use,
                                    assets={row['asset']: assets[row['asset']] for row in use}))
                    out.append(_job(kind, ctx, mode=mode, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'],
                                    items=[{**row, 'body': None} for row in use], assets={row['asset']: assets[row['asset']] for row in use}))
        return out
    if kind == 'T06':
        side = assets['side']; box = side['content_bbox']
        rows = dimensions_mm(ctx); unit = ctx['unit']
        across = next(mm for key, mm in rows if key in ('width', 'length')); up = next(mm for key, mm in rows if key == 'height')
        depth = next(mm for key, mm in rows if key == 'depth')
        dims = []
        for key, letter, mm, start, end, side_dir, at in (('height', 'H', up, [box[0], box[1]], [box[0], box[3]], [-1, 0], box[0] - .07),
                                                          ('width', 'W', across, [box[0], box[3]], [box[2], box[3]], [0, 1], box[3] + .07)):
            value, shown = _measure(mm, unit)
            dims.append({'key': key, 'key_letter': letter, 'name': t[key], 'value': value, 'unit': shown, 'from': start, 'to': end,
                         'side': side_dir, 'at': at})
        depth_value, depth_unit = _measure(depth, unit)
        # Each sentence on its own line, so the depth figure never wraps away from its label.
        note = t['table_note'] + ('\n' if ctx['language'] == 'zh' else ' ') + t['depth_note'] % ('%g\u00a0%s' % (depth_value, depth_unit))
        # A long, low product leaves the table column too little room: its figures go on the lines instead.
        wide = side['width'] / side['height'] > 1.8
        modes = ('onproduct', 'table') if wide else ('table', 'onproduct')
        return [_job(kind, ctx, mode=mode, hero='side', eyebrow=_clip(title, 24) or t['T06_eyebrow'], headline=t['T06_headline'], dimensions=dims, dim_pad=pad,
                     table_note=note if mode == 'table' else None, assets={'side': side}) for mode in modes for pad in (110, 150)]
    if kind == 'T05':
        import components
        items = [{'asset': 'hero', 'name': _clip(title or t['main'], 20), 'role_label': t['main'], 'qty': 1}]
        for index, part in enumerate(ctx['parts']):
            role = components.ROLES.get(part.get('role'), ('配件', 'Accessory'))
            items.append({'asset': 'part%d' % index, 'name': _clip(part.get('name') or role[ctx['language'] == 'en'], 20),
                          'role_label': role[0 if ctx['language'] == 'zh' else 1], 'qty': part.get('quantity') or None})
        counts = sorted({min(len(items), count) for count in (8, 6, 4, 3)}, reverse=True)
        return [_job(kind, ctx, headline=t['T05_headline'], items=items[:count], note=t['T05_note'],
                     labels={'qty_pending': t['qty_pending'], 'qty_prefix': t['qty_prefix']},
                     assets={row['asset']: assets[row['asset']] for row in items[:count]}) for count in counts]
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
            'smallest_important_text_px_at_360': small, 'text': [{'id': row['id'], 'lines': row.get('lines'), 'text': row.get('text', '')[:40]}
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
    if any(row.get('check') == 'font_missing' for row in qa.get('issues', [])):
        notes.append('本机缺少 Noto Sans 字体，已用系统字体代替。')
    for line in qa.get('notes', [])[:2]:
        notes.append(str(line)[:160])
    return qa, notes, record

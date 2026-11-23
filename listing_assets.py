"""Pictures into template assets: a render flattened onto the page ground, the region of the parts a close-up is about
(kept clear of other parts), and where a scene's product sits. Split out of ``brandkit_set``, which still re-exports every name.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import brandkit_render


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


def scene_asset(source, target):
    """A scene picture as an opaque PNG for the templates."""
    with Image.open(source) as opened:
        opened.convert('RGB').save(target, 'PNG', optimize=True)
    return brandkit_render.asset(target)

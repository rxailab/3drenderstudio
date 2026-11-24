"""Keeping the real product in a generated scene.

An image model asked to put a product into a scene from reference renders draws the product
again, and what it draws drifts: a handle bends, a part goes missing, the proportions change,
most of all when hands hold it. So the product is not left to the model to draw.

* **Anchor.** The product's own render is placed on the canvas first, at the camera angle of
  the scene, on a plain ground. A mask marks it as the part of the picture to keep; the model
  paints everything else (the place, the light, the hands, the shadow) around it. The model
  edits a photograph that already contains the product instead of drawing one.
* **Check.** Whether the model kept the product is then measured, not asked. The render's
  edges are looked for in the picture at small shifts and scales: a product that was moved,
  resized or drawn somewhere else scores low. Inside the product, tile by tile, the picture's
  edges must still agree with the render's; a tile that no longer does, and is not covered by
  something in front (a hand, the workpiece), is a part the model changed.

Pasting the render back over the model's picture was tried and dropped: wherever the model
moved a part by a few pixels, a finger, a pipe or an outline ends up cut or doubled.

Only Pillow is used. Nothing here calls a model.
"""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

VERSION = 2                         # 2: a product drawn turned is not kept
GROUND = (198, 200, 203)          # the plain ground the anchor stands on; nothing of it may remain in the picture
# The product's longer side as a share of the canvas's shorter side, and how far down its centre sits.
# A product on a wall sits higher in the frame, with the wall around it and the room below it.
PLACING = {'hands': (.56, .53), 'person': (.36, .58), 'none': (.5, .55), 'wall': (.46, .43)}
LONG = 3.0                        # a cut-out this many times longer than it is wide is a long, thin tool
LONG_HANDS = .8                   # held in hands, its length fills this share of the frame (hands then come out life-size)
ROOM = .07                        # of the canvas: the product moves this far away from the side its work lies on
ROOM_SIDES = {'below': (0, -1), 'above': (0, 1), 'left': (1, 0), 'right': (-1, 0)}
WORK = 384                        # longer side, in pixels, at which the product is looked for
MIN_SCORE = .45                   # edge agreement below this: the product is not where the anchor put it (moved .38; kept .53 to .60)
MAX_HIDDEN = .55                  # more of the product covered than this: it is no longer shown
ABSENT = .85                      # and this much: what was found is not the product at all, which is somewhere else
FOREIGN = 44                      # colour distance (0-255) beyond which a pixel is not the product's own surface
TILES = 8                         # the product's box is checked in this many tiles each way
TILE_AGREEMENT = .1               # a tile whose edges agree less than this with the render's has changed (kept products: one stray tile; a redrawn half: five at 0)
DETAIL = 4.0                      # mean edge strength (0-255) a tile needs before it can be compared
MAX_CHANGED = .1                  # share of the product's tiles that may disagree before the product counts as redrawn
MIN_CHANGED = 2                   # and never fewer tiles than this: one tile is the model's ordinary wobble
# The image model sometimes draws the product turned a few degrees. The search above has no rotation and a plain surface
# (a red cabinet's doors) has no tiles to compare, so such a picture read as kept; the render pasted back over it then left
# the turned drawing showing round it: two cabinets, one tilted (users' pictures). Whether the render lies better on the
# picture turned than upright is measured here; calibrated on the listings' own pictures and turned copies of them.
TURN_ANGLES = (-8, -6, -4, -3, -2, 2, 3, 4, 6, 8)
TURN_MIN = 3                      # degrees: a best fit nearer upright is the ordinary wobble, not a turned drawing
TURN_RATIO = 1.12                 # the turned fit this much better than the upright one...
TURN_GAIN = .05                   # ...and by at least this much agreement
TURN_SIDE = 384                   # pixels: the product's own area is searched at this size
SEARCH_SCALES = (.94, .97, 1.0, 1.03, 1.06)
SEARCH_SHIFT = .06                # of the canvas, either way
# The image model often draws the product smaller or a little off its anchor (a wall cabinet came back 16% smaller):
# it is first looked for over this wide range at a small size, then the usual search runs around where it was found.
WIDE = 128                        # longer side, in pixels, of that first look
WIDE_SCALES = tuple(round(.7 + .025 * step, 3) for step in range(25))      # .7 to 1.3
WIDE_SHIFT = .15                  # of the canvas, either way
FIT = 1024                        # longer side, in pixels, at which the render is fitted before it is put back
FIT_MIN = .3                      # edge agreement the fitted render needs at that size; below it the picture is left as painted
STATUSES = ('kept', 'moved', 'changed', 'hidden')


def anchor(cutout, size, framing=None, room=None):
    """The product placed on the canvas. Returns (canvas RGB, layer RGBA the size of the canvas, box in pixels).

    ``room`` is the side of the product its work lies on ('below', 'above', 'left', 'right'): the product
    moves a little the other way, so the workpiece and the hands have space in the frame.
    """
    width, height = size
    cut = cutout.convert('RGBA'); bounds = cut.getchannel('A').point(lambda value: 255 if value > 8 else 0).getbbox()
    if bounds is None:
        raise ValueError('产品抠图是空白的，无法锚定产品。')
    cut = cut.crop(bounds)
    share, down = PLACING.get(framing if framing in PLACING else 'none')
    if framing == 'hands' and max(cut.size) > LONG * min(cut.size):
        # A long, thin tool (a caliper, a ruler, a tape blade) at the usual share leaves the hands holding it small in the
        # frame, and the image model zooms in to make a close-up of them, enlarging the product off its anchor.
        share = LONG_HANDS
    scale = share * min(width, height) / max(cut.size)
    w = max(1, round(cut.width * scale)); h = max(1, round(cut.height * scale))
    placed = cut.resize((w, h), Image.Resampling.LANCZOS)
    margin = round(min(width, height) * .04)
    shift = ROOM_SIDES.get(room, (0, 0))
    x = round((width - w) / 2 + shift[0] * ROOM * width); y = round(height * down - h / 2 + shift[1] * ROOM * height)
    x = max(margin, min(width - w - margin, x)); y = max(margin, min(height - h - margin, y))
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0)); layer.alpha_composite(placed, (x, y))
    canvas = Image.new('RGB', (width, height), GROUND); canvas.paste(layer, mask=layer.getchannel('A'))
    return canvas, layer, [x, y, x + w, y + h]


def mask(layer):
    """The edit mask: opaque over the product (kept), transparent everywhere else (painted). RGBA, ready to save as PNG."""
    keep = layer.getchannel('A').point(lambda value: 255 if value > 40 else 0)
    result = Image.new('RGBA', layer.size, (0, 0, 0, 0)); result.paste((0, 0, 0, 255), mask=keep)
    return result


def _small(image, longest):
    scale = min(1.0, longest / max(image.size))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)


def _edges(image):
    """Edge strength, a little blurred so that a line one pixel off still meets itself."""
    found = image.convert('L').filter(ImageFilter.FIND_EDGES)
    frame = Image.new('L', found.size, 0); frame.paste(found.crop((2, 2, found.width - 2, found.height - 2)), (2, 2))       # the filter rings the border
    return frame.filter(ImageFilter.GaussianBlur(1.3))


def _transformed(image, scale, dx, dy, centre, resample=Image.Resampling.BILINEAR):
    """The image scaled about ``centre`` and shifted by (dx, dy) pixels."""
    inverse = 1 / scale
    return image.transform(image.size, Image.Transform.AFFINE,
                           (inverse, 0, centre[0] - inverse * (centre[0] + dx), 0, inverse, centre[1] - inverse * (centre[1] + dy)), resample=resample)


def _total(image, region=None):
    return ImageStat.Stat(image, region).sum[0]


def _agreement(a, b, region=None):
    """Normalised correlation of two edge images (0 to 1) over ``region``."""
    own = _total(ImageChops.multiply(a, a), region); seen = _total(ImageChops.multiply(b, b), region)
    if own <= 0 or seen <= 0:
        return 0.0
    return _total(ImageChops.multiply(a, b), region) / (own * seen) ** .5


class _Views:
    """The render and the picture at the working size, with the render's edges ready to be moved about."""

    def __init__(self, layer, picture, longest=WORK):
        small = _small(layer, longest); self.size = small.size
        self.matte = small.getchannel('A')
        self.bounds = self.matte.getbbox()
        self.centre = ((self.bounds[0] + self.bounds[2]) / 2, (self.bounds[1] + self.bounds[3]) / 2) if self.bounds else (0, 0)
        self.render = Image.new('RGB', self.size, GROUND); self.render.paste(small, mask=self.matte)
        self.near = self.matte.filter(ImageFilter.MaxFilter(7))
        self.render_edges = ImageChops.multiply(_edges(self.render), self.near)
        self.picture = picture.convert('RGB').resize(self.size, Image.Resampling.LANCZOS)
        self.picture_edges = _edges(self.picture)

    def score(self, scale, dx, dy):
        moved = _transformed(self.render_edges, scale, dx, dy, self.centre)
        return _agreement(moved, self.picture_edges, _transformed(self.near, scale, dx, dy, self.centre))


def locate(layer, generated):
    """Where the product is in the generated picture, as a small change from the anchor.

    Returns (scale, dx, dy, score): the shift as shares of the canvas, the score the normalised
    agreement (0 to 1) between the render's edges and the picture's at that place.
    """
    views = _Views(layer, generated)
    if views.bounds is None:
        return 1.0, 0.0, 0.0, 0.0
    size = views.size; reach = SEARCH_SHIFT * max(size); step = reach / 4
    # where it is roughly, over a wide range of sizes and places, at a small size
    wide = _Views(layer, generated, WIDE); far = WIDE_SHIFT * max(wide.size); stride = 2.0
    rows = int(far // stride)
    _, s0, x0, y0 = max(((wide.score(scale, ix * stride, iy * stride), scale, ix * stride, iy * stride)
                         for scale in WIDE_SCALES for ix in range(-rows, rows + 1) for iy in range(-rows, rows + 1)), key=lambda row: row[0])
    x0 *= size[0] / wide.size[0]; y0 *= size[1] / wide.size[1]
    # then the usual search around both: where the anchor put it, and where the first look found it
    centres = [(1.0, 0.0, 0.0)] + ([(s0, x0, y0)] if (s0, x0, y0) != (1.0, 0.0, 0.0) else [])
    candidates = {(round(c_scale * scale, 4), c_x + ix * step, c_y + iy * step) for c_scale, c_x, c_y in centres
                  for scale in (SEARCH_SCALES if c_scale == 1.0 else (.97, 1.0, 1.03)) for ix in range(-4, 5) for iy in range(-4, 5)}
    best = max(((views.score(scale, dx, dy), scale, dx, dy) for scale, dx, dy in candidates), key=lambda row: row[0])
    _, scale, dx, dy = best
    fine = max(((views.score(scale + ds, dx + ix * step / 3, dy + iy * step / 3), scale + ds, dx + ix * step / 3, dy + iy * step / 3)
                for ds in (-.012, 0, .012) for ix in range(-2, 3) for iy in range(-2, 3)), key=lambda row: row[0])
    return round(fine[1], 4), round(fine[2] / size[0], 5), round(fine[3] / size[1], 5), round(fine[0], 4)


def _distance(a, b):
    """The largest difference of any channel, per pixel."""
    red, green, blue = ImageChops.difference(a, b).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def _foreign(render, picture, radius):
    """How far each pixel of the picture is from every colour the render has within ``radius`` of it."""
    nearest = _distance(render, picture)
    for reach in (radius * .5, radius):
        step = max(1, round(reach))
        for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step), (step, step), (-step, -step), (step, -step), (-step, step)):
            nearest = ImageChops.darker(nearest, _distance(ImageChops.offset(render, dx, dy), picture))
    return nearest


def _reaching(region, rim, least):
    """Of the white areas of ``region``, those of at least ``least`` pixels that touch ``rim``. Pure Python, on a small image."""
    width, height = region.size; cells = list(region.getdata()); edge = list(rim.getdata())
    seen = bytearray(width * height); kept = Image.new('L', region.size, 0); out = kept.load()
    for start, value in enumerate(cells):
        if not value or seen[start]:
            continue
        stack = [start]; seen[start] = 1; blob = []; touches = False
        while stack:
            index = stack.pop(); blob.append(index)
            touches = touches or bool(edge[index])
            x = index % width
            for neighbour in (index - 1 if x else -1, index + 1 if x < width - 1 else -1, index - width, index + width):
                if 0 <= neighbour < width * height and cells[neighbour] and not seen[neighbour]:
                    seen[neighbour] = 1; stack.append(neighbour)
        if touches and len(blob) >= least:
            for index in blob:
                out[index % width, index // width] = 255
    return kept


def _harmonised(render, picture, region):
    """The render given the picture's exposure and cast over ``region``: a per-channel gain and offset, within limits."""
    own = ImageStat.Stat(render, region); seen = ImageStat.Stat(picture, region)
    if not own.count[0]:
        return render
    bands = []
    for band, mean, spread, target, wanted in zip(render.split(), own.mean, own.stddev, seen.mean, seen.stddev):
        gain = max(.72, min(1.35, wanted / spread)) if spread > 4 else 1.0
        offset = max(-48.0, min(48.0, target - gain * mean))
        bands.append(band.point([max(0, min(255, round(value * gain + offset))) for value in range(256)]))
    return Image.merge('RGB', bands)


def inspect(layer, generated):
    """Whether the generated picture kept the anchored product. Returns a report.

    ``status`` is 'kept', 'moved' (not found near where the anchor put it), 'changed' (parts
    of it redrawn) or 'hidden' (mostly covered). ``covered`` is the share of the product hidden
    by something in front of it, ``changed`` the share of its tiles whose edges no longer agree
    with the render's, ``regions`` where those tiles are, as boxes in shares of the canvas.
    """
    width, height = layer.size
    picture = generated.convert('RGB')
    if picture.size != (width, height):
        picture = picture.resize((width, height), Image.Resampling.LANCZOS)
    scale, dx, dy, score = locate(layer, picture)
    report = {'version': VERSION, 'score': score, 'scale': scale, 'shift': [dx, dy]}
    if score < MIN_SCORE:
        return {**report, 'status': 'moved', 'covered': None, 'changed': None, 'regions': []}
    placed, views, solid, covered, area, box, extent = _in_front(layer, picture, scale, dx, dy)
    size = views.size
    hidden = _total(ImageChops.multiply(covered, solid)) / 255 / area
    # Tile by tile: does the picture still show the render's edges where the product is in view?
    visible = ImageChops.multiply(views.near, ImageChops.invert(covered))
    tile_w = (box[2] - box[0]) / TILES; tile_h = (box[3] - box[1]) / TILES
    counted = 0; changed = []; scores = []
    for row in range(TILES):
        for column in range(TILES):
            cell = (round(box[0] + column * tile_w), round(box[1] + row * tile_h), round(box[0] + (column + 1) * tile_w), round(box[1] + (row + 1) * tile_h))
            if cell[2] <= cell[0] or cell[3] <= cell[1]:
                continue
            region = Image.new('L', size, 0); ImageDraw.Draw(region).rectangle((cell[0], cell[1], cell[2] - 1, cell[3] - 1), fill=255)
            region = ImageChops.multiply(region, visible)
            cell_area = (cell[2] - cell[0]) * (cell[3] - cell[1])
            in_view = _total(region) / 255
            detail = _total(views.render_edges, region) / max(1.0, in_view)          # mean edge strength (0-255) the render has here
            if in_view < cell_area * .35 or detail < DETAIL:          # mostly hidden, off the product, or a plain surface with nothing to compare
                continue
            counted += 1
            agreement = _agreement(views.render_edges, views.picture_edges, region); scores.append(round(agreement, 3))
            if agreement < TILE_AGREEMENT:
                changed.append([round(cell[0] / size[0], 3), round(cell[1] / size[1], 3), round(cell[2] / size[0], 3), round(cell[3] / size[1], 3)])
    share = len(changed) / counted if counted else 0.0
    report.update(covered=round(hidden, 4), changed=round(share, 4), regions=changed[:12], tiles=counted, tile_scores=scores)
    status = ('moved' if hidden > ABSENT else 'hidden' if hidden > MAX_HIDDEN else
              'changed' if share > MAX_CHANGED and len(changed) >= MIN_CHANGED else 'kept')
    if status == 'kept':
        angle, upright, best = turned(layer, picture, scale, dx, dy)
        report['turn'] = {'angle': angle, 'upright': round(upright, 3), 'turned': round(best, 3)}
        if abs(angle) >= TURN_MIN and best >= upright * TURN_RATIO and best - upright >= TURN_GAIN:
            # drawn turned: pasting the upright render over it would show the product twice
            status = 'changed'; report['turned'] = True
            report['regions'] = report['regions'] or [[round(v, 3) for v in (box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1])]]
    return {**report, 'status': status}


def _rotated(image, angle, dx, dy, centre):
    """``image`` turned by ``angle`` degrees about ``centre`` and moved by (dx, dy) pixels."""
    a = math.radians(angle); c, s = math.cos(a), math.sin(a); tx, ty = centre[0] + dx, centre[1] + dy
    return image.transform(image.size, Image.Transform.AFFINE, (c, s, centre[0] - c * tx - s * ty, -s, c, centre[1] + s * tx - c * ty),
                           resample=Image.Resampling.BILINEAR)


def turned(layer, picture, scale, dx, dy):
    """How the render lies on the picture upright and turned, where the check found it: (best angle, upright agreement,
    best turned agreement). Searched on the product's own area at TURN_SIDE pixels, each angle with a small shift."""
    width, height = layer.size
    bounds = layer.getchannel('A').getbbox(); centre = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    placed = layer if (scale, dx, dy) == (1.0, 0.0, 0.0) else _transformed(layer, scale, dx * width, dy * height, centre, Image.Resampling.BICUBIC)
    box = placed.getchannel('A').getbbox()
    if box is None:
        return 0, 0.0, 0.0
    pad = .3 * max(box[2] - box[0], box[3] - box[1])
    crop = (max(0, int(box[0] - pad)), max(0, int(box[1] - pad)), min(width, int(box[2] + pad)), min(height, int(box[3] + pad)))
    views = _Views(placed.crop(crop), picture.convert('RGB').resize((width, height)).crop(crop) if picture.size != (width, height)
                   else picture.convert('RGB').crop(crop), TURN_SIDE)

    def best_at(angle):
        return max(_agreement(_rotated(views.render_edges, angle, ix * 2.0, iy * 2.0, views.centre), views.picture_edges,
                              _rotated(views.near, angle, ix * 2.0, iy * 2.0, views.centre))
                   for ix in range(-2, 3) for iy in range(-2, 3))
    upright = best_at(0)
    angle, best = max(((angle, best_at(angle)) for angle in TURN_ANGLES), key=lambda row: row[1])
    return angle, upright, best


def _in_front(layer, picture, scale, dx, dy):
    """The render moved to where the product was found, and what in the picture is in front of it (a hand, the workpiece),
    at the working size: (placed layer at full size, views, solid matte, covered mask, matte area, matte box, extent)."""
    width, height = layer.size
    bounds = layer.getchannel('A').getbbox(); centre = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    placed = layer if (scale, dx, dy) == (1.0, 0.0, 0.0) else _transformed(layer, scale, dx * width, dy * height, centre, Image.Resampling.BICUBIC)
    views = _Views(placed, picture)
    solid = views.matte.point(lambda value: 255 if value > 200 else 0)
    core = solid.filter(ImageFilter.MinFilter(5))
    box = solid.getbbox() or (0, 0, 1, 1); extent = max(box[2] - box[0], box[3] - box[1]); radius = max(2.0, extent * .035)
    area = _total(solid) / 255 or 1
    # What is in front of the product: colours the render has nowhere near, in patches that reach its outline (a hand comes from outside).
    # The light is fitted twice: the second time without what turned out to be in front of the product, which would skew it.
    lit = _harmonised(views.render, views.picture, core)
    apart = ImageChops.multiply(_foreign(lit, views.picture, radius).point(lambda value: 255 if value > FOREIGN else 0), solid)
    own = ImageChops.multiply(core, ImageChops.invert(apart))
    if _total(own) > _total(core) * .15:
        lit = _harmonised(views.render, views.picture, own)
        apart = ImageChops.multiply(_foreign(lit, views.picture, radius).point(lambda value: 255 if value > FOREIGN else 0), solid)
    cleaned = apart.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    rim = ImageChops.subtract(solid, solid.filter(ImageFilter.MinFilter(max(3, 2 * round(extent * .012) + 1))))
    covered = _reaching(cleaned, rim, max(12, round(area * .004))).filter(ImageFilter.MaxFilter(5))
    return placed, views, solid, covered, area, box, extent


def fit(layer, generated, scale, dx, dy):
    """The scale and shift (shares of the canvas) at which the render lies exactly on the product in the picture, refined
    from the check's coarse ones (found at WORK pixels, a few canvas pixels and about a percent out), and the edge agreement
    there: (scale, dx, dy, score). Searched on the product's own area at FIT pixels: shift, then size, then shift again."""
    width, height = layer.size; factor = min(1.0, FIT / max(width, height))
    small = _small(layer, FIT); picture = generated.convert('RGB').resize(small.size, Image.Resampling.LANCZOS)
    bounds = small.getchannel('A').getbbox()
    if bounds is None:
        return scale, dx, dy, 0.0
    pad = round(max(small.size) * (SEARCH_SHIFT + .04))
    crop = (max(0, bounds[0] - pad), max(0, bounds[1] - pad), min(small.width, bounds[2] + pad), min(small.height, bounds[3] + pad))
    piece = small.crop(crop); centre = ((bounds[0] + bounds[2]) / 2 - crop[0], (bounds[1] + bounds[3]) / 2 - crop[1])
    render = Image.new('RGB', piece.size, GROUND); render.paste(piece, mask=piece.getchannel('A'))
    near = piece.getchannel('A').filter(ImageFilter.MaxFilter(5))
    render_edges = ImageChops.multiply(_edges(render), near); seen = _edges(picture.crop(crop))

    def score(s, x, y):
        return _agreement(_transformed(render_edges, s, x, y, centre), seen, _transformed(near, s, x, y, centre))
    s, x, y = scale, dx * small.width, dy * small.height
    for step, reach, scales in ((1.0, 4, (0,)), (None, 0, (-.015, -.012, -.009, -.006, -.003, 0, .003, .006, .009, .012, .015)), (.5, 3, (0,))):
        if step is None:
            s = max(((score(s + ds, x, y), s + ds) for ds in scales), key=lambda row: row[0])[1]
            continue
        _, x, y = max(((score(s, x + ix * step, y + iy * step), x + ix * step, y + iy * step)
                       for ix in range(-reach, reach + 1) for iy in range(-reach, reach + 1)), key=lambda row: row[0])
    return round(s, 4), round(x / small.width, 5), round(y / small.height, 5), round(score(s, x, y), 4)


def restore(layer, generated, report):
    """The picture with the product put back as the render, where the check found it: the image model paints the place, the
    light and what is in front of the product, but the product itself is the model's own render, at its real proportions
    and with every one of its parts. Only for a picture whose ``report`` says 'kept'. Returns an RGB image of the picture's size,
    or None when the render cannot be fitted exactly onto the product in the picture."""
    width, height = layer.size
    picture = generated.convert('RGB')
    own_size = picture.size
    if own_size != (width, height):
        picture = picture.resize((width, height), Image.Resampling.LANCZOS)
    scale, dx, dy, fitted = fit(layer, picture, float(report.get('scale', 1.0)), *(float(value) for value in report.get('shift', [0, 0])))
    if fitted < FIT_MIN:
        return None                     # the render does not lie cleanly on the painted product: pasting it would double its outline
    placed, views, solid, covered, _, _, _ = _in_front(layer, picture, scale, dx, dy)
    # what is in front of the product, at full size; the product's own matte at full size, a pixel inside its outline
    front = covered.resize((width, height), Image.Resampling.BILINEAR).filter(ImageFilter.MaxFilter(3))
    matte = placed.getchannel('A').filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(.8))
    alpha = ImageChops.multiply(matte, ImageChops.invert(front))
    render = Image.new('RGB', (width, height), GROUND); render.paste(placed.convert('RGB'), mask=placed.getchannel('A'))
    # the model's light on it: the render given the exposure and cast the picture has where the product is in view
    seen = ImageChops.multiply(placed.getchannel('A').point(lambda value: 255 if value > 200 else 0).filter(ImageFilter.MinFilter(5)),
                               ImageChops.invert(front))
    lit = _harmonised(render, picture, seen)
    restored = Image.composite(lit, picture, alpha)
    return restored if own_size == (width, height) else restored.resize(own_size, Image.Resampling.LANCZOS)


def describe(report, box):
    """Where on the product the changed tiles are, in words for a correction: 'upper left', 'middle'… ``box`` is the product's box in shares."""
    places = []
    x0, y0, x1, y1 = box
    for region in report.get('regions') or []:
        cx = ((region[0] + region[2]) / 2 - x0) / max(1e-6, x1 - x0); cy = ((region[1] + region[3]) / 2 - y0) / max(1e-6, y1 - y0)
        vertical = 'upper' if cy < .34 else 'lower' if cy > .66 else ''
        horizontal = 'left' if cx < .34 else 'right' if cx > .66 else ''
        places.append((vertical + ' ' + horizontal).strip() or 'middle')
    return list(dict.fromkeys(places))


STACK_RATIO = 1.6          # a product box wider than this (a caliper, a long tool) is stacked: render on top, picture below
# Each stacked panel's width: about 1.7 times what each half of a side-by-side strip arrives at, for about 1k more image
# tokens per check; the full 1536 the check could send costs twice that again.
STACK_WIDTH = 1280


def side_by_side(layer, generated, report, pad=.06, height=640):
    """The product's box in the render (on white) beside the same box in the picture, for a visual check.

    Side by side a long product makes a strip three or four times wider than tall, and the check, which sends images at most
    1536 px wide, sees each half at about 40 % scale: a bent jaw or a missing button is then a few pixels. A box wider than
    ``STACK_RATIO`` is therefore stacked instead, render on top and the picture below, each ``STACK_WIDTH`` wide, so the
    two line up part by part and arrive at about 70 % of the render's scale instead of 40 %."""
    width, full_height = layer.size
    bounds = layer.getchannel('A').getbbox()
    grow = round(max(bounds[2] - bounds[0], bounds[3] - bounds[1]) * pad)
    box = (max(0, bounds[0] - grow), max(0, bounds[1] - grow), min(width, bounds[2] + grow), min(full_height, bounds[3] + grow))
    render = Image.new('RGB', (width, full_height), (255, 255, 255)); render.paste(layer, mask=layer.getchannel('A'))
    picture = generated.convert('RGB')
    if picture.size != (width, full_height):
        picture = picture.resize((width, full_height), Image.Resampling.LANCZOS)
    dx, dy = report.get('shift') or [0, 0]
    moved = (round(box[0] + dx * width), round(box[1] + dy * full_height), round(box[2] + dx * width), round(box[3] + dy * full_height))
    left = render.crop(box); right = picture.crop(moved)
    if left.width / left.height > STACK_RATIO:
        size = (STACK_WIDTH, max(1, round(left.height * STACK_WIDTH / left.width)))
        left = left.resize(size, Image.Resampling.LANCZOS); right = right.resize(size, Image.Resampling.LANCZOS)
        sheet = Image.new('RGB', (size[0], size[1] * 2 + 24), (255, 255, 255))
        sheet.paste(left, (0, 0)); sheet.paste(right, (0, size[1] + 24))
        return sheet
    scale = height / left.height
    left = left.resize((max(1, round(left.width * scale)), height), Image.Resampling.LANCZOS)
    right = right.resize(left.size, Image.Resampling.LANCZOS)
    sheet = Image.new('RGB', (left.width * 2 + 24, height), (255, 255, 255))
    sheet.paste(left, (0, 0)); sheet.paste(right, (left.width + 24, 0))
    return sheet

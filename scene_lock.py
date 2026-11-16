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
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

VERSION = 1
GROUND = (198, 200, 203)          # the plain ground the anchor stands on; nothing of it may remain in the picture
# The product's longer side as a share of the canvas's shorter side, and how far down its centre sits.
PLACING = {'hands': (.56, .53), 'person': (.36, .58), 'none': (.5, .55)}
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
SEARCH_SCALES = (.94, .97, 1.0, 1.03, 1.06)
SEARCH_SHIFT = .06                # of the canvas, either way
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

    def __init__(self, layer, picture):
        small = _small(layer, WORK); self.size = small.size
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
    best = max(((views.score(scale, ix * step, iy * step), scale, ix * step, iy * step)
                for scale in SEARCH_SCALES for ix in range(-4, 5) for iy in range(-4, 5)), key=lambda row: row[0])
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
    # Compare in the place the product was found: the render moved there, the picture as it is.
    bounds = layer.getchannel('A').getbbox(); centre = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    placed = layer if (scale, dx, dy) == (1.0, 0.0, 0.0) else _transformed(layer, scale, dx * width, dy * height, centre, Image.Resampling.BICUBIC)
    views = _Views(placed, picture); size = views.size
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
    return {**report, 'status': status}


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


def side_by_side(layer, generated, report, pad=.06, height=640):
    """The product's box in the render (on white) beside the same box in the picture, for a visual check."""
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
    scale = height / left.height
    left = left.resize((max(1, round(left.width * scale)), height), Image.Resampling.LANCZOS)
    right = right.resize(left.size, Image.Resampling.LANCZOS)
    sheet = Image.new('RGB', (left.width * 2 + 24, height), (255, 255, 255))
    sheet.paste(left, (0, 0)); sheet.paste(right, (left.width + 24, 0))
    return sheet

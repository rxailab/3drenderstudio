"""Compositions for the text layer: the render placed on a designed canvas, with few words set well.

The overlay layer of v23/v24 laid cards over the full-frame render wherever
there was room. These compositions work the other way round, the way the
twelve brand references in references/tool-layout-12 do: the layout first
reserves the space for words, then places the product in the rest, so text
never has to squeeze around it. Each composition is one of those references
adapted to a square, 4:3 or 3:4 product image:

    split      左右分区   words in a column beside the product (refs 01, 05)
    callouts   结构标注   the product on one side, aligned notes with hairlines to its parts (02, 07, 11)
    scene      场景叙事   a headline in the calm part of a scene photo (03, 09)
    cards      卡片网格   the product in a large card, key points in small ones (04, 06)
    spotlight  单点卖点   one buying question: a headline, the product, a band of key figures (08)
    specs      规格清单   the product above a row of specifications (10)

Reference 12 (系列对照, two product groups side by side) compares several
products and has no layout here: one composition shows one render.

Everything is plain layer data (see info_layer.validate): a canvas colour, the
photo's frame and crop, and items. The browser draws it (static/layer.js); the
measures here mirror the ones there.
"""
import copy
import re

LAYOUTS = ('split', 'callouts', 'scene', 'cards', 'spotlight', 'specs')
LABELS = {'split': '左右分区', 'callouts': '结构标注', 'scene': '场景叙事', 'cards': '卡片网格', 'spotlight': '单点卖点', 'specs': '规格清单'}
REFERENCES = {'split': ('01', '05'), 'callouts': ('02', '07', '11'), 'scene': ('03', '09'), 'cards': ('04', '06'),
              'spotlight': ('08',), 'specs': ('10',)}
# The type and colour system. Few sizes, one weight for emphasis, colour only for figures.
INK = {'light': '#1d1d1f', 'dark': '#f5f5f7'}
SOFT = {'light': '#6e6e73', 'dark': '#a1a1a6'}
CANVAS = {'light': '#f2f2f0', 'dark': '#111113'}
CARD = {'light': '#ffffff', 'dark': '#1c1c1e'}
LEADING = {'headline': 1.16, 'subline': 1.45, 'body': 1.45, 'kicker': 1.2, 'caption': 1.4}
FIGURE_UNIT = .4            # a figure's unit and label, as shares of its value's size
FIGURE_LABEL = .3
FIGURE_GAP = .22
ROW_DETAIL = .78            # a point's detail, as a share of its title's size
ROW_ICON = 1.5              # a point's icon, as a multiple of its title's size


# ------------------------------------------------------------------ measuring

class Frame:
    """The canvas: lengths in units of its shorter side, positions as shares of each side."""

    def __init__(self, aspect):
        width, height = [float(part) for part in str(aspect).split(':')]
        self.W, self.H = width, height
        unit = min(width, height)
        self.sx = unit / width; self.sy = unit / height
        self.portrait = height > width * 1.05
        self.wide = width > height * 1.05

    def x(self, length):
        return length * self.sx

    def y(self, length):
        return length * self.sy


def _width(text, size):
    import info_layer
    return info_layer.text_width(text, size)


def _wrap(text, size, limit):
    import info_layer
    # A little slack: the browser's glyphs run a few percent wider than the estimate for bold and Latin text.
    return [line for line in info_layer.wrap(text, size, limit * .93) if line.strip()] or ['']


NOUNS = ('工具柜', '工具箱', '工具车', '工具包', '工具墙', '工作台', '收纳柜', '抽屉柜', '扳手', '套筒', '钳子', '螺丝刀', '电钻', '套装', '手提袋',
         '按压瓶', '托盘', '玻璃杯', '割草机', '绿篱机', '万用表', '收纳', '台面', '抽屉', '脚轮')


def _nouns():
    words = set(NOUNS)
    try:
        import scene_stylist
        for recipe in scene_stylist.RECIPES:
            words.update(word for word in recipe.get('words', ()) if len(word) >= 2 and not word.isascii())
    except Exception:
        pass
    return sorted(words, key=len, reverse=True)


def _word_break(text, size, limit):
    """Two lines broken before a product noun ("红色 / 工具柜"), when that fits; None otherwise."""
    if ' ' in text.strip():
        return None
    best = None
    for word in _nouns():
        at = text.find(word)
        while at > 0:
            first, second = text[:at], text[at:]
            if _width(first, size) <= limit * .93 and _width(second, size) <= limit * .93:
                balance = abs(_width(first, size) - _width(second, size))
                if best is None or balance < best[0]:
                    best = (balance, [first, second])
            at = text.find(word, at + 1)
    return best[1] if best else None


def _balanced(text, size, limit, lines):
    """The text in as few lines as fit, broken so that the lines are about equally long (no lone last word)."""
    wrapped = _wrap(text, size, limit)
    if len(wrapped) < 2 or len(wrapped) > lines:
        return wrapped
    if len(wrapped) == 2:
        nouns = _word_break(text, size, limit)
        if nouns:
            return nouns
    total = _width(text, size)
    for share in (.5, .56, .62, .44, .38):
        # Shorten the line width step by step until the break falls near the middle.
        trial = _wrap(text, size, max(total * share / .93, size * 2))
        if len(trial) == len(wrapped) and all(_width(line, size) <= limit * .93 for line in trial):
            return trial
    return wrapped


def _fit(text, sizes, limit, lines):
    """The largest size at which the text takes no more than ``lines`` balanced lines, none of them a lone character.

    A short headline that fits on one line at nearly full size stays on one line.
    """
    for size in sizes:
        if size >= sizes[0] * .8 and len(_wrap(text, size, limit)) == 1:
            return size, _wrap(text, size, limit)
    for size in sizes:
        wrapped = _balanced(text, size, limit, lines)
        widths = [_width(line, size) for line in wrapped]
        if len(wrapped) <= lines and (len(wrapped) == 1 or min(widths) >= max(widths) * .34):
            return size, wrapped
    size = sizes[-1]
    return size, _balanced(text, size, limit, lines)[:lines]


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(value))) for value in rgb)


def _luminance(color):
    value = int(color[1:], 16)
    return (.2126 * (value >> 16 & 255) + .7152 * (value >> 8 & 255) + .0722 * (value & 255)) / 255


def _channel(value):
    value /= 255
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def contrast(a, b):
    """The WCAG contrast ratio of two '#rrggbb' colours."""
    def light(color):
        value = int(color[1:], 16)
        return .2126 * _channel(value >> 16 & 255) + .7152 * _channel(value >> 8 & 255) + .0722 * _channel(value & 255)
    high, low = sorted((light(a), light(b)), reverse=True)
    return (high + .05) / (low + .05)


def readable(color, background, toward, minimum):
    """``color``, moved towards ``toward`` just as far as it takes to be read on ``background``."""
    if contrast(color, background) >= minimum:
        return color
    a = [int(color[index:index + 2], 16) for index in (1, 3, 5)]; b = [int(toward[index:index + 2], 16) for index in (1, 3, 5)]
    for step in range(1, 11):
        mixed = _hex([x + (y - x) * step / 10 for x, y in zip(a, b)])
        if contrast(mixed, background) >= minimum:
            return mixed
    return toward


def palette(picture, generated=False):
    """How this picture can be composed.

    studio: the backdrop is calm and its edge is one even colour (a sweep), so the render can be
    moved on a canvas of that colour without a visible edge. A gradient or a scene that reaches the
    picture's edge would show where the render ends: such a picture is a photo and keeps hard edges.
    scene: the picture shows the product in a setting (a generated photo, props around it, a
    busy backdrop), where a headline set in the picture itself reads best.
    """
    mean, uneven = picture.backdrop()
    border = picture.border_color() if hasattr(picture, 'border_color') else None
    spread = picture.edge_spread() if hasattr(picture, 'edge_spread') else 0.0
    studio = not generated and uneven < .2 and spread < .045 and (mean >= .62 or mean <= .3)
    scenery = picture.cover((0, 0, 1, 1))[1] if hasattr(picture, 'cover') else 0.0
    scene = generated or scenery > .06 or uneven > .24
    tone = 'dark' if (border and _luminance(border) < .45) or (not border and mean < .45) else 'light'
    if not studio:
        tone = 'dark' if mean < .5 else 'light'
    canvas = border if studio and border else CANVAS[tone]
    accent = picture.accent() if hasattr(picture, 'accent') else None
    ink = readable(INK[tone], canvas, '#000000' if tone == 'light' else '#ffffff', 7)
    # Small grey words and the coloured figures must still be read on the canvas: 4.5:1 for the one, 3:1 (large type) for the other.
    soft = readable(SOFT[tone], canvas, ink, 4.5)
    accent = accent if accent and contrast(accent, canvas) >= 3 else None
    return {'studio': studio, 'scene': scene, 'tone': tone, 'canvas': canvas, 'ink': ink, 'soft': soft, 'card': CARD[tone],
            'accent': accent or ink, 'line': ink}


# ------------------------------------------------------------------ the photo

def place(frame, picture, region, centre, height, width, studio):
    """The photo's frame and crop that put the product at ``centre`` (canvas shares), ``height`` of the canvas
    tall and at most ``width`` of it wide.

    ``region`` (x, y, w, h) is the part of the canvas the photo may show in. On a studio backdrop the crop may
    run past the render's own edges (the canvas has the backdrop's colour). A photo is only ever cropped, never
    extended, and the crop never cuts into the product: it fills the region when it can do so and still show
    the whole product, and otherwise keeps a shape of its own, as large as fits in the region.
    Returns (photo, to_canvas) where to_canvas maps a point of the render to the canvas.
    """
    rx0, ry0, fw, fh = region
    product = picture.rect or (.3, .2, .7, .8)
    pw = max(.02, product[2] - product[0]); ph = max(.02, product[3] - product[1])
    pcx, pcy = (product[0] + product[2]) / 2, (product[1] + product[3]) / 2
    inset = False
    # The render and the canvas share one aspect: k is how much the render is scaled, and a window of the
    # render (in its shares) becomes a frame of the same shape on the canvas (in the canvas's shares).
    if studio:
        k = min(height / ph, width / pw, 1.08)
        cw, ch = fw / k, fh / k
        cx0 = pcx - (centre[0] - rx0) / k; cy0 = pcy - (centre[1] - ry0) / k
        box = (rx0, ry0, fw, fh)
    else:
        edge = .05
        need = (max(0.0, product[0] - pw * edge), max(0.0, product[1] - ph * edge), min(1.0, product[2] + pw * edge), min(1.0, product[3] + ph * edge))
        nw, nh = need[2] - need[0], need[3] - need[1]
        cover = max(fw, fh)                               # the least scale at which the render fills the region
        keep = min(fw / nw, fh / nh)                      # the most at which the region still shows the whole product
        if cover <= keep + 1e-9:
            k = min(max(cover, min(height / ph, width / pw)), keep)
            cw, ch = fw / k, fh / k
            box = (rx0, ry0, fw, fh)
        else:
            # The photo becomes a print of its own: inset from the region on every side, with softened corners.
            mx, my = frame.x(.035), frame.y(.035); fw, fh = fw - 2 * mx, fh - 2 * my; rx0, ry0 = rx0 + mx, ry0 + my
            shape = fw / fh; cw, ch = nw, nh
            if cw / ch < shape:
                cw = min(1.0, ch * shape)
            else:
                ch = min(1.0, cw / shape)
            k = min(fw / cw, fh / ch)
            w, h = cw * k, ch * k
            box = (min(max(centre[0] - w / 2, rx0), rx0 + fw - w), min(max(centre[1] - h / 2, ry0), ry0 + fh - h), w, h)
            inset = True
        cx0 = pcx - (centre[0] - box[0]) / k; cy0 = pcy - (centre[1] - box[1]) / k
        cx0 = min(max(cx0, need[2] - cw, 0.0), need[0], 1.0 - cw)
        cy0 = min(max(cy0, need[3] - ch, 0.0), need[1], 1.0 - ch)
    crop = [cx0, cy0, cx0 + cw, cy0 + ch]
    photo = {'x': box[0], 'y': box[1], 'w': box[2], 'h': box[3], 'crop': [round(value, 5) for value in crop], 'feather': bool(studio)}
    if inset:
        photo['radius'] = .016

    def to_canvas(point):
        return [box[0] + (point[0] - crop[0]) * k, box[1] + (point[1] - crop[1]) * k]
    return photo, to_canvas


def product_box(picture, to_canvas):
    product = picture.rect or (.3, .2, .7, .8)
    a = to_canvas((product[0], product[1])); b = to_canvas((product[2], product[3]))
    return (a[0], a[1], b[0], b[1])


# ------------------------------------------------------------------ items

def _text(ident, role, lines, x, y, w, size, color, weight=400, align='left'):
    return {'id': ident, 'type': 'text', 'role': role, 'x': x, 'y': y, 'w': w, 'text': '\n'.join(lines), 'size': size, 'weight': weight,
            'align': align, 'color': color, 'plate': None, 'leading': LEADING[role]}


def _text_height(frame, item):
    return len(item['text'].split('\n')) * item['size'] * item['leading'] * frame.sy


def title_block(frame, content, x, y, w, pal, sizes=(.066, .06, .054, .048, .044), lines=2, sub_lines=3, align='left', subline=True):
    """Tag, headline and supporting line from the top of a column. Returns (items, bottom)."""
    items = []; limit = w / frame.sx
    kicker = (content.get('kicker') or '').strip()
    if kicker:
        size = .021
        item = _text('kicker', 'kicker', [kicker.upper()], x, y, w, size, pal['soft'], 600, align)
        items.append(item); y += _text_height(frame, item) + frame.y(.016)
    headline = (content.get('headline') or '').strip()
    if headline:
        size, wrapped = _fit(headline, sizes, limit, lines)
        item = _text('headline', 'headline', wrapped, x, y, w, size, pal['ink'], 700, align)
        items.append(item); y += _text_height(frame, item)
    sub = (content.get('subline') or '').strip()
    if sub and subline:
        size = .024; wrapped = _wrap(sub, size, limit)[:sub_lines]
        y += frame.y(.018)
        item = _text('subline', 'subline', wrapped, x, y, w, size, pal['soft'], 400, align)
        items.append(item); y += _text_height(frame, item)
    return items, y


def figure_height(frame, size, labelled=True):
    return (size * 1.02 + (size * FIGURE_GAP + size * FIGURE_LABEL * 1.4 if labelled else 0)) * frame.sy


def _figure(ident, figure, x, y, w, size, color, label_color, align='left', glyph=None):
    item = {'id': ident, 'type': 'figure', 'x': x, 'y': y, 'w': w, 'value': str(figure.get('value', ''))[:24], 'unit': str(figure.get('unit') or '')[:12],
            'label': str(figure.get('label') or '')[:40], 'size': size, 'color': color, 'soft': label_color, 'align': align}
    if glyph:
        item['glyph'] = glyph
    return item


def _row(ident, point, x, y, w, size, pal, glyph=None):
    item = {'id': ident, 'type': 'callout', 'style': 'row', 'x': x, 'y': y, 'w': w, 'title': point['title'], 'detail': point.get('detail') or '',
            'icon': None, 'anchor': None, 'size': size, 'color': pal['ink'], 'soft': pal['soft'], 'plate': None}
    if glyph:
        item['glyph'] = glyph
    return item


def row_height(frame, point, size, w, icon=True):
    limit = (w / frame.sx) - (size * (ROW_ICON + .8) if icon else 0)
    title = _wrap(point['title'], size, limit); detail = _wrap(point['detail'], size * ROW_DETAIL, limit) if point.get('detail') else []
    text = len(title) * size * 1.3 + len(detail) * size * ROW_DETAIL * 1.45
    return max(size * ROW_ICON if icon else 0, text) * frame.sy


def _panel(ident, x, y, w, h, color, opacity=1.0, radius=0.0, fade=None):
    item = {'id': ident, 'type': 'panel', 'x': x, 'y': y, 'w': w, 'h': h, 'color': color, 'opacity': opacity, 'radius': radius, 'size': .02}
    if fade:
        item['fade'] = fade
    return item


def _rule(ident, x, y, x2, y2, color, width=.0016, opacity=.22):
    return {'id': ident, 'type': 'rule', 'x': x, 'y': y, 'x2': x2, 'y2': y2, 'color': color, 'width': width, 'opacity': opacity, 'size': .02}


def _icon(ident, glyph, x, y, size, color):
    return {'id': ident, 'type': 'icon', 'icon': 'dot', 'glyph': glyph, 'x': x, 'y': y, 'size': size, 'color': color}


# ------------------------------------------------------------------ the compositions

def compose_split(frame, picture, content, pal):
    M = .075; items = []; dropped = []
    if frame.portrait:
        column = (frame.x(M), frame.y(M), 1 - 2 * frame.x(M))
        photo, to_canvas = place(frame, picture, (0, 0, 1, 1) if pal['studio'] else (0, .34, 1, .66), (.5, .66), .52, .84, pal['studio'])
    else:
        share = .41 if not frame.wide else .37
        column = (frame.x(M), frame.y(M) + frame.y(.02), share - frame.x(M) - .02)
        region = (0, 0, 1, 1) if pal['studio'] else (share, 0, 1 - share, 1)
        photo, to_canvas = place(frame, picture, region, (share + (1 - share) * .5, .53), .7, (1 - share) * .84, pal['studio'])
    x, y, w = column
    head, bottom = title_block(frame, content, x, y, w, pal, sizes=(.064, .058, .052, .046, .042) if not frame.portrait else (.06, .054, .048),
                               lines=2 if not frame.portrait else 2, sub_lines=3 if not frame.portrait else 2)
    items += head
    floor = 1 - frame.y(M)
    figures = content.get('figures', [])[:2]; points = content.get('points', [])[:3]
    if frame.portrait:
        # Words on top: the key figures or points follow the headline instead of standing at the foot.
        top = bottom + frame.y(.04); room = (photo['y'] + photo['h'] * .2) if not pal['studio'] else product_box(picture, to_canvas)[1] - frame.y(.03)
        if figures:
            size = .056; each = (w - frame.x(.04) * (len(figures) - 1)) / len(figures)
            for index, figure in enumerate(figures):
                items.append(_figure('fig%d' % (index + 1), figure, x + index * (each + frame.x(.04)), top, each, size, pal['accent'], pal['soft'],
                                     glyph=figure.get('glyph')))
        elif points:
            size = .022; each = (w - frame.x(.03) * (len(points) - 1)) / len(points)
            for index, point in enumerate(points):
                items.append(_row('point%d' % (index + 1), point, x + index * (each + frame.x(.03)), top, each, size, pal, point.get('glyph')))
        if top > room:
            dropped += [item['id'] for item in items if item['id'].startswith(('fig', 'point'))]
            items = [item for item in items if not item['id'].startswith(('fig', 'point'))]
    elif figures:
        size = .074 if len(figures) == 1 else .066
        heights = [figure_height(frame, size) for _ in figures]; gap = frame.y(.045)
        y0 = floor - sum(heights) - gap * (len(figures) - 1)
        if figures and len(figures) > 1:
            items.append(_rule('figrule', x, y0 - frame.y(.035), x + w * .3, y0 - frame.y(.035), pal['line'], .0016, .3))
        for index, figure in enumerate(figures):
            items.append(_figure('fig%d' % (index + 1), figure, x, y0, w, size, pal['accent'], pal['soft'], glyph=figure.get('glyph')))
            y0 += heights[index] + gap
    elif points:
        size = .023; gap = frame.y(.026)
        heights = [row_height(frame, point, size, w, True) for point in points]
        y0 = floor - sum(heights) - gap * 2 * (len(points) - 1)
        while points and y0 < bottom + frame.y(.06):
            dropped.append(points[-1]['title']); points = points[:-1]; heights = heights[:-1]
            y0 = floor - sum(heights) - gap * 2 * (len(points) - 1)
        for index, point in enumerate(points):
            if index:
                items.append(_rule('rule%d' % index, x, y0 - gap, x + w, y0 - gap, pal['line'], .0014, .18))
            items.append(_row('point%d' % (index + 1), point, x, y0, w, size, pal, point.get('glyph')))
            y0 += heights[index] + gap * 2
    return {'color': pal['canvas'], 'photo': photo}, items, dropped


def compose_callouts(frame, picture, content, pal):
    M = .07; items = []; dropped = []
    studio = pal['studio']
    column_x = .6 if frame.portrait else .67
    column_w = 1 - frame.x(M) - column_x
    # The words take their space first and the product is fitted into the rest, so no note or headline lies on it:
    # a portrait canvas has the headline across the top, a square or wide one at the head of the notes.
    if frame.portrait:
        head, top = title_block(frame, content, frame.x(M), frame.y(M), 1 - 2 * frame.x(M), pal, sizes=(.052, .046, .042, .038), lines=2, sub_lines=1, subline=False)
        area_y = top + frame.y(.03)
    else:
        head, top = title_block(frame, content, column_x, frame.y(M), column_w, pal, sizes=(.046, .042, .038, .034), lines=3, sub_lines=2)
        area_y = 0.0
    area_w = column_x - frame.x(.06)                     # room for the lines' elbows before the notes
    if studio:
        x0 = frame.x(M); y0 = max(area_y, frame.y(M)); x1 = area_w; y1 = 1 - frame.y(M)
        photo, to_canvas = place(frame, picture, (0, 0, 1, 1), ((x0 + x1) / 2, (y0 + y1) / 2), (y1 - y0) * .92, (x1 - x0) * .92, True)
    else:
        region = (0, area_y, column_x - frame.x(.03), 1 - area_y)
        photo, to_canvas = place(frame, picture, region, (region[2] * .5, area_y + region[3] * .52), region[3] * .8, region[2] * .82, False)
    items += head
    if head:
        top = max(top, frame.y(M)) + frame.y(.03)
    points = [point for point in content.get('points', []) if point.get('anchor')][:4]
    loose = [point for point in content.get('points', []) if not point.get('anchor')]
    size = .024; gap = frame.y(.035)
    rows = []
    for point in points:
        anchor = to_canvas(point['anchor'])
        if not (photo['x'] <= anchor[0] <= photo['x'] + photo['w'] and photo['y'] <= anchor[1] <= photo['y'] + photo['h']):
            dropped.append(point['title']); continue
        height = row_height(frame, point, size, column_w, False)
        rows.append({'point': point, 'anchor': anchor, 'h': height})
    rows.sort(key=lambda row: row['anchor'][1])
    # Each note level with its part where it can be, top to bottom, never overlapping.
    floor = 1 - frame.y(M)
    ceiling = max(top, frame.y(M))
    for row in rows:
        row['y'] = row['anchor'][1] - size * .65 * frame.sy
    for _ in range(3):
        cursor = ceiling
        for row in rows:
            row['y'] = max(row['y'], cursor); cursor = row['y'] + row['h'] + gap
        overflow = cursor - gap - floor
        if overflow > 0:
            for row in rows:
                row['y'] -= overflow
            cursor = floor
            for row in reversed(rows):
                row['y'] = min(row['y'], cursor - row['h']); cursor = row['y'] - gap
    while rows and rows[0]['y'] < ceiling - 1e-6:
        dropped.append(rows.pop()['point']['title'])
        for row in rows:
            row['y'] = max(ceiling, row['y'])
    elbow = column_x - frame.x(.05)
    for index, row in enumerate(rows):
        point = row['point']
        items.append({'id': 'point%d' % (index + 1), 'type': 'callout', 'style': 'line', 'x': column_x, 'y': row['y'], 'w': column_w,
                      'title': point['title'], 'detail': point.get('detail') or '', 'icon': None, 'anchor': [round(v, 5) for v in row['anchor']],
                      'elbow': round(max(elbow, row['anchor'][0] + frame.x(.03)), 5), 'size': size, 'color': pal['ink'], 'soft': pal['soft'], 'plate': None})
    # Points that name no visible part are listed under the notes, without a line.
    cursor = max([row['y'] + row['h'] for row in rows], default=top) + frame.y(.04)
    for index, point in enumerate(loose[:max(0, 4 - len(rows))]):
        height = row_height(frame, point, size * .92, column_w, False)
        if cursor + height > floor:
            dropped.append(point['title']); continue
        items.append({'id': 'note%d' % (index + 1), 'type': 'callout', 'style': 'line', 'x': column_x, 'y': cursor, 'w': column_w, 'title': point['title'],
                      'detail': point.get('detail') or '', 'icon': None, 'anchor': None, 'size': size * .92, 'color': pal['ink'], 'soft': pal['soft'], 'plate': None})
        cursor += height + gap
    dropped += [point['title'] for point in loose[max(0, 4 - len(rows)):]]
    return {'color': pal['canvas'], 'photo': photo}, items, dropped


def free_band(picture, frame, hint=None):
    """Where a scene photo is calm enough for a headline: (side, its rectangle, its mean brightness)."""
    bands = {'left': (0, 0, .46, 1), 'right': (.54, 0, 1, 1), 'top': (0, 0, 1, .34), 'bottom': (0, .66, 1, 1)}
    order = [hint] if hint in bands else []
    best = None
    for side, rect in bands.items():
        product, scenery = picture.cover(rect) if not picture.estimated or getattr(picture, 'boxed', False) else (0.0, 0.0)
        mean, uneven = picture.tone(rect)
        cost = product * 4 + scenery * 1.2 + uneven * 1.5 + (0 if side in order else .15) + (.05 if side in ('right', 'bottom') else 0)
        if best is None or cost < best[0]:
            best = (cost, side, rect, mean, product)
    return best


def compose_scene(frame, picture, content, pal):
    M = .07; dropped = []
    cost, side, rect, mean, product = free_band(picture, frame, content.get('free_area'))
    if product > .2 or cost > 1.6:
        # No calm side: the headline gets its own band above the picture (ref 09).
        tone = 'light'; band = .28 if not frame.portrait else .24
        local = {**pal, 'ink': INK[tone], 'soft': SOFT[tone]}
        head, bottom = title_block(frame, content, frame.x(M), frame.y(.06), 1 - 2 * frame.x(M), local, sizes=(.06, .054, .048, .044), lines=2, sub_lines=1)
        band = max(band, bottom + frame.y(.05))
        if pal['studio']:
            photo, _ = place(frame, picture, (0, 0, 1, 1), (.5, band + (1 - band - frame.y(.05)) / 2), (1 - band - frame.y(.08)) * .92, .8, True)
            return {'color': pal['canvas'], 'photo': photo}, head, dropped
        photo, _ = place(frame, picture, (0, band, 1, 1 - band), (.5, band + (1 - band) / 2), 1.0, 1.0, False)
        return {'color': CANVAS[tone], 'photo': photo}, head, dropped
    light_words = mean < .58
    ink = INK['dark'] if light_words else INK['light']; soft = '#e8e8ed' if light_words else SOFT['light']
    local = {**pal, 'ink': ink, 'soft': soft}
    if side in ('left', 'right'):
        w = .4 if not frame.portrait else .46
        x = frame.x(M) if side == 'left' else 1 - frame.x(M) - w
        head, bottom = title_block(frame, content, x, frame.y(M + .02), w, local, sizes=(.068, .06, .054, .048), lines=3, sub_lines=3)
        fade = {'left': 'right', 'right': 'left'}[side]
        scrim = _panel('scrim', 0 if side == 'left' else 1 - .62, 0, .62, 1, '#000000' if light_words else '#ffffff', .5 if light_words else .55, 0, fade)
    else:
        w = 1 - 2 * frame.x(M)
        head, bottom = title_block(frame, content, frame.x(M), frame.y(M), w, local, sizes=(.068, .06, .054, .048), lines=2, sub_lines=2)
        if side == 'bottom':
            shift = 1 - frame.y(M) - bottom
            for item in head:
                item['y'] += shift
        fade = {'top': 'down', 'bottom': 'up'}[side]
        h = .42
        scrim = _panel('scrim', 0, 0 if side == 'top' else 1 - h, 1, h, '#000000' if light_words else '#ffffff', .5 if light_words else .55, 0, fade)
    return None, [scrim] + head, dropped


def compose_cards(frame, picture, content, pal):
    M = .045; G = .018; items = []; dropped = []
    tone = pal['tone']; canvas = CANVAS[tone] if not pal['studio'] else ('#f2f2f0' if tone == 'light' else '#111113')
    card = CARD[tone]; ink = INK[tone]; soft = SOFT[tone]
    radius = .024
    cells = ([('fig', figure) for figure in content.get('figures', [])] + [('point', point) for point in content.get('points', [])])[:3]
    count = max(1, len(cells))
    if frame.portrait:
        big = (frame.x(M), frame.y(M), 1 - 2 * frame.x(M), .6 - frame.y(M))
        cw = (1 - 2 * frame.x(M) - frame.x(G) * (count - 1)) / count
        small = [(frame.x(M) + index * (cw + frame.x(G)), .6 + frame.y(G), cw, 1 - .6 - frame.y(G) - frame.y(M)) for index in range(count)]
    else:
        share = .6 if not frame.wide else .58
        big = (frame.x(M), frame.y(M), share - frame.x(M), 1 - 2 * frame.y(M))
        ch = (1 - 2 * frame.y(M) - frame.y(G) * (count - 1)) / count
        small = [(share + frame.x(G), frame.y(M) + index * (ch + frame.y(G)), 1 - share - frame.x(G) - frame.x(M), ch) for index in range(count)]
    bx, by, bw, bh = big
    local = {**pal, 'ink': ink, 'soft': soft}
    inset = .04 if pal['studio'] else 0                  # on the card; a photo's headline stands above it, flush with its edge
    head, bottom = title_block(frame, content, bx + frame.x(inset), by + frame.y(inset), bw - frame.x(2 * inset), local,
                               sizes=(.05, .046, .042, .038), lines=2, sub_lines=1, subline=False)
    product_top = bottom + frame.y(.04)
    centre_y = (product_top + by + bh - frame.y(.03)) / 2
    height = (by + bh - frame.y(.04) - product_top)
    if pal['studio']:
        photo, _ = place(frame, picture, (bx, by, bw, bh), (bx + bw / 2, centre_y), min(height, bh * .8), bw * .82, True)
    else:
        # A photo always fills its frame, so its frame starts under the headline: the words stay on the card.
        top = (product_top - frame.y(.015)) if head else by
        photo, _ = place(frame, picture, (bx, top, bw, by + bh - top), (bx + bw / 2, (top + by + bh) / 2), (by + bh - top) * .8, bw * .82, False)
    photo['radius'] = radius
    # The large card is the photo's own frame: on a light studio render, white like the render's sweep.
    photo['fill'] = pal['canvas'] if pal['studio'] else card
    if pal['studio'] and tone == 'light':
        photo['fill'] = '#ffffff' if _luminance(pal['canvas']) > .96 else pal['canvas']
    for index, (kind, value) in enumerate(cells):
        x, y, w, h = small[index]
        items.append(_panel('card%d' % (index + 1), x, y, w, h, card, 1, radius))
        pad_x = frame.x(.032); pad_y = frame.y(.03)
        glyph = value.get('glyph')
        if glyph:
            items.append(_icon('cardicon%d' % (index + 1), glyph, x + pad_x + frame.x(.02), y + pad_y + frame.y(.02), .04, pal['accent'] if pal['accent'] != ink else ink))
        inner = w - 2 * pad_x
        if kind == 'fig':
            size = min(.06, (h - 2 * pad_y) / frame.sy * .42)
            fh = figure_height(frame, size)
            items.append(_figure('fig%d' % (index + 1), value, x + pad_x, y + h - pad_y - fh, inner, size, pal['accent'], soft))
        else:
            size = .026 if not frame.portrait else .022
            limit = inner / frame.sx
            title = _wrap(value['title'], size, limit)[:2]; detail = _wrap(value.get('detail') or '', size * .78, limit)[:2] if value.get('detail') else []
            th = len(title) * size * LEADING['headline'] * frame.sy; dh = len(detail) * size * .78 * LEADING['body'] * frame.sy
            y0 = y + h - pad_y - th - (dh + frame.y(.01) if detail else 0)
            items.append(_text('title%d' % (index + 1), 'body', title, x + pad_x, y0, inner, size, ink, 700))
            items[-1]['leading'] = LEADING['headline']
            if detail:
                items.append(_text('detail%d' % (index + 1), 'caption', detail, x + pad_x, y0 + th + frame.y(.01), inner, size * .78, soft, 400))
    return {'color': canvas, 'photo': photo}, items + head, dropped


def compose_spotlight(frame, picture, content, pal):
    M = .07; items = []; dropped = []
    figures = content.get('figures', [])[:2]
    head, bottom = title_block(frame, content, frame.x(M), frame.y(M), 1 - 2 * frame.x(M), pal, sizes=(.06, .054, .048, .044), lines=1, sub_lines=1,
                               align='center')
    items += head
    band_h = .15 if not frame.portrait else .13
    band_y = 1 - frame.y(M) - band_h if figures else 1
    top = bottom + frame.y(.03); low = band_y - frame.y(.035)
    region = (0, 0, 1, 1) if pal['studio'] else (0, top, 1, band_y - top)
    photo, to_canvas = place(frame, picture, region, (.5, (top + low) / 2), max(.2, low - top) * .96, .78, pal['studio'])
    if figures:
        items.append(_panel('band', 0, band_y, 1, band_h, pal['accent'] if pal['accent'] not in INK.values() else '#1d1d1f', 1, 0))
        on = '#ffffff' if _luminance(items[-1]['color']) < .62 else '#1d1d1f'
        each = (1 - 2 * frame.x(M)) / len(figures)
        size = .062 if len(figures) == 1 else .056
        fh = figure_height(frame, size)
        for index, figure in enumerate(figures):
            x = frame.x(M) + index * each
            items.append(_figure('fig%d' % (index + 1), figure, x, band_y + (band_h - fh) / 2, each, size, on, on, 'center'))
            if index:
                items.append(_rule('figrule%d' % index, x, band_y + band_h * .22, x, band_y + band_h * .78, on, .0016, .45))
    return {'color': pal['canvas'], 'photo': photo}, items, dropped


def compose_specs(frame, picture, content, pal):
    M = .07; items = []; dropped = []
    specs = content.get('specs', [])[:4] or [{**figure, 'values': None} for figure in content.get('figures', [])[:4]]
    head, bottom = title_block(frame, content, frame.x(M), frame.y(M), 1 - 2 * frame.x(M), pal, sizes=(.056, .05, .046, .042), lines=1, sub_lines=1)
    items += head
    lists = [spec for spec in specs if spec.get('values')]
    plain = [spec for spec in specs if not spec.get('values')][:4]
    row_h = .15 if plain else 0
    ladder_h = .12 if lists else 0
    floor = 1 - frame.y(M)
    ladder_y = floor - ladder_h * frame.sy / frame.sy - (row_h + .03 if plain else 0)
    row_y = floor - row_h
    top = bottom + frame.y(.03)
    low = (ladder_y if lists else row_y) - frame.y(.04)
    region = (0, 0, 1, 1) if pal['studio'] else (0, top, 1, low - top)
    photo, _ = place(frame, picture, region, (.5, (top + low) / 2), max(.2, low - top) * .98, .86, pal['studio'])
    if plain:
        items.append(_rule('specrule', frame.x(M), row_y - frame.y(.025), 1 - frame.x(M), row_y - frame.y(.025), pal['line'], .0016, .2))
        each = (1 - 2 * frame.x(M)) / len(plain)
        size = .046 if len(plain) <= 3 else .04
        for index, spec in enumerate(plain):
            x = frame.x(M) + index * each
            if index:
                items.append(_rule('specsep%d' % index, x, row_y + frame.y(.005), x, row_y + row_h - frame.y(.01), pal['line'], .0016, .2))
            inner_x = x + (frame.x(.03) if index else 0)
            if spec.get('glyph'):
                items.append(_icon('specicon%d' % (index + 1), spec['glyph'], inner_x + frame.x(.02), row_y + frame.y(.03), .036, pal['ink']))
            items.append(_figure('spec%d' % (index + 1), spec, inner_x, row_y + frame.y(.062), each - frame.x(.04), size, pal['ink'], pal['soft']))
    if lists:
        spec = lists[0]
        values = [str(value)[:8] for value in spec['values']][:12]
        items.append({'id': 'ladder', 'type': 'chips', 'x': frame.x(M), 'y': ladder_y, 'w': 1 - 2 * frame.x(M), 'values': values,
                      'unit': str(spec.get('unit') or '')[:12], 'label': str(spec.get('label') or '')[:40], 'size': .03, 'color': pal['ink'],
                      'soft': pal['soft'], 'fill': pal['card'] if pal['tone'] == 'light' else '#2a2a2d'})
        dropped += [other.get('label', '') for other in lists[1:]]
    return {'color': pal['canvas'], 'photo': photo}, items, dropped


def _slots(items, content, layout):
    points = [point['title'] for point in content.get('points', [])]
    for item in items:
        ident = item['id']
        if ident in ('kicker', 'headline', 'subline'):
            item['slot'] = ident
        elif item['type'] == 'callout' and item.get('title') in points:
            item['slot'] = 'points.%d' % points.index(item['title'])
        elif item['type'] == 'figure' and re.fullmatch(r'(fig|spec)\d+', ident):
            rows = content.get('figures' if ident.startswith('fig') else 'specs', [])
            position = next((index for index, row in enumerate(rows) if str(row.get('value')) == item['value'] and str(row.get('label') or '') == item['label']), None)
            if position is None and ident.startswith('fig') and layout == 'specs':
                rows = content.get('figures', [])
                position = next((index for index, row in enumerate(rows) if str(row.get('value')) == item['value']), None)
            if position is not None:
                item['slot'] = '%s.%d' % ('figures' if ident.startswith('fig') and not (layout == 'specs' and content.get('specs')) else 'specs', position)
        elif item['type'] == 'text' and re.fullmatch(r'(title|detail)\d+', ident):
            text = item['text'].replace('\n', '')
            for index, point in enumerate(content.get('points', [])):
                if text in (point['title'].replace(' ', ''), point['title']) or (ident.startswith('detail') and text == (point.get('detail') or '').replace(' ', '')):
                    item['slot'] = 'points.%d.%s' % (index, 'title' if ident.startswith('title') else 'detail'); break
        elif item['type'] == 'chips':
            position = next((index for index, row in enumerate(content.get('specs', [])) if row.get('values')), None)
            if position is not None:
                item['slot'] = 'specs.%d' % position


COMPOSERS = {'split': compose_split, 'callouts': compose_callouts, 'scene': compose_scene, 'cards': compose_cards,
             'spotlight': compose_spotlight, 'specs': compose_specs}


def suitable(content, pal, picture):
    """The compositions this content and picture can fill, best first."""
    figures = len(content.get('figures', [])); specs = len(content.get('specs', []))
    anchored = sum(1 for point in content.get('points', []) if point.get('anchor'))
    points = len(content.get('points', []))
    order = []
    if pal.get('scene'):
        order.append('scene')
    if specs >= 2 or figures >= 3 or any(spec.get('values') for spec in content.get('specs', [])):
        order.append('specs')
    if figures:
        order.append('spotlight')
    if anchored >= 2 and not picture.estimated:
        order.append('callouts')
    order.append('split')
    if points + figures >= 2:
        order.append('cards')
    for name in LAYOUTS:
        if name not in order:
            order.append(name)
    return order


def compose(layout, content, picture, aspect, generated=False, accent=None):
    """(canvas, items, report) for one composition of this content on this picture."""
    if layout not in COMPOSERS:
        raise ValueError('未知的版式')
    frame = Frame(aspect); pal = palette(picture, generated)
    if accent:
        pal['accent'] = accent
    canvas, items, dropped = COMPOSERS[layout](frame, picture, copy.deepcopy(content), pal)
    # Each item says which words of the content it shows, so an edit is kept when the layout changes.
    _slots(items, content, layout)
    # Nothing is left out silently: every point, figure or specification no item shows is named in the report.
    shown = {'.'.join(item['slot'].split('.')[:2]) for item in items if item.get('slot')}
    values = {item['value'] for item in items if item['type'] == 'figure'} | {'–'.join(item['values'][::max(1, len(item['values']) - 1)]) for item in items if item['type'] == 'chips'}
    for group in ('points', 'figures', 'specs'):
        for index, entry in enumerate(content.get(group, [])):
            if '%s.%d' % (group, index) not in shown and (group == 'points' or entry.get('value') not in values):
                dropped.append(entry.get('title') or entry.get('label') or entry.get('value'))
    dropped = list(dict.fromkeys(text for text in dropped if text))
    # Every item stays on the canvas.
    for item in items:
        for key in ('x', 'y'):
            item[key] = round(min(1.4, max(-.4, item[key])), 5)
        for key in ('w', 'h', 'x2', 'y2', 'size', 'elbow'):
            if key in item and isinstance(item[key], float):
                item[key] = round(item[key], 5)
    return canvas, items, {'layout': layout, 'dropped': dropped, 'tone': pal['tone'], 'studio': pal['studio']}


# ------------------------------------------------------------------ what a figure may say

NUMBER = re.compile(r'\d+(?:\.\d+)?')


def numbers_in(text):
    """The numbers in a text, written the same way ("24.0" and "24" are one number)."""
    return {value.rstrip('0').rstrip('.') if '.' in value else value for value in NUMBER.findall(str(text or ''))}


def grounded(value, facts):
    """Whether every number in ``value`` was given by the user (or the facts the app knows)."""
    wanted = numbers_in(value)
    return bool(wanted) and wanted <= numbers_in(facts)

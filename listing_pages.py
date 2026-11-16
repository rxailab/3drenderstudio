"""The pages of a listing: one template per kind of image a product page carries.

Each template takes the same context (brand, product record, components, staged pictures) and
returns a page: a list of drawing items on a 1000 x 1000 unit canvas. Templates only arrange;
what they say comes from the product record, and every number they print is one the user
entered or one measured on the model.

    main      the product large on white with everything in the box around it, no words
    includes  what is in the box: a grid of cut-outs with model numbers
    callouts  the product with leader lines to its features
    usecase   the product on a workpiece, with dimensions and features marked
    feature   one feature close up, with a headline
    band      a picture above a band that carries the headline and key figures
    benefits  the product beside a list of points with icons or ticks
    specs     a side view with dimension lines over a specification table
"""
import math

import listing_draw as draw
import listing_elements as el
from blender import stage_geometry as geometry

H = 1000
KINDS = {'main': ('主图', 'Main image'), 'includes': ('套装清单', 'Kit includes'), 'callouts': ('功能标注', 'Feature callouts'),
         'usecase': ('使用场景标注', 'Use case'), 'feature': ('特写卖点', 'Feature close-up'), 'band': ('标题横幅', 'Headline band'),
         'benefits': ('卖点列表', 'Benefits'), 'specs': ('尺寸规格', 'Specifications')}
STRINGS = {
    'en': {'includes': 'Kit includes', 'specs': 'Specifications', 'length': 'Length', 'width': 'Width', 'height': 'Height', 'in_use': 'In use',
           'why': 'Built for the job', 'on': 'on', 'pieces': 'PCS'},
    'zh': {'includes': '套装包含', 'specs': '规格参数', 'length': '长度', 'width': '宽度', 'height': '高度', 'in_use': '使用示意',
           'why': '核心卖点', 'on': '用于', 'pieces': '件套'},
}
WORKPIECE_NAMES = {'en': {'pipe': 'pipe', 'rod': 'rod', 'board': 'board', 'sheet': 'sheet', 'block': 'block'},
                   'zh': {'pipe': '管材', 'rod': '圆棒', 'board': '板材', 'sheet': '薄板', 'block': '块料'}}
MATERIAL_NAMES = {'en': {'pvc_white': 'PVC', 'pvc_grey': 'PVC', 'plastic_black': 'plastic', 'copper': 'copper', 'steel': 'steel', 'aluminium': 'aluminium',
                         'wood': 'wood', 'concrete': 'concrete', 'drywall': 'drywall'},
                  'zh': {'pvc_white': 'PVC', 'pvc_grey': 'PVC', 'plastic_black': '塑料', 'copper': '铜', 'steel': '钢', 'aluminium': '铝',
                         'wood': '木', 'concrete': '混凝土', 'drywall': '石膏板'}}


class Shot:
    """A staged picture laid on a page: where it is drawn, and where its points land."""

    def __init__(self, result, picture=None):
        self.result = result; self.path = str(result['path']); self.picture = picture
        self.width = result['width']; self.height = result['height']
        self.crop = [0.0, 0.0, 1.0, 1.0]; self.origin = (0.0, 0.0); self.scale = 1000 / self.width; self.box = [0, 0, 1000, 1000]

    def place(self, box, pad=.015, anchor=(.5, .5), shadow=False, whole=False):
        """The image item that shows the product part of the picture inside ``box`` [x0, y0, x1, y1]."""
        rect = [0, 0, 1, 1] if whole else self.result.get('product_rect') or [0, 0, 1, 1]
        self.crop = [max(0.0, rect[0] - pad), max(0.0, rect[1] - pad), min(1.0, rect[2] + pad), min(1.0, rect[3] + pad)]
        width = (self.crop[2] - self.crop[0]) * self.width; height = (self.crop[3] - self.crop[1]) * self.height
        self.scale = min((box[2] - box[0]) / width, (box[3] - box[1]) / height)
        drawn = (width * self.scale, height * self.scale)
        self.origin = (box[0] + (box[2] - box[0] - drawn[0]) * anchor[0], box[1] + (box[3] - box[1] - drawn[1]) * anchor[1])
        self.box = [self.origin[0], self.origin[1], self.origin[0] + drawn[0], self.origin[1] + drawn[1]]
        return el.cutout(self.path, self.origin[0], self.origin[1], drawn[0], drawn[1], (.5, .5), shadow, None if whole else list(self.crop))

    def point(self, fx, fy):
        """A point of the picture (fractions of its size) in page units."""
        return (self.origin[0] + (fx - self.crop[0]) * self.width * self.scale, self.origin[1] + (fy - self.crop[1]) * self.height * self.scale)

    def fraction(self, x, y):
        return ((x - self.origin[0]) / (self.width * self.scale) + self.crop[0], (y - self.origin[1]) / (self.height * self.scale) + self.crop[1])

    def anchor(self, parts, facing=None):
        """Where these parts are visible, in page units; None when they are hidden or unknown."""
        if not self.picture or not parts:
            return None
        found, share = self.picture.anchor(parts, facing)
        return self.point(*found) if found and share > .0008 else None

    def cover(self, rect):
        """How much of a page rectangle lies on the product (or the workpiece)."""
        if not self.picture:
            return 0.0
        x0, y0 = self.fraction(rect[0], rect[1]); x1, y1 = self.fraction(rect[2], rect[3])
        if x1 <= 0 or y1 <= 0 or x0 >= 1 or y0 >= 1:
            return 0.0
        return self.picture.cover([max(0, x0), max(0, y0), min(1, x1), min(1, y1)])[0]

    def shares(self, rect):
        """(share of a page rectangle on the product, share on the workpiece)."""
        picture = self.picture
        if not picture:
            return 0.0, 0.0
        x0, y0 = self.fraction(rect[0], rect[1]); x1, y1 = self.fraction(rect[2], rect[3])
        if x1 <= 0 or y1 <= 0 or x0 >= 1 or y0 >= 1:
            return 0.0, 0.0
        piece = picture.objects.index('Listing workpiece') if 'Listing workpiece' in picture.objects else None
        c0 = max(0, int(x0 * picture.nx)); c1 = min(picture.nx, max(c0 + 1, int(x1 * picture.nx + 1)))
        r0 = max(0, int(y0 * picture.ny)); r1 = min(picture.ny, max(r0 + 1, int(y1 * picture.ny + 1)))
        product = work = 0
        for row in range(r0, r1):
            for column in range(c0, c1):
                cell = picture.cells[row * picture.nx + column]
                if cell == piece and piece is not None:
                    work += 1
                elif cell >= 0:
                    product += 1
        area = max(1, (r1 - r0) * (c1 - c0))
        return product / area, work / area

    def product_box(self):
        rect = self.result.get('product_rect') or [0, 0, 1, 1]
        return [*self.point(rect[0], rect[1]), *self.point(rect[2], rect[3])]


def strings(ctx):
    return STRINGS[ctx['language']]


def length_text(ctx, mm):
    return geometry.dimension_text(mm, ctx['brand'].unit)


def _page(kind, items, background, notes=None):
    return {'kind': kind, 'height': H, 'background': background, 'items': items, 'notes': notes or []}


def _away(p1, p2, centre, amount):
    """The signed offset that moves a dimension line away from ``centre``."""
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) or 1.0
    nx, ny = -(p2[1] - p1[1]) / length, (p2[0] - p1[0]) / length
    middle = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    return amount if (middle[0] - centre[0]) * nx + (middle[1] - centre[1]) * ny >= 0 else -amount


def _dimension_fit(shot, p1, p2, centre, label, avoid=()):
    """Where a dimension can be read: (offset, label position along it), or None when it would run over the tool.

    The line may cross the workpiece, never the product; its label sits clear of both, and of the points in ``avoid``.
    """
    for amount in (30, 64):
        offset = _away(p1, p2, centre, amount)
        lines, _, normal = draw.dimension_parts({'p1': list(p1), 'p2': list(p2), 'offset': offset})
        a, b = lines[0]['points']
        along = [(a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share) for share in (.06, .2, .35, .5, .65, .8, .94)]
        if any(shot.shares([x - 12, y - 12, x + 12, y + 12])[0] > .08 for x, y in along):
            continue
        if any(math.hypot(x - ax, y - ay) < 46 for x, y in along for ax, ay in avoid):
            continue
        width = len(label) * 15 + 24; side = 1 if offset >= 0 else -1
        for share in (.5, .34, .66, .22, .78):
            x = a[0] + (b[0] - a[0]) * share + normal[0] * 26 * side; y = a[1] + (b[1] - a[1]) * share + normal[1] * 26 * side
            if sum(shot.shares([x - width / 2, y - 20, x + width / 2, y + 20])) < .02 and all(math.hypot(x - ax, y - ay) > 70 for ax, ay in avoid):
                return offset, share
    return None


# ---- main ----

def page_main(ctx):
    """The first image: white, wordless, the product large in front of everything that comes with it."""
    hero = ctx['shots']['hero']; parts = ctx['components']; items = []
    if not parts:
        return _page('main', [hero.place([75, 75, 925, 925], shadow=True)], '#ffffff')
    product_mm = ctx['product_mm']
    containers = [part for part in parts if part['role'] in ('case', 'bag')]
    back = max(containers, key=lambda part: part.get('size_mm') or 0) if containers else None
    front = []
    for part in parts:
        if part is not back:
            front += [part] * min(2, part['quantity'])
    front = front[:5]
    if back:
        items.append(el.cutout(back['path'], 30, 60, 650, 580, (.5, 1)))
        items.append(hero.place([330, 190, 985, 800], anchor=(.5, 1), shadow=True))
    else:
        items.append(hero.place([170, 50, 960, 720] if front else [75, 75, 925, 925], anchor=(.5, 1), shadow=True))
    if front:
        widths = [max(190.0, min(340.0, 600 * (part.get('size_mm') or product_mm * .5) / product_mm)) for part in front]
        overlap = 18; total = sum(widths) - overlap * (len(widths) - 1); limit = 640 if back else 900
        if total > limit:
            widths = [width * limit / total for width in widths]
        # Lowest and leftmost first, so the row reads front to back.
        at = 36.0
        for index, (part, width) in enumerate(zip(front, widths)):
            lift = 0 if index % 2 == 0 else 26
            items.append(el.cutout(part['path'], at, 962 - lift - width, width, width, (.5, 1)))
            at += width - overlap
    return _page('main', items, '#ffffff')


# ---- kit includes ----

def _rows(count):
    table = {1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [2, 3], 6: [3, 3], 7: [3, 4], 8: [4, 4], 9: [3, 3, 3], 10: [3, 3, 4], 11: [3, 4, 4], 12: [4, 4, 4]}
    return table.get(count) or [4] * math.ceil(count / 4)


def page_includes(ctx):
    """What is in the box: each thing as its own cut-out over its model number and a line of description."""
    brand = ctx['brand']; product = ctx['product']; hero = ctx['shots']['hero']
    items = el.ground(brand, H); frame_items, box = el.frame(brand, H, strings(ctx)['includes']); items += frame_items
    cells = [{'path': None, 'heading': product['model'] or product['title'], 'lines': [product['title'] if product['model'] else product['subline']], 'quantity': 1}]
    for part in ctx['components']:
        prefix = '(%d) ' % part['quantity'] if part['quantity'] > 1 else ''
        cells.append({'path': part['path'], 'heading': prefix + (part['sku'] or part['name']), 'lines': [part['name'] if part['sku'] else '', part['caption']],
                      'quantity': part['quantity']})
    cells = cells[:12]; rows = _rows(len(cells)); ruled = brand.tokens['card']['style'] == 'rules'
    gap = 0 if ruled else 18; top = box[1] - (16 if ruled else 0); bottom = box[3] + (12 if ruled else 0)
    left = 0 if ruled else box[0]; right = 1000 if ruled else box[2]
    row_height = (bottom - top - gap * (len(rows) - 1)) / len(rows); index = 0
    for row_index, columns in enumerate(rows):
        width = (right - left - gap * (columns - 1)) / columns; y = top + row_index * (row_height + gap)
        for column in range(columns):
            if index >= len(cells):
                break
            cell = cells[index]; index += 1; x = left + column * (width + gap)
            ground = brand.ground
            if not ruled:
                ground = brand.panel
                card = {'type': 'rect', 'x': x, 'y': y, 'w': width, 'h': row_height, 'radius': brand.tokens['card']['radius'], 'fill': brand.panel}
                if brand.tokens['card']['style'] == 'outline':
                    card.update(stroke=draw.mix(brand.ground, brand.accent_on_ground, .55), stroke_width=2)
                items.append(card)
            inner_x = x + (10 if ruled else 6); inner_w = width - (20 if ruled else 12)
            if cell['path'] is None:
                # The product itself, from its staged picture.
                heading_style = brand.label(min(26, inner_w * .075), ground, align='center')
                shown = [line for line in cell['lines'] if line][:1]
                text_height = draw.cap_height(heading_style) * 2.2 + (34 if shown else 0)
                picture = Shot(hero.result, hero.picture)
                items.append(picture.place([inner_x + 20, y + 16, inner_x + inner_w - 20, y + row_height - text_height - 34], anchor=(.5, .5), shadow=True))
                heading, height = el.text(cell['heading'], heading_style, inner_x + 12, 0, inner_w - 24, 2, .7)
                at = y + row_height - 18 - height - (sum(el.text(line, brand.body(min(17, inner_w * .05), ground, align='center'), 0, 0, inner_w - 36, 2, .8)[1] + 7 for line in shown))
                heading['y'] = at; items.append(heading); at += height + 7
                for line in shown:
                    item, height = el.text(line, brand.body(min(17, inner_w * .05), ground, align='center'), inner_x + 18, at, inner_w - 36, 2, .8)
                    items.append(item); at += height + 7
            else:
                items += el.grid_cell(brand, cell['path'], cell['heading'], cell['lines'], inner_x, y, inner_w, row_height, cell['quantity'], ground)
            if ruled and column:
                items.append({'type': 'rect', 'x': x - 1, 'y': y, 'w': 2, 'h': row_height, 'fill': brand.line})
        if ruled and row_index:
            items.append({'type': 'rect', 'x': 0, 'y': y - 1, 'w': 1000, 'h': 2, 'fill': brand.line})
    return _page('includes', items, brand.ground)


# ---- callouts ----

def _place_labels(brand, shot, entries, box, column, detail=True, size=27):
    """Labels in a column either side of the picture, each with a leader to its anchor. Returns (items, placed count)."""
    centre = (shot.box[0] + shot.box[2]) / 2
    sides = {'left': [entry for entry in entries if entry[1][0] < centre], 'right': [entry for entry in entries if entry[1][0] >= centre]}
    limit = math.ceil(len(entries) / 2) if len(entries) > 1 else 1
    for side, other in (('left', 'right'), ('right', 'left')):
        # An overfull side hands its innermost labels to the other.
        while len(sides[side]) > max(limit, 1) and len(sides[other]) < limit:
            moved = min(sides[side], key=lambda entry: abs(entry[1][0] - centre)); sides[side].remove(moved); sides[other].append(moved)
    items = []; placed = 0
    for side, group in sides.items():
        group.sort(key=lambda entry: entry[1][1])
        if not group:
            continue
        top = box[1] + 24; bottom = box[3] - 24; slot = (bottom - top) / len(group)
        x = box[0] + 4 if side == 'left' else box[2] - column - 4
        for index, (feature, point) in enumerate(group):
            point = shot.anchor(feature.get('parts'), side) or point
            trial, height = el.callout(brand, feature['title'], feature.get('detail') if detail else '', None, x, 0, column, side, size=size)
            low = top + slot * index + 4; high = max(low, top + slot * (index + 1) - height - 8)
            best = None; step = 10; at = low
            while at <= high + .1:
                cover = shot.cover([x - 6, at - 6, x + column + 6, at + height + 6])
                score = cover * 2000 + abs(at + 12 - point[1])
                if best is None or score < best[0]:
                    best = (score, at)
                at += step
            parts, _ = el.callout(brand, feature['title'], feature.get('detail') if detail else '', point, x, best[1], column, side, size=size)
            if shot.cover([x - 6, best[1] - 6, x + column + 6, best[1] + height + 6]) > .04:
                # No clear spot in its column: the words get a plate of the ground so they stay readable over the picture.
                words = [part for part in parts if part['type'] == 'text']; widest = max(draw.block_size(part['lines'], part['style'])[0] for part in words)
                left = x + column - widest if side == 'left' else x
                parts.insert(1, {'type': 'rect', 'x': left - 10, 'y': best[1] - 10, 'w': widest + 20, 'h': height + 18, 'fill': brand.ground, 'radius': 6, 'opacity': .88})
            items += parts; placed += 1
    return items, placed


def page_callouts(ctx):
    """The product in the middle, its features named around it with leader lines in the brand's manner."""
    brand = ctx['brand']; product = ctx['product']; hero = Shot(ctx['shots']['hero'].result, ctx['shots']['hero'].picture)
    items = el.ground(brand, H); frame_items, box = el.frame(brand, H, product['title'] if brand.tokens['header'] != 'band' else None); items += frame_items
    features = product['features'][:6]; column = 196
    anchored = []; loose = []
    picture_box = [box[0] + column - 70, box[1] + 10, box[2] - column + 70, box[3] - 10]
    loose_room = 0
    picture_item = hero.place(picture_box, shadow=not brand.on_dark)
    for feature in features:
        point = hero.anchor(feature.get('parts'))
        (anchored if point else loose).append((feature, point))
    if loose:
        loose_room = 70
        picture_item = hero.place([picture_box[0], picture_box[1], picture_box[2], picture_box[3] - loose_room], shadow=not brand.on_dark)
        anchored = [(feature, hero.anchor(feature.get('parts'))) for feature, _ in anchored]
    items.append(picture_item)
    label_items, placed = _place_labels(brand, hero, anchored, [box[0], box[1], box[2], box[3] - loose_room], column, detail=brand.preset != 'frame')
    items += label_items
    notes = []
    if loose:
        chips, _ = el.chip_row(brand, [feature['title'] for feature, _ in loose][:4], box[0], box[3] - 52, box[2] - box[0], 46, align='center')
        items += chips
        notes.append('有 %d 个卖点没有对应到模型上看得见的部件，改为底部标签显示；在卖点里选择对应部件后可画出引线。' % len(loose))
    return _page('callouts', items, brand.ground, notes)


# ---- use case ----

def workpiece_label(ctx, facts):
    language = ctx['language']; name = WORKPIECE_NAMES[language].get(facts['kind'], facts['kind']); material = MATERIAL_NAMES[language].get(facts.get('material'), '')
    return ('%s%s' % (material, name)) if language == 'zh' else ('%s %s' % (material, name)).strip()


def page_usecase(ctx):
    """The product at work on a plain workpiece, with the sizes that matter and its features marked."""
    brand = ctx['brand']; product = ctx['product']; shot = ctx['shots']['usecase']; facts = shot.result.get('workpiece') or {}
    use = ctx['usecase']; language = ctx['language']
    items = el.ground(brand, H)
    items.append({'type': 'image', 'src': shot.path, 'x': 0, 'y': 0, 'w': 1000, 'h': 1000, 'fit': 'cover'})
    shot.crop = [0, 0, 1, 1]; shot.origin = (0, 0); shot.scale = 1000 / shot.width; shot.box = [0, 0, 1000, 1000]
    title = use.get('headline') or ('%s %s' % (strings(ctx)['in_use'], '· ' + workpiece_label(ctx, facts) if facts else '')).strip()
    frame_items, box = el.frame(brand, H, title); items += frame_items
    centre_box = shot.product_box(); centre = ((centre_box[0] + centre_box[2]) / 2, (centre_box[1] + centre_box[3]) / 2)
    notes = []; marked = []
    for key, mark in (('diameter', 'Ø '), ('thickness', '')):
        anchor = (facts.get('anchors') or {}).get(key)
        if not anchor:
            continue
        p1 = shot.point(*anchor['p1']); p2 = shot.point(*anchor['p2'])
        marked.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
        # Read across the workpiece, the label carried clear of it on the side away from the tool.
        if math.hypot(p1[0] - centre[0], p1[1] - centre[1]) > math.hypot(p2[0] - centre[0], p2[1] - centre[1]):
            p1, p2 = p2, p1
        length = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) or 1.0
        ux, uy = (p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length
        ink = brand.ink_for(brand.ground); end = (p2[0] + ux * 54, p2[1] + uy * 54)
        items.append({'type': 'line', 'points': [list(p1), list(p2)], 'color': ink, 'width': 2.6, 'start': 'bar', 'end': 'bar', 'marker_size': 13, 'round': False})
        items.append({'type': 'line', 'points': [list(p2), list(end)], 'color': ink, 'width': 1.6, 'round': False})
        label = mark + length_text(ctx, anchor['mm']); style = {**brand.tokens['label'], 'size': 27, 'color': ink, 'case': 'none'}
        width = draw.text_width(label, style); cap = draw.cap_height(style)
        lx = end[0] + (10 if ux >= 0 else -10 - width); ly = end[1] + uy * 16 - cap / 2
        lx = max(box[0], min(box[2] - width, lx)); ly = max(box[1], min(box[3] - cap, ly))
        items.append({'type': 'rect', 'x': lx - 9, 'y': ly - 9, 'w': width + 18, 'h': cap + 18, 'fill': brand.ground, 'radius': 6, 'opacity': .86})
        items.append({'type': 'text', 'x': lx, 'y': ly, 'w': width + 4, 'text': label, 'style': style})
        marked.append((lx + width / 2, ly + cap / 2))
    if ctx['size_known']:
        edges = {row['axis']: row for row in shot.result.get('dimension_edges', [])}
        level = max((edges[axis] for axis in 'xy' if axis in edges), key=lambda row: row['mm'], default=None)
        for row in [level, edges.get('z')]:
            if not row or row['apparent'] < .12:
                continue
            p1 = shot.point(*row['p1']); p2 = shot.point(*row['p2']); label = length_text(ctx, row['mm'])
            # A size that cannot be read clear of the tool is left out rather than drawn across it.
            found = _dimension_fit(shot, p1, p2, centre, label, marked)
            if found:
                items.append(el.dimension(brand, p1, p2, label, found[0], size=24, label_at=found[1]))
    else:
        notes.append('没有填写产品的真实尺寸，产品本身的尺寸线没有画出；工件按模型文件的单位估算比例。')
    anchored = [(feature, shot.anchor(feature.get('parts'))) for feature in product['features']]
    anchored = [(feature, point) for feature, point in anchored if point][:4]
    label_box = [box[0], box[1] + 10, box[2], box[3] - 10]
    label_items, _ = _place_labels(brand, shot, anchored, label_box, 190, detail=False, size=25)
    items += label_items
    for code in facts.get('notes', []):
        notes.append({'no_opening': '没有在模型上找到可以让工件穿过的开口，工件放在了产品中心；请改用“垫在下方”或“顶在前端”，或手动调整位置。',
                      'wider_than_opening': '所选工件比模型上量到的开口（约 %s mm）更宽。' % facts.get('opening_mm')}.get(code, code))
    return _page('usecase', items, brand.ground, notes)


# ---- feature close-up ----

def page_feature(ctx, index):
    """One feature, close: a headline and a line over a picture of the part that fills the rest of the page."""
    brand = ctx['brand']; feature = ctx['product']['features'][index]; shot = ctx['shots'].get('detail%d' % index)
    items = el.ground(brand, H); frame_items, box = el.frame(brand, H, None); items += frame_items
    x = box[0] + 16; y = box[1] + 22; width = box[2] - box[0] - 32
    number, _ = el.badge(brand, '%02d' % (index + 1), x, y, 44, 'pill' if brand.preset != 'tech' else 'hex'); items += number; y += 44 + 30
    parts, height = el.headline(brand, feature['title'], x, y, width, 78, 2)
    items += parts; y += height
    if feature.get('detail'):
        detail, detail_height = el.paragraph(brand, feature['detail'], x, y + 24, width, 27, max_lines=2)
        items += detail; y += 24 + detail_height
    y += 40
    if shot:
        edge = brand.tokens['card']['style'] == 'rules'            # a framed brand runs the picture from edge to edge
        left = 0 if edge else box[0]; right = 1000 if edge else box[2]; bottom = box[3] + (22 if edge else 0)
        plate = '#eef0f2' if not brand.on_dark else draw.mix(brand.ground, '#ffffff', .1)
        radius = 0 if edge else brand.tokens['card']['radius']
        items.append({'type': 'rect', 'x': left, 'y': y, 'w': right - left, 'h': bottom - y, 'fill': plate, 'radius': radius,
                      'gradient': {'to': draw.mix(plate, '#000000', .1)}})
        if brand.on_dark:
            items.append({'type': 'texture', 'kind': 'glow', 'x': left + 100, 'y': y, 'w': right - left - 200, 'h': bottom - y, 'color': brand.accent, 'opacity': .2})
        items.append({'type': 'image', 'src': shot.path, 'x': left, 'y': y, 'w': right - left, 'h': bottom - y, 'fit': 'cover', 'anchor': (.5, .5),
                      **({'mask': 'round', 'radius': radius} if radius else {})})
    return _page('feature', items, brand.ground)


# ---- headline band ----

def page_band(ctx):
    """A picture above a band: the band carries the headline, a line under it and up to three key figures."""
    brand = ctx['brand']; product = ctx['product']; items = []; split = 610
    band = brand.dark if brand.preset != 'flat' else brand.ground
    if ctx.get('photo'):
        items.append({'type': 'image', 'src': str(ctx['photo']), 'x': 0, 'y': 0, 'w': 1000, 'h': split, 'fit': 'cover', 'anchor': (.5, .5)})
    else:
        top = '#f4f5f7' if not brand.on_dark else draw.mix(brand.dark, '#ffffff', .12)
        items.append({'type': 'rect', 'x': 0, 'y': 0, 'w': 1000, 'h': split, 'fill': top, 'gradient': {'to': draw.mix(top, '#000000', .1)}})
        if brand.on_dark:
            items.append({'type': 'texture', 'kind': 'glow', 'x': 150, 'y': 20, 'w': 700, 'h': 560, 'color': brand.accent, 'opacity': .22})
        hero = Shot(ctx['shots']['hero'].result, ctx['shots']['hero'].picture)
        items.append(hero.place([120, 40, 880, split - 40], shadow=True))
    items += [{'type': 'rect', 'x': 0, 'y': split, 'w': 1000, 'h': H - split, 'fill': band},
              {'type': 'rect', 'x': 0, 'y': split, 'w': 1000, 'h': 10, 'fill': brand.accent_for(band)}]
    figures = product['figures'][:3]; text_width = 560 if figures else 880; x = 56; y = split + 62
    parts, height = el.headline(brand, product['headline'] or product['title'], x, y, text_width, 80, 3, ground=band)
    items += parts
    if product['subline']:
        sub, _ = el.paragraph(brand, product['subline'], x, y + height + 24, text_width, 25, band, max_lines=3); items += sub
    if figures:
        column = 360 / len(figures) if len(figures) > 1 else 300; left = 1000 - 56 - column * len(figures)
        for index, figure in enumerate(figures):
            stat_items, _ = el.stat(brand, figure['value'], figure.get('unit', ''), figure['label'], left + index * column + 10, split + 96, column - 20, band,
                                    size=min(86, column * .36), align='center')
            items += stat_items
            if index:
                items.append({'type': 'rect', 'x': left + index * column, 'y': split + 86, 'w': 2, 'h': 170, 'fill': draw.mix(band, brand.ink_for(band), .25)})
    return _page('band', items, band)


# ---- benefits ----

def page_benefits(ctx):
    """The product beside its points: an icon or a tick, a title and a line each."""
    brand = ctx['brand']; product = ctx['product']
    items = el.ground(brand, H); frame_items, box = el.frame(brand, H, product['headline'] or strings(ctx)['why']); items += frame_items
    hero = Shot(ctx['shots']['hero'].result, ctx['shots']['hero'].picture)
    items.append(hero.place([box[0], box[1] + 10, 520, box[3] - 10], shadow=not brand.on_dark))
    features = product['features'][:5]; x = 560; width = box[2] - x; top = box[1] + 20
    slot = (box[3] - top) / max(1, len(features)); size = min(74, slot * .5)
    for index, feature in enumerate(features):
        y = top + slot * index + (slot - size) / 2 - 10; glyph = ctx['glyphs'].get(index) if brand.tokens['icons'] else None
        accent = brand.accent_on_ground
        if brand.tokens['icons']:
            disc = accent if brand.tokens['card']['style'] != 'outline' else draw.mix(brand.ground, accent, .2)
            items.append({'type': 'ellipse', 'x': x, 'y': y, 'w': size, 'h': size, 'fill': disc,
                          **({'stroke': accent, 'stroke_width': 2} if brand.tokens['card']['style'] == 'outline' else {})})
            ink = brand.ink_for(disc) if disc == accent else accent
            items.append({'type': 'icon', 'glyph': glyph, 'x': x + size * .24, 'y': y + size * .24, 'size': size * .52, 'color': ink} if glyph
                         else el.tick(x + size * .26, y + size * .26, size * .48, ink))
        else:
            items.append({'type': 'rect', 'x': x, 'y': y + 4, 'w': 10, 'h': size - 8, 'fill': accent})
        tx = x + (size + 22 if brand.tokens['icons'] else 30)
        title, height = el.text(feature['title'], brand.label(min(30, size * .42)), tx, y + 6, width - (tx - x), 2, .7)
        items.append(title)
        if feature.get('detail'):
            detail, _ = el.text(feature['detail'], brand.body(min(20, size * .28)), tx, y + 6 + height + 10, width - (tx - x), 2, .8)
            items.append(detail)
    return _page('benefits', items, brand.ground)


# ---- specifications ----

def page_specs(ctx):
    """A level side view with its measured length and height, over the specification table."""
    brand = ctx['brand']; product = ctx['product']; shot = ctx['shots']['side']
    items = el.ground(brand, H); frame_items, box = el.frame(brand, H, strings(ctx)['specs']); items += frame_items
    rows = [(spec['label'], spec['value']) for spec in product['specs']][:8]
    dims = shot.result.get('dimensions_mm') or []; names = strings(ctx)
    if ctx['size_known'] and len(dims) == 3:
        level = sorted(dims[:2], reverse=True)
        measured = [(names['length'], length_text(ctx, level[0])), (names['width'], length_text(ctx, level[1])), (names['height'], length_text(ctx, dims[2]))]
        rows = (rows + [row for row in measured if not any(row[0].lower() == label.lower() for label, _ in rows)])[:9]
    columns = 2 if len(rows) > 4 else 1; per = math.ceil(len(rows) / columns) if rows else 0
    row_height = 56; table_height = per * row_height + 16 if rows else 0
    picture_box = [box[0] + 90, box[1] + 50, box[2] - 90, box[3] - table_height - 86]
    items.append(shot.place(picture_box, shadow=not brand.on_dark))
    notes = []
    if ctx['size_known']:
        centre_box = shot.product_box(); centre = ((centre_box[0] + centre_box[2]) / 2, (centre_box[1] + centre_box[3]) / 2)
        edges = {row['axis']: row for row in shot.result.get('dimension_edges', [])}
        level = max((edges[axis] for axis in 'xy' if axis in edges), key=lambda row: row['apparent'], default=None)
        for row in [level, edges.get('z')]:
            if not row or row['apparent'] < .05:
                continue
            p1 = shot.point(*row['p1']); p2 = shot.point(*row['p2'])
            items.append(el.dimension(brand, p1, p2, length_text(ctx, row['mm']), _away(p1, p2, centre, 34)))
    else:
        notes.append('没有填写产品的真实尺寸，尺寸线没有画出；在产品信息里填写最长边尺寸后重新生成。')
    if rows:
        top = box[3] - table_height + 8; gap = 24; width = (box[2] - box[0] - 40 - gap * (columns - 1)) / columns
        for column in range(columns):
            table_items, _ = el.table(brand, rows[column * per:(column + 1) * per], box[0] + 20 + column * (width + gap), top, width, row_height)
            items += table_items
    return _page('specs', items, brand.ground, notes)


TEMPLATES = {'main': page_main, 'includes': page_includes, 'callouts': page_callouts, 'usecase': page_usecase, 'band': page_band,
             'benefits': page_benefits, 'specs': page_specs}


def compose(kind, ctx, index=0):
    """The page of this kind for this context."""
    if kind == 'feature':
        return page_feature(ctx, index)
    return TEMPLATES[kind](ctx)

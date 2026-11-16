"""The element library: what a listing page is assembled from, drawn in the brand's manner.

Every builder returns plain drawing items (see ``listing_draw``) and, where a layout needs
it, the room they take. Sizes are in page units; the page is 1000 units wide. A builder is
given the ground it will stand on so that ink and accent always read against it.
"""
import math

import listing_draw as draw

ELEMENTS = {
    'ground': ('底色与纹理', 'Page ground: flat colour, glow, grid, stripes, vignette'),
    'frame': ('页眉页脚边框', 'Header and footer frame with a title or a badge and a legal line'),
    'headline': ('标题（可双色）', 'Headline, optionally with one phrase in the accent colour'),
    'paragraph': ('副标题 / 正文', 'Sub-line and body text'),
    'badge': ('徽标', 'Badge: box, pill, hexagon or burst'),
    'chip': ('标签', 'Chip: solid, outlined, soft or slanted'),
    'cutout': ('抠图（带投影）', 'Cut-out picture with a ground shadow'),
    'inset': ('圆形特写', 'Circular close-up with a ring'),
    'callout': ('引线标注', 'Callout with a leader line: elbow, angled or straight'),
    'dimension': ('尺寸线', 'Dimension line with witness lines and a label'),
    'stat': ('大数字指标', 'Key figure: a large number, its unit and a label'),
    'table': ('规格表', 'Specification table'),
    'tick_list': ('勾选列表', 'Tick list'),
    'icon_tile': ('图标要点', 'Icon with a title and a line'),
    'grid_cell': ('套装单元格', 'Kit cell: a cut-out over its model number and description'),
    'step': ('步骤编号', 'Numbered step marker'),
    'arrow': ('箭头', 'Arrow'),
    'chevrons': ('连续箭头', 'Chevrons'),
    'burst': ('爆炸贴', 'Starburst'),
    'corner_flag': ('角标', 'Corner flag'),
    'rule': ('分隔线', 'Rule'),
}


def text(content, style, x, y, w, max_lines=None, shrink=.7):
    """A text item that fits its width. Returns (item, height)."""
    if max_lines:
        style, lines = draw.fit(content, style, w, max_lines, shrink)
    else:
        lines = draw.wrap(content, style, w)
    _, height = draw.block_size(lines, style)
    return {'type': 'text', 'x': x, 'y': y, 'w': w, 'lines': lines, 'style': {**draw.normal(style), 'case': 'none'}}, height


def ground(brand, height):
    """What lies behind everything else on a page of this brand."""
    kind = brand.tokens['ground']; items = []
    if brand.preset == 'tech':
        items += [{'type': 'texture', 'kind': 'glow', 'x': 150, 'y': height * .12, 'w': 900, 'h': height * .9, 'color': brand.accent, 'opacity': .2, 'falloff': 1.4},
                  {'type': 'texture', 'kind': 'grid', 'x': 0, 'y': 0, 'w': 1000, 'h': height, 'color': '#ffffff', 'opacity': .045, 'step': 50, 'size': 1},
                  {'type': 'texture', 'kind': 'vignette', 'x': -200, 'y': -200, 'w': 1400, 'h': height + 400, 'color': '#000000', 'opacity': .5, 'falloff': 2.2}]
    elif brand.preset == 'promo':
        items += [{'type': 'texture', 'kind': 'glow', 'x': 100, 'y': height * .1, 'w': 1000, 'h': height, 'color': brand.accent, 'opacity': .16, 'falloff': 1.3},
                  {'type': 'texture', 'kind': 'diagonal', 'x': 0, 'y': 0, 'w': 1000, 'h': height, 'color': '#ffffff', 'opacity': .03, 'step': 44, 'size': .5}]
    elif kind == 'accent':
        items += [{'type': 'texture', 'kind': 'glow', 'x': -250, 'y': -300, 'w': 1100, 'h': 1100, 'color': '#ffffff', 'opacity': .2, 'falloff': 1.2},
                  {'type': 'texture', 'kind': 'vignette', 'x': -150, 'y': -150, 'w': 1300, 'h': height + 300, 'color': '#000000', 'opacity': .16, 'falloff': 2.4}]
    elif brand.preset == 'clean':
        items += [{'type': 'rect', 'x': 0, 'y': height * .62, 'w': 1000, 'h': height * .38, 'fill': '#ffffff', 'gradient': {'to': '#eef0f2'}}]
    return items


def badge(brand, content, x, y, h, kind=None, ground=None):
    """A short mark such as a voltage or a piece count. Returns (items, width)."""
    ground = ground or brand.ground; kind = kind or brand.tokens['badge']
    accent = brand.accent_for(ground); ink = brand.ink_for(ground)
    if kind == 'burst':
        style = {**brand.tokens['headline'], 'size': h * .26, 'color': brand.ink_for(accent), 'align': 'center', 'leading': 1.0}
        item, height = text(content, style, x + h * .14, 0, h * .72, 2, .5)
        item['y'] = y + (h - height) / 2 + (0 if item['style']['case'] == 'upper' else 0)
        return [burst(x + h / 2, y + h / 2, h / 2, accent)] + [item], h
    style = {**brand.tokens['headline'], 'size': h * .52, 'leading': 1.0, 'case': 'upper'}
    w = draw.text_width(content, style); pad = h * .34; width = w + pad * 2
    cap = draw.cap_height(style); top = y + (h - cap) / 2
    if kind == 'box':
        items = [{'type': 'rect', 'x': x, 'y': y, 'w': width, 'h': h, 'stroke': ink, 'stroke_width': max(3, h * .07)}]
        fill = ink
    elif kind == 'hex':
        cut = h * .28
        items = [{'type': 'poly', 'points': [[x + cut, y], [x + width - cut, y], [x + width, y + h / 2], [x + width - cut, y + h], [x + cut, y + h], [x, y + h / 2]],
                  'stroke': accent, 'stroke_width': max(2.5, h * .06), 'fill': draw.mix(ground, accent, .16)}]
        fill = ink
    else:
        items = [{'type': 'rect', 'x': x, 'y': y, 'w': width, 'h': h, 'radius': h / 2, 'fill': accent}]
        fill = brand.ink_for(accent)
    items.append({'type': 'text', 'x': x + pad, 'y': top, 'w': w + 2, 'text': content, 'style': {**style, 'color': fill}})
    return items, width


def chip(brand, content, x, y, h, ground=None, strong=False):
    """A small label. Returns (items, width)."""
    ground = ground or brand.ground; tokens = brand.tokens['chip']; ink = brand.ink_for(ground); accent = brand.accent_for(ground)
    style = {**brand.tokens['label'], 'size': h * .44, 'leading': 1.0}
    w = draw.text_width(content, style); pad = h * .42; width = w + pad * 2
    kind = tokens['style']; radius = min(tokens['radius'], h / 2)
    if kind == 'outline':
        items = [{'type': 'rect', 'x': x, 'y': y, 'w': width, 'h': h, 'radius': radius, 'stroke': accent if strong else draw.mix(ink, ground, .5), 'stroke_width': 2,
                  'fill': draw.mix(ground, '#ffffff' if draw.luminance(ground) < .4 else '#000000', .06)}]
        fill = ink
    elif kind == 'soft':
        plate = draw.mix(ground, ink, .07) if not strong else accent
        items = [{'type': 'rect', 'x': x, 'y': y, 'w': width, 'h': h, 'radius': radius, 'fill': plate}]
        fill = brand.ink_for(plate)
    elif kind == 'slant':
        lean = h * .3; plate = accent if strong else draw.mix(ground, '#ffffff', .14)
        items = [{'type': 'poly', 'points': [[x + lean, y], [x + width + lean, y], [x + width, y + h], [x, y + h]], 'fill': plate}]
        fill = brand.ink_for(plate); x += lean / 2
    else:
        plate = accent if strong else (ink if draw.luminance(ground) > .4 else '#ffffff')
        items = [{'type': 'rect', 'x': x, 'y': y, 'w': width, 'h': h, 'radius': radius, 'fill': plate}]
        fill = brand.ink_for(plate)
    items.append({'type': 'text', 'x': x + pad, 'y': y + (h - draw.cap_height(style)) / 2, 'w': w + 2, 'text': content, 'style': {**style, 'color': fill}})
    return items, width + (h * .3 if kind == 'slant' else 0)


def chip_row(brand, labels, x, y, w, h, ground=None, gap=None, align='left'):
    """Chips side by side, wrapping to more rows when they do not fit. Returns (items, height)."""
    gap = gap if gap is not None else h * .3; rows = [[]]; widths = [0.0]
    built = [chip(brand, label, 0, 0, h, ground, index == 0) for index, label in enumerate(labels)]
    for parts, width in built:
        if rows[-1] and widths[-1] + gap + width > w:
            rows.append([]); widths.append(0.0)
        widths[-1] += (gap if rows[-1] else 0) + width; rows[-1].append((parts, width))
    items = []
    for index, row in enumerate(rows):
        at = x + ((w - widths[index]) / 2 if align == 'center' else w - widths[index] if align == 'right' else 0)
        for parts, width in row:
            for part in parts:
                items.append(shifted(part, at, y + index * (h + gap)))
            at += width + gap
    return items, len(rows) * h + (len(rows) - 1) * gap


def shifted(item, dx, dy):
    """A copy of an item moved by (dx, dy)."""
    moved = dict(item)
    if 'points' in moved:
        moved['points'] = [[px + dx, py + dy] for px, py in moved['points']]
    for key in ('p1', 'p2'):
        if key in moved:
            moved[key] = [moved[key][0] + dx, moved[key][1] + dy]
    if 'x' in moved:
        moved['x'] = moved['x'] + dx; moved['y'] = moved['y'] + dy
    return moved


def headline(brand, content, x, y, w, size, max_lines=3, ground=None, align='left', two_tone=True, shrink=.6):
    """The page's headline. Without marks of its own, its second line (or last word) takes the accent. Returns (items, height)."""
    style = brand.headline(size, ground, align=align)
    style, lines = draw.fit(content, style, w, max_lines, shrink)
    if two_tone and not any(accented for line in lines for _, accented in line):
        if len(lines) >= 2:
            lines[1] = [(part, True) for part, _ in lines[1]]
        elif lines and ' ' in ''.join(part for part, _ in lines[0]).strip():
            whole = ''.join(part for part, _ in lines[0]); head, _, tail = whole.rpartition(' ')
            lines[0] = [(head + ' ', False), (tail, True)]
    _, height = draw.block_size(lines, style)
    return [{'type': 'text', 'x': x, 'y': y, 'w': w, 'lines': lines, 'style': {**draw.normal(style), 'case': 'none'}}], height


def paragraph(brand, content, x, y, w, size, ground=None, role='body', max_lines=None, **more):
    style = (brand.label if role == 'label' else brand.body)(size, ground, **more)
    item, height = text(content, style, x, y, w, max_lines)
    return [item], height


def frame(brand, height, title=None, mark=None):
    """The header and footer every page of the listing shares. Returns (items, content box [x0, y0, x1, y1])."""
    items = []; top = 40; bottom = height - 40; header = brand.tokens['header']; footer = brand.tokens['footer']
    mark = mark or (brand.badges[0] if brand.badges else None)
    if header == 'band':
        band = 124; stripe = 10
        items += [{'type': 'rect', 'x': 0, 'y': 0, 'w': 1000, 'h': band, 'fill': brand.dark},
                  {'type': 'rect', 'x': 0, 'y': band, 'w': 1000, 'h': stripe, 'fill': brand.accent_on_dark}]
        right = 948
        if brand.name and title:
            style = {**brand.tokens['headline'], 'size': 24, 'color': draw.mix('#ffffff', brand.dark, .25), 'align': 'right'}
            items.append({'type': 'text', 'x': 600, 'y': (band - draw.cap_height(style)) / 2, 'w': 348, 'text': brand.name, 'style': style}); right = 560
        if title:
            style = {**brand.tokens['headline'], 'size': 50, 'color': '#ffffff'}
            item, block = text(title, style, 52, 0, right - 52 if brand.name else 896, 1, .5)
            item['y'] = (band - draw.cap_height(item['style'])) / 2; items.append(item)
        elif mark:
            parts, _ = badge(brand, mark, 52, 30, 64, 'box', brand.dark); items += parts
            if brand.name:
                style = {**brand.tokens['headline'], 'size': 24, 'color': draw.mix('#ffffff', brand.dark, .25), 'align': 'right'}
                items.append({'type': 'text', 'x': 500, 'y': (band - draw.cap_height(style)) / 2, 'w': 448, 'text': brand.name, 'style': style})
        elif brand.name:
            style = {**brand.tokens['headline'], 'size': 44, 'color': '#ffffff'}
            items.append({'type': 'text', 'x': 52, 'y': (band - draw.cap_height(style)) / 2, 'w': 896, 'text': brand.name, 'style': style})
        top = band + stripe + 26
    elif header == 'slash' and (title or mark):
        accent = brand.accent_on_ground; label = title or mark
        style = {**brand.tokens['headline'], 'size': 62, 'color': brand.ink_for(accent)}
        style, lines = draw.fit(label, style, 760, 1, .5)
        width = draw.line_width(lines[0], style) + 130
        items += [{'type': 'poly', 'points': [[0, 46], [width, 46], [width - 46, 152], [0, 152]], 'fill': accent},
                  {'type': 'poly', 'points': [[width + 14, 46], [width + 40, 46], [width - 6, 152], [width - 32, 152]], 'fill': accent, 'opacity': .55},
                  {'type': 'text', 'x': 52, 'y': 46 + (106 - draw.cap_height(style)) / 2, 'w': 780, 'lines': lines, 'style': {**draw.normal(style), 'case': 'none'}}]
        top = 186
    elif title:
        parts, block = headline(brand, title, 56, 62, 888, 58, 2, two_tone=brand.preset == 'clean')
        items += parts; top = 62 + block + 20
        if brand.preset in ('clean', 'tech'):
            items.append({'type': 'rect', 'x': 56, 'y': top, 'w': 72, 'h': 7, 'fill': brand.accent_on_ground}); top += 34
        else:
            top += 14
    elif mark:
        parts, _ = badge(brand, mark, 56, 52, 58); items += parts; top = 138
    if footer == 'band':
        band = 54
        items.append({'type': 'rect', 'x': 0, 'y': height - band, 'w': 1000, 'h': band, 'fill': brand.dark})
        if brand.legal:
            style = {'font': 'inter', 'weight': 500, 'size': 15, 'color': draw.mix('#ffffff', brand.dark, .18)}
            item, block = text(brand.legal, style, 40, 0, 920, 2, .7)
            item['y'] = height - band + (band - block) / 2 + 1; items.append(item)
        bottom = height - band - 22
    elif footer == 'line':
        items.append({'type': 'rect', 'x': 56, 'y': height - 62, 'w': 888, 'h': 2, 'fill': brand.line})
        note = brand.legal or brand.name
        if note:
            style = {'font': 'inter', 'weight': 500, 'size': 15, 'color': brand.soft}
            item, _ = text(note, style, 56, height - 46, 888, 1, .7); items.append(item)
        bottom = height - 80
    elif brand.legal:
        style = {'font': 'inter', 'weight': 500, 'size': 15, 'color': brand.soft}
        item, block = text(brand.legal, style, 56, 0, 888, 2, .7)
        item['y'] = height - 34 - block; items.append(item); bottom = height - 50 - block
    return items, [40, top, 960, bottom]


def cutout(src, x, y, w, h, anchor=(.5, 1), shadow=True, crop=None, flip=False, opacity=1):
    """A cut-out picture fitted into a box, standing on its own soft shadow."""
    item = {'type': 'image', 'src': str(src), 'x': x, 'y': y, 'w': w, 'h': h, 'fit': 'contain', 'anchor': anchor}
    if shadow:
        item['shadow'] = {'kind': 'ground', 'opacity': .26, 'blur': 9, 'squash': .1}
    if crop:
        item['crop'] = crop
    if flip:
        item['flip'] = True
    if opacity < 1:
        item['opacity'] = opacity
    return item


def inset(brand, src, cx, cy, diameter, crop=None, ground=None):
    """A circular close-up with a ring in the accent colour."""
    ground = ground or brand.ground
    return [{'type': 'ellipse', 'x': cx - diameter / 2, 'y': cy - diameter / 2, 'w': diameter, 'h': diameter, 'fill': '#ffffff',
             'shadow': {'dy': 8, 'blur': 14, 'opacity': .22}},
            {'type': 'image', 'src': str(src), 'x': cx - diameter / 2, 'y': cy - diameter / 2, 'w': diameter, 'h': diameter, 'fit': 'cover', 'mask': 'circle',
             'stroke': brand.accent_for(ground), 'stroke_width': max(5, diameter * .03), **({'crop': crop} if crop else {})}]


def leader(brand, start, anchor, ground=None):
    """The line from a label to the spot on the product it names, in the brand's manner."""
    ground = ground or brand.ground; tokens = brand.tokens['leader']
    ink = brand.accent_for(ground) if tokens['ink'] == 'accent' else draw.mix(brand.ink_for(ground), ground, .45) if tokens['ink'] == 'soft' else brand.ink_for(ground)
    (sx, sy), (ax, ay) = start, anchor; route = tokens['route']
    if route == 'elbow' and abs(ax - sx) > 14 and abs(ay - sy) > 14:
        points = [[sx, sy], [ax, sy], [ax, ay]]
    elif route == 'angled' and abs(ax - sx) > 60:
        run = min(46, abs(ax - sx) * .35) * (1 if ax > sx else -1)
        points = [[sx, sy], [sx + run, sy], [ax, ay]]
    else:
        points = [[sx, sy], [ax, ay]]
    return {'type': 'line', 'points': points, 'color': ink, 'width': tokens['width'], 'end': tokens['end'], 'marker_size': max(11, tokens['width'] * 4.2), 'ground': ground}


def callout(brand, title, detail, anchor, x, y, w, side, ground=None, size=27):
    """A label beside the product with a leader to ``anchor``. ``side`` is where the label sits ('left' or 'right'). Returns (items, height)."""
    ground = ground or brand.ground; align = 'right' if side == 'left' else 'left'
    title_item, height = text(title, brand.label(size, ground, align=align), x, y, w, 3, .72)
    items = [title_item]; block = height
    if detail:
        detail_item, extra = text(detail, brand.body(size * .62, ground, align=align), x, y + height + size * .3, w, 2, .8)
        items.append(detail_item); block += size * .3 + extra
    if anchor:
        edge = x + w + 14 if side == 'left' else x - 14
        middle = y + draw.cap_height(title_item['style']) / 2
        items.insert(0, leader(brand, (edge, middle), anchor, ground))
    return items, block


def dimension(brand, p1, p2, label, offset=34, ground=None, size=25):
    ground = ground or brand.ground; ink = brand.ink_for(ground)
    return {'type': 'dimension', 'p1': list(p1), 'p2': list(p2), 'offset': offset, 'label': label, 'color': ink, 'width': 2.4, 'plate': ground, 'ends': 'arrow',
            'marker_size': 11, 'style': {**brand.tokens['label'], 'size': size, 'color': ink, 'case': 'none'}}


def stat(brand, value, unit, label, x, y, w, ground=None, size=84, align='left'):
    """A key figure: the number largest, its unit beside it, the label under. Returns (items, height)."""
    ground = ground or brand.ground
    number = brand.number(size, ground); unit_style = brand.number(size * .42, ground)
    total = draw.text_width(value, number) + (draw.text_width(' ' + unit, unit_style) if unit else 0)
    if total > w:
        scale = w / total; number['size'] *= scale; unit_style['size'] *= scale; total = w
    left = x + ((w - total) / 2 if align == 'center' else 0); cap = draw.cap_height(number)
    value_width = draw.text_width(value, number)
    items = [{'type': 'text', 'x': left, 'y': y, 'w': value_width + 4, 'text': value, 'style': number}]
    if unit:
        items.append({'type': 'text', 'x': left + value_width + number['size'] * .08, 'y': y + cap - draw.cap_height(unit_style), 'w': w, 'text': unit, 'style': unit_style})
    label_item, label_height = text(label, brand.label(size * .25, ground, align=align), x, y + cap + size * .2, w, 2, .8)
    items.append(label_item)
    return items, cap + size * .2 + label_height


def table(brand, rows, x, y, w, row_height=58, ground=None, split=.46):
    """Label and value rows on alternating plates. Returns (items, height)."""
    ground = ground or brand.ground; items = []; ink = brand.ink_for(ground)
    plate = draw.mix(ground, ink, .055); radius = min(brand.tokens['card']['radius'], 12)
    for index, (label, value) in enumerate(rows):
        top = y + index * row_height
        if index % 2 == 0:
            items.append({'type': 'rect', 'x': x, 'y': top, 'w': w, 'h': row_height, 'fill': plate, 'radius': radius})
        label_style = brand.label(row_height * .34, ground, color=draw.mix(ink, ground, .35))
        value_style = brand.label(row_height * .38, ground)
        label_item, _ = text(label, label_style, x + 22, 0, w * split - 34, 1, .6)
        value_item, _ = text(value, value_style, x + w * split, 0, w * (1 - split) - 22, 1, .5)
        label_item['y'] = top + (row_height - draw.cap_height(label_item['style'])) / 2
        value_item['y'] = top + (row_height - draw.cap_height(value_item['style'])) / 2
        items += [label_item, value_item]
    items.append({'type': 'rect', 'x': x, 'y': y - 4, 'w': w, 'h': 4, 'fill': brand.accent_for(ground)})
    return items, len(rows) * row_height


def tick(x, y, size, ink):
    return {'type': 'line', 'points': [[x + size * .12, y + size * .55], [x + size * .4, y + size * .82], [x + size * .9, y + size * .2]], 'color': ink, 'width': size * .16}


def tick_list(brand, entries, x, y, w, size=26, ground=None, gap=.7):
    """Lines of text, each after a tick in the accent colour. Returns (items, height)."""
    ground = ground or brand.ground; items = []; at = y; accent = brand.accent_for(ground)
    for entry in entries:
        item, height = text(entry, brand.label(size, ground), x + size * 1.5, at, w - size * 1.5, 2, .8)
        cap = draw.cap_height(item['style'])
        items += [tick(x, at + cap / 2 - size * .5, size, accent), item]
        at += height + size * gap
    return items, at - y - size * gap if entries else 0


def icon_tile(brand, glyph, title, detail, x, y, w, ground=None, size=64, align='left'):
    """An icon in a disc over a title and a line. Returns (items, height)."""
    ground = ground or brand.ground; accent = brand.accent_for(ground); items = []
    left = x + (w - size) / 2 if align == 'center' else x
    disc = accent if brand.tokens['card']['style'] != 'outline' else draw.mix(ground, accent, .2)
    items.append({'type': 'ellipse', 'x': left, 'y': y, 'w': size, 'h': size, 'fill': disc,
                  **({'stroke': accent, 'stroke_width': 2} if brand.tokens['card']['style'] == 'outline' else {})})
    if glyph:
        items.append({'type': 'icon', 'glyph': glyph, 'x': left + size * .24, 'y': y + size * .24, 'size': size * .52, 'color': brand.ink_for(disc) if disc == accent else accent})
    else:
        items.append(tick(left + size * .26, y + size * .26, size * .48, brand.ink_for(disc) if disc == accent else accent))
    title_item, height = text(title, brand.label(size * .36, ground, align=align), x, y + size + size * .26, w, 2, .75)
    items.append(title_item); total = size + size * .26 + height
    if detail:
        detail_item, extra = text(detail, brand.body(size * .27, ground, align=align), x, y + total + size * .14, w, 3, .8)
        items.append(detail_item); total += size * .14 + extra
    return items, total


def grid_cell(brand, src, heading, lines, x, y, w, h, quantity=1, ground=None):
    """One thing in the box: its picture over a heading and up to two short lines."""
    ground = ground or brand.ground; items = []
    heading_style = brand.label(min(26, w * .075), ground, align='center')
    line_style = brand.body(min(17, w * .05), ground, align='center')
    built = []; text_height = 0.0
    item, height = text(heading, heading_style, x + 12, 0, w - 24, 2, .7); built.append((item, height)); text_height += height
    for line in [line for line in lines if line][:2]:
        item, height = text(line, line_style, x + 18, 0, w - 36, 2, .8); built.append((item, height)); text_height += height + 7
    picture_h = h - text_height - 46; top = y + 14
    if quantity > 1:
        # Two of them: one behind and to the side of the other, as kits show a pair of batteries.
        size_w = (w - 40) * .72; size_h = picture_h * .74
        items.append(cutout(src, x + 20, top, size_w, size_h, (.5, 1)))
        items.append(cutout(src, x + w - 20 - size_w, top + picture_h - size_h, size_w, size_h, (.5, 1)))
    else:
        items.append(cutout(src, x + 26, top, w - 52, picture_h, (.5, .5 if picture_h > w else 1)))
    at = y + h - text_height - 18
    for item, height in built:
        item['y'] = at; items.append(item); at += height + 7
    return items


def step(brand, number, x, y, size, ground=None):
    ground = ground or brand.ground; accent = brand.accent_for(ground)
    style = {**brand.tokens['headline'], 'size': size * .52, 'color': brand.ink_for(accent), 'align': 'center', 'italic': False}
    return [{'type': 'ellipse', 'x': x, 'y': y, 'w': size, 'h': size, 'fill': accent},
            {'type': 'text', 'x': x, 'y': y + (size - draw.cap_height(style)) / 2, 'w': size, 'text': str(number), 'style': style}]


def arrow(x1, y1, x2, y2, ink, width=6):
    return {'type': 'line', 'points': [[x1, y1], [x2, y2]], 'color': ink, 'width': width, 'end': 'arrow', 'marker_size': width * 4}


def chevrons(x, y, h, count, ink, gap=.5, fade=True):
    items = []; w = h * .62
    for index in range(count):
        left = x + index * w * (1 + gap) * .62
        items.append({'type': 'poly', 'points': [[left, y], [left + w * .5, y], [left + w, y + h / 2], [left + w * .5, y + h], [left, y + h], [left + w * .5, y + h / 2]],
                      'fill': ink, 'opacity': (.35 + .65 * (index + 1) / count) if fade else 1})
    return items


def burst(cx, cy, radius, fill, spikes=18, depth=.84):
    points = []
    for index in range(spikes * 2):
        r = radius * (1 if index % 2 == 0 else depth); angle = math.pi * index / spikes - math.pi / 2
        points.append([cx + r * math.cos(angle), cy + r * math.sin(angle)])
    return {'type': 'poly', 'points': points, 'fill': fill}


def corner_flag(brand, big, small, ground=None, size=170):
    """A flag in the top-right corner: a count over a word, as on '30 PCS' kits."""
    ground = ground or brand.ground; accent = brand.accent_for(ground); ink = brand.ink_for(accent)
    x = 1000 - size - 36
    items = [{'type': 'poly', 'points': [[x, 0], [x + size, 0], [x + size, size * 1.05], [x + size / 2, size * .86], [x, size * 1.05]], 'fill': accent,
              'shadow': {'dy': 6, 'blur': 10, 'opacity': .25}}]
    number = {**brand.tokens['headline'], 'size': size * .4, 'color': ink, 'align': 'center', 'italic': False}
    word = {**brand.tokens['label'], 'size': size * .15, 'color': ink, 'align': 'center', 'tracking': .12}
    item, height = text(big, number, x + 8, size * .16, size - 16, 1, .5)
    items += [item, {'type': 'text', 'x': x, 'y': size * .16 + height + size * .09, 'w': size, 'text': small, 'style': word}]
    return items


def rule(x, y, w, ink, weight=2):
    return {'type': 'rect', 'x': x, 'y': y, 'w': w, 'h': weight, 'fill': ink}

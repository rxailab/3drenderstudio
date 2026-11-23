"""The sizes the user stated, and where each is drawn on the dimensions page. Split out of ``brandkit_set``, which
still re-exports every name.
"""

import re

from listing_copy import _clip


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
        # "1,200 mm": the thousands separator is part of the number (it was read as 200 mm)
        for number, unit in re.findall(r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|"|毫米|厘米|公分)(?![a-z])', text.lower()):
            found.append((float(number.replace(',', '')) * LENGTH_MM[unit], number.replace(',', ''),
                          {'inches': 'in', 'inch': 'in', '"': 'in'}.get(unit, unit)))
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

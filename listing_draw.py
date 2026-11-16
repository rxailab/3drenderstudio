"""Draws a listing page from its items: the renderer behind the element library.

A page is a list of positioned items in page units (the page is 1000 units wide, whatever its
pixel size). This module measures and wraps text with the real fonts, and paints items with
Pillow at twice the output size so edges come out smooth. The output is the PNG the user
downloads, so there is no second renderer to disagree with it.

Item types: rect, poly, ellipse, line, text, image, icon, texture, dimension.
Everything richer (badges, callouts, tables, chips) is assembled from these in
``listing_elements``.
"""
import math
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / 'static' / 'vendor' / 'fonts'
UNITS = 1000.0                  # page width in page units
SUPERSAMPLE = 2
MEASURE_PX = 4.0                # text is measured at four pixels per unit, whatever the output size
TYPES = ('rect', 'poly', 'ellipse', 'line', 'text', 'image', 'icon', 'texture', 'dimension')
FACES = {            # family: (upright file, italic file or None, variation axes in file order, cap height in ems)
    'archivo': ('archivo/archivo-latin-wdth-normal.woff2', 'archivo/archivo-latin-wdth-italic.woff2', ('weight', 'width'), .686),
    'orbitron': ('orbitron/orbitron-latin-wght-normal.woff2', None, ('weight',), .72),
    'inter': ('inter/inter-latin.woff2', None, ('weight',), .727),
}
AXES = {'archivo': {'weight': (100, 900), 'width': (62, 125)}, 'orbitron': {'weight': (400, 900)}, 'inter': {'weight': (100, 900)}}
CJK_FILE = 'noto-sans-sc/noto-sans-sc-900.woff2'
CJK = re.compile(r'[⺀-鿿豈-﫿＀-￯　-〿]')
MARKERS = ('dot', 'ring', 'square', 'arrow', 'bar', 'diamond')
TEXTURES = ('dots', 'grid', 'diagonal', 'carbon', 'glow', 'vignette', 'halftone')


def color(value, alpha=1.0):
    """'#rrggbb' or '#rgb' to an RGBA tuple."""
    value = value.lstrip('#')
    if len(value) == 3:
        value = ''.join(char * 2 for char in value)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), max(0, min(255, round(alpha * 255))))


def luminance(value):
    r, g, b, _ = color(value)
    channel = lambda c: c / 255 / 12.92 if c / 255 <= .04045 else ((c / 255 + .055) / 1.055) ** 2.4
    return .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b)


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + .05) / (low + .05)


def mix(a, b, share):
    """A colour between two: ``share`` of b in a."""
    ca, cb = color(a), color(b)
    return '#%02x%02x%02x' % tuple(round(ca[i] + (cb[i] - ca[i]) * share) for i in range(3))


def on(ground, dark='#111111', light='#ffffff'):
    """Ink that reads on this ground."""
    return dark if contrast(dark, ground) >= contrast(light, ground) else light


# ---- fonts and text ----

@lru_cache(maxsize=512)
def _font(family, pixels, weight, width, italic):
    upright, slanted, axes, _ = FACES[family]
    face = ImageFont.truetype(str(FONTS / (slanted if italic and slanted else upright)), max(1, pixels))
    limits = AXES[family]
    values = {'weight': max(limits['weight'][0], min(limits['weight'][1], weight)),
              'width': max(limits.get('width', (100, 100))[0], min(limits.get('width', (100, 100))[1], width))}
    try:
        face.set_variation_by_axes([values[axis] for axis in axes])
    except (OSError, AttributeError):
        pass
    return face


@lru_cache(maxsize=64)
def _cjk_font(pixels):
    return ImageFont.truetype(str(FONTS / CJK_FILE), max(1, pixels))


def normal(style):
    """A text style with every field present."""
    family = style.get('font', 'inter')
    if family not in FACES:
        family = 'inter'
    return {'font': family, 'weight': int(style.get('weight', 600)), 'width': int(style.get('width', 100)), 'italic': bool(style.get('italic')),
            'size': float(style.get('size', 24)), 'color': style.get('color', '#111111'), 'case': style.get('case', 'none'),
            'tracking': float(style.get('tracking', 0)), 'leading': float(style.get('leading', 1.16)), 'align': style.get('align', 'left'),
            'accent': style.get('accent'), 'outline': style.get('outline'), 'shadow': style.get('shadow'), 'gradient': style.get('gradient'),
            'opacity': float(style.get('opacity', 1))}


def cap_height(style):
    return FACES[normal(style)['font']][3] * float(style.get('size', 24))


def cased(text, case):
    return text.upper() if case == 'upper' else text.title() if case == 'title' else text


@lru_cache(maxsize=4096)
def _has_glyph(family, char):
    """Whether this family draws the character itself. A display face covers little beyond basic Latin."""
    face = _font(family, 32, 400, 100, False)
    try:
        return bytes(face.getmask(char)) != bytes(face.getmask('\uffff')) or char == '\uffff'
    except (OSError, ValueError):
        return True


def _runs(text, family='inter'):
    """Split into stretches drawn with one font: [(text, 'cjk' | 'plain' | '')], '' being the style's own face."""
    runs = []
    for char in text:
        kind = 'cjk' if CJK.match(char) else 'plain' if ord(char) > 126 and family != 'inter' and not _has_glyph(family, char) else ''
        if char == ' ' and runs:
            kind = runs[-1][1] if runs[-1][1] != 'cjk' else ''
        if runs and runs[-1][1] == kind:
            runs[-1][0] += char
        else:
            runs.append([char, kind])
    return [(part, kind) for part, kind in runs]


def _face(style, size, kind):
    if kind == 'cjk':
        return _cjk_font(size)
    if kind == 'plain':
        return _font('inter', size, style['weight'], 100, False)
    return _font(style['font'], size, style['weight'], style['width'], style['italic'])


def _advance(text, style, pixels_per_unit):
    """Width in pixels of one stretch of text at this scale, tracking included."""
    size = max(1, round(style['size'] * pixels_per_unit)); total = 0.0
    for part, kind in _runs(text, style['font']):
        face = _face(style, size, kind)
        if style['tracking']:
            total += sum(face.getlength(char) for char in part) + style['tracking'] * size * len(part)
        else:
            total += face.getlength(part)
    return total


def text_width(text, style):
    """Width in page units of text on one line, in its final case. ``{braces}`` mark accent words and take no room."""
    style = normal(style)
    return _advance(cased(text.replace('{', '').replace('}', ''), style['case']), style, MEASURE_PX) / MEASURE_PX


def _marked(text):
    """[(text, accented)] from ``plain {accented} plain``."""
    parts = []; accented = False
    for piece in re.split(r'([{}])', text):
        if piece == '{':
            accented = True
        elif piece == '}':
            accented = False
        elif piece:
            parts.append((piece, accented))
    return parts


def wrap(text, style, limit):
    """Lines that fit ``limit`` units: each a list of (text, accented) runs. Breaks at spaces, and anywhere in Chinese."""
    style = normal(style); lines = []
    for paragraph in str(text).split('\n'):
        tokens = []                               # (word, accented, glue before it)
        for piece, accented in _marked(cased(paragraph, style['case'])):
            for word in re.findall(r'\s+|[^\s⺀-鿿＀-￯　-〿]+|[⺀-鿿＀-￯　-〿]', piece):
                tokens.append((word, accented))
        line = []; width = 0.0
        for word, accented in tokens:
            if word.isspace():
                if line:
                    line.append((' ', accented)); width += text_width(' ', style)
                continue
            w = text_width(word, {**style, 'case': 'none'})
            if line and width + w > limit + .01:
                while line and line[-1][0] == ' ':
                    line.pop()
                lines.append(line); line = []; width = 0.0
            line.append((word, accented)); width += w
        while line and line[-1][0] == ' ':
            line.pop()
        lines.append(line)
    merged = []
    for line in lines:
        out = []
        for word, accented in line:
            if out and out[-1][1] == accented:
                out[-1] = (out[-1][0] + word, accented)
            else:
                out.append((word, accented))
        merged.append(out)
    return merged


def line_width(line, style):
    style = {**normal(style), 'case': 'none'}
    return sum(text_width(part, style) for part, _ in line)


def block_size(lines, style):
    """(width, height) in units of wrapped lines: from the top of the first line's capitals to the last baseline, plus descenders."""
    style = normal(style); count = max(1, len(lines))
    height = cap_height(style) + (count - 1) * style['size'] * style['leading']
    if style['case'] != 'upper':
        height += style['size'] * .2
    return max((line_width(line, style) for line in lines), default=0.0), height


def fit(text, style, limit, max_lines=2, smallest=.6):
    """Shrink the type until the text takes at most ``max_lines`` lines. Returns (style, lines)."""
    style = dict(style); start = float(style.get('size', 24))
    for step in range(24):
        style['size'] = start * (1 - step * (1 - smallest) / 23)
        lines = wrap(text, style, limit)
        if len(lines) <= max_lines and all(line_width(line, style) <= limit + .5 for line in lines):
            return style, lines
    return style, wrap(text, style, limit)          # never cut words off: the caller gets every line and makes room


# ---- dimension lines ----

def dimension_parts(item):
    """A dimension as plain lines and a label: the measured span moved clear of the object, with witness lines back to it."""
    (x1, y1), (x2, y2) = item['p1'], item['p2']; length = math.hypot(x2 - x1, y2 - y1) or 1.0
    nx, ny = -(y2 - y1) / length, (x2 - x1) / length; offset = float(item.get('offset', 30))
    a = (x1 + nx * offset, y1 + ny * offset); b = (x2 + nx * offset, y2 + ny * offset)
    past = 8 if offset >= 0 else -8; gap = 6 if offset >= 0 else -6
    lines = [{'points': [a, b], 'start': item.get('ends', 'arrow'), 'end': item.get('ends', 'arrow')}]
    if abs(offset) > 10:
        for (px, py), (qx, qy) in (((x1, y1), a), ((x2, y2), b)):
            lines.append({'points': [(px + nx * gap, py + ny * gap), (qx + nx * past, qy + ny * past)], 'thin': True})
    share = float(item.get('label_at', .5))                 # where along the span the label sits
    middle = (a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share)
    return lines, middle, (nx, ny)


# ---- painting ----

class Painter:
    def __init__(self, width, height, background='#ffffff', supersample=SUPERSAMPLE):
        self.width = width; self.height = height; self.ss = supersample
        self.scale = width * supersample / UNITS
        self.canvas = Image.new('RGBA', (width * supersample, height * supersample), color(background))
        self.sources = {}

    def px(self, value):
        return value * self.scale

    def _layer(self, box):
        """A transparent layer covering this unit box (clipped to the page), its drawing context and its pixel origin."""
        left = max(0, math.floor(self.px(box[0])) - 2); top = max(0, math.floor(self.px(box[1])) - 2)
        right = min(self.canvas.width, math.ceil(self.px(box[2])) + 2); bottom = min(self.canvas.height, math.ceil(self.px(box[3])) + 2)
        if right <= left or bottom <= top:
            return None, None, (0, 0)
        layer = Image.new('RGBA', (right - left, bottom - top), (0, 0, 0, 0))
        return layer, ImageDraw.Draw(layer), (left, top)

    def _put(self, layer, origin, opacity=1.0):
        if layer is None:
            return
        if opacity < 1:
            layer.putalpha(layer.getchannel('A').point(lambda value: round(value * opacity)))
        self.canvas.alpha_composite(layer, origin)

    def _shadow(self, mask, origin, shadow):
        """A blurred copy of a shape's coverage, laid under it."""
        blur = self.px(float(shadow.get('blur', 14))); dx = round(self.px(float(shadow.get('dx', 0)))); dy = round(self.px(float(shadow.get('dy', 8))))
        pad = math.ceil(blur * 3)
        grown = Image.new('L', (mask.width + pad * 2, mask.height + pad * 2), 0); grown.paste(mask, (pad, pad))
        grown = grown.filter(ImageFilter.GaussianBlur(blur)).point(lambda value: round(value * float(shadow.get('opacity', .28))))
        tint = Image.new('RGBA', grown.size, color(shadow.get('color', '#000000'))); tint.putalpha(grown)
        x = origin[0] - pad + dx; y = origin[1] - pad + dy
        crop = (max(0, -x), max(0, -y), min(tint.width, self.canvas.width - x), min(tint.height, self.canvas.height - y))
        if crop[2] > crop[0] and crop[3] > crop[1]:
            self.canvas.alpha_composite(tint.crop(crop), (max(0, x), max(0, y)))

    # -- shapes --
    def rect(self, item):
        x, y, w, h = (float(item[key]) for key in ('x', 'y', 'w', 'h'))
        if w <= 0 or h <= 0:
            return
        layer, draw, origin = self._layer((x, y, x + w, y + h))
        if layer is None:
            return
        left = self.px(x) - origin[0]; top = self.px(y) - origin[1]
        box = (left, top, max(left, self.px(x + w) - origin[0] - 1), max(top, self.px(y + h) - origin[1] - 1))       # never inside out, however small
        radius = min(self.px(float(item.get('radius', 0))), (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
        stroke = max(1, round(self.px(float(item.get('stroke_width', 2))))) if item.get('stroke') else 0
        fill = color(item['fill']) if item.get('fill') else None
        draw.rounded_rectangle(box, radius, fill=fill, outline=color(item['stroke']) if stroke else None, width=stroke)
        if item.get('gradient') and fill:
            mask = layer.getchannel('A'); to = color(item['gradient']['to'])
            ramp = Image.linear_gradient('L')
            ramp = ramp.resize(layer.size) if item['gradient'].get('angle', 'v') == 'v' else ramp.rotate(90).resize(layer.size)
            blend = Image.composite(Image.new('RGBA', layer.size, to), Image.new('RGBA', layer.size, fill), ramp)
            blend.putalpha(mask); layer = blend
        if item.get('shadow'):
            self._shadow(layer.getchannel('A'), origin, item['shadow'])
        self._put(layer, origin, float(item.get('opacity', 1)))

    def poly(self, item):
        points = [(float(px), float(py)) for px, py in item['points']]
        if len(points) < 3:
            return
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        layer, draw, origin = self._layer((min(xs), min(ys), max(xs), max(ys)))
        if layer is None:
            return
        shape = [(self.px(px) - origin[0], self.px(py) - origin[1]) for px, py in points]
        draw.polygon(shape, fill=color(item['fill']) if item.get('fill') else None)
        if item.get('stroke'):
            draw.line(shape + [shape[0]], fill=color(item['stroke']), width=max(1, round(self.px(float(item.get('stroke_width', 2))))), joint='curve')
        if item.get('shadow'):
            self._shadow(layer.getchannel('A'), origin, item['shadow'])
        self._put(layer, origin, float(item.get('opacity', 1)))

    def ellipse(self, item):
        x, y, w, h = (float(item[key]) for key in ('x', 'y', 'w', 'h'))
        grow = float(item.get('stroke_width', 2)) if item.get('stroke') else 0
        layer, draw, origin = self._layer((x - grow, y - grow, x + w + grow, y + h + grow))
        if layer is None:
            return
        box = (self.px(x) - origin[0], self.px(y) - origin[1], self.px(x + w) - origin[0], self.px(y + h) - origin[1])
        stroke = max(1, round(self.px(grow))) if item.get('stroke') else 0
        if item.get('dash') and stroke:
            on_len, off_len = item['dash']; around = math.pi * (3 * (w + h) / 2 - math.sqrt((3 * w / 2 + h / 2) * (w / 2 + 3 * h / 2)))
            count = max(8, round(around / (on_len + off_len))); share = on_len / (on_len + off_len)
            if item.get('fill'):
                draw.ellipse(box, fill=color(item['fill']))
            for index in range(count):
                draw.arc(box, 360 * index / count, 360 * (index + share) / count, fill=color(item['stroke']), width=stroke)
        else:
            draw.ellipse(box, fill=color(item['fill']) if item.get('fill') else None, outline=color(item['stroke']) if stroke else None, width=stroke)
        if item.get('shadow'):
            self._shadow(layer.getchannel('A'), origin, item['shadow'])
        self._put(layer, origin, float(item.get('opacity', 1)))

    def _marker(self, draw, origin, kind, point, direction, size, ink, ground=None):
        x = self.px(point[0]) - origin[0]; y = self.px(point[1]) - origin[1]; r = self.px(size) / 2
        dx, dy = direction
        if kind == 'dot':
            draw.ellipse((x - r, y - r, x + r, y + r), fill=ink)
        elif kind == 'ring':
            draw.ellipse((x - r, y - r, x + r, y + r), fill=ground or (255, 255, 255, 255), outline=ink, width=max(1, round(r * .45)))
        elif kind == 'square':
            draw.rectangle((x - r, y - r, x + r, y + r), fill=ink)
        elif kind == 'diamond':
            draw.polygon([(x, y - r * 1.2), (x + r * 1.2, y), (x, y + r * 1.2), (x - r * 1.2, y)], fill=ink)
        elif kind == 'bar':
            draw.line([(x - dy * r * 1.4, y + dx * r * 1.4), (x + dy * r * 1.4, y - dx * r * 1.4)], fill=ink, width=max(1, round(r * .5)))
        elif kind == 'arrow':
            back = (x - dx * r * 2.6, y - dy * r * 2.6)
            draw.polygon([(x, y), (back[0] - dy * r, back[1] + dx * r), (back[0] + dy * r, back[1] - dx * r)], fill=ink)

    def line(self, item):
        points = [(float(px), float(py)) for px, py in item['points']]
        if len(points) < 2:
            return
        width = float(item.get('width', 2)); marker = float(item.get('marker_size', max(8, width * 4)))
        grow = marker * 1.6 + width
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        layer, draw, origin = self._layer((min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow))
        if layer is None:
            return
        ink = color(item.get('color', '#111111')); stroke = max(1, round(self.px(width)))
        path = [(self.px(px) - origin[0], self.px(py) - origin[1]) for px, py in points]
        if item.get('dash'):
            on_len, off_len = (self.px(float(value)) for value in item['dash'])
            for (ax, ay), (bx, by) in zip(path, path[1:]):
                length = math.hypot(bx - ax, by - ay)
                if length <= 0:
                    continue
                ux, uy = (bx - ax) / length, (by - ay) / length; at = 0.0
                while at < length:
                    end = min(length, at + on_len)
                    draw.line([(ax + ux * at, ay + uy * at), (ax + ux * end, ay + uy * end)], fill=ink, width=stroke)
                    at += on_len + off_len
        else:
            draw.line(path, fill=ink, width=stroke, joint='curve')
            if item.get('round', True) and stroke > 2:
                for px, py in path:
                    draw.ellipse((px - stroke / 2, py - stroke / 2, px + stroke / 2, py + stroke / 2), fill=ink)
        ground = color(item['ground']) if item.get('ground') else None
        for kind, at, towards in ((item.get('start'), points[0], points[1]), (item.get('end'), points[-1], points[-2])):
            if kind in MARKERS:
                length = math.hypot(at[0] - towards[0], at[1] - towards[1]) or 1.0
                self._marker(draw, origin, kind, at, ((at[0] - towards[0]) / length, (at[1] - towards[1]) / length), marker, ink, ground)
        self._put(layer, origin, float(item.get('opacity', 1)))

    # -- text --
    def _draw_line(self, draw, x, baseline, line, style, fill, accent, stroke=0, stroke_fill=None):
        size = max(1, round(self.px(style['size'])))
        for part, accented in line:
            ink = accent if accented and accent else fill
            for piece, kind in _runs(part, style['font']):
                face = _face(style, size, kind)
                if style['tracking']:
                    for char in piece:
                        draw.text((x, baseline), char, font=face, fill=ink, anchor='ls', stroke_width=stroke, stroke_fill=stroke_fill)
                        x += face.getlength(char) + style['tracking'] * size
                else:
                    draw.text((x, baseline), piece, font=face, fill=ink, anchor='ls', stroke_width=stroke, stroke_fill=stroke_fill)
                    x += face.getlength(piece)
        return x

    def text(self, item):
        style = normal(item.get('style') or {})
        lines = item.get('lines')
        if lines is None:
            lines = wrap(item.get('text', ''), style, float(item.get('w', UNITS)))
        if not lines:
            return
        x = float(item['x']); y = float(item['y']); width = float(item.get('w') or max(line_width(line, style) for line in lines))
        cap = cap_height(style); height = cap + (len(lines) - 1) * style['size'] * style['leading'] + style['size'] * .3
        outline = style['outline'] or {}; shadow = style['shadow'] or {}
        grow = style['size'] * .25 + float(outline.get('width', 0)) + abs(float(shadow.get('dx', 0))) + abs(float(shadow.get('dy', 0))) + float(shadow.get('blur', 0)) * 3
        layer, draw, origin = self._layer((x - grow, y - style['size'] * .3 - grow, x + width + grow, y + height + grow))
        if layer is None:
            return
        fill = color(style['color']); accent = color(style['accent']) if style['accent'] else None
        stroke = max(1, round(self.px(float(outline['width'])))) if outline.get('width') else 0
        stroke_fill = color(outline.get('color', '#000000')) if stroke else None
        starts = []
        for index, line in enumerate(lines):
            measured = sum(_advance(part, style, self.scale) for part, _ in line)
            left = self.px(x) - origin[0]
            if style['align'] == 'center':
                left += (self.px(width) - measured) / 2
            elif style['align'] == 'right':
                left += self.px(width) - measured
            starts.append((left, self.px(y + cap + index * style['size'] * style['leading']) - origin[1]))
        if shadow:
            under = Image.new('RGBA', layer.size, (0, 0, 0, 0)); under_draw = ImageDraw.Draw(under)
            tint = color(shadow.get('color', '#000000'), float(shadow.get('opacity', .5)))
            dx = self.px(float(shadow.get('dx', 0))); dy = self.px(float(shadow.get('dy', 4)))
            for (left, baseline), line in zip(starts, lines):
                self._draw_line(under_draw, left + dx, baseline + dy, line, style, tint, tint, stroke, tint)
            if shadow.get('blur'):
                under = under.filter(ImageFilter.GaussianBlur(self.px(float(shadow['blur']))))
            layer.alpha_composite(under)
        for (left, baseline), line in zip(starts, lines):
            self._draw_line(draw, left, baseline, line, style, fill, accent, stroke, stroke_fill)
        if style['gradient']:
            # Recolour the letters top to bottom, keeping their coverage (and any outline as drawn).
            letters = Image.new('RGBA', layer.size, (0, 0, 0, 0)); letter_draw = ImageDraw.Draw(letters)
            for (left, baseline), line in zip(starts, lines):
                self._draw_line(letter_draw, left, baseline, line, style, (255, 255, 255, 255), (255, 255, 255, 255))
            top, bottom = (color(value) for value in style['gradient'])
            ramp = Image.linear_gradient('L').resize(layer.size)
            blend = Image.composite(Image.new('RGBA', layer.size, bottom), Image.new('RGBA', layer.size, top), ramp)
            blend.putalpha(letters.getchannel('A')); layer.alpha_composite(blend)
        self._put(layer, origin, style['opacity'])

    # -- pictures --
    def _source(self, src):
        if src not in self.sources:
            with Image.open(src) as opened:
                self.sources[src] = opened.convert('RGBA')
        return self.sources[src]

    def image(self, item):
        x, y, w, h = (float(item[key]) for key in ('x', 'y', 'w', 'h'))
        if w <= 0 or h <= 0:
            return
        picture = self._source(item['src'])
        if item.get('crop'):
            cx0, cy0, cx1, cy1 = item['crop']
            picture = picture.crop((round(cx0 * picture.width), round(cy0 * picture.height), round(cx1 * picture.width), round(cy1 * picture.height)))
        if item.get('flip'):
            picture = picture.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        box_w = max(1, round(self.px(w))); box_h = max(1, round(self.px(h)))
        ratio = picture.width / picture.height
        if item.get('fit', 'contain') == 'cover':
            scale = max(box_w / picture.width, box_h / picture.height)
        else:
            scale = min(box_w / picture.width, box_h / picture.height)
        size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
        scaled = picture.resize(size, Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BICUBIC)
        ax, ay = item.get('anchor', (.5, .5))
        if item.get('fit', 'contain') == 'cover':
            left = round((size[0] - box_w) * ax); top = round((size[1] - box_h) * ay)
            scaled = scaled.crop((left, top, left + box_w, top + box_h)); offset = (0, 0)
        else:
            offset = (round((box_w - size[0]) * ax), round((box_h - size[1]) * ay))
        layer = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 0)); layer.alpha_composite(scaled, offset)
        mask_kind = item.get('mask')
        if mask_kind in ('circle', 'round'):
            mask = Image.new('L', layer.size, 0); mask_draw = ImageDraw.Draw(mask)
            if mask_kind == 'circle':
                mask_draw.ellipse((0, 0, box_w - 1, box_h - 1), fill=255)
            else:
                mask_draw.rounded_rectangle((0, 0, box_w - 1, box_h - 1), self.px(float(item.get('radius', 16))), fill=255)
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask))
        origin = (round(self.px(x)), round(self.px(y)))
        shadow = item.get('shadow')
        if shadow and shadow.get('kind') == 'ground':
            # What the object stands on: the lower part of its outline laid flat under it, low and soft.
            alpha = layer.getchannel('A'); bounds = alpha.getbbox()
            if bounds:
                shape = alpha.crop(bounds); foot = shape.crop((0, round(shape.height * .58), shape.width, shape.height))
                flat = max(2, round(shape.height * float(shadow.get('squash', .1))))
                lying = foot.resize((shape.width, flat), Image.Resampling.BILINEAR)
                self._shadow(lying, (origin[0] + bounds[0], origin[1] + bounds[3] - round(flat * .78)),
                             {'blur': shadow.get('blur', 8), 'dy': 0, 'opacity': shadow.get('opacity', .26), 'color': shadow.get('color', '#000000')})
        elif shadow:
            self._shadow(layer.getchannel('A'), origin, shadow)
        if item.get('reflection'):
            self._reflection(layer, origin, item['reflection'])
        crop = (max(0, -origin[0]), max(0, -origin[1]), min(layer.width, self.canvas.width - origin[0]), min(layer.height, self.canvas.height - origin[1]))
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            return
        opacity = float(item.get('opacity', 1)); piece = layer.crop(crop)
        if opacity < 1:
            piece.putalpha(piece.getchannel('A').point(lambda value: round(value * opacity)))
        self.canvas.alpha_composite(piece, (max(0, origin[0]), max(0, origin[1])))
        if item.get('stroke'):
            ring = {'x': x, 'y': y, 'w': w, 'h': h, 'stroke': item['stroke'], 'stroke_width': item.get('stroke_width', 4)}
            if mask_kind == 'circle':
                self.ellipse(ring)
            else:
                self.rect({**ring, 'radius': item.get('radius', 0) if mask_kind == 'round' else 0})

    def _reflection(self, layer, origin, settings):
        """The cut-out mirrored down from where each part of it meets the floor, fading with distance (as blender/reflection.py does for renders).

        Done in narrow vertical strips, each flipped about its own lowest point, so the reflection meets an outline that is
        not level (a box seen from above, a tool on a diagonal) all along it.
        """
        alpha = layer.getchannel('A'); bounds = alpha.point(lambda value: 255 if value > 200 else 0).getbbox()
        if not bounds:
            return
        opacity = float(settings.get('opacity', .32)); share = float(settings.get('length', .45))
        strip = max(2, round(layer.width / 260)); height = bounds[3] - bounds[1]
        reach = max(2, round(height * share))
        mirror = Image.new('RGBA', (layer.width, layer.height + reach), (0, 0, 0, 0))
        solid = alpha.point(lambda value: 255 if value > 200 else 0)
        for left in range(bounds[0], bounds[2], strip):
            right = min(bounds[2], left + strip)
            column = solid.crop((left, 0, right, layer.height)).getbbox()
            if not column:
                continue
            lowest = column[3]; length = max(1, min(reach, round((column[3] - column[1]) * share)))
            source = layer.crop((left, max(0, lowest - length), right, lowest)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            mirror.alpha_composite(source, (left, lowest))
        # Fade: strongest where it meets the product, gone by the end of its reach.
        ramp = Image.linear_gradient('L').resize((1, reach)).point(lambda value: round((1 - value / 255) ** 1.6 * 255 * opacity))
        fade = Image.new('L', mirror.size, 0)
        for left in range(bounds[0], bounds[2], strip):
            right = min(bounds[2], left + strip)
            column = solid.crop((left, 0, right, layer.height)).getbbox()
            if column:
                fade.paste(ramp.resize((right - left, reach)), (left, column[3]))
        mirror.putalpha(ImageChops.multiply(mirror.getchannel('A'), fade))
        mirror = mirror.filter(ImageFilter.GaussianBlur(max(.6, layer.width / 1400)))
        crop = (max(0, -origin[0]), max(0, -origin[1]), min(mirror.width, self.canvas.width - origin[0]), min(mirror.height, self.canvas.height - origin[1]))
        if crop[2] > crop[0] and crop[3] > crop[1]:
            self.canvas.alpha_composite(mirror.crop(crop), (max(0, origin[0]), max(0, origin[1])))

    def icon(self, item):
        import layer_icons
        glyph = item['glyph']; size = float(item['size']); x = float(item['x']); y = float(item['y'])
        layer, draw, origin = self._layer((x, y, x + size, y + size))
        if layer is None:
            return
        unit = self.px(size) / float(glyph.get('grid', 24)); ink = color(item.get('color', '#111111'))
        width = max(1, round(float(item.get('stroke_width') or glyph.get('stroke', 2)) * unit))
        ox = self.px(x) - origin[0]; oy = self.px(y) - origin[1]
        for mode, d in glyph['ops']:
            try:
                lines = layer_icons.flatten(d)
            except (ValueError, IndexError, TypeError, ZeroDivisionError):
                continue
            if mode in ('f', 'e'):
                mask = Image.new('1', layer.size, 0)
                for line in lines:
                    if len(line) > 2:
                        shape = Image.new('1', layer.size, 0)
                        ImageDraw.Draw(shape).polygon([(ox + px * unit, oy + py * unit) for px, py in line], fill=1)
                        mask = ImageChops.logical_xor(mask, shape)
                layer.paste(ink, mask=mask)
                continue
            for line in lines:
                points = [(ox + px * unit, oy + py * unit) for px, py in line]
                if len(points) > 1:
                    draw.line(points, fill=ink, width=width, joint='curve')
                    for px, py in (points[0], points[-1]):
                        draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=ink)
        self._put(layer, origin, float(item.get('opacity', 1)))

    def texture(self, item):
        x, y, w, h = (float(item[key]) for key in ('x', 'y', 'w', 'h')); kind = item.get('kind', 'dots')
        layer, draw, origin = self._layer((x, y, x + w, y + h))
        if layer is None:
            return
        ink = color(item.get('color', '#000000')); step = max(2.0, self.px(float(item.get('step', 16))))
        left = self.px(x) - origin[0]; top = self.px(y) - origin[1]; right = self.px(x + w) - origin[0]; bottom = self.px(y + h) - origin[1]
        if kind in ('glow', 'vignette'):
            radial = Image.radial_gradient('L').resize((max(1, round(right - left)), max(1, round(bottom - top))))
            alpha = radial if kind == 'vignette' else ImageChops.invert(radial)
            alpha = alpha.point(lambda value: round((value / 255) ** float(item.get('falloff', 1.6)) * 255))
            tint = Image.new('RGBA', alpha.size, ink); tint.putalpha(alpha)
            layer.alpha_composite(tint, (round(left), round(top)))
        elif kind == 'dots' or kind == 'halftone':
            radius = max(1.0, step * float(item.get('size', .12))); rows = int((bottom - top) / step) + 2; columns = int((right - left) / step) + 2
            for row in range(rows):
                for column in range(columns):
                    cx = left + column * step + (step / 2 if kind == 'halftone' and row % 2 else 0); cy = top + row * step
                    r = radius * (1 - row / rows) if kind == 'halftone' else radius
                    if r >= .5:
                        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ink)
        elif kind == 'grid':
            line = max(1, round(self.px(float(item.get('size', 1)))))
            at = left
            while at <= right:
                draw.line([(at, top), (at, bottom)], fill=ink, width=line); at += step
            at = top
            while at <= bottom:
                draw.line([(left, at), (right, at)], fill=ink, width=line); at += step
        elif kind == 'diagonal':
            line = max(1, round(step * float(item.get('size', .4)))); span = bottom - top; at = left - span
            while at <= right:
                draw.line([(at, bottom), (at + span, top)], fill=ink, width=line); at += step
        elif kind == 'carbon':
            cell = step / 2; rows = int((bottom - top) / cell) + 2; columns = int((right - left) / cell) + 2
            for row in range(rows):
                for column in range(columns):
                    if (row + column) % 2 == 0:
                        draw.rectangle((left + column * cell, top + row * cell, left + (column + .82) * cell, top + (row + .82) * cell), fill=ink)
        clip = Image.new('L', layer.size, 0)
        ImageDraw.Draw(clip).rectangle((left, top, right, bottom), fill=255)
        layer.putalpha(ImageChops.multiply(layer.getchannel('A'), clip))
        self._put(layer, origin, float(item.get('opacity', 1)))

    def dimension(self, item):
        ink = item.get('color', '#111111'); width = float(item.get('width', 2.5))
        lines, middle, normal_vector = dimension_parts(item)
        for line in lines:
            self.line({'points': line['points'], 'color': ink, 'width': width * (.6 if line.get('thin') else 1), 'start': line.get('start'), 'end': line.get('end'),
                       'marker_size': item.get('marker_size', 12), 'round': False})
        label = item.get('label')
        if label:
            style = normal({'font': 'archivo', 'weight': 700, 'size': 26, 'color': ink, **(item.get('style') or {})})
            w = text_width(label, style); cap = cap_height(style); pad = style['size'] * .32
            side = 1 if float(item.get('offset', 30)) >= 0 else -1; away = cap / 2 + pad + 6
            cx = middle[0] + normal_vector[0] * away * side; cy = middle[1] + normal_vector[1] * away * side
            if item.get('plate'):
                self.rect({'x': cx - w / 2 - pad, 'y': cy - cap / 2 - pad, 'w': w + pad * 2, 'h': cap + pad * 2, 'fill': item['plate'], 'radius': pad})
            self.text({'x': cx - w / 2, 'y': cy - cap / 2, 'w': w, 'lines': [[(cased(label, style['case']), False)]], 'style': {**style, 'case': 'none'}})

    def draw(self, items):
        for item in items:
            kind = item.get('type')
            if kind in TYPES:
                getattr(self, kind)(item)

    def result(self):
        return self.canvas.resize((self.width, self.height), Image.Resampling.LANCZOS) if self.ss != 1 else self.canvas


def render(page, pixels=2000, supersample=SUPERSAMPLE):
    """The page as an RGB picture ``pixels`` wide. ``page`` holds 'height' (units), 'background' and 'items'."""
    height = round(pixels * float(page.get('height', UNITS)) / UNITS)
    painter = Painter(pixels, height, page.get('background', '#ffffff'), supersample)
    painter.draw(page.get('items', []))
    return painter.result().convert('RGB')

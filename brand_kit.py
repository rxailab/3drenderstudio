"""The brand kit: one look carried across every image of a listing.

A listing keeps one accent colour, one headline treatment, one way of drawing leader lines and
one frame from its first image to its last. That is stored once per project as the brand and
read by every page template. The five presets are the families found in the example listings:

* ``frame``  dark header and footer bands around a white body, wide heavy capitals, right-angle
             accent leader lines with square ends, no icons; the big tool brands.
* ``flat``   a flat brand-colour ground, white type of normal width, rounded cards and pills.
* ``tech``   a charcoal ground with a glow and a fine grid, a square display face, thin accent
             lines with ring ends, outlined panels and icons.
* ``clean``  white, a heavy black headline with one phrase in the accent colour, thin grey
             lines with dots, soft grey cards.
* ``promo``  heavy condensed italic capitals, slanted accent bands, outlined and shadowed
             headlines, burst badges and chevrons.

The presets describe treatments, not anyone's trade dress: no logos, names or marks are built
in. A brand name, badge texts, a legal line and a logo are the user's own.
"""
import colorsys
import json
import re

from PIL import Image

import brandkit_assets
import listing_draw as draw

PRESETS = {
    # The default preset draws the templates as they were designed (page_style sets no bands, type or corners of its own):
    # its name and note say that, not the band frame its tokens describe for other uses.
    'frame': {'title': ('经典版式', 'Classic'), 'ground': 'light', 'header': 'band', 'footer': 'band',
              'headline': {'font': 'archivo', 'weight': 900, 'width': 125, 'case': 'upper', 'leading': .98},
              'label': {'font': 'archivo', 'weight': 800, 'width': 112, 'case': 'upper', 'leading': 1.08},
              'body': {'font': 'archivo', 'weight': 500, 'width': 108, 'case': 'upper', 'leading': 1.25},
              'leader': {'route': 'elbow', 'end': 'square', 'width': 3.4, 'ink': 'accent'},
              'card': {'radius': 0, 'style': 'rules'}, 'chip': {'radius': 0, 'style': 'solid'}, 'badge': 'box', 'icons': False,
              'note': ('模板原本的设计：白底主体，粗体大标题，直角引线', 'The templates as designed: a white body, bold headlines, right-angle leader lines')},
    'flat': {'title': ('品牌色铺底', 'Flat colour'), 'ground': 'accent', 'header': 'none', 'footer': 'none',
             'headline': {'font': 'archivo', 'weight': 800, 'width': 100, 'case': 'none', 'leading': 1.04},
             'label': {'font': 'archivo', 'weight': 700, 'width': 100, 'case': 'none', 'leading': 1.14},
             'body': {'font': 'inter', 'weight': 500, 'case': 'none', 'leading': 1.32},
             'leader': {'route': 'straight', 'end': 'ring', 'width': 2.6, 'ink': 'ink'},
             'card': {'radius': 26, 'style': 'solid'}, 'chip': {'radius': 999, 'style': 'solid'}, 'badge': 'pill', 'icons': True,
             'note': ('整页品牌色，白色文字，圆角卡片与胶囊标签', 'A flat brand-colour page, white type, rounded cards and pills')},
    'tech': {'title': ('深色科技', 'Dark tech'), 'ground': 'dark', 'header': 'none', 'footer': 'line',
             'headline': {'font': 'orbitron', 'weight': 800, 'case': 'upper', 'leading': 1.08, 'tracking': .01},
             'label': {'font': 'archivo', 'weight': 700, 'width': 104, 'case': 'upper', 'leading': 1.14, 'tracking': .02},
             'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.34},
             'leader': {'route': 'angled', 'end': 'ring', 'width': 2, 'ink': 'accent'},
             'card': {'radius': 14, 'style': 'outline'}, 'chip': {'radius': 8, 'style': 'outline'}, 'badge': 'hex', 'icons': True,
             'note': ('深灰底 + 光晕与细网格，方形展示字体，描边面板与图标', 'Charcoal with a glow and a fine grid, a square display face, outlined panels and icons')},
    'clean': {'title': ('简洁白底', 'Clean white'), 'ground': 'light', 'header': 'none', 'footer': 'none',
              'headline': {'font': 'archivo', 'weight': 800, 'width': 100, 'case': 'none', 'leading': 1.04},
              'label': {'font': 'archivo', 'weight': 700, 'width': 100, 'case': 'none', 'leading': 1.14},
              'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.34},
              'leader': {'route': 'angled', 'end': 'dot', 'width': 1.8, 'ink': 'soft'},
              'card': {'radius': 20, 'style': 'soft'}, 'chip': {'radius': 999, 'style': 'soft'}, 'badge': 'pill', 'icons': True,
              'note': ('白底留白，黑色粗标题中一个词用品牌色，细灰引线', 'White space, a heavy black headline with one accent phrase, thin grey lines')},
    'promo': {'title': ('促销冲击', 'Bold promo'), 'ground': 'dark', 'header': 'slash', 'footer': 'none',
              'headline': {'font': 'archivo', 'weight': 900, 'width': 68, 'italic': True, 'case': 'upper', 'leading': .96},
              'label': {'font': 'archivo', 'weight': 800, 'width': 80, 'italic': True, 'case': 'upper', 'leading': 1.08},
              'body': {'font': 'archivo', 'weight': 500, 'width': 92, 'case': 'none', 'leading': 1.26},
              'leader': {'route': 'straight', 'end': 'arrow', 'width': 4, 'ink': 'accent'},
              'card': {'radius': 6, 'style': 'solid'}, 'chip': {'radius': 4, 'style': 'slant'}, 'badge': 'burst', 'icons': True, 'effect': 'outline',
              'note': ('窄体粗斜大写，斜切色带，描边加阴影标题，爆炸贴', 'Heavy condensed italic capitals, slanted bands, outlined headlines, burst badges')},
}
DEFAULT_PRESET = 'frame'
DEFAULT_ACCENT = '#e5322d'
DARK = '#16171a'
LIGHT = '#ffffff'
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
UNITS = ('mm', 'in')
MAX_BADGES = 3
# The common elements a brand can pick (brandkit/library/elements.json, drawn by runtime.js TPL.badge / TPL.texture):
# how its badges are drawn ('auto' lets each page choose) and a faint texture on the page ground.
ELEMENTS = json.loads((brandkit_assets.ROOT / 'library' / 'elements.json').read_text(encoding='utf-8'))
BADGE_STYLES = tuple(row['id'] for row in ELEMENTS['badge_styles'])
TEXTURES = tuple(row['id'] for row in ELEMENTS['textures'])


def _text(value, limit, label):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(label + '必须是文字')
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value).strip()
    if len(value) > limit:
        raise ValueError('%s不能超过 %d 个字符' % (label, limit))
    return value


def _color(value, label):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not HEX.match(value):
        raise ValueError(label + '必须是 #rrggbb 颜色')
    return value.lower()


def validate(value, current=None):
    """A clean brand record. Missing fields keep their current value, then the defaults."""
    if not isinstance(value, dict):
        raise ValueError('品牌设置格式无效')
    current = current or {}
    preset = value.get('preset', current.get('preset', DEFAULT_PRESET))
    if preset not in PRESETS:
        raise ValueError('未知的品牌风格')
    badges = value.get('badges', current.get('badges', []))
    if isinstance(badges, str):
        badges = [part for part in re.split(r'[\n,，;；]+', badges) if part.strip()]
    if not isinstance(badges, list) or len(badges) > MAX_BADGES:
        raise ValueError('徽标文字最多 %d 个' % MAX_BADGES)
    unit = value.get('unit', current.get('unit', 'mm'))
    if unit not in UNITS:
        raise ValueError('尺寸单位只能是 mm 或 in')
    fonts = value.get('fonts', current.get('fonts', brandkit_assets.DEFAULT_PAIRING))
    if fonts not in brandkit_assets.PAIRINGS:
        raise ValueError('未知的字体搭配')
    badge_style = value.get('badge_style', current.get('badge_style', 'auto'))
    if badge_style not in BADGE_STYLES:
        raise ValueError('未知的徽标样式')
    texture = value.get('texture', current.get('texture', 'none'))
    if texture not in TEXTURES:
        raise ValueError('未知的底纹')
    logo = value.get('logo', current.get('logo'))
    if logo is not None and (not isinstance(logo, str) or not logo.startswith('/files/') or len(logo) > 300):
        raise ValueError('标志文件无效')
    return {'preset': preset, 'name': _text(value.get('name', current.get('name', '')), 40, '品牌名称'),
            'accent': _color(value['accent'], '品牌色') if 'accent' in value else current.get('accent'),
            'dark': _color(value['dark'], '深色') if 'dark' in value else current.get('dark'),
            'badges': [_text(badge, 16, '徽标文字') for badge in badges if _text(badge, 16, '徽标文字')],
            'legal': _text(value.get('legal', current.get('legal', '')), 200, '说明文字'),
            'unit': unit, 'logo': logo, 'fonts': fonts, 'badge_style': badge_style, 'texture': texture}


def dominant_color(path):
    """The product's own colour, as an accent: the most common vivid hue of a cut-out, or None for a grey product."""
    try:
        with Image.open(path) as opened:
            small = opened.convert('RGBA').resize((72, 72), Image.Resampling.BOX)
    except (OSError, ValueError):
        return None
    bins = {}; solid = 0
    for r, g, b, a in small.getdata():
        if a < 200:
            continue
        solid += 1
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < .38 or v < .25:
            continue
        key = int(h * 24) % 24; entry = bins.setdefault(key, [0.0, 0.0, 0.0, 0.0])
        weight = s * v; entry[0] += weight; entry[1] += r * weight; entry[2] += g * weight; entry[3] += b * weight
    if not bins or not solid:
        return None
    weight, r, g, b = max(bins.values(), key=lambda entry: entry[0])
    if weight < solid * .02:
        return None
    h, s, v = colorsys.rgb_to_hsv(r / weight / 255, g / weight / 255, b / weight / 255)
    r, g, b = colorsys.hsv_to_rgb(h, max(.72, s), min(.94, max(.62, v * 1.12)))
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


class Brand:
    """A brand record resolved to the colours and type styles the templates draw with."""

    def __init__(self, record=None, product_color=None):
        record = validate(record or {})
        self.record = record; self.preset = record['preset']; self.tokens = PRESETS[self.preset]
        self.name = record['name']; self.badges = record['badges']; self.legal = record['legal']; self.unit = record['unit']
        self.logo = record['logo']
        self.accent = record['accent'] or product_color or DEFAULT_ACCENT
        self.accent_source = 'user' if record['accent'] else 'product' if product_color else 'default'
        self.dark = record['dark'] or DARK; self.light = LIGHT
        kind = self.tokens['ground']
        # A colour taken from the product would hide the product on a page of that colour: such a ground is deepened.
        flat = draw.mix(self.accent, '#000000', .42) if self.accent_source == 'product' else self.accent
        self.ground = flat if kind == 'accent' else self.dark if kind == 'dark' else self.light
        self.ink = draw.on(self.ground, '#121212', '#ffffff')
        self.on_dark = self.ground != self.light and self.ink == '#ffffff'
        self.soft = draw.mix(self.ink, self.ground, .42)
        self.line = draw.mix(self.ink, self.ground, .82)
        # The accent as it must appear on this page's ground and on the dark bands: always readable.
        self.accent_on_ground = self.readable(self.accent, self.ground)
        self.accent_on_dark = self.readable(self.accent, self.dark)
        style = self.tokens['card']['style']
        self.panel = (draw.mix(self.ground, self.ink, .06) if style in ('soft', 'rules') else
                      '#ffffff' if kind == 'accent' else draw.mix(self.ground, '#ffffff', .1) if kind == 'dark' else draw.mix(self.ground, self.ink, .05))
        self.panel_ink = draw.on(self.panel, '#121212', '#ffffff')

    @staticmethod
    def readable(accent, ground, need=2.6):
        """The accent, nudged towards white or black until it stands out from the ground."""
        if draw.contrast(accent, ground) >= need:
            return accent
        towards = '#ffffff' if draw.luminance(ground) < .4 else '#000000'
        for step in range(1, 11):
            candidate = draw.mix(accent, towards, step / 10)
            if draw.contrast(candidate, ground) >= need:
                return candidate
        return towards

    def accent_for(self, ground):
        return self.readable(self.accent, ground)

    def ink_for(self, ground):
        return draw.on(ground, '#121212', '#ffffff')

    def _style(self, role, size, ground, **more):
        ink = self.ink_for(ground)
        style = {**self.tokens[role], 'size': size, 'color': ink, **more}
        return style

    def headline(self, size, ground=None, **more):
        ground = ground or self.ground
        style = self._style('headline', size, ground, accent=self.accent_for(ground), **more)
        if self.tokens.get('effect') == 'outline' and 'outline' not in more:
            style['shadow'] = {'dx': size * .05, 'dy': size * .06, 'opacity': .55, 'color': '#000000'}
        return style

    def label(self, size, ground=None, **more):
        return self._style('label', size, ground or self.ground, **more)

    def body(self, size, ground=None, **more):
        ground = ground or self.ground
        style = self._style('body', size, ground, **more)
        if 'color' not in more:
            style['color'] = draw.mix(self.ink_for(ground), ground, .28)
        return style

    def number(self, size, ground=None, **more):
        ground = ground or self.ground
        return {**self.tokens['headline'], 'size': size, 'color': self.accent_for(ground), 'leading': 1.0, **more}

    def public(self):
        return {**self.record, 'accent_used': self.accent, 'accent_source': self.accent_source, 'dark_used': self.dark, 'ground': self.ground}


def page_style(brand):
    """What the brand's preset asks of a template page (brandkit/engine/runtime.js TPL.applyStyle): how headlines and labels
    are set, the ground, the frame marks, how round cards and chips are and how leader lines are drawn.

    The default preset is the look the templates were designed in: it sets no type and no corners of its own, so pages
    built before presets reached the templates come out as they did. Every other preset states all of them."""
    tokens = brand.tokens

    def type_of(role):
        style = tokens[role]
        return {'weight': style.get('weight', 800), 'stretch': style.get('width', 100), 'case': style.get('case', 'none'),
                'italic': bool(style.get('italic')), 'tracking': style.get('tracking', 0)}
    default = brand.preset == DEFAULT_PRESET
    return {'preset': brand.preset, 'ground': tokens['ground'],
            'headline': None if default else type_of('headline'), 'label': None if default else type_of('label'),
            'card': None if default else {'radius': tokens['card']['radius'], 'style': tokens['card']['style']},
            'chip': None if default else {'radius': tokens['chip']['radius'], 'style': tokens['chip']['style']},
            'leader': None if default else dict(tokens['leader']),
            'frame': {'header': 'none', 'footer': 'none'} if default else {'header': tokens['header'], 'footer': tokens['footer']}}


def presets():
    return [{'id': key, 'label': {'zh': value['title'][0], 'en': value['title'][1]}, 'note': {'zh': value['note'][0], 'en': value['note'][1]},
             'ground': value['ground']} for key, value in PRESETS.items()]

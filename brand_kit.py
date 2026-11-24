"""The brand kit: one look carried across every image of a listing.

A listing keeps one accent colour, one headline treatment, one way of drawing leader lines and
one frame from its first image to its last. That is stored once per project as the brand and
read by every page template. A preset is a whole look: its type, ground and marks, and (``layout``)
the composition each page is tried in first. The ten presets are the composition families found
in Amazon listing galleries and agency practice:

* ``frame``      the templates as designed (no layout recipe): the default.
* ``clean``      white space, the product centred and large, thin grey leaders, circular close-ups.
* ``flat``       the page in the brand colour, one oversized word set behind the product.
* ``tech``       dark premium: charcoal, the product big off one side, numbered markers, ring close-ups.
* ``promo``      the words on a heavy band under the scene, the product running off the page, italic caps.
* ``split``      a brand-colour block holding every word and a light field, the product across the seam.
* ``blueprint``  a drafting grid on blueprint blue, tick-ended dimension lines, a title block.
* ``workshop``   the headline over the top of the scene, callouts stacked in one column on the open side.
* ``editorial``  warm paper, a serif headline, a one-third colour column, numbered features.
* ``catalogue``  white, the words beside the product, numbered markers and a legend, kit laid flat.

The presets describe treatments, not anyone's trade dress: no logos, names or marks are built
in. A brand name, badge texts, a legal line and a logo are the user's own.
"""
import colorsys
import json
import re

from PIL import Image

import brandkit_assets
import listing_draw as draw

# A preset's ``layout`` is its composition, page by page (listing_jobs.preset_jobs): for each kind, 'prefer' lists copies
# of the builder's versions to try first ({'from': the builder's mode or '*', 'set': knobs}), and 'all' knobs go on every
# version of that kind. The builder's own versions always follow, so a page the preferred composition cannot hold still
# comes out. ``fonts``: the font pairing the preset reads in, used while the brand's own is left at the default.
PRESETS = {
    # The default preset draws the templates as they were designed (page_style sets no bands, type or corners of its own):
    # its name and note say that, not the band frame its tokens describe for other uses. It has no layout recipe.
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
             'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'mode': 'center', 'center_type': 'behind'}}]},
                        'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'numbered', 'label_side': 'left'}}]},
                        'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'strip'}}]},
                        'T02': {'prefer': [{'from': 'macro', 'set': {'layout': 'band'}}]},
                        'T04': {'all': {'chip_style': 'dark'}},
                        'T08': {'prefer': [{'from': 'stagger', 'set': {'mode': 'bento'}}]},
                        'T05': {'all': {'qty_style': 'badge'}}},
             'note': ('整页品牌色：一个超大的词垫在产品背后，部件上是编号圆点、图例排在左侧，场景是一大两小的拼格',
                      'The page in the brand colour: one oversized word behind the product, numbered dots on the parts with the legend on the left, one big scene tile and two small')},
    'tech': {'title': ('深色科技', 'Dark tech'), 'ground': 'dark', 'header': 'none', 'footer': 'line', 'fonts': 'tech',
             'headline': {'font': 'orbitron', 'weight': 800, 'case': 'upper', 'leading': 1.08, 'tracking': .01},
             'label': {'font': 'archivo', 'weight': 700, 'width': 104, 'case': 'upper', 'leading': 1.14, 'tracking': .02},
             'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.34},
             'leader': {'route': 'angled', 'end': 'ring', 'width': 2, 'ink': 'accent'},
             'card': {'radius': 14, 'style': 'outline'}, 'chip': {'radius': 8, 'style': 'outline'}, 'badge': 'hex', 'icons': True,
             'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'solid_layout': 'column', 'hero_bleed': .1}}]},
                        'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'numbered', 'label_side': 'right'}}]},
                        'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'ring'}}]},
                        'T02': {'all': {'ground': 'carbon', 'rim': .22}},
                        'T04': {'prefer': [{'from': '*', 'set': {'mode': 'band'}}], 'all': {'chip_style': 'dark'}},
                        'T08': {'prefer': [{'from': 'stagger', 'set': {'mode': 'bento'}}]},
                        'T06': {'all': {'dim_style': 'ticks'}},
                        'T05': {'all': {'qty_style': 'badge'}}},
             'note': ('深色高端：文字一栏靠左，产品放大出血到右下角，部件上编号、图例在右，圆形特写，参数数字排成一行',
                      'Dark premium: the words in a column on the left, the product large and running off the corner, numbered parts with the legend on the right, round close-ups, figures in a row')},
    'clean': {'title': ('简洁白底', 'Clean white'), 'ground': 'light', 'white': True, 'header': 'none', 'footer': 'none', 'fonts': 'clean',
              'headline': {'font': 'archivo', 'weight': 800, 'width': 100, 'case': 'none', 'leading': 1.04},
              'label': {'font': 'archivo', 'weight': 700, 'width': 100, 'case': 'none', 'leading': 1.14},
              'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.34},
              'leader': {'route': 'angled', 'end': 'dot', 'width': 1.8, 'ink': 'soft'},
              'card': {'radius': 20, 'style': 'soft'}, 'chip': {'radius': 999, 'style': 'soft'}, 'badge': 'pill', 'icons': True,
              'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'mode': 'center', 'center_type': 'below'}}]},
                         'T01': {'prefer': [{'from': '*', 'set': {'frame': 'plain'}}]},
                         'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'ring', 'ring_style': 'grey'}}]},
                         'T02': {'all': {'edge': 'fade'}},
                         'T04': {'prefer': [{'from': '*', 'set': {'mode': 'glass'}}]},
                         'T08': {'prefer': [{'from': 'columns', 'set': {}}]},
                         'T13': {'prefer': [{'from': 'columns', 'set': {}}]},
                         'T05': {'all': {'header': 'light'}}},
              'note': ('大留白白底：产品居中放大，标题在产品下方，细灰引线从两侧指向部件，圆形特写围着产品',
                       'White space: the product centred and large with the headline under it, thin grey leaders from both sides, round close-ups around it')},
    'promo': {'title': ('促销冲击', 'Bold promo'), 'ground': 'dark', 'header': 'slash', 'footer': 'none', 'fonts': 'poster',
              'headline': {'font': 'archivo', 'weight': 900, 'width': 68, 'italic': True, 'case': 'upper', 'leading': .96},
              'label': {'font': 'archivo', 'weight': 800, 'width': 80, 'italic': True, 'case': 'upper', 'leading': 1.08},
              'body': {'font': 'archivo', 'weight': 500, 'width': 92, 'case': 'none', 'leading': 1.26},
              'leader': {'route': 'straight', 'end': 'arrow', 'width': 4, 'ink': 'accent'},
              'card': {'radius': 6, 'style': 'solid'}, 'chip': {'radius': 4, 'style': 'slant'}, 'badge': 'burst', 'icons': True, 'effect': 'outline',
              'layout': {'T12': {'prefer': [{'from': 'band', 'set': {}}, {'from': 'solid', 'set': {'hero_bleed': .12}}]},
                         'T01': {'prefer': [{'from': '*', 'set': {'frame': 'band'}}]},
                         'T03': {'prefer': [{'from': 'split', 'set': {'caption_style': 'band'}}]},
                         'T02': {'prefer': [{'from': 'macro', 'set': {'panel_side': 'right'}}], 'all': {'ground': 'carbon'}},
                         'T04': {'prefer': [{'from': '*', 'set': {'mode': 'band'}}]},
                         'T08': {'all': {'first_side': 'left'}},
                         'T05': {'all': {'qty_style': 'badge'}}},
              'note': ('促销冲击：照片在上、大字压在下方厚色带里，无场景时产品放大冲出画面，页眉色带，特写配色块标签',
                       'Bold promo: the scene on top and big words on a heavy band under it, without a scene the product large and running off the page, a header band, close-ups with solid tabs')},
    'split': {'title': ('分屏色块', 'Split screen'), 'ground': 'light', 'header': 'none', 'footer': 'none',
              'headline': {'font': 'archivo', 'weight': 900, 'width': 100, 'case': 'upper', 'leading': .96},
              'label': {'font': 'archivo', 'weight': 800, 'width': 100, 'case': 'upper', 'leading': 1.08},
              'body': {'font': 'inter', 'weight': 500, 'case': 'none', 'leading': 1.3},
              'leader': {'route': 'straight', 'end': 'square', 'width': 3, 'ink': 'accent'},
              'card': {'radius': 0, 'style': 'solid'}, 'chip': {'radius': 0, 'style': 'solid'}, 'badge': 'box', 'icons': False,
              'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'mode': 'split', 'split_side': 'left', 'split_ratio': .46}}]},
                         'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'column', 'label_side': 'right'}}]},
                         'T03': {'prefer': [{'from': 'split', 'set': {'rail_side': 'right'}}]},
                         'T02': {'prefer': [{'from': 'macro', 'set': {'panel_side': 'right'}}]},
                         'T04': {'all': {'chip_style': 'dark'}},
                         'T08': {'prefer': [{'from': 'split', 'set': {'split_dir': 'rows'}}, {'from': 'stagger', 'set': {'stagger_style': 'rows'}}]},
                         'T05': {'all': {'header': 'light'}}},
              'note': ('分屏色块：左边品牌色块放全部文字，产品跨在色块和浅色区的分界线上，标注竖排一列，特写栏在右',
                       'Split screen: a brand-colour block on the left holds every word, the product straddles the seam into a light field, labels in one column, close-ups on the right')},
    'blueprint': {'title': ('工程蓝图', 'Blueprint'), 'ground': 'blueprint', 'header': 'none', 'footer': 'line', 'fonts': 'impact',
                  'texture': {'kind': 'drafting', 'opacity': .16},
                  'headline': {'font': 'archivo', 'weight': 700, 'width': 75, 'case': 'upper', 'leading': 1.0, 'tracking': .03},
                  'label': {'font': 'archivo', 'weight': 600, 'width': 80, 'case': 'upper', 'leading': 1.1, 'tracking': .04},
                  'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.34},
                  'leader': {'route': 'elbow', 'end': 'ring', 'width': 2, 'ink': 'ink'},
                  'card': {'radius': 0, 'style': 'outline'}, 'chip': {'radius': 0, 'style': 'outline'}, 'badge': 'box', 'icons': True,
                  'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'hero_bleed': 0}}]},
                             'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'numbered', 'label_side': 'right', 'frame': 'light'}}]},
                             'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'strip'}}]},
                             'T02': {'all': {'edge': 'fade', 'grain': False}},
                             'T04': {'prefer': [{'from': '*', 'set': {'mode': 'band'}}]},
                             'T08': {'prefer': [{'from': 'columns', 'set': {}}]},
                             'T13': {'prefer': [{'from': 'columns', 'set': {}}]},
                             'T06': {'all': {'dim_style': 'ticks', 'ground_style': 'blueprint', 'title_block': True}},
                             'T05': {'all': {'qty_style': 'both', 'header': 'light'}}},
                  'note': ('工程蓝图：蓝底细制图网格，产品完整摆正，尺寸线画在轮廓外、两端短竖线，右下角图签，零件编号加图例',
                           'Blueprint: a fine drafting grid on blue, the whole product square on, dimension lines outside its outline with tick ends, a title block, numbered parts with a legend')},
    'workshop': {'title': ('工业车间', 'Workshop'), 'ground': 'light', 'header': 'band', 'footer': 'none',
                 'headline': {'font': 'archivo', 'weight': 900, 'width': 112, 'case': 'upper', 'leading': .96},
                 'label': {'font': 'archivo', 'weight': 800, 'width': 104, 'case': 'upper', 'leading': 1.08},
                 'body': {'font': 'archivo', 'weight': 500, 'width': 100, 'case': 'none', 'leading': 1.26},
                 'leader': {'route': 'straight', 'end': 'square', 'width': 4, 'ink': 'accent'},
                 'card': {'radius': 0, 'style': 'rules'}, 'chip': {'radius': 0, 'style': 'solid'}, 'badge': 'box', 'icons': False,
                 'layout': {'T12': {'prefer': [{'from': 'overlay', 'set': {'text_zone': 'top'}}, {'from': 'band', 'set': {}},
                                               {'from': 'solid', 'set': {'mode': 'split', 'split_side': 'right', 'split_ratio': .42}}]},
                            'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'column', 'label_side': 'left', 'frame': 'band'}},
                                               {'from': '*', 'set': {'callout_style': 'column', 'label_side': 'left'}}]},
                            'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'rail'}}]},
                            'T02': {'all': {'ground': 'carbon'}},
                            'T04': {'prefer': [{'from': '*', 'set': {'mode': 'band'}}]},
                            'T08': {'prefer': [{'from': 'stagger', 'set': {'stagger_style': 'rows'}}]},
                            'T06': {'all': {'dim_style': 'ticks'}}},
                 'note': ('工业车间：标题压在作业照片上方，深色页眉带，标注全部竖排在产品一侧、每条一根水平引线，一个场景一行',
                          'Workshop: the headline over the top of a job-site scene, a dark header band, every label stacked on one side with a level leader each, one scene per row')},
    'editorial': {'title': ('杂志编排', 'Editorial'), 'ground': 'paper', 'header': 'none', 'footer': 'none', 'fonts': 'elegant',
                  'headline': {'font': 'archivo', 'weight': 700, 'width': 100, 'case': 'none', 'leading': 1.06},
                  'label': {'font': 'archivo', 'weight': 600, 'width': 100, 'case': 'none', 'leading': 1.14},
                  'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.4},
                  'leader': {'route': 'straight', 'end': 'dot', 'width': 1.6, 'ink': 'ink'},
                  'card': {'radius': 0, 'style': 'rules'}, 'chip': {'radius': 999, 'style': 'outline'}, 'badge': 'pill', 'icons': False,
                  'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'mode': 'split', 'split_side': 'right', 'split_ratio': .38}}]},
                             'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'numbered', 'label_side': 'left'}}]},
                             'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'rail'}}]},
                             'T02': {'prefer': [{'from': 'macro', 'set': {'layout': 'band'}}], 'all': {'grain': False}},
                             'T04': {'prefer': [{'from': '*', 'set': {'mode': 'stack'}}]},
                             'T08': {'prefer': [{'from': 'stagger', 'set': {'mode': 'bento', 'bento_split': .64}}]},
                             'T05': {'all': {'header': 'light'}}},
                  'note': ('杂志编排：暖纸色底，衬线大标题，右侧三分之一窄色栏放文字，01/02/03 编号要点，标题、照片、参数上下分层',
                           'Editorial: warm paper, a serif headline, the words in a narrow one-third column on the right, 01/02/03 numbered points, headline, scene and figures in layers')},
    'catalogue': {'title': ('产品目录', 'Catalogue'), 'ground': 'light', 'white': True, 'header': 'none', 'footer': 'line', 'fonts': 'geometric',
                  'headline': {'font': 'archivo', 'weight': 800, 'width': 100, 'case': 'none', 'leading': 1.04},
                  'label': {'font': 'archivo', 'weight': 700, 'width': 100, 'case': 'none', 'leading': 1.12},
                  'body': {'font': 'inter', 'weight': 400, 'case': 'none', 'leading': 1.34},
                  'leader': {'route': 'straight', 'end': 'ring', 'width': 2, 'ink': 'soft'},
                  'card': {'radius': 12, 'style': 'soft'}, 'chip': {'radius': 8, 'style': 'soft'}, 'badge': 'pill', 'icons': True,
                  'layout': {'T12': {'prefer': [{'from': 'solid', 'set': {'solid_layout': 'column', 'hero_bleed': 0}}]},
                             'T01': {'prefer': [{'from': '*', 'set': {'callout_style': 'numbered', 'label_side': 'right', 'frame': 'plain'}}]},
                             'T03': {'prefer': [{'from': 'split', 'set': {'mode': 'strip'}}]},
                             'T02': {'all': {'edge': 'fade'}},
                             'T04': {'prefer': [{'from': '*', 'set': {'mode': 'glass'}}]},
                             'T08': {'prefer': [{'from': 'stagger', 'set': {'mode': 'bento', 'bento_split': .5}}]},
                             'T13': {'prefer': [{'from': 'columns', 'set': {}}]},
                             'T06': {'prefer': [{'from': 'table', 'set': {}}], 'all': {'title_block': True}},
                             'T05': {'prefer': [{'from': '*', 'set': {'mode': 'knolling'}}], 'all': {'qty_style': 'both', 'header': 'light'}}},
                  'note': ('产品目录：白底网格，文字一栏在左、产品在右，部件编号加图例代替引线，多个状态并排，配件平铺成套',
                           'Catalogue: a white grid, the words in a column left of the product, numbered parts with a legend instead of leaders, states side by side, the kit laid flat')},
}
DEFAULT_PRESET = 'frame'
DEFAULT_ACCENT = '#e5322d'
DARK = '#16171a'
LIGHT = '#ffffff'
# The grounds of the presets that bring their own: warm paper (editorial) and blueprint blue (blueprint).
GROUNDS = {'paper': '#f4efe6', 'blueprint': '#173b63'}
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
        self.ground = flat if kind == 'accent' else self.dark if kind == 'dark' else GROUNDS.get(kind, self.light)
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
    style = {'preset': brand.preset, 'ground': tokens['ground'],
             'headline': None if default else type_of('headline'), 'label': None if default else type_of('label'),
             'card': None if default else {'radius': tokens['card']['radius'], 'style': tokens['card']['style']},
             'chip': None if default else {'radius': tokens['chip']['radius'], 'style': tokens['chip']['style']},
             'leader': None if default else dict(tokens['leader']),
             'frame': {'header': 'none', 'footer': 'none'} if default else {'header': tokens['header'], 'footer': tokens['footer']}}
    if tokens.get('texture'):
        # a ground pattern of the preset's own (the blueprint's drafting grid); only stated when there is one, so the
        # pages of the other presets carry the same style as before. A texture the seller picked is drawn instead.
        style['texture'] = dict(tokens['texture'])
    return style


def fonts_of(brand, language='en'):
    """The font pairing a page is set in: the brand's own, or, while that is left at the default, the preset's. A Chinese
    page takes the preset's only when its Chinese face is the default one: the other Chinese faces are downloads, and a
    preset is never a reason to wait for one."""
    if brand is None:
        return None
    chosen = brand.record.get('fonts')
    own = brand.tokens.get('fonts')
    if not own or chosen not in (None, brandkit_assets.DEFAULT_PAIRING) or own not in brandkit_assets.PAIRINGS:
        return chosen
    if language == 'zh' and brandkit_assets.PAIRINGS[own]['cjk'] != brandkit_assets.PAIRINGS[brandkit_assets.DEFAULT_PAIRING]['cjk']:
        return chosen
    return own


def presets():
    return [{'id': key, 'label': {'zh': value['title'][0], 'en': value['title'][1]}, 'note': {'zh': value['note'][0], 'en': value['note'][1]},
             'ground': value['ground']} for key, value in PRESETS.items()]

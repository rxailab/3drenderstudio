"""The brand on the page: which pages carry badges, the logo and the legal line, the page palette from the brand
colours, and the ink that reads on a ground. Split out of ``brandkit_set``, which still re-exports every name.
"""

import brandkit_render

from listing_kinds import KINDS


# Pages that carry the brand's badge texts (TPL.badge), and how many: the main image and the poster.
BADGED = {'T01': 1, 'T12': 2}                    # how many brand badges a page has room for
# Where the brand's own marks go. The logo: the pages that open the set or show the product in a scene. The legal line:
# the pages that state facts. A mark is drawn only where it covers nothing (runtime.js TPL.brandMarks).
LOGO_PAGES = ('T12', 'T01', 'T04', 'T08')
LEGAL_PAGES = ('T06', 'T05', 'T04', 'T02', 'T03')


def badges_on(template_id, badges, kinds=None):
    """The brand badges one page carries. Three can be entered: the poster takes the first and the third, the callout
    page the second (the first when there is only one). In a set without a callout page (``kinds``: the pages being
    built) the poster takes the first two instead, so the order the seller gave decides what is shown."""
    texts = [text.strip() for text in (badges or []) if isinstance(text, str) and text.strip()]
    callouts = kinds is None or 'T01' in kinds
    if template_id == 'T12':
        return ([text for index, text in enumerate(texts) if index in (0, 2)] if callouts else texts)[:BADGED['T12']]
    if template_id == 'T01':
        return texts[1:2] or texts[:1]
    return []


def brand_usage(record, kinds, logo=True):
    """Which pages of a set will carry the brand's logo, legal line and each badge, before anything is built:
    {'logo': [kinds], 'legal': [kinds], 'badges': [{'text', 'pages'}], 'omitted': [what the seller entered that no page shows]}."""
    kinds = [kind for kind in kinds if kind in KINDS]
    record = record or {}
    out = {'logo': [kind for kind in kinds if kind in LOGO_PAGES] if record.get('logo') and logo else [],
           'legal': [kind for kind in kinds if kind in LEGAL_PAGES] if str(record.get('legal') or '').strip() else [],
           'badges': [], 'omitted': []}
    for text in [text.strip() for text in (record.get('badges') or []) if isinstance(text, str) and text.strip()]:
        pages = [kind for kind in kinds if text in badges_on(kind, record.get('badges'), kinds)]
        out['badges'].append({'text': text, 'pages': pages})
        if not pages:
            out['omitted'].append('徽标「%s」' % text)
    if record.get('logo') and logo and not out['logo']:
        out['omitted'].append('品牌标志')
    if str(record.get('legal') or '').strip() and not out['legal']:
        out['omitted'].append('说明文字')
    return out


def marks_drawn(qa):
    """The brand marks a laid-out page really carries: {'logo': bool, 'legal': bool, 'badges': [texts]}."""
    elements = {str(row.get('id')): row for row in (qa or {}).get('elements', []) if isinstance(row, dict)}
    return {'logo': 'brand_logo' in elements, 'legal': 'brand_legal' in elements,
            'badges': [' '.join(str(row.get('text') or '').split()) for key, row in sorted(elements.items())
                       if key.startswith('brand_badge') and key.endswith('_t')]}


def marks_missing(record, pages, logo=True):
    """What the brand record holds that none of the built pages shows, named for the build's notes."""
    record = record or {}; drawn = [page.get('marks') or {} for page in pages]
    missing = []
    if record.get('logo') and logo and not any(row.get('logo') for row in drawn):
        missing.append('品牌标志')
    if str(record.get('legal') or '').strip() and not any(row.get('legal') for row in drawn):
        missing.append('说明文字')
    shown = {text.upper() for row in drawn for text in row.get('badges') or []}
    for text in [text.strip() for text in (record.get('badges') or []) if isinstance(text, str) and text.strip()]:
        if ' '.join(text.split()).upper() not in shown:
            missing.append('徽标「%s」' % text)
    return missing


MARK_NOTES = (('brand logo left out', '品牌标志没有放上这一页：四个角都被文字或产品占着。'),
              ('legal line left out: too long', '说明文字没有放上这一页：一行放不下，请改短。'),
              ('legal line left out', '说明文字没有放上这一页：底边被内容占着。'),
              ('no free spot for badge', '品牌徽标没有放上这一页：没有不遮挡内容的空位。'),
              ('does not fit a', '品牌徽标没有放上这一页：文字太长，放不进这种徽标。'))


def mark_note(line):
    """A layout note about a brand mark the page left out, in the seller's language; other notes as they are."""
    text = str(line)
    return next((said for key, said in MARK_NOTES if key in text), text[:160])


DARK_TEMPLATES = ('#24272b', '#26292d')          # the ground of the templates designed dark (close-ups, the scene mosaic)
# Ground colours a template reads besides --bg: on a dark or brand-colour ground the page's inks and hairlines turn light.
ON_DARK = {'text': '#F4EFE3', 'muted': '#C3C9CE', 'ink': '#F4EFE3', 'ink-2': '#D8D2C6', 'line': '#7C848B', 'hair': 'rgba(255,255,255,.2)',
           'grid': '#5A6066', 'split': 'rgba(255,255,255,.14)', 'wm': 'rgba(255,255,255,.05)'}


def _palette(template_id, brand):
    """The template's colours with the brand's: its accent (kept readable on the ground), its dark, and the ground its
    preset asks for. A light page turns dark for a dark preset and brand-coloured for the flat one, its inks with it;
    the pages designed dark stay dark (their type is drawn for it) and take the brand's dark or deepened colour."""
    tokens = brandkit_render.template_info(template_id)['tokens']
    palette = dict(tokens['palette'])
    if brand is not None:
        import listing_draw as draw
        dark = palette['bg'].lower() in DARK_TEMPLATES
        ground = brand.tokens['ground']
        if dark:
            if ground == 'accent':
                palette['bg'] = draw.mix(brand.ground, '#000000', .45)
            elif brand.record.get('dark') or ground == 'dark':
                palette['bg'] = brand.dark
        elif ground == 'dark':
            palette.update(ON_DARK, bg=brand.dark, paper=brand.dark)
        elif ground == 'accent':
            palette.update(ON_DARK, bg=brand.ground, paper=brand.ground, muted='rgba(255,255,255,.82)')
            if draw.contrast('#FFFFFF', brand.ground) < 3:          # a pale brand colour: dark type on it instead
                palette.update(text='#151515', ink='#151515', muted='rgba(0,0,0,.7)', line='rgba(0,0,0,.45)', hair='rgba(0,0,0,.2)',
                               grid='rgba(0,0,0,.3)', split='rgba(0,0,0,.16)', wm='rgba(0,0,0,.05)')
                palette['ink-2'] = '#2A2A2A'
        elif brand.preset == 'clean':
            palette.update(bg='#FFFFFF', paper='#FFFFFF')
        palette['accent'] = brand.readable(brand.accent, palette['bg'])
    # Text on an accent fill (chips, pills, number badges, small captions): whichever ink reads better on the accent as
    # it is finally drawn. (A brightness threshold chose white on a mid grey, #999999: 2.85:1, where near-black gives 6.63:1.)
    palette['accent-ink'] = ink_on(palette.get('accent', '#D0612A'))
    return palette


INKS = ('#111111', '#FFFFFF')
SMALL_TEXT = 4.5              # the contrast small type needs (WCAG AA)


def ink_on(ground, inks=INKS):
    """The ink with the higher contrast on this ground (WCAG contrast ratio); the dark one on a tie or an unreadable colour.
    On the few mid greys where neither the kit's near-black nor white reaches what small type needs, pure black does."""
    import listing_draw as draw
    try:
        best = max(inks, key=lambda ink: draw.contrast(ink, ground))
        return best if draw.contrast(best, ground) >= SMALL_TEXT or draw.contrast('#000000', ground) < SMALL_TEXT else '#000000'
    except (ValueError, TypeError, IndexError):
        return inks[0]

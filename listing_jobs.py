"""Jobs for the templates: one job (the data a template lays out) per version of a page, built from the product
record, its pictures and its scenes, with re-cropped variants. Split out of ``brandkit_set``, which still re-exports every name.
"""

import json
import re

import brand_kit
import brandkit_assets
import brandkit_render

from listing_kinds import CANVAS, TEXT
from listing_copy import humanised, mesh_named, _body, _keep_together, _clip, _headline
from listing_scenes import usable_scene, _clause_lines, bare
from listing_theme import LOGO_PAGES, LEGAL_PAGES, badges_on, _palette
from listing_dimensions import view_axes, stated_dimensions


def _job(template_id, ctx, **fields):
    width, height = CANVAS[ctx['aspect']]
    tokens = brandkit_render.template_info(template_id)['tokens']
    palette = _palette(template_id, ctx.get('brand'))
    background = str(palette.get('bg', '')).lstrip('#')
    if ctx.get('light_product') and len(background) == 6 and sum(int(background[i:i + 2], 16) for i in (0, 2, 4)) / 765 > .8:
        palette['bg'] = '#D5D9DD'        # a bright steel or white product on a near-white page would vanish: a cool grey ground
    brand = ctx.get('brand'); record = getattr(brand, 'record', None) or {}
    extra = {}
    badges = badges_on(template_id, record.get('badges'), ctx.get('kinds'))
    if badges and 'badges' not in fields:
        extra['badges'] = [{'text': text, 'style': record.get('badge_style') or 'auto'} for text in badges]
    if record.get('texture') and record['texture'] != 'none':
        extra['texture'] = record['texture']
    if brand is not None:
        # the preset reaches every page (type, ground, frame, corners, leader lines), and so do the brand's own marks
        extra['style'] = brand_kit.page_style(brand)
        marks = {}
        logo = getattr(brand, 'logo_path', None)
        if logo and template_id in LOGO_PAGES:
            try:
                marks['logo'] = brandkit_render.asset(logo)
            except (OSError, ValueError):
                pass
        if brand.legal and template_id in LEGAL_PAGES:
            marks['legal'] = brand.legal
        if marks:
            extra['brand_marks'] = marks
    return {'template_id': template_id, 'variant': 'studio', 'canvas': {'width': width, 'height': height}, 'lang': ctx['language'],
            'safe_margin': tokens.get('safe_margin', 84), 'min_margin': tokens.get('min_margin', 60),
            'palette': palette, 'publishability': 'user_product',
            'fonts': brandkit_assets.job_fonts(brand_kit.fonts_of(brand, ctx['language']), ctx['language']), **extra, **fields}


# The mode a template draws when a job names none (T04 overlay on the square and 4:5 canvases, T05 the grid...), so a
# recipe can say 'from' the mode a builder's version really has.
DEFAULT_MODES = {'T03': 'split', 'T04': 'overlay', 'T05': 'grid', 'T06': 'onproduct', 'T12': 'solid', 'T13': 'auto'}


def preset_jobs(kind, jobs, recipe):
    """A page's versions in the preset's composition (brand_kit.PRESETS[id]['layout'], or the recipe itself): for each
    'prefer' entry in order, copies of the versions whose mode is its 'from' ('*': any) with its 'set' knobs, first; then
    the builder's own versions as the fallback (lay_out keeps the first clean one). 'all' knobs go on every version, an
    entry's 'set' winning over them. A version made twice is kept once. Without a recipe for the kind: the list as given."""
    recipe = (recipe or {}).get(kind) if isinstance(recipe, dict) else None
    if not recipe or not jobs:
        return jobs
    every = recipe.get('all') or {}
    out = []
    for entry in recipe.get('prefer') or []:
        out += [{**job, **every, **(entry.get('set') or {})} for job in jobs
                if entry.get('from') == '*' or entry.get('from') == (job.get('mode') or DEFAULT_MODES.get(kind))]
    out += [{**job, **every} for job in jobs]
    seen, kept = set(), []
    for job in out:
        key = json.dumps(job, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key); kept.append(job)
    return kept


def _layout_of(ctx):
    brand = ctx.get('brand')
    return getattr(brand, 'tokens', {}).get('layout') if brand is not None else None


ICON_WORDS = ((('battery', '电池', 'mah'), 'battery-4'), (('volt', '电压'), 'bolt'), (('weight', '重量', '净重', 'kg', ' lb'), 'weight'),
              (('speed', 'rpm', '转速'), 'gauge'), (('torque', '扭矩', 'n·m', 'nm'), 'rotate-clockwise-2'),
              (('capacity', '容量', 'volume', '容积'), 'package'), (('time', 'runtime', '时间', '续航', 'min'), 'clock-hour-4'),
              (('waterproof', 'water', '防水', 'ip6'), 'droplet'), (('warranty', '保修', '质保'), 'shield-check'), (('led', 'light', '照明', '灯'), 'bulb'),
              (('length', 'width', 'height', 'depth', 'size', 'diameter', '长', '宽', '高', '深', '厚', '尺寸', '直径', '管径', '范围'), 'ruler-measure'),
              (('material', 'steel', 'canvas', 'leather', 'aluminium', 'aluminum', 'plastic', '材质', '材料', '钢', '帆布', '皮', '铝'), 'stack-2'))


def _icon_for(label, unit=''):
    """A Tabler line icon for a spec, by the words in its label (then its unit); a height, width or depth gets its own arrow."""
    text = ' %s %s ' % (str(label or '').lower(), str(unit or '').lower())
    for words, icon in ((('height', '高度', '高'), 'arrow-autofit-height'), (('width', 'length', '宽度', '长度', '宽', '长'), 'arrow-autofit-width'),
                        (('depth', 'thickness', '深度', '厚度', '深', '厚'), 'arrows-diagonal')):
        if any(word in str(label or '').lower() for word in words):
            return icon
    return next((icon for words, icon in ICON_WORDS if any(word in text for word in words)), None)


def _chip_rows(ctx, limit=3):
    """The product's figures as chips (icon, value, unit, label): its stated figures and number specs, then the sizes the user stated."""
    t = TEXT[ctx['language']]; product = ctx['product']; rows = []
    for row in product['figures']:
        number, _ = _number_unit(row['value'])
        if number is not None:
            rows.append({'label': _clip(row['label'], 18), 'value': row['value'], 'unit': row.get('unit', ''), 'icon': _icon_for(row['label'], row.get('unit'))})
    for row in product['specs']:
        number, unit = _number_unit(row['value'])
        if number is not None and len(row['value']) <= 14 and not any(item['label'] == _clip(row['label'], 18) for item in rows):
            rows.append({'label': _clip(row['label'], 18), 'value': number, 'unit': unit, 'icon': _icon_for(row['label'], unit)})
    def as_mm(value, unit):
        number, _ = _number_unit(value)
        scale = {'mm': 1, 'cm': 10, 'm': 1000, 'in': 25.4, '"': 25.4, '毫米': 1, '厘米': 10}.get(str(unit or '').strip().lower())
        return float(str(number).replace(',', '')) * scale if number is not None and scale else None
    shown = [as_mm(row['value'], row.get('unit')) for row in rows]
    # Then the overall sizes the user stated (never one measured on the model), unless a figure already gives it (476 mm = 47.6 cm).
    stated = ctx['stated'] if 'stated' in ctx else stated_dimensions(product, ctx.get('dims_mm'), ctx.get('flat'), unit=ctx['unit'])[0]
    for row in stated:
        if len(rows) >= limit:
            break
        if any(value and abs(value - row['mm']) <= max(2.0, row['mm'] * .02) for value in shown) or any(item['label'] == t[row['key']] for item in rows):
            continue
        icon = {'height': 'arrow-autofit-height', 'width': 'arrow-autofit-width', 'length': 'arrow-autofit-width', 'depth': 'arrows-diagonal',
                'thickness': 'arrows-diagonal'}.get(row['key'], 'ruler-measure')
        rows.append({'label': t[row['key']], 'value': '%g' % row['value'], 'unit': row['unit'], 'icon': icon})
    return [{key: value for key, value in row.items() if value is not None} for row in rows[:limit]]


def _number_unit(value):
    match = re.match(r'^\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(.*)$', str(value or ''))
    if not match:
        return None, None
    return match.group(1), match.group(2).strip()


def jobs_for(kind, ctx):
    """Versions of one page, best first: when one fails the layout checks the next, lighter one is tried. The lines are
    the copywriter's (when it wrote this page's), then the product's name as headline, then the template's own short line."""
    lists = []
    # the preset's composition first, before the crop variants, so each preferred version keeps its own crop group
    recipe = _layout_of(ctx)
    versions = lambda context: _recropped(preset_jobs(kind, _jobs_for(kind, context), recipe))
    if (ctx.get('copy') or {}).get(kind):
        lists.append([{**job, '_lines': 'model'} for job in versions(ctx)])
    plain = {**ctx, 'copy': {}}
    lists.append([{**job, '_lines': 'name'} for job in versions(plain)])
    lists.append([{**job, '_lines': 'stock'} for job in versions({**plain, 'stock_lines': True})])
    out = []; seen = set(); offset = 0
    for jobs in lists:
        if not jobs:
            continue
        heads = {job.get('headline') for job in jobs}
        if heads <= seen:
            continue                         # the same lines again (a page whose headline is not the name's)
        seen |= heads
        top = 0
        for job in jobs:                    # crop variants keep their group, numbered across the lists
            out.append({**job, '_group': job.get('_group', 0) + offset}); top = max(top, job.get('_group', 0))
        offset += top + 1
    return out


CROP_SHIFTS = ((0, -.2), (0, .2), (-.2, 0), (.2, 0))


def _recropped(jobs):
    """After each version whose close-ups have a part map, the same version with their crops moved a little each way:
    tried in turn when a frame edge cuts through a small part (a badge, a rivet)."""
    out = []
    for group, job in enumerate(jobs):
        job = {**job, '_group': group}
        out.append(job)
        name = next((key for key in ('details', 'features') if isinstance(job.get(key), list) and any(isinstance(row, dict) and row.get('detail') for row in job[key])), None)
        if not name or not any((job.get('assets') or {}).get(row.get('detail'), {}).get('_parts') for row in job[name]):
            continue
        for dx, dy in CROP_SHIFTS:
            # a close-up cut round its feature's spot moves half as far, so the spot stays in the middle of the frame
            scale = lambda row: .5 if (job.get('assets') or {}).get(row.get('detail'), {}).get('_centred') else 1
            rows = [{**row, 'focus': [round(min(1, max(0, (row.get('focus') or [.5, .5])[0] + dx * scale(row))), 3),
                                      round(min(1, max(0, (row.get('focus') or [.5, .5])[1] + dy * scale(row))), 3)]} if row.get('detail') else row for row in job[name]]
            out.append({**job, name: rows})
    return out


def _data_line(kind, ctx):
    """A headline made of the product's own facts for a page the copywriter did not write: the selling line the seller gave,
    the parts a close-up shows, the strongest figure, the occasions the scenes show. None when the data has nothing to say."""
    product = ctx['product']; zh = ctx['language'] == 'zh'; name = _clip(humanised(product['title']), 26)
    features = [row for row in product['features'] if row.get('title') and not _not_printed(row['title'], product)]
    if kind == 'T01':
        line = product.get('subline') or product.get('headline')
        return _clip(line, 44) if line else None
    if kind == 'T02':
        shown = [_clip(product['features'][index]['title'], 22) for index, _ in _printable_rows(ctx, ctx.get('macros', []))[:2]] or [_clip(row['title'], 22) for row in features[:2]]
        if len(shown) >= 2:
            return ('%s\n[[%s]]' % (shown[0], shown[1])) if zh else '%s,\n[[%s]]' % (shown[0], shown[1].lower())
        return None
    if kind == 'T04':
        figure = next((row for row in product['figures'] if row.get('value')), None)
        if figure:
            return ('[[%s%s]] %s' % (figure['value'], figure.get('unit', ''), figure['label'])) if zh else '[[%s %s]]\n%s' % (figure['value'], figure.get('unit', ''), figure['label'].lower())
        return name or None                  # never the template's stock line ("Key figures"): the product's name
    if kind == 'T13':
        # "Closed to fully open" from the states the pose agent named, in the page's language.
        labels = [' '.join(str(row.get('label') or '').split()) for row in ctx.get('poses') or []]
        labels = [label for label in labels if label]
        if len(labels) >= 2 and labels[0].lower() != labels[-1].lower():
            first, last = _clip(labels[0], 18), _clip(labels[-1], 18)
            return TEXT['zh' if zh else 'en']['T13_from_to'] % (first, last if zh else last.lower())
        return None
    if kind == 'T08':
        if bare(product):
            # nothing was said of what the product is for: "from the workshop to the garage" is a story of its use,
            # and the review holds it to be a claim. The tiles' own titles name the places.
            return name or None
        places = [row['title'] for row in ctx.get('scenes') or [] if row.get('title') and row.get('role') in ('use', 'place', 'other')][:2]
        # "On the job site" and "In the workshop" read "from the job site to the workshop"; zh "在车间" reads "车间".
        places = [re.sub(r'^(on|in|at|by|for|inside|around)\s+', '', place.strip(), flags=re.I) if not zh else re.sub(r'^[在于]', '', place.strip())
                  for place in places]
        # a title that names no place ("In use", "使用中") would read "from use to …"
        places = [place for place in places if place.strip().lower() not in GENERIC_PLACES]
        if len(places) == 2 and places[0].lower() != places[1].lower():
            return ('从%s\n[[到%s]]' % (places[0], places[1])) if zh else 'From %s\n[[to %s]]' % (places[0].lower(), places[1].lower())
        return name or None
    return None


GENERIC_PLACES = {'use', 'in use', 'at work', 'work', 'action', 'in action', 'use case', '使用中', '使用', '使用场景', '工作中'}


def _data_eyebrow(ctx, headline):
    """A kicker that says something: the product's name, or its category when the headline already names it."""
    product = ctx['product']; name = _clip(humanised(product['title']), 24)
    if name and name.lower() not in TPL_PLAIN(headline).lower():
        return name
    category = str(product.get('_category') or '')
    return _clip(category, 24) or None


def TPL_PLAIN(text):
    return re.sub(r'\[\[|\]\]|[{}]', '', str(text or ''))


def _not_printed(title, product):
    """Why a page builder leaves this selling point out, or None: as ``printable`` decides, a record never drafted is
    checked only for a file name's marks (its points were typed by the seller)."""
    return mesh_named(title, product if product.get('drafted') else {}, strict=False)


def _printable_rows(ctx, rows):
    """(feature index, …) rows whose selling point may be printed: never one titled with a mesh name (``mesh_named``)."""
    features = ctx['product']['features']
    return [row for row in rows if row[0] < len(features) and not _not_printed(features[row[0]].get('title'), ctx['product'])]


def _jobs_for(kind, ctx):
    t = dict(TEXT[ctx['language']]); product = ctx['product']; assets = ctx['assets']
    written = (ctx.get('copy') or {}).get(kind) or {}
    for key in ('eyebrow', 'headline'):
        if written.get(key):
            t['%s_%s' % (kind, key)] = written[key]          # the copywriter's line for this product, when it passed its checks
    title = humanised(product['title']); headline = product['headline'] or title      # never the upload's file stem
    if not ctx.get('stock_lines'):
        # No usable line from the copywriter: words from the product's own data, never a stock phrase.
        line = _data_line(kind, ctx)
        if line and not written.get('headline'):
            t['%s_headline' % kind] = line
        if not written.get('eyebrow') and '%s_eyebrow' % kind in t:
            t['%s_eyebrow' % kind] = _data_eyebrow(ctx, t.get('%s_headline' % kind, ''))
    if kind == 'T12':
        # The poster takes a picture the image model made; a studio render of the workpiece stays on the inside pages.
        made = [index for index, row in enumerate(ctx['scenes']) if row['role'] in ('use', 'place', 'other')]
        scene_index = next((index for index in made if usable_scene(ctx['scenes'][index])), None)
        scene = ctx['scenes'][scene_index] if scene_index is not None else None
        brand = ctx.get('brand'); slot = bool(brand and brand.name) and not getattr(brand, 'logo_path', None)      # the logo itself stands for the name
        base = {'headline': _headline(headline, ctx['language'], 40), 'support': _clip(product['subline'], 90) or None, 'brand_slot': slot,
                'labels': {'brand_slot': brand.name if slot else ''}}
        out = []
        if scene:
            name = 'scene%d' % (scene_index + 1)
            box = assets[name].get('_product')
            chips = _chip_rows(ctx)
            # Words over the scene where it leaves room (the template falls back to the band itself when it does not).
            out.append(_job(kind, ctx, mode='overlay', scene=name, focus=scene['focus'], product_box=box, figures=chips or None,
                            assets={name: assets[name]}, **base))
            out.append(_job(kind, ctx, mode='band', scene=name, focus=scene['focus'], product_box=box, assets={name: assets[name]}, **base))
        out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, **base))
        out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, compact=True, **base))
        # The solid poster's text column is narrow: as poster setting does, a clause mark ending a line may go.
        bare = re.sub(r'[，、；：,;:]\n', '\n', base['headline'])
        if bare != base['headline']:
            out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, **{**base, 'headline': bare}))
            out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, compact=True, **{**base, 'headline': bare}))
        out.append(_job(kind, ctx, mode='solid', hero='hero', assets={'hero': assets['hero']}, compact=True,
                        **{**base, 'headline': bare, 'support': None}))
        return out
    if kind == 'T01':
        points = [{'title': _keep_together(_clip(product['features'][index]['title'], 24)), 'body': _body(product['features'][index].get('detail'), 48), 'anchor': point}
                  for index, point in _printable_rows(ctx, ctx['anchors']) if point]
        hero = assets['hero']; wide = hero['width'] / hero['height'] >= .8
        modes = ('band', 'diagonal') if wide else ('diagonal', 'band')
        # A part seen as separate patches (two rivets) may be pointed at on its other patch, if the first line crosses the product.
        other = {index: (ctx.get('alternatives') or {}).get(index) or [] for index, point in _printable_rows(ctx, ctx['anchors']) if point}
        shown = [index for index, point in _printable_rows(ctx, ctx['anchors']) if point]
        points = [{**row, 'other_patch': True} if other[index] else row for row, index in zip(points, shown)]
        swapped = [{**row, 'anchor': other[index][0]} if other[index] else row for row, index in zip(points, shown)]
        out = []
        for count in (4, 3, 2):
            if len(points) >= count:
                for mode in modes:
                    for features in ([points[:count], swapped[:count]] if swapped[:count] != points[:count] else [points[:count]]):
                        out.append(_job(kind, ctx, mode=mode, hero='hero', eyebrow=t['T01_eyebrow'], headline=t['T01_headline'],
                                        features=features, assets={'hero': hero}))
        return out
    if kind == 'T03':
        called = {index for index, _ in ctx['anchors'][:4]} if 'T01' in (ctx.get('kinds') or ()) else set()
        usable = _printable_rows(ctx, ctx['details'])
        fresh = [(index, name) for index, name in usable if index not in called]
        chosen = fresh if len(fresh) >= 2 else usable      # the callouts' points again only when there are not two others
        details = [{'title': _keep_together(_clip(product['features'][index]['title'], 22)), 'body': _body(product['features'][index].get('detail'), 40),
                    'detail': name, 'focus': (ctx.get('focus') or {}).get(name, [.5, .5]), 'part_box': assets[name].get('_part_box')}
                   for index, name in chosen]
        details = [{key: value for key, value in row.items() if value is not None} for row in details]
        out = []
        for count in (3, 2):
            if len(details) >= count:
                use = details[:count]
                fields = dict(mode='split', hero='hero', eyebrow=t['T03_eyebrow'], headline=_headline(title or headline, ctx['language'], 30),
                              assets={'hero': assets['hero'], **{row['detail']: assets[row['detail']] for row in use}}, details=use)
                out.append(_job(kind, ctx, subhead=_clip(product['subline'], 60) or None, **fields))
                out.append(_job(kind, ctx, **fields))
        return out
    if kind == 'T13':
        # The product in the states of its moving parts: one camera, one window, one scale (``pose_assets``); the template
        # sets them side by side or, for a long product, stacked. Three states, else the first and the last.
        poses = [{'asset': row['asset'], 'label': _clip(row.get('label') or '', 18)} for row in ctx.get('poses') or [] if row.get('asset') in assets][:3]
        groups = [poses] + ([[poses[0], poses[-1]]] if len(poses) == 3 else [])
        out = []
        for use in groups:
            if len(use) < 2:
                continue
            for mode in ('auto', 'columns', 'rows'):
                out.append(_job(kind, ctx, mode=mode, eyebrow=t['T13_eyebrow'], headline=_headline(t['T13_headline'], ctx['language'], 40), poses=use,
                                assets={row['asset']: assets[row['asset']] for row in use}))
        return out
    if kind == 'T02':
        rows = [{'title': _keep_together(_clip(product['features'][index]['title'], 22)), 'body': _body(product['features'][index].get('detail'), 48), 'detail': name,
                 'focus': (ctx.get('focus') or {}).get(name, [.5, .5]), 'parts': assets[name].get('_parts') or []} for index, name in _printable_rows(ctx, ctx['macros'])]
        out = []
        for count in (2, 1):
            if len(rows) >= count:
                use = rows[:count]
                out.append(_job(kind, ctx, mode='macro', hero=None, eyebrow=t['T02_eyebrow'], headline=t['T02_headline'], features=use,
                                assets={row['detail']: assets[row['detail']] for row in use}))
        return out
    if kind == 'T04':
        # A scene whose product check passed, other than the poster's when there is one.
        made = [index for index, row in enumerate(ctx['scenes']) if row['role'] in ('use', 'place', 'other')]
        poster = next((index for index in made if usable_scene(ctx['scenes'][index])), None)
        # Only a scene whose product check did not fail; the poster's scene again rather than none.
        order = sorted((index for index in range(len(ctx['scenes'])) if usable_scene(ctx['scenes'][index])), key=lambda index: (index == poster, index))
        if not order:
            return []
        scene = ctx['scenes'][order[0]]; name = 'scene%d' % (order[0] + 1)
        specs = _chip_rows(ctx, 3)
        if len(specs) < 3:           # a word spec (a material) fills the row after the numbers
            for row in product['specs']:
                number, _ = _number_unit(row['value'])
                if number is None and len(specs) < 3:
                    specs.append({'label': _clip(row['label'], 18), 'text': _clip(row['value'], 30), 'icon': _icon_for(row['label'], row['value'])})
        specs = [{key: value for key, value in row.items() if value is not None} for row in specs]
        counts = sorted({min(3, len(specs)), min(2, len(specs))} - {0}, reverse=True)
        return [_job(kind, ctx, scene=name, focus=scene['focus'], eyebrow=t['T04_eyebrow'], headline=t['T04_headline'],
                     scene_product=assets[name].get('_product'), specs=specs[:count], assets={name: assets[name]}) for count in counts]
    if kind == 'T08':
        good = [index for index in range(len(ctx['scenes'])) if usable_scene(ctx['scenes'][index])]
        # A scene whose product check failed is never on the page; with fewer than two others there is no page.
        if len(good) < 2:
            return []
        ranked = good[:3]
        items = [{'asset': 'scene%d' % (index + 1), 'title': _clip(ctx['scenes'][index]['title'], 20),
                  'body': _clause_lines(_clip(ctx['scenes'][index].get('body'), 44), ctx['language'], 8) or None,
                  'focus': ctx['scenes'][index]['focus'], 'box': assets['scene%d' % (index + 1)].get('_product')} for index in sorted(ranked)]
        if not all(row['body'] for row in items) or not (product['features'] or product.get('headline') or product.get('subline')):
            # Captions under some tiles only would break the row's rhythm; and with no selling points or description a line
            # saying what the product does there ("slides out smooth") is a claim the user never made.
            items = [{**row, 'body': None} for row in items]
        out = []
        for count in (3, 2):
            if len(items) >= count:
                use = items[:count]
                # Big photos first: a mosaic of three or two halves; then one wide row per scene (a long tool held across the
                # picture cannot be cropped into a tall tile); tall columns last.
                layouts = (({'mode': 'stagger'}, {'mode': 'stagger', 'stagger_style': 'rows'}, {'mode': 'columns'}) if count == 3 else
                           ({'mode': 'split'}, {'mode': 'split', 'split_dir': 'rows'}, {'mode': 'columns'}))
                for layout in layouts:
                    out.append(_job(kind, ctx, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'], items=use,
                                    assets={row['asset']: assets[row['asset']] for row in use}, **layout))
                    out.append(_job(kind, ctx, semantics='uses', eyebrow=t['T08_eyebrow'], headline=t['T08_headline'],
                                    items=[{**row, 'body': None} for row in use], assets={row['asset']: assets[row['asset']] for row in use}, **layout))
        return out
    if kind == 'T06':
        side = assets['side']; box = side['content_bbox']; end_asset = assets.get('end')
        # Only the sizes the user stated (stated_dimensions), each on the view that shows it: the product face on (or from
        # above when it lies flat), and the same product turned a quarter for the one size that view hides, at the same scale.
        rows = ctx['stated'] if 'stated' in ctx else stated_dimensions(product, ctx.get('dims_mm'), ctx.get('flat'), unit=ctx['unit'])[0]
        if not end_asset:
            rows = [row for row in rows if row['view'] == 'main']
        if not rows or not ctx.get('dims_mm'):
            return []
        (across, _), _ = view_axes(ctx['dims_mm'], ctx.get('flat'))

        def dimension(row, edge, at):
            """A dimension line along one edge of a view's content box: 'below', 'left' or 'right' of the product."""
            b = edge
            start, end, side_dir, place = {'below': ([b[0], b[3]], [b[2], b[3]], [0, 1], b[3] + .07),
                                           'left': ([b[0], b[1]], [b[0], b[3]], [-1, 0], b[0] - .07),
                                           'right': ([b[2], b[1]], [b[2], b[3]], [1, 0], b[2] + .07)}[at]
            letter = t[row['key']][:1].upper() if ctx['language'] == 'en' else row['key'][:1].upper()
            return {'key': row['key'], 'key_letter': letter, 'name': t[row['key']], 'value': row['value'], 'unit': row['unit'], 'from': start,
                    'to': end, 'side': side_dir, 'at': place, **({'alt': row['alt']} if row.get('alt') else {})}
        main = [row for row in rows if row['view'] == 'main']
        dims = [dimension(row, box, 'below' if row['axis'] == across else 'left') for row in sorted(main, key=lambda row: row['axis'] == across)]
        end = None
        turned = [row for row in rows if row['view'] == 'end']
        if turned:
            ebox = end_asset['content_bbox']; flat = bool(ctx.get('flat'))
            # The two views share an edge: the length across (a flat tool, seen from above and then from the front) or the
            # height (anything else, face on and then from the end); its pixels set the turned view's scale.
            if flat:
                scale = (box[2] - box[0]) * side['width'] / max(1e-6, (ebox[2] - ebox[0]) * end_asset['width'])
            else:
                scale = (box[3] - box[1]) * side['height'] / max(1e-6, (ebox[3] - ebox[1]) * end_asset['height'])
            end = {'asset': 'end', 'scale': round(scale, 5), 'at': 'below' if flat else 'right',
                   'dimensions': [dimension(row, ebox, 'right' if flat else 'below') for row in turned]}
        # Figures on the product first (the Amazon dimension pages); a table when the page is too narrow for them.
        modes = ('onproduct', 'table')
        extra = [{'label': _clip(row['label'], 18), 'value': row['value']} for row in product['specs']
                 if not any(word in row['label'].lower() for word in ('高', '宽', '长', '深', '厚', '直径', 'height', 'width', 'length', 'depth',
                                                                      'thick', 'diameter', 'size', 'dimension', '尺寸'))][:3]
        return [_job(kind, ctx, mode=mode, hero='side', eyebrow=_clip(title, 24) or t['T06_eyebrow'], headline=t['T06_headline'], dimensions=dims,
                     end=end, specs=extra or None, disclaimer=t['table_note'].rstrip('。.'), table_note=t['table_note'] if mode == 'table' else None,
                     assets={'side': side, **({'end': end_asset} if end else {})}) for mode in modes]
    if kind == 'T05':
        import components
        items = [{'asset': 'hero', 'name': _clip(title or t['main'], 20), 'code': _clip(product.get('model'), 20) or None, 'qty': 1}]
        for index, part in enumerate(ctx['parts']):
            role = components.ROLES.get(part.get('role'), ('配件', 'Accessory'))
            items.append({'asset': 'part%d' % index, 'name': _clip(part.get('name') or role[ctx['language'] == 'en'], 20),
                          'code': _clip(part.get('sku'), 20) or None, 'qty': part.get('quantity') or None})
        items = [{key: value for key, value in row.items() if value is not None} for row in items]
        counts = sorted({min(len(items), count) for count in (8, 6, 4, 3)}, reverse=True)
        jobs = [_job(kind, ctx, headline=t['T05_headline'], items=items[:count], note=t['T05_note'],
                     assets={row['asset']: assets[row['asset']] for row in items[:count]}) for count in counts]
        if len(items) >= 6:             # many pieces: laid out flat, at their relative sizes
            jobs.insert(1, {**jobs[0], 'mode': 'knolling'})
        return jobs
    return []

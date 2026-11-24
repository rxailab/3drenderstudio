"""The listing set, laid out with the brand-kit templates (``brandkit/templates``).

The listing's pictures come from the product's own 3D model: a cut-out of the whole product, close-ups
of the parts its selling points name, a straight-on view to measure, the kit's components. Its usage
scenes are made by the image model from those renders, with the product locked by a mask
(``scene_lock``), so scene generation is now one step of building the set instead of a tool of its own.

This module turns those pictures and the product record into jobs for the templates, lays every page
out in a headless browser (``brandkit_render``), checks the measured layout, and falls back to a
lighter version of a page (fewer labels, another arrangement) when a check fails.

Templates (each one rule-driven HTML; see ``brandkit/templates/T*.json`` for its reference map):
T12 poster · T01 callouts · T03 product + detail column · T13 poses (the product in the states of its moving parts:
pliers closed / open, a caliper closed / measuring / open, from the pose agent ``articulation``) · T02 dark close-ups ·
T04 scene + figures band · T08 usage scenes · T06 dimensions · T05 in the box. T07, T09, T10 and T11 need evidence a 3D
model does not carry (module levels, test results, connection maps, a model range) and are not built.

Since the split this module is a facade: the work lives in listing_kinds, listing_copy, listing_assets, listing_scenes,
listing_theme, listing_dimensions, listing_jobs and listing_layout, and every name they define is re-exported here, private
ones included, so callers and tests keep using ``brandkit_set.<name>``. What stays here are the steps that ask a model
(locate, check_points, localise, captions, seen_captions) with their prompts: a request is labelled with the module and
function that sent it (ai_client._caller, brandkit_review._ask), and that label is what data/ai_usage.jsonl and the cost
analysis group by and what brandkit_review.MODEL_KNOBS picks the model by. Moving them would rename them there.

listing_scenes.make_scenes and scene_supply look up image_model_ready, scene_key, scene_briefs, captions and seen_captions
on this module when they run, so patching those here still reaches a build. Other names are used where they are
defined: patch them on their own module (POSES_SHOWN on listing_scenes, _uncut on listing_assets, and jobs_for as
listing_layout.jobs_for, the name lay_out calls).
"""

import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw


# every name of the split modules, private ones included (see above)
from listing_kinds import KINDS, ORDER, AUTO, BLOCKED, CANVAS, ASPECTS, TEXT, anchored, pointed, NO_FEATURES, requirements
from listing_copy import (
    MODEL_SUFFIXES, FILE_TOKENS, SMALL_WORDS, humanised, PRIMITIVES, _words, _mesh_key, mesh_named, printable, _HAN, _NUMERALS,
    _arabic, _strings, _body, _keep_together, _clip, _headline
)
from listing_assets import (
    _hull, shape, flatten, pose_assets, _visible_patch, part_region, _cuts, _cut_parts, _uncut, part_map, part_box, part_focus,
    remap, scene_box, scene_asset
)
from listing_scenes import (
    MAX_SCENES, SCENE_PEOPLE, default_scenes, validate_scenes, image_model_ready, scene_supply, SCENE_PLAN, scene_key,
    legacy_scene_key, carry_over, scene_briefs, usable_scene, swappable_scenes, recheck_scenes, _check_fields, HANGS, STANDS_ON,
    hangs_on_wall, _longest_m, POSES_SHOWN, _scene_states, _with_moving_parts, _fit_line, _clause_lines, PLACE_WORDS,
    _TITLE_FILLER, _same_place, CAPTIONED, PLACE_ONLY, bare, caption_way, _in_parallel, _free_folder, SCENE_TRIES, SCENE_WORKERS,
    make_scenes
)
from listing_theme import (
    BADGED, LOGO_PAGES, LEGAL_PAGES, badges_on, brand_usage, marks_drawn, marks_missing, MARK_NOTES, mark_note, DARK_TEMPLATES,
    ON_DARK, _palette, INKS, SMALL_TEXT, ink_on
)
from listing_dimensions import (
    dimensions_mm, LENGTH_MM, _stated_lengths, _stated_measure, _measure, DIM_WORDS, DIM_GENERIC, DIM_LETTERS, SIZE_UNITS,
    UNIT_SHOWN, _SIZE_RE, PROPORTION_LIMIT, _word_re, _label_keys, _sizes_in, view_axes, stated_dimensions
)
from listing_jobs import (
    _job, ICON_WORDS, _icon_for, _chip_rows, _number_unit, jobs_for, CROP_SHIFTS, _recropped, _data_line, GENERIC_PLACES,
    _data_eyebrow, TPL_PLAIN, _not_printed, _printable_rows, _jobs_for
)
from listing_layout import SOFT_CHECKS, hard_issues, _measured, scene_vetoes, _better, _cut_text, lay_out


LOCATOR = ('You look at a product photo for an online listing and say where each selling point is on the product. For each '
           'numbered selling point, give the point (x, y as fractions of the picture width and height, from the top left) at the '
           'centre of the part it is about: a display, a button, a jaw, a cutter, a handle, a battery, a guard. A feature that a part '
           'delivers points at that part: measuring modes at the jaws and depth rod, a unit switch or zero at its button, a motor at '
           'the motor housing, a material at the part made of it. Only when no part shows it (an accuracy, a warranty, a speed with no '
           'visible source) or that part cannot be seen from this side, set visible to false.')


def locate(picture_path, picture, product, cache):
    """Where the selling points without a usable part are on the main picture, by the vision model: {index: point}, with
    the mesh under the point as the feature's part when the model has more than one. Only points the part grid confirms are on
    the product are kept. ``cache`` ({key: answer}) keeps the answer with the project. Returns (features, notes)."""
    features = [dict(row) for row in product['features']]
    wanted = [index for index in range(len(features)) if index not in anchored(product) and features[index].get('title')]
    if not wanted or picture is None:
        return features, []
    key = hashlib.sha256(json.dumps([str(picture_path), [features[index]['title'] for index in wanted]], ensure_ascii=False).encode()).hexdigest()[:16]
    answer = cache.get(key)
    if answer is None:
        try:
            import brandkit_review
            _, vision = brandkit_review._configs()
        except Exception:
            vision = None
        if not vision:
            return features, []
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['points'],
                  'properties': {'points': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                                     'required': ['number', 'visible', 'x', 'y'],
                                                                     'properties': {'number': {'type': 'integer'}, 'visible': {'type': 'boolean'},
                                                                                    'x': {'type': 'number'}, 'y': {'type': 'number'}}}}}}
        try:
            answer = brandkit_review._ask(vision, LOCATOR, {'product': product.get('title', ''), 'selling_points': [
                {'number': number + 1, 'title': features[index]['title'], 'detail': features[index].get('detail', '')} for number, index in enumerate(wanted)]},
                schema, image_path=str(picture_path))
        except Exception as exc:
            return features, ['没能在主图上找出卖点的位置（%s），这些卖点不画引线。' % str(exc)[:80]]
        cache[key] = answer
    several = len(set(name for name in picture.objects)) > 1
    found = 0
    for row in answer.get('points') or []:
        number = row.get('number')
        if not isinstance(number, int) or not 1 <= number <= len(wanted) or not row.get('visible'):
            continue
        x, y = float(row.get('x', -1)), float(row.get('y', -1))
        if not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        cell = picture.cells[min(picture.ny - 1, int(y * picture.ny)) * picture.nx + min(picture.nx - 1, int(x * picture.nx))]
        if not isinstance(cell, int) or cell < 0 or cell >= len(picture.objects):
            continue                     # not on the product: a guess the part grid does not back
        index = wanted[number - 1]
        features[index]['point'] = [round(x, 4), round(y, 4)]
        if several:
            features[index]['parts'] = [picture.objects[cell]]
        found += 1
    return features, (['%d 条卖点的位置由视觉模型在主图上找出。' % found] if found else [])


POINT_CHECK = ('You check the callout points of a product photo for an online listing before the lines are drawn. The picture has '
               'numbered markers: a ring whose centre is the point, with its number beside it. For each numbered point look at the '
               'exact spot at the ring centre and say whether it lies on the part of the product the selling point given with that '
               'number is about (on: true), or somewhere else: another part, the joint between two parts, the background (on: false). '
               'Judge by the selling point\'s own words: a cutting edge is the sharpened edge of the jaws, a grip or handle is the '
               'handle\'s covering, a pivot is the rivet or joint, a display is the screen. When a point is wrong, give x, y (fractions '
               'of the picture width and height from the top left, ignoring the markers) at the centre of the right part, or visible '
               'false when that part cannot be seen from this side. When you cannot tell, answer on: false.')


def candidate_points(product, picture):
    """[(feature index, point on the main picture)]: every spot a callout line would be drawn to, as the build draws them
    (a point found on the picture, else the visible middle of the feature's parts)."""
    out = []
    for index in anchored(product):
        feature = product['features'][index]
        if feature.get('point'):
            out.append((index, [float(value) for value in feature['point']]))
            continue
        found, share = picture.anchor(feature['parts']) if picture else (None, 0)
        if found and share > .0003:
            out.append((index, [float(value) for value in found]))
    out += [(index, [float(value) for value in product['features'][index]['point']]) for index in pointed(product)]
    return sorted(out)


def _marked(picture_path, candidates, target):
    """The main picture with each candidate point ringed and numbered, for the vision model (and the user) to check."""
    from PIL import ImageFont
    with Image.open(picture_path) as opened:
        image = opened.convert('RGBA')
    ground = Image.new('RGBA', image.size, (255, 255, 255, 255)); ground.alpha_composite(image); image = ground.convert('RGB')
    image.thumbnail((1280, 1280))
    width, height = image.size; pen = ImageDraw.Draw(image)
    radius = max(10, round(min(width, height) * .022)); size = max(18, round(min(width, height) * .032))
    try:
        font = ImageFont.truetype(str(Path(__file__).resolve().parent / 'brandkit' / 'fonts' / 'Archivo-Variable.ttf'), size)
    except OSError:
        font = ImageFont.load_default(size)
    for number, (_, point) in enumerate(candidates, 1):
        x, y = point[0] * width, point[1] * height
        pen.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(230, 20, 60), width=max(3, radius // 4))
        pen.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(230, 20, 60))
        label = str(number); box = pen.textbbox((0, 0), label, font=font)
        lx = x + radius + 4 if x + radius + 8 + box[2] < width else x - radius - 8 - box[2]
        ly = min(max(0, y - radius - box[3]), height - box[3] - 4)
        pen.rectangle((lx - 4, ly - 2, lx + box[2] + 4, ly + box[3] + 4), fill=(230, 20, 60))
        pen.text((lx, ly), label, fill=(255, 255, 255), font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, 'PNG')
    return target


def check_points(picture_path, picture, product, original, cache, folder):
    """Every callout point checked once by the vision model before a line is drawn to it.

    The pliers' "Angled cutting edges" line ended at the joint of jaw and handle and "Cylinder grip" at the rivet: the
    locator's guess and a mesh's middle were drawn as they came. One call now sees the main picture with all candidate
    points ringed and numbered (``_marked``) and says per point whether it is on the named feature. A point it rejects is
    dropped; the corrected spot it gives is taken only where the feature's own part is under it (the part grid backs it).
    A dropped feature keeps the parts the user chose (its close-ups) but gets no line (``no_leader``); one the locator
    placed goes back to how the user wrote it. The answer is kept in ``cache`` (the listing's ``located``), keyed by the
    picture and the points, so a rebuild asks nothing. ``original`` is the features before ``locate``.
    Returns (features, notes)."""
    features = [dict(row) for row in product['features']]
    candidates = candidate_points(product, picture)
    if not candidates or picture is None:
        return features, []
    key = 'check-' + hashlib.sha256(json.dumps([str(picture_path), [(features[index]['title'], [round(value, 3) for value in point])
                                                                    for index, point in candidates]], ensure_ascii=False).encode()).hexdigest()[:16]
    answer = cache.get(key)
    if answer is None:
        try:
            import brandkit_review
            _, vision = brandkit_review._configs()
        except Exception:
            vision = None
        if not vision:
            return features, []
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['points'],
                  'properties': {'points': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                                     'required': ['number', 'on', 'visible', 'x', 'y'],
                                                                     'properties': {'number': {'type': 'integer'}, 'on': {'type': 'boolean'},
                                                                                    'visible': {'type': 'boolean'}, 'x': {'type': 'number'},
                                                                                    'y': {'type': 'number'}}}}}}
        marked = _marked(picture_path, candidates, Path(folder) / 'points-check.png')
        try:
            answer = brandkit_review._ask(vision, POINT_CHECK, {'product': humanised(product.get('title', '')), 'points': [
                {'number': number, 'selling_point': features[index]['title'], 'detail': features[index].get('detail', '')}
                for number, (index, _) in enumerate(candidates, 1)]}, schema, image_path=str(marked))
        except Exception as exc:
            return features, ['没能核对引线位置（%s），引线未经核对。' % str(exc)[:80]]
        if not isinstance(answer, dict) or not isinstance(answer.get('points'), list):
            return features, ['核对引线位置没有得到可用的回答，引线未经核对。']
        cache[key] = answer
    verdicts = {row.get('number'): row for row in answer.get('points') or [] if isinstance(row, dict)}
    dropped, moved = [], 0
    for number, (index, point) in enumerate(candidates, 1):
        row = verdicts.get(number) or {}
        if row.get('on') is True:
            continue
        own = set((original[index] if index < len(original) else {}).get('parts') or features[index].get('parts') or ())
        try:
            x, y = float(row.get('x', -1)), float(row.get('y', -1))
        except (TypeError, ValueError):
            x = y = -1
        if row.get('visible') and own and 0 <= x <= 1 and 0 <= y <= 1:
            cell = picture.cells[min(picture.ny - 1, int(y * picture.ny)) * picture.nx + min(picture.nx - 1, int(x * picture.nx))]
            if isinstance(cell, int) and 0 <= cell < len(picture.objects) and picture.objects[cell] in own:
                features[index]['point'] = [round(x, 4), round(y, 4)]; moved += 1
                continue
        dropped.append(features[index]['title'])
        before = original[index] if index < len(original) else {}
        features[index].pop('point', None)
        features[index]['parts'] = list(before.get('parts') or [])
        features[index]['no_leader'] = True
    notes = []
    if dropped:
        notes.append('视觉模型核对引线时发现 %d 处没有指在所写的部位上，已去掉这些引线：%s。' % (len(dropped), '、'.join('「%s」' % title for title in dropped[:4])))
    if moved:
        notes.append('%d 条引线按核对结果移到了对应部件上。' % moved)
    return features, notes


LOCALISER = ('You fit the words of a product listing to the listing images: lines in another language are translated into the '
             'images\' language, and lines longer than max_chars are rewritten shorter in the same language, keeping their meaning as '
             'a complete phrase (never cut off mid-sentence). Translate faithfully '
             'and concisely, as a seller would write them on an Amazon listing; keep every number, unit, model code and brand name exactly '
             'as written; do not add claims or words that are not there. Each line has max_chars, the space the page gives it: stay within '
             'it, shortening to the essential words rather than cutting a word.')


def localise(product, scenes, parts, cache):
    """The record's words in the language the pages are printed in: on an English set, every line written in Chinese
    (a product name, a selling point, a scene caption made for an earlier Chinese set) is translated by the text model,
    keeping its numbers. ``cache`` ({language: {source: text}}) is updated in place and kept with the project, so a
    line is translated once. Returns (product, scenes, parts, notes); without a text model the lines stay as written
    and a note says so. A Chinese set keeps English words (model codes, units, terms) as they are."""
    language = product.get('language')
    if language not in ('en', 'zh'):
        return product, scenes, parts, []
    known = cache.setdefault(language, {})
    limits = _strings(product, scenes, parts)
    # Lines in the wrong language (Chinese on an English set) and lines longer than the space the pages give them.
    foreign = lambda text: language == 'en' and bool(_HAN.search(text))
    wanted = sorted(text for text in limits if text not in known and (foreign(text) or len(text) > limits[text]))
    notes = []
    if wanted:
        try:
            import brandkit_review
            config, _ = brandkit_review._configs()
        except Exception:
            config = None
        if not config:
            if any(foreign(text) for text in wanted):
                notes.append('产品信息里有中文，但图片文字选的是英文，且没有配置文本模型来翻译：这些中文内容不会印在英文图上，请填写英文或配置文本模型。')
        else:
            schema = {'type': 'object', 'additionalProperties': False, 'required': ['lines'],
                      'properties': {'lines': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['source', 'text'],
                                                                         'properties': {'source': {'type': 'string'}, 'text': {'type': 'string'}}}}}}
            digits = lambda text: set(re.findall(r'\d+(?:\.\d+)?', unicodedata.normalize('NFKC', text)))
            long = {}
            for attempt in (1, 2):              # a line over its space is asked once more, more strictly
                ask = [text for text in wanted if text not in known]
                if not ask:
                    break
                try:
                    answer = brandkit_review._ask(config, LOCALISER, {'language': 'English' if language == 'en' else 'Simplified Chinese', 'product': product.get('title', ''),
                                                                      'strict_length': attempt == 2,
                                                                      'lines': [{'source': text, 'max_chars': limits[text]} for text in ask]}, schema)
                except Exception as exc:
                    notes.append('翻译产品文字时文本模型没有回应（%s）。' % str(exc)[:80]); break
                for row in answer.get('lines') or []:
                    source, text = row.get('source'), ' '.join(str(row.get('text', '')).split())
                    # Every number written in figures stays; a number in the translation must come from the line ("四" may become "4").
                    kept = digits(source) <= digits(text) and digits(text) <= digits(source) | digits(_arabic(source))
                    if source in ask and text and not (language == 'en' and _HAN.search(text)) and kept:
                        if len(text) <= limits[source]:
                            known[source] = text
                        else:
                            long[source] = text
            for source, text in long.items():   # still too long: the translation beats the Chinese line; a long original stays itself
                if foreign(source) or len(text) < len(source):
                    known.setdefault(source, text)
            missing = [text for text in wanted if text not in known and foreign(text)]
            if missing:
                notes.append('有 %d 条产品文字没能译成英文，这些文字不会印在英文图上：%s。' % (len(missing), '、'.join(missing[:3])))
    # A line that could not be translated is left off the English pages (a blank field falls back to the template's own words).
    swap = lambda text: known.get(text, '' if foreign(text) else text) if isinstance(text, str) else text
    product = copy.deepcopy(product)
    for key in ('title', 'headline', 'subline'):
        product[key] = swap(product.get(key))
    if isinstance(product.get('usecase'), dict):
        product['usecase']['headline'] = swap(product['usecase'].get('headline'))
    for key, fields in (('features', ('title', 'detail')), ('specs', ('label', 'value')), ('figures', ('label', 'unit'))):
        product[key] = [{**row, **{field: swap(row.get(field)) for field in fields}} for row in product.get(key) or []]
    scenes = [{**row, 'title': swap(row.get('title')), 'body': swap(row.get('body'))} for row in scenes or []]
    parts = [{**row, 'name': swap(row.get('name')), 'caption': swap(row.get('caption'))} for row in parts or []]
    return product, scenes, parts, notes


def captions(project, product, briefs):
    """A short title and line for each scene, in the listing's language: by the copy model when one is
    configured, else plain titles by what the scene shows."""
    language = product['language']; fixed = TEXT[language]['scene_titles']
    rows = [{'title': fixed[('use', 'place', 'other').index(item['role'])], 'body': ''} for item in briefs]
    try:
        import brandkit_review
        import scene_stylist
        config = brandkit_review.model_for(scene_stylist._config(None), 'brandkit_set.captions')   # AI_COPY_MODEL when named (v40)
        if not config:
            return rows
        limit = (8, 15) if language == 'zh' else (22, 44)
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['scenes'],
                  'properties': {'scenes': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['title', 'body'],
                                                                       'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}}}}}}
        facts = {'product': product['title'] or project.get('name', ''), 'language': 'Simplified Chinese' if language == 'zh' else 'English',
                 'title_max_chars': limit[0], 'line_max_chars': limit[1],
                 'scenes': [str(item['proposal'].get('scene', ''))[:600] for item in briefs],
                 # nothing is known of what the product is for: the title names the place, and there is no line
                 **({'titles_name': 'the place only ("In the garage"), never a task, a use or what anyone does there; leave every line empty'}
                    if bare(product) else {})}
        system = ('You write captions for product listing images. For each scene description, write a short title naming the moment or '
                  'place of use (not words like "展示"/"showcase" that describe the photo) and one short line saying what the buyer sees. '
                  'Describe only what the scene shows; no numbers, no benefits or claims, no prices or brands. Respect the character limits '
                  'strictly; different scenes get clearly different titles. In Chinese, write the line as two short clauses joined by "，", '
                  'each at most 8 characters, so it can be set one clause per line.')
        answer = scene_stylist._ask(config, system, facts, schema)
        for row, made in zip(rows, answer.get('scenes', [])):
            title = ' '.join(str(made.get('title', '')).split()); body = _fit_line(' '.join(str(made.get('body', '')).split()), limit[1])
            title = title if len(title) <= limit[0] else ''
            if title:
                row.update(title=title, body='' if bare(product) else body)
    except Exception:
        pass
    return rows


SEEN = ('You caption one picture of a product listing set: a scene the image model made with the product in it. Look at the '
        'picture. Title: what `shows` asks for (the task being done, or the place), as a shopper would name it ("Stripping wire", '
        '"At the desk", "Weekend away"), only what is in view (no "office" without office things in view, no "job site" for a '
        'bench). Line: what the product is doing for its owner there, as the picture shows it ("Stands upright, ready to grab"), not '
        'a description of the room. The title is about the product: never what people elsewhere in the picture are doing. No numbers, prices, brands or claims the picture does not show. The title must name a different '
        'place or task from every title in titles_already_used (workshop, workbench and bench are the same place; so are garage and '
        'shed), and the line must not repeat lines_already_used. Respect the character limits strictly. In Chinese, write the line as '
        'two short clauses joined by "，", each at most 8 characters.')
SHOWS = {'use': 'the product at work: title the task being done', 'place': 'the product at rest where it is kept or used: title the place',
         'other': 'a different setting from the other pictures: title the place or the task',
         'bare': 'the place the picture is set in: title the place only ("In the garage", "On site"), never a task, a use or what anyone is doing; leave the line empty'}
SEEN_WORKERS = 3      # vision calls captioning scenes at once (one per picture; a set has at most three scenes)


def seen_captions(product, rows):
    """Each scene captioned from the picture itself by the vision model (the brief it was made from may not be what came
    out). Rows the model does not answer for keep their captions. Returns (rows, how many were rewritten).

    The pictures are asked about at once (one vision call each, several seconds apiece), each shown the captions of the rows
    already captioned in this language. The answers are then taken in row order against every caption before them, as when
    they were asked one by one; an answer that clashes only with a caption it was not shown (that row was being asked at the
    same time) is asked once more, alone, with all of them in view. So no two scenes are titled with the same place, and only
    a clash costs a second, serial call."""
    try:
        import brandkit_review
        _, vision = brandkit_review._configs()
    except Exception:
        return rows, 0
    if not vision:
        return rows, 0
    language = product['language']; limit = (8, 15) if language == 'zh' else (22, 44)
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['title', 'body'],
              'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}}}
    done_here = lambda row: ((row.get('captions') or {}).get(language) or {}).get('how') == caption_way(product)

    def ask(row, taken, said):
        """The vision model's (title, body) for ``row``, tidied; None when the call failed."""
        try:
            facts = {'product': product['title'], 'language': 'Simplified Chinese' if language == 'zh' else 'English',
                     'title_max_chars': limit[0], 'line_max_chars': limit[1], 'titles_already_used': sorted(taken),
                     'lines_already_used': sorted(said),
                     # nothing is known of what the product is for: the title names the place, never a task
                     'shows': SHOWS['bare'] if bare(product) else SHOWS.get(row.get('role'), SHOWS['other'])}
            # named: from a worker thread _ask would log (and pick the model of) this inner function instead
            answer = brandkit_review._ask(vision, SEEN, facts, schema, image_path=row['path'], caller='brandkit_set.seen_captions')
            title = ' '.join(str(answer.get('title', '')).split()); body = _fit_line(' '.join(str(answer.get('body', '')).split()), limit[1])
            return title, '' if bare(product) else body
        except Exception:
            return None

    def fits(answer, taken, said):
        title, body = answer
        return bool(title and len(title) <= limit[0] and title not in taken and not any(_same_place(title, other) for other in taken)
                    and (not body or body not in said) and not re.search(r'\d', title + body))

    # what every question is shown: the rows captioned before this call, wherever they sit in the set (a later one keeps its
    # title, so an earlier row must not take it either)
    shown = {row['title'] for row in rows if done_here(row)}; shown_said = {row.get('body') or '' for row in rows if done_here(row)}
    asking = [index for index, row in enumerate(rows) if not done_here(row)]
    answers = dict(zip(asking, _in_parallel([lambda row=rows[index]: ask(row, shown, shown_said) for index in asking], SEEN_WORKERS)))
    out = []; done = 0; taken = set(); said = set()
    for index, row in enumerate(rows):
        if index in answers:
            answer = answers[index]
            if answer and not fits(answer, taken | shown, said | shown_said) and fits(answer, shown, shown_said):
                answer = ask(row, taken | shown, said | shown_said)          # clashed only with an answer it could not see
            if answer and fits(answer, taken | shown, said | shown_said):
                title, body = answer
                captions = {**(row.get('captions') or {}), language: {'title': title, 'body': body, 'how': caption_way(product)}}
                row = {**row, 'title': title, 'body': body, 'caption': caption_way(product), 'captions': captions}; done += 1
        taken.add(row['title']); said.add(row.get('body') or ''); out.append(row)
    return out, done

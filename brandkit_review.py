"""Supervision of the brand-kit listing set by the copy and vision models.

Three roles, each bounded and each with a plain fallback when no model is configured:

* Copywriter (text model): writes each page's eyebrow and headline for this product, from the
  product record alone, within the template's length. A line with a number the user did not give
  is refused, and the template's stock line is kept.
* Art director (vision model): looks at every finished page (and, for scene pages, at the product's
  own render) and scores it against the quality gate of the design pack (``brandkit/briefs.json``
  gives each template's task and what it must avoid). It names at most three problems, each with a
  fix taken from a fixed list of edits the code can apply and check: shorten or remove a line, drop
  an item, move a scene's crop, use another scene, try the next arrangement. The page is laid out
  again, measured again and looked at again: two rounds of revision at most, the best page kept.
  A page that no model looked at is 'not_verified', never 'passed'.
* Set review (vision model): one look at the whole set on a contact sheet, for repeated pictures,
  inconsistent styling or a page that adds nothing. It reports; it does not delete pages.

The models never write pixels or layout: every change goes through the templates and their checks.
"""
import copy
import json
import threading
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
BRIEFS = ROOT / 'brandkit' / 'briefs.json'
PASS_SCORE = 80
MAX_REVISIONS = 2
MAX_ISSUES = 3
CRITERIA = {'product': 25, 'hierarchy': 20, 'composition': 20, 'task': 15, 'color': 10, 'independence': 10}
VETOES = ('product_distorted', 'product_cropped', 'text_unreadable', 'text_broken', 'claim_not_in_facts', 'scene_product_mismatch', 'wrong_pointer')
ACTIONS = ('shorten_text', 'remove_text', 'drop_item', 'refocus_scene', 'swap_scene', 'next_layout', 'none')
TEXT_FIELDS = ('eyebrow', 'headline', 'support', 'subhead', 'table_note', 'note')
LISTS = {'T01': ('features', 2), 'T02': ('features', 1), 'T03': ('details', 2), 'T04': ('specs', 2), 'T05': ('items', 1), 'T08': ('items', 2)}
ITEM_TEXT = ('title', 'body', 'name', 'label', 'text')
COPY_KINDS = ('T01', 'T02', 'T03', 'T04', 'T08')
COPY_LIMITS = {'zh': {'eyebrow': 6, 'headline': 14}, 'en': {'eyebrow': 18, 'headline': 34}}
_LOCK = threading.Lock()
_BRIEFS = None

ART_DIRECTOR = (
    'You are the art director reviewing one finished e-commerce listing image before it is published. '
    'Judge only what you see in CURRENT_RENDER (and, when given, compare the product with REFERENCE_IMAGE, the product\'s own render). '
    'Score each criterion within its maximum: product (presentation and fidelity), hierarchy (text hierarchy and readability, also at a '
    '360 px wide phone thumbnail), composition (balance, white space, alignment), task (does the page do its one job, see template.goal), '
    'color (restrained, no fake textures or effects), independence (does the layout suit this product rather than look forced). '
    'List a veto only for a visible, serious fault: product_distorted, product_cropped (a working end, handle, wheel or similar cut off '
    'without being a declared close-up), text_unreadable, text_broken (overflow, clipped, wrong line break inside a word or phrase), '
    'claim_not_in_facts (text says something the facts do not support), scene_product_mismatch (the product in a scene is not the one in '
    'REFERENCE_IMAGE), wrong_pointer (a callout line points at the wrong part). '
    'Then name at most three of the most important problems, each with the region, the visible evidence, and one fix chosen from the '
    'allowed actions (editable lists the exact targets): shorten_text (target a text field, give the new shorter text, meaning kept, no '
    'new facts or numbers), remove_text (target an optional text field), drop_item (target a list and index; keeps at least the minimum), '
    'refocus_scene (target a scene, give focus x,y in 0..1 of the scene picture for its crop centre), swap_scene (use another scene picture), '
    'next_layout (try the template\'s next arrangement), none (no automatic fix). Do not ask for things the actions cannot do. '
    'Never praise; be specific. Write evidence and summary in the language of the page; quote page text with 「」, never with double quotes.')
COPYWRITER = (
    'You write the small eyebrow label and the headline of each page of a product listing set. Use only the facts given: the product, '
    'its selling points and specs. Each page has one job (goal). Write in the requested language, within the character limits, no '
    'numbers unless the same number is in the facts, no prices, no superlatives you cannot support, no brand names. Chinese headlines may '
    'contain one "\\n" at a natural pause. Keep the eyebrow a plain label (e.g. "细节特写", "Up close").')
SET_REVIEW = (
    'You review a whole e-commerce listing set shown as a contact sheet (pages numbered in reading order). Say whether the set reads as one '
    'brand system, whether any two pages repeat the same picture or message, and which page adds least. Be brief and specific.')


def briefs():
    global _BRIEFS
    with _LOCK:
        if _BRIEFS is None:
            try:
                _BRIEFS = json.loads(BRIEFS.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                _BRIEFS = {}
        return _BRIEFS


def _configs():
    import scene_stylist
    return scene_stylist._config(None), scene_stylist._config(True)


def available():
    """(text model ready, vision model ready)."""
    try:
        text, vision = _configs()
    except Exception:
        return False, False
    return bool(text), bool(vision)


def _repaired(text):
    """JSON a model wrote with plain double quotes inside its strings: a quote that is not next to JSON structure
    (``: , } ]`` after a closing one) is taken as part of the text and escaped."""
    out = []; inside = False
    for index, char in enumerate(text):
        if char == '"' and (index == 0 or text[index - 1] != '\\'):
            after = text[index + 1:].lstrip()[:1]
            if not inside:
                inside = True; out.append(char); continue
            if after in (':', ',', '}', ']', ''):
                inside = False; out.append(char); continue
            out.append('\\"'); continue
        out.append(char)
    return ''.join(out)


def _decoded(value, schema):
    """A model's answer with any nested list or object it sent as a JSON string read back into one."""
    kind = schema.get('type')
    if kind in ('array', 'object') and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            try:
                value = json.loads(_repaired(value))
            except ValueError:
                return value
    if kind == 'object' and isinstance(value, dict):
        return {key: _decoded(child, schema.get('properties', {}).get(key, {})) for key, child in value.items()}
    if kind == 'array' and isinstance(value, list):
        return [_decoded(child, schema.get('items', {})) for child in value]
    return value


def _ask(config, system, facts, schema, image_path=None, reference_path=None):
    """One call; an answer that does not fit the schema is asked for once more."""
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, image_path=image_path, reference_path=reference_path,
                       label_images=bool(image_path), max_tokens=3000)
    for attempt in (1, 2):
        output = _decoded(ai._response(ai._post(config, body), config, ai.TOOL_NAME), schema)
        try:
            ai._check_schema(output, schema)
            return output
        except ai.SchemaValidationError:
            if attempt == 2:
                raise


def facts_of(product):
    """What the product record says: the only source of words and numbers on the pages."""
    return {'title': product['title'], 'model': product.get('model', ''), 'headline': product['headline'], 'subline': product['subline'],
            'selling_points': [{'title': row['title'], 'detail': row.get('detail', '')} for row in product['features']],
            'specs': product['specs'], 'figures': product['figures'], 'longest_side_mm': product.get('size_mm')}


def _numbers(text):
    import layer_compose
    return layer_compose.numbers_in(text)


def grounded(text, facts):
    """Every number in ``text`` appears in the facts (a line without numbers is grounded)."""
    wanted = _numbers(text)
    return not wanted or wanted <= _numbers(json.dumps(facts, ensure_ascii=False))


# ---- copywriter ----

def write_copy(product, kinds):
    """{kind: {'eyebrow', 'headline'}} written for this product, or {} when no text model answers.
    Returns (copy, note)."""
    kinds = [kind for kind in kinds if kind in COPY_KINDS]
    if not kinds:
        return {}, None
    text, _ = _configs()
    if not text:
        return {}, None
    language = product['language']; limits = COPY_LIMITS[language]; facts = facts_of(product)
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['pages'],
              'properties': {'pages': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['template', 'eyebrow', 'headline'],
                                                                  'properties': {'template': {'type': 'string', 'enum': kinds}, 'eyebrow': {'type': 'string'},
                                                                                 'headline': {'type': 'string'}}}}}}
    request = {'language': 'Simplified Chinese' if language == 'zh' else 'English', 'limits_in_characters': limits, 'facts': facts,
               'pages': [{'template': kind, 'goal': briefs().get(kind, {}).get('goal', ''), 'what_it_shows': _SHOWS[kind]} for kind in kinds]}
    try:
        answer = _ask(text, COPYWRITER, request, schema)
    except Exception as exc:
        return {}, '文案模型没有回应（%s），各页使用默认标题。' % str(exc)[:80]
    written = {}; refused = 0
    for row in answer.get('pages', []):
        kind = row.get('template')
        eyebrow = ' '.join(str(row.get('eyebrow', '')).split())
        headline = '\n'.join(' '.join(line.split()) for line in str(row.get('headline', '')).split('\n')[:2]).strip()
        if kind not in kinds or not headline:
            continue
        if len(headline.replace('\n', '')) > limits['headline'] or len(eyebrow) > limits['eyebrow'] or not grounded(headline + ' ' + eyebrow, facts):
            refused += 1; continue
        written[kind] = {'eyebrow': eyebrow, 'headline': headline}
    return written, ('有 %d 条模型写的标题超长或含未提供的数字，已改用默认标题。' % refused) if refused else None


_SHOWS = {'T01': 'the whole product with callout lines to its parts', 'T02': 'dark close-ups of one or two parts',
          'T03': 'the product with a column of part close-ups (headline is the product name; only the eyebrow is used)',
          'T04': 'a usage scene above a band of up to three key figures', 'T08': 'two or three usage scenes side by side'}


# ---- art director ----

def _review_schema():
    action = {'type': 'object', 'additionalProperties': False, 'required': ['type'],
              'properties': {'type': {'type': 'string', 'enum': list(ACTIONS)}, 'target': {'type': 'string'}, 'index': {'type': 'integer'},
                             'text': {'type': 'string'}, 'focus': {'type': 'array', 'items': {'type': 'number'}}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['scores', 'vetoes', 'issues', 'summary'],
            'properties': {'scores': {'type': 'object', 'additionalProperties': False, 'required': list(CRITERIA),
                                      'properties': {key: {'type': 'integer'} for key in CRITERIA}},
                           'vetoes': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'evidence'],
                                                                 'properties': {'kind': {'type': 'string', 'enum': list(VETOES)}, 'evidence': {'type': 'string'}}}},
                           'issues': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['region', 'evidence', 'action'],
                                                                 'properties': {'region': {'type': 'string'}, 'evidence': {'type': 'string'},
                                                                                'impact': {'type': 'string'}, 'action': action}}},
                           'summary': {'type': 'string'}}}


def editable(kind, job):
    """The targets the art director may name for this page, with what each holds now."""
    rows = {field: job[field] for field in TEXT_FIELDS if isinstance(job.get(field), str) and job[field]}
    name, minimum = LISTS.get(kind, (None, 0))
    if name and isinstance(job.get(name), list):
        rows[name] = {'count': len(job[name]), 'minimum': minimum,
                      'items': [{key: item.get(key) for key in ITEM_TEXT if isinstance(item.get(key), str) and item.get(key)} for item in job[name]]}
    scenes = [key for key, value in (job.get('assets') or {}).items() if key.startswith('scene')]
    if scenes:
        rows['scenes'] = {'shown': scenes, 'focus': job.get('focus') or [item.get('focus') for item in job.get('items', []) if isinstance(item, dict)]}
    return rows


def review(kind, job, png, product, reference=None, measured=None):
    """The art director's verdict on one page: {'score', 'scores', 'vetoes', 'issues', 'summary'}, or None without a vision model."""
    _, vision = _configs()
    if not vision:
        return None
    brief = briefs().get(kind, {})
    request = {'template': {'id': kind, 'name': brief.get('name_zh'), 'goal': brief.get('goal'), 'avoid': brief.get('avoid'),
                            'adaptation_rules': brief.get('adaptation_rules')},
               'page_language': product['language'], 'canvas': job['canvas'], 'facts': facts_of(product),
               'editable': editable(kind, job), 'criteria_maximum': CRITERIA,
               'measured_layout': measured or {}, 'max_issues': MAX_ISSUES}
    answer = _ask(vision, ART_DIRECTOR, request, _review_schema(), image_path=str(png), reference_path=str(reference) if reference else None)
    scores = {key: max(0, min(CRITERIA[key], int(answer['scores'].get(key, 0)))) for key in CRITERIA}
    issues = [row for row in answer.get('issues', []) if isinstance(row, dict)][:MAX_ISSUES]
    return {'score': sum(scores.values()), 'scores': scores, 'vetoes': answer.get('vetoes', [])[:4], 'issues': issues,
            'summary': str(answer.get('summary', ''))[:400], 'model': vision.get('model')}


def passed(verdict):
    return bool(verdict) and not verdict['vetoes'] and verdict['score'] >= PASS_SCORE


def revise(kind, job, verdict, facts, other_scenes=()):
    """A new job with the art director's fixes applied, and the fixes that were applied; (None, []) when none can be."""
    new = copy.deepcopy(job); applied = []; next_layout = False
    name, minimum = LISTS.get(kind, (None, 0))
    drops = []
    for issue in verdict['issues']:
        action = issue.get('action') or {}; kind_of = action.get('type'); target = str(action.get('target') or '')
        if kind_of in ('shorten_text', 'remove_text'):
            field, _, rest = target.partition('.')
            if field in TEXT_FIELDS and isinstance(new.get(field), str):
                if kind_of == 'remove_text' and field not in ('headline',):
                    new[field] = None; applied.append('删去%s' % field)
                elif kind_of == 'shorten_text':
                    text = str(action.get('text') or '').strip()
                    if text and len(text.replace('\n', '')) < len(new[field].replace('\n', '')) and grounded(text, facts):
                        new[field] = text; applied.append('缩短%s' % field)
            elif name and field == name and rest:
                index_text, _, key = rest.partition('.')
                index = int(index_text) if index_text.isdigit() else action.get('index')
                if isinstance(index, int) and 0 <= index < len(new[name]) and key in ITEM_TEXT and isinstance(new[name][index].get(key), str):
                    if kind_of == 'remove_text' and key == 'body':
                        new[name][index][key] = None; applied.append('删去第 %d 项说明' % (index + 1))
                    elif kind_of == 'shorten_text':
                        text = str(action.get('text') or '').strip()
                        if text and len(text) < len(new[name][index][key]) and grounded(text, facts):
                            new[name][index][key] = text; applied.append('缩短第 %d 项' % (index + 1))
        elif kind_of == 'drop_item' and name and target.split('.')[0] == name:
            index = action.get('index')
            if isinstance(index, int) and 0 <= index < len(new[name]):
                drops.append(index)
        elif kind_of == 'refocus_scene':
            focus = action.get('focus')
            if isinstance(focus, list) and len(focus) == 2 and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in focus):
                focus = [round(float(focus[0]), 3), round(float(focus[1]), 3)]
                if 'focus' in new and new.get('scene'):
                    new['focus'] = focus; applied.append('调整场景取景')
                elif kind == 'T08':
                    index = action.get('index') if isinstance(action.get('index'), int) else next((i for i, item in enumerate(new['items']) if item['asset'] == target), None)
                    if index is not None and 0 <= index < len(new['items']):
                        new['items'][index]['focus'] = focus; applied.append('调整第 %d 张场景取景' % (index + 1))
        elif kind_of == 'swap_scene' and new.get('scene') and other_scenes:
            current = new['scene']; choice = next((row for row in other_scenes if row[0] != current), None)
            if choice:
                new['scene'], record, focus = choice
                new['assets'] = {key: value for key, value in new['assets'].items() if not key.startswith('scene')}
                new['assets'][new['scene']] = record; new['focus'] = focus; applied.append('换用另一张场景图')
        elif kind_of == 'next_layout':
            next_layout = True
    if drops and name:
        keep = [item for index, item in enumerate(new[name]) if index not in set(drops)]
        if len(keep) >= minimum:
            new[name] = keep; applied.append('去掉 %d 项' % (len(job[name]) - len(keep)))
    if not applied and next_layout:
        return 'next_layout', ['换一种排法']
    return (new, applied) if applied else (None, [])


# ---- the whole set ----

def contact_sheet(paths, target, width=360):
    images = []
    for path in paths:
        with Image.open(path) as opened:
            image = opened.convert('RGB'); image.thumbnail((width, round(width * 1.25)))
            images.append(image)
    columns = 4; rows = (len(images) + columns - 1) // columns; cell_h = max(image.height for image in images)
    sheet = Image.new('RGB', (columns * (width + 12) + 12, rows * (cell_h + 12) + 12), (128, 128, 128))
    for index, image in enumerate(images):
        sheet.paste(image, (12 + (index % columns) * (width + 12), 12 + (index // columns) * (cell_h + 12)))
    sheet.save(target, quality=88)
    return target


def review_set(pages, folder, product):
    """Notes on the set as a whole, or None without a vision model."""
    _, vision = _configs()
    if not vision or len(pages) < 2:
        return None
    sheet = contact_sheet([folder / page['file'] for page in pages], folder / '_set_review.jpg')
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['consistent', 'repeats', 'weakest', 'notes'],
              'properties': {'consistent': {'type': 'boolean'}, 'repeats': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'integer'}}},
                             'weakest': {'type': 'integer'}, 'notes': {'type': 'string'}}}
    request = {'pages': [{'number': index + 1, 'template': page['kind'], 'goal': briefs().get(page['kind'], {}).get('goal')} for index, page in enumerate(pages)],
               'language_for_notes': 'Simplified Chinese'}
    try:
        answer = _ask(vision, SET_REVIEW, request, schema, image_path=str(sheet))
    finally:
        Path(sheet).unlink(missing_ok=True)
    return {'consistent': bool(answer['consistent']), 'repeats': [row for row in answer['repeats'] if isinstance(row, list)][:4],
            'weakest': answer['weakest'], 'notes': str(answer['notes'])[:400], 'model': vision.get('model')}

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
import re
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
ACTIONS = ('shorten_text', 'remove_text', 'default_text', 'drop_item', 'refocus_scene', 'swap_scene', 'next_layout', 'none')
TEXT_FIELDS = ('eyebrow', 'headline', 'support', 'subhead', 'table_note', 'note')
LISTS = {'T01': ('features', 2), 'T02': ('features', 1), 'T03': ('details', 2), 'T04': ('specs', 2), 'T05': ('items', 1), 'T08': ('items', 2)}
ITEM_TEXT = ('title', 'body', 'name', 'label', 'text')
COPY_KINDS = ('T01', 'T02', 'T03', 'T04', 'T08')
COPY_LIMITS = {'zh': {'eyebrow': 6, 'headline': 16}, 'en': {'eyebrow': 22, 'headline': 44}}
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
    'new facts or numbers), remove_text (target an optional text field), default_text (the eyebrow or headline is wrong, mistyped or '
    'claims too much: use the template\'s plain default lines instead), drop_item (target a list and index; keeps at least the minimum), '
    'refocus_scene (target a scene, give focus x,y in 0..1 of the scene picture for its crop centre), swap_scene (use another scene picture), '
    'next_layout (try the template\'s next arrangement), none (no automatic fix). Do not ask for things the actions cannot do. '
    'measured_layout is ground truth measured in the browser: where it says a picture shows the whole product, uncropped and fully '
    'inside the page, never report it cropped or cut off; frames marked as a crop by design are close-ups or scene frames and may cut '
    'the product. Report position or alignment only when it is plainly visible. For product fidelity compare details carefully with '
    'REFERENCE_IMAGE: labels or patches on the product, colours, handles, hardware; a missing patch or a changed colour in a scene is '
    'scene_product_mismatch. Check every line of text against the facts: a use, benefit or quality the facts do not state is '
    'claim_not_in_facts. '
    'Never praise; be specific. Write evidence and summary in the language of the page; quote page text with 「」, never with double quotes.')
COPYWRITER = (
    'You write the small eyebrow label and the headline of each page of a product listing set. Use only the facts given: the product, '
    'its selling points and specs. Each page has one job (goal): its headline says what the buyer gets from the concrete thing that '
    'page shows, as a selling headline on an Amazon listing would ("Rolled handles that stay comfortable", not "Every key part, clearly '
    'shown"), never a description of the picture itself, never vague praise (no "处处讲究", "品质之选", "premium quality") and never a '
    'benefit the facts do not state (no capacity, comfort or durability claims the selling points do not make). A number may lead a headline only when '
    'the facts give it (a size, a count, a capacity), written as the facts write it; never a number the facts do not contain. No prices, superlatives or brand names. Respect each page\'s max_lines: a one-line headline has no '
    '"\\n"; a Chinese headline of two lines breaks with one "\\n" at a natural pause. Keep the eyebrow a plain label (e.g. "细节特写", '
    '"Up close") that does not repeat the headline. On pages that print the selling points (T01, T02), the headline sums them up '
    'and never repeats a selling point\'s title word for word. Write the headlines of different pages differently.')
VERIFIER = (
    'You check another reviewer\'s claims about one listing image. For each numbered claim, look at CURRENT_RENDER (and REFERENCE_IMAGE, '
    'the product\'s own render, for fidelity claims) and the measured layout, and say whether the claim is clearly true. measured_layout '
    'is ground truth: a picture it says shows the whole product inside the page is not cropped. Default to real=false when you cannot '
    'clearly see it. Answer in the language of the page, briefly.')
SET_REVIEW = (
    'You review a whole e-commerce listing set shown as a contact sheet (pages numbered in reading order). Say whether the set reads as one '
    'brand system, which pages repeat the same picture or message, and which page adds least. notes: at most two plain sentences, no '
    'markdown, no lists.')


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


def _ask(config, system, facts, schema, image_path=None, reference_path=None, clean=None):
    """One call; an answer that does not fit the schema is asked for once more. ``clean`` tidies an answer before it is checked."""
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, image_path=image_path, reference_path=reference_path,
                       label_images=bool(image_path), max_tokens=3000)
    for attempt in (1, 2):
        output = _decoded(ai._response(ai._post(config, body), config, ai.TOOL_NAME), schema)
        if clean:
            try:
                output = clean(output)
            except (TypeError, AttributeError):
                pass
        try:
            ai._check_schema(output, schema)
            return output
        except ai.SchemaValidationError:
            if attempt == 2:
                raise


def facts_of(product, measured_mm=None):
    """What the product record says, and what was measured on the 3D model: the only sources of words and numbers on the pages."""
    facts = {'title': product['title'], 'model': product.get('model', ''), 'headline': product['headline'], 'subline': product['subline'],
             'selling_points': [{'title': row['title'], 'detail': row.get('detail', '')} for row in product['features']],
             'specs': product['specs'], 'figures': product['figures'], 'longest_side_mm': product.get('size_mm')}
    if measured_mm:
        facts['measured_on_3d_model_mm'] = {key: round(value, 1) for key, value in measured_mm.items()}
        facts['measurement_note'] = 'Sizes on the dimensions page are measured on the 3D model, scaled to the longest side the user gave; they are facts.'
    return facts


def _numbers(text):
    import layer_compose
    return layer_compose.numbers_in(text)


def grounded(text, facts):
    """Every number in ``text`` appears in the facts (a line without numbers is grounded)."""
    wanted = _numbers(text)
    return not wanted or wanted <= _numbers(json.dumps(facts, ensure_ascii=False))


# ---- copywriter ----

def _numbers_in(text):
    """The numbers in a text, by value ("2.0" is "2")."""
    return {('%f' % float(match)).rstrip('0').rstrip('.') for match in re.findall(r'\d+(?:\.\d+)?', str(text or ''))}


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
               'pages': [{'template': kind, 'goal': briefs().get(kind, {}).get('goal', ''), 'what_it_shows': _SHOWS[kind],
                          'max_lines': _LINES.get(kind, 2)} for kind in kinds]}
    try:
        answer = _ask(text, COPYWRITER, request, schema)
    except Exception as exc:
        return {}, '文案模型没有回应（%s），各页使用默认标题。' % str(exc)[:80]
    written = {}; refused = 0; why_refused = []
    titles = [' '.join(str(row.get('title', '')).split()) for row in product.get('features') or []]
    stated = _numbers_in(json.dumps(facts, ensure_ascii=False))
    for row in answer.get('pages', []):
        kind = row.get('template')
        eyebrow = ' '.join(str(row.get('eyebrow', '')).split())
        headline = '\n'.join(' '.join(line.split()) for line in str(row.get('headline', '')).split('\n') if line.strip())
        if kind not in kinds or not headline:
            continue
        too_many_lines = headline.count('\n') + 1 > _LINES.get(kind, 2)
        # The callout and close-up pages print the selling points' titles under the headline: it must not say one again.
        repeats = kind in ('T01', 'T02') and any(len(title) >= 4 and title.lower() in headline.replace('\n', '').lower() for title in titles)
        reasons = [why for why, bad in (('超长', len(headline.replace('\n', '')) > limits['headline'] or len(eyebrow) > limits['eyebrow']),
                                        ('行数不对', too_many_lines), ('含资料里没有的数字', not _numbers_in(headline + eyebrow) <= stated),
                                        ('眉题与标题重复', bool(eyebrow and eyebrow in headline)), ('照抄卖点标题', repeats)) if bad]
        if reasons:
            refused += 1; why_refused.extend(reasons); continue
        written[kind] = {'eyebrow': eyebrow, 'headline': headline}
    return written, ('有 %d 条模型写的标题没有采用（%s），已改用默认标题。' % (refused, '、'.join(sorted(set(why_refused))))) if refused else None


_LINES = {'T01': 2, 'T02': 2, 'T03': 2, 'T04': 2, 'T08': 2}
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


def _cleaned(answer):
    """An answer with an action or veto the code does not know read as no action / no veto, rather than thrown away whole."""
    for row in answer.get('issues') or []:
        if isinstance(row, dict) and isinstance(row.get('action'), dict) and row['action'].get('type') not in ACTIONS:
            row['action'] = {'type': 'none'}
    if isinstance(answer.get('vetoes'), list):
        answer['vetoes'] = [row for row in answer['vetoes'] if isinstance(row, dict) and row.get('kind') in VETOES]
    return answer


def review(kind, job, png, product, reference=None, measured=None, facts=None):
    """The art director's verdict on one page: {'score', 'scores', 'vetoes', 'issues', 'summary'}, or None without a vision model."""
    _, vision = _configs()
    if not vision:
        return None
    brief = briefs().get(kind, {})
    request = {'template': {'id': kind, 'name': brief.get('name_zh'), 'goal': brief.get('goal'), 'avoid': brief.get('avoid'),
                            'adaptation_rules': brief.get('adaptation_rules')},
               'page_language': product['language'], 'canvas': job['canvas'], 'facts': facts or facts_of(product),
               'editable': editable(kind, job), 'criteria_maximum': CRITERIA,
               'measured_layout': measured or {}, 'max_issues': MAX_ISSUES}
    answer = _ask(vision, ART_DIRECTOR, request, _review_schema(), image_path=str(png), reference_path=str(reference) if reference else None,
                  clean=_cleaned)
    scores = {key: max(0, min(CRITERIA[key], int(answer['scores'].get(key, 0)))) for key in CRITERIA}
    issues = [row for row in answer.get('issues', []) if isinstance(row, dict)][:MAX_ISSUES]
    return {'score': sum(scores.values()), 'scores': scores, 'vetoes': answer.get('vetoes', [])[:4], 'issues': issues,
            'summary': str(answer.get('summary', ''))[:400], 'model': vision.get('model')}


def passed(verdict):
    """No confirmed veto, and either the score clears the bar or none of the problems that held it down stood up to checking."""
    if not verdict or verdict['vetoes']:
        return False
    return verdict['score'] >= PASS_SCORE or (verdict.get('refuted', 0) > 0 and not verdict['issues'])


def verify(kind, png, product, reference, measured, verdict, facts=None):
    """A second look at the art director's vetoes and problems: only the ones that are clearly visible are kept.
    Returns the verdict with the refuted ones removed and their number in 'refuted'."""
    _, vision = _configs()
    claims = [('veto', row) for row in verdict['vetoes']] + [('issue', row) for row in verdict['issues']]
    if not vision or not claims:
        return verdict
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['claims'],
              'properties': {'claims': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['number', 'real', 'why'],
                                                                   'properties': {'number': {'type': 'integer'}, 'real': {'type': 'boolean'},
                                                                                  'why': {'type': 'string'}}}}}}
    request = {'template': kind, 'facts': facts or facts_of(product), 'measured_layout': measured or {},
               'claims': [{'number': index + 1, 'kind': row.get('kind', 'issue') if kind_of == 'veto' else 'issue',
                           'region': row.get('region', ''), 'claim': row.get('evidence', '')} for index, (kind_of, row) in enumerate(claims)]}
    answer = _ask(vision, VERIFIER, request, schema, image_path=str(png), reference_path=str(reference) if reference else None)
    real = {row['number'] for row in answer.get('claims', []) if row.get('real') is True}
    kept_vetoes = [row for index, (kind_of, row) in enumerate(claims) if kind_of == 'veto' and index + 1 in real]
    kept_issues = [row for index, (kind_of, row) in enumerate(claims) if kind_of == 'issue' and index + 1 in real]
    return {**verdict, 'vetoes': kept_vetoes, 'issues': kept_issues, 'refuted': len(claims) - len(kept_vetoes) - len(kept_issues)}


def checked(kind, job, png, product, reference=None, measured=None, facts=None):
    """The art director's verdict, with its claims checked by a second look whenever the page would not pass as it stands.
    (On a passing page the remarks are kept unchecked: they change nothing and are not shown; checking them, live, did not
    make them more accurate.)"""
    verdict = review(kind, job, png, product, reference, measured, facts)
    if verdict and not passed(verdict):
        verdict = verify(kind, png, product, reference, measured, verdict, facts)
    return verdict


def revise(kind, job, verdict, facts, other_scenes=()):
    """A new job with the art director's fixes applied, and the fixes that were applied; (None, []) when none can be."""
    new = copy.deepcopy(job); applied = []; next_layout = False
    if any((issue.get('action') or {}).get('type') == 'default_text' for issue in verdict['issues']) or \
            any(row.get('kind') in ('text_broken', 'claim_not_in_facts') for row in verdict['vetoes']):
        if job.get('_lines') == 'model':
            return 'default_text', ['改用模板默认标题']          # a written line that is wrong goes before anything is tuned
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
                    if field == 'headline' and job.get('lang') == 'zh':
                        import brandkit_set
                        text = brandkit_set._headline(text.replace('\n', ''), 'zh', 60)       # broken at a pause, not where the model put it
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
            'weakest': answer['weakest'], 'notes': re.sub(r'[*#`>]+|^\s*[-•]\s*', '', ' '.join(str(answer['notes']).split()))[:240], 'model': vision.get('model')}

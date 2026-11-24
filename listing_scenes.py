"""Usage scenes: the scene settings kept on the product record, the briefs the image model paints from, the key that
decides when a set is made again, and ``make_scenes``, which paints the scenes of a set at once. Split out of
``brandkit_set``, which still re-exports every name.

The two caption steps that ask a model (``captions``, ``seen_captions``) stay in ``brandkit_set``, because the name of the
function that sends a request labels it in data/ai_usage.jsonl and picks its model (brandkit_review.MODEL_KNOBS).
``make_scenes`` and ``scene_supply`` therefore look those steps, and the names tests patch on ``brandkit_set``
(image_model_ready, scene_key, scene_briefs), up on ``brandkit_set`` when they run.
"""

import contextvars
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


MAX_SCENES = 3
SCENE_PEOPLE = ('auto', 'hands', 'include', 'exclude')


def default_scenes():
    return {'enabled': True, 'count': MAX_SCENES, 'audience': 'auto', 'people': 'auto', 'hint': ''}


def validate_scenes(value, current=None):
    """The listing's scene settings: whether the image model makes scenes for it, how many, for whom, with or without people."""
    import scene_audience
    merged = {**default_scenes(), **(current or {}), **(value if isinstance(value, dict) else {})}
    if value is not None and not isinstance(value, dict):
        raise ValueError('场景设置格式无效')
    if type(merged['enabled']) is not bool:
        raise ValueError('场景开关必须是 true 或 false')
    count = merged['count']
    if isinstance(count, bool) or not isinstance(count, (int, float)) or int(count) != count or not 1 <= count <= MAX_SCENES:
        raise ValueError('场景图数量必须是 1 到 %d' % MAX_SCENES)
    audience = merged['audience'] or 'auto'
    if audience != 'auto' and not scene_audience.segment(audience):
        raise ValueError('所选人群不在参考库中。')
    if merged['people'] not in SCENE_PEOPLE:
        raise ValueError('人物选项无效')
    hint = merged.get('hint') or ''
    if not isinstance(hint, str):
        raise ValueError('场景说明必须是文字')
    hint = re.sub(r'[\x00-\x1f\x7f]+', ' ', hint).strip()
    if len(hint) > 300:
        raise ValueError('场景说明不能超过 300 字')
    return {'enabled': merged['enabled'], 'count': int(count), 'audience': audience, 'people': merged['people'], 'hint': hint}


def image_model_ready():
    try:
        import scene_imagegen
        return scene_imagegen.config() is not None
    except Exception:
        return False


def scene_supply(product, legacy=0):
    """How many scene pictures a build can count on: from the image model, the workpiece render and earlier scenes."""
    import brandkit_set
    image_model_ready = brandkit_set.image_model_ready      # looked up on the facade, where callers and tests patch it
    settings = product.get('scenes') or default_scenes()
    made = settings['count'] if settings['enabled'] and image_model_ready() else 0
    return made + (1 if product['usecase']['enabled'] else 0) + (legacy if not made else 0)


SCENE_PLAN = 7        # how the scenes are planned; a change makes every listing's scenes again (2: a third scene in its own setting;


def scene_key(project, product, settings):
    """What the scene pictures are made from: a change in any of it makes them again, and nothing else does. The listing's
    title and copy only change the words, so they are not in it; the product's name, category, real size and how it is
    installed choose the places and the scale, so they are."""
    import mounting
    plan = project.get('plan') or {}
    moving = project.get('articulation') if isinstance(project.get('articulation'), dict) else {}
    metadata = project.get('metadata') if isinstance(project.get('metadata'), dict) else {}
    raw = json.dumps([project.get('model_path'), plan.get('rotation'), plan.get('material_overrides'), settings, SCENE_PLAN,
                      moving.get('key'), moving.get('status'), *([POSES_SHOWN] if moving.get('states') else []),
                      {'name': project.get('name'), 'category': metadata.get('product_category'), 'size_mm': plan.get('dimensions_mm'),
                       'mounting': mounting.of(project, (settings or {}).get('hint', '') if isinstance(settings, dict) else '')}],
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def legacy_scene_key(project, product, settings):
    """The key scene sets were saved under before v48 (it held the listing title and not the product's own facts): a set
    saved so is carried over once to the new key instead of being paid for again."""
    plan = project.get('plan') or {}
    moving = project.get('articulation') if isinstance(project.get('articulation'), dict) else {}
    raw = json.dumps([project.get('model_path'), plan.get('rotation'), plan.get('material_overrides'), product.get('title'), settings, SCENE_PLAN,
                      moving.get('key'), moving.get('status'), *([POSES_SHOWN] if moving.get('states') else [])],
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def carry_over(store, old_folder, new_folder, key):
    """A scene set saved under another key, moved to ``new_folder`` and filed under ``key``: (store or None)."""
    if not isinstance(store, dict) or not Path(old_folder).is_dir() or Path(new_folder).exists():
        return None
    Path(old_folder).rename(new_folder)
    old, new = str(old_folder), str(new_folder)

    def moved(value):
        return value.replace(old, new, 1) if isinstance(value, str) and value.startswith(old) else value
    items = []
    for row in store.get('items', []):
        again = row.get('recheck')
        if isinstance(again, dict):
            again = {**again, 'reference': moved(again.get('reference')), 'closeup': moved(again.get('closeup'))}
        items.append({**row, 'path': moved(row.get('path')), **({'recheck': again} if again is not None else {})})
    return {**store, 'key': key, 'items': items}


def scene_briefs(project, settings, count):
    """What each scene shows: the strategist's picture of the product in use, the product set down where it
    is used, and the same for the next most likely buyer. Returns [{'proposal', 'role'}]."""
    import scene_audience
    import scene_stylist
    import mounting as mounted
    first = scene_stylist.propose(project, settings['hint'], settings['audience'], None, scene_stylist._source_image(project), settings['people'])
    mount = mounted.of(project, settings['hint']); longest = _longest_m(project, first)
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    text = ' '.join(str(value) for value in (project.get('name', ''), metadata.get('product_category', ''), settings['hint']))
    ranked = scene_audience.rank(text, text)

    def suited(segment_id):
        return bool(segment_id) and bool(scene_audience.segment(segment_id)) and scene_audience.fits(segment_id, mount, longest)
    segment = first.get('segment_id')
    if segment and not suited(segment):
        # a place this product does not belong in (a wall cabinet at a van): the next buyer whose place suits it
        segment = next((item for item in ranked + [scene_audience.NEIGHBOUR.get(segment)] + [row['id'] for row in scene_audience.SEGMENTS]
                        if item != first.get('segment_id') and suited(item)), None)
        first = {**first, 'segment_id': segment, 'reference_id': None, 'summary': (scene_audience.segment(segment) or {}).get('label', ''),
                 'scene': scene_audience.fixed_scene(segment, bool(first.get('people')), mount)}
    elif mount == 'wall' and not hangs_on_wall(first.get('scene')):
        # the strategist's scene sets the product down somewhere: a wall-mounted one is hung on the place's wall instead
        first = {**first, 'scene': scene_audience.fixed_scene(segment, bool(first.get('people')), mount)}
    first = {**first, 'mounting': mount}
    briefs = [{'proposal': first, 'role': 'use'}]
    # a second scene with nobody in it is the product at rest in a place; with people asked for (whole figures or hands at
    # work) it is another moment of the work, and is posed and captioned as one
    people_allowed = settings['people'] not in ('include', 'hands')
    if count >= 2:
        if settings['people'] in ('include', 'hands'):
            # Asked to see people in every picture: the second is still the product at work, a different moment of it.
            second = {**first, 'scene': first.get('scene', '') + ' A different moment of the same job, seen from another side.'}
        else:
            # the product at rest in the buyer's place, nobody there: set into a backdrop photograph when one can be had
            # (a backdrop is a floor or a table top: a wall-mounted product is painted on a wall instead)
            second = {**first, 'people': False, 'framing': None, 'scene': scene_audience.fixed_scene(segment, False, mount), 'backdrop': mount != 'wall'}
        briefs.append({'proposal': second, 'role': 'place' if people_allowed else 'use'})
    if count >= 3:
        others = [item for item in ranked if item != segment and suited(item)]
        if not others and segment:
            # nothing else in the product's words: the neighbouring buyer, never the first scene again, and never a place
            # the product does not belong in
            neighbour = scene_audience.NEIGHBOUR.get(segment)
            others = [neighbour] if neighbour != segment and suited(neighbour) else []
        other = others[0] if others else None
        wants = first.get('people') if settings['people'] == 'auto' else settings['people'] in ('include', 'hands')
        third = {**first, 'segment_id': other or segment, 'people': bool(wants), 'framing': first.get('framing') if wants else None,
                 'scene': scene_audience.fixed_scene(other, bool(wants), mount) if other else
                          (first.get('scene', '') + ' Another setting where the same buyer would use it.')}
        if other:
            # the third scene's own plan, not the first one's carried over
            third.update(summary=(scene_audience.segment(other) or {}).get('label', ''), reference_id=None, reason='',
                         backdrop=not wants and mount != 'wall')
        briefs.append({'proposal': third, 'role': 'other'})
    briefs = briefs[:count]
    import articulation
    from studio_store import get
    try:
        stored = get('project', project['id'])
    except (KeyError, TypeError):
        stored = project
    shown = _scene_states(articulation.states(stored), [brief['role'] for brief in briefs])
    return [_with_moving_parts(project, brief, state) for brief, state in zip(briefs, shown)]


def usable_scene(row):
    """Whether a scene picture may go on a page: one whose product check failed (moved, redrawn, wrongly installed or
    used) is never shown, not even when no other scene is there; the page then does without it. Nor is one whose check
    could not run (quota, timeout): it is checked again at the next build."""
    return isinstance(row, dict) and row.get('check') != 'failed' and not row.get('check_error')


def swappable_scenes(ctx):
    """What a page revision may swap in: [(name, asset, focus)], only scenes that may be on a page at all."""
    return [('scene%d' % (index + 1), ctx['assets']['scene%d' % (index + 1)], row['focus']) for index, row in enumerate(ctx.get('scenes') or [])
            if usable_scene(row)]


def recheck_scenes(rows):
    """Run the vision check again on kept scenes whose check could not run, on the picture already made (no image-model
    call). Returns (rows, notes)."""
    import scene_imagegen
    out = []; notes = []
    for row in rows:
        again = row.get('recheck') if row.get('check_error') else None
        if not again or not Path(row.get('path', '')).is_file() or not again.get('reference') or not Path(again['reference']).is_file():
            out.append(row); continue
        closeup = again.get('closeup') if again.get('closeup') and Path(again['closeup']).is_file() else None
        checked = scene_imagegen.check([again['reference']], row['path'], again.get('product') or '', again.get('people'), again.get('framing'),
                                       closeup, again.get('size_mm'), **({'usage': again['usage']} if again.get('usage') else {}),
                                       **({'mounting': again['mounting']} if again.get('mounting') else {}))
        row = {**row, **_check_fields(checked)}
        if checked.get('status') == 'failed':
            row['usage_retry'] = 0           # failed on its first real check: it gets the one more try a failed scene gets
        if not checked.get('error'):
            notes.append('补做了一张场景图的产品核对（上次因 AI 服务未能完成）：%s。' % ('通过' if checked.get('status') != 'failed' else '未通过，不放进页面'))
        out.append(row)
    return out, notes


def _check_fields(checked):
    """What a scene row keeps of its check."""
    return {'check': checked.get('status') or 'unchecked', 'check_error': bool(checked.get('error')),
            'check_issues': [str(item)[:200] for item in (checked.get('issues') or [])[:5]],
            'check_flags': [name for name, ok in (checked.get('flags') or {}).items() if ok is False]}


HANGS = re.compile(r'(?i)(?:mounted|fixed|hung|hangs?|hanging)\s+(?:flat\s+)?(?:on|to|from)\s+(?:the|a|its)\s+(?:\w+\s+){0,3}wall')
STANDS_ON = re.compile(r'(?i)(?:stands?|standing|sits?|sitting|rests?|resting|placed|set)\s+(?:\w+\s+){0,2}on\s+(?:the|a)\s+(?:\w+\s+){0,2}(?:floor|bench|table|ground)')


def hangs_on_wall(scene):
    """Whether a scene text has the product hanging on a wall, not standing on a floor next to one."""
    text = str(scene or '')
    return bool(HANGS.search(text)) and not STANDS_ON.search(text)


def _longest_m(project, proposal):
    """The product's longest side in metres: the stated or measured size, else the strategist's estimate; None when unknown."""
    sizes = (project.get('plan') or {}).get('dimensions_mm') if isinstance(project.get('plan'), dict) else None
    try:
        if sizes and max(float(value) for value in sizes) > 0:
            return max(float(value) for value in sizes) / 1000
    except (TypeError, ValueError):
        pass
    try:
        return float(proposal.get('max_dimension_mm')) / 1000 if proposal.get('max_dimension_mm') else None
    except (TypeError, ValueError):
        return None


POSES_SHOWN = 2       # how the scenes share out the product's states; a change makes the scenes of products with moving parts again


def _scene_states(states, roles):
    """Which of the product's states each scene shows, so that the set shows the product in more than one: at rest where it
    is kept, at work where it is used, and fully open in the third picture when that is another state again. A pose the
    user asked for in the scene hint ("拉开前两个抽屉") is what the pictures of the product in use show; the picture of it
    at rest still shows it closed. Returns one state (or None) per role."""
    def first(*wanted, besides=()):
        return next((row for role in wanted for row in states if row.get('role') == role and row['id'] not in besides), None)
    asked = next((row for row in states if row.get('matches_scene_hint')), None)
    chosen = []
    for role in roles:
        if role == 'place':
            chosen.append(first('rest'))
        elif role == 'other' and not asked:
            shown = {row['id'] for row in chosen if row}
            chosen.append(first('open', 'working', besides=shown) or first('working', 'open'))
        else:
            chosen.append(asked or first('working', 'open'))
    return chosen


def _with_moving_parts(project, brief, state=False):
    """A brief with the product's moving parts set for its moment (the pose agent's states) and the product's own rules for
    being used correctly. ``state`` is the state to show (None for the rest pose); left out, the scene's own."""
    import articulation
    from studio_store import get
    try:
        project = get('project', project['id'])
    except (KeyError, TypeError):
        pass
    proposal = dict(brief['proposal'])
    chosen = _scene_states(articulation.states(project), [brief['role']])[0] if state is False else state
    plan = articulation.plan_for(project, chosen['id']) if chosen else None
    if plan:
        proposal['articulation'] = {**plan, 'label': chosen.get('label_en') or chosen['id']}
    rules = articulation.usage(project, 'en')
    if rules:
        proposal['usage'] = rules
    return {**brief, 'proposal': proposal}


def _fit_line(text, limit):
    """A line within ``limit`` characters, cut only after a whole clause; '' when even the first clause is too long."""
    if len(text) <= limit:
        return text
    cuts = [index for index, char in enumerate(text[:limit + 1]) if char in '，、；。,;' and index > 0]
    return text[:cuts[-1]].rstrip('，、；,;') + ('。' if re.search(r'[\u4e00-\u9fff]', text) else '') if cuts else ''


def _clause_lines(text, language, limit):
    """A Chinese line set one clause per line (the browser would otherwise wrap it mid-word); '' when a clause is
    longer than ``limit`` characters and so would still wrap. Other languages wrap at spaces and pass unchanged."""
    if not text or language != 'zh':
        return text or ''
    clauses = [part for part in re.split(r'(?<=[，、；。])', text.replace('\n', '')) if part]
    if any(len(part.rstrip('，、；。')) > limit for part in clauses):
        return ''
    return '\n'.join(clauses).rstrip('，、；')


PLACE_WORDS = {'workbench': 'shop', 'bench': 'shop', 'workshop': 'shop', 'shop': 'shop', 'garage': 'shed', 'shed': 'shed',
               '工作台': '工坊', '工坊': '工坊', '车间': '工坊', '工作间': '工坊', '台面': '工坊'}
_TITLE_FILLER = {'at', 'in', 'on', 'the', 'a', 'an', 'by', 'for', 'of', 'to', 'and', 'with'}


def _same_place(title, other):
    """Whether two scene titles name the same place ("In the workshop" / "At the workbench", the words for one place counted
    as one) or say the same thing in other filler words."""
    def words(text):
        text = str(text or '').lower()
        found = {PLACE_WORDS.get(word, word) for word in re.findall(r'[a-z]+', text) if word not in _TITLE_FILLER and len(word) > 2}
        found |= {value for key, value in PLACE_WORDS.items() if re.search(r'[\u4e00-\u9fff]', key) and key in text}
        return found
    a, b = words(title), words(other); places = set(PLACE_WORDS.values())
    return bool(a & b & places) or (bool(a) and a == b)


CAPTIONED = 'occasion'          # how the captions were written; older ones are written again once
PLACE_ONLY = 'place'            # ... for a product with no selling points or description: every scene is titled by its place


def bare(product):
    """Whether the user gave nothing but a name and figures: no selling points, no headline, no description."""
    return isinstance(product.get('features'), list) and not (product['features'] or product.get('headline') or product.get('subline'))


def caption_way(product):
    return PLACE_ONLY if bare(product) else CAPTIONED


def _in_parallel(calls, workers, failed=None):
    """Run ``calls`` (functions of no arguments) on worker threads, at most ``workers`` at once; their results in the order
    given. Each runs in its own copy of the caller's context: the AI steps read contextvars (brandkit_review.FRESH, the step
    ai_usage.jsonl names) that a bare worker thread would not see. When a call raises, ``failed`` is called at once on that
    thread (to tell the others to stop: a worker cannot be killed, it notices through its ``cancelled``), the calls not
    started are dropped, the running ones are waited for, and the first exception raised is raised then (the later ones are
    mostly those stops; by the time the main thread looks, several may have ended, so the order is kept as they were raised)."""
    if not calls:
        return []
    errors = []; guard = threading.Lock()

    def run(call):
        try:
            return call()
        except BaseException as exc:
            with guard:
                errors.append(exc)
            if failed:
                failed()
            raise
    with ThreadPoolExecutor(max(1, min(workers, len(calls)))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, run, call) for call in calls]
        for future in as_completed(futures):
            if not future.cancelled() and future.exception() is not None:
                for other in futures:
                    other.cancel()
    if errors:
        raise errors[0]
    return [future.result() for future in futures]


def _free_folder(folder, name, kept):
    """``folder``/``name``, or a fresh variant of it when a scene kept from the last build lives there (it would be
    overwritten: kept scenes are matched by role, so the third picture can sit in scene2/)."""
    used = {Path(row.get('path', '')).parent.name for row in kept or []}
    if name not in used:
        return folder / name
    number = 2
    while '%s-%d' % (name, number) in used or (folder / ('%s-%d' % (name, number))).exists():
        number += 1
    return folder / ('%s-%d' % (name, number))


SCENE_TRIES = 2       # builds in a row in which a scene's every picture lost the product before that scene is left out
SCENE_WORKERS = 3     # scenes painted at once: every scene of a set (2-3), each 60-90 s of image-model time


def make_scenes(project, product, folder, cancelled, say, previous=None, failures=None):
    """Scene pictures for the listing: [{'path', 'title', 'body', 'focus', 'role', 'record'}]. The product at work is
    painted by the image model around the product's render; the product at rest in a place is the render itself set
    into a photograph of that place (scene_backdrop.py), painted only when that cannot be done.

    A set made with the same settings for the same model is reused (``previous``: the stored rows with their key).
    ``failures`` (a list, when given) receives the scenes whose every picture lost the product, with how many builds in a
    row that happened: kept with the set (previous['failed']) so the next build tries such a scene once more, then leaves
    it out instead of paying for it on every rebuild.
    Returns (rows, key, notes).
    """
    import scene_stylist
    # Looked up on the facade when a build runs: the two caption steps live there (the name of the function that asks the
    # model labels the request in the usage log), and callers and tests patch these names there and expect a build to see it.
    import brandkit_set
    image_model_ready, scene_key, scene_briefs = brandkit_set.image_model_ready, brandkit_set.scene_key, brandkit_set.scene_briefs
    captions, seen_captions = brandkit_set.captions, brandkit_set.seen_captions
    settings = product.get('scenes') or default_scenes()
    notes = []
    if not settings['enabled']:
        return [], None, notes
    if not image_model_ready():
        notes.append('图像模型未配置，没有生成 AI 场景图；海报改用纯色版，需要场景的页面已跳过。')
        return [], None, notes
    key = scene_key(project, product, settings)
    kept = []
    if previous and previous.get('key') == key:
        rows = [row for row in previous.get('items', []) if Path(row.get('path', '')).is_file()]
        rows, rechecked = recheck_scenes(rows); notes = notes + rechecked
        import scene_imagegen
        stale = [row for row in rows if row.get('check') == 'failed' and (row.get('usage_retry') or 0) < scene_imagegen.USAGE_RETRY]
        if stale and len(rows) - len(stale) > 0:
            # a scene that failed its check before the extra try for a wrong grip or use existed is made once more
            rows = [row for row in rows if row not in stale]
        if rows and len(rows) < settings['count']:
            # Same settings, but the last run stopped short (the image service refused one, the quota ran out): the
            # pictures it made are kept and only the missing ones are made, rather than paying for the whole set again.
            kept = rows
        elif len(rows) == len(previous.get('items', [])) and rows:
            notes = ['场景图沿用上次生成的 %d 张（设置没有变化）。' % len(rows)] + notes
            # Each language keeps its own captions: a set in another language, or captions written the old way, are read again.
            language = product['language']
            own = lambda row: ((row.get('captions') or {}).get(language) or {})
            rows = [{**row, 'title': own(row)['title'], 'body': own(row).get('body', '')} if own(row).get('how') == caption_way(product) else row for row in rows]
            if any(own(row).get('how') != caption_way(product) for row in rows):
                rows, done = seen_captions(product, [row for row in rows])
                if done:
                    notes.append('已按场景图的实际内容重写 %d 条图注。' % done)
            return rows, key, notes
    folder.mkdir(parents=True, exist_ok=True)
    try:
        briefs = scene_briefs(project, settings, settings['count'])
    except ValueError as exc:
        notes.append('没有生成场景图：%s' % exc)
        return [], None, notes
    if briefs[0]['proposal'].get('method') != 'generate' and not image_model_ready():
        return [], None, notes
    titles = captions(project, product, briefs)
    failed_before = {row.get('scene'): row for row in ((previous or {}).get('failed') or [])
                     if (previous or {}).get('key') == key and isinstance(row, dict)}
    import scene_imagegen
    # A kept picture goes back to the slot it was made for ("use2"); one from before slots were recorded fills its role's
    # slots in order. Redrawing the first of two "use" scenes then remakes that one, not the second.
    own = {}; spare = {}
    for row in kept:
        if row.get('slot') and row['slot'] not in own:
            own[row['slot']] = row
        else:
            spare.setdefault(row.get('role'), []).append(row)
    if kept:
        notes.append('场景图沿用上次生成的 %d 张，补生成缺少的 %d 张。' % (len(kept), max(0, len(briefs) - len(kept))))
    # Per scene, in brief order: its row, its notes and its failure memory. Filled by the workers out of order and read
    # back in order, so the set, the notes and previous['failed'] come out as when the scenes were made one by one.
    slots = [None] * len(briefs); said = [[] for _ in briefs]; failed = [[] for _ in briefs]; jobs = []
    for index, (brief, caption) in enumerate(zip(briefs, titles)):
        if cancelled():
            raise InterruptedError()
        name = '%s%d' % (brief['role'], sum(1 for other in briefs[:index] if other['role'] == brief['role']) + 1)
        before = failed_before.get(name) or {}
        mine = own.pop(name, None)
        if mine is None and spare.get(brief['role']):
            mine = spare[brief['role']].pop(0)
        if mine is not None:
            slots[index] = mine
            if before and failures is not None:
                failed[index].append(before)          # kept for later, not dropped because a kept picture filled the slot
            continue
        if before.get('count', 0) >= SCENE_TRIES:
            said[index].append('第 %d 张场景图连续 %d 次构建都没能保住产品的样子，这次不再付费重画；换场景人群、提示或设置后会重新生成。'
                               % (index + 1, before['count']))
            if failures is not None:
                failed[index].append(before)
            continue
        # the folder is picked here, on one thread: each scene its own, so the workers never write into one another's
        jobs.append((index, brief, caption, name, before, _free_folder(folder, 'scene%d' % (index + 1), kept)))
    # The scenes are independent (own brief, own folder) and each is a minute or more of image-model time: they are made at
    # once. A cancel, or one scene stopping the build, stops the others through the ``cancelled`` they are handed.
    stop = threading.Event(); halt = lambda: stop.is_set() or cancelled()
    finished = [0]; counting = threading.Lock()
    # progress is how many scenes are finished (several run at once, so the index of the one speaking would jump back and forth)
    tell = lambda detail: say(finished[0], len(briefs), detail)

    def make(index, brief, caption, name, before, where):
        if halt():
            raise InterruptedError()
        tell('生成第 %d / %d 张场景图' % (index + 1, len(briefs)))
        try:
            _, record, picture = scene_stylist.generate(project, brief['proposal'], settings['hint'], halt,
                                                        lambda text: tell('场景 %d：%s' % (index + 1, text)), where)
        except InterruptedError:
            raise
        except Exception as exc:
            said[index].append('第 %d 张场景图没有生成：%s' % (index + 1, str(exc)[:160]))
            if isinstance(exc, scene_imagegen.ProductNotKept) and failures is not None:
                # remembered (a quota or network failure is not: that one is simply tried again)
                failed[index].append({'scene': name, 'role': brief['role'], 'count': before.get('count', 0) + 1, 'reason': str(exc)[:160], 'at': time.time()})
            elif before and failures is not None:
                failed[index].append(before)          # a quota or network failure says nothing about the product: the count stands
            return
        finally:
            with counting:
                finished[0] += 1
        box = (picture['record'].get('anchor') or {}).get('box') or [.3, .3, .7, .7]
        checked = record.get('check') or {}
        if checked.get('status') == 'failed':
            why = [flag for flag, ok in (checked.get('flags') or {}).items() if ok is False]
            said[index].append('第 %d 张场景图的产品核对没有完全通过%s，请检查后再使用。' % (
                index + 1, '（%s）' % (checked.get('note') or '、'.join(why))[:120] if (checked.get('note') or why) else ''))
        composite = record.get('model') == 'composite'
        if composite:
            said[index].append('第 %d 张场景图由产品模型渲染后合成进背景图：产品按实际尺寸摆放，没有经过图像模型重画。' % (index + 1))
        slots[index] = {'path': str(picture['png']), 'title': caption['title'], 'body': caption['body'], 'role': brief['role'],
                        # how the picture came to be: the model's render set into a backdrop, or painted by the image model
                        'placed': 'composite' if composite else 'painted', 'slot': name,
                        # the product's own render put back over the one the image model painted (scene_imagegen)
                        'restored': (record.get('restored') or (picture.get('record') or {}).get('restored')) is True,
                        'focus': [round((box[0] + box[2]) / 2, 3), round((box[1] + box[3]) / 2, 3)], 'box': box,
                        'summary': record.get('summary'), 'model': record.get('model'), 'people': record.get('people'),
                        **_check_fields(checked), 'recheck': record.get('recheck') or picture['record'].get('recheck'),
                        # what the picture cost: the image service's token counts over all attempts, and how many there were
                        'usage': record.get('usage'), 'attempts': record.get('attempts'), 'usage_retry': record.get('usage_retry')}

    try:
        # stopped at once, not when the main thread gets round to it: the others stop paying for pictures
        _in_parallel([lambda job=job: make(*job) for job in jobs], SCENE_WORKERS, stop.set)
    finally:
        # what was made before a stop is still told and remembered; the caller drops the rows on an exception, as before
        for index in range(len(briefs)):
            notes.extend(said[index])
            if failures is not None:
                failures.extend(failed[index])
    rows = [row for row in slots if row is not None]
    rows, _ = seen_captions(product, rows)
    return rows, key if rows else None, notes

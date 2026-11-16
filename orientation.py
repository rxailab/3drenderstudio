"""Which way up a product stands.

A model file's axes are whatever its CAD program used, so a tool can arrive
upside-down or lying on its back, and every picture then shows it that way. At
analysis, and again whenever the user asks, the model is pictured in six
candidate poses (blender/orient.py) and one of them is chosen:

    vision   the vision model looks at the six pictures and says which is the right way up
    names    without one, parts called wheel, foot or base belong at the bottom, lid or top at the top
    file     with neither, the file's own orientation is kept

When the pose changed, the facing side is looked at too (four quarter turns).
The choice is written to plan['rotation'], which every render, the 3D scene
layout and the reference renders of a generated scene already follow. Whatever
was chosen, the user can pick another candidate or flip and turn the product
with one click. The uploaded model itself is never modified.
"""
import copy
import json
import math
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from studio_store import DATA, LOCK, ROOT, all_records, atomic_json, get, put

VERSION = 1
KIND = 'orient_task'
ACTIVE = {'queued', 'running'}
TIMEOUT = 420
SIZE = 320
LETTERS = 'ABCDEFGHIJKL'
# The file's own pose first; the rest are the five other ways its axes could point up.
POSES = (('file', '原始朝向', [0, 0, 0]), ('flip', '上下翻转', [180, 0, 0]),
         ('forward', '向前倒 90°', [90, 0, 0]), ('backward', '向后倒 90°', [-90, 0, 0]),
         ('right', '向右倒 90°', [0, 90, 0]), ('left', '向左倒 90°', [0, -90, 0]))
TURNS = (0, 90, 180, 270)
# Words in part names that say where the part belongs. Only whole words count for the short English ones.
BOTTOM_WORDS = ('脚轮', '轮子', '车轮', '万向轮', '底座', '底板', '底盘', '支脚', '脚垫', '地脚', '柜脚', '桌脚', '椅脚',
                'caster', 'castor', 'wheel', 'foot', 'feet', 'leg', 'legs', 'base', 'bottom', 'stand', 'pedestal', 'skid')
# A cover or a cap can be anywhere on a product (a battery cover is underneath), so only names that mean "the top" count.
TOP_WORDS = ('顶盖', '上盖', '顶板', '台面', 'lid', 'top', 'worktop', 'countertop', 'tabletop', 'roof')

UPRIGHT_PROMPT = '''You are shown ONE product's 3D model in several candidate poses, as a grid of labelled cells. The model file may have been exported upside-down or lying on a side, so the cells show the same model turned in different ways, resting on the same grey floor and seen from the same camera.
Decide which cell shows the product the right way up: the way it stands or rests when it is displayed for sale or put down ready for use, with its base, feet, wheels or resting side on the floor and any lettering upright. A hand-held tool is the right way up as it is normally shown in a catalogue or set down on a bench.
Return data only:
- product: what the product is, two to five plain English words.
- upright: the letter of the cell that shows it the right way up.
- also_normal: letters of other cells that are an equally normal way to show this product (a tool that can lie on either side, a box with no top). Empty when there are none.
- confident: true only when you can tell what the product is and are sure about the cell.
- reason: one sentence in the language of reason_language saying what you recognised (which part is the base, which the top).
Judge only what the pictures show. part_names, when present, are the model's own part names and may help to recognise it.'''

FRONT_PROMPT = '''You are shown ONE product in several cells of a labelled grid. In every cell it stands the same way up; between the cells it is turned a quarter turn about the vertical, and the camera stays where it is, a little to the right of straight ahead and slightly above.
Decide which cell would make the best main product photograph for a shop listing: the view a catalogue would choose, in which the product's shape and its recognisable features read most clearly. A product with a front (a cabinet's drawers and doors, an appliance's controls, a device's screen) shows that front and a little of one side. A long tool (a drill, a saw, a wrench, a grinder) shows its long side in profile so that its whole length is seen, never end-on with its tip pointing at the camera or away from it. A view of the back, or one that hides the main features, is wrong.
Cell A is how the product stands now. Keep A unless another cell is clearly the better main photograph: two views that are mirror images of each other, or equally good, are no reason to turn it.
Return data only:
- front: the letter of that cell.
- confident: true only when that cell is clearly a better main photograph than cell A (or, for A, than every other cell).
- reason: one sentence in the language of reason_language.'''


# ---- rotations: plan['rotation'] is XYZ Euler angles in degrees, as Blender applies them ----

def matrix(rotation):
    x, y, z = (math.radians(float(value)) for value in rotation)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return [[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
            [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
            [-sy, sx * cy, cx * cy]]


def _product(a, b):
    return [[sum(a[row][k] * b[k][column] for k in range(3)) for column in range(3)] for row in range(3)]


def _angle(radians):
    degrees = math.degrees(radians)
    degrees = (degrees + 180) % 360 - 180
    if abs(degrees + 180) < 1e-6: degrees = 180.0
    nearest = round(degrees)
    return float(nearest) if abs(degrees - nearest) < 1e-6 else round(degrees, 4)


def euler(m):
    """The XYZ Euler angles of a rotation matrix, in degrees within (-180, 180]."""
    sy = max(-1.0, min(1.0, -m[2][0]))
    if abs(sy) < 1 - 1e-9:
        return [_angle(math.atan2(m[2][1], m[2][2])), _angle(math.asin(sy)), _angle(math.atan2(m[1][0], m[0][0]))]
    # Looking straight along an axis: only the sum (or difference) of the other two angles is defined.
    if sy > 0:
        return [_angle(math.atan2(m[0][1], m[1][1])), 90.0, 0.0]
    return [_angle(math.atan2(-m[0][1], m[1][1])), -90.0, 0.0]


def same_up(a, b, tolerance=1e-4):
    """Two rotations that put the same side of the model on top; they may face different ways."""
    first, second = matrix(a)[2], matrix(b)[2]
    return all(abs(first[column] - second[column]) <= tolerance for column in range(3))


def same(a, b, tolerance=1e-4):
    """Two rotations that pose the model identically, however their angles are written."""
    first, second = matrix(a), matrix(b)
    return all(abs(first[row][column] - second[row][column]) <= tolerance for row in range(3) for column in range(3))


def then(rotation, extra):
    """``rotation`` followed by ``extra``, both about the scene's fixed axes."""
    return euler(_product(matrix(extra), matrix(rotation)))


def turned(rotation, degrees):
    """The pose turned about the vertical: which side faces the camera changes, what is up does not."""
    return then(rotation, [0, 0, degrees])


def flipped(rotation):
    """The pose turned upside-down."""
    return then(rotation, [180, 0, 0])


def oriented_dimensions(dimensions, rotation):
    """The box around a box of ``dimensions`` after ``rotation``: its width, depth and height as posed."""
    m = matrix(rotation)
    return [sum(abs(m[row][column]) * float(dimensions[column]) for column in range(3)) for row in range(3)]


def valid_rotation(value):
    return (isinstance(value, list) and len(value) == 3
            and all(type(item) in (int, float) and math.isfinite(item) and -360 <= item <= 360 for item in value))


# ---- the candidate pictures ----

def _blender(request, folder, cancelled=None):
    import engine
    executable = engine.blender_path(); script = ROOT / 'blender' / 'orient.py'
    if not executable or not script.is_file():
        raise ValueError('朝向检查组件未就绪，请确认 Blender 可用并已更新 blender/orient.py。')
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / 'request.json', {**request, 'output_dir': str(folder)})
    (folder / 'result.json').unlink(missing_ok=True)
    command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '4', '--python-exit-code', '1',
               '--python', str(script), '--', str(folder / 'request.json')]
    started = time.time(); stop = cancelled or (lambda: False)
    try:
        with (folder / 'blender.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while process.poll() is None:
                if stop():
                    process.terminate(); raise InterruptedError()
                if time.time() - started > TIMEOUT:
                    process.kill(); raise ValueError('朝向检查超时，模型保持当前朝向。')
                time.sleep(.2)
    except InterruptedError:
        raise
    except OSError as exc:
        raise ValueError('朝向检查未能启动，模型保持当前朝向。') from exc
    try:
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        result = None
    if process.returncode or not isinstance(result, dict) or result.get('success') is not True:
        raise ValueError('朝向检查失败，模型保持当前朝向。')
    return result


def picture(project, plan, poses, folder, cancelled=None):
    """Measure and render the given poses [(id, label, rotation)]: rows of {'id','label','rotation','path',...}."""
    import engine
    request = {'model_path': project['model_path'], 'plan': plan, 'size': SIZE, 'samples': 16,
               'candidates': [{'id': ident, 'rotation': rotation} for ident, _, rotation in poses]}
    result = _blender(request, folder, cancelled)
    found = {row.get('id'): row for row in result.get('candidates', []) if isinstance(row, dict)}
    rows = []
    for ident, label, rotation in poses:
        row = found.get(ident)
        if not row or not isinstance(row.get('image'), str):
            raise ValueError('朝向检查没有返回全部候选姿态。')
        path = folder / row['image']
        engine.verify_image(path, 'PNG')
        rows.append({'id': ident, 'label': label, 'rotation': [float(value) for value in rotation], 'path': path,
                     'dimensions': row.get('dimensions'), 'support_ratio': row.get('support_ratio'),
                     'weight_height': row.get('weight_height'), 'tip_angle': row.get('tip_angle'), 'parts': row.get('parts') or []})
    return rows


def sheet(rows, target, columns=3):
    """One picture of all candidates, each cell lettered, for the vision model."""
    from PIL import Image, ImageDraw, ImageFont
    gap = 10; cell = SIZE
    lines = math.ceil(len(rows) / columns)
    canvas = Image.new('RGB', (columns * cell + (columns + 1) * gap, lines * cell + (lines + 1) * gap), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=34)
    except TypeError:                       # an older Pillow has one small built-in font only
        font = ImageFont.load_default()
    for index, row in enumerate(rows):
        left = gap + (index % columns) * (cell + gap); top = gap + (index // columns) * (cell + gap)
        with Image.open(row['path']) as tile:
            canvas.paste(tile.convert('RGB').resize((cell, cell)), (left, top))
        draw.rectangle((left, top, left + 48, top + 48), fill=(20, 24, 32))
        box = draw.textbbox((0, 0), LETTERS[index], font=font)
        draw.text((left + 24 - (box[0] + box[2]) / 2, top + 24 - (box[1] + box[3]) / 2), LETTERS[index], fill=(255, 255, 255), font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, format='PNG')
    return target


# ---- choosing ----

def _words(name):
    return set(re.findall(r'[a-z]+', re.sub(r'([a-z])([A-Z])', r'\1 \2', str(name)).casefold()))


def _cue(name):
    """-1 when a part's name says it belongs at the bottom, +1 at the top, 0 when it says nothing."""
    text = str(name).casefold(); words = _words(name)
    def named(cues): return any(cue in text if not cue.isascii() else cue in words for cue in cues)
    low, high = named(BOTTOM_WORDS), named(TOP_WORDS)
    return 0 if low == high else -1 if low else 1


def by_names(rows):
    """The pose whose named parts sit where their names say: (row, reason) or None.

    Part names are only trusted when they are unambiguous: several named parts,
    or one that sits clearly at an end, and one pose that fits them much better
    than the file's own.
    """
    scores = {}
    for row in rows:
        cues = [(_cue(part.get('name')), part.get('height')) for part in row['parts'] if isinstance(part, dict)]
        cues = [(cue, height) for cue, height in cues if cue and type(height) in (int, float)]
        if not cues:
            return None
        # A wheel at the bottom scores +1, at the top -1; a lid the other way round.
        scores[row['id']] = (sum(cue * height for cue, height in cues) / len(cues), len(cues))
    best = max(rows, key=lambda row: scores[row['id']][0])
    score, count = scores[best['id']]; own = scores[rows[0]['id']][0]
    if best is rows[0] or score < .45 or score - own < .6:
        return None
    names = [str(part['name']) for part in best['parts'] if _cue(part.get('name'))][:3]
    return best, '按部件名称判断：%s 应在%s。' % ('、'.join(names), '底部' if any(_cue(name) < 0 for name in names) else '顶部')


def _vision():
    import ai_config
    value = ai_config.get_vision_config()
    return value if all(value.get(key) for key in ('enabled', 'api_key', 'model')) else None


def _look(config, system, facts, schema, image):
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, max_tokens=900)
    message = body['messages'][-1]
    if not isinstance(message.get('content'), list):
        raise ValueError('unexpected request shape')
    message['content'][:0] = [{'type': 'text', 'text': 'CANDIDATE_POSES: the same model in each lettered cell.'},
                              ai._image(image, config['provider'], max_size=1280)]
    answer = ai._response(ai._post(config, body), config, ai.TOOL_NAME)
    ai._check_schema(answer, schema)
    return answer


def _schema(key, count, extra=None):
    letters = list(LETTERS[:count])
    properties = {key: {'type': 'string', 'enum': letters}, 'confident': {'type': 'boolean'},
                  'reason': {'type': 'string', 'minLength': 1, 'maxLength': 300}}
    properties.update(extra or {})
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def by_vision(rows, image, part_names=()):
    """What the vision model makes of the candidates: {'row','confident','reason','product','also'} or None when it cannot be asked."""
    config = _vision()
    if not config:
        return None
    letters = list(LETTERS[:len(rows)])
    schema = _schema('upright', len(rows), {'product': {'type': 'string', 'minLength': 1, 'maxLength': 60},
                                            'also_normal': {'type': 'array', 'items': {'type': 'string', 'enum': letters}, 'maxItems': len(rows)}})
    facts = {'cells': letters, 'reason_language': 'zh-CN'}
    names = [str(name)[:80] for name in part_names if str(name).strip()][:40]
    if names: facts['part_names'] = names
    try:
        answer = _look(config, UPRIGHT_PROMPT, facts, schema, image)
    except Exception:            # no answer leaves the model as it is; it is never a reason to fail an upload
        return None
    return {'row': rows[letters.index(answer['upright'])], 'confident': answer['confident'],
            'reason': re.sub(r'\s+', ' ', answer['reason']).strip()[:300], 'product': re.sub(r'\s+', ' ', answer['product']).strip()[:60],
            'also': [rows[letters.index(letter)]['id'] for letter in dict.fromkeys(answer['also_normal'])]}


def front_by_vision(rows, image):
    config = _vision()
    if not config:
        return None
    letters = list(LETTERS[:len(rows)])
    try:
        answer = _look(config, FRONT_PROMPT, {'cells': letters, 'reason_language': 'zh-CN'}, _schema('front', len(rows)), image)
    except Exception:
        return None
    return {'row': rows[letters.index(answer['front'])], 'confident': answer['confident'],
            'reason': re.sub(r'\s+', ' ', answer['reason']).strip()[:300]}


def choose(rows, sheet_path, part_names=()):
    """Pick a pose from the pictured candidates: (row, source, reason, product).

    The file's own pose is left only on evidence: a vision model that is
    confident and does not count the file's pose among the normal ones, or,
    where no vision model can be asked, unambiguous part names. ``source`` is
    'file' when nothing could be told, and the caller then changes nothing.
    """
    own = rows[0]
    seen = by_vision(rows, sheet_path, part_names)
    if seen is not None:
        if seen['row'] is own or own['id'] in seen['also']:
            return own, 'vision', seen['reason'], seen['product']
        if not seen['confident']:
            return own, 'file', '视觉模型不能确定哪一面朝上，朝向未自动调整；可在下方改选姿态。', seen['product']
        return seen['row'], 'vision', seen['reason'], seen['product']
    named = by_names(rows)
    if named:
        return named[0], 'names', named[1], None
    return own, 'file', '未能调用视觉模型，部件名称也没有说明上下，朝向未自动调整；可在下方改选姿态。', None


def _url(path):
    return '/files/' + Path(path).relative_to(DATA).as_posix()


def decide(project, plan, folder, cancelled=None, progress=None):
    """Picture the candidates and choose: the record stored as project['orientation'].

    Raises ValueError when the candidates could not be pictured; choosing itself
    never fails (it falls back to the file's own pose).
    """
    say = progress or (lambda stage: None)
    say('检查模型摆放朝向')
    rows = picture(project, plan, POSES, folder / 'up', cancelled)
    sheet_path = sheet(rows, folder / 'up' / 'sheet.png')
    analysis = project.get('analysis') or {}
    names = [item.get('name') for item in analysis.get('objects', []) if isinstance(item, dict) and item.get('type', 'MESH') == 'MESH']
    names = names or [part.get('name') for part in rows[0]['parts']]
    if _vision(): say('由视觉模型判断哪一面朝上')
    chosen, source, reason, product = choose(rows, sheet_path, names)
    rotation = list(chosen['rotation']); front = None
    if chosen is not rows[0]:
        # A file that was not the right way up says nothing reliable about its front either.
        front = _front(project, plan, rotation, folder, cancelled, say)
        if front:
            rotation = front['rotation']
    record = {'version': VERSION, 'status': 'ready', 'checked_at': time.time(), 'source': source, 'reason': reason,
              'product': product, 'chosen': chosen['id'], 'changed': chosen is not rows[0], 'rotation': rotation,
              'candidates': [{'id': row['id'], 'label': row['label'], 'rotation': row['rotation'], 'image_url': _url(row['path']),
                              'dimensions': row['dimensions'], 'support_ratio': row['support_ratio'], 'tip_angle': row['tip_angle']}
                             for row in rows],
              'sheet_url': _url(sheet_path)}
    if front:
        record['front'] = {'turn': front['turn'], 'reason': front['reason'], 'sheet_url': front['sheet_url']}
    return record


def _front(project, plan, rotation, folder, cancelled, say):
    """Which quarter turn of the righted pose faces the camera best; None when nobody can tell."""
    if not _vision():
        return None
    try:
        say('判断产品正面')
        poses = [('turn%d' % degrees, '转 %d°' % degrees, turned(rotation, degrees)) for degrees in TURNS]
        rows = picture(project, plan, poses, folder / 'front', cancelled)
        sheet_path = sheet(rows, folder / 'front' / 'sheet.png', columns=2)
        seen = front_by_vision(rows, sheet_path)
    except InterruptedError:
        raise
    except Exception:
        return None
    if not seen or not seen['confident'] or seen['row'] is rows[0]:
        return None
    return {'turn': TURNS[rows.index(seen['row'])], 'rotation': seen['row']['rotation'], 'reason': seen['reason'], 'sheet_url': _url(sheet_path)}


def first_pose(project, analysis, folder, cancelled=None, progress=None):
    """The pose for a newly analysed upload. Never raises: an upload does not fail over its orientation."""
    try:
        return decide({**project, 'analysis': analysis}, project['plan'], folder, cancelled, progress)
    except InterruptedError:
        raise
    except Exception as exc:
        return {'version': VERSION, 'status': 'failed', 'checked_at': time.time(), 'source': 'file', 'changed': False,
                'rotation': [0, 0, 0], 'candidates': [],
                'reason': str(exc)[:200] if isinstance(exc, ValueError) else '朝向检查未完成，模型保持文件原始朝向。'}


# ---- applying a pose to the plan ----

def repose(plan, rotation):
    """The plan with the product in another pose. What was fitted to the old pose is dropped."""
    result = copy.deepcopy(plan)
    result['rotation'] = [float(value) for value in rotation]
    # Saved studio scenes hold the product as it stood; hand-set framing was set on its old outline.
    result.pop('scene_checkpoints', None)
    for shot in result.get('shots', []):
        for key in ('camera_distance', 'ortho_scale', 'target_offset'):
            shot.pop(key, None)
    return result


def public(record):
    """What the interface is given of project['orientation']."""
    if not isinstance(record, dict):
        return None
    keys = ('version', 'status', 'checked_at', 'source', 'reason', 'product', 'chosen', 'changed', 'rotation', 'candidates', 'front')
    return {key: copy.deepcopy(record[key]) for key in keys if key in record}


def apply(project_id, body):
    """Put the product in the pose the user picked, as one undoable version, and preview it."""
    import engine
    import planner
    if not isinstance(body, dict): raise ValueError('朝向请求格式无效')
    expected = body.get('expected_version')
    if expected is not None and (type(expected) is not int or expected < 1): raise ValueError('方案版本无效')
    actions = [key for key in ('candidate', 'turn', 'flip', 'rotation') if key in body]
    if len(actions) != 1: raise ValueError('请指定一种朝向调整')
    with LOCK:
        project = get('project', project_id)
        if not project.get('analysis') or project.get('status') != 'ready': raise ValueError('请先等待模型分析完成')
        if expected is not None and project['version'] != expected: raise engine.JobConflictError('方案已更新，请重新载入后再调整朝向。')
        current = project['plan'].get('rotation', [0, 0, 0])
        if 'candidate' in body:
            rows = (project.get('orientation') or {}).get('candidates') or [{'id': ident, 'rotation': rotation} for ident, _, rotation in POSES]
            row = next((item for item in rows if item.get('id') == body['candidate']), None)
            if row is None or not valid_rotation(row.get('rotation')): raise ValueError('没有这个候选姿态')
            rotation = row['rotation']; reason = '改选摆放姿态'
        elif 'turn' in body:
            if body['turn'] not in (90, -90, 180): raise ValueError('转向角度无效')
            rotation = turned(current, body['turn']); reason = '转动产品朝向'
        elif 'flip' in body:
            if body['flip'] is not True: raise ValueError('翻转请求无效')
            rotation = flipped(current); reason = '上下翻转产品'
        else:
            if not valid_rotation(body['rotation']): raise ValueError('朝向角度无效')
            rotation = [float(value) for value in body['rotation']]; reason = '调整摆放朝向'
        if same(rotation, current):
            return engine.project_view(project_id)
        engine.yield_to_user(project_id)
        if any(job['project_id'] == project_id and job['status'] in ('queued', 'running') for job in all_records('job')):
            raise engine.JobConflictError('当前项目已有渲染任务，请等待完成或取消后再调整朝向。')
        if any(task['project_id'] == project_id and task['status'] in ACTIVE for task in all_records(KIND) + all_records('scene_task')):
            raise engine.JobConflictError('当前项目有任务正在进行，请稍候再调整朝向。')
        plan = planner.validate(repose(project['plan'], rotation))
        project.setdefault('history', []).append({'version': project['version'], 'plan': copy.deepcopy(project['plan']),
                                                  'at': time.time(), 'reason': reason})
        project['plan'] = plan; project['version'] += 1
        put('project', project)
        _preview(project, body.get('shot_id'))
    return engine.project_view(project_id)


def _preview(project, shot_id=None):
    """Show the new pose at once: a preview of the shot on screen, or of the main image."""
    import engine
    import scene_audience
    shots = [shot['id'] for shot in project['plan']['shots']]
    drawn = [shot for shot in shots if shot not in scene_audience.image_shots(project['plan'])]
    target = shot_id if shot_id in drawn else 'hero' if 'hero' in drawn else (drawn[0] if drawn else None)
    if target is None:
        return None
    try:
        engine.require_render_runtime(project['plan'], [target])
        return engine.submit(project['id'], 'preview', 'preview', [target], expected_version=project['version'])
    except (ValueError, OSError):
        return None


# ---- checking again, for a project that already exists ----

def _update(task_id, **fields):
    with LOCK:
        task = get(KIND, task_id)
        if task['status'] == 'cancelled':
            raise InterruptedError()
        task.update(fields); put(KIND, task)
        return task


def task_view(task):
    keys = ('id', 'project_id', 'status', 'stage', 'error', 'applied_version', 'changed', 'created_at', 'finished_at', 'base_version')
    return {key: copy.deepcopy(task[key]) for key in keys if key in task}


def latest(project_id):
    tasks = [task for task in all_records(KIND) if task.get('project_id') == project_id]
    return task_view(max(tasks, key=lambda task: task.get('created_at', 0))) if tasks else None


def submit(project_id, body):
    """Look at the model again and stand it the right way up."""
    import engine
    if not isinstance(body, dict): raise ValueError('朝向请求格式无效')
    expected = body.get('expected_version')
    if expected is not None and (type(expected) is not int or expected < 1): raise ValueError('方案版本无效')
    with LOCK:
        project = get('project', project_id)
        if not project.get('analysis') or project.get('status') != 'ready': raise ValueError('请先等待模型分析完成')
        if expected is not None and project['version'] != expected: raise engine.JobConflictError('方案已更新，请重新载入后再检查朝向。')
        engine.require_runtime('orientation')
        engine.yield_to_user(project_id)
        if any(job['project_id'] == project_id and job['status'] in ('queued', 'running') for job in all_records('job')):
            raise engine.JobConflictError('当前项目已有渲染任务，请等待完成或取消后再检查朝向。')
        if any(task['project_id'] == project_id and task['status'] in ACTIVE for task in all_records(KIND) + all_records('scene_task')):
            raise engine.JobConflictError('当前项目有任务正在进行，请稍候。')
        task = {'id': uuid.uuid4().hex, 'project_id': project_id, 'status': 'queued', 'stage': '等待开始', 'error': None,
                'base_version': project['version'], 'shot_id': body.get('shot_id') if isinstance(body.get('shot_id'), str) else None,
                'created_at': time.time()}
        put(KIND, task)
        threading.Thread(target=_run, args=(task['id'],), daemon=True, name='orient-' + task['id'][:8]).start()
        return task_view(task)


def _run(task_id):
    import engine
    import planner
    with LOCK:
        try: task = get(KIND, task_id)
        except KeyError: return
        if task['status'] != 'queued': return
    try:
        _update(task_id, status='running', stage='检查模型摆放朝向', started_at=time.time())
        project = get('project', task['project_id'])
        cancelled = lambda: get(KIND, task_id)['status'] == 'cancelled'
        # Candidates are pictured from the file's own pose, whatever the plan holds now.
        record = decide(project, project['plan'], DATA / project['id'] / 'orientation' / task_id, cancelled,
                        lambda stage: _update(task_id, stage=stage))
        with LOCK:
            current = get('project', task['project_id'])
            if get(KIND, task_id)['status'] == 'cancelled': return
            if current['version'] != task['base_version']:
                raise ValueError('方案在检查朝向期间已被修改，本次结果未应用，请重试。')
            if any(job['project_id'] == current['id'] and job['status'] in ('queued', 'running')
                   and job.get('origin') not in (engine.POLISH_ORIGIN, engine.THUMBNAIL_ORIGIN) for job in all_records('job')):
                raise ValueError('已有渲染任务开始执行，本次朝向结果未应用，请稍后重试。')
            engine.yield_to_user(current['id'])
            current['orientation'] = record
            now = current['plan'].get('rotation', [0, 0, 0])
            # Nothing could be told: the pose stays. The right side is already up: the way the user turned it stays.
            changed = record['source'] != 'file' and not same_up(record['rotation'], now)
            if changed:
                plan = planner.validate(repose(current['plan'], record['rotation']))
                current.setdefault('history', []).append({'version': current['version'], 'plan': copy.deepcopy(current['plan']),
                                                          'at': time.time(), 'reason': '自动摆正模型'})
                current['plan'] = plan; current['version'] += 1
            put('project', current)
            if changed:
                _preview(current, task.get('shot_id'))
            done = get(KIND, task_id)
            done.update(status='completed', stage='已摆正模型' if changed else '朝向无需调整', changed=changed,
                        applied_version=current['version'], finished_at=time.time())
            put(KIND, done)
    except InterruptedError:
        pass
    except Exception as exc:
        with LOCK:
            task = get(KIND, task_id)
            if task['status'] != 'cancelled':
                message = str(exc)[:300] if isinstance(exc, ValueError) else '朝向检查未完成，模型保持当前朝向。'
                task.update(status='failed', stage='朝向未检查', error=message, finished_at=time.time()); put(KIND, task)


def cancel(project_id, task_id):
    with LOCK:
        task = get(KIND, task_id)
        if task.get('project_id') != project_id: raise KeyError(task_id)
        if task['status'] in ACTIVE:
            task.update(status='cancelled', stage='已取消，朝向未修改', error=None, finished_at=time.time()); put(KIND, task)
        return task_view(task)


def recover_interrupted():
    for task in all_records(KIND):
        if task.get('status') in ACTIVE:
            task.update(status='failed', stage='朝向未检查', error='应用重启，朝向检查被中断；模型保持当前朝向，可重新检查。'); put(KIND, task)

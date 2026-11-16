"""Which way up a product stands.

A model file's axes are whatever its CAD program used, so a tool can arrive
upside-down or lying on its back, and every picture then shows it that way. At
analysis, and again whenever the user asks, the model is pictured in six
candidate poses (blender/orient.py) and one of them is chosen:

    vision   the pose agent looks at the six pictures and decides how the product rests
    names    without one, parts called wheel, foot or base belong at the bottom, lid or top at the top
    file     with neither, the file's own orientation is kept

The pose agent does not pick a letter at a glance, which goes wrong for tools:
"the battery is at the bottom" is true of a grinder lying on its battery's
underside and of one balanced on the battery's end. It works in order: what
the product is, how that kind of product is set down, what touches the floor
in every candidate, and only then which candidate that is. It is given what
was measured in each pose (which named parts reach the floor and the top,
whether the model lies level or stands tall). Before a pose is changed the
chosen one is looked at again from four sides, where the same question is far
easier, and a pose that does not rest as described is not applied. That second
look also settles which side faces the camera.

Judging a pose asks more of a vision model than describing a picture.
AI_POSE_MODEL names a stronger model for this agent alone, on the vision
model's endpoint and key.

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

VERSION = 2
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
CARRIERS = ('flat_underside', 'feet_or_wheels', 'side', 'unsure')     # what carries a product in its proper pose
# The steps of a pose check in the order they happen, and how far along each one starts.
STAGES = (('检查模型摆放朝向', 0), ('由视觉模型判断产品应如何摆放', 40), ('从四个方向复核姿态并判断正面', 70))
# Words in part names that say where the part belongs. Only whole words count for the short English ones.
BOTTOM_WORDS = ('脚轮', '轮子', '车轮', '万向轮', '底座', '底板', '底盘', '支脚', '脚垫', '地脚', '柜脚', '桌脚', '椅脚',
                'caster', 'castor', 'wheel', 'foot', 'feet', 'leg', 'legs', 'base', 'bottom', 'stand', 'pedestal', 'skid')
# A cover or a cap can be anywhere on a product (a battery cover is underneath), so only names that mean "the top" count.
TOP_WORDS = ('顶盖', '上盖', '顶板', '台面', 'lid', 'top', 'worktop', 'countertop', 'tabletop', 'roof')

POSE_PROMPT = '''You decide how a product's 3D model should stand for product photographs. The model file may have been exported upside-down or lying on a side, so you are shown the SAME model in several candidate poses, as a grid of lettered cells. In every cell it rests on the same grey floor and is seen from the same camera.
Work in this order and return data only:
1. product: what it is, two to five plain English words.
2. rests_on: how this kind of product is set down on a bench ready for use, or shown in a catalogue: which part is on the ground, what points up, and whether its long axis is level or upright. One English sentence, from what you know about the product, written before you judge the cells. carried_by: what carries it in that pose: flat_underside (a flat base, or the flat underside of a housing or battery pack), feet_or_wheels (feet, legs, wheels, casters, skids, a rim), side (it simply lies on one of its sides), or unsure.
3. cells: for EVERY cell, on_ground says which parts touch the floor there, and fits says whether that is the pose of step 2.
4. upright: the one cell that fits best. also_normal: other cells that are an equally normal way to show this product, or empty. confident: true only when you recognised the product and one cell clearly fits.
5. reason: one sentence in the language of reason_language, for the person who uploaded the model: what the product is and what it rests on in the chosen cell.
What "set down properly" means:
- A product rests on the surfaces made to carry it: its base, feet, wheels, skids, or the flat underside of its battery pack. It is never balanced on an edge, a tip, a handle, a cutting disc or the narrow end of a part.
- Cordless tools rest on the flat underside of the battery pack. A drill or impact driver stands upright on it. A tool whose body is long and level in use (angle grinder, circular saw, jigsaw, sander, multi-tool) lies level: the battery's underside on the ground, the disc, blade or base plate facing down, the body and main grip above them.
- Wheels, casters and feet go on the floor; lids, worktops and carrying handles go on top; printed lettering reads upright.
- Things with no working top or bottom (a wrench, a screwdriver) lie flat on their largest side.
facts, when present, were measured from the model for each cell. stance says whether it lies level or stands tall there. flat_face_on_floor is the share of its footprint that is a flat face lying on the floor: near 0 means it touches only along edges, points or a curved surface, as a housing on its rounded back or a tool on its side does, and as anything on wheels or feet does too; a clearly larger share than in the opposite cell means a flat base is down. on_floor and on_top name the model's own parts that reach the floor and the top; part names can be meaningless, but where they mean something, trust them over a small picture. Two cells that look alike are usually the same pose upside-down: use the facts to tell them apart.'''

FRONT_PROMPT = '''You are shown ONE product in several cells of a labelled grid. In every cell it stands the same way up; between the cells it is turned a quarter turn about the vertical, and the camera stays where it is, a little to the right of straight ahead and slightly above. Answer two questions and return data only.
First, does it rest properly? product and should_rest, when given, say what it is and how it should be set down. The four cells show one pose from four sides, so what is on the floor is easy to see here. rests_properly is false when the pictures clearly show something else: it is upside-down, it stands on an end, on a disc or blade edge or on a handle, or the part that should carry it is in the air. It is true when it rests as described, and also when you cannot tell.
Second, which cell would make the best main product photograph for a shop listing: the view a catalogue would choose, in which the product's shape and its recognisable features read most clearly. A product with a front (a cabinet's drawers and doors, an appliance's controls, a device's screen) shows that front and a little of one side. A long tool (a drill, a saw, a wrench, a grinder) shows its long side in profile so that its whole length is seen, never end-on with its tip pointing at the camera or away from it. A view of the back, or one that hides the main features, is wrong.
Cell A is how the product stands now. Keep A unless another cell is clearly the better main photograph: two views that are mirror images of each other, or equally good, are no reason to turn it.
- rests_properly: as above.
- front: the letter of the cell for the main photograph.
- confident: true only when that cell is clearly a better main photograph than cell A (or, for A, than every other cell).
- reason: one sentence in the language of reason_language. When rests_properly is false, say what is on the floor instead.'''


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


def about(axis, degrees):
    """The rotation by ``degrees`` about ``axis`` (a direction in the scene's fixed frame, right-handed), as XYZ Euler angles."""
    x, y, z = (float(value) for value in axis)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        return [0.0, 0.0, 0.0]
    x, y, z = x / length, y / length, z / length
    angle = math.radians(float(degrees)); c, s = math.cos(angle), math.sin(angle); t = 1 - c
    return euler([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                  [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                  [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


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


def progress_of(stage, start=0, end=100):
    """How far a pose check is when it enters ``stage``, as a percentage between ``start`` and ``end``."""
    share = dict(STAGES).get(stage)
    return None if share is None else round(start + (end - start) * share / 100)


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


def picture(project, plan, poses, folder, cancelled=None, view=None, floor=True, size=SIZE, samples=16, render=True):
    """Measure and render the given poses [(id, label, rotation)]: rows of {'id','label','rotation','path',...}.

    ``view`` is the camera's (azimuth, elevation), the standard three-quarter view when None; ``floor`` False shows the
    product on a plain background, as it would be held rather than set down. With ``render`` False the poses are only
    measured and rows have no 'path'.
    """
    import engine
    request = {'model_path': project['model_path'], 'plan': plan, 'size': size, 'samples': samples, 'floor': bool(floor),
               'render': bool(render), 'candidates': [{'id': ident, 'rotation': rotation} for ident, _, rotation in poses]}
    if view:
        request.update(azimuth=float(view[0]), elevation=float(view[1]))
    result = _blender(request, folder, cancelled)
    found = {row.get('id'): row for row in result.get('candidates', []) if isinstance(row, dict)}
    rows = []
    for ident, label, rotation in poses:
        row = found.get(ident)
        if not row or (render and not isinstance(row.get('image'), str)):
            raise ValueError('朝向检查没有返回全部候选姿态。')
        path = None
        if render:
            path = folder / row['image']
            engine.verify_image(path, 'PNG')
        rows.append({'id': ident, 'label': label, 'rotation': [float(value) for value in rotation], 'path': path,
                     'dimensions': row.get('dimensions'), 'support_ratio': row.get('support_ratio'),
                     'weight_height': row.get('weight_height'), 'tip_angle': row.get('tip_angle'),
                     'flat_contact': row.get('flat_contact'), 'parts': row.get('parts') or []})
    return rows


def sheet(rows, target, columns=3, cell=SIZE):
    """One picture of all candidates, each cell lettered, for the vision model."""
    from PIL import Image, ImageDraw, ImageFont
    gap = 10
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


def pose_model():
    """The model named for judging poses (AI_POSE_MODEL), or '' for the configured vision model."""
    import os
    try:
        import ai_config
        values = ai_config.read_env()
    except Exception:
        values = {}
    return str(values.get('AI_POSE_MODEL', os.getenv('AI_POSE_MODEL', ''))).strip()[:120]


def _vision():
    """The endpoint the pose agent asks: the vision model's, with its own model when one is named."""
    import ai_config
    value = ai_config.get_vision_config()
    if not all(value.get(key) for key in ('enabled', 'api_key', 'model')):
        return None
    named = pose_model()
    return {**value, 'model': named} if named else value


def _look(config, system, facts, schema, image, max_tokens=900):
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, max_tokens=max_tokens)
    message = body['messages'][-1]
    if not isinstance(message.get('content'), list):
        raise ValueError('unexpected request shape')
    message['content'][:0] = [{'type': 'text', 'text': 'CANDIDATE_POSES: the same model in each lettered cell.'},
                              ai._image(image, config['provider'], max_size=1280)]
    answer = ai._response(ai._post(config, body), config, ai.TOOL_NAME)
    ai._check_schema(answer, schema)
    return answer


def _text(low, high):
    return {'type': 'string', 'minLength': low, 'maxLength': high}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def pose_schema(count):
    letters = list(LETTERS[:count])
    cell = _object({'cell': {'type': 'string', 'enum': letters}, 'on_ground': _text(1, 200), 'fits': {'type': 'boolean'}})
    # The order is the order of the reasoning: what it is, how it rests, each cell, and only then the choice.
    return _object({'product': _text(1, 60), 'rests_on': _text(1, 400), 'carried_by': {'type': 'string', 'enum': list(CARRIERS)},
                    'cells': {'type': 'array', 'items': cell, 'minItems': count, 'maxItems': count},
                    'upright': {'type': 'string', 'enum': letters},
                    'also_normal': {'type': 'array', 'items': {'type': 'string', 'enum': letters}, 'maxItems': count},
                    'confident': {'type': 'boolean'}, 'reason': _text(1, 300)})


def front_schema(count):
    return _object({'rests_properly': {'type': 'boolean'}, 'front': {'type': 'string', 'enum': list(LETTERS[:count])},
                    'confident': {'type': 'boolean'}, 'reason': _text(1, 300)})


def cell_facts(rows):
    """What was measured in each candidate, in words the pose agent can use: how it stands and which parts reach floor and top."""
    facts = []
    for letter, row in zip(LETTERS, rows):
        fact = {'cell': letter}
        size = row.get('dimensions')
        if isinstance(size, list) and len(size) == 3 and all(type(value) in (int, float) and value > 0 for value in size):
            share = size[2] / max(size)
            fact['stance'] = 'lies level' if share <= .6 else 'stands tall' if share >= .95 else 'in between'
        if type(row.get('flat_contact')) in (int, float):
            fact['flat_face_on_floor'] = round(float(row['flat_contact']), 2)
        parts = [part for part in row.get('parts') or [] if isinstance(part, dict) and str(part.get('name', '')).strip()]
        # With hundreds of parts the lists say nothing a picture does not; with one part there is nothing to tell apart.
        if 2 <= len(parts) <= 80:
            def near(key, limit):
                found = [part for part in parts if type(part.get(key)) in (int, float) and limit(part[key])]
                found.sort(key=lambda part: part[key], reverse=key == 'high')
                return [str(part['name'])[:48] for part in found[:6]]
            fact['on_floor'] = near('low', lambda value: value <= .04)
            fact['on_top'] = near('high', lambda value: value >= .96)
        facts.append(fact)
    return facts


def contradicted(rows, row, carried_by):
    """Whether the measurements speak against a chosen pose: the agent said the product is carried by a flat
    underside, yet the same pose upside-down is the one with a flat face on the floor.

    A pose and its mirror image look alike in a small picture and are the usual mistake. This never picks a
    pose; it only withholds confidence, so a contradicted choice leaves the model as it is.
    """
    if carried_by != 'flat_underside':
        return False
    twin = next((other for other in rows if other is not row and same_up(other['rotation'], flipped(row['rotation']))), None)
    own, other = row.get('flat_contact'), (twin or {}).get('flat_contact')
    if type(own) not in (int, float) or type(other) not in (int, float):
        return False
    return other >= .08 and other >= 2 * own


def _line(value, limit):
    return re.sub(r'\s+', ' ', str(value)).strip()[:limit]


def by_vision(rows, image, part_names=()):
    """What the pose agent makes of the candidates, or None when it cannot be asked.

    {'row', 'confident', 'reason', 'product', 'also', 'rests_on', 'cells', 'model'}. An answer that
    contradicts itself (the chosen cell is one the agent said does not fit) is not confident.
    """
    config = _vision()
    if not config:
        return None
    letters = list(LETTERS[:len(rows)])
    facts = {'cells': letters, 'reason_language': 'zh-CN', 'facts': cell_facts(rows)}
    try:
        answer = _look(config, POSE_PROMPT, facts, pose_schema(len(rows)), image, max_tokens=1800)
    except Exception:            # no answer leaves the model as it is; it is never a reason to fail an upload
        return None
    said = {cell['cell']: cell for cell in answer['cells']}
    chosen = answer['upright']; row = rows[letters.index(chosen)]
    consistent = len(said) == len(letters) and said.get(chosen, {}).get('fits') is True
    doubt = contradicted(rows, row, answer['carried_by'])
    return {'row': row, 'confident': answer['confident'] and consistent and not doubt, 'doubt': doubt, 'carried_by': answer['carried_by'],
            'reason': _line(answer['reason'], 300), 'product': _line(answer['product'], 60), 'rests_on': _line(answer['rests_on'], 400),
            'also': [rows[letters.index(letter)]['id'] for letter in dict.fromkeys(answer['also_normal']) if letter != chosen],
            'cells': [{'id': rows[letters.index(letter)]['id'], 'on_ground': _line(said[letter]['on_ground'], 200), 'fits': said[letter]['fits']}
                      for letter in letters if letter in said],
            'model': config['model']}


def front_by_vision(rows, image, product=None, rests_on=None):
    """A second look at one pose from four sides: does it rest as it should, and which side should face the camera."""
    config = _vision()
    if not config:
        return None
    letters = list(LETTERS[:len(rows)])
    facts = {'cells': letters, 'reason_language': 'zh-CN'}
    if product: facts['product'] = str(product)[:60]
    if rests_on: facts['should_rest'] = str(rests_on)[:400]
    try:
        answer = _look(config, FRONT_PROMPT, facts, front_schema(len(rows)), image)
    except Exception:
        return None
    return {'row': rows[letters.index(answer['front'])], 'confident': answer['confident'], 'rests': answer['rests_properly'],
            'reason': _line(answer['reason'], 300)}


def choose(rows, sheet_path, part_names=()):
    """Pick a pose from the pictured candidates: (row, source, reason, seen).

    The file's own pose is left only on evidence: a pose agent that is
    confident and does not count the file's pose among the normal ones, or,
    where no vision model can be asked, unambiguous part names. ``source`` is
    'file' when nothing could be told, and the caller then changes nothing.
    ``seen`` is the pose agent's full answer, or None when it gave none.
    """
    own = rows[0]
    seen = by_vision(rows, sheet_path, part_names)
    if seen is not None:
        if seen['row'] is own or own['id'] in seen['also']:
            return own, 'vision', seen['reason'], seen
        if not seen['confident']:
            why = ('视觉模型选的姿态与测量不符（它认为产品靠平整底面承托，但测得平面着地的是相反的一面），'
                   if seen.get('doubt') else '视觉模型不能确定哪一面朝上，')
            return own, 'file', why + '朝向未自动调整；可在下方改选姿态。', seen
        return seen['row'], 'vision', seen['reason'], seen
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
    if _vision(): say('由视觉模型判断产品应如何摆放')
    chosen, source, reason, seen = choose(rows, sheet_path, names)
    rotation = list(chosen['rotation']); front = None; rejected = None
    if chosen is not rows[0]:
        # Before the pose is changed it is looked at again from four sides; a file that was
        # not the right way up says nothing reliable about its front either.
        front = _front(project, plan, rotation, folder, cancelled, say, seen)
        if front and front.get('rejected'):
            rejected = {'id': chosen['id'], 'reason': front['reason'], 'sheet_url': front['sheet_url']}
            chosen = rows[0]; rotation = list(chosen['rotation']); source = 'file'; front = None
            reason = '第二次查看认为所选姿态并不正确（%s），朝向未自动调整；可在下方改选姿态。' % rejected['reason']
        elif front:
            rotation = front['rotation']
    record = {'version': VERSION, 'status': 'ready', 'checked_at': time.time(), 'source': source, 'reason': reason,
              'product': seen['product'] if seen else None, 'chosen': chosen['id'], 'changed': chosen is not rows[0], 'rotation': rotation,
              'candidates': [{'id': row['id'], 'label': row['label'], 'rotation': row['rotation'], 'image_url': _url(row['path']),
                              'dimensions': row['dimensions'], 'support_ratio': row['support_ratio'], 'tip_angle': row['tip_angle'],
                              'flat_contact': row.get('flat_contact')}
                             for row in rows],
              'sheet_url': _url(sheet_path)}
    if seen:
        record.update(rests_on=seen.get('rests_on'), model=seen.get('model'), cells=seen.get('cells') or [])
    if rejected:
        record['rejected'] = rejected
    if front:
        record['front'] = {'turn': front['turn'], 'reason': front['reason'], 'sheet_url': front['sheet_url']}
    return record


def _front(project, plan, rotation, folder, cancelled, say, seen=None):
    """The second look at a pose that is about to be applied.

    {'rejected': True, ...} when it does not rest as the pose agent said it
    should; {'turn', 'rotation', ...} when another side should face the
    camera; None when the pose stands as it is, or nobody can be asked.
    """
    if not _vision():
        return None
    try:
        say('从四个方向复核姿态并判断正面')
        poses = [('turn%d' % degrees, '转 %d°' % degrees, turned(rotation, degrees)) for degrees in TURNS]
        rows = picture(project, plan, poses, folder / 'front', cancelled)
        sheet_path = sheet(rows, folder / 'front' / 'sheet.png', columns=2)
        answer = front_by_vision(rows, sheet_path, (seen or {}).get('product'), (seen or {}).get('rests_on'))
    except InterruptedError:
        raise
    except Exception:
        return None
    if answer and answer.get('rests') is False:
        return {'rejected': True, 'reason': answer['reason'], 'sheet_url': _url(sheet_path)}
    if not answer or not answer['confident'] or answer['row'] is rows[0]:
        return None
    return {'turn': TURNS[rows.index(answer['row'])], 'rotation': answer['row']['rotation'], 'reason': answer['reason'], 'sheet_url': _url(sheet_path)}


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
    keys = ('version', 'status', 'checked_at', 'source', 'reason', 'product', 'chosen', 'changed', 'rotation', 'candidates', 'front',
            'rests_on', 'model', 'cells', 'rejected')
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
    keys = ('id', 'project_id', 'status', 'stage', 'progress', 'error', 'applied_version', 'changed', 'created_at', 'finished_at', 'base_version')
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
                        lambda stage: _update(task_id, stage=stage, progress=progress_of(stage, 5, 95) or 5))
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

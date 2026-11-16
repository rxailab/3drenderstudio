"""The pose of the product in a generated scene: held or set down the way it is used there.

The main image shows the product the way it stands on a shelf. A picture of it at
work shows it the way it is held or put down for that work: a grinder level with
its disc tilted onto the steel, a pipe cutter round a pipe, a tool lying on the
bench. The pose is chosen by a vision model from renders of real poses, seen from
the scene's own camera, so it is always a pose the 3D model can really take. The
chosen pose is then rendered and locked into the picture as before (scene_lock):
the image model paints the scene around it and cannot bend or redraw the product.

Two steps for a product someone holds: which way up and which way round (six ways
up, each from two sides), then a small adjustment of that pose (turned round,
leaning, tipped, turned on the spot). A product nobody holds must rest on a
surface: it is shown the same twelve ways, standing on a floor, with what was
measured of each (whether it lies level, which parts touch the floor, whether it
clearly tips over), and is not adjusted afterwards: a tilted product set down
would fall. Without a vision model, or when anything here fails, the product
keeps its pose.
"""
import hashlib
import json
import math
import re
import time
from pathlib import Path

import orientation
from studio_store import DATA

VERSION = 1
SIZE = 288                 # pixels per candidate; the vision model sees the sheet at about this size anyway
SAMPLES = 10
COLUMNS = 4
TIPS_OVER = -15.0          # degrees: a weight this far outside what the product stands on cannot rest, even leaning onto a second point
MODES = ('auto', 'keep')
HOLDS = ('hands', 'person', 'nobody', 'scene')
SIDES = ('below', 'above', 'left', 'right', 'front', 'behind', 'around', 'none')
POINTS = ('up', 'down', 'left', 'right', 'toward_camera', 'away', 'hidden')
# Six ways up, as seen from the camera: (id, label, axis, degrees). 'toward' is the level line from the product to the
# camera (a turn about it rolls the product in the picture), 'right' the picture's level right-hand direction.
UPS = (('as_is', '原姿态', None, 0), ('upside_down', '倒置', 'toward', 180), ('top_left', '顶部朝左躺倒', 'toward', 90),
       ('top_right', '顶部朝右躺倒', 'toward', -90), ('top_to_camera', '顶部朝向镜头', 'right', 90), ('top_away', '顶部背向镜头', 'right', -90))
TURNS = (0, 90)
# Small changes to a pose someone holds: (id, label, axis, degrees); 'up' is the vertical.
ADJUSTMENTS = (('chosen', '所选姿态', None, 0), ('turned_round', '转到另一面', 'up', 180),
               ('lean_left', '向左倾斜 25°', 'toward', 25), ('lean_right', '向右倾斜 25°', 'toward', -25),
               ('lean_left_more', '向左倾斜 50°', 'toward', 50), ('lean_right_more', '向右倾斜 50°', 'toward', -50),
               ('tip_toward', '顶部前倾 30°', 'right', 30), ('tip_away', '顶部后仰 30°', 'right', -30),
               ('turn_left', '原地左转 35°', 'up', 35), ('turn_right', '原地右转 35°', 'up', -35))

POSE_PROMPT = '''You pose a product for one photograph of it in use. scene describes the photograph. people says who is in it: "hands" (a close-up of hands holding and working with the product), "person" (a person using it, seen from about the waist up), "nobody" (the product set down where it is used, nobody touching it) or "scene" (as the scene describes). You are shown the SAME product in several candidate poses, a grid of lettered cells, each seen from the camera that will take the photograph. cells_are says how the candidates differ.
Choose the pose the product really has at that moment of the scene. Work it out in this order and return data only:
- product: what the product is, a few plain English words.
- working_part: the part that does the work (the disc, the bit, the jaws, the blade, the nozzle), or, for something that is not a tool, the part that matters most to its use.
- use: one sentence on how this product is held or set down for the work in the scene: which part meets the work, from which direction, and where a hand grips it. Know how such a product is really used: a grinder is held with both hands, body roughly level, its disc tilted onto the work below; a drill points its bit straight into the work; a pipe cutter's jaws close round the pipe; a spray gun points its nozzle at the surface; something set down rests on its proper base or lies flat on its side.
- cells: for every cell in order, where its working part points in the picture and whether that pose fits the use.
- choice: the cell that fits best. With nobody in the picture, a pose it can rest in on a surface. With hands or a person, a pose a hand can hold it in at that moment, its working part toward the work, the product seen well and not end-on.
- pose: 12 to 50 words of plain English for an image generator, describing the product exactly as it is in the chosen cell: how it is held or rests, where its working part is in the frame and what it touches there. Do not describe its design or colours.
- contact: where in the chosen cell the working part meets the work, in a few words such as "the disc, at the lower left", or "none".
- work_side: where the thing it works on lies relative to the product, seen from the camera: below, above, left, right, front, behind, around (passing through or round the product) or none.
- reason: one sentence in reason_language.
hint, when present, is the user's own wish and takes priority.'''

FIRST_STEP = {'held': 'every way up the product can be turned, each from two sides; the top-left cell is how it stands for its main image',
              'rest': ('every way up the product can be set down on a surface, each from two sides; the top-left cell is how it stands for '
                       'its main image. cell_facts says what was measured of each: stance (lies level or stands tall), the parts on the '
                       'floor, and tips_over when it would fall over. A tool is usually laid down on its side or its flat back, the way '
                       'a person puts it down on a bench; something made to stand stands on its base.')}
SECOND_STEP = ('the pose chosen a moment ago (top-left) and small changes to it: turned round, leaning left or right, the top tipped toward '
               'or away from the camera, turned a little on the spot. Choose the one a photographer would use for this moment.')


def axes(azimuth):
    """The camera's level directions for a camera at ``azimuth`` degrees: (toward the camera, the picture's right, up)."""
    angle = math.radians(float(azimuth))
    return {'toward': (math.sin(angle), -math.cos(angle), 0.0), 'right': (math.cos(angle), math.sin(angle), 0.0), 'up': (0.0, 0.0, 1.0)}


def moved(rotation, azimuth, axis, degrees):
    """``rotation`` followed by a turn of ``degrees`` about one of the camera's directions."""
    if not axis or not degrees:
        return [float(value) for value in rotation]
    return orientation.then(rotation, orientation.about(axes(azimuth)[axis], degrees))


def first_poses(base, azimuth, ups=None, turns=TURNS):
    """The ways up (all six, or the ids in ``ups``), each turned by ``turns`` about the vertical: [(id, label, rotation)]."""
    rows = []
    for ident, label, axis, degrees in UPS:
        if ups is not None and ident not in ups:
            continue
        upright = moved(base, azimuth, axis, degrees)
        for turn in turns:
            rows.append(('%s@%d' % (ident, turn), label + ('' if not turn else '，转 %d°' % turn), orientation.then(upright, [0, 0, turn])))
    return rows


def adjusted_poses(chosen, azimuth):
    return [(ident, label, moved(chosen, azimuth, axis, degrees)) for ident, label, axis, degrees in ADJUSTMENTS]


def rest_facts(rows):
    """What was measured of each pose set down, for the vision model: how it lies, what touches the floor, whether it falls over.

    The measure counts only the lowest points as support, so a tool on its side that would settle onto a second point
    (a grinder onto its battery and its guard) reads as leaning a little; only a weight far outside it is said to fall.
    """
    facts = orientation.cell_facts(rows)
    for fact, row in zip(facts, rows):
        tip = row.get('tip_angle')
        fact['tips_over'] = bool(type(tip) in (int, float) and tip < TIPS_OVER and not row['id'].startswith('as_is@'))
    return facts


def schema(count):
    letters = list(orientation.LETTERS[:count])
    text = lambda low, high: {'type': 'string', 'minLength': low, 'maxLength': high}
    cell = {'type': 'object', 'properties': {'cell': {'type': 'string', 'enum': letters},
                                             'working_part_points': {'type': 'string', 'enum': list(POINTS)},
                                             'fits': {'type': 'boolean'}},
            'required': ['cell', 'working_part_points', 'fits'], 'additionalProperties': False}
    properties = {'product': text(1, 60), 'working_part': text(1, 80), 'use': text(1, 400),
                  'cells': {'type': 'array', 'items': cell, 'minItems': count, 'maxItems': count},
                  'choice': {'type': 'string', 'enum': letters}, 'pose': text(1, 400), 'contact': text(1, 120),
                  'work_side': {'type': 'string', 'enum': list(SIDES)}, 'reason': text(1, 300)}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def _line(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def ask(rows, sheet_path, facts):
    """The vision model's choice among the pictured rows: {'row', 'answer', 'consistent', 'model'}, or None when it cannot be asked."""
    config = orientation._vision()
    if not config:
        return None
    answer = orientation._look(config, POSE_PROMPT, {**facts, 'cells': list(orientation.LETTERS[:len(rows)]), 'reason_language': 'zh-CN'},
                               schema(len(rows)), sheet_path, max_tokens=2200)
    letters = list(orientation.LETTERS[:len(rows)])
    said = {cell['cell']: cell for cell in answer['cells']}
    return {'row': rows[letters.index(answer['choice'])], 'answer': answer, 'model': config['model'],
            'consistent': len(said) == len(letters) and said.get(answer['choice'], {}).get('fits') is True}


def _pictured(project, plan, poses, view, held, folder, stop):
    """The poses rendered from the scene's camera, reused when the same poses of the same product were rendered before."""
    request = [VERSION, str(project['model_path']), plan, [list(map(float, rotation)) for _, _, rotation in poses],
               [round(float(view[0]), 2), round(float(view[1]), 2)], held]
    key = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()[:24]
    target = folder / key; index = target / 'rows.json'
    try:
        stored = json.loads(index.read_text(encoding='utf-8'))
        rows = [{**row, 'path': target / row['path']} for row in stored]
        if [row['id'] for row in rows] == [ident for ident, _, _ in poses] and all(row['path'].is_file() for row in rows):
            return rows
    except (OSError, ValueError, KeyError, TypeError):
        pass
    rows = orientation.picture(project, plan, poses, target, stop, view=view, floor=not held, size=SIZE, samples=SAMPLES)
    index.write_text(json.dumps([{**row, 'path': row['path'].name} for row in rows], ensure_ascii=False), encoding='utf-8')
    return rows


def _url(path):
    try:
        return '/files/' + Path(path).relative_to(DATA).as_posix()
    except ValueError:
        return None


def kept(base, mode, source, reason, started=None):
    return {'version': VERSION, 'mode': mode, 'source': source, 'rotation': [float(value) for value in base],
            'base_rotation': [float(value) for value in base], 'changed': False, 'description': None, 'contact': None,
            'work_side': 'none', 'reason': reason, 'model': None, 'sheets': [],
            'seconds': round(time.time() - started, 1) if started else 0.0}


def decide(project, plan, azimuth, elevation, scene, product, hold, cancelled=None, progress=None, mode='auto', hint=''):
    """The pose for the product in this scene. Returns a record; its 'rotation' replaces plan['rotation'] for the scene's render.

    ``hold`` is 'hands', 'person', 'nobody' or 'scene' (people left to the scene description). ``plan`` is the plan the
    product is rendered with (its materials and pose). Never raises for anything but a cancellation.
    """
    started = time.time(); stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    base = [float(value) for value in plan.get('rotation', [0, 0, 0])]
    if mode == 'keep':
        return kept(base, mode, 'user', '按你的选择保持默认姿态。', started)
    if not orientation._vision():
        return kept(base, mode, 'default', '视觉模型未配置，产品保持默认姿态。', started)
    held = hold != 'nobody'; view = (float(azimuth), float(elevation))
    folder = DATA / project['id'] / 'scene_ai' / 'poses'
    facts = {'scene': _line(scene, 1500), 'people': hold if hold in HOLDS else 'scene', 'product': _line(product, 120),
             'part_names': [_line(item.get('name'), 60) for item in (project.get('analysis') or {}).get('objects', [])[:60]
                            if isinstance(item, dict) and item.get('name')]}
    if hint:
        facts['hint'] = _line(hint, 300)
    sheets = []
    try:
        say('渲染候选使用姿态')
        # Held, the product is seen in the air; set down, standing on a floor, so that what it rests on shows.
        rows = _pictured(project, plan, first_poses(base, azimuth), view, held, folder, stop)
        sheet = orientation.sheet(rows, rows[0]['path'].parent / 'sheet.png', columns=COLUMNS, cell=SIZE)
        sheets.append(sheet)
        if stop(): raise InterruptedError()
        say('选择产品的使用姿态')
        first = ask(rows, sheet, {**facts, 'cells_are': FIRST_STEP['held' if held else 'rest'],
                                  **({} if held else {'cell_facts': rest_facts(rows)})})
        if first is None:
            return kept(base, mode, 'default', '视觉模型未能回答，产品保持默认姿态。', started)
        chosen = first
        if held:
            if stop(): raise InterruptedError()
            say('渲染姿态微调')
            rows = _pictured(project, plan, adjusted_poses(first['row']['rotation'], azimuth), view, held, folder, stop)
            sheet = orientation.sheet(rows, rows[0]['path'].parent / 'sheet.png', columns=COLUMNS, cell=SIZE)
            sheets.append(sheet)
            if stop(): raise InterruptedError()
            say('微调产品的使用姿态')
            facts['first_choice'] = _line(first['answer']['use'], 400)
            second = ask(rows, sheet, {**facts, 'cells_are': SECOND_STEP})
            if second is not None:
                chosen = second
    except InterruptedError:
        raise
    except Exception as exc:       # a pose that cannot be worked out is never a reason to fail the scene
        record = kept(base, mode, 'default', '未能设计使用姿态，产品保持默认姿态。', started)
        record['error'] = _line(exc, 200)
        return record
    answer = chosen['answer']; rotation = chosen['row']['rotation']
    if not orientation.valid_rotation(rotation):
        return kept(base, mode, 'default', '设计出的姿态无效，产品保持默认姿态。', started)
    changed = not orientation.same(rotation, base)
    contact = _line(answer['contact'], 120)
    return {'version': VERSION, 'mode': mode, 'source': 'ai', 'rotation': [float(value) for value in rotation],
            'base_rotation': base, 'changed': changed, 'candidate': chosen['row']['id'], 'label': chosen['row']['label'],
            'description': _line(answer['pose'], 400) or None, 'contact': None if contact.casefold() in ('', 'none') else contact,
            'work_side': answer['work_side'], 'working_part': _line(answer['working_part'], 80), 'use': _line(answer['use'], 400),
            'reason': _line(answer['reason'], 300), 'consistent': chosen['consistent'], 'model': chosen['model'],
            'sheets': [url for url in (_url(path) for path in sheets) if url], 'seconds': round(time.time() - started, 1)}

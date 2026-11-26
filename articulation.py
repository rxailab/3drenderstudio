"""The pose agent: which parts of a product move, how, and the states worth showing.

A model file says nothing about its moving parts, but most hand tools have them: pliers open about a rivet, a caliper's
slider runs along the beam, a tape's hook pulls the blade out, a utility knife's blade slides out, a toolbox lid swings
up. Showing them closed in every picture undersells the product, and an image model given a closed caliper paints it
"measuring" with its jaws shut and the tool tilted across the work.

The agent works in three steps, each checked:

1. Blender splits the model into numbered parts (objects, or the loose pieces of a one-piece mesh) and draws them
   colour-coded from above and at three-quarters (blender/articulate.py ``probe``).
2. The vision model names the joints on that picture: the parts that move, the parts that turn the other way (a
   scissor pair), a flexible part that stretches (a tape blade), the axis, how the pivot is found, which way is "open",
   the range, and the states a listing shows (closed / open / measuring ...). It also writes how the product is
   correctly used, for the scene pictures and their check.
3. Blender solves the geometry (pivot, opening sign by collision tests, safe range, a slider's bare rail) and renders
   every state; the vision model looks at the states and either accepts them or says what is wrong, and step 2 runs
   once more with that note.

The result is kept on the project (``project['articulation']``) for this model file, and any render can show a state:
``plan_for(project, state_id)`` gives the ``plan['articulation']`` that worker.prepare applies.
"""
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import components
from studio_store import get, put

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / 'blender' / 'articulate.py'
DATA = components.DATA
VERSION = 4
LOCK = threading.Lock()
RUNNING = {}

ARTICULATOR = (
    'You are a product engineer looking at a 3D model of one product, drawn twice with every part in its own flat colour '
    'and its number: from straight above (left; X to the right, Y up, Z toward you) and at three-quarters (right). Decide '
    'which parts move relative to the others when the product is used, as on the real product: pliers and cutters open '
    'about their rivet (the two halves turn in opposite directions; a half is its jaw with its handle); a caliper\'s slider '
    '(with its moving jaw, display, buttons and the legends printed on it) runs along the beam away from the fixed jaw; a '
    'tape measure\'s hook (the end tab) pulls the blade out of the case: moving is the hook only, the blade itself goes in '
    'stretch, never in moving; a utility knife\'s blade '
    'slides out of the body with its slider button; a box lid swings up about its back edge; a folding handle folds down. '
    'Most tools have no moving parts worth showing (a wrench, a screwdriver, a hammer, a saw, a drill): then articulated is '
    'false and joints and states are empty. A spinning part (a drill chuck, a disc) is not a pose. Use only part numbers that '
    'are in the parts list; every part of a moving assembly must be listed (missing ones would stay behind). For each joint:\n'
    '- type: hinge (turns) or slide (moves in a straight line).\n'
    '- moving: the parts that move; counter: for a scissor pair only, the parts that turn the other way; stretch: a flexible '
    'part that lengthens with the movement (a tape blade), else empty.\n'
    '- axis: x, y or z of the top view: for a hinge, the axis it turns about (pliers lying flat in the top view turn about z); '
    'for a slide, the direction it moves along.\n'
    '- pivot (hinge only): kind origin (the parts\' own origin is the rivet), part (a pin or rivet part: give its number), '
    'crossing (two halves crossing like scissors, no pin part), edge (a lid: give side, the edge it turns on, as +x/-x/+y/-y/+z/-z '
    'in top-view axes), or auto. Use 0 for no part and "" for no side.\n'
    '- away_from: a part the moving parts move away from when opening (the fixed jaw, the case, the box), or 0.\n'
    '- max: the realistic full range: degrees for a hinge (each half of a scissor pair, e.g. 15-25), millimetres for a slide '
    '(a caliper\'s measuring range, a knife blade\'s reach out of the body).\n'
    'states: two or three poses a listing should show, the first being how the product rests: e.g. closed / open; for a '
    'caliper closed / measuring (a typical opening such as 25 mm) / fully open; for a tape retracted / pulled out. values '
    'give each joint\'s value in that state, measured from the model AS DELIVERED (0 = exactly as modelled) toward OPEN: '
    'a larger value is always more open (jaws further apart, slider further from the fixed jaw, blade further out, lid '
    'higher). Pliers are usually modelled closed, so closed is 0 and open is positive; never give closed a positive value. '
    'Only a part modelled already out (a tape or knife blade showing) takes a negative value to pull it in, no more than '
    'it is out. role: rest, open or working.\n'
    'A cabinet, chest or toolbox: each drawer is its own slide joint (the drawer front with its handle and the drawer body, '
    'moving out of the cabinet, away from it, along the drawer\'s depth), each door and lid its own hinge. '
    'CRITICAL for cabinets with front doors (wall-mounted cabinets, double-door cabinets, lockers): '
    'each front door is its own hinge with axis "z" (vertical hinge line), pivot kind "edge" with side "-x" for the left door '
    'and "+x" for the right door (the outer side edge where the hinge pins sit, opposite the center handles), max 90-110 degrees, '
    'and moving MUST include the door panel PLUS every part mounted on that door (handles/aluminum strips, inner astragal/door-stop '
    'stiffeners, latch/hinge brackets mounted on the door, lock cylinder, lock rods, logo badge) so nothing floats behind when the door opens. '
    'NEVER treat a fixed bolted/screwed top housing panel as a hinged lid just because its CAD name says "盖子", "上盖", "顶盖" or "cover": '
    'in Chinese sheet-metal CAD, the fixed top panel of a cabinet is commonly named "盖子". A wall-mounted cabinet or front-door cabinet '
    'whose front doors carry handles or a lock opens via its FRONT DOORS, not its top panel; only a true top-opening toolbox/chest '
    'whose lid is unfastened and designed to lift open has a lid hinge. '
    'When user_selected_parts is present, the user explicitly clicked those moving parts on the product: set articulated=true, '
    'articulate ONLY the joints for those user-selected moving parts (each moving together with its attached hardware such as handles, '
    'locks, badges, or drawers), and do NOT articulate other panels.\n'
    'scene_hint, when present, is what the user wants the scene pictures to show. If it asks for moving parts in a pose '
    '(drawers pulled out, a door or a lid open, a blade out), add one state showing exactly that and only that (\"the first '
    'two drawers\" are the two top drawers, pulled out about two thirds of their depth; everything else stays shut) and set '
    'matches_scene_hint true on it; false on every other state.\n'
    'usage: 2-4 short, concrete rules for showing the product used correctly in a photo, in English and in Simplified '
    'Chinese, each one checkable in a picture: name the part and where it is, never just "properly" or "correctly". '
    'First where the hand goes and where it never goes (pliers and cutters: both handles in the palm, the fingers wrapped '
    'round them, never pinched at the head or the pivot; a hammer: the lower half of the handle; a screwdriver: the '
    'handle, the shaft in line with the forearm). Then exactly how the working part meets the work: where (cutters: the '
    'wire deep in the jaws near the pivot, never at the tips; a hammer\'s claw: the nail shank seated in the claw\'s slot, '
    'the handle levered back), at what angle (square to the blades, perpendicular to the surface, on the screw\'s axis; a '
    'caliper\'s jaws square on both faces of the workpiece, the beam perpendicular to them, not tilted) and what the work '
    'is (only a wire thin enough for the jaws). '
    'Keep labels short (zh at most 6 characters, en at most 16). '
    'summary: one brief factual sentence (under 180 characters) naming which parts move and how; no step-by-step commentary.')

VERIFIER = (
    'You check poses of a 3D product model. The picture shows the same product in several states side by side, each '
    'labelled. For each state say whether it is physically right for this product: the moving parts still joined where '
    'they should be (a pliers half still on its rivet, a slider still on its beam, a lid still on its hinge), nothing '
    'floating or detached, no part passing through another, opened the way the real product opens and not past its real '
    'range. FIRST check functional correctness: did the RIGHT parts move for this product? If a front-door or wall-mounted '
    'cabinet lifted its fixed top housing panel like a lid while its front doors (with handles or lock) stayed shut, OR if '
    'handles, locks, badges, inner door stiffeners/astragal bars, or door-mounted brackets stayed floating behind in the opening when a door opened, FAIL that state with fault "detached" (or "wrong_part" if the wrong panel moved). '
    'A state that looks like the model as delivered is fine, and a state whose values are all 0 IS the model as '
    'delivered: never fail it, even if the product was modelled half open. Be strict about wrong moving parts, detached or '
    'intersecting parts, not about rendering style. If something is wrong, say in fix which part moved wrongly and how '
    '(e.g. "parts 1 and 2 turn about the wrong point: the rivet is part 5", "the slider should move toward +x, away from the '
    'fixed jaw", "part 12 is the fixed top panel; open the front doors instead"). Write '
    'issues in Simplified Chinese. When a state is drawn twice, the upper picture is from the front right and the lower one '
    'from the front left: judge a door, lid or flap in the picture that shows it broad, never in the one that shows it '
    'edge-on. `measured` is what was measured on the geometry in that state, not an opinion: crossing "none" or "at the '
    'hinge only" (a door\'s edge meeting the cabinet along its hinge line) means no moving part passes through any other '
    'part, and moved_away_mm above 0 means the moving parts went away from the body (outward). Do not report a part '
    'passing through another, or opening inward, when the measurement rules it out. Give each state its fault: none, '
    'wrong_part, passes_through, wrong_direction, detached, past_range or other.')
MEASURABLE = ('passes_through', 'wrong_direction')        # faults the geometry answers for itself


def _schema():
    number = {'type': 'number'}
    joint = {'type': 'object', 'additionalProperties': False,
             'required': ['name', 'type', 'moving', 'counter', 'stretch', 'axis', 'pivot', 'away_from', 'max'],
             'properties': {'name': {'type': 'string'}, 'type': {'type': 'string', 'enum': ['hinge', 'slide']},
                            'moving': {'type': 'array', 'items': {'type': 'integer'}},
                            'counter': {'type': 'array', 'items': {'type': 'integer'}},
                            'stretch': {'type': 'array', 'items': {'type': 'integer'}},
                            'axis': {'type': 'string', 'enum': ['x', 'y', 'z']},
                            'pivot': {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'part', 'side'],
                                      'properties': {'kind': {'type': 'string', 'enum': ['auto', 'origin', 'part', 'crossing', 'edge']},
                                                     'part': {'type': 'integer'}, 'side': {'type': 'string'}}},
                            'away_from': {'type': 'integer'}, 'max': number}}
    state = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'label_zh', 'label_en', 'role', 'matches_scene_hint', 'values'],
             'properties': {'id': {'type': 'string'}, 'label_zh': {'type': 'string'}, 'label_en': {'type': 'string'},
                            'role': {'type': 'string', 'enum': ['rest', 'open', 'working']}, 'matches_scene_hint': {'type': 'boolean'},
                            'values': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['joint', 'value'],
                                                                  'properties': {'joint': {'type': 'string'}, 'value': number}}}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['articulated', 'product_kind', 'joints', 'states', 'usage', 'summary'],
            'properties': {'articulated': {'type': 'boolean'}, 'product_kind': {'type': 'string'},
                           'joints': {'type': 'array', 'items': joint}, 'states': {'type': 'array', 'items': state},
                           'usage': {'type': 'object', 'additionalProperties': False, 'required': ['en', 'zh'],
                                     'properties': {'en': {'type': 'array', 'items': {'type': 'string'}},
                                                    'zh': {'type': 'array', 'items': {'type': 'string'}}}},
                           'summary': {'type': 'string', 'maxLength': 240}}}


def _verify_schema():
    return {'type': 'object', 'additionalProperties': False, 'required': ['states', 'ok', 'fix'],
            'properties': {'states': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'ok', 'issue', 'fault'],
                                                                 'properties': {'id': {'type': 'string'}, 'ok': {'type': 'boolean'}, 'issue': {'type': 'string'},
                                                                                'fault': {'type': 'string', 'enum': ['none', 'wrong_part', 'passes_through', 'wrong_direction',
                                                                                                                     'detached', 'past_range', 'other']}}}},
                           'ok': {'type': 'boolean'}, 'fix': {'type': 'string'}}}


# ---------- pictures ----------

def _font(size):
    for name in ('DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype(str(ROOT / 'brandkit' / 'fonts' / 'Inter-Variable.ttf'), size)
    except OSError:
        return ImageFont.load_default()


FASTENER_WORDS = re.compile(r'螺丝|螺钉|螺母|卡扣|固定片|固定件|垫片|screw|bolt|nut|clip|washer', re.I)
DOOR_WORDS = re.compile(r'(?:^|[^a-z])doors?(?:$|[^a-z])|左门|右门|柜门|门板|前门|侧门', re.I)
DOOR_NON_PANEL_WORDS = re.compile(r'门挡|门档|门框|门插销|门锁|门铝条|door\s*(?:stop|frame|lock|handle|hinge|pin)', re.I)


def _is_fastener(part, longest):
    size = part.get('size_mm') or [0, 0, 0]
    return bool(FASTENER_WORDS.search(str(part.get('object') or ''))) and max(size) < longest * .08


def _structural_analysis(probe):
    """Detect mechanically fastened top panels and front cabinet doors (+ attached hardware) from geometry and part names."""
    parts = probe.get('parts') or []
    bbox = probe.get('bbox_mm')
    if not parts or not bbox or len(bbox) != 2:
        return {}
    lo, hi = bbox[0], bbox[1]
    span = [max(float(b - a), 1e-6) for a, b in zip(lo, hi)]
    longest = max(span)
    fasteners = [p for p in parts if _is_fastener(p, longest)]
    top_slabs = []
    front_doors = []
    for part in parts:
        size = [float(v) for v in (part.get('size_mm') or [0, 0, 0])]
        centre = [float(v) for v in (part.get('centroid_mm') or [0, 0, 0])]
        name = str(part.get('object') or '')
        if (size[0] >= span[0] * .6 and size[1] >= span[1] * .55
                and size[2] <= max(size[0] * .25, size[1] * .35)
                and centre[2] >= hi[2] - span[2] * .16):
            near = [f for f in fasteners if abs(float(f['centroid_mm'][2]) - centre[2]) <= max(size[2], longest * .05)]
            top_slabs.append({'id': part['id'], 'object': name, 'fastener_count': len(near)})
        is_vertical_front = (size[2] >= span[2] * .45 and size[0] >= span[0] * .2
                             and size[1] <= min(size[0], size[2]) * .35
                             and (centre[1] <= lo[1] + span[1] * .22 or centre[1] >= hi[1] - span[1] * .22))
        named_door = bool(DOOR_WORDS.search(name)) and not DOOR_NON_PANEL_WORDS.search(name)
        if is_vertical_front and named_door:
            mid_x = (lo[0] + hi[0]) / 2.0
            hinge_side = '-x' if centre[0] < mid_x else '+x'
            front_doors.append({'id': part['id'], 'object': name, 'hinge_axis': 'z', 'hinge_side': hinge_side,
                                'attached_parts': [], '_centre': centre, '_size': size})
    if front_doors:
        # Assign each front-mounted hardware part (handles, locks, badges, lock rods) to its single nearest door.
        door_ids = {d['id'] for d in front_doors}
        pad_xz = longest * .05
        pad_y = longest * .08
        for other in parts:
            if other['id'] in door_ids or _is_fastener(other, longest):
                continue
            osize = [float(v) for v in (other.get('size_mm') or [0, 0, 0])]
            oc = [float(v) for v in (other.get('centroid_mm') or [0, 0, 0])]
            oname = str(other.get('object') or '')
            if re.search(r'固定插销|活动插销', oname):
                continue
            best = None
            for d in front_doors:
                dc, ds = d['_centre'], d['_size']
                if max(osize) >= max(ds) * 1.05 or osize[0] > ds[0] * 1.05:
                    continue
                dlo_x, dhi_x = dc[0] - ds[0] / 2.0, dc[0] + ds[0] / 2.0
                dlo_z, dhi_z = dc[2] - ds[2] / 2.0, dc[2] + ds[2] / 2.0
                if dlo_x - pad_xz <= oc[0] <= dhi_x + pad_xz and dlo_z - pad_xz <= oc[2] <= dhi_z + pad_xz and abs(oc[1] - dc[1]) <= pad_y:
                    dx = 0.0 if dlo_x <= oc[0] <= dhi_x else min(abs(oc[0] - dlo_x), abs(oc[0] - dhi_x))
                    dist = (dx, abs(oc[0] - dc[0]))
                    if best is None or dist < best[0]:
                        best = (dist, d)
            if best is not None:
                best[1]['attached_parts'].append(other['id'])
        for d in front_doors:
            d.pop('_centre', None); d.pop('_size', None)
    out = {}
    fixed_tops = [t for t in top_slabs if t['fastener_count'] >= 2 or front_doors]
    if fixed_tops:
        out['fixed_top_panels'] = fixed_tops
    if front_doors:
        out['front_doors'] = front_doors
        out['note'] = (
            'Front cabinet doors detected. Open these front doors around vertical axis "z" using pivot kind "edge" '
            'with the listed hinge_side ("-x" for left door, "+x" for right door), moving each door together with its '
            'attached_parts (handles, lock, logo). Do NOT lift fixed_top_panels as a lid.'
        )
    return out


def _resolve_selected_parts(selected_parts, probe):
    """Map user-selected object names or part numbers to valid part dicts in probe['parts']."""
    if not selected_parts:
        return []
    parts = probe.get('parts') or []
    by_id = {part['id']: part for part in parts}
    by_obj = {}
    for part in parts:
        by_obj.setdefault(str(part.get('object') or ''), []).append(part)
    found = []; seen = set()
    for item in selected_parts:
        matches = []
        if isinstance(item, int) and item in by_id:
            matches = [by_id[item]]
        elif isinstance(item, str):
            text = item.strip()
            if text.isdigit() and int(text) in by_id:
                matches = [by_id[int(text)]]
            elif text in by_obj:
                matches = by_obj[text]
        for part in matches:
            if part['id'] not in seen:
                seen.add(part['id']); found.append(part)
    return found


def _structural_fault(answer, probe, selected_ids=None):
    """Deterministic guard: enforce user-selected parts when provided, and reject lifting a fixed bolted/cabinet top panel."""
    if not answer.get('articulated') or not answer.get('joints'):
        if selected_ids:
            return 'The user explicitly selected moving parts %s. You must set articulated=true and create joints for them.' % sorted(selected_ids)
        return ''
    moved_ids = {pid for joint in answer['joints'] for pid in (joint.get('moving') or []) + (joint.get('counter') or [])}
    if selected_ids:
        missing = [pid for pid in sorted(selected_ids) if pid not in moved_ids]
        if missing:
            by_id = {p['id']: p.get('object', '') for p in (probe.get('parts') or [])}
            desc = ', '.join('part %d (%s)' % (pid, by_id.get(pid, '')) for pid in missing)
            return 'The user explicitly clicked %s as moving parts, but your answer left them stationary. Articulate these parts (with their attached handles/locks/badges) and remove unrelated housing joints.' % desc
    info = _structural_analysis(probe)
    fixed_tops = {row['id']: row for row in info.get('fixed_top_panels') or []}
    front_doors = info.get('front_doors') or []
    door_ids = {row['id'] for row in front_doors}
    if not fixed_tops:
        return ''
    bad_tops = [fixed_tops[pid] for pid in moved_ids if pid in fixed_tops and (not selected_ids or pid not in selected_ids)]
    if not bad_tops:
        return ''
    # Reject if the top panel is mechanically fastened (>=2 screws/clips) OR if front doors exist and were ignored.
    bolted = [row for row in bad_tops if row['fastener_count'] >= 2]
    doors_ignored = bool(front_doors) and not (moved_ids & door_ids)
    if not bolted and not doors_ignored:
        return ''
    top_desc = ', '.join('part %d (%s)' % (row['id'], row['object']) for row in bad_tops)
    if front_doors:
        door_desc = '; '.join(
            'part %d (%s): axis="z", pivot={"kind":"edge","side":"%s"}, moving=[%s]' % (
                d['id'], d['object'], d['hinge_side'],
                ', '.join(str(x) for x in [d['id']] + d['attached_parts'])
            ) for d in front_doors
        )
        return (
            '%s is a FIXED bolted top roof panel (not a hinged lid; Chinese CAD often names fixed cabinet tops "盖子"). '
            'Remove the lid joint and instead articulate the front doors: %s.' % (top_desc, door_desc)
        )
    return '%s is fastened with screws/clips and is a fixed top housing panel, not a hinged lid.' % top_desc


def labelled_sheet(folder, probe, out):
    """The two probe views side by side, each part's number in a white disc at its label point, and the top view's axes."""
    parts = probe.get('parts') or []
    bbox = probe.get('bbox_mm')
    longest = max((max(b - a for a, b in zip(*bbox)) if bbox and len(bbox) == 2 else 0), 1e-6)
    skip_labels = {str(p['id']) for p in parts if _is_fastener(p, longest)} if len(parts) > 20 else set()
    tiles = []
    for view in probe['views']:
        with Image.open(Path(folder) / view['image']) as opened:
            image = opened.convert('RGB')
        draw = ImageDraw.Draw(image); w, h = image.size; font = _font(max(14, w // 48)); r = max(11, w // 70)
        placed = []
        for part_id, (x, y) in sorted(view['labels'].items(), key=lambda kv: int(kv[0])):
            if str(part_id) in skip_labels:
                continue
            px, py = x * w, y * h
            for _ in range(12):            # nudge labels apart
                clash = [q for q in placed if (q[0] - px) ** 2 + (q[1] - py) ** 2 < (2.2 * r) ** 2]
                if not clash:
                    break
                py += 2.2 * r
            placed.append((px, py))
            draw.ellipse([px - r, py - r, px + r, py + r], fill='white', outline='black', width=2)
            draw.text((px, py), str(part_id), fill='black', font=font, anchor='mm')
        if view['name'] == 'top':
            x0, y0, length = 30, h - 30, w // 10
            draw.line([x0, y0, x0 + length, y0], fill='black', width=3); draw.text((x0 + length + 12, y0), 'X', fill='black', font=font, anchor='lm')
            draw.line([x0, y0, x0, y0 - length], fill='black', width=3); draw.text((x0, y0 - length - 12), 'Y', fill='black', font=font, anchor='mb')
        tiles.append(image)
    sheet = Image.new('RGB', (sum(t.width for t in tiles) + 20 * (len(tiles) - 1), max(t.height for t in tiles)), 'white')
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0)); x += tile.width + 20
    sheet.save(out)
    return out


def state_sheet(folder, previews, states, out, language='zh'):
    """Every state side by side, labelled; under each, the same pose from the other front corner when it was rendered."""
    tiles = []
    for preview in previews:
        state = next((row for row in states if row['id'] == preview['state']), {})
        with Image.open(Path(folder) / preview['image']) as opened:
            image = opened.convert('RGB')
        draw = ImageDraw.Draw(image); font = _font(max(16, image.width // 26))
        label = '%s · %s' % (state.get('id', ''), state.get('label_en', ''))
        draw.rectangle([0, 0, image.width, image.width // 12], fill='white')
        draw.text((14, image.width // 24), label, fill='black', font=font, anchor='lm')
        second = Path(folder) / preview['other'] if preview.get('other') else None
        if second and second.is_file():
            with Image.open(second) as opened:
                below = opened.convert('RGB')
            both = Image.new('RGB', (max(image.width, below.width), image.height + below.height), 'white')
            both.paste(image, (0, 0)); both.paste(below, (0, image.height)); image = both
        tiles.append(image)
    if not tiles:
        return None
    sheet = Image.new('RGB', (sum(t.width for t in tiles) + 16 * (len(tiles) - 1), max(t.height for t in tiles)), 'white')
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0)); x += tile.width + 16
    sheet.save(out)
    return out


# ---------- the agent ----------

def key_of(project, size_mm=None, hint=''):
    """The record's identity: this model file, its real size, the solver's code, and the scene hint when it asks for a
    pose ("pull out the top two drawers"), since the agent then adds a state for it."""
    model = Path(project['model_path']); stat = model.stat()
    parts = [VERSION, str(model), stat.st_size, stat.st_mtime_ns, size_mm, components._digest(SCRIPT)]
    if asks_for_pose(hint):
        parts.append(' '.join(str(hint).split()))
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]


POSE_WORDS = re.compile(r'拉开|拉出|抽出|打开|开着|掀开|翻开|展开|伸出|张开|开门|开盖|open|pull(?:ed)? out|extend|unfold|lid up|doors? open|drawers? out', re.I)


def asks_for_pose(hint):
    """Whether the user's scene hint asks for moving parts in a pose (drawers pulled out, a door or lid open, a blade out)."""
    return bool(POSE_WORDS.search(str(hint or '')))


def _facts(project, product, probe):
    facts = {'product': (product or {}).get('title') or project.get('name', ''),
             'category': ((project.get('metadata') or {}).get('product_category') or ''),
             'description': ' '.join(str((product or {}).get(key) or '') for key in ('headline', 'subline'))[:300],
             'model_size_mm': [round(b - a, 1) for a, b in zip(*probe['bbox_mm'])],
             'parts': [{'number': part['id'], 'object': part['object'], 'size_mm': part['size_mm'], 'centre_mm': part['centroid_mm'],
                        'materials': part['materials'], 'loose_piece_of_one_mesh': bool(part.get('pieces'))} for part in probe['parts']],
             'objects': [{'name': row['name'], 'parent': row['parent'], 'type': row['type']} for row in probe['objects']]}
    hints = _structural_analysis(probe)
    if hints:
        facts['structural_hints'] = hints
    return facts


def _tidy(answer):
    """Small shape slips a model makes: usage rules as a bare list (taken as English), a missing usage, overlong summary."""
    usage = answer.get('usage')
    if isinstance(usage, list):
        answer['usage'] = {'en': [str(rule) for rule in usage], 'zh': []}
    elif not isinstance(usage, dict):
        answer['usage'] = {'en': [], 'zh': []}
    if 'summary' not in answer and isinstance(answer.get('reasoning'), str):
        answer['summary'] = answer.pop('reasoning')
    if isinstance(answer.get('summary'), str):
        answer['summary'] = answer['summary'].strip()[:240]
    return answer


def _clean_answer(answer, probe):
    """Only joints over known parts; states that name known joints; values within each joint's range."""
    ids = {part['id'] for part in probe['parts']}
    joints = []
    for joint in answer.get('joints') or []:
        moving = [i for i in joint.get('moving') or [] if i in ids]
        if not moving:
            continue
        pivot = joint.get('pivot') or {}
        pivot = {'kind': pivot.get('kind', 'auto'), 'part': pivot.get('part') if pivot.get('part') in ids else None, 'side': pivot.get('side') or None}
        joints.append({**joint, 'pivot': pivot, 'away_from': joint.get('away_from') if joint.get('away_from') in ids else None, 'moving': moving, 'counter': [i for i in joint.get('counter') or [] if i in ids and i not in moving],
                       'stretch': [i for i in joint.get('stretch') or [] if i in ids and i not in moving],
                       'max': max(1.0, min(float(joint.get('max') or 20), 180.0 if joint.get('type') == 'hinge' else 2000.0))})
    names = {joint['name'] for joint in joints}
    hinges = {joint['name']: joint['max'] for joint in joints if joint.get('type') == 'hinge'}
    states = []
    for state in answer.get('states') or []:
        kinds = {joint['name']: joint.get('type') for joint in joints}
        # a hinge's value is how far it is open: the solver finds which way that is, so a sign the model wrote means nothing
        values = {row['joint']: abs(float(row['value'])) if kinds.get(row['joint']) == 'hinge' else float(row['value'])
                  for row in state.get('values') or [] if row.get('joint') in names}
        if state.get('id') and state['id'] not in {row['id'] for row in states}:
            states.append({'id': str(state['id'])[:24], 'label_zh': str(state.get('label_zh', ''))[:12], 'label_en': str(state.get('label_en', ''))[:24],
                           'role': state.get('role', 'open'), 'values': values,
                           'matches_scene_hint': bool(state.get('matches_scene_hint')) and any(values.values())})
    # A hinge at rest is as modelled (0): pliers are modelled with their jaws shut, so a "closed" state the model gave an
    # angle would drive the jaws through each other. An opened state left at 0 takes the widest opening it gave.
    for name, limit in hinges.items():
        widest = max([state['values'].get(name, 0) for state in states] + [0]) or limit * .75
        for state in states:
            if state['role'] == 'rest':
                state['values'][name] = 0.0
            elif not state['values'].get(name):
                state['values'][name] = widest
    return {'articulated': bool(answer.get('articulated') and joints and len(states) >= 2), 'product_kind': str(answer.get('product_kind', ''))[:60],
            'reasoning': str(answer.get('summary') or answer.get('reasoning') or '')[:400], 'joints': joints, 'states': states[:4],
            'usage': {lang: [str(rule)[:200] for rule in (answer.get('usage') or {}).get(lang) or []][:4] for lang in ('en', 'zh')}}


def _measured_wins(row, measured):
    """A state the check failed for a part passing through another or opening the wrong way, when the geometry was measured
    and shows neither: the check misread the picture (a door opened towards the camera is seen edge-on and looks as if it
    stood inside the cabinet). Faults that cannot be measured (a detached part, a pose past the real range) stand."""
    if row.get('ok') or not measured or row.get('fault') not in MEASURABLE:
        return row
    away = measured.get('moved_away_mm')
    if measured.get('crossing') in ('none', 'at the hinge only') and (away is None or away > 0):
        return {**row, 'ok': True, 'overruled': row.get('issue', ''), 'issue': ''}
    return row


def _clamped(states, joints):
    """State values inside each joint's solved range: up to its maximum; down to its minimum (a slide pulled in as far
    as flush; a hinge no further shut than the model is)."""
    ranges = {joint['name']: (float(joint.get('min', 0.0)), float(joint['max'])) for joint in joints}
    out = []
    for state in states:
        values = {name: max(ranges[name][0], min(value, ranges[name][1])) if name in ranges else value for name, value in state['values'].items()}
        out.append({**state, 'values': values})
    return out


def analyse(project_id, product=None, size_mm=None, cancelled=None, say=lambda text: None, force=False, hint='', selected_parts=None):
    """The project's moving parts, worked out once per model file (``force`` asks again). ``hint`` is the user's scene hint:
    when it asks for a pose ("拉开前两个抽屉"), the agent adds a state for exactly that, rendered from the model, so the scene
    pictures show the real drawers pulled out instead of drawers an image model painted. ``selected_parts`` lets the user
    click specific moving parts on the product to speed up and guide identification. Returns the stored record."""
    project = get('project', project_id)
    key = key_of(project, size_mm, hint)
    known = project.get('articulation') if isinstance(project.get('articulation'), dict) else None
    clean_selected = [str(item).strip() for item in (selected_parts or []) if str(item).strip()][:24]
    if known and known.get('key') == key and known.get('status') in ('ready', 'none') and not force and not clean_selected:
        return known
    import ai_client
    import brandkit_review
    _, vision = brandkit_review._configs()
    folder = DATA / project_id / 'articulation' / key; folder.mkdir(parents=True, exist_ok=True)
    probe_result = folder / 'probe' / 'result.json'
    sheet = folder / 'parts.png'
    probe = None
    # Reuse an already-probed part breakdown for the same model key to skip ~10-15s of Blender startup & rendering.
    if probe_result.is_file() and sheet.is_file():
        try:
            cached = json.loads(probe_result.read_text(encoding='utf-8'))
            if cached.get('success') and cached.get('parts') and cached.get('bbox_mm'):
                probe = cached
        except (OSError, ValueError):
            probe = None
    if probe is None:
        say('正在拆分模型部件')
        probe = components.run_blender(SCRIPT, {'action': 'probe', 'model_path': str(project['model_path']), 'size_mm': size_mm, 'size': 1024},
                                       folder / 'probe', cancelled)
        sheet = labelled_sheet(folder / 'probe', probe, folder / 'parts.png')
    resolved_selected = _resolve_selected_parts(clean_selected, probe)
    selected_ids = {p['id'] for p in resolved_selected}
    record = {'key': key, 'version': VERSION, 'at': time.time(), 'parts_sheet': str(sheet), 'parts': probe['parts'], 'unit_mm': probe['unit_mm'],
              'selected_parts': clean_selected,
              'status': 'none', 'joints': [], 'states': [], 'usage': {'en': [], 'zh': []}, 'notes': [], 'previews': None, 'usage_tokens': 0}
    if not vision:
        record['notes'].append('没有配置视觉模型，无法识别活动部件。')
        return _store(project_id, record)
    facts = _facts(project, product, probe); feedback = None
    if resolved_selected:
        doors_by_id = {d['id']: d for d in ((facts.get('structural_hints') or {}).get('front_doors') or [])}
        facts['user_selected_parts'] = [
            {'number': p['id'], 'object': p['object'], 'size_mm': p['size_mm'], 'centre_mm': p['centroid_mm'],
             **({'hinge_axis': doors_by_id[p['id']]['hinge_axis'], 'hinge_side': doors_by_id[p['id']]['hinge_side'],
                 'attached_parts': doors_by_id[p['id']]['attached_parts']} if p['id'] in doors_by_id else {})}
            for p in resolved_selected
        ]
    if asks_for_pose(hint):
        facts['scene_hint'] = ' '.join(str(hint).split())[:300]
    for attempt in (1, 2):
        say('正在识别活动部件' if attempt == 1 else '按检查意见重新识别活动部件')
        ask = dict(facts, **({'previous_attempt': {k: v for k, v in feedback.items() if k != 'states_picture'},
                              'note': ('The second picture shows how your previous answer posed the product; fix what the check found.'
                                       if feedback.get('states_picture')
                                       else 'Your previous answer failed the structural check; fix the joints as instructed in previous_attempt.fix.')}
                             if feedback else {}))
        with ai_client.tally() as used:          # the tokens both questions of each attempt cost, in usage_tokens
            answer = _clean_answer(brandkit_review._ask(vision, ARTICULATOR, ask, _schema(), image_path=str(sheet),
                                                        reference_path=str(feedback['states_picture']) if feedback and feedback.get('states_picture') else None,
                                                        clean=_tidy), probe)
        record['usage_tokens'] += used['tokens']
        record.update(product_kind=answer['product_kind'], reasoning=answer['reasoning'], usage=answer['usage'])
        if not answer['articulated'] and not selected_ids:
            record.update(status='none', joints=[], states=[])
            record['notes'].append('没有需要展示的活动部件。')
            return _store(project_id, record)
        structural_issue = _structural_fault(answer, probe, selected_ids=selected_ids)
        if structural_issue:
            feedback = {'answer': {'joints': answer['joints'], 'states': answer['states']},
                        'problems': [{'id': 'open', 'ok': False, 'issue': structural_issue, 'fault': 'wrong_part'}],
                        'fix': structural_issue}
            record['notes'].append('结构检查未通过：%s' % structural_issue[:200])
            continue
        say('正在求解关节并渲染各个姿态')
        where = folder / ('solve%d' % attempt)
        solved = components.run_blender(SCRIPT, {'action': 'solve', 'model_path': str(project['model_path']), 'size_mm': size_mm, 'size': 640,
                                                 'joints': answer['joints'], 'states': answer['states']}, where, cancelled)
        joints = solved['joints']
        states = _clamped(answer['states'], joints)
        if states != answer['states']:          # a value past the solved range: render the states as they will be shown
            where = folder / ('solve%db' % attempt)
            solved = components.run_blender(SCRIPT, {'action': 'solve', 'model_path': str(project['model_path']), 'size_mm': size_mm, 'size': 640,
                                                     'joints': answer['joints'], 'states': states}, where, cancelled)
            joints = solved['joints']
        picture = state_sheet(where, solved['previews'], states, folder / ('states%d.png' % attempt))
        say('正在检查姿态是否合理')
        measured = {row.get('state'): row['measured'] for row in solved.get('previews') or [] if isinstance(row.get('measured'), dict)}
        with ai_client.tally() as used:
            verdict = brandkit_review._ask(vision, VERIFIER, {'product': facts['product'],
                                                              'states': [{'id': s['id'], 'label': s['label_en'], 'values': s['values'],
                                                                          **({'measured': measured[s['id']]} if s['id'] in measured else {})} for s in states],
                                                              'joints': [{'name': j['name'], 'type': j['type'], 'max': j['max']} for j in joints]},
                                           _verify_schema(), image_path=str(picture))
        record['usage_tokens'] += used['tokens']
        verdict['states'] = [_measured_wins(row, measured.get(row.get('id'))) for row in verdict.get('states') or []]
        record.update(joints=joints, states=states, previews=str(picture), verdict=verdict)
        # A state with every value 0 is the model exactly as delivered: whatever the check thinks of how it was modelled
        # (cutters modelled with their handles spread), it is not a pose this agent made, so it cannot be wrong.
        as_delivered = {state['id'] for state in states if not any(state['values'].values())}
        verdict['states'] = [{**row, 'ok': True} if row.get('id') in as_delivered else row for row in verdict.get('states') or []]
        if not [row for row in verdict['states'] if not row.get('ok')]:
            verdict['ok'] = True
        if verdict.get('ok') and all(row.get('ok') for row in verdict.get('states') or []):
            record['status'] = 'ready'
            return _store(project_id, record)
        problems = [row for row in verdict.get('states') or [] if not row.get('ok')]
        feedback = {'answer': {'joints': answer['joints'], 'states': answer['states']}, 'problems': problems, 'fix': verdict.get('fix', ''),
                    'states_picture': str(picture)}
        record['notes'].append('姿态检查未通过：%s' % (verdict.get('fix') or '；'.join(row.get('issue', '') for row in problems))[:200])
    # two tries and still wrong: keep only the rest pose
    record['status'] = 'failed'
    return _store(project_id, record)


def _store(project_id, record):
    with LOCK:
        project = get('project', project_id)
        project['articulation'] = record
        put('project', project)
    return record


def plan_for(project, state_id):
    """``plan['articulation']`` for one of the project's states, or None (rest pose, or no articulation)."""
    record = project.get('articulation') if isinstance(project.get('articulation'), dict) else None
    if not record or record.get('status') != 'ready':
        return None
    state = next((row for row in record.get('states') or [] if row['id'] == state_id), None)
    if not state or not any(state['values'].values()):
        return None
    return {'state': state_id, 'joints': [{**{k: v for k, v in joint.items() if k not in ('notes',)}, 'value': state['values'].get(joint['name'], 0)}
                                          for joint in record['joints']]}


def states(project):
    record = project.get('articulation') if isinstance(project.get('articulation'), dict) else None
    return list(record.get('states') or []) if record and record.get('status') == 'ready' else []


def usage(project, language='en'):
    record = project.get('articulation') if isinstance(project.get('articulation'), dict) else None
    return list(((record or {}).get('usage') or {}).get(language) or [])


def public(project):
    """What the UI shows: status, joints in words, states with their preview."""
    record = project.get('articulation') if isinstance(project.get('articulation'), dict) else None
    parts_src = (record.get('parts') if record and record.get('parts') else None) or []
    choices = []
    seen = set()
    if parts_src:
        longest = max((max(p.get('size_mm') or [0]) for p in parts_src), default=1.0) or 1.0
        for p in sorted(parts_src, key=lambda r: max(r.get('size_mm') or [0]), reverse=True):
            name = str(p.get('object') or '').strip()
            if not name or name in seen or (len(parts_src) > 12 and _is_fastener(p, longest)):
                continue
            seen.add(name); choices.append({'id': p['id'], 'name': name})
    else:
        objs = ((project.get('analysis') or {}).get('objects') or [])
        for row in objs:
            name = str(row.get('name') or '').strip() if isinstance(row, dict) else ''
            if not name or name in seen or (len(objs) > 12 and FASTENER_WORDS.search(name)):
                continue
            seen.add(name); choices.append({'id': None, 'name': name})
    if not record:
        return {'status': 'unknown', 'selected_parts': [], 'part_choices': choices[:40]}
    return {'status': record.get('status'), 'product_kind': record.get('product_kind', ''), 'reasoning': record.get('reasoning', ''),
            'joints': [{'name': j['name'], 'type': j['type'], 'max': j['max'], 'moving': len(j.get('moving') or [])} for j in record.get('joints') or []],
            'states': record.get('states') or [], 'usage': record.get('usage') or {}, 'notes': record.get('notes') or [],
            'selected_parts': list(record.get('selected_parts') or []), 'part_choices': choices[:40],
            'running': project.get('id') in RUNNING}

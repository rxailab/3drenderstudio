"""Usage scenes that need people or a real place, made by an image model.

A scene with an operator, a jobsite or a garden cannot be assembled from a
floor, a wall and a few props. For those an image model paints the scene. It
used to be given renders of the product and asked to draw the product into
the scene; it drew it again, and the copy drifted: parts bent, went missing or
changed size, most of all in a hand. Now the product is not drawn by the model
at all (see scene_lock.py):

    product_render()  the product from the scene's camera, on transparent film (cached)
    prompt()          the request: the product in image 1 is finished and fixed;
                      paint the scene, the hands and the shadow around it
    edit()            one call to the Images API with the canvas and a mask that
                      keeps the product, streamed so slow gateways hold; when a
                      gateway gives up all the same, once more at low quality
    scene_lock        whether the product stayed where it was and as it was,
                      measured on the picture
    check()           the vision model's look: the product beside its render,
                      whether a person (or only hands) is there
    create()          all of the above; returns the image and its record

A picture that moved, redrew or hid the product, or got the people wrong, is
made once more with the fault named.

No third-party picture is ever sent: the reference pack contributes words
only (see scene_audience.py), and publisher names are removed from them.
"""
import base64
import copy
import hashlib
import io
import json
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

import engine
import scene_audience
import usage_rules
from studio_store import DATA, ROOT, atomic_json

REFERENCE_VERSION = 1          # part of the cache key: raise it when reference renders change
REFERENCE_QUALITY = 'reference'
REFERENCE_TIMEOUT = 900
MAX_ATTEMPTS = 2               # the first picture, and one correction when the product changed
# A picture that kept the product but got its use wrong (held by one handle, the wire at the tip, balanced on a point)
# is the image model's commonest miss and the one a correction most often fixes: it gets one try more.
USAGE_FLAGS = frozenset(('held_correctly', 'used_correctly', 'rests_stably', 'contact_plausible', 'installed_correctly'))
USAGE_RETRY = 1                # recorded on a scene made with that extra try, so older failed scenes are tried again once
RENDER_PX = {2048: 1600, 1536: 1300, 1024: 1100}     # the product render for a canvas of this longer side
RENDER_SAMPLES = 48
LOCK_FIXES = {'moved': 'the product was moved, resized or drawn again somewhere else: it must stay exactly where it is in image 1, at the '
                       'same size and angle, and only the area around it may be painted',
              'changed': 'parts of the product were redrawn%s: every part must stay exactly as it is in image 1',
              'hidden': 'the product is almost hidden: hands or objects may overlap its edges, but most of it must stay in view'}
LOCK_NOTES = {'moved': '图像模型移动或重画了产品，没有保持它在画面中的位置与外形。', 'changed': '图像模型改动了产品的部分外形。',
              'hidden': '产品被手或物体遮挡得太多。'}
READ_TIMEOUT = 150             # seconds without a byte
TOTAL_TIMEOUT = 480
MAX_STREAM = 96 * 1024 * 1024
MAX_IMAGE = 40 * 1024 * 1024
# The pages are laid out at 1500 px: a 1536 px picture is all they use. At 2048 px the image model's output cost 3568
# tokens a picture ($0.107 at gpt-image-2 medium); at 1536 px it is about 2511 ($0.075), 30% less. Sizes are multiples of 16.
SIZES = {'1:1': (1536, 1536), '4:3': (1536, 1152), '3:4': (1152, 1536)}
SIZES_1K = {'1:1': (1024, 1024), '4:3': (1536, 1024), '3:4': (1024, 1536)}

FIDELITY_PROMPT = '''You check a generated photograph against renders of the real product. The REFERENCE images are renders of the product's 3D model from several sides: they are the truth about its shape, parts, colours and materials. GENERATED is a photograph made by an image model; it should show this same product in a scene.
Compare the product in GENERATED with the references. Return data only:
- product_present: the product is in the picture exactly once and recognisable; a second copy, a doubled outline or a ghost of it counts as false.
- shape_consistent: its overall shape and proportions match, allowing for perspective; every drawer, door, lid, jaw and blade is open or closed exactly as in the references (one shown open that is shut in the references counts as false).
- parts_consistent: every distinct part visible from this side is there, in the same number and place (handles, drawers, doors, wheels, buttons, locks, feet); nothing was invented or dropped. Things kept inside an open drawer, box or cabinet (tools, sockets, parts) are the scene's props, not parts of the product.
- colours_consistent: the colours and finishes of the body and of each part match, allowing for the light of the scene.
- no_added_text: nothing is written or printed on the product that the references do not show, and the scene has no logo, sign or caption.
- nothing_attached: no rigid piece is attached to the product or wrapped round it that the references do not show: an extra grip, sleeve, cover, cap, handle, bracket or strap counts, even where it hides part of the product. Hands, gloves and the workpiece it works on are not attached pieces.
- held_correctly: when hands hold the product, each hand is on a part made to be held (a handle, a grip, a body), from the side a user holds it, with the working end (jaws, blade, bit, nozzle, bar) pointing at the work; no hand holds a blade, a battery, a guard or a cutting part, and every arm leads back to the same person. Hand tools are held one way only: pliers, cutters and scissors by both handles, in the palm with the fingers wrapped round them, never pinched at the head, the jaws or the pivot; a hammer by the lower half of its handle, never by the head or the neck; a screwdriver by its handle, the shaft in line with the forearm and the bit in the screw; a saw by its handle. usage_rules, when given, say how this product is held. True when no hand touches it.
- contact_plausible: everything that meets the product (a wire, cable, pipe, branch, board or screw) touches it or passes in front of or behind it as one continuous piece, and a thing being cut or gripped lies between the jaws or blade where it would really be; a screw, bolt or nail being driven is on the same straight line as the bit or driver, head against its tip; nothing passes through solid metal or plastic, and no loose bits (wire ends, strands, offcuts) stick out from behind the product without joining something visible.
- used_correctly: when usage rules are in the facts and the product is being used, it is used as they say (for example a caliper's jaws close squarely on both faces of the workpiece with the beam perpendicular to them, not tilted across it or touching only an edge; cutting pliers hold the wire deep in the jaws near the pivot, square to the blades, never at the tips; a hammer's face meets the nail head squarely with the nail perpendicular to the surface, or the nail's shank sits in the claw's slot with the handle levered back, never the claw just pressed against a wall). True when the product is at rest or no rules are given.
- rests_stably: when nothing holds the product, it rests the way it really would on its surface (a product whose mounting is 'wall', hanging flat against a wall, rests stably): lying on a side or standing on its base, its weight over what carries it. False when it is balanced on a tip, an edge, a jaw point or a handle end, propped at an angle with nothing holding it, or floating above the surface. True when hands hold it.
- surroundings_new: everything around the product looks brand new and clean. False when any work surface, table, bench, shelf, wall, floor, vehicle interior or object in the scene looks used: worn, scratched, scored, dented, drilled, chipped, rusty, stained, oily, greasy, dusty or dirty, with peeling paint, or with rubbish or a mess strewn about. Judge the condition of the surfaces and things, not whether the place is a working one: tools, parts, boxes and supplies set out or hanging in their places are not wear, and neither are reflections, shadows, natural wood grain or the even texture of concrete or plaster. False only for wear or dirt that can clearly be seen; true when unsure. The product itself is not judged here. When false, one of the issues says what looks used (\"the workbench top is scratched and stained\").
- installed_correctly: when mounting in the facts is 'wall', the product hangs on a vertical wall with its back against it, at a natural height, and does not stand on a floor, a bench, a table, a shelf or a vehicle floor. True when mounting is not 'wall'.
- scale_plausible: the surroundings are at a believable scale for the product's real size (size_mm in the facts, its longest side): a tool on a van floor or a table is the size it would really be beside them, not a giant or a toy.
- person_visible: a person, or a clearly visible part of one (a hand, an arm, legs), appears anywhere in GENERATED. This is an observation, not a fault.
- face_visible: a human face or head appears anywhere in GENERATED. This is an observation, not a fault.
- issues: up to 5 short, concrete differences in English, each naming the part and what is wrong ("top surface is black, should be light brushed steel"). Empty when there are none.
- note: one sentence in the language of note_language giving the verdict.
Judge only what can be seen. A side that faces away is not a difference, and a part hidden behind a person or an object is not missing.
When a CLOSE-UP is given, judge shape, parts and colours there first: it shows the product larger than GENERATED does, the render's panel (left, or top for a long product) beside the generated one (right, or below).'''


# What the request says about people, and what a picture that got it wrong is told to fix.
PEOPLE_RULES = {
    True: ('PEOPLE. The picture must show a person actually using or handling the product: their hands on it and at least their arms '
           'and upper body in the frame, in a natural working posture. The person is clearly visible, not a blur at the edge. The '
           'product stays the main subject and is not hidden by the person; hands hold it as it is placed and never change its shape.'),
    False: ('PEOPLE. Nobody is in the picture: no person, no hands or other body parts, no silhouettes, shadows or reflections of '
            'people. The product lies or stands on its base ready for use in the scene, resting stably.'),
}
PEOPLE_WALL = ('PEOPLE. Nobody is in the picture: no person, no hands or other body parts, no silhouettes, shadows or reflections of '
               'people. The product hangs on its wall, ready for use.')
PEOPLE_FIXES = {True: 'no person is visible: show a person clearly using or handling the product, hands on it, arms and upper body in frame',
                False: 'a person or a part of one is visible: remove every person, hand and body part from the picture'}
PEOPLE_NOTES = {True: '画面里没有出现人物，与「包含人物」的要求不符。', False: '画面里出现了人物，与「不含人物」的要求不符。'}
# A hand at work says more about a tool than a whole figure does: the default way a person appears.
FRAMINGS = ('hands', 'person')
HANDS_RULE = ('PEOPLE. A close-up of the product at work. One or two hands of an adult come in from the edge of the frame, hold and operate the '
              'product where a user grips it, and the thing it '
              'is working on is in the frame at the point of contact. The frame of image 1 is final: do not zoom in, crop, widen or '
              'reframe it, so the product keeps its place and size; forearms may enter from the edge of the frame, but no face, no head '
              'and no whole figure appears. This overrides any wider view in the scene description. The product stays sharp and in '
              'full view: fingers may wrap round it where a hand grips it and cover small parts of it, and never change its shape; '
              'every part of it still in view stays exactly as it is in image 1.')
HANDS_FIXES = {'missing': 'no hand is visible: show one or two hands holding and operating the product, close up, with what it works on',
               'face': 'a face, a head or a whole figure is visible: crop to a close-up of the hands on the product and the work, with no '
                       'head or torso in the frame'}
HANDS_NOTES = {'missing': '画面里没有出现操作产品的手，与「手部特写」的要求不符。', 'face': '画面里出现了人脸或整个人，与「手部特写」的要求不符。'}


class ImageError(ValueError):
    """A generation that did not produce a picture; the message is safe to show."""


class GatewayError(ImageError):
    """The gateway in front of the image model gave up (HTTP 502 or 504), usually because the picture took too long."""


class RejectedError(ImageError):
    """The service refused the request as made (HTTP 400): a parameter it does not take, such as the mask."""


def config():
    """The image endpoint if it can be called, else None."""
    import ai_config
    value = ai_config.get_image_config()
    return value if ai_config.public_image_config()['available'] else None


def endpoint(value):
    base = value['base_url'].rstrip('/')
    if base.endswith('/images/edits'):
        return base
    return base + ('/images/edits' if re.search(r'/v[0-9]+(?:beta)?$', base) else '/v1/images/edits')


def size_for(aspect, model=''):
    """Pixels to ask for. The first image model only makes three fixed sizes."""
    table = SIZES_1K if re.search(r'(?:^|[/-])gpt-image-1(?:[.-]|$)', str(model).lower()) else SIZES
    return table.get(aspect, table['1:1'])


AZIMUTH_STEP = 15              # reference views snap to these steps, so a slightly different camera reuses the renders
ELEVATION_STEP = 10


def angles(azimuth, elevation):
    """The target view, the product's other front corner, and a look down on it.

    Rendering the three views is the longest part of a generated scene, and every
    proposal names a slightly different camera (35 degrees, then 30). An image
    model does not need the angle to the degree, so the views snap to a coarse
    grid: a second scene for the same product then finds them already rendered.
    """
    azimuth = round(float(azimuth) / AZIMUTH_STEP) * AZIMUTH_STEP
    elevation = max(ELEVATION_STEP, round(float(elevation) / ELEVATION_STEP) * ELEVATION_STEP)
    other = -azimuth if abs(azimuth) >= 20 else azimuth + 75
    return [(round(float(azimuth), 1), round(float(elevation), 1)), (round(float(other), 1), round(float(elevation), 1)),
            (round(azimuth * .5, 1), round(min(60.0, elevation + 30), 1))]


def reference_shot(index, azimuth, elevation):
    return {'id': 'ref%d' % index, 'label': '参考角度 %d' % index, 'kind': 'full', 'style': 'white', 'projection': 'perspective',
            'azimuth': azimuth, 'elevation': elevation, 'focal_length': 55}


def _reference_plan(plan):
    """The product as the plan shows it, on white, with nothing of any scene."""
    clean = copy.deepcopy(plan)
    for key in ('scene_set', 'scene_brief', 'scene_checkpoints', 'shot_adjustments'):
        clean.pop(key, None)
    clean.update(aspect='1:1', visual_review=False)
    return clean


def _render(request, folder, cancelled):
    executable = engine.blender_path(); script = ROOT / 'blender' / 'worker.py'
    if not executable or not script.is_file():
        raise ImageError('渲染组件未就绪，无法为图像模型准备产品参考图。')
    folder.mkdir(parents=True, exist_ok=True)
    request = {**request, 'output_dir': str(folder)}
    atomic_json(folder / 'request.json', request)
    (folder / 'result.json').unlink(missing_ok=True)
    command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '8', '--python-exit-code', '1',
               '--python', str(script), '--', str(folder / 'request.json')]
    started = time.time()
    try:
        with (folder / 'blender.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while process.poll() is None:
                if cancelled():
                    process.terminate(); raise InterruptedError()
                if time.time() - started > REFERENCE_TIMEOUT:
                    process.kill(); raise ImageError('产品参考图渲染超时，请重试。')
                time.sleep(.3)
    except InterruptedError:
        raise
    except OSError as exc:
        raise ImageError('产品参考图未能渲染，请重试。') from exc
    try:
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        result = None
    png = folder / (folder.name + '.png')
    if process.returncode or not isinstance(result, dict) or result.get('success') is not True or not png.is_file():
        raise ImageError('产品参考图渲染失败，当前方案未修改。')
    engine.verify_image(png, 'PNG')
    return png


def references(project, plan, azimuth, elevation, cancelled=None, progress=None):
    """Renders of the product for the image model: [{'path', 'azimuth', 'elevation'}], target view first.

    Each depends only on the product's look and the angle, so a second scene
    for the same product reuses them.
    """
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    clean = _reference_plan(plan); rows = []
    views = angles(azimuth, elevation)
    for index, (az, el) in enumerate(views, 1):
        shot = reference_shot(index, az, el)
        request = {'action': 'render', 'model_path': project['model_path'], 'plan': clean, 'shot': shot, 'quality': REFERENCE_QUALITY}
        key = hashlib.sha256(json.dumps([REFERENCE_VERSION, request], sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]
        folder = DATA / project['id'] / 'scene_ai' / 'references' / key
        png = folder / (folder.name + '.png')
        ready = False
        if png.is_file() and (folder / 'result.json').is_file():
            try:
                engine.verify_image(png, 'PNG'); ready = True
            except (OSError, ValueError, RuntimeError):
                ready = False
        if not ready:
            if stop(): raise InterruptedError()
            say('渲染产品参考角度 %d / %d' % (index, len(views)))
            png = _render(request, folder, stop)
        rows.append({'path': png, 'azimuth': az, 'elevation': el, 'cached': ready})
    return rows


def product_render(project, plan, azimuth, elevation, size_mm, canvas, cancelled=None, progress=None):
    """The product as the scene's camera sees it, on transparent film, at the plan's pose and finishes. Cached per request.

    Returns the staged result: 'path' (RGBA PNG), 'product_rect', 'azimuth', 'elevation' and the rest of blender/stage.py's report.
    """
    import components
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    pixels = RENDER_PX.get(max(canvas), 1400)
    request = {'action': 'render', 'model_path': str(project['model_path']), 'plan': _reference_plan(plan), 'size_mm': float(size_mm or 300),
               'camera': {'azimuth': float(azimuth), 'elevation': float(elevation), 'focal_length': 55, 'margin': .04},
               'ground': 'none', 'size': [pixels, pixels], 'samples': RENDER_SAMPLES}
    say('渲染产品（锁定外形用）')
    try:
        return components.stage(project['id'], request, stop)
    except components.ComponentCancelled:
        raise InterruptedError() from None
    except ValueError as exc:
        raise ImageError('产品渲染失败，当前方案未修改：%s' % str(exc)[:160]) from None


POSE_LINES = {'held': 'Build the scene around this exact pose: the hands grip the product where a hand naturally holds it in this pose, '
                      'and the thing it works on meets it there, at the right scale.',
              'rest': 'Build the scene around this exact pose: the product rests on its surface just as it is, with its shadow under it, '
                      'the surface right under the parts that carry it.'}
# A product set down must rest the way it really would: the pose (scene_pose) is already one it can rest in, and the scene
# must agree with it. Measured on the user's pictures: pliers stood on a jaw tip and a handle end, which no bench holds.
REST_RULE = ('It rests stably, the way a person would put it down: lying on a side or standing on its base, its weight over what carries '
             'it, on a level surface. It is never balanced on a tip, an edge, a jaw point or a handle end, never propped at an angle '
             'with nothing holding it, and never floating.')


# Every place a product is shown in is new: the image model's own idea of a workshop, a van or a jobsite is a used one
# (an old bench scored and drilled, oil on the floor, scuffed walls), which makes the product look second-hand too.
NEW_PLACE = ('THE PLACE IS NEW. Everything around the product is brand new and spotless, as on the day it was fitted out: '
             'new furniture and work surfaces with smooth, unmarked tops, freshly painted clean walls, clean floors, new tools '
             'and objects, and any vehicle in the scene new and clean inside. No wear, scratches, dents, holes, burn marks, rust, peeling paint, '
             'stains, oil, grease, dust, dirt or grime anywhere, no rubbish and no mess. Tidy and orderly.')
# The scene text is free text (the strategist's, or the user's hint): words asking for wear are taken out of it, so it
# never argues with NEW_PLACE.
WORN = re.compile(r'(?P<art>\b(?:an?)\s+)?(?<![\w-])(?P<word>well[- ](?:worn|used)|worn|weathered|grimy|greasy|oily|'
                  r'(?:oil|grease|paint)[- ](?:stained|splattered|spattered|covered)|dirty|dusty|dust[- ]covered|rust(?:y|ed)|battered|'
                  r'beat[- ]up|scuffed|stained|scratched|dented|chipped|peeling|shabby|cluttered|messy|lived[- ]in|gritty|muddy|grubby|'
                  r'filthy|mucky|smudged|scorched|old|aged|ageing|aging|vintage|rustic|time[- ]?worn|run[- ]down)(?![\w-])'
                  r'(?P<tail>\s*,\s*|\s+(?:and|or)\s+)?\s*', re.I)
PLACE_FLAGS = frozenset(('surroundings_new',))      # about the place, not the product: they do not make the product "changed"
WORN_FIX = ('the place looks used or dirty: make every surface, wall, floor, vehicle interior and piece of furniture around the '
            'product brand new and spotless, with no wear, scratches, stains, oil, dust or mess')
WORN_NOTE = '背景环境显得陈旧或脏乱。'


# A wear word that is negated ("no dusty corners", "never worn or dirty", "free of grease") asks for a clean place: kept.
NEGATED = re.compile(r'(?i)\b(?:no|not|never|without|nothing|free\s+of|free\s+from)\s+(?:(?:any|a|an)\s+)?(?:(?:%s)\b(?:\s*,\s*|\s+(?:and|or|nor)\s+)?)+'
                     % WORN.pattern.split('(?P<word>', 1)[1].split(')(?![\\w-])', 1)[0])


def fresh(text):
    """``text`` with the words that ask for wear or dirt taken out ("an old, oily workbench" -> "a workbench"). Text without
    such words comes back as it was; negated ones ("no dusty corners") stay, since they already ask for a clean place."""
    text = str(text or ''); kept = []

    def keep(match):
        kept.append(match.group(0)); return '\1%d\1' % (len(kept) - 1)

    def cut(match):
        article = match.group('art')
        return '\0%s ' % article.strip() if article else ''
    guarded = NEGATED.sub(keep, text)
    cleaned = WORN.sub(cut, guarded)
    if cleaned == guarded:
        return text
    def article(m):
        vowel = m.group(2).lower() in 'aeiou'; said = m.group(1)
        word = 'an' if vowel else 'a'
        return (word.upper() if said.isupper() and len(said) > 1 else word.capitalize() if said[0].isupper() else word) + ' '
    cleaned = re.sub(r'\0([Aa][Nn]?) (?=(\w))', article, cleaned).replace('\0', '')
    cleaned = re.sub(r'\s+([.,;:])', r'\1', re.sub(r'\s{2,}', ' ', cleaned)).strip()
    cleaned = re.sub(r'(^|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), cleaned)
    return re.sub(r'\x01(\d+)\x01', lambda m: kept[int(m.group(1))], cleaned)


# A product made to hang on a wall (mounting.py) is shown hanging there: never stood on a floor, a bench or a van floor.
WALL_RULE = ('IT HANGS ON A WALL. The product is made to be mounted on a wall: it hangs on a vertical wall, its back flat against '
             'the wall and held by its fixings, at a natural height (a cabinet at about chest height), with only the wall behind it '
             'and nothing under it. It never stands on a floor, a bench, a table, a shelf or a vehicle floor, and its front faces the '
             'camera exactly as in image 1.')
INSTALL_FIX = ('the product must hang on a wall: its back flat against a vertical wall at a natural height with nothing under it, '
               'never standing on a floor, bench, table, shelf or vehicle floor')


def prompt(product, size_mm, scene, aspect='1:1', corrections=None, people=None, framing=None, pose=None, usage=None, setting=None,
           mounting=None):
    """The words sent with the anchored canvas. ``scene`` is the only free text in it.

    ``people`` is True when a person must be in the picture, False when nobody
    may be, and None when the scene description alone decides. With a person,
    ``framing`` 'hands' asks for a close-up of the hands at work and no face. ``pose`` is scene_pose's record when the
    product was posed for the scene: its words say how it is held and where it meets the work.
    """
    hands = people is True and framing == 'hands'; wall = mounting == 'wall'
    scene = fresh(scene_audience.scrub(re.sub(r'\s+', ' ', str(scene or '')).strip()))[:1500]
    product = re.sub(r'\s+', ' ', str(product or 'product')).strip()[:120]
    size = ''
    if size_mm:
        size = ' Its largest dimension is about %s, which sets the scale of everything around it.' % (
            '%d cm' % round(size_mm / 10) if size_mm >= 100 else '%d mm' % round(size_mm))
    lines = [
        'Complete this photograph for an online shop. Image 1 is the unfinished photograph.',
        'THE PRODUCT is a %s.%s It is already in image 1, in its final place, %s. It is finished and fixed: '
        'keep it exactly as it is, in the same position, at the same size and angle, with the same shape, parts, colours and materials. '
        'Do not move, turn, resize, redraw, simplify or tidy it, and put no logo or lettering on it. Its moving parts (drawers, doors, '
        'lids, jaws, blades, sliders) stay exactly as image 1 shows them: never open, pull out, close, fold or move any of them, '
        'whatever the scene says. Only the light falling on it may change, to match the scene.' % (
            product, size, 'in front of a plain grey ground' if wall else 'on a plain grey ground'),
        'PAINT EVERYTHING ELSE. Replace the whole grey ground with the scene, built around the product as it %s, the place '
        'around it. The perspective, scale and light of the scene must agree with the product. Nothing of the grey ground may '
        'remain. THE SCENE. ' % ('hangs: the wall it is mounted on, directly behind it, and the soft shadow it casts on that wall' if wall
                                 else 'stands: what it rests on or is held by, the shadow it casts') + (scene or (
            'A believable place where this product is used, the product set down there ready for use.' if people is False
            else 'A believable place where this product is used, with a person using it.')),
        NEW_PLACE,
        'RULES. Natural, believable light. Correct scale between the product%s. No brand names, logos, '
        'signs, captions or other text anywhere in the picture, and no watermark.%s Nothing is added to the product: no extra grip, '
        'sleeve, cover, cap, strap or bracket on it. Whatever it touches (a wire, pipe, branch, board) meets it as one whole piece, '
        'in front of it, behind it or between its jaws, never through it, with no stray ends or strands around it. A screw, bolt or '
        'nail being driven sits exactly on the line of the bit or driver, its head on the bit\'s tip and its point going straight into '
        'the work along that same line. Composition for a %s frame.'
        % (((' and its surroundings', ' ' + (WALL_RULE if wall else REST_RULE)) if people is False else
            (', the hands and the surroundings', ' The hands are an ordinary adult\'s, bare or in plain work gloves suited to the task, '
             'holding the product by the parts made to be held, from the user\'s side, its working end pointing at the work: '
             'pliers and cutters by both handles with the fingers wrapped round them, never pinched at the head; a hammer by the '
             'lower half of its handle.') if hands else
            (', people and surroundings', ' People are ordinary adults in plain clothes suited to the work, shown naturally, '
             'holding the product by the parts made to be held, as the tool is meant to be held.'))
           + ({'1:1': 'square', '4:3': 'landscape 4:3', '3:4': 'portrait 3:4'}.get(aspect, 'square'),)),
    ]
    if pose and pose.get('description'):
        text = re.sub(r'\s+', ' ', str(pose['description'])).strip()[:400].rstrip('.')
        contact = re.sub(r'\s+', ' ', str(pose.get('contact') or '')).strip()[:120].rstrip('.')
        lines.insert(2, 'THE POSE. The product in image 1 is already posed for this moment: %s.%s %s' % (
            text, (' It meets the work at %s.' % contact) if contact else '', POSE_LINES['rest' if people is False else 'held']))
    use = []
    if setting:
        use.append('Its moving parts are set as image 1 shows (%s): keep them exactly so; whatever it works on fits that setting '
                   '(the gap between open jaws is the thickness of the piece between them).' % re.sub(r'\s+', ' ', str(setting)).strip()[:120])
    rules = [re.sub(r'\s+', ' ', str(rule)).strip().rstrip('.')[:usage_rules.LENGTH] for rule in (usage or [])[:usage_rules.LIMIT] if str(rule).strip()]
    if rules and people is not False:
        use.append('Show it used correctly: ' + '; '.join(rules) + '.')
    if use:
        lines.insert(3 if pose and pose.get('description') else 2, 'USE. ' + ' '.join(use))
    if hands:
        lines.insert(3 if not (pose and pose.get('description')) else 4, HANDS_RULE)
    elif people in PEOPLE_RULES:
        lines.insert(3 if not (pose and pose.get('description')) else 4,
                     PEOPLE_WALL if wall and people is False else PEOPLE_RULES[people])
    if wall and people is not False:
        lines.insert(2, WALL_RULE)
    if corrections:
        fixes = '; '.join(re.sub(r'\s+', ' ', str(item)).strip()[:200] for item in corrections[:6] if str(item).strip())
        if fixes:
            lines.append('CORRECTIONS. An earlier attempt got these wrong, compared with image 1 and with this request. Fix each of them: ' + fixes + '.')
    return '\n\n'.join(lines)


def _multipart(fields, files):
    boundary = 'studio' + uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n' % (boundary, name)).encode('utf-8') + str(value).encode('utf-8') + b'\r\n')
    for name, filename, content in files:
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: image/png\r\n\r\n'
                      % (boundary, name, filename)).encode('utf-8') + content + b'\r\n')
    parts.append(('--%s--\r\n' % boundary).encode('utf-8'))
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


def _http_message(error):
    """A safe sentence for an HTTP failure. Upstream text is read for its code only, never shown."""
    code = ''
    try:
        detail = json.loads(error.read(8192).decode('utf-8', 'replace'))
        problem = detail.get('error') if isinstance(detail, dict) else None
        if isinstance(problem, dict):
            code = '%s %s' % (problem.get('code') or '', problem.get('type') or '')
    except (OSError, ValueError):
        pass
    if 'moderation' in code or 'safety' in code:
        return '图像模型的内容审核拒绝了这次生成，请调整场景说明后重试。'
    messages = {400: '图像请求被拒绝，请检查图像模型名称及其支持的尺寸。', 401: '图像接口的 API Key 无效或未授权。',
                402: '图像服务的额度已用尽或账户欠费，请充值后重试。', 403: '图像服务拒绝访问，请检查令牌与模型权限。',
                404: '未找到图像接口或模型，请检查地址与模型名称。', 408: '图像服务处理超时，请重试。',
                418: '图像服务拒绝了当前网络地址（多次使用无效的 Key 后可能被临时屏蔽）；请核对 Key，稍后再试。',
                413: '参考图过大，图像服务未接受。', 429: '图像服务请求过于频繁或额度受限，请稍后重试。'}
    return messages.get(error.code, '图像服务暂时不可用，请稍后重试。' if error.code >= 500 else '图像请求失败，请检查服务配置。') + '（HTTP %s）' % error.code


def _decode(encoded):
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ImageError('图像服务返回的图片无法解码，请重试。') from None
    if not content or len(content) > MAX_IMAGE:
        raise ImageError('图像服务返回的图片大小异常，请重试。')
    try:
        with Image.open(io.BytesIO(content)) as picture:
            picture.verify()
        with Image.open(io.BytesIO(content)) as picture:
            picture.load()
            if picture.format != 'PNG' or min(picture.size) < 256 or max(picture.size) > 8192:
                raise ValueError('unexpected image')
            size = picture.size
    except (OSError, ValueError, SyntaxError):
        raise ImageError('图像服务返回的不是有效的 PNG 图片，请重试。') from None
    return content, size


def _usage(value):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ('input_tokens', 'output_tokens', 'total_tokens') if type(value.get(key)) is int}


def _read_stream(response, stop, say, deadline):
    """Server-sent events: partial pictures keep the connection alive, the completed one is the result."""
    total = 0; data = []; final = None; usage = {}
    while True:
        if stop(): raise InterruptedError()
        if time.time() > deadline: raise ImageError('图像生成超时，请重试。')
        line = response.readline(MAX_STREAM + 1)
        total += len(line)
        if total > MAX_STREAM: raise ImageError('图像服务的响应超过大小上限。')
        text = line.decode('utf-8', 'replace').rstrip('\r\n')
        if text.startswith('data:'):
            data.append(text[5:].lstrip())
        if text == '' or not line:
            if data and data != ['[DONE]']:
                try:
                    event = json.loads('\n'.join(data))
                except ValueError:
                    event = None
                kind = event.get('type', '') if isinstance(event, dict) else ''
                if isinstance(event, dict) and (event.get('error') or kind == 'error'):
                    raise ImageError('图像服务未完成这次生成，请重试。')
                if kind.endswith('.partial_image'):
                    say('图像模型正在生成（已返回草图）')
                elif kind.endswith('.completed') and isinstance(event.get('b64_json'), str):
                    final = event['b64_json']; usage = _usage(event.get('usage'))
            data = []
            if not line:
                break
    if final is None:
        raise ImageError('图像服务没有返回图片，请重试。')
    return final, usage


def edit(value, text, images, size, cancelled=None, progress=None, mask=None):
    """One generation: the renders in, a PNG out. Returns {'png', 'size', 'usage'}.

    ``mask`` is PNG bytes the size of the first image: where it is transparent the model may paint,
    where it is opaque the first image is to be kept.
    """
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    from ai_client import NoRedirect
    from ai_model_policy import assert_model_allowed
    assert_model_allowed(value)        # a model the user has barred is barred here too
    key = value.get('api_key')
    if not isinstance(key, str) or not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ImageError('图像接口的 API Key 格式无效，请去除空格、换行和非英文字符。')
    if not images or len(images) > 8:
        raise ImageError('产品参考图数量无效。')
    files = []
    for index, path in enumerate(images, 1):
        try:
            content = Path(path).read_bytes()
            engine.verify_image(path, 'PNG')
        except (OSError, ValueError, RuntimeError):
            raise ImageError('无法读取产品参考图，未发送图像请求。') from None
        files.append(('image[]', 'product-%d.png' % index, content))
    if mask:
        files.append(('mask', 'mask.png', mask))
    fields = [('model', value['model']), ('prompt', text), ('size', '%dx%d' % tuple(size)), ('quality', value.get('quality', 'medium')),
              ('stream', 'true'), ('partial_images', '2')]
    body, content_type = _multipart(fields, files)
    request = urllib.request.Request(endpoint(value), data=body, method='POST',
                                     headers={'Content-Type': content_type, 'Accept': 'text/event-stream, application/json',
                                              'Authorization': 'Bearer ' + key})
    deadline = time.time() + TOTAL_TIMEOUT
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=READ_TIMEOUT) as response:
            if 'text/event-stream' in (response.headers.get('Content-Type') or ''):
                encoded, usage = _read_stream(response, stop, say, deadline)
            else:
                raw = response.read(MAX_STREAM + 1)
                if len(raw) > MAX_STREAM: raise ImageError('图像服务的响应超过大小上限。')
                try:
                    answer = json.loads(raw.decode('utf-8'))
                    encoded = answer['data'][0]['b64_json']; usage = _usage(answer.get('usage'))
                except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
                    raise ImageError('图像服务返回了无效响应，请检查图像接口配置。') from None
    except InterruptedError:             # a cancellation is an OSError by inheritance; it is not a network failure
        raise
    except urllib.error.HTTPError as error:
        message = _http_message(error); code = error.code; error.close()
        raise (GatewayError if code in (502, 504) else RejectedError if code == 400 else ImageError)(message) from None
    except (socket.timeout, TimeoutError):
        raise ImageError('图像生成超时，请重试。') from None
    except urllib.error.URLError:
        raise ImageError('无法连接图像服务，请检查网络、证书和接口地址。') from None
    except (ConnectionError, OSError):
        raise ImageError('图像服务的连接中断，请重试。') from None
    if not isinstance(encoded, str):
        raise ImageError('图像服务返回了无效响应，请检查图像接口配置。')
    content, actual = _decode(encoded)
    # what the picture cost goes into the same log as the text and vision requests (data/ai_usage.jsonl)
    import ai_client
    ai_client.log_usage({'model': str(value['model'])[:120], 'caller': 'scene_imagegen.edit', 'usage': usage or {}, 'n_images': len(files)})
    return {'png': content, 'size': list(actual), 'usage': usage}


FIDELITY_FLAGS = ('product_present', 'shape_consistent', 'parts_consistent', 'colours_consistent', 'no_added_text',
                  'nothing_attached', 'held_correctly', 'contact_plausible', 'used_correctly', 'rests_stably', 'scale_plausible',
                  'installed_correctly', 'surroundings_new')


def fidelity_schema():
    properties = {name: {'type': 'boolean'} for name in FIDELITY_FLAGS + ('person_visible', 'face_visible')}
    properties['issues'] = {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}, 'minItems': 0, 'maxItems': 5}
    properties['note'] = {'type': 'string', 'minLength': 1, 'maxLength': 300}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def check(reference_paths, generated_path, product, people=None, framing=None, closeup=None, size_mm=None, usage=None, mounting=None):
    """Compare the generated product with the renders, and see whether a person is in the picture.

    Returns {'status': 'passed' | 'failed' | 'unchecked', 'issues', 'note', ...}.
    ``people`` is what was asked for (True, False, or None for no requirement);
    a picture that shows the opposite fails like one that changed the product.
    Without a vision model, or when it does not answer, the picture is kept and
    marked unchecked: the person looking at it is then the check.
    """
    import ai_client as ai
    import ai_config
    value = ai_config.get_vision_config()
    if not all(value.get(key) for key in ('enabled', 'api_key', 'model')):
        return {'status': 'unchecked', 'issues': [], 'note': '未配置视觉模型，未自动核对产品是否变形，请对照参考图查看。'}
    schema = fidelity_schema()
    try:
        facts = {'product': str(product)[:120], 'reference_count': len(reference_paths), 'note_language': 'zh-CN',
                 'size_mm': round(float(size_mm)) if size_mm else None, 'mounting': mounting or 'not wall-mounted',
                 'usage_rules': [str(rule)[:usage_rules.LENGTH] for rule in (usage or [])[:usage_rules.LIMIT]]}
        body = ai._payload(value, FIDELITY_PROMPT, facts, schema, max_tokens=1500)
        blocks = []
        for index, path in enumerate(reference_paths, 1):
            blocks += [{'type': 'text', 'text': 'REFERENCE %d: render of the real product.' % index}, ai._image(path, value['provider'])]
        blocks += [{'type': 'text', 'text': 'GENERATED: the photograph to check.'}, ai._image(generated_path, value['provider'], max_size=1536)]
        if closeup:
            blocks += [{'type': 'text', 'text': 'CLOSE-UP: two panels with a white gap between them, the same box at the same scale: the render '
                                                'first, then the same part of GENERATED. Side by side the render is on the left and GENERATED on '
                                                'the right; stacked (a long product) the render is on top and GENERATED below. '
                                                'Compare the product part by part here; anything in front of it (a hand, the workpiece) is not a difference.'},
                       ai._image(closeup, value['provider'], max_size=1536)]
        message = body['messages'][-1]
        if not isinstance(message.get('content'), list):
            raise ValueError('unexpected request shape')
        message['content'][:0] = blocks
        answer = ai._response(ai._post(value, body), value, ai.TOOL_NAME)
        ai._check_schema(answer, schema)
    except Exception:                # the picture exists; a check that could not run must not lose it
        return {'status': 'unchecked', 'issues': [], 'note': '视觉模型未能完成核对，请对照参考图查看产品是否变形。'}
    flags = {name: answer[name] for name in FIDELITY_FLAGS}
    faithful = all(flags.values()); visible = answer['person_visible']; face = answer['face_visible']
    kept = all(ok for name, ok in flags.items() if name not in PLACE_FLAGS)      # the product, apart from the place round it
    issues = [] if faithful else [re.sub(r'\s+', ' ', item).strip()[:200] for item in answer['issues']]
    note = re.sub(r'\s+', ' ', answer['note']).strip()[:300]
    if mounting != 'wall' and not flags['installed_correctly']:
        flags['installed_correctly'] = True              # only a wall-mounted product can be wrongly installed
        faithful = all(flags.values()); kept = all(ok for name, ok in flags.items() if name not in PLACE_FLAGS)
        issues = [] if faithful else issues
    if not flags['installed_correctly'] and INSTALL_FIX not in issues:
        issues = [INSTALL_FIX] + issues[:5]
    if not flags['surroundings_new']:
        issues = issues[:5] + [WORN_FIX]          # the product's own differences first: corrections keep the first six
        if kept:
            note = WORN_NOTE + note[:300 - len(WORN_NOTE)]
    hands = people is True and framing == 'hands'
    wrong_people = people in PEOPLE_FIXES and visible != people
    # Hands were asked for: a picture that shows a face or a whole figure is as wrong as one with nobody in it.
    slip = 'missing' if hands and wrong_people else 'face' if hands and face else None
    if slip:
        issues = [HANDS_FIXES[slip]] + issues[:5]
        note = (HANDS_NOTES[slip] + ('' if faithful else note))[:300]; wrong_people = True
    elif wrong_people:
        issues = [PEOPLE_FIXES[people]] + issues[:5]
        note = (PEOPLE_NOTES[people] + ('' if faithful else note))[:300]
    seen = {'expected': people, 'visible': visible, 'ok': not wrong_people}
    if hands:
        seen.update(framing='hands', face_visible=face)
    return {'status': 'passed' if faithful and not wrong_people else 'failed', 'flags': flags, 'issues': issues, 'note': note,
            'product_consistent': kept, 'people': seen}


PLACE_PROMPT = '''You check a photograph of an empty place that products will be shown in, for an online shop: a room, a workshop, a bench or table top, a vehicle. Every place must look brand new: as on the day it was fitted out.
Return data only:
- new: true when everything in the picture looks new and clean. False when any work surface, table, bench, shelf, wall, floor, vehicle interior or object looks used: worn, scratched, scored, dented, drilled, chipped, rusty, stained, oily, greasy, dusty or dirty, with peeling paint, or with rubbish or mess lying about. Judge the condition of the surfaces and things, not whether the place is a working one: tools, parts, boxes and supplies set out or hanging in their places are not wear, and neither are reflections, shadows, fine natural wood grain, knots or the even texture of concrete or plaster. False only for wear or dirt that can clearly be seen; true when unsure.
- issues: up to 3 short, concrete things in English that look used ("the bench top is scored and has drilled holes"). Empty when new is true.'''


def place_check(path):
    """Whether a picture of an empty place looks new: {'new': bool, 'issues': [...]}, or None when no vision model answered."""
    import ai_client as ai
    import ai_config
    value = ai_config.get_vision_config()
    if not all(value.get(key) for key in ('enabled', 'api_key', 'model')):
        return None
    schema = {'type': 'object', 'properties': {'new': {'type': 'boolean'},
                                               'issues': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                                                          'minItems': 0, 'maxItems': 3}},
              'required': ['new', 'issues'], 'additionalProperties': False}
    try:
        body = ai._payload(value, PLACE_PROMPT, {'picture': 'PLACE'}, schema, max_tokens=500)
        message = body['messages'][-1]
        if not isinstance(message.get('content'), list):
            raise ValueError('unexpected request shape')
        message['content'][:0] = [{'type': 'text', 'text': 'PLACE: the photograph to check.'}, ai._image(path, value['provider'], max_size=1536)]
        answer = ai._response(ai._post(value, body), value, ai.TOOL_NAME)
        ai._check_schema(answer, schema)
    except Exception:
        return None
    return {'new': bool(answer['new']), 'issues': [re.sub(r'\s+', ' ', item).strip()[:200] for item in answer['issues']][:3]}

def _lock_verdict(lock, box):
    """A failed check for a picture whose product the measurement says was not kept."""
    import scene_lock
    status = lock['status']
    places = scene_lock.describe(lock, box) if status == 'changed' else []
    issue = LOCK_FIXES[status] % ((' (around its %s)' % ', '.join(places[:3])) if places else '') if status == 'changed' else LOCK_FIXES[status]
    return {'status': 'failed', 'issues': [issue], 'note': LOCK_NOTES[status], 'product_consistent': False}


REJECTED_KEPT = 6               # sets of rejected attempts kept per project, newest first


def _set_aside(project, folder, attempts):
    """Copy a scene's rejected attempts, with what the check said of each, to scene_ai/rejected/ (the last few sets)."""
    try:
        place = DATA / project['id'] / 'scene_ai' / 'rejected'
        target = place / ('%s-%d' % (folder.name, int(time.time()))); target.mkdir(parents=True, exist_ok=True)
        for name in ('canvas.png', 'reference.png'):
            if (folder / name).is_file():
                shutil.copyfile(folder / name, target / name)
        for item in attempts:
            shutil.copyfile(item['file'], target / Path(item['file']).name)
        (target / 'checks.json').write_text(json.dumps([{'file': Path(item['file']).name, 'check': item['check'], 'prompt': item['prompt'][:6000]}
                                                         for item in attempts], ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        for old in sorted(place.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)[REJECTED_KEPT:]:
            shutil.rmtree(old, ignore_errors=True)
    except (OSError, KeyError, TypeError, ValueError):
        pass                    # keeping evidence must never be why a scene fails differently


def create(project, plan, azimuth, elevation, scene, product, size_mm, folder, cancelled=None, progress=None, people=None, framing=None,
           pose='auto', hint='', articulation=None, usage_rules=None, mounting=None):
    """Make the scene picture. Returns {'png', 'jpg', 'width', 'height', 'record'} with files inside ``folder``.

    ``people`` is True when a person must be in the picture and False when
    nobody may be; None leaves it to the scene description. ``framing``
    'hands' shows that person as hands at work, close up, with no face.
    ``pose`` 'auto' poses the product the way it is used in the scene
    (scene_pose); 'keep' shows it in the plan's own pose. ``articulation`` sets its moving parts
    (articulation.plan_for: pliers open, a caliper's jaws at the workpiece's thickness), with a 'label' saying how;
    ``usage_rules`` are the product's own rules for being used correctly, given to the image model and checked after;
    the written-down rules for its kind of tool (usage_rules.py: how pliers, a hammer, a saw are held and meet the work)
    come first, so the image model, the pose agent and the check all go by the same, concrete rules.
    """
    import usage_rules as canon
    import scene_lock
    import scene_pose
    hands = people is True and framing == 'hands'
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    usage_rules = canon.merged(canon.words_for(project, product), usage_rules) or None
    value = config()
    if not value:
        raise ImageError('图像模型未配置或已关闭，无法生成含人物或复杂环境的场景。')
    started = time.time()
    aspect = plan.get('aspect', '1:1'); size = size_for(aspect, value['model'])
    folder.mkdir(parents=True, exist_ok=True)
    # The pose it has in this moment of its use, worked out from renders of real poses; then rendered and locked like any other.
    hold = 'hands' if hands else 'person' if people else 'nobody' if people is False else 'scene'
    if articulation:
        # the moving parts set for this moment (opened, slid, pulled out): every candidate pose and the locked render show it
        plan = {**plan, 'articulation': {key: value for key, value in articulation.items() if key != 'label'}}
    # A product on a wall hangs as it is made to, front to the room: no other pose is looked for.
    posed = scene_pose.decide(project, _reference_plan(plan), azimuth, elevation, scene, product, hold, stop, say,
                              'keep' if mounting == 'wall' else pose if pose in scene_pose.MODES else 'auto', hint,
                              **({'usage': usage_rules} if usage_rules else {}))
    if stop(): raise InterruptedError()
    staged = product_render(project, {**plan, 'rotation': posed['rotation']}, azimuth, elevation, size_mm, size, stop, say)
    if stop(): raise InterruptedError()
    # The anchor: the product's own render in its final place; the mask keeps it, the model paints the rest.
    with Image.open(staged['path']) as cutout:
        canvas, layer, box = scene_lock.anchor(cutout, size, 'hands' if hands else 'person' if people else 'wall' if mounting == 'wall' else 'none',
                                               posed.get('work_side') if posed.get('changed') or posed.get('description') else None)
    canvas_path = folder / 'canvas.png'; canvas.save(canvas_path)
    reference = Image.new('RGB', size, (255, 255, 255)); reference.paste(layer, mask=layer.getchannel('A'))
    reference_path = folder / 'reference.png'; reference.save(reference_path)
    buffer = io.BytesIO(); scene_lock.mask(layer).save(buffer, 'PNG'); masked = buffer.getvalue()
    box_share = [round(box[0] / size[0], 4), round(box[1] / size[1], 4), round(box[2] / size[0], 4), round(box[3] / size[1], 4)]
    attempts = []; corrections = None; usage = {}; retry = ''; lowered = None
    number = 0; limit = MAX_ATTEMPTS
    while number < limit:
        number += 1
        if stop(): raise InterruptedError()
        say('图像模型正在生成场景' if number == 1 else retry)
        text = prompt(product, size_mm, scene, aspect, corrections, people, framing, posed, usage_rules,
                      (articulation or {}).get('label'), mounting)

        def call(value):
            nonlocal masked
            try:
                return edit(value, text, [canvas_path], size, stop, say, mask=masked)
            except RejectedError:
                if not masked:
                    raise
                # A service that takes no mask still gets the anchored canvas; the measurement below still applies.
                masked = None
                return edit(value, text, [canvas_path], size, stop, say)
        try:
            result = call(value)
        except GatewayError:
            # A gateway that cuts slow requests off still lets a quick, low-quality picture through.
            if value.get('quality', 'medium') == 'low':
                raise
            if stop(): raise InterruptedError()
            say('图像服务未能在「%s」质量下完成，改用 low 质量重试' % value.get('quality', 'medium'))
            lowered = value.get('quality', 'medium'); value = {**value, 'quality': 'low'}
            result = call(value)
        target = folder / ('attempt%d.png' % number)
        target.write_bytes(result['png'])
        for key, amount in result['usage'].items():
            usage[key] = usage.get(key, 0) + amount
        if stop(): raise InterruptedError()
        say('核对产品位置与外形')
        restored = None
        with Image.open(target) as picture:
            lock = scene_lock.inspect(layer, picture)
            closeup = None
            if lock['status'] == 'kept':
                # The image model's product is only its drawing of it: the render goes back in its place, so the size,
                # proportions and parts are the product's own. The model's original stays beside it as attempt*.model.png.
                put_back = scene_lock.restore(layer, picture, lock)
                if put_back is not None:                # None: the render does not lie cleanly on it; the painting stays
                    restored = folder / ('attempt%d.restored.png' % number); put_back.save(restored)
                else:
                    closeup = folder / ('closeup%d.jpg' % number)
                    scene_lock.side_by_side(layer, picture, lock).save(closeup, quality=90)
        if restored:
            # replace(), not rename(): a scene folder made again already holds these files, and rename() will not overwrite on Windows
            target.replace(folder / ('attempt%d.model.png' % number)); restored.replace(target)
            with Image.open(target) as picture:
                closeup = folder / ('closeup%d.jpg' % number)
                scene_lock.side_by_side(layer, picture, lock).save(closeup, quality=90)
        if lock['status'] != 'kept':
            verdict = _lock_verdict(lock, box_share)          # no need to ask a model about a product that is measurably not there
        else:
            say('核对产品是否与参考图一致' if people is None else '核对产品与画面中的手部' if hands else '核对产品与画面中的人物')
            verdict = check([reference_path], target, product, people, framing, closeup, size_mm,
                            **({'usage': usage_rules} if usage_rules else {}), **({'mounting': mounting} if mounting else {}))
        verdict['lock'] = lock
        attempts.append({'file': target, 'size': result['size'], 'check': verdict, 'prompt': text, 'closeup': closeup,
                         'original': folder / ('attempt%d.model.png' % number) if restored else target})
        if verdict['status'] != 'failed':
            break
        corrections = verdict['issues']
        wrong_people = verdict.get('people', {}).get('ok') is False
        wrong = {name for name, ok in (verdict.get('flags') or {}).items() if ok is False}
        retry = ('产品被移动或改动，正在重新生成' if lock['status'] != 'kept' else
                 '背景显得陈旧或脏乱，正在按崭新的环境重新生成' if wrong and wrong <= PLACE_FLAGS and not wrong_people else
                 '产品与参考图不一致，正在重新生成' if not wrong_people else
                 '画面里出现了人脸或整个人，正在重新生成手部特写' if hands and verdict['people'].get('visible') else
                 '画面里没有操作产品的手，正在重新生成' if hands else
                 '画面里没有人物，正在重新生成' if people else '画面里出现了人物，正在重新生成')
        if number == limit == MAX_ATTEMPTS and lock['status'] == 'kept' and not wrong_people and wrong and wrong <= USAGE_FLAGS | PLACE_FLAGS:
            limit += 1; retry = ('背景仍显陈旧或脏乱，按崭新的环境再试一次' if wrong <= PLACE_FLAGS else '握持、用法或摆放还不对，按核对意见再试一次')
    # A passing picture wins; otherwise one that kept the product, then got the people right, then the fewest differences, the later on a tie.
    chosen = next((item for item in attempts if item['check']['status'] != 'failed'), None)
    if chosen is None and not any(item['check']['lock']['status'] == 'kept' for item in attempts):
        # every picture moved or redrew the product: none of them shows this product, so none is kept. The attempts are
        # set aside (the listing's scene folder is cleared when it holds no scene), so what went wrong can be looked at.
        _set_aside(project, folder, attempts)
        raise ImageError('图像模型每次都改动了产品的位置、大小或外形（%s），这张场景图没有采用。'
                         % LOCK_NOTES.get(attempts[-1]['check']['lock']['status'], '')[:60].rstrip('。'))
    if chosen is None:
        # A picture whose only fault is the place beats one whose product is wrong.
        chosen = min(reversed(attempts), key=lambda item: (item['check']['lock']['status'] != 'kept',
                                                           item['check'].get('people', {}).get('ok') is False,
                                                           item['check'].get('product_consistent') is False, len(item['check']['issues'])))
    stem = folder.name
    png = folder / (stem + '.png'); jpg = folder / (stem + '.jpg')
    png.write_bytes(chosen['file'].read_bytes())       # with the product put back; the model's own file (and its provenance) is 'original'
    with Image.open(png) as picture:
        picture.convert('RGB').save(jpg, quality=95, subsampling=0)
    width, height = chosen['size']
    def url(path): return '/files/' + Path(path).relative_to(DATA).as_posix()
    import components
    record = {'model': value['model'], 'quality': value.get('quality', 'medium'), 'size': [width, height], 'attempts': len(attempts),
              'restored': chosen['original'] != chosen['file'], 'mounting': mounting, 'original': url(chosen['original']),
              'usage_retry': USAGE_RETRY,
              'check': chosen['check'], 'people': people, 'framing': framing if people is True else None, 'prompt': chosen['prompt'][:6000], 'usage': usage,
              'seconds': round(time.time() - started, 1),
              'references': [{'url': url(staged['path']), 'azimuth': staged.get('azimuth', azimuth), 'elevation': staged.get('elevation', elevation)}],
              'pose': posed,
              'anchor': {'version': scene_lock.VERSION, 'box': box_share, 'mask': bool(masked), 'canvas_url': url(canvas_path),
                         **({'closeup_url': url(chosen['closeup'])} if chosen.get('closeup') else {})},
              # The quality that was asked for, when the service only completed the picture at a lower one.
              **({'quality_lowered_from': lowered} if lowered else {}),
              'discarded': [{'url': url(item['file']), 'issues': item['check']['issues'], 'lock': item['check']['lock']['status']}
                            for item in attempts if item is not chosen]}
    return {'png': png, 'jpg': jpg, 'width': width, 'height': height, 'record': record}

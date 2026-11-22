"""Backdrops: places a product is set down in, photographed once and used for every product.

A scene with nobody in it, the product standing where it is kept or used, does not need an image model to draw the
product. Asked to, the image model draws it again, and then decides its size and what it stands on as well: a floor
cabinet came out standing on a workbench, a third of its real size. Here the product's own model is rendered into a
photograph of the empty place (blender/backdrop.py): its shape, its moving parts and its size are the model's, it
stands on the measured floor or bench top, and the place lights it and takes its shadow.

    spec_for()   the backdrop a scene asks for: the buyer's place, what the product stands on, how large it is
    ensure()     that backdrop, made when it is missing: one picture of the empty place from the image model, measured
                 once by a depth model (scripts/backdrop_measure.py, in its own Python) and checked; kept for good
    create()     the product in the backdrop: what scene_imagegen.create() returns, or raises Unavailable when this
                 scene has to be painted after all (an outdoor place, no measuring set up, no free spot at a fair size)

Once a backdrop exists, a product is set into it without any AI request: one Blender run of about half a minute.
The depth model is needed only to add a backdrop. It lives outside the app's environment (.venv-depth) and the app
runs without it: scenes are then painted by the image model as before.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

import engine
from studio_store import DATA, ROOT, atomic_json

VERSION = 1
LIBRARY = DATA / 'backdrops'
FLOOR_FROM_M = .6             # a product longer than this stands on the floor; a smaller one on the place's table or bench
SIZES = (('small', .08, .3), ('medium', .3, .6), ('large', .6, 1.8))
SHARE = .5                    # the product's longer side in the frame (scene_lock.PLACING for a picture without people)
SHARE_RANGE = (.3, .64)       # what is still a fair size when the free spot does not allow that
MAX_TURN = 40                 # degrees: the product is seen from its front quarter
SAMPLES = 256
RENDER_TIMEOUT = 900
MEASURE_TIMEOUT = 1500
MAX_REJECTED = 2              # pictures of one place that may fail their measurement before the place is given up
DEPTH_MODEL = 'models--apple--DepthPro-hf'

# The empty place of each buyer's scene (scene_audience.SEGMENTS, their 'place'), indoors only: a garden or a street is
# lit by the sun, which the light model of blender/backdrop.py does not find, and grass is not a plane to stand on.
# 'room' is the place seen across its floor; 'behind' what is seen behind its table or bench, when that is not the same.
PLACES = {
    'home_starter': {'room': 'a bright, tidy living room at home, a sofa and a bookshelf far behind',
                     'surface': 'a plain light wooden table', 'floor': 'a light wooden floor'},
    'diy_project': {'room': 'a home garage during a home improvement project, timber boards leaning at one side and a half-finished wooden frame far behind',
                    'surface': 'a sturdy plywood work table', 'floor': 'a smooth sealed concrete floor'},
    'workshop_hobbyist': {'room': 'a well-kept private garage workshop, hand tools on a pegboard on the wall and a wooden workbench against the wall at one side',
                          'behind': 'the wall of a well-kept private workshop, hand tools hanging on a pegboard',
                          'surface': 'a solid wooden workbench', 'floor': 'a smooth, light grey sealed concrete floor'},
    'site_team': {'room': 'a small renovation jobsite, a bare plastered room with a stepladder and folded dust sheets at one side',
                  'surface': 'a clean plywood board laid across two trestles', 'floor': 'a swept bare screed floor'},
    'industrial_maintenance': {'room': 'a clean industrial plant room with pipework and grey control cabinets along the walls',
                               'surface': 'a stainless steel work table', 'floor': 'a grey epoxy-painted floor'},
}
# How the place is photographed for each size of product. The image model does not keep to distances it is told, so what
# a picture is framed for is measured afterwards (FRAMED) and the picture filed under that size.
CAMERAS = {
    ('floor', 'large'): {'camera': 'The camera stands at eye height, about 1.4 metres above the floor, looking slightly down, 35 mm lens, level horizon.',
                         'clear': 'two metres'},
    ('surface', 'medium'): {'camera': 'The camera is about 60 centimetres above the table top and one metre back from its clear middle, '
                                      'looking down at about 30 degrees, 50 mm lens, level horizon.',
                            'clear': '80 centimetres'},
    ('surface', 'small'): {'camera': 'A close-up, as for a small hand tool lying there: the camera is only about 30 centimetres above the table top and '
                                     '40 centimetres back from its clear middle, looking down at about 35 degrees, 50 mm lens, level horizon. The wood '
                                     'of the table top is large and sharp in the foreground.',
                           'clear': '40 centimetres'},
}
HEIGHT_M = {'floor': (.9, 2.0), 'surface': (.12, 1.1)}       # the camera above the plane: a floor view is not a bench view
# The product size (metres) a picture is framed for: one that fills SHARE of the frame where the free plane is nearest.
# Each size takes pictures framed for about its own products; blender/backdrop.py enlarges a part of the frame for the rest.
FRAMED = {'small': (.12, .34), 'medium': (.3, .8), 'large': (.7, 1.9)}
FLOOR_PROMPT = ('Photograph of an empty corner of {room}, no people. {camera} The lower half of the picture is {plane}, completely empty in the '
                'middle: nothing stands or lies on it, a clear open area at least {clear} wide. Soft, even daylight from large windows behind the '
                'camera and ceiling lights, gentle soft shadows, no hard sun patches on the floor. Natural colours, realistic, sharp, '
                'product-catalogue photography background. No text, no logos, no people, no vehicles, nothing in the centre of the floor.')
SURFACE_PROMPT = ('Photograph of the top of {plane}, seen from its long side, no people. {camera} The lower half of the picture is the table top, '
                  'completely empty in the middle: nothing stands or lies on it, a clear open area at least {clear} wide. Behind it, slightly out '
                  'of focus, {room}. Soft, even daylight from large windows behind the camera and ceiling lights, gentle soft shadows, no hard sun '
                  'patches on the table top. Natural colours, realistic, product-catalogue photography background. No text, no logos, no people, '
                  'nothing in the centre of the table top.')

_LOCKS = {}; _GUARD = threading.Lock()


class Unavailable(Exception):
    """This scene cannot be set into a backdrop; the message says why. The image model paints it instead."""


def enabled():
    return os.getenv('SCENE_BACKDROPS', '1').strip() != '0' and bool(engine.blender_path()) and (ROOT / 'blender' / 'backdrop.py').is_file()


def depth_python():
    """The Python that has the depth model's libraries, or None."""
    named = os.getenv('BACKDROP_PYTHON')
    for path in ([Path(named)] if named else []) + [ROOT / '.venv-depth' / 'bin' / 'python', ROOT / '.venv-depth' / 'Scripts' / 'python.exe']:
        if path.is_file():
            return path
    return None


def models_folder():
    return Path(os.getenv('BACKDROP_MODELS') or DATA / 'models' / 'hf')


def can_measure():
    """Whether a new backdrop can be measured: the depth environment, its script and the model's weights are there."""
    return bool(depth_python()) and (ROOT / 'scripts' / 'backdrop_measure.py').is_file() and (models_folder() / 'hub' / DEPTH_MODEL).is_dir()


def size_class(longest_m):
    return next((name for name, low, high in SIZES if low <= longest_m < high), None)


def _spec(segment_id, stand, size):
    place = PLACES.get(segment_id); camera = CAMERAS.get((stand, size))
    if not place or not camera:
        return None
    template = FLOOR_PROMPT if stand == 'floor' else SURFACE_PROMPT
    return {'id': '%s-%s-%s' % (segment_id, stand, size), 'segment': segment_id, 'stand': stand, 'size': size,
            'prompt': template.format(room=place['room'] if stand == 'floor' else place.get('behind', place['room']), plane=place[stand],
                                      camera=camera['camera'], clear=camera['clear'])}


def spec_for(segment_id, longest_m):
    """The backdrop for a buyer's place and a product of this size, or None when there is none to be had."""
    longest_m = float(longest_m or 0); size = size_class(longest_m)
    return _spec(segment_id, 'floor' if longest_m >= FLOOR_FROM_M else 'surface', size) if size else None


def siblings(spec):
    """The same place and stand photographed for the other sizes: one of them may have room for this product too."""
    return [other for other in (_spec(spec['segment'], spec['stand'], name) for name, _, _ in SIZES) if other and other['id'] != spec['id']]


def framed_for(geometry):
    """The product size (metres) the picture is framed for: what fills SHARE of it standing where the free plane is nearest."""
    try:
        return SHARE * geometry['size'][0] * float(geometry['near_m']) / float(geometry['focal_px'])
    except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError):
        return 0.0


def flaw(geometry, stand):
    """Why a measured picture cannot serve as a backdrop of this stand at all, or None. The picture is the image model's: checked, not trusted."""
    low, high = HEIGHT_M[stand]
    if not isinstance(geometry, dict) or geometry.get('version') != VERSION:
        return 'not measured'
    if geometry.get('support_share', 0) < .12:
        return 'too little free %s in the picture' % stand
    if abs(geometry.get('roll_deg', 0)) > 4:
        return 'the camera is tilted sideways'
    if not 4 <= geometry.get('tilt_down_deg', 0) <= 70:
        return 'the camera does not look down at the %s' % stand
    if not low <= geometry.get('height_m', 0) <= high:
        return 'the camera is %.2f m above the %s, %.2f to %.2f m was asked' % (geometry.get('height_m', 0), stand, low, high)
    if not 25 <= geometry.get('fov_deg', 0) <= 100:
        return 'the lens could not be measured'
    return None


def suits(geometry, stand, asked=None):
    """The size a measured picture is framed for (the asked one when it is among them), or None."""
    size = framed_for(geometry)
    fitting = [name for name, (low, high) in FRAMED.items() if low <= size <= high and (stand, name) in CAMERAS]
    return asked if asked in fitting else (fitting[0] if fitting else None)


def gate(geometry, spec):
    """Why a measured picture is not this backdrop, or None."""
    reason = flaw(geometry, spec['stand'])
    if reason:
        return reason
    low, high = FRAMED[spec['size']]
    if not low <= framed_for(geometry) <= high:
        return 'the picture is framed for a product of %.2f m, %.2f to %.2f m was asked' % (framed_for(geometry), low, high)
    return None


def ready(spec):
    """The backdrop's folder when it is in the library and passes its checks."""
    folder = LIBRARY / spec['id']
    try:
        geometry = json.loads((folder / 'geometry.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None
    if gate(geometry, spec) or not all((folder / name).is_file() for name in ('backdrop.png', 'support.png', 'depth.npy')):
        return None
    return folder


def _rejected(spec):
    try:
        value = json.loads((LIBRARY / (spec['id'] + '.rejected.json')).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) and value.get('version') == VERSION else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _paint(prompt, stop, say):
    """One picture from the image model's text-to-image endpoint: PNG bytes."""
    import ai_client
    import scene_imagegen as gen
    from ai_model_policy import assert_model_allowed
    value = gen.config()
    if not value:
        raise Unavailable('图像模型未配置，无法生成背景图')
    assert_model_allowed(value)
    size = gen.size_for('1:1', value['model'])
    url = gen.endpoint(value)[:-len('/images/edits')] + '/images/generations'
    body = json.dumps({'model': value['model'], 'prompt': prompt, 'size': '%dx%d' % tuple(size), 'quality': value.get('quality', 'medium'),
                       'stream': True, 'partial_images': 2}).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json',
                                              'Authorization': 'Bearer ' + value['api_key']})
    try:
        with urllib.request.build_opener(ai_client.NoRedirect()).open(request, timeout=gen.READ_TIMEOUT) as response:
            if 'text/event-stream' in (response.headers.get('Content-Type') or ''):
                encoded, usage = gen._read_stream(response, stop, say, time.time() + gen.TOTAL_TIMEOUT)
            else:
                answer = json.loads(response.read(gen.MAX_STREAM + 1).decode('utf-8'))
                encoded = answer['data'][0]['b64_json']; usage = gen._usage(answer.get('usage'))
    except InterruptedError:
        raise
    except urllib.error.HTTPError as error:
        message = gen._http_message(error); error.close()
        raise Unavailable('背景图没有生成：' + message) from None
    except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError, OSError, ValueError, KeyError, IndexError, TypeError):
        raise Unavailable('背景图没有生成：图像服务没有返回图片') from None
    content, _ = gen._decode(encoded)
    ai_client.log_usage({'model': str(value['model'])[:120], 'caller': 'scene_backdrop.paint', 'usage': usage or {}, 'n_images': 0})
    return content


def _run(command, folder, log, stop, timeout, env=None):
    """A helper process to its end: its exit code. Stopped when the job is cancelled or takes too long."""
    started = time.time()
    with (folder / log).open('w', encoding='utf-8') as handle:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while process.poll() is None:
            if stop():
                process.terminate(); raise InterruptedError()
            if time.time() - started > timeout:
                process.kill(); return None
            time.sleep(.3)
    return process.returncode


def measure(folder, stop=lambda: False):
    """Measure folder/backdrop.png with the depth model, in its own Python: the geometry, or None when that did not work."""
    python = depth_python()
    if not python:
        return None
    env = {**os.environ, 'HF_HOME': str(models_folder()), 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_VERBOSITY': 'error'}
    code = _run([str(python), '-I', str(ROOT / 'scripts' / 'backdrop_measure.py'), str(folder)], folder, 'measure.log', stop, MEASURE_TIMEOUT, env)
    if code != 0:
        return None
    try:
        return json.loads((folder / 'geometry.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None


def ensure(spec, cancelled=None, progress=None):
    """The backdrop asked for, made now when the library does not have it: (folder, spec). The image model frames a picture
    as it likes, so a new one is filed under the size it turned out to be framed for; the spec returned is that one, and may
    be the place's backdrop for another size. Raises Unavailable when no usable picture came of it."""
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    with _GUARD:
        lock = _LOCKS.setdefault('%s-%s' % (spec['segment'], spec['stand']), threading.Lock())
    with lock:                                      # two listings asking for the same new place: it is made once
        folder = ready(spec)
        if folder:
            return folder, spec
        rejected = _rejected(spec)
        if rejected.get('count', 0) >= MAX_REJECTED:
            raise Unavailable('这个场所的背景图 %d 次都没有通过测量（%s）' % (rejected['count'], rejected.get('reason', '')))
        if not can_measure():
            raise Unavailable('没有安装测量背景图用的深度模型环境')
        say('生成空场景的背景图（每个场所只做一次，之后所有产品共用）')
        content = _paint(spec['prompt'], stop, say)
        LIBRARY.mkdir(parents=True, exist_ok=True)
        work = LIBRARY / ('.%s-%s' % (spec['id'], uuid.uuid4().hex[:8])); work.mkdir()
        try:
            (work / 'backdrop.png').write_bytes(content)
            engine.verify_image(work / 'backdrop.png', 'PNG')
            if stop(): raise InterruptedError()
            say('测量背景图的地面、相机和灯（每个场所只做一次，需要几分钟）')
            geometry = measure(work, stop)
            reason = flaw(geometry, spec['stand']) if geometry else 'the depth model did not finish'
            filed = None
            if not reason:
                size = suits(geometry, spec['stand'], spec['size'])
                filed = _spec(spec['segment'], spec['stand'], size) if size else None
                if not filed:
                    reason = 'the picture is framed for a product of %.2f m: no size of this place uses that' % framed_for(geometry)
                elif filed['id'] != spec['id'] and ready(filed):
                    reason = 'the picture is framed like the one the place already has for %s products' % filed['size']; filed = None
            if reason or filed['id'] != spec['id']:
                # not the picture that was asked for: the asked size has used one of its tries
                count = rejected.get('count', 0) + 1; why = reason or 'the picture is framed for %s products and was kept for those' % filed['size']
                shutil.copyfile(work / 'backdrop.png', LIBRARY / ('%s.rejected-%d.png' % (spec['id'], count)))
                atomic_json(LIBRARY / (spec['id'] + '.rejected.json'), {'version': VERSION, 'count': count, 'reason': why, 'at': time.time()})
            if not filed:
                raise Unavailable('新生成的背景图没有通过测量（%s）' % reason)
            atomic_json(work / 'backdrop.json', {'version': VERSION, **{key: filed[key] for key in ('id', 'segment', 'stand', 'size')},
                                                 'prompt': spec['prompt'], 'framed_for_m': round(framed_for(geometry), 3), 'made_at': time.time()})
            target = LIBRARY / filed['id']
            if target.exists():
                shutil.rmtree(target)
            work.rename(target)
            return target, filed
        finally:
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)


def render(request, folder, stop=lambda: False):
    """blender/backdrop.py on one request: its result (a dict), or None when Blender did not report one."""
    folder.mkdir(parents=True, exist_ok=True)
    request = {**request, 'output_dir': str(folder)}
    atomic_json(folder / 'request.json', request)
    (folder / 'result.json').unlink(missing_ok=True)
    command = [engine.blender_path(), '-b', '--factory-startup', '--disable-autoexec', '-t', '8', '--python-exit-code', '1',
               '--python', str(ROOT / 'blender' / 'backdrop.py'), '--', str(folder / 'request.json')]
    try:
        _run(command, folder, 'blender.log', stop, RENDER_TIMEOUT)
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    except InterruptedError:
        raise
    except (OSError, ValueError, UnicodeError):
        return None
    return result if isinstance(result, dict) else None


def create(project, plan, proposal, azimuth, size_mm, folder, cancelled=None, progress=None):
    """The product set down in the backdrop its scene asks for.

    Returns {'png', 'jpg', 'width', 'height', 'record'} like scene_imagegen.create(); the record says 'model': 'composite'.
    Raises Unavailable when the scene cannot be made this way.
    """
    import scene_imagegen
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    if not enabled():
        raise Unavailable('合成场景已关闭或渲染组件未就绪')
    if not size_mm or size_mm <= 0:
        raise Unavailable('不知道产品的实际尺寸')
    spec = spec_for(proposal.get('segment_id'), size_mm / 1000)
    if not spec:
        raise Unavailable('这个场所或这个尺寸的产品没有可用的背景图')
    started = time.time()
    clean = scene_imagegen._reference_plan(plan)
    if proposal.get('articulation'):
        clean['articulation'] = {key: value for key, value in proposal['articulation'].items() if key != 'label'}
    png = folder / (folder.name + '.png')

    def set_into(candidate, backdrop):
        """The product in one backdrop: Blender's result when it found a place for it, None when it has no room there."""
        say('把产品模型渲染进背景图（不经过图像模型）')
        result = render({'model_path': str(project['model_path']), 'plan': clean, 'longest_m': size_mm / 1000, 'backdrop': str(backdrop),
                         'azimuth': max(-MAX_TURN, min(MAX_TURN, float(azimuth))), 'share': SHARE, 'share_range': list(SHARE_RANGE),
                         'stand': candidate['stand'], 'samples': SAMPLES}, folder, stop)
        if result and result.get('success') is True and png.is_file():
            return result
        if result and result.get('reason') == 'no_fit':
            return None
        raise Unavailable('产品没有能渲染进背景图')

    # The backdrop framed for this size; else one the place already has for another size, in a part of which the product
    # may still be shown large enough; a new picture is paid for only when none of those has room for it.
    own = ready(spec); result = None; tried = set()
    for candidate in ([spec] if own else []) + [other for other in siblings(spec) if ready(other)]:
        tried.add(candidate['id']); result = set_into(candidate, ready(candidate))
        if stop(): raise InterruptedError()
        if result:
            spec = candidate; break
    if not result and not own:
        backdrop, filed = ensure(spec, stop, say)
        if filed['id'] not in tried:
            result = set_into(filed, backdrop)
            if result: spec = filed
    if not result:
        raise Unavailable('背景图里没有能按合适大小放下产品的空位')
    width, height = engine.verify_image(png, 'PNG')
    jpg = folder / (folder.name + '.jpg')
    with Image.open(png) as picture:
        picture.convert('RGB').save(jpg, quality=95, subsampling=0)
    # The product is the model's own render at its real size on a measured plane: what the check asks of a painted
    # scene holds by construction, and the vision model is not asked (it reported differences between two copies of
    # the same render when it was).
    check = {'status': 'passed', 'issues': [], 'how': 'composite', 'flags': {name: True for name in scene_imagegen.FIDELITY_FLAGS},
             'note': '产品由模型渲染后合成进背景图：外形、零件和尺寸来自模型本身，没有经过图像模型重画。'}
    record = {'model': 'composite', 'quality': 'render', 'size': [width, height], 'attempts': 1, 'usage_retry': scene_imagegen.USAGE_RETRY,
              'check': check, 'people': False, 'framing': None, 'prompt': '', 'usage': {}, 'seconds': round(time.time() - started, 1),
              'references': [], 'pose': {'mode': 'keep', 'source': 'plan', 'changed': False},
              'anchor': {'version': VERSION, 'box': result['box'], 'mask': False},
              'backdrop': {'id': spec['id'], 'segment': spec['segment'], 'stand': spec['stand'], 'share': result.get('share'),
                           'distance_m': result.get('distance_m'), 'yaw_deg': result.get('yaw_deg'), 'zoom': result.get('zoom'),
                           'light': result.get('light')},
              'discarded': []}
    return {'png': png, 'jpg': jpg, 'width': width, 'height': height, 'record': record}

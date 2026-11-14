"""Usage scenes that need people or a real place, made by an image model.

A scene with an operator, a jobsite or a garden cannot be assembled from a
floor, a wall and a few props. For those the product is rendered in Blender
from a few angles, exactly as it is, and the renders go to an image model with
the description of the scene. The renders are the only pictures the model
receives: they pin down the product's shape, parts and colours, so the model
places this product in the scene instead of inventing a similar one. The
result is then compared with the renders by the vision model, and a picture
that changed the product is made once more with the differences named.

    references()   the product from the target angle and two others (cached)
    prompt()       the request: what to keep exactly, which scene to build
    edit()         one call to the Images API, streamed so slow gateways hold
    check()        does the product in the picture still match the renders
    create()       all of the above; returns the image and its record

No third-party picture is ever sent: the reference pack contributes words
only (see scene_audience.py), and publisher names are removed from them.
"""
import base64
import copy
import hashlib
import io
import json
import re
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
from studio_store import DATA, ROOT, atomic_json

REFERENCE_VERSION = 1          # part of the cache key: raise it when reference renders change
REFERENCE_QUALITY = 'reference'
REFERENCE_TIMEOUT = 900
MAX_ATTEMPTS = 2               # the first picture, and one correction when the product changed
READ_TIMEOUT = 150             # seconds without a byte
TOTAL_TIMEOUT = 480
MAX_STREAM = 96 * 1024 * 1024
MAX_IMAGE = 40 * 1024 * 1024
SIZES = {'1:1': (2048, 2048), '4:3': (2048, 1536), '3:4': (1536, 2048)}
SIZES_1K = {'1:1': (1024, 1024), '4:3': (1536, 1024), '3:4': (1024, 1536)}

FIDELITY_PROMPT = '''You check a generated photograph against renders of the real product. The REFERENCE images are renders of the product's 3D model from several sides: they are the truth about its shape, parts, colours and materials. GENERATED is a photograph made by an image model; it should show this same product in a scene.
Compare the product in GENERATED with the references. Return data only:
- product_present: the product is in the picture, once, and recognisable.
- shape_consistent: its overall shape and proportions match, allowing for perspective and for a drawer, door or lid shown open.
- parts_consistent: every distinct part visible from this side is there, in the same number and place (handles, drawers, doors, wheels, buttons, locks, feet); nothing was invented or dropped.
- colours_consistent: the colours and finishes of the body and of each part match, allowing for the light of the scene.
- no_added_text: nothing is written or printed on the product that the references do not show, and the scene has no logo, sign or caption.
- issues: up to 5 short, concrete differences in English, each naming the part and what is wrong ("top surface is black, should be light brushed steel"). Empty when there are none.
- note: one sentence in the language of note_language giving the verdict.
Judge only what can be seen. A side that faces away is not a difference, and a part hidden behind a person or an object is not missing.'''


class ImageError(ValueError):
    """A generation that did not produce a picture; the message is safe to show."""


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


def angles(azimuth, elevation):
    """The target view, the product's other front corner, and a look down on it."""
    other = -azimuth if abs(azimuth) >= 20 else azimuth + 70
    return [(round(float(azimuth), 1), round(float(elevation), 1)), (round(float(other), 1), round(float(elevation), 1)),
            (round(azimuth * .5, 1), round(min(62.0, elevation + 30), 1))]


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


def prompt(product, size_mm, count, scene, aspect='1:1', corrections=None):
    """The words sent with the renders. ``scene`` is the only free text in it."""
    scene = scene_audience.scrub(re.sub(r'\s+', ' ', str(scene or '')).strip())[:1500]
    product = re.sub(r'\s+', ' ', str(product or 'product')).strip()[:120]
    size = ''
    if size_mm:
        size = ' Its largest dimension is about %s.' % ('%d cm' % round(size_mm / 10) if size_mm >= 100 else '%d mm' % round(size_mm))
    views = ('The attached image is a render of it.' if count == 1 else
             'The attached %d images are renders of it from different sides: image 1 is the side to show it from, '
             'the others show its other sides and its top.' % count)
    lines = [
        'Create one photorealistic photograph for an online shop.',
        'THE PRODUCT is a %s.%s %s Reproduce this product exactly as rendered: the same shape and proportions, the same number '
        'and position of every part, the same colours and materials. Do not redesign, simplify, add or remove anything on it, and '
        'do not put any logo or lettering on it. Show one single product, complete and clearly recognisable, seen from roughly the '
        'side of image 1.' % (product, size, views),
        'THE SCENE. ' + (scene or 'A believable place where this product is used, with a person using it.'),
        'RULES. Natural, believable light. Correct scale between the product, people and surroundings. No brand names, logos, '
        'signs, captions or other text anywhere in the picture, and no watermark. People are ordinary adults in plain clothes '
        'suited to the work, shown naturally. Composition for a %s frame.' % {'1:1': 'square', '4:3': 'landscape 4:3', '3:4': 'portrait 3:4'}.get(aspect, 'square'),
    ]
    if corrections:
        fixes = '; '.join(re.sub(r'\s+', ' ', str(item)).strip()[:200] for item in corrections[:5] if str(item).strip())
        if fixes:
            lines.append('CORRECTIONS. An earlier attempt changed the product. Compared with the renders it got these wrong, fix them: ' + fixes + '.')
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


def edit(value, text, images, size, cancelled=None, progress=None):
    """One generation: the renders in, a PNG out. Returns {'png', 'size', 'usage'}."""
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
        message = _http_message(error); error.close()
        raise ImageError(message) from None
    except (socket.timeout, TimeoutError):
        raise ImageError('图像生成超时，请重试。') from None
    except urllib.error.URLError:
        raise ImageError('无法连接图像服务，请检查网络、证书和接口地址。') from None
    except (ConnectionError, OSError):
        raise ImageError('图像服务的连接中断，请重试。') from None
    if not isinstance(encoded, str):
        raise ImageError('图像服务返回了无效响应，请检查图像接口配置。')
    content, actual = _decode(encoded)
    return {'png': content, 'size': list(actual), 'usage': usage}


def fidelity_schema():
    flags = ('product_present', 'shape_consistent', 'parts_consistent', 'colours_consistent', 'no_added_text')
    properties = {name: {'type': 'boolean'} for name in flags}
    properties['issues'] = {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}, 'minItems': 0, 'maxItems': 5}
    properties['note'] = {'type': 'string', 'minLength': 1, 'maxLength': 300}
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def check(reference_paths, generated_path, product):
    """Compare the generated product with the renders.

    Returns {'status': 'passed' | 'failed' | 'unchecked', 'issues', 'note', ...}.
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
        facts = {'product': str(product)[:120], 'reference_count': len(reference_paths), 'note_language': 'zh-CN'}
        body = ai._payload(value, FIDELITY_PROMPT, facts, schema, max_tokens=1500)
        blocks = []
        for index, path in enumerate(reference_paths, 1):
            blocks += [{'type': 'text', 'text': 'REFERENCE %d: render of the real product.' % index}, ai._image(path, value['provider'])]
        blocks += [{'type': 'text', 'text': 'GENERATED: the photograph to check.'}, ai._image(generated_path, value['provider'], max_size=1536)]
        message = body['messages'][-1]
        if not isinstance(message.get('content'), list):
            raise ValueError('unexpected request shape')
        message['content'][:0] = blocks
        answer = ai._response(ai._post(value, body), value, ai.TOOL_NAME)
        ai._check_schema(answer, schema)
    except Exception:                # the picture exists; a check that could not run must not lose it
        return {'status': 'unchecked', 'issues': [], 'note': '视觉模型未能完成核对，请对照参考图查看产品是否变形。'}
    flags = {name: answer[name] for name in schema['properties'] if name not in ('issues', 'note')}
    passed = all(flags.values())
    issues = [] if passed else [re.sub(r'\s+', ' ', item).strip()[:200] for item in answer['issues']]
    return {'status': 'passed' if passed else 'failed', 'flags': flags, 'issues': issues, 'note': re.sub(r'\s+', ' ', answer['note']).strip()[:300]}


def create(project, plan, azimuth, elevation, scene, product, size_mm, folder, cancelled=None, progress=None):
    """Make the scene picture. Returns {'png', 'jpg', 'width', 'height', 'record'} with files inside ``folder``."""
    stop = cancelled or (lambda: False); say = progress or (lambda stage: None)
    value = config()
    if not value:
        raise ImageError('图像模型未配置或已关闭，无法生成含人物或复杂环境的场景。')
    started = time.time()
    rows = references(project, plan, azimuth, elevation, stop, say)
    paths = [row['path'] for row in rows]
    aspect = plan.get('aspect', '1:1'); size = size_for(aspect, value['model'])
    folder.mkdir(parents=True, exist_ok=True)
    attempts = []; corrections = None; usage = {}
    for number in range(1, MAX_ATTEMPTS + 1):
        if stop(): raise InterruptedError()
        say('图像模型正在生成场景' if number == 1 else '产品与参考图不一致，正在重新生成')
        text = prompt(product, size_mm, len(paths), scene, aspect, corrections)
        result = edit(value, text, paths, size, stop, say)
        target = folder / ('attempt%d.png' % number)
        target.write_bytes(result['png'])
        for key, amount in result['usage'].items():
            usage[key] = usage.get(key, 0) + amount
        if stop(): raise InterruptedError()
        say('核对产品是否与参考图一致')
        verdict = check(paths, target, product)
        attempts.append({'file': target, 'size': result['size'], 'check': verdict, 'prompt': text})
        if verdict['status'] != 'failed':
            break
        corrections = verdict['issues']
    # A passing picture wins; otherwise the one with the fewest differences, the later on a tie.
    chosen = next((item for item in attempts if item['check']['status'] != 'failed'), None)
    if chosen is None:
        chosen = min(reversed(attempts), key=lambda item: len(item['check']['issues']))
    stem = folder.name
    png = folder / (stem + '.png'); jpg = folder / (stem + '.jpg')
    png.write_bytes(chosen['file'].read_bytes())       # untouched: the model's own provenance metadata stays in the file
    with Image.open(png) as picture:
        picture.convert('RGB').save(jpg, quality=95, subsampling=0)
    width, height = chosen['size']
    def url(path): return '/files/' + Path(path).relative_to(DATA).as_posix()
    record = {'model': value['model'], 'quality': value.get('quality', 'medium'), 'size': [width, height], 'attempts': len(attempts),
              'check': chosen['check'], 'prompt': chosen['prompt'][:6000], 'usage': usage, 'seconds': round(time.time() - started, 1),
              'references': [{'url': url(row['path']), 'azimuth': row['azimuth'], 'elevation': row['elevation']} for row in rows],
              'discarded': [{'url': url(item['file']), 'issues': item['check']['issues']} for item in attempts if item is not chosen]}
    return {'png': png, 'jpg': jpg, 'width': width, 'height': height, 'record': record}

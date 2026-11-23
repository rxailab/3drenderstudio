"""Components of a product: the other things in the box, each with its own model or picture.

A project is one product model. A listing shows more than that: the battery, the charger, the
case. Each of those is a component. A model is analysed, stood the right way up, given finishes
and rendered to a transparent cut-out exactly as the product is; a picture is cut out of its
plain background. Every component carries what a kit page prints under it: a name, a model
number, a quantity and a line of description.

This module also owns the staged picture (``blender/stage.py``), which listing pages use for
the product itself: one render per distinct request, kept and reused.
"""
import copy
import hashlib
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import blender_runner
from studio_store import ROOT, DATA, LOCK, get, put, atomic_json

STAGE_SCRIPT = ROOT / 'blender' / 'stage.py'
WORKER_SCRIPT = ROOT / 'blender' / 'worker.py'
STAGE_TIMEOUT = 1800
STAGE_LOCK = threading.Lock()                 # one staged render at a time
MODEL_SUFFIXES = ('.glb', '.obj')
PICTURE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp')
MAX_COMPONENTS = 24
MAX_PICTURE_BYTES = 40 * 1024 * 1024
CUTOUT_PX = 1400
ROLES = {'battery': ('电池', 'Battery'), 'charger': ('充电器', 'Charger'), 'case': ('工具箱', 'Case'), 'bag': ('工具包', 'Bag'),
         'blade': ('刀片 / 磨片', 'Blade'), 'bit': ('批头 / 钻头', 'Bits'), 'attachment': ('附件', 'Attachment'),
         'manual': ('说明书', 'Manual'), 'other': ('其他', 'Item')}
ROLE_WORDS = (('battery', ('battery', 'pack', '电池')), ('charger', ('charger', '充电')), ('case', ('case', 'box', '箱', '盒')),
              ('bag', ('bag', '包')), ('blade', ('blade', 'disc', 'wheel', '刀片', '磨片', '锯片')), ('bit', ('bit', 'drill', '批头', '钻头')),
              ('manual', ('manual', '说明书')))
STAGES = {'queued': 0, 'analyse': 8, 'pose': 30, 'render': 60, 'cutout': 92, 'ready': 100}
QUEUE = queue.Queue()
PROCESSES = {}
_WORKER = None
_DIGESTS = {}


class ComponentCancelled(Exception):
    pass


# ---- text and numbers ----

def _text(value, limit, label):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(label + '必须是文字')
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value).strip()
    if len(value) > limit:
        raise ValueError('%s不能超过 %d 个字符' % (label, limit))
    return value


def _positive(value, low, high, label):
    if value in (None, ''):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + '必须是数字') from None
    if number != number or not low <= number <= high:
        raise ValueError('%s必须在 %g 到 %g 之间' % (label, low, high))
    return number


def guess_role(name):
    lowered = name.lower()
    return next((role for role, words in ROLE_WORDS if any(word in lowered for word in words)), 'other')


def fields(body, current=None):
    """The editable fields of a component, validated; missing ones keep their current value."""
    if not isinstance(body, dict):
        raise ValueError('组件信息格式无效')
    current = current or {}
    result = {}
    result['name'] = _text(body['name'], 60, '名称') if 'name' in body else current.get('name', '')
    result['sku'] = _text(body['sku'], 40, '型号') if 'sku' in body else current.get('sku', '')
    result['caption'] = _text(body['caption'], 80, '说明') if 'caption' in body else current.get('caption', '')
    role = body.get('role', current.get('role') or 'other')
    if role not in ROLES:
        raise ValueError('未知的组件类型')
    result['role'] = role
    quantity = body.get('quantity', current.get('quantity', 1))
    if type(quantity) is str and quantity.strip().isdigit():
        quantity = int(quantity)
    if type(quantity) is not int or not 1 <= quantity <= 99:
        raise ValueError('数量必须是 1 到 99 的整数')
    result['quantity'] = quantity
    result['size_mm'] = _positive(body['size_mm'], 1, 100000, '最长边尺寸') if 'size_mm' in body else current.get('size_mm')
    included = body.get('included', current.get('included', True))
    if type(included) is not bool:
        raise ValueError('是否显示必须是 true 或 false')
    result['included'] = included
    return result


# ---- storage ----

def components(project):
    rows = project.get('components')
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)] if isinstance(rows, list) else []


def _folder(project_id, component_id):
    return DATA / project_id / 'components' / component_id


def url(path):
    return '/files/' + Path(path).relative_to(DATA).as_posix()


def path_of(project_id, file_url, suffixes=('.png',)):
    """The file a stored URL names, or None: only inside this project's own folder."""
    if not isinstance(file_url, str) or not file_url.startswith('/files/' + project_id + '/'):
        return None
    path = (DATA / file_url[len('/files/'):]).resolve()
    root = (DATA / project_id).resolve()
    return path if root in path.parents and path.suffix.lower() in suffixes and path.is_file() else None


def public(record):
    keys = ('id', 'name', 'role', 'sku', 'caption', 'quantity', 'kind', 'status', 'stage', 'progress', 'error', 'size_mm', 'size_source',
            'included', 'cutout', 'rotation', 'pose', 'notes', 'created_at', 'updated_at')
    return {key: record.get(key) for key in keys}


def _change(project_id, component_id, **changes):
    """Write fields of one component. Returns the record, or None when it has been removed."""
    with LOCK:
        project = get('project', project_id)
        record = next((row for row in components(project) if row['id'] == component_id), None)
        if record is None:
            return None
        record.update(changes); record['updated_at'] = time.time()
        put('project', project)
        return record


def _cancelled(project_id, component_id, token):
    try:
        project = get('project', project_id)
    except KeyError:
        return True
    record = next((row for row in components(project) if row['id'] == component_id), None)
    return record is None or record.get('token') != token


# ---- Blender ----

def _digest(path):
    path = Path(path); stat = path.stat(); key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _DIGESTS:
        _DIGESTS[key] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return _DIGESTS[key]


def _tail_progress(log_path):
    """The share of samples the running render reports, from the end of its log; None before it starts."""
    try:
        with open(log_path, 'rb') as stream:
            stream.seek(0, 2); size = stream.tell(); stream.seek(max(0, size - 600))
            tail = stream.read().decode('utf-8', 'replace')
    except OSError:
        return None
    found = re.findall(r'PROGRESS: Sample (\d+)/(\d+)', tail)
    if not found:
        return None
    done, total = (int(value) for value in found[-1])
    return min(1.0, done / total) if total else None


def run_blender(script, request, folder, cancelled=None, on_progress=None, timeout=STAGE_TIMEOUT, key=None):
    """Run one of the application's own Blender scripts on a request file. Returns its result dict."""
    import engine
    executable = engine.blender_path()
    if not executable or not Path(script).is_file():
        raise ValueError('渲染组件未就绪，请确认 Blender 可用。')
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / 'request.json', {**request, 'output_dir': str(folder)})
    (folder / 'result.json').unlink(missing_ok=True)
    command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '0', '--python-exit-code', '1',
               '--python', str(script), '--', str(folder / 'request.json')]
    stop = cancelled or (lambda: False)
    try:
        # Cut-outs are Cycles renders: they take the GPU lane in turn with the shot renders.
        with (folder / 'blender.log').open('w', encoding='utf-8') as log, blender_runner.launched(
                command, ident=key, lane=blender_runner.HEAVY, cancelled=stop, cancel_error=ComponentCancelled,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT) as process:
            started = time.time()
            if key:
                PROCESSES[key] = process
            try:
                while process.poll() is None:
                    if stop():
                        process.terminate(); raise ComponentCancelled()
                    if time.time() - started > timeout:
                        process.kill(); raise ValueError('渲染超时，请重试。')
                    if on_progress:
                        share = _tail_progress(folder / 'blender.log')
                        if share is not None:
                            on_progress(share)
                    time.sleep(.25)
            finally:
                PROCESSES.pop(key, None)
    except OSError as exc:
        raise ValueError('渲染进程未能启动。') from exc
    try:
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        result = None
    if process.returncode or not isinstance(result, dict) or result.get('success') is not True:
        reason = result.get('error') if isinstance(result, dict) and isinstance(result.get('error'), str) else ''
        raise ValueError('渲染失败%s' % ('：' + reason[:200] if reason else '，请查看模型文件是否完整。'))
    return result


def stage(project_id, request, cancelled=None, on_progress=None):
    """A staged picture for this request, rendered once and kept. Returns the result with 'path' and 'folder'."""
    model = Path(request['model_path'])
    if not model.is_file():
        raise ValueError('模型文件不存在，请重新上传。')
    stat = model.stat()
    identity = hashlib.sha256(json.dumps([request, stat.st_size, stat.st_mtime_ns, _digest(STAGE_SCRIPT), _digest(WORKER_SCRIPT),
                                          _digest(ROOT / 'blender' / 'stage_geometry.py'), _digest(ROOT / 'blender' / 'articulate.py')],
                                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    folder = DATA / project_id / 'stage' / identity

    def stored():
        try:
            result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(result, dict) or result.get('success') is not True:
            return None
        if result.get('image') and not (folder / result['image']).is_file():
            return None
        return {**result, 'folder': folder, 'path': folder / result['image'] if result.get('image') else None, 'reused': True}
    found = stored()
    if found:
        return found
    with STAGE_LOCK:
        found = stored()
        if found:
            return found
        work = folder.with_name(identity + '-' + uuid.uuid4().hex[:8])
        try:
            result = run_blender(STAGE_SCRIPT, request, work, cancelled, on_progress, key=project_id)
            if result.get('image'):
                with Image.open(work / result['image']) as image:
                    if image.size != (result['width'], result['height']) or image.mode != 'RGBA':
                        raise ValueError('渲染结果不是预期的透明图片。')
            shutil.rmtree(folder, ignore_errors=True)
            work.rename(folder)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return {**result, 'folder': folder, 'path': folder / result['image'] if result.get('image') else None, 'reused': False}


# ---- cut-outs ----

def trim(image, rect=None, pad=.02):
    """The picture cropped to what it shows (to ``rect`` when the product's outline is known), with a little room."""
    width, height = image.size
    box = image.getchannel('A').point(lambda value: 255 if value > 8 else 0).getbbox()
    if box is None:
        raise ValueError('渲染结果是空白的。')
    if rect:
        # The outline bounds the product; the alpha also holds its shadow, which is kept where it falls.
        box = (min(box[0], int(rect[0] * width)), min(box[1], int(rect[1] * height)),
               max(box[2], int(rect[2] * width + 1)), max(box[3], int(rect[3] * height + 1)))
    grow = round(max(box[2] - box[0], box[3] - box[1]) * pad)
    box = (max(0, box[0] - grow), max(0, box[1] - grow), min(width, box[2] + grow), min(height, box[3] + grow))
    return image.crop(box), box


def cut_out_picture(source, target):
    """A photograph on a plain light background, with that background made transparent. Returns notes."""
    notes = []
    with Image.open(source) as opened:
        opened.load()
        if max(opened.size) > 6000:
            raise ValueError('图片过大，请缩小到 6000 像素以内。')
        image = opened.convert('RGBA')
    alpha = image.getchannel('A')
    if alpha.getextrema()[0] < 250:
        notes.append('使用图片自带的透明背景。')
    else:
        rgb = image.convert('RGB'); width, height = rgb.size
        border = [rgb.getpixel((x, y)) for x in range(0, width, max(1, width // 64)) for y in (0, height - 1)] + \
                 [rgb.getpixel((x, y)) for y in range(0, height, max(1, height // 64)) for x in (0, width - 1)]
        ground = tuple(sorted(pixel[channel] for pixel in border)[len(border) // 2] for channel in range(3))
        spread = max(max(abs(pixel[channel] - ground[channel]) for channel in range(3)) for pixel in border)
        if spread > 40:
            notes.append('图片背景不是纯色，未能自动抠图；请上传白底或透明背景的图片。')
        else:
            # Distance from the background colour becomes coverage; only what connects to the border is removed.
            difference = ImageChops.difference(rgb, Image.new('RGB', rgb.size, ground))
            distance = ImageChops.lighter(ImageChops.lighter(difference.getchannel(0), difference.getchannel(1)), difference.getchannel(2))
            solid = distance.point(lambda value: 255 if value > 18 else 0)
            outside = Image.new('L', (width + 2, height + 2), 0); outside.paste(solid, (1, 1))
            ImageDraw.floodfill(outside, (0, 0), 128, thresh=0)
            background = outside.crop((1, 1, width + 1, height + 1)).point(lambda value: 255 if value == 128 else 0)
            soft = distance.point(lambda value: min(255, max(0, (value - 6) * 12)))
            alpha = ImageChops.lighter(ImageChops.invert(background), ImageChops.multiply(soft, background)).filter(ImageFilter.GaussianBlur(.6))
            image.putalpha(alpha)
            notes.append('已按纯色背景自动抠图。')
    cropped, _ = trim(image, pad=.01)
    if max(cropped.size) > CUTOUT_PX * 2:
        scale = CUTOUT_PX * 2 / max(cropped.size)
        cropped = cropped.resize((max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))), Image.Resampling.LANCZOS)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    cropped.save(target, 'PNG')
    return cropped.size, notes


def component_plan(analysis, model_path, rotation):
    """The render plan of a component: the same showcase look and inferred finishes as a new product."""
    import lookdev
    import planner
    plan = planner.default_plan(); plan.update(look='showcase', rotation=[float(value) for value in rotation])
    try:
        plan, _ = lookdev.apply(plan, analysis, str(model_path))
    except (ValueError, KeyError, TypeError):
        pass
    return plan


def cutout_request(model_path, plan, size_mm, azimuth=35, elevation=18, pixels=CUTOUT_PX):
    return {'action': 'render', 'model_path': str(model_path), 'plan': plan, 'size_mm': float(size_mm),
            'camera': {'azimuth': azimuth, 'elevation': elevation, 'focal_length': 85, 'margin': .05},
            'ground': 'none', 'size': [pixels, pixels], 'samples': 48}


def save_cutout(result, target):
    """Trim a staged picture to its product and store it. Returns the stored description."""
    with Image.open(result['path']) as opened:
        image = opened.convert('RGBA')
    cropped, box = trim(image, result.get('product_rect'))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    cropped.save(target, 'PNG')
    width, height = image.size; rect = result.get('product_rect') or [0, 0, 1, 1]
    inner = [max(0, (rect[0] * width - box[0]) / cropped.width), max(0, (rect[1] * height - box[1]) / cropped.height),
             min(1, (rect[2] * width - box[0]) / cropped.width), min(1, (rect[3] * height - box[1]) / cropped.height)]
    return {'url': url(target), 'width': cropped.width, 'height': cropped.height, 'product_rect': [round(value, 4) for value in inner],
            'azimuth': result.get('azimuth'), 'elevation': result.get('elevation')}


# ---- adding, editing, removing ----

def reserve(project_id):
    """A new component's id and the empty folder its upload is saved into."""
    with LOCK:
        project = get('project', project_id)
        if len(components(project)) >= MAX_COMPONENTS:
            raise ValueError('一个项目最多 %d 个组件' % MAX_COMPONENTS)
    component_id = 'c' + uuid.uuid4().hex[:10]; base = _folder(project_id, component_id) / 'source'
    base.mkdir(parents=True)
    return component_id, base


def discard(project_id, component_id):
    shutil.rmtree(_folder(project_id, component_id), ignore_errors=True)


def register(project_id, component_id, body, check_resources=None):
    """Record the component whose files were saved into its reserved folder, and queue its processing."""
    base = _folder(project_id, component_id) / 'source'
    try:
        models = [path for path in base.rglob('*') if path.suffix.lower() in MODEL_SUFFIXES]
        pictures = [path for path in base.rglob('*') if path.suffix.lower() in PICTURE_SUFFIXES]
        if len(models) > 1:
            raise ValueError('每个组件请上传一个 OBJ 或 GLB；多个组件请分别添加')
        if models:
            if check_resources:
                check_resources(base)
            kind = 'model'; source = models[0]
        elif len(pictures) == 1:
            kind = 'image'; source = pictures[0]
            if source.stat().st_size > MAX_PICTURE_BYTES:
                raise ValueError('图片文件过大')
        else:
            raise ValueError('请为组件上传一个模型（OBJ / GLB / ZIP），或一张白底 / 透明背景的图片')
        default_name = re.sub(r'[_\-]+', ' ', source.stem).strip()[:60] or '组件'
        given = fields({'name': default_name, 'role': guess_role(default_name), **{key: value for key, value in (body or {}).items() if value not in (None, '')}})
        record = {'id': component_id, 'kind': kind, 'source': str(source), 'status': 'queued', 'stage': 'queued', 'progress': 0, 'error': '',
                  'token': uuid.uuid4().hex, 'size_source': 'user' if given['size_mm'] else None, 'rotation': [0, 0, 0], 'cutout': None, 'notes': [],
                  'created_at': time.time(), 'updated_at': time.time(), **given}
        with LOCK:
            project = get('project', project_id)
            if len(components(project)) >= MAX_COMPONENTS:
                raise ValueError('一个项目最多 %d 个组件' % MAX_COMPONENTS)
            project['components'] = components(project) + [record]
            put('project', project)
    except Exception:
        discard(project_id, component_id)
        raise
    QUEUE.put((project_id, component_id, record['token']))
    return public(record)


def update(project_id, component_id, body):
    """Change a component's details. A new size or pose renders it again."""
    with LOCK:
        project = get('project', project_id)
        record = next((row for row in components(project) if row['id'] == component_id), None)
        if record is None:
            raise KeyError(component_id)
        changed = fields(body, record)
        again = False
        if 'size_mm' in body and changed['size_mm'] != record.get('size_mm'):
            record['size_source'] = 'user' if changed['size_mm'] else None
        turn = body.get('turn')
        if turn is not None:
            import orientation
            if record['kind'] != 'model':
                raise ValueError('图片组件不能调整朝向')
            if turn not in ('flip', 'left', 'right', 'forward', 'backward'):
                raise ValueError('未知的朝向调整')
            current = record.get('rotation') or [0, 0, 0]
            record['rotation'] = (orientation.flipped(current) if turn == 'flip' else orientation.turned(current, 90 if turn == 'left' else -90) if turn in ('left', 'right')
                                  else orientation.then(current, [90 if turn == 'forward' else -90, 0, 0]))
            record['pose'] = {**(record.get('pose') or {}), 'source': 'user'}
            again = True
        record.update(changed)
        if again and record['status'] in ('ready', 'failed'):
            record.update(status='queued', stage='queued', progress=0, error='', token=uuid.uuid4().hex, keep_pose=True)
        record['updated_at'] = time.time()
        put('project', project)
    if again:
        QUEUE.put((project_id, component_id, record['token']))
    return public(record)


def refresh(project_id, component_id):
    """Process a component again from its stored file."""
    with LOCK:
        project = get('project', project_id)
        record = next((row for row in components(project) if row['id'] == component_id), None)
        if record is None:
            raise KeyError(component_id)
        record.update(status='queued', stage='queued', progress=0, error='', token=uuid.uuid4().hex, keep_pose=False, updated_at=time.time())
        put('project', project)
    QUEUE.put((project_id, component_id, record['token']))
    return public(record)


def remove(project_id, component_id):
    with LOCK:
        project = get('project', project_id)
        rows = components(project)
        if not any(row['id'] == component_id for row in rows):
            raise KeyError(component_id)
        project['components'] = [row for row in rows if row['id'] != component_id]
        put('project', project)
    process = PROCESSES.get(project_id)
    shutil.rmtree(_folder(project_id, component_id), ignore_errors=True)
    return {'removed': component_id, 'stopped': bool(process)}


# ---- processing ----

def process(project_id, component_id, token):
    """Turn one stored component into a cut-out. Every failure is recorded on the component."""
    cancelled = lambda: _cancelled(project_id, component_id, token)

    def say(stage_name, share=None, **more):
        low = STAGES[stage_name]; high = min((value for value in STAGES.values() if value > low), default=100)
        progress = round(low + (high - low) * (share or 0))
        _change(project_id, component_id, **({} if cancelled() else {'status': 'processing', 'stage': stage_name, 'progress': progress, **more}))
    try:
        project = get('project', project_id)
        record = next((row for row in components(project) if row['id'] == component_id), None)
        if record is None or record.get('token') != token:
            return
        # A component uploaded with a new product waits until the product's own analysis and first image are under way:
        # the first image comes first.
        waited = time.time()
        while time.time() - waited < 1800 and not cancelled():
            state = get('project', project_id)
            if state.get('status') != 'analyzing':
                break
            time.sleep(2)
        if cancelled():
            return
        folder = _folder(project_id, component_id); source = Path(record['source']); notes = []
        target = folder / ('cutout-%s.png' % uuid.uuid4().hex[:8])
        if record['kind'] == 'image':
            say('cutout')
            size, notes = cut_out_picture(source, target)
            cutout = {'url': url(target), 'width': size[0], 'height': size[1], 'product_rect': [0, 0, 1, 1]}
            changes = {'cutout': cutout}
        else:
            import orientation
            from blender import stage_geometry
            say('analyse')
            analysis = run_blender(WORKER_SCRIPT, {'action': 'analyze', 'model_path': str(source)}, folder / 'analysis', cancelled)
            rotation = record.get('rotation') or [0, 0, 0]; pose = record.get('pose')
            if not record.get('keep_pose'):
                say('pose')
                stub = {'id': project_id, 'model_path': str(source), 'analysis': analysis}
                try:
                    judged = orientation.decide(stub, component_plan(analysis, source, [0, 0, 0]), folder / ('pose-' + uuid.uuid4().hex[:6]), cancelled)
                    rotation = judged['rotation'] if judged.get('changed') and orientation.valid_rotation(judged.get('rotation')) else [0, 0, 0]
                    pose = {'source': judged.get('source'), 'chosen': judged.get('chosen'), 'changed': bool(judged.get('changed')),
                            'reason': str(judged.get('reason') or '')[:300]}
                except InterruptedError:
                    raise ComponentCancelled() from None
                except ValueError as exc:
                    notes.append('朝向检查未完成，保持文件原始朝向：%s' % str(exc)[:120]); rotation = [0, 0, 0]; pose = {'source': 'file', 'changed': False}
            plan = component_plan(analysis, source, rotation)
            size_mm = record.get('size_mm'); size_source = record.get('size_source')
            if not size_mm or size_source != 'user':
                dims = orientation.oriented_dimensions((analysis.get('features') or {}).get('dimensions') or [1, 1, 1], rotation)
                size_mm = stage_geometry.model_size_mm(max(dims), source.suffix) or 100.0; size_source = 'model'
                notes.append('尺寸按模型文件的单位估计为最长边 %g mm；填写真实尺寸后，套装图里的大小比例才准确。' % size_mm)
            say('render')
            result = stage(project_id, cutout_request(source, plan, size_mm), cancelled, lambda share: say('render', share))
            say('cutout')
            cutout = save_cutout(result, target)
            changes = {'cutout': cutout, 'rotation': rotation, 'pose': pose, 'size_mm': size_mm, 'size_source': size_source,
                       'dimensions_mm': result.get('dimensions_mm'), 'parts': [row['name'] for row in result.get('parts', [])][:80],
                       # The finishes it was rendered with, so a kit render with the product shows it the same way.
                       'finishes': [row for row in plan.get('material_overrides') or [] if isinstance(row, dict)][:60]}
        if cancelled():
            target.unlink(missing_ok=True)
            return
        old = (record.get('cutout') or {}).get('url')
        _change(project_id, component_id, status='ready', stage='ready', progress=100, error='', notes=notes, keep_pose=False, **changes)
        previous = path_of(project_id, old)
        if previous and previous != target:
            previous.unlink(missing_ok=True)
    except ComponentCancelled:
        return
    except Exception as exc:
        message = str(exc)[:300] if isinstance(exc, ValueError) and str(exc) else '处理失败，请重试或更换文件。'
        if not cancelled():
            _change(project_id, component_id, status='failed', stage='queued', error=message)


def _work():
    while True:
        project_id, component_id, token = QUEUE.get()
        try:
            process(project_id, component_id, token)
        except Exception:
            pass
        finally:
            QUEUE.task_done()


def start():
    global _WORKER
    if _WORKER is None or not _WORKER.is_alive():
        _WORKER = threading.Thread(target=_work, name='components', daemon=True); _WORKER.start()


def recover_interrupted():
    """Components left mid-way by a restart are queued again."""
    from studio_store import all_records
    for stored in all_records('project'):
        rows = components(stored)
        if not any(row.get('status') in ('queued', 'processing') for row in rows):
            continue
        with LOCK:
            try:
                project = get('project', stored['id'])
            except KeyError:
                continue
            waiting = []
            for row in components(project):
                if row.get('status') in ('queued', 'processing'):
                    row.update(status='queued', stage='queued', progress=0, token=uuid.uuid4().hex); waiting.append((project['id'], row['id'], row['token']))
            put('project', project)
        for item in waiting:
            QUEUE.put(item)


def wait_idle(timeout=600):
    """For tests and scripts: block until queued components are done."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if QUEUE.unfinished_tasks == 0:
            return True
        time.sleep(.2)
    return False

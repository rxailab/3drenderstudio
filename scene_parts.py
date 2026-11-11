"""Inspect the exact saved preview scene without changing the photography plan.

The inspector is an independent, read-only Blender process. Its neutral part
catalog shows actual isolated material surfaces, including occluded surfaces.
Only this module chooses files, checks identity and prepares images for AI.
"""
import copy
import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import engine
import target_evidence
from studio_store import ROOT, atomic_json


CATALOG_VERSION = 1
SURFACE_MEASUREMENT_VERSION = 1
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_PAGE_BYTES = 16 * 1024 * 1024
INSPECTION_TIMEOUT = 180


class InspectionUnavailable(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _file_hash(path):
    before = path.stat(); digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InspectionUnavailable('检查期间场景发生变化，请重新生成当前预览。')
    return digest.hexdigest()


def _source(output):
    return {'output_id': output.get('job_id'),
            **{key: output.get(key) for key in ('shot_id', 'quality', 'version')}}


def _confined_file(root, url, suffix):
    if not isinstance(url, str) or not url.startswith('/files/'):
        return None
    # The caller's root is the exact project folder, not an arbitrary URL root.
    path = (root.parent / url[7:]).resolve()
    if root.resolve() not in path.parents or path.suffix.lower() != suffix or not path.is_file():
        return None
    return path


def _scene(data_root, project, output):
    if (not isinstance(output, dict) or output.get('version') != project.get('version')
            or not target_evidence._snapshot(_source(output))):
        raise InspectionUnavailable('需要当前方案版本的预览，才能检查完整部件。')
    path = _confined_file(data_root / project['id'], output.get('blend_url'), '.blend')
    if path is None:
        raise InspectionUnavailable('当前预览缺少三维场景，请重新生成预览后再定位部件。')
    return path


def _known_objects(analysis):
    rows = []
    for obj in (analysis or {}).get('objects', []):
        if not isinstance(obj, dict) or not isinstance(obj.get('name'), str) or obj.get('type', 'MESH') != 'MESH':
            continue
        materials = obj.get('materials') or ['Default material']
        if not isinstance(materials, list) or not all(isinstance(name, str) and name for name in materials):
            continue
        rows.append({'name': obj['name'], 'materials': list(dict.fromkeys(materials))})
    return rows


def _observed_slots(data_root, project, output):
    """Only current, real render samples can explain renamed material slots."""
    path = _confined_file(data_root / project['id'], output.get('result_url'), '.json')
    if path is None or path.stat().st_size > target_evidence.MAX_RESULT_BYTES:
        return []
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return []
    if (not isinstance(value, dict) or value.get('success') is not True
            or not target_evidence._result_matches_source(value, _source(output))):
        return []
    samples = value.get('product_samples')
    if not isinstance(samples, list): return []
    known = target_evidence._pairs(project.get('analysis')); rows = []; seen = set()
    for sample in samples[:50000]:
        if not target_evidence._point(sample): continue
        pair = (sample.get('object_name'), sample.get('material_name'))
        if not all(isinstance(item, str) for item in pair) or pair not in known: continue
        row = {'object_name': pair[0], 'material_name': pair[1]}
        alias = sample.get('material_render_name')
        if isinstance(alias, str) and 0 < len(alias) <= 300: row['material_render_name'] = alias
        slot = sample.get('slot_index', sample.get('material_index'))
        if type(slot) is int and 0 <= slot < 10000: row['slot_index'] = slot
        key = _digest(row)
        if key not in seen: rows.append(row); seen.add(key)
    return rows


def wants_inspection(text, analysis):
    objects = {obj.get('name') for obj in (analysis or {}).get('objects', [])
               if isinstance(obj, dict) and isinstance(obj.get('name'), str)}
    materials = {pair[1] for pair in target_evidence._pairs(analysis)}
    for assignment in target_evidence.material_assignments(text):
        phrase = assignment['phrase']
        if (not target_evidence._whole_target(phrase)
                and not any(name in phrase for name in objects)
                and not any(target_evidence.named_global_material(name, phrase) for name in materials)):
            return True
    return False


def _identity(data_root, project, output, scene, seeds=None):
    stat = scene.stat()
    image = _confined_file(data_root / project['id'], output.get('png_url'), '.png')
    if image is None:
        raise InspectionUnavailable('当前预览图片缺失，请重新生成预览。')
    seeds = seeds or []
    known = target_evidence._pairs(project.get('analysis'))
    if (not isinstance(seeds, list) or len(seeds) > 12
            or any(not isinstance(seed, dict) or set(seed) != {'object_name', 'material_name'}
                   or (seed['object_name'], seed['material_name']) not in known for seed in seeds)):
        raise InspectionUnavailable('示例部位未对应到当前模型的真实材质槽。')
    seeds = sorted(copy.deepcopy(seeds), key=lambda seed: (seed['object_name'], seed['material_name']))
    return {'generation_version': CATALOG_VERSION, 'surface_measurement_version': SURFACE_MEASUREMENT_VERSION,
            'source_snapshot': _source(output),
            'analysis_digest': _digest(project.get('analysis')),
            'image_digest': _file_hash(image),
            'target_seeds': seeds,
            'scene': {'url': output['blend_url'], 'size': stat.st_size, 'modified_ns': stat.st_mtime_ns,
                      'digest': _file_hash(scene)},
            'observed_slots': _digest(_observed_slots(data_root, project, output))}


def _folder(data_root, project, identity):
    root = (data_root / project['id']).resolve()
    folder = root / 'part-inspections' / _digest(identity)
    if root not in folder.resolve().parents:
        raise InspectionUnavailable('部件检查目录无效。')
    return folder


def _page_path(folder, value):
    if not isinstance(value, str): return None
    path = Path(value)
    if not path.is_absolute(): path = folder / path
    path = path.resolve()
    if folder.resolve() not in path.parents or path.suffix.lower() != '.png' or not path.is_file():
        return None
    if path.stat().st_size > MAX_PAGE_BYTES: return None
    return path


def _load(folder, identity, analysis):
    manifest = folder / 'manifest.json'
    if not manifest.is_file() or manifest.stat().st_size > MAX_RESULT_BYTES: return None
    try:
        value = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('identity') != identity: return None
        evidence = target_evidence.public_evidence(value.get('evidence'), analysis, identity['source_snapshot']['version'])
        pages = value.get('images')
        if not evidence or not isinstance(pages, list) or len(pages) != len(evidence['catalog_pages']): return None
        paths = []
        for index, page in enumerate(pages):
            path = _page_path(folder, page.get('path')) if isinstance(page, dict) else None
            if path is None or _file_hash(path) != page.get('digest'): return None
            paths.append({'label': evidence['catalog_pages'][index]['label'], 'path': path})
        evidence['_inspection_guard'] = _digest(value)
        evidence['_scene_identity'] = copy.deepcopy(identity)
        return {'evidence': evidence, 'images': paths, 'cache_hit': True}
    except (OSError, ValueError, UnicodeError, TypeError, KeyError):
        return None


def _font(size):
    # IDs are ASCII; no host-dependent Chinese fonts are needed in the atlas.
    for name in ('DejaVuSans.ttf', 'arial.ttf'):
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default()


def _finish(folder, identity, analysis, result):
    if (not isinstance(result, dict) or result.get('success') is not True
            or result.get('method') != 'scene_part_catalog' or result.get('generation_version') != CATALOG_VERSION):
        raise InspectionUnavailable('未能完成三维部件检查，请重新生成当前预览后重试。')
    pages = result.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= 4:
        raise InspectionUnavailable('没有得到可核验的部件图，请点选部位后重试。')
    public_pages = []; images = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict): raise InspectionUnavailable('部件图记录无效。')
        path = _page_path(folder, page.get('path'))
        if path is None: raise InspectionUnavailable('部件图未生成。')
        with Image.open(path) as original:
            original.load()
            if original.format != 'PNG' or any(not 64 <= dimension <= 1600 for dimension in original.size):
                raise InspectionUnavailable('部件图尺寸或格式无效。')
            image = original.convert('RGB')
        width, height = image.size
        if page.get('width') != width or page.get('height') != height:
            raise InspectionUnavailable('部件图与检查记录不一致。')
        public_pages.append({'label': 'PART_CATALOG_' + str(index + 1), 'width': width, 'height': height})
        images.append(image)
    raw_evidence = {'method': 'scene_part_catalog', 'generation_version': CATALOG_VERSION,
                    'source_snapshot': identity['source_snapshot'], 'catalog_pages': public_pages,
                    'targets': result.get('targets'), 'truncated': bool(result.get('truncated')),
                    'unresolved_count': len(result.get('unresolved', [])) if isinstance(result.get('unresolved'), list) else 0}
    evidence = target_evidence.public_evidence(raw_evidence, analysis, identity['source_snapshot']['version'])
    if not evidence: raise InspectionUnavailable('部件检查未对应到当前模型的真实材质槽，当前方案未修改。')
    for row in evidence['targets']:
        thumbnail = row['thumbnail']; image = images[thumbnail['page']]
        x, y, w, h = thumbnail['rect']; width, height = image.size
        left, top = round(x * width), round(y * height)
        right = min(width, round((x + w) * width)); bottom = min(height, top + 26)
        draw = ImageDraw.Draw(image)
        draw.rectangle((left, top, right - 1, bottom - 1), fill='#edf1f7')
        draw.text((left + 7, top + 3), row['id'], fill='#142a45', font=_font(18))
    page_records = []
    for index, image in enumerate(images):
        path = folder / ('catalog-' + str(index + 1) + '.png')
        image.save(path, format='PNG')
        page_records.append({'path': path.name, 'digest': _file_hash(path)})
    manifest = {'identity': identity, 'evidence': evidence, 'images': page_records}
    atomic_json(folder / 'manifest.json', manifest)
    loaded = _load(folder, identity, analysis)
    if not loaded: raise InspectionUnavailable('部件检查结果未能通过保存校验，请重试。')
    loaded['cache_hit'] = False
    return loaded


def _prepare(data_root, project, output, seeds=None):
    """Prepare or reuse one immutable source-bound catalog; no AI calls."""
    scene = _scene(data_root, project, output)
    identity = _identity(data_root, project, output, scene, seeds)
    folder = _folder(data_root, project, identity)
    loaded = _load(folder, identity, project.get('analysis'))
    if loaded: return loaded
    script = ROOT / 'blender' / 'inspect_parts.py'
    executable = engine.blender_path()
    if not script.is_file() or not executable:
        raise InspectionUnavailable('三维部件检查组件未就绪，请确认已更新 blender/inspect_parts.py 并配置 Blender。')
    folder.mkdir(parents=True, exist_ok=True)
    request_path = folder / 'request.json'; result_path = folder / 'result.json'
    request = {'blend_path': str(scene), 'result_path': str(result_path), 'output_dir': str(folder),
               'known_objects': _known_objects(project.get('analysis')),
               'observed_slots': _observed_slots(data_root, project, output)}
    if seeds:
        request['priority_objects'] = list(dict.fromkeys(seed['object_name'] for seed in seeds))
    atomic_json(request_path, request)
    result_path.unlink(missing_ok=True)
    command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '4',
               '--python-exit-code', '1', '--python', str(script), '--', str(request_path)]
    try:
        with (folder / 'inspect.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=INSPECTION_TIMEOUT, cwd=str(ROOT),
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise InspectionUnavailable('部件检查未完成，请保留当前要求并重试；也可以点选示例部位帮助识别。') from exc
    if result.returncode or not result_path.is_file() or result_path.stat().st_size > MAX_RESULT_BYTES:
        raise InspectionUnavailable('三维部件检查失败，当前方案未修改。可下载项目归档，保留场景供排查。')
    if _identity(data_root, project, output, scene, seeds) != identity:
        raise InspectionUnavailable('部件检查期间场景发生变化，当前方案未修改，请重新生成预览。')
    try: payload = json.loads(result_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise InspectionUnavailable('部件检查结果无法读取，当前方案未修改。') from exc
    return _finish(folder, identity, project.get('analysis'), payload)


def prepare(data_root, project, output, seeds=None):
    try:
        return _prepare(data_root, project, output, seeds)
    except InspectionUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InspectionUnavailable('部件检查文件已变化或无法读取，请重新生成当前预览后重试。') from exc


def verify(data_root, project, output, evidence):
    """No new inspection or model call during validation/confirmation."""
    try:
        scene = _scene(data_root, project, output)
        seeds = evidence.get('_scene_identity', {}).get('target_seeds', [])
        identity = _identity(data_root, project, output, scene, seeds)
        if identity != evidence.get('_scene_identity'): return False
        loaded = _load(_folder(data_root, project, identity), identity, project.get('analysis'))
        return bool(loaded and loaded['evidence'] == evidence)
    except (OSError, ValueError):
        return False


def guard(data_root, project, output, seeds=None):
    """Fingerprint an existing inspection without generating a new one."""
    try:
        scene = _scene(data_root, project, output)
        identity = _identity(data_root, project, output, scene, seeds)
        loaded = _load(_folder(data_root, project, identity), identity, project.get('analysis'))
    except (OSError, ValueError) as exc:
        raise InspectionUnavailable('已定位部件的场景已变化，请重新生成当前预览。') from exc
    if not loaded:
        raise InspectionUnavailable('已定位部件的检查结果失效，请重新分析要求。')
    return {'identity': identity, 'digest': loaded['evidence']['_inspection_guard']}

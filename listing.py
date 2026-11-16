"""A product's listing: its record, its brand, and the set of images built from them.

The studio's shots are six views of one object. A listing is a sequence of arguments: what you
get, what is in the box, what the parts do, what it is like in use, how big it is. This module
keeps what those images are made from (the product record, the brand kit, the components) and
builds the set: it stages the pictures each page needs, lays the pages out with the templates
in ``listing_pages`` and writes them as PNG files.

A page prints only numbers the user entered or that were measured on the model.
"""
import copy
import io
import json
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

from studio_store import DATA, LOCK, get, put, all_records

import brand_kit
import components
import listing_draw
import listing_elements
import listing_pages

KIND = 'listing_task'
PAGE_PX = 2000
MAX_FEATURES = 8
MAX_SPECS = 10
MAX_FIGURES = 3
MAX_PAGES = 9
LANGUAGES = ('en', 'zh')
WORKPIECES = ('pipe', 'rod', 'board', 'sheet', 'block')
MATERIALS = ('pvc_white', 'pvc_grey', 'copper', 'steel', 'aluminium', 'wood', 'concrete', 'drywall', 'plastic_black')
MODES = ('through', 'under', 'front', 'manual')
AUTO_ORDER = ('main', 'includes', 'callouts', 'usecase', 'feature', 'band', 'specs', 'benefits')
# What a tool is shown working on, by what it is called. Only a starting point: the user can change every part of it.
USECASE_RULES = (
    (('pipe cutter', 'tube cutter', 'tubing cutter', 'pipe-cutter', '割管', '切管', '管刀', '管子割刀'), {'kind': 'pipe', 'material': 'pvc_white', 'mode': 'through'}),
    (('pipe wrench', 'wrench', 'spanner', '扳手', '管钳'), {'kind': 'pipe', 'material': 'steel', 'mode': 'through'}),
    (('clamp', 'vise', 'vice', '夹具', '台钳', '夹钳'), {'kind': 'board', 'material': 'wood', 'mode': 'through'}),
    (('crimper', 'crimping', '压接', '压线'), {'kind': 'rod', 'material': 'copper', 'mode': 'through'}),
    (('jigsaw', 'circular saw', 'saw', '曲线锯', '电锯', '圆锯', '锯'), {'kind': 'board', 'material': 'wood', 'mode': 'under'}),
    (('sander', 'planer', 'router', '砂光', '打磨机', '电刨', '修边'), {'kind': 'board', 'material': 'wood', 'mode': 'under'}),
    (('grinder', '角磨', '磨光'), {'kind': 'sheet', 'material': 'steel', 'mode': 'under'}),
    (('drill', 'driver', '电钻', '冲击钻', '起子'), {'kind': 'board', 'material': 'wood', 'mode': 'front'}),
)
STAGES = {'queued': 0, 'pictures': 4, 'compose': 86, 'done': 100}
TASKS = {}


class ListingCancelled(Exception):
    pass


# ---- the product record ----

def _text(value, limit, label):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(label + '必须是文字')
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value).strip()
    if len(value) > limit:
        raise ValueError('%s不能超过 %d 个字符' % (label, limit))
    return value


def _number(value, low, high, label, empty=True):
    if value in (None, ''):
        if empty:
            return None
        raise ValueError(label + '不能为空')
    if isinstance(value, bool):
        raise ValueError(label + '必须是数字')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + '必须是数字') from None
    if number != number or not low <= number <= high:
        raise ValueError('%s必须在 %g 到 %g 之间' % (label, low, high))
    return number


def _triple(value, low, high, label):
    if value in (None, ''):
        return [0.0, 0.0, 0.0]
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(label + '必须是三个数字')
    return [_number(entry, low, high, label, False) for entry in value]


def part_names(project):
    analysis = project.get('analysis') or {}
    return [item['name'] for item in analysis.get('objects', []) if isinstance(item, dict) and item.get('type', 'MESH') == 'MESH' and isinstance(item.get('name'), str)]


def usecase_for(name):
    """The workpiece a product of this name is usually shown on, or None."""
    lowered = (name or '').lower()
    for words, plan in USECASE_RULES:
        if any(word in lowered for word in words):
            return dict(plan)
    return None


def validate_usecase(value, current=None):
    current = current or {}
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('使用场景设置格式无效')
    merged = {**current, **value}
    enabled = merged.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('使用场景开关必须是 true 或 false')
    kind = merged.get('kind') or 'pipe'; material = merged.get('material') or 'pvc_white'; mode = merged.get('mode') or 'through'
    if kind not in WORKPIECES:
        raise ValueError('未知的工件类型')
    if material not in MATERIALS:
        raise ValueError('未知的工件材质')
    if mode not in MODES:
        raise ValueError('未知的工件位置')
    side = merged.get('side') or '+'
    if side not in ('+', '-'):
        raise ValueError('工件所在的一端只能是 + 或 -')
    return {'enabled': enabled, 'kind': kind, 'material': material, 'mode': mode, 'side': side,
            'diameter_mm': _number(merged.get('diameter_mm'), 1, 2000, '工件直径'), 'width_mm': _number(merged.get('width_mm'), 1, 5000, '工件宽度'),
            'thickness_mm': _number(merged.get('thickness_mm'), .2, 1000, '工件厚度'), 'length_mm': _number(merged.get('length_mm'), 5, 20000, '工件长度'),
            'offset_mm': _triple(merged.get('offset_mm'), -5000, 5000, '工件偏移'), 'rotate': _triple(merged.get('rotate'), -360, 360, '工件旋转'),
            'headline': _text(merged.get('headline'), 60, '使用场景标题')}


def default_product(project):
    use = usecase_for(project.get('name', '') + ' ' + str((project.get('metadata') or {}).get('product_category', '')))
    return {'title': str(project.get('name', ''))[:60], 'model': '', 'headline': '', 'subline': '', 'language': 'en', 'size_mm': None,
            'features': [], 'specs': [], 'figures': [], 'pages': None, 'drafted': None,
            # Shown by default only where the placement can be worked out from the model: a tool that closes round its work.
            'usecase': validate_usecase({'enabled': bool(use) and use['mode'] == 'through', **(use or {})})}


def validate_product(body, project, current=None):
    """A clean product record. Fields the body leaves out keep their current value."""
    if not isinstance(body, dict):
        raise ValueError('产品信息格式无效')
    current = current or default_product(project); names = set(part_names(project))
    record = {'title': _text(body['title'], 60, '产品名称') if 'title' in body else current['title'],
              'model': _text(body['model'], 40, '型号') if 'model' in body else current['model'],
              'headline': _text(body['headline'], 60, '主标题') if 'headline' in body else current['headline'],
              'subline': _text(body['subline'], 120, '副标题') if 'subline' in body else current['subline'],
              'size_mm': _number(body['size_mm'], 1, 100000, '最长边尺寸') if 'size_mm' in body else current.get('size_mm'),
              'drafted': current.get('drafted')}
    language = body.get('language', current.get('language', 'en'))
    if language not in LANGUAGES:
        raise ValueError('文字语言只能是 en（英文）或 zh（中文）')
    record['language'] = language
    features = body.get('features', current.get('features', []))
    if not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError('卖点最多 %d 条' % MAX_FEATURES)
    clean = []
    for row in features:
        if not isinstance(row, dict):
            raise ValueError('卖点格式无效')
        title = _text(row.get('title'), 40, '卖点标题')
        if not title:
            continue
        parts = row.get('parts') or []
        if not isinstance(parts, list) or len(parts) > 40 or any(not isinstance(name, str) for name in parts):
            raise ValueError('卖点对应的部件格式无效')
        clean.append({'title': title, 'detail': _text(row.get('detail'), 90, '卖点说明'), 'parts': [name for name in parts if name in names][:40],
                      'icon': _text(row.get('icon'), 40, '图标关键词')})
    record['features'] = clean
    specs = body.get('specs', current.get('specs', []))
    if not isinstance(specs, list) or len(specs) > MAX_SPECS:
        raise ValueError('规格最多 %d 条' % MAX_SPECS)
    record['specs'] = [{'label': _text(row.get('label'), 30, '规格名称'), 'value': _text(row.get('value'), 30, '规格数值')}
                       for row in specs if isinstance(row, dict) and _text(row.get('label'), 30, '规格名称') and _text(row.get('value'), 30, '规格数值')]
    figures = body.get('figures', current.get('figures', []))
    if not isinstance(figures, list) or len(figures) > MAX_FIGURES:
        raise ValueError('大数字指标最多 %d 个' % MAX_FIGURES)
    record['figures'] = [{'value': _text(row.get('value'), 12, '指标数值'), 'unit': _text(row.get('unit'), 8, '指标单位'), 'label': _text(row.get('label'), 30, '指标名称')}
                         for row in figures if isinstance(row, dict) and _text(row.get('value'), 12, '指标数值') and _text(row.get('label'), 30, '指标名称')]
    pages = body.get('pages', current.get('pages'))
    if pages is not None:
        if not isinstance(pages, list) or len(pages) > MAX_PAGES or any(kind not in listing_pages.KINDS for kind in pages):
            raise ValueError('图片类型列表无效')
        pages = list(dict.fromkeys(pages))
    record['pages'] = pages
    record['usecase'] = validate_usecase(body.get('usecase'), current.get('usecase'))
    return record


def stored(project):
    """(product record, brand record, listing dict) of a project, with defaults filled in."""
    listing = project.get('listing') if isinstance(project.get('listing'), dict) else {}
    try:
        product = validate_product({}, project, listing.get('product') if isinstance(listing.get('product'), dict) else None)
    except ValueError:
        product = default_product(project)
    try:
        brand = brand_kit.validate(listing.get('brand') if isinstance(listing.get('brand'), dict) else {})
    except ValueError:
        brand = brand_kit.validate({})
    return product, brand, listing


def product_size(project, product):
    """(longest side in mm, whether it is a real figure). A model's own units are only a guess."""
    import orientation
    from blender import stage_geometry
    dims = project['plan'].get('dimensions_mm')
    if isinstance(dims, list) and len(dims) == 3:
        return float(max(dims)), True
    if product.get('size_mm'):
        return float(product['size_mm']), True
    measured = ((project.get('analysis') or {}).get('features') or {}).get('dimensions') or [1, 1, 1]
    guess = stage_geometry.model_size_mm(max(orientation.oriented_dimensions(measured, project['plan'].get('rotation', [0, 0, 0]))),
                                         Path(project.get('model_path', '')).suffix)
    return float(guess or 200.0), False


def ready_components(project):
    rows = []
    for row in components.components(project):
        path = components.path_of(project['id'], (row.get('cutout') or {}).get('url'))
        if row.get('status') == 'ready' and row.get('included', True) and path:
            rows.append({'id': row['id'], 'name': row.get('name', ''), 'sku': row.get('sku', ''), 'caption': row.get('caption', ''), 'role': row.get('role', 'other'),
                         'quantity': row.get('quantity', 1), 'size_mm': row.get('size_mm'), 'path': path})
    return rows


def planned(project, product, parts):
    """The images this listing gets: [{'kind', 'index', 'reason' or None when skipped}] in order."""
    has = {'main': True, 'includes': bool(parts), 'callouts': bool(product['features']), 'usecase': product['usecase']['enabled'],
           'feature': any(feature['parts'] for feature in product['features']), 'band': True,
           'specs': bool(product['specs']) or product_size(project, product)[1], 'benefits': len(product['features']) >= 3}
    why = {'includes': '还没有处理完成的组件', 'callouts': '还没有卖点', 'usecase': '没有启用使用场景', 'feature': '卖点没有选择对应的部件',
           'specs': '没有规格，也没有填写真实尺寸', 'benefits': '卖点少于 3 条'}
    wanted = product['pages'] if product['pages'] is not None else [kind for kind in AUTO_ORDER if has[kind]][:7]
    rows = []
    for kind in wanted:
        if not has[kind]:
            rows.append({'kind': kind, 'skipped': why.get(kind, '缺少内容')}); continue
        if kind == 'feature':
            index = next(index for index, feature in enumerate(product['features']) if feature['parts'])
            rows.append({'kind': kind, 'index': index})
        else:
            rows.append({'kind': kind, 'index': 0})
    return rows


# ---- saving ----

def _update(project_id, change):
    with LOCK:
        project = get('project', project_id)
        listing = dict(project.get('listing')) if isinstance(project.get('listing'), dict) else {}
        change(project, listing)
        project['listing'] = listing
        put('project', project)
        return project


def save_product(project_id, body):
    def change(project, listing):
        current = listing.get('product') if isinstance(listing.get('product'), dict) else None
        listing['product'] = validate_product(body, project, validate_product({}, project, current) if current else None)
    _update(project_id, change)
    return state(project_id)


def save_brand(project_id, body):
    def change(project, listing):
        listing['brand'] = brand_kit.validate(body, listing.get('brand') if isinstance(listing.get('brand'), dict) else None)
    _update(project_id, change)
    return state(project_id)


def save_logo(project_id, stream, filename):
    """Store an uploaded logo picture and point the brand at it."""
    from PIL import Image
    suffix = Path(filename or '').suffix.lower()
    if suffix not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError('标志请上传 PNG、JPG 或 WebP 图片')
    folder = DATA / project_id / 'listing' / 'brand'; folder.mkdir(parents=True, exist_ok=True)
    data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('标志图片不能超过 8 MB')
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load(); image = opened.convert('RGBA')
    except Exception:
        raise ValueError('标志图片无法读取') from None
    if max(image.size) > 2000:
        image.thumbnail((2000, 2000))
    target = folder / ('logo-%s.png' % uuid.uuid4().hex[:8]); image.save(target, 'PNG')
    return save_brand(project_id, {'logo': components.url(target)})


def draft(project_id, body):
    """Write the product record's words from the model's names and the user's notes (by the copy model when one is configured)."""
    import info_layer
    if not isinstance(body, dict):
        raise ValueError('请求格式无效')
    hint = body.get('hint', '')
    if not isinstance(hint, str) or len(hint) > info_layer.MAX_HINT:
        raise ValueError('要点说明不能超过 %d 字' % info_layer.MAX_HINT)
    project = get('project', project_id)
    if not project.get('analysis'):
        raise ValueError('请先等待模型分析完成')
    product, _, _ = stored(project)
    language = body.get('language', product['language'])
    if language not in LANGUAGES:
        raise ValueError('文字语言只能是 en（英文）或 zh（中文）')
    names = part_names(project)
    content, source, notes, _ = info_layer.content_for(project, hint.strip(), None, names, language)
    features = [{'title': point['title'], 'detail': point.get('detail', ''), 'parts': point.get('parts', []), 'icon': point.get('icon', '')}
                for point in content['points']][:MAX_FEATURES]
    listed = {row['label'].lower() for row in content['figures']}
    specs = [{'label': spec['label'], 'value': (spec['value'] + (' ' + spec['unit'] if spec.get('unit') else '')).strip()} for spec in content['specs']][:MAX_SPECS]
    figures = [{'value': row['value'], 'unit': row.get('unit', ''), 'label': row['label']} for row in content['figures']][:MAX_FIGURES]
    update = {'language': language, 'headline': content['headline'][:60], 'subline': content['subline'][:120]}
    # Words the user already wrote are kept; a draft only fills what is empty unless asked to replace.
    replace = body.get('replace') is True
    if replace or not product['features']:
        update['features'] = features
    if replace or not product['specs']:
        update['specs'] = specs
    if replace or not product['figures']:
        update['figures'] = figures
    if not replace:
        for key in ('headline', 'subline'):
            if product[key]:
                update.pop(key)

    def change(project, listing):
        current = validate_product({}, project, listing.get('product') if isinstance(listing.get('product'), dict) else None)
        record = validate_product(update, project, current)
        record['drafted'] = {'source': source, 'at': time.time(), 'notes': [str(note)[:200] for note in notes][:6]}
        listing['product'] = record
    _update(project_id, change)
    return state(project_id)


# ---- tasks ----

def _task_update(task_id, **changes):
    with LOCK:
        task = get(KIND, task_id)
        task.update(changes)
        return put(KIND, task)


def public_task(task):
    return {key: task.get(key) for key in ('id', 'project_id', 'status', 'stage', 'progress', 'detail', 'error', 'created_at', 'updated_at', 'pages')} if task else None


def latest(project_id):
    rows = [task for task in all_records(KIND) if task.get('project_id') == project_id]
    return public_task(max(rows, key=lambda task: task.get('created_at', 0))) if rows else None


def state(project_id):
    """Everything the listing workspace shows."""
    import engine
    project = get('project', project_id)
    product, brand_record, listing = stored(project)
    brand = brand_kit.Brand(brand_record, listing.get('product_color'))
    parts = ready_components(project)
    size_mm, known = product_size(project, product)
    return {'product': product, 'brand': brand.public(), 'presets': brand_kit.presets(),
            'components': [components.public(row) for row in components.components(project)],
            'roles': {key: {'zh': zh, 'en': en} for key, (zh, en) in components.ROLES.items()},
            'pages': listing.get('pages') or [], 'built_at': listing.get('built_at'), 'notes': listing.get('notes') or [],
            'plan': planned(project, product, parts), 'kinds': {key: {'zh': zh, 'en': en} for key, (zh, en) in listing_pages.KINDS.items()},
            'parts': part_names(project), 'size': {'mm': size_mm, 'known': known},
            'options': {'workpieces': list(WORKPIECES), 'materials': list(MATERIALS), 'modes': list(MODES), 'languages': list(LANGUAGES),
                        'workpiece_names': listing_pages.WORKPIECE_NAMES['zh'], 'material_names': listing_pages.MATERIAL_NAMES['zh']},
            'elements': {key: {'zh': zh, 'en': en} for key, (zh, en) in listing_elements.ELEMENTS.items()},
            'task': latest(project_id), 'ready': bool(project.get('analysis')) and project.get('status') == 'ready',
            'available': bool(engine.runtime_status().get('capabilities', {}).get('render', True))}


def submit(project_id, body=None):
    """Start building the listing's images. One build per project at a time."""
    import engine
    engine.require_runtime('render')
    with LOCK:
        project = get('project', project_id)
        if not project.get('analysis') or project.get('status') != 'ready':
            raise ValueError('请先等待模型分析完成')
        running = [task for task in all_records(KIND) if task.get('project_id') == project_id and task.get('status') in ('queued', 'running')]
        if running:
            raise engine.JobConflictError('这个项目的套图正在生成，请等待完成或先取消。')
        task = {'id': uuid.uuid4().hex, 'project_id': project_id, 'status': 'queued', 'stage': 'queued', 'progress': 0, 'detail': '', 'error': '',
                'created_at': time.time(), 'pages': 0}
        put(KIND, task)
    thread = threading.Thread(target=_run, args=(task['id'],), name='listing', daemon=True)
    TASKS[task['id']] = thread; thread.start()
    return public_task(task)


def cancel(project_id, task_id):
    with LOCK:
        task = get(KIND, task_id)
        if task.get('project_id') != project_id:
            raise KeyError(task_id)
        if task['status'] in ('queued', 'running'):
            task.update(status='cancelled', error='已取消'); put(KIND, task)
    process = components.PROCESSES.get(project_id)
    if process and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    return public_task(task)


def recover_interrupted():
    for task in all_records(KIND):
        if task.get('status') in ('queued', 'running'):
            task.update(status='failed', error='应用重启，套图生成已中断；请重新生成。'); put(KIND, task)


# ---- building ----

def _hero_view(project):
    shot = next((row for row in project['plan'].get('shots', []) if row.get('id') == 'hero'), {})
    azimuth = float(shot.get('azimuth', 35))
    return azimuth, min(24.0, max(10.0, float(shot.get('elevation', 18))))


def picture_requests(project, product, size_mm, rows):
    """The staged pictures these pages need: {name: request}."""
    plan = copy.deepcopy(project['plan']); model = project['model_path']; azimuth, elevation = _hero_view(project)
    kinds = {row['kind'] for row in rows if not row.get('skipped')}
    requests = {'hero': components.cutout_request(model, plan, size_mm, azimuth, elevation, 1500)}
    if 'specs' in kinds:
        import orientation
        measured = ((project.get('analysis') or {}).get('features') or {}).get('dimensions') or [1, 1, 1]
        dims = orientation.oriented_dimensions(measured, plan.get('rotation', [0, 0, 0]))
        requests['side'] = {'action': 'render', 'model_path': str(model), 'plan': plan, 'size_mm': float(size_mm),
                            'camera': {'azimuth': 0 if dims[0] >= dims[1] else 90, 'elevation': 0, 'projection': 'orthographic', 'margin': .06},
                            'ground': 'none', 'size': [1500, 1500], 'samples': 48}
    for row in rows:
        if row['kind'] == 'feature' and not row.get('skipped'):
            parts = product['features'][row['index']]['parts']
            requests['detail%d' % row['index']] = {
                'action': 'render', 'model_path': str(model), 'plan': plan, 'size_mm': float(size_mm),
                'camera': {'azimuth': azimuth, 'elevation': 22, 'focal_length': 85, 'margin': .2, 'focus': parts,
                           'azimuths': [azimuth, -azimuth, azimuth + 90, azimuth - 90, azimuth + 180]},
                'ground': 'none', 'size': [1300, 1300], 'samples': 48}
    if 'usecase' in kinds:
        use = product['usecase']
        workpiece = {'kind': use['kind'], 'material': use['material'],
                     'place': {'mode': use['mode'], 'side': use['side'], 'offset_mm': use['offset_mm'], 'rotate': use['rotate']}}
        for key in ('diameter_mm', 'width_mm', 'thickness_mm', 'length_mm'):
            if use.get(key):
                workpiece[key] = use[key]
        requests['usecase'] = {'action': 'render', 'model_path': str(model), 'plan': plan, 'size_mm': float(size_mm), 'workpiece': workpiece,
                               'camera': {'azimuth': azimuth, 'elevation': 20, 'focal_length': 85, 'margin': .15, 'auto': 'workpiece', 'offset': [0, .03]},
                               'ground': 'shadow' if use['mode'] in ('under', 'front') else 'none', 'size': [1600, 1600], 'samples': 48}
    return requests


def _photo(project):
    """A finished photograph of the product to put above a headline band: a generated scene, or a large studio render."""
    rows = [output for output in project.get('outputs', []) if output.get('quality') != 'thumbnail' and (output.get('width') or 0) >= 1000]
    scenes = [output for output in rows if output.get('quality') == 'generated']
    studio = [output for output in rows if output.get('shot_id') == 'campaign']
    for group in (scenes, studio):
        for output in sorted(group, key=lambda row: (row.get('version', 0), row.get('created_at') or 0), reverse=True):
            path = components.path_of(project['id'], output.get('png_url')) or components.path_of(project['id'], output.get('jpg_url'), ('.jpg', '.jpeg'))
            if path:
                return path
    return None


def _glyphs(product):
    import layer_icons
    found = {}
    for index, feature in enumerate(product['features']):
        for query in (feature.get('icon'), feature['title']):
            if not query:
                continue
            try:
                rows = layer_icons.search(query, 1)
            except Exception:
                rows = []
            if rows:
                glyph = layer_icons.glyph(rows[0]['id'])
                if glyph:
                    found[index] = glyph
                    break
    return found


def context(project, product, brand, parts, shots, size_mm, known):
    return {'brand': brand, 'product': product, 'language': product['language'], 'components': parts, 'shots': shots, 'product_mm': size_mm,
            'size_known': known, 'usecase': product['usecase'], 'photo': _photo(project), 'glyphs': _glyphs(product)}


def build(project_id, cancelled=lambda: False, progress=lambda stage, share, detail='': None):
    """Stage the pictures, lay out every page and write the PNG files. Returns the stored page rows and notes."""
    import info_layer
    project = get('project', project_id)
    product, brand_record, listing = stored(project)
    parts = ready_components(project); size_mm, known = product_size(project, product)
    rows = [row for row in planned(project, product, parts) if not row.get('skipped')]
    if not rows:
        raise ValueError('没有可以生成的图片。')
    requests = picture_requests(project, product, size_mm, rows)
    shots = {}; names = list(requests); notes = []
    waiting = [row.get('name') for row in components.components(project) if row.get('status') in ('queued', 'processing')]
    if waiting:
        notes.append('有 %d 个组件还在处理，本次套图没有包含：%s。' % (len(waiting), '、'.join(str(name) for name in waiting[:4])))
    for index, name in enumerate(names):
        if cancelled():
            raise ListingCancelled()
        progress('pictures', index / len(names), '渲染第 %d / %d 张素材' % (index + 1, len(names)))
        try:
            result = components.stage(project_id, requests[name], cancelled,
                                      lambda share, index=index: progress('pictures', (index + share) / len(names), '渲染第 %d / %d 张素材' % (index + 1, len(names))))
        except components.ComponentCancelled:
            raise ListingCancelled() from None
        picture = info_layer.Picture(result['probe'], result['path']) if info_layer._valid_probe(result.get('probe')) else None
        shots[name] = listing_pages.Shot(result, picture)
    product_color = brand_kit.dominant_color(shots['hero'].path)
    brand = brand_kit.Brand(brand_record, product_color)
    ctx = context(project, product, brand, parts, shots, size_mm, known)
    folder = DATA / project_id / 'listing' / ('build-' + uuid.uuid4().hex[:10]); folder.mkdir(parents=True)
    pages = []
    try:
        for index, row in enumerate(rows):
            if cancelled():
                raise ListingCancelled()
            progress('compose', index / len(rows), '排版第 %d / %d 张图片' % (index + 1, len(rows)))
            page = listing_pages.compose(row['kind'], ctx, row.get('index', 0))
            image = listing_draw.render(page, PAGE_PX)
            name = '%02d-%s.png' % (index + 1, row['kind'])
            image.save(folder / name, 'PNG', optimize=True)
            pages.append({'id': uuid.uuid4().hex[:10], 'kind': row['kind'], 'index': row.get('index', 0), 'file': name, 'url': components.url(folder / name),
                          'width': image.width, 'height': image.height, 'notes': page.get('notes', [])})
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if not known:
        notes.append('产品的真实尺寸没有填写，按模型文件的单位估计为最长边 %g mm，没有画出产品的尺寸线。' % size_mm)
    if brand.accent_source == 'product':
        notes.append('品牌色取自产品本身的颜色 %s；可在品牌设置里改成你的品牌色。' % brand.accent)

    def change(project, listing):
        listing.update(pages=pages, built_at=time.time(), notes=notes, product_color=product_color, folder=folder.name)
        listing.setdefault('product', product); listing.setdefault('brand', brand_record)
        for stale in (DATA / project_id / 'listing').glob('build-*'):
            if stale != folder:
                shutil.rmtree(stale, ignore_errors=True)
    _update(project_id, change)
    return pages, notes


def _run(task_id):
    task = get(KIND, task_id); project_id = task['project_id']

    def cancelled():
        try:
            return get(KIND, task_id).get('status') == 'cancelled'
        except KeyError:
            return True

    def progress(stage, share, detail=''):
        low = STAGES[stage]; high = min((value for value in STAGES.values() if value > low), default=100)
        if not cancelled():
            _task_update(task_id, status='running', stage=stage, progress=round(low + (high - low) * max(0.0, min(1.0, share))), detail=detail)
    try:
        _task_update(task_id, status='running')
        pages, _ = build(project_id, cancelled, progress)
        if not cancelled():
            _task_update(task_id, status='completed', stage='done', progress=100, detail='', pages=len(pages))
    except ListingCancelled:
        pass
    except KeyError:
        pass
    except Exception as exc:
        message = str(exc)[:300] if isinstance(exc, ValueError) and str(exc) else '套图生成失败，请重试。'
        try:
            if not cancelled():
                _task_update(task_id, status='failed', error=message)
        except KeyError:
            pass
    finally:
        TASKS.pop(task_id, None)


def archive(project_id):
    """The built pages as one ZIP, in order. Returns (bytes, file name)."""
    project = get('project', project_id)
    _, _, listing = stored(project)
    pages = listing.get('pages') or []
    files = [(page, components.path_of(project_id, page.get('url'))) for page in pages]
    files = [(page, path) for page, path in files if path]
    if not files:
        raise ValueError('还没有生成套图。')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as bundle:
        for page, path in files:
            bundle.write(path, page['file'])
    slug = re.sub(r'[^\w\-]+', '_', str(project.get('slug') or 'product'))[:40] or 'product'
    return buffer.getvalue(), slug + '-listing.zip'

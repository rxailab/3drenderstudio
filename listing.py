"""A product's listing: its record, its brand, and the set of images built from them.

The studio's shots are six views of one object. A listing is a sequence of arguments: what you
get, what is in the box, what the parts do, what it is like in use, how big it is. This module
keeps what those images are made from (the product record, the brand kit, the components) and
builds the set: it stages the pictures each page needs, has the image model make the usage scenes
around the locked product, lays the pages out with the brand-kit templates (``brandkit_set``) and
writes them as PNG files.

A page prints only numbers the user entered or that were measured on the model.
"""
import concurrent.futures
import copy
import hashlib
import io
import math
import json
import re
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path

from studio_store import DATA, LOCK, get, put, all_records

import articulation
import brand_kit
import brandkit_assets
import brandkit_render
import brandkit_review
import brandkit_set
import components

KIND = 'listing_task'
MAX_FEATURES = 8
MAX_SPECS = 10
MAX_FIGURES = 3
MAX_SOURCE = 8000
MAX_PAGES = len(brandkit_set.KINDS)
LANGUAGES = ('en', 'zh')
WORKPIECES = ('pipe', 'rod', 'board', 'sheet', 'block')
MATERIALS = ('pvc_white', 'pvc_grey', 'copper', 'steel', 'aluminium', 'wood', 'concrete', 'drywall', 'plastic_black')
MODES = ('through', 'under', 'front', 'manual')
WORKPIECE_NAMES = {'pipe': '管材', 'rod': '圆棒', 'board': '板材', 'sheet': '薄板', 'block': '块料'}
MATERIAL_NAMES = {'pvc_white': 'PVC（白）', 'pvc_grey': 'PVC（灰）', 'plastic_black': '塑料', 'copper': '铜', 'steel': '钢', 'aluminium': '铝',
                  'wood': '木', 'concrete': '混凝土', 'drywall': '石膏板'}
AUTO_ORDER = brandkit_set.AUTO
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
STAGES = {'queued': 0, 'pictures': 4, 'scenes': 30, 'compose': 82, 'done': 100}
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
            'features': [], 'specs': [], 'figures': [], 'pages': None, 'drafted': None, 'aspect': '1:1', 'scenes': brandkit_set.default_scenes(),
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
    if 'source' in body:            # the product description the user pasted, kept so it is there next time
        source = body['source']
        if source is not None and not isinstance(source, str):
            raise ValueError('产品介绍必须是文字')
        if source and len(source) > MAX_SOURCE:
            raise ValueError('产品介绍不能超过 %d 个字符' % MAX_SOURCE)
        record['source'] = (source or '').strip()
    else:
        record['source'] = current.get('source', '')
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
    if 'pages' in body:
        pages = body['pages']
        if pages is not None and (not isinstance(pages, list) or len(pages) > MAX_PAGES or any(kind not in brandkit_set.KINDS for kind in pages)):
            raise ValueError('图片类型列表无效')
    else:
        # A record kept from before the brand-kit templates names the old page kinds: those choices no longer apply.
        pages = current.get('pages')
        pages = [kind for kind in pages if kind in brandkit_set.KINDS] or None if isinstance(pages, list) else None
    record['pages'] = list(dict.fromkeys(pages)) if pages is not None else None
    aspect = body.get('aspect', current.get('aspect') or '1:1')
    if aspect not in brandkit_set.ASPECTS:
        raise ValueError('图片比例只能是 1:1 或 4:5')
    record['aspect'] = aspect
    record['usecase'] = validate_usecase(body.get('usecase'), current.get('usecase'))
    record['scenes'] = brandkit_set.validate_scenes(body.get('scenes'), current.get('scenes') if isinstance(current.get('scenes'), dict) else None)
    return record


def _view_key(project):
    """The model and the way the main picture shows it: points found on that picture hold while this stays the same."""
    plan = project.get('plan') or {}
    shot = next((row for row in plan.get('shots', []) if row.get('id') == 'hero'), {})
    key = [project.get('model_path'), plan.get('rotation'), shot.get('azimuth'), shot.get('elevation')]
    if plan and _flat_lay(project)[0]:
        # A flat lay's main picture is taken from FLAT_HERO_ELEVATION, not the hero shot's angle: points found on an
        # earlier picture from 24 degrees are not where the product is now. Only flat products get the extra entry, so
        # every other project keeps its key and its points.
        key.append(FLAT_HERO_ELEVATION)
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]


def stored(project):
    """(product record, brand record, listing dict) of a project, with defaults filled in."""
    listing = project.get('listing') if isinstance(project.get('listing'), dict) else {}
    try:
        product = validate_product({}, project, listing.get('product') if isinstance(listing.get('product'), dict) else None)
    except ValueError:
        product = default_product(project)
    # Every part of the model: a selling point that names them all is about the whole product (brandkit_set.anchored).
    product['_model_parts'] = part_names(project)
    product['_category'] = str((project.get('metadata') or {}).get('product_category') or '') if isinstance(project.get('metadata'), dict) else ''
    # Where the last build found selling points on the main picture, while the product is still shown the same way.
    placed = listing.get('placed') if isinstance(listing.get('placed'), dict) else {}
    if placed.get('view') == _view_key(project):
        whole = set(product['_model_parts'])
        for row in product['features']:
            found = (placed.get('features') or {}).get(row['title'])
            if found and not (row.get('parts') and not set(row['parts']) >= whole):
                row['point'] = found['point']
                if found.get('parts'):
                    row['parts'] = found['parts']
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
            entry = {'id': row['id'], 'name': row.get('name', ''), 'sku': row.get('sku', ''), 'caption': row.get('caption', ''), 'role': row.get('role', 'other'),
                     'quantity': row.get('quantity', 1), 'size_mm': row.get('size_mm'), 'path': path, 'kind': row.get('kind', 'model')}
            source = Path(row.get('source') or '')
            if entry['kind'] == 'model' and source.is_file():
                entry.update(model_path=str(source), rotation=row.get('rotation') or [0, 0, 0], finishes=row.get('finishes') or [])
            rows.append(entry)
    return rows


def planned(project, product, parts):
    """The images this listing gets: [{'kind', 'index', 'reason' or None when skipped}] in order. Kinds are brand-kit templates."""
    has, why = brandkit_set.requirements(product, parts, product_size(project, product)[1],
                                         brandkit_set.scene_supply(product, 1 if _photo(project) else 0), len(articulation.states(project)))
    # Left to itself the set has every page there is something for; the ones it cannot make yet are listed with the reason.
    wanted = product['pages'] if product['pages'] is not None else list(AUTO_ORDER)
    rows = []
    for kind in wanted:
        if not has.get(kind):
            rows.append({'kind': kind, 'skipped': why.get(kind, '缺少内容')})
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


def whole(text, limit):
    """A line cut short by the copy writer ('…') ends at its last whole word or clause instead."""
    text = ' '.join(str(text or '').split())
    if text.endswith(('…', '...')):
        text = text.rstrip('…').rstrip('.').rstrip()
        cut = max(text.rfind(mark) for mark in (',', '，', ';', '；', ' — ', ' - '))
        text = text[:cut] if cut > len(text) * .5 else text.rsplit(' ', 1)[0] if ' ' in text else text
        text = text.rstrip(' ,，;；-—')
    return text[:limit]


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
    features = [{'title': whole(point['title'], 40), 'detail': whole(point.get('detail', ''), 90), 'parts': point.get('parts', []), 'icon': point.get('icon', '')}
                for point in content['points']][:MAX_FEATURES]
    listed = {row['label'].lower() for row in content['figures']}
    specs = [{'label': spec['label'], 'value': (spec['value'] + (' ' + spec['unit'] if spec.get('unit') else '')).strip()} for spec in content['specs']][:MAX_SPECS]
    figures = [{'value': row['value'], 'unit': row.get('unit', ''), 'label': row['label']} for row in content['figures']][:MAX_FIGURES]
    update = {'language': language, 'headline': whole(content['headline'], 60), 'subline': whole(content['subline'], 120)}
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


EXTRACTOR = (
    'You read a product description a seller pasted (a product page, a spec sheet, notes) and sort what it says into the fields of '
    'a product record. Extract; do not write new copy. Use only what the text states: never add a feature, number, unit, material, '
    'certification or claim it does not contain. Keep the text\'s own words where they fit the length limits, shortened if needed; '
    'write every field in the output language (translate faithfully when the text is in another language). '
    'title: the product\'s short name as a listing headline prints it, 2 to 5 words ("Digital Caliper", "Cordless Angle Grinder"), '
    'without sizes, materials or slogans. model: the model or SKU code if given, else "". '
    'longest_side: the overall size along the WHOLE product\'s longest side as written in the text (value and unit, and quote: the '
    'exact words of the text you took it from), or empty strings when the text gives no overall size; the size of a part (a disc '
    'diameter, a jaw opening, a blade, a measuring range, a cutting capacity) is never the product\'s size. headline: the text\'s main selling sentence, shortened; subline: a second line that supports it. '
    'features: the distinct selling points, each a short title of 2 to 4 words that fits a callout label and a one-line detail, most important first; for each, parts lists '
    'the names from model_parts that show it (exact names, may be empty). specs: label/value pairs of technical data, value with '
    'its unit. figures: up to three numbers worth showing large (value, unit, label), each also present in specs or the text. '
    'notes: anything in the text you could not place, briefly (or nothing).')


def _numbers(text):
    """The numbers a piece of text contains, by value: full-width digits read as ordinary ones, a comma counted as a
    thousands separator only between groups of three ("1,200"; "16,20,25" is three numbers), "2.0" the same as "2"."""
    text = unicodedata.normalize('NFKC', str(text or ''))
    found = set()
    for match in re.findall(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?', text):
        value = float(match.replace(',', ''))
        found.add(('%f' % value).rstrip('0').rstrip('.'))
    return found


def _language_of(text):
    """'zh' for text that is mostly Chinese, else 'en'."""
    han = len(re.findall(r'[\u4e00-\u9fff]', text)); latin = len(re.findall(r'[A-Za-z]', text))
    return 'zh' if han >= latin / 3 else 'en'


UNIT_MM = {'mm': 1, '毫米': 1, 'cm': 10, '厘米': 10, '公分': 10, 'm': 1000, '米': 1000, 'in': 25.4, 'inch': 25.4, 'inches': 25.4, '"': 25.4,
           '″': 25.4, '英寸': 25.4, 'ft': 304.8, 'feet': 304.8, 'foot': 304.8, '英尺': 304.8}


def extract(project_id, body):
    """Sort a pasted product description into the product record by the text model, keeping only what the text says:
    a field with a number the text does not contain is left out. Fields the text fills replace what was there; the
    others are kept. Returns the listing state."""
    import brandkit_review
    if not isinstance(body, dict):
        raise ValueError('请求格式无效')
    text = body.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('请先粘贴一段产品介绍')
    text = text.strip()
    if len(text) > MAX_SOURCE:
        raise ValueError('产品介绍不能超过 %d 个字符' % MAX_SOURCE)
    config, _ = brandkit_review._configs()
    if not config:
        raise ValueError('没有配置文本模型，无法从介绍中提取。请在「AI 设置」里配置，或手动填写各栏。')
    project = get('project', project_id)
    product, _, _ = stored(project)
    # A language the user chose (the record already has words in it) is kept; a new record takes the text's own.
    chosen = product['title'] or product['headline'] or product['features']
    language = body.get('language') or (product['language'] if chosen else _language_of(text))
    if language not in LANGUAGES:
        raise ValueError('文字语言只能是 en（英文）或 zh（中文）')
    names = part_names(project)
    row = lambda fields: {'type': 'object', 'additionalProperties': False, 'required': list(fields), 'properties': {key: {'type': 'string'} for key in fields}}
    schema = {'type': 'object', 'additionalProperties': False,
              'required': ['title', 'model', 'longest_side', 'headline', 'subline', 'features', 'specs', 'figures', 'notes'],
              'properties': {'title': {'type': 'string'}, 'model': {'type': 'string'}, 'headline': {'type': 'string'}, 'subline': {'type': 'string'},
                             'longest_side': {'type': 'object', 'additionalProperties': False, 'required': ['value', 'unit', 'quote'],
                                              'properties': {'value': {'type': 'string'}, 'unit': {'type': 'string'}, 'quote': {'type': 'string'}}},
                             'features': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['title', 'detail', 'parts'],
                                                                     'properties': {'title': {'type': 'string'}, 'detail': {'type': 'string'},
                                                                                    'parts': {'type': 'array', 'items': {'type': 'string'}}}}},
                             'specs': {'type': 'array', 'items': row(('label', 'value'))},
                             'figures': {'type': 'array', 'items': row(('value', 'unit', 'label'))},
                             'notes': {'type': 'array', 'items': {'type': 'string'}}}}
    request = {'text': text, 'output_language': 'Simplified Chinese' if language == 'zh' else 'English', 'model_parts': names[:200],
               'limits_in_characters': {'title': 32, 'model': 40, 'headline': 60, 'subline': 120, 'feature_title': 24, 'feature_detail': 90,
                                        'spec_label': 30, 'spec_value': 30, 'figure_value': 12, 'figure_unit': 8, 'figure_label': 30},
               'most': {'features': MAX_FEATURES, 'specs': MAX_SPECS, 'figures': MAX_FIGURES}}
    try:
        answer = brandkit_review._ask(config, EXTRACTOR, request, schema)
    except Exception as exc:
        raise ValueError('文本模型没有完成提取：%s' % str(exc)[:160]) from None
    known = _numbers(text); dropped = []; notes = []

    def grounded(*values):
        return _numbers(' '.join(str(value) for value in values)) <= known

    def keep(label, *values):
        if grounded(*values):
            return True
        dropped.append(label); return False
    update = {'language': language, 'source': text}
    for key, limit in (('title', 60), ('model', 40), ('headline', 60), ('subline', 120)):
        value = whole(answer.get(key), limit)
        if value and keep(key, value):
            update[key] = value
    features = [{'title': whole(item.get('title'), 40), 'detail': whole(item.get('detail'), 90),
                 'parts': list(dict.fromkeys(name for name in item.get('parts') or [] if name in names))[:40]}
                for item in answer.get('features') or [] if isinstance(item, dict)]
    features = [item for item in features if item['title'] and keep('卖点「%s」' % item['title'], item['title'], item['detail'])][:MAX_FEATURES]
    specs = [{'label': whole(item.get('label'), 30), 'value': whole(item.get('value'), 30)} for item in answer.get('specs') or [] if isinstance(item, dict)]
    specs = [item for item in specs if item['label'] and item['value'] and keep('规格「%s」' % item['label'], item['label'], item['value'])][:MAX_SPECS]
    figures = [{'value': whole(item.get('value'), 12), 'unit': whole(item.get('unit'), 8), 'label': whole(item.get('label'), 30)}
               for item in answer.get('figures') or [] if isinstance(item, dict)]
    figures = [item for item in figures if item['value'] and item['label'] and keep('指标「%s」' % item['label'], item['value'], item['label'])][:MAX_FIGURES]
    for key, rows in (('features', features), ('specs', specs), ('figures', figures)):
        if rows:
            update[key] = rows
    side = answer.get('longest_side') or {}
    if isinstance(side, dict) and side.get('value'):
        unit = str(side.get('unit', '')).strip().lower(); value = str(side['value']).replace(',', '').strip()
        try:
            number = float(value)
        except ValueError:
            number = None
        unit = unicodedata.normalize('NFKC', unit) if unit not in UNIT_MM else unit
        quote = ' '.join(str(side.get('quote', '')).split())
        overall = re.search(r'overall|total|length|long|size|dimension|整机|总长|全长|长度|尺寸|外形|长', quote, re.I)
        part = re.search(r'disc|disk|blade|jaw|range|capacity|diameter|opening|cut|thread|片|口|径|范围|容量|行程', quote, re.I)
        if number and (not quote or quote not in ' '.join(text.split()) or not overall or (part and not re.search(r'overall|total|整机|总长|全长', quote, re.I))):
            notes.append('介绍里没有写明整件产品的长度，最长边尺寸请手动填写。')
        elif number and not _numbers(value) <= known:
            dropped.append('最长边尺寸')
        elif number and unit not in UNIT_MM:
            notes.append('最长边尺寸的单位「%s」无法识别，请手动填写毫米数。' % str(side.get('unit', ''))[:12])
        elif number and 1 <= number * UNIT_MM[unit] <= 100000:
            update['size_mm'] = round(number * UNIT_MM[unit], 1)
        elif number:
            notes.append('提取到的最长边尺寸超出范围，没有采用。')
    model_notes = [str(note)[:200] for note in answer.get('notes') or [] if str(note).strip()][:3]
    if dropped:
        notes.insert(0, '以下内容含有介绍里没有的数字，已略去：%s。' % '、'.join(dropped[:6]))
    notes += model_notes

    def change(project, listing):
        current = validate_product({}, project, listing.get('product') if isinstance(listing.get('product'), dict) else None)
        record = validate_product(update, project, current)
        record['drafted'] = {'source': 'extracted', 'at': time.time(), 'notes': notes[:6]}
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


def _scene_options():
    import scene_audience
    try:
        audiences = [{'id': row['id'], 'label': row['label']} for row in scene_audience.library()]
    except Exception:
        audiences = []
    return {'audiences': audiences, 'image_ready': brandkit_set.image_model_ready(), 'max': brandkit_set.MAX_SCENES,
            'people': {'auto': '自动', 'hands': '只出现手部', 'include': '包含人物', 'exclude': '不含人物'},
            'aspects': list(brandkit_set.ASPECTS)}


def state(project_id):
    """Everything the listing workspace shows."""
    import engine
    project = get('project', project_id)
    product, brand_record, listing = stored(project)
    brand = brand_kit.Brand(brand_record, listing.get('product_color'))
    parts = ready_components(project)
    size_mm, known = product_size(project, product)
    return {'product': product, 'brand': brand.public(), 'presets': brand_kit.presets(), 'font_pairings': brandkit_assets.pairings(), 'elements': brand_kit.ELEMENTS,
            'components': [components.public(row) for row in components.components(project)],
            'roles': {key: {'zh': zh, 'en': en} for key, (zh, en) in components.ROLES.items()},
            'pages': listing.get('pages') or [], 'built_at': listing.get('built_at'), 'notes': listing.get('notes') or [],
            'plan': planned(project, product, parts), 'kinds': {key: {'zh': zh, 'en': en} for key, (zh, en) in brandkit_set.KINDS.items()},
            'order': list(brandkit_set.ORDER), 'auto': list(AUTO_ORDER),
            'scenes': [{key: row.get(key) for key in ('url', 'title', 'body', 'role')} for row in (listing.get('scenes') or {}).get('items', [])],
            'scene_options': _scene_options(),
            'layout_available': brandkit_render.available(),
            'parts': part_names(project), 'size': {'mm': size_mm, 'known': known},
            'options': {'workpieces': list(WORKPIECES), 'materials': list(MATERIALS), 'modes': list(MODES), 'languages': list(LANGUAGES),
                        'workpiece_names': WORKPIECE_NAMES, 'material_names': MATERIAL_NAMES},
            'task': latest(project_id), 'ready': bool(project.get('analysis')) and project.get('status') == 'ready',
            'available': bool(engine.runtime_status().get('capabilities', {}).get('render', True))}


def submit(project_id, body=None):
    """Start building the listing's images. One build per project at a time.
    ``body['fresh']`` true asks the copy and the reviews again instead of reusing the answers to the same requests
    (brandkit_review keeps them), for when the user wants new headlines."""
    import engine
    engine.require_runtime('render')
    fresh = isinstance(body, dict) and body.get('fresh') is True
    with LOCK:
        project = get('project', project_id)
        if not project.get('analysis') or project.get('status') != 'ready':
            raise ValueError('请先等待模型分析完成')
        running = [task for task in all_records(KIND) if task.get('project_id') == project_id and task.get('status') in ('queued', 'running')]
        if running:
            raise engine.JobConflictError('这个项目的套图正在生成，请等待完成或先取消。')
        task = {'id': uuid.uuid4().hex, 'project_id': project_id, 'status': 'queued', 'stage': 'queued', 'progress': 0, 'detail': '', 'error': '',
                'created_at': time.time(), 'pages': 0, 'fresh': fresh}
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

TOOL_WORDS = ('grinder', 'saw', 'cutter', 'wrench', 'plier', 'screwdriver', 'driver', 'hammer', 'sander', 'planer', 'router', 'blower',
              'trimmer', 'torch', 'gun', 'file', 'chisel', 'knife', 'tool', '角磨', '磨光', '锯', '刀', '钳', '扳手', '起子', '螺丝刀', '锤',
              '砂光', '打磨', '电刨', '吹风', '修边', '割草', '工具', '切割', '电动')
UPRIGHT_LIMIT = 1.45     # a tool this many times taller than it is long or wide is shown lying level, as listings show it


def display_rotation(project):
    """The pose a listing shows the product in. A long tool that the pose check stood on end (a grinder on its battery)
    is laid level, its length across the picture, as tool listings show it; anything else keeps the project's pose.

    Returns (rotation, laid): ``laid`` says whether it was turned.
    """
    import orientation
    rotation = [float(value) for value in project['plan'].get('rotation', [0, 0, 0])]
    metadata = project.get('metadata') if isinstance(project.get('metadata'), dict) else {}
    name = ' '.join(str(value) for value in (project.get('name', ''), metadata.get('product_category', ''))).lower()
    measured = ((project.get('analysis') or {}).get('features') or {}).get('dimensions')
    if not measured or not any(word in name for word in TOOL_WORDS):
        return rotation, False
    width, depth, height = orientation.oriented_dimensions(measured, rotation)
    if height < UPRIGHT_LIMIT * max(width, depth):
        return rotation, False
    # A quarter turn about the level line from the product to the camera, as if the picture itself were turned: the side
    # that faced the camera still faces it (a grinder's disc stays face-on), its length now runs across, its top to the left.
    azimuth = math.radians(_hero_view(project)[0])
    ux, uy = math.sin(azimuth), -math.cos(azimuth)
    turn = [[ux * ux, ux * uy, uy], [ux * uy, uy * uy, -ux], [-uy, ux, 0.0]]          # 90 degrees about (ux, uy, 0)
    laid = orientation.then(rotation, orientation.euler(turn))
    # Then about the vertical, so its own length, depth and height line up with the scene's axes again: it is measured true,
    # and the listing camera sees it from the same three-quarter angle the product was first shown from.
    return orientation.then(laid, [0, 0, -math.degrees(azimuth)]), True


def _hero_view(project):
    shot = next((row for row in project['plan'].get('shots', []) if row.get('id') == 'hero'), {})
    azimuth = float(shot.get('azimuth', 35))
    return azimuth, min(24.0, max(10.0, float(shot.get('elevation', 18))))


def _focus_features(product, limit):
    """[(feature index, parts)] of the first selling points that name parts of the model."""
    return [(index, product['features'][index]['parts']) for index in brandkit_set.anchored(product)][:limit]


def _light_share(path):
    """The share of a cut-out render's product pixels that are near white (bright steel, white plastic)."""
    from PIL import Image
    try:
        with Image.open(path) as opened:
            image = opened.convert('RGBA'); image.thumbnail((360, 360))
    except OSError:
        return 0.0
    pixels = [pixel for pixel in image.getdata() if pixel[3] > 200]
    return sum(1 for pixel in pixels if min(pixel[:3]) > 185) / max(1, len(pixels))


FLAT_HERO_ELEVATION = 60.0      # the listing's main picture of a product lying flat; the T06 outline stays straight down (89)


def _flat(dims):
    """A product lying flat: its height well under the shorter of its two sides on the ground."""
    return bool(dims) and float(dims[2]) < .4 * min(float(dims[0]), float(dims[1]))


def _flat_lay(project):
    """(whether the listing shows the product lying flat, the rotation it is shown in, its dimensions in that pose)."""
    import orientation
    rotation, _ = display_rotation(project)
    measured = ((project.get('analysis') or {}).get('features') or {}).get('dimensions') or [1, 1, 1]
    dims = orientation.oriented_dimensions(measured, rotation)
    return _flat(dims), rotation, dims


def picture_requests(project, product, size_mm, rows, parts=()):
    """The staged pictures these pages need: {name: request}."""
    plan = copy.deepcopy(project['plan']); model = project['model_path']; azimuth, elevation = _hero_view(project)
    flat, plan['rotation'], dims = _flat_lay(project)
    kinds = {row['kind'] for row in rows if not row.get('skipped')}
    if flat:
        # A tool lying flat (a saw, a wrench) is shown as a flat lay, its big face to the camera: seen from 24 degrees its
        # blade is a sliver that mirrors the bright studio behind it and vanishes into the white.
        elevation = FLAT_HERO_ELEVATION
    requests = {'hero': components.cutout_request(model, plan, size_mm, azimuth, elevation, 1800)}
    if 'T06' in kinds:
        # A tool lying flat (pliers, a caliper) is measured from above, where its outline is; anything else from the side.
        side_elevation = 89 if flat else 0
        requests['side'] = {'action': 'render', 'model_path': str(model), 'plan': plan, 'size_mm': float(size_mm),
                            'camera': {'azimuth': 0 if dims[0] >= dims[1] else 90, 'elevation': side_elevation, 'projection': 'orthographic', 'margin': .06},
                            'ground': 'none', 'size': [1500, 1500], 'samples': 48}
    # Selling points found on the main picture are shown close up by cropping round the spot in the same view at twice the size.
    if kinds & {'T03', 'T02'} and any(product['features'][index].get('point') for index, _ in _focus_features(product, 6)):
        # the hero's own camera at twice the size, so a point found on the hero is the same spot here
        requests['zoom'] = {**copy.deepcopy(requests['hero']), 'size': [3600, 3600]}
    # Close-ups of the parts the selling points name: a medium one for the detail column, a tight one for the dark close-ups.
    for key, kind, limit, focal, margin, pixels in (('detail', 'T03', 6, 85, .2, 1600), ('macro', 'T02', 2, 120, .06, 2200)):
        if kind in kinds:
            for index, names in _focus_features(product, limit):
                if product['features'][index].get('point'):
                    continue
                requests['%s%d' % (key, index)] = {
                    'action': 'render', 'model_path': str(model), 'plan': plan, 'size_mm': float(size_mm),
                    'camera': {'azimuth': azimuth, 'elevation': 22, 'focal_length': focal, 'margin': margin, 'focus': names,
                               'azimuths': [azimuth, -azimuth, azimuth + 90, azimuth - 90, azimuth + 180]},
                    'ground': 'none', 'size': [pixels, pixels], 'samples': 48}
    if 'T13' in kinds:
        requests.update(pose_requests(project, requests['hero']))
    if product['usecase']['enabled'] and kinds & {'T04', 'T08', 'T12'}:
        use = product['usecase']
        workpiece = {'kind': use['kind'], 'material': use['material'],
                     'place': {'mode': use['mode'], 'side': use['side'], 'offset_mm': use['offset_mm'], 'rotate': use['rotate']}}
        for key in ('diameter_mm', 'width_mm', 'thickness_mm', 'length_mm'):
            if use.get(key):
                workpiece[key] = use[key]
        requests['usecase'] = {'action': 'render', 'model_path': str(model), 'plan': plan, 'size_mm': float(size_mm), 'workpiece': workpiece,
                               'camera': {'azimuth': azimuth, 'elevation': 20, 'focal_length': 85, 'margin': .15, 'auto': 'workpiece', 'offset': [0, -.02]},
                               'ground': 'shadow', 'size': [1600, 1600], 'samples': 48}
    return requests


def pose_requests(project, hero):
    """The product in each state of its moving parts (the pose agent's, ``articulation``), from the main picture's camera:
    {'pose_<state id>': request}, or {} with fewer than two states. Every pose carries the envelope of all states, so the
    camera frames them alike (one scale, one framing) and the pictures can be compared side by side."""
    states = articulation.states(project)
    if len(states) < 2:
        return {}
    envelope = [articulation.plan_for(project, state['id']) or {} for state in states]
    requests = {}
    for state in states:
        request = copy.deepcopy(hero)
        request['plan']['articulation_envelope'] = copy.deepcopy(envelope)
        posed = articulation.plan_for(project, state['id'])
        if posed:
            request['plan']['articulation'] = posed
        requests['pose_' + state['id']] = request
    return requests


def _pose_label(state, language):
    """A state's name in the page's language (the pose agent writes both)."""
    label = state.get('label_zh') if language == 'zh' else state.get('label_en')
    return ' '.join(str(label or '').split())


def pose_agent_ready():
    """Whether the pose agent can look at a model: it needs the vision model."""
    return brandkit_review.available()[1]


def _moving_parts(project_id, project, product, size_mm, cancelled, say):
    """The pose agent's record of this model's moving parts (``articulation.analyse``), worked out once per model file when a
    vision model can look at it; a stored record is used as long as the model file is the same. Returns (project, notes):
    the project as the build should see it (without a record that does not hold for this model) and notes in Chinese.
    The set is built without poses whenever this fails."""
    record = project.get('articulation') if isinstance(project.get('articulation'), dict) else None
    if not pose_agent_ready() or project_id in articulation.RUNNING:      # (already being asked from the 活动部件 tab: its last answer)
        try:
            fresh = bool(record) and record.get('key') == articulation.key_of(project, size_mm)
        except (OSError, KeyError, TypeError, ValueError):
            fresh = False
        return (project if fresh else {**project, 'articulation': None}), []
    notes = []
    articulation.RUNNING[project_id] = True
    try:
        record = articulation.analyse(project_id, product, size_mm, cancelled, say)
    except components.ComponentCancelled:
        raise ListingCancelled() from None
    except Exception as exc:
        return {**project, 'articulation': None}, ['活动部件识别失败（%s），套图按静止姿态生成。' % str(exc)[:120]]
    finally:
        articulation.RUNNING.pop(project_id, None)
    if record.get('status') == 'failed':
        notes.append('活动部件的姿态没有通过检查，套图按静止姿态生成%s' % ('：' + '；'.join(str(note) for note in record.get('notes') or [])[:200]
                                                              if record.get('notes') else '。'))
    return {**get('project', project_id), 'articulation': record}, notes


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


def _oriented_mm(project, plan_rotation, size_mm):
    """The product's width, depth and height in mm, in the pose the listing shows it."""
    import orientation
    dims = project['plan'].get('dimensions_mm')
    measured = ((project.get('analysis') or {}).get('features') or {}).get('dimensions') or [1, 1, 1]
    oriented = orientation.oriented_dimensions(measured, plan_rotation)
    if isinstance(dims, list) and len(dims) == 3 and max(dims) > 0:
        scale = max(dims) / max(oriented)
    else:
        scale = size_mm / max(oriented)
    return [value * scale for value in oriented]


def _scene_on_white(source, target):
    """A render with a transparent background (the workpiece shot) as a scene picture: on a soft studio ground."""
    from PIL import Image
    with Image.open(source) as opened:
        image = opened.convert('RGBA')
    ground = Image.new('RGBA', image.size, (236, 236, 232, 255)); ground.alpha_composite(image)
    ground.convert('RGB').save(target, 'PNG')
    return target


def build(project_id, cancelled=lambda: False, progress=lambda stage, share, detail='': None, fresh=False):
    """Stage the pictures, make the usage scenes, lay out every page with the brand-kit templates and write the PNG files.
    ``fresh`` asks the copy and the reviews again rather than reusing kept answers. Returns the stored page rows and notes."""
    import info_layer
    if not brandkit_render.available():
        raise ValueError('没有找到 Chrome 或 Edge 浏览器，无法排版品牌模板。请安装 Chrome，或在 .env 中设置 BRANDKIT_CHROME。')
    project = get('project', project_id)
    product, brand_record, listing = stored(project)
    parts = ready_components(project); size_mm, known = product_size(project, product)
    # Which parts of the model move and the states worth showing (pliers closed / open): asked once per model file.
    project, notes = _moving_parts(project_id, project, product, size_mm, cancelled, lambda text: progress('pictures', 0, text))
    rows = [row for row in planned(project, product, parts) if not row.get('skipped')]
    if not rows:
        raise ValueError('没有可以生成的图片。')
    kinds = {row['kind'] for row in rows}
    requests = picture_requests(project, product, size_mm, rows, parts)
    shots = {}; names = list(requests)
    waiting = [row.get('name') for row in components.components(project) if row.get('status') in ('queued', 'processing')]
    if waiting:
        notes.append('有 %d 个组件还在处理，本次套图没有包含：%s。' % (len(waiting), '、'.join(str(name) for name in waiting[:4])))
    located = listing.get('located') if isinstance(listing.get('located'), dict) else {}
    refused = False                  # the AI service refused (quota, key): nothing more is asked of it in this build
    index = 0
    while index < len(names):
        name = names[index]
        if cancelled():
            raise ListingCancelled()
        progress('pictures', index / len(names), '渲染第 %d / %d 张素材' % (index + 1, len(names)))
        try:
            result = components.stage(project_id, requests[name], cancelled,
                                      lambda share, index=index: progress('pictures', (index + share) / len(names), '渲染第 %d / %d 张素材' % (index + 1, len(names))))
        except components.ComponentCancelled:
            raise ListingCancelled() from None
        picture = info_layer.Picture(result['probe'], result['path']) if info_layer._valid_probe(result.get('probe')) else None
        shots[name] = {'result': result, 'path': result['path'], 'picture': picture}
        index += 1
        if name == 'hero':
            # Selling points the model's part names do not place are found on the main picture; the pages are planned again.
            features, found = brandkit_set.locate(result['path'], picture, product, located)
            refused = any(re.search(r'HTTP 40[123]', note) for note in found)
            if features != product['features']:
                product = {**product, 'features': features}; notes += found
                rows = [row for row in planned(project, product, parts) if not row.get('skipped')]
                kinds = {row['kind'] for row in rows}
                requests = {**picture_requests(project, product, size_mm, rows, parts), 'hero': requests['hero']}
                names = ['hero'] + [key for key in requests if key != 'hero']
            elif found:
                notes += found
    product_color = brand_kit.dominant_color(shots['hero']['path'])
    brand = brand_kit.Brand(brand_record, product_color)
    brand.logo_path = components.path_of(project_id, brand_record.get('logo'))

    # Usage scenes: made by the image model around the locked product, kept while the settings stay the same.
    scenes = []; scene_store = listing.get('scenes') if isinstance(listing.get('scenes'), dict) else None; scene_key = None
    if kinds & {'T12', 'T04', 'T08'}:
        key = brandkit_set.scene_key(project, product, product['scenes'])
        scene_folder = DATA / project_id / 'listing' / ('scenes-' + key)

        def say(index, total, detail):
            progress('scenes', index / max(1, total), detail)
        try:
            scenes, scene_key, scene_notes = brandkit_set.make_scenes(project, product, scene_folder, cancelled, say, scene_store)
        except InterruptedError:
            raise ListingCancelled() from None
        notes += scene_notes
    folder = DATA / project_id / 'listing' / ('build-' + uuid.uuid4().hex[:10]); folder.mkdir(parents=True)
    assets_folder = folder / 'assets'; assets_folder.mkdir()
    if 'usecase' in shots:
        path = _scene_on_white(shots['usecase']['path'], assets_folder / 'usecase-scene.png')
        title = product['usecase']['headline'] or ('使用中' if product['language'] == 'zh' else 'In use')
        scenes.append({'path': str(path), 'title': title, 'body': '', 'role': 'workpiece', 'focus': [.5, .5]})
    if not scenes and kinds & {'T04', 'T08'} and _photo(project):
        scenes.append({'path': str(_photo(project)), 'title': brandkit_set.TEXT[product['language']]['scene_titles'][0], 'body': '',
                       'role': 'earlier', 'focus': [.5, .55]})
    pages = []
    try:
        progress('compose', 0, '准备排版素材')
        assets = {}; anchors = []; alternatives = {}; details = []; macros = []; focus = {}
        hero, crop = brandkit_set.flatten(shots['hero']['path'], assets_folder / 'hero.png')
        assets['hero'] = hero
        picture = shots['hero']['picture']
        for index in brandkit_set.pointed(product):          # found on the picture by the vision model, no part of its own
            point = brandkit_set.remap(product['features'][index]['point'], crop)
            if 0 <= point[0] <= 1 and 0 <= point[1] <= 1:
                anchors.append((index, point))
        for index in brandkit_set.anchored(product):
            if product['features'][index].get('point'):     # found on the picture: the spot itself, not the middle of its mesh
                point = brandkit_set.remap(product['features'][index]['point'], crop)
                if 0 <= point[0] <= 1 and 0 <= point[1] <= 1:
                    anchors.append((index, point))
                continue
            found, share = picture.anchor(product['features'][index]['parts']) if picture else (None, 0)
            if found and share > .0003:          # a small part (a rivet, a button) still gets its line
                point = brandkit_set.remap(found, crop)
                if 0 <= point[0] <= 1 and 0 <= point[1] <= 1:
                    others = [brandkit_set.remap(other, crop) for other in picture.other_anchors(product['features'][index]['parts'])]
                    alternatives[index] = [other for other in others if 0 <= other[0] <= 1 and 0 <= other[1] <= 1]
                    anchors.append((index, point))
        # In selling-point order; two points on the same spot (a model in one piece) would stack their callouts: one is kept.
        kept = []
        for index, point in sorted(anchors):
            if all(math.hypot(point[0] - other[0], point[1] - other[1]) >= .05 for _, other in kept):
                kept.append((index, point))
        anchors = kept
        # Close-ups cut from the zoom: only when it was taken from the hero's camera (the points are the hero's), and one
        # per spot: a point inside half a window of one already shown would give the same picture twice.
        same_view = 'zoom' in shots and all(shots['zoom']['result'].get(key) == shots['hero']['result'].get(key) for key in ('azimuth', 'elevation'))
        taken = {'detail': [], 'macro': []}
        for index, names in _focus_features(product, 6):
            point = product['features'][index].get('point')
            if point and 'zoom' in shots and same_view is False:
                notes.append('特写放大图与主图视角不一致，未用它裁特写。'); same_view = None
            if point and same_view:
                zoom = shots['zoom']['result']
                for key, side, kind_list, ground in (('detail', .3, details, (38, 41, 45)), ('macro', .22, macros, (22, 24, 27))):
                    if key == 'macro' and len(macros) >= 2:
                        continue
                    if any(math.hypot(point[0] - other[0], point[1] - other[1]) < side / 2 for other in taken[key]):
                        continue
                    taken[key].append(point)
                    name = '%s%d' % (key, index)
                    half = side / 2; x = min(max(point[0], half), 1 - half); y = min(max(point[1], half), 1 - half)
                    region = (round(x - half, 4), round(y - half, 4), round(x + half, 4), round(y + half, 4))
                    assets[name], _ = brandkit_set.flatten(shots['zoom']['path'], assets_folder / (name + '.png'), ground=ground, dark=True,
                                                           trim=False, region=region)
                    assets[name]['_parts'] = brandkit_set.part_map(zoom, region)
                    assets[name]['_centred'] = True          # cut round the feature's own spot: recrops stay near it
                    focus[name] = [round((point[0] - region[0]) / side, 3), round((point[1] - region[1]) / side, 3)]
                    assets[name]['_part_box'] = [max(0, focus[name][0] - .25), max(0, focus[name][1] - .25), min(1, focus[name][0] + .25), min(1, focus[name][1] + .25)]
                    kind_list.append((index, name))
                continue
            if 'detail%d' % index in shots:
                name = 'detail%d' % index
                region = brandkit_set.part_region(shots[name]['result'], names, least=.36, margin=.25, inset=.04)
                # On the detail page's dark rail (its only layout here), as studio shots on the rail's own tone, not white cards.
                assets[name], _ = brandkit_set.flatten(shots[name]['path'], assets_folder / (name + '.png'), ground=(38, 41, 45), dark=True,
                                                       trim=False, region=region)
                assets[name]['_parts'] = brandkit_set.part_map(shots[name]['result'], region)
                assets[name]['_part_box'] = brandkit_set.part_box(shots[name]['result'], names, region)
                focus[name] = brandkit_set.part_focus(shots[name]['result'], names, region)
                details.append((index, name))
            if 'macro%d' % index in shots:
                name = 'macro%d' % index
                region = brandkit_set.part_region(shots[name]['result'], names, least=.34, margin=.15, inset=.1)   # the dark close-up frames fade out
                assets[name], _ = brandkit_set.flatten(shots[name]['path'], assets_folder / (name + '.png'), ground=(22, 24, 27), dark=True, trim=False,
                                                       region=region)
                assets[name]['_parts'] = brandkit_set.part_map(shots[name]['result'], region)
                focus[name] = brandkit_set.part_focus(shots[name]['result'], names, region)
                macros.append((index, name))
        if 'side' in shots:
            assets['side'], _ = brandkit_set.flatten(shots['side']['path'], assets_folder / 'side.png', pad=0)
        for index, part in enumerate(parts):
            assets['part%d' % index], _ = brandkit_set.flatten(part['path'], assets_folder / ('part%d.png' % index), pad=.04)
        # The poses, cut with one window so they stay at one scale, named in the page's language.
        posed = [state for state in articulation.states(project) if 'pose_' + state['id'] in shots]
        poses = []
        if len(posed) >= 2:
            fields = brandkit_set.pose_assets([shots['pose_' + state['id']]['path'] for state in posed], assets_folder / 'poses')
            for state, field in zip(posed, fields):
                assets['pose_' + state['id']] = field
                poses.append({'asset': 'pose_' + state['id'], 'label': _pose_label(state, product['language'])})
        for index, scene in enumerate(scenes):
            assets['scene%d' % (index + 1)] = brandkit_set.scene_asset(scene['path'], assets_folder / ('scene%d.png' % (index + 1)))
            product_box = brandkit_set.scene_box(scene)
            if product_box:
                assets['scene%d' % (index + 1)]['_product'] = product_box
        # The record's own words in the language the pages are printed in (translated once, kept with the project).
        translations = listing.get('translations') if isinstance(listing.get('translations'), dict) else {}
        written, made_scenes = product, scenes          # kept as the user wrote them; only the pages use the translation
        product, scenes, parts, localised = brandkit_set.localise(product, scenes, parts, translations)
        notes += localised
        if poses:
            product = {**product, '_poses': [row['label'] for row in poses if row['label']]}      # facts for the copywriter and the review
        ctx = {'product': product, 'language': product['language'], 'aspect': product['aspect'], 'brand': brand, 'assets': assets,
               'anchors': anchors, 'alternatives': alternatives, 'details': details, 'macros': macros, 'focus': focus, 'kinds': kinds, 'scenes': scenes, 'parts': parts, 'unit': brand.unit, 'poses': poses,
               'dims_mm': _oriented_mm(project, display_rotation(project)[0], size_mm) if known else None}
        ctx['flat'] = bool(ctx['dims_mm']) and _flat(ctx['dims_mm'])
        ctx['light_product'] = _light_share(shots['hero']['path']) > .35
        ctx['facts'] = brandkit_review.facts_of(product, dict(zip(('width', 'depth', 'height'), ctx['dims_mm'])) if ctx['dims_mm'] else None)
        has, why = brandkit_set.requirements(product, parts, known, len(scenes), len(poses))
        has['T01'] = has['T01'] and len(anchors) >= 2; has['T03'] = has['T03'] and len(details) >= 2; has['T02'] = has['T02'] and bool(macros)
        why.update(T01='选中的部件在主图里看不到（至少需要 2 处）', T03='没有渲染出 2 张以上的部件特写', T02='没有渲染出部件特写',
                   T13='活动部件的姿态图没有渲染出 2 张以上')
        work = folder / '_layout'
        made = [row for row in rows if has.get(row['kind'])]
        for row in rows:
            if not has.get(row['kind']):
                notes.append('「%s」没有生成：%s。' % (brandkit_set.KINDS[row['kind']][0], why.get(row['kind'], '缺少内容')))
        # Supervision: the copy model writes each page's lines, the vision model reviews every finished page.
        text_ready, vision_ready = brandkit_review.available()
        if text_ready:
            progress('compose', 0, '文案模型撰写各页标题')
            with brandkit_review.fresh_answers(fresh):
                ctx['copy'], copy_note = brandkit_review.write_copy(product, [row['kind'] for row in made])
            if copy_note:
                notes.append(copy_note)
        ctx['review'] = vision_ready
        if refused:
            ctx['review_refused'] = '额度或密钥被拒绝'
        if not vision_ready:
            notes.append('视觉模型未配置：各页只做了版式测量，没有经过视觉审核。')
        laid = {}; done = [0]; lock = threading.Lock()

        def one(row):
            if cancelled():
                raise ListingCancelled()
            kind = row['kind']
            try:
                with brandkit_review.fresh_answers(fresh):          # set in this worker thread: it does not inherit the build's
                    result = brandkit_set.lay_out(kind, ctx, folder / ('%s.png' % kind), work)
            except brandkit_render.RenderError:
                raise
            except Exception as exc:
                result = exc
            with lock:
                done[0] += 1
                progress('compose', done[0] / len(made), '已排好 %d / %d 张%s' % (done[0], len(made), '（含视觉审核）' if vision_ready else ''))
            return kind, result
        progress('compose', 0, '排版 %d 张图片%s' % (len(made), '，并逐张视觉审核' if vision_ready else ''))
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for kind, result in pool.map(one, made):
                laid[kind] = result
        for row in made:
            kind = row['kind']; result = laid[kind]
            if isinstance(result, Exception):
                notes.append('「%s」排版失败：%s' % (brandkit_set.KINDS[kind][0], str(result)[:160])); continue
            qa, page_notes, review = result
            name = '%02d-%s.png' % (len(pages) + 1, kind)
            (folder / ('%s.png' % kind)).rename(folder / name)
            vector = folder / 'svg' / ('%s.svg' % kind)
            if vector.is_file():
                vector = vector.rename(vector.with_name(name[:-4] + '.svg'))
            width, height = qa['render']['size']
            pages.append({'id': uuid.uuid4().hex[:10], 'kind': kind, 'index': 0, 'file': name, 'url': components.url(folder / name),
                          'svg': components.url(vector) if vector.is_file() else None,
                          'width': width, 'height': height, 'notes': page_notes, 'review': review,
                          'checks': {'passed': not brandkit_set.hard_issues(qa), 'issues': [row['check'] for row in qa.get('issues', [])][:12]}})
        if ctx.get('review_refused'):
            notes.append('视觉审核被 AI 服务拒绝（%s），之后的页面没有再请求审核。' % ctx['review_refused'])
        if vision_ready and len(pages) > 1 and not ctx.get('review_refused'):
            progress('compose', 1, '视觉模型检查整套图')
            try:
                with brandkit_review.fresh_answers(fresh):
                    set_review = brandkit_review.review_set(pages, folder, product)
            except Exception as exc:
                set_review = None; notes.append('整套图的视觉检查没有完成（%s）。' % str(exc)[:80])
            if set_review:
                repeats = '；'.join('第 %s 张' % '、'.join(str(number) for number in group) for group in set_review['repeats'] if len(group) > 1)
                notes.append('整套检查：%s%s%s' % ('风格一致。' if set_review['consistent'] else '风格不够统一。',
                                                 ('内容重复：' + repeats + '。') if repeats else '', set_review['notes']))
        shutil.rmtree(work, ignore_errors=True)
        if not pages:
            raise ValueError('没有排出任何图片：' + '；'.join(notes[-3:]))
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if not known:
        notes.append('产品的真实尺寸没有填写，按模型文件的单位估计为最长边 %g mm，没有生成尺寸图。' % size_mm)
    if brand.accent_source == 'product':
        notes.append('品牌色取自产品本身的颜色 %s；可在品牌设置里改成你的品牌色。' % brand.accent)
    scene_rows = [{'path': row['path'], 'url': components.url(row['path']), 'title': row['title'], 'body': row.get('body', ''),
                   'focus': row['focus'], 'role': row['role'], 'summary': row.get('summary'), 'model': row.get('model'), 'check': row.get('check'), 'caption': row.get('caption'), 'box': row.get('box'),
                   'captions': row.get('captions') or {}, 'usage': row.get('usage'), 'attempts': row.get('attempts')}
                  for row in made_scenes if row['role'] not in ('workpiece', 'earlier')]

    def change(project, listing):
        listing.update(pages=pages, built_at=time.time(), notes=notes, product_color=product_color, folder=folder.name, translations=translations,
                       located=located, placed={'view': _view_key(project),
                                                'features': {row['title']: {'point': row['point'], 'parts': row.get('parts') or []}
                                                             for row in product['features'] if row.get('point')}})
        if scene_key:
            listing['scenes'] = {'key': scene_key, 'items': scene_rows, 'at': time.time()}
        listing.setdefault('product', written); listing.setdefault('brand', brand_record)
        kept_scenes = scene_key or (scene_store or {}).get('key')
        keep = {folder.name} | ({'scenes-' + kept_scenes} if kept_scenes else set())
        for stale in list((DATA / project_id / 'listing').glob('build-*')) + list((DATA / project_id / 'listing').glob('scenes-*')):
            if stale.name not in keep:
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
        with LOCK:
            task = get(KIND, task_id)
            if task['status'] != 'queued':          # cancelled before it started
                return
            task['status'] = 'running'; put(KIND, task)
        pages, _ = build(project_id, cancelled, progress, fresh=task.get('fresh') is True)
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
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for page, path in files:
            bundle.write(path, page['file'])
            vector = components.path_of(project_id, page.get('svg'), ('.svg',)) if page.get('svg') else None
            if vector and vector.is_file():
                bundle.write(vector, 'editable/' + vector.name)          # live text and named layers, for Illustrator, Figma or Inkscape
        fonts = files[0][1].parent / 'svg' / 'fonts'
        if fonts.is_dir():
            for font in sorted(fonts.iterdir()):
                bundle.write(font, 'editable/fonts/' + font.name)
    slug = re.sub(r'[^\w\-]+', '_', str(project.get('slug') or 'product'))[:40] or 'product'
    return buffer.getvalue(), slug + '-listing.zip'

"""Data contract of a usage scene: what is stored in plan['scene_set'].

Shared by the planner (validation), the stylist (which writes it) and the
Blender worker (which rebuilds it). Everything here is plain validated data:
file paths inside the project, numbers, fixed words. Never model text.
"""
import copy
import math
import re
from pathlib import Path

ZONES = ('on_top', 'beside', 'inside', 'behind')
POSES = ('auto', 'flat', 'upright')
SETTINGS = ('workshop', 'kitchen', 'bathroom', 'living', 'office', 'garden', 'studio')
MAX_PROPS = 12
TEXTURE_KEYS = ('base_color', 'roughness', 'normal')


def basis(plan):
    """What a stored layout depends on. A change here makes the layout stale."""
    return {'rotation': [float(value) for value in plan.get('rotation', [0, 0, 0])],
            'pose': copy.deepcopy(plan.get('pose', {})),
            'dimensions_mm': [float(value) for value in plan['dimensions_mm']] if plan.get('dimensions_mm') else None}


def _number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('场景布置的 %s 无效' % label)


def _text(value, limit, label):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError('场景布置的 %s 无效' % label)


def _path(value, label):
    _text(value, 4096, label)
    if value.lower().startswith(('http:', 'https:', 'file:', 'data:')):
        raise ValueError('场景道具须先下载到当前项目')


def _material(value, label):
    if not isinstance(value, dict) or set(value) - {'asset_id', 'name', 'textures', 'tile_m', 'color', 'source_url', 'license', 'distance_m'}:
        raise ValueError('场景布置的 %s 无效' % label)
    if 'asset_id' in value and not re.fullmatch(r'[A-Za-z0-9_]{1,100}', str(value['asset_id'])):
        raise ValueError('场景布置的 %s 无效' % label)
    textures = value.get('textures', {})
    if not isinstance(textures, dict) or set(textures) - set(TEXTURE_KEYS):
        raise ValueError('场景布置的 %s 贴图无效' % label)
    for path in textures.values():
        _path(path, label + ' 贴图')
    if 'tile_m' in value: _number(value['tile_m'], .01, 100, label + ' 铺贴尺寸')
    if 'distance_m' in value: _number(value['distance_m'], 0, 100, label + ' 距离')
    if 'color' in value:
        color = value['color']
        if not isinstance(color, list) or len(color) != 3: raise ValueError('场景布置的 %s 颜色无效' % label)
        for channel in color: _number(channel, 0, 1, label + ' 颜色')
    for key, limit in (('name', 200), ('source_url', 500), ('license', 100)):
        if key in value: _text(value[key], limit, label)


def validate(value, plan):
    """Raise ValueError unless ``value`` is a complete, self-consistent scene set."""
    allowed = {'version', 'shot_id', 'setting', 'source', 'summary', 'product', 'units_per_meter', 'props', 'open',
               'surface', 'wall', 'basis', 'seed', 'skipped'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('version') != 1:
        raise ValueError('场景布置数据无效')
    shots = {shot['id'] for shot in plan.get('shots', [])}
    if value.get('shot_id') not in shots:
        raise ValueError('场景布置必须对应一个现有镜头')
    if value.get('setting') not in SETTINGS or value.get('source') not in ('rules', 'ai'):
        raise ValueError('场景布置的场景类型无效')
    _number(value.get('units_per_meter'), 1e-6, 1e6, '比例')
    if 'summary' in value: _text(value['summary'], 400, '说明')
    if 'seed' in value and type(value['seed']) is not int: raise ValueError('场景布置的随机种子无效')
    product = value.get('product', {})
    if not isinstance(product, dict) or set(product) - {'type', 'max_dimension_mm', 'size_basis'}:
        raise ValueError('场景布置的产品信息无效')
    if 'type' in product: _text(product['type'], 120, '产品类型')
    if 'max_dimension_mm' in product: _number(product['max_dimension_mm'], 1, 100000, '产品尺寸')
    if product.get('size_basis', 'user') not in ('user', 'category', 'model_units', 'ai'):
        raise ValueError('场景布置的尺寸依据无效')
    props = value.get('props')
    if not isinstance(props, list) or len(props) > MAX_PROPS:
        raise ValueError('场景道具数量无效')
    seen = set()
    for prop in props:
        if not isinstance(prop, dict) or set(prop) - {'id', 'asset_id', 'name', 'file', 'zone', 'pose', 'matrix', 'size_mm',
                                                      'license', 'source_url', 'provider', 'concept'}:
            raise ValueError('场景道具数据无效')
        if not re.fullmatch(r'p[1-9][0-9]?', str(prop.get('id'))) or prop['id'] in seen:
            raise ValueError('场景道具标识无效')
        seen.add(prop['id'])
        if not re.fullmatch(r'[A-Za-z0-9_]{1,100}', str(prop.get('asset_id'))): raise ValueError('场景道具来源标识无效')
        _path(prop.get('file'), '道具文件')
        if Path(prop['file']).suffix.lower() not in ('.gltf', '.glb'): raise ValueError('场景道具必须是 glTF 模型')
        if prop.get('zone') not in ZONES or prop.get('pose', 'auto') not in POSES: raise ValueError('场景道具的摆放方式无效')
        matrix = prop.get('matrix')
        if not isinstance(matrix, list) or len(matrix) != 16: raise ValueError('场景道具缺少位置')
        for number in matrix: _number(number, -1e6, 1e6, '道具位置')
        if 'size_mm' in prop:
            if not isinstance(prop['size_mm'], list) or len(prop['size_mm']) != 3: raise ValueError('场景道具尺寸无效')
            for number in prop['size_mm']: _number(number, 0, 1e6, '道具尺寸')
        for key, limit in (('name', 200), ('license', 100), ('source_url', 500), ('provider', 60), ('concept', 120)):
            if key in prop: _text(prop[key], limit, '道具信息')
    opening = value.get('open')
    if opening is not None:
        if (not isinstance(opening, dict) or set(opening) - {'objects', 'translation', 'cavity_area', 'cavity_depth'}
                or not isinstance(opening.get('objects'), list) or not 1 <= len(opening['objects']) <= 2000
                or any(not isinstance(name, str) or not name or len(name) > 300 for name in opening['objects'])
                or not isinstance(opening.get('translation'), list) or len(opening['translation']) != 3):
            raise ValueError('场景布置的开合部件无效')
        for number in opening['translation']: _number(number, -100, 100, '开合距离')
        for key in ('cavity_area', 'cavity_depth'):
            if key in opening: _number(opening[key], 0, 1e6, '开合尺寸')
    for key in ('surface', 'wall'):
        if value.get(key) is not None: _material(value[key], '地面' if key == 'surface' else '背景墙')
    stored = value.get('basis')
    if not isinstance(stored, dict) or set(stored) != {'rotation', 'pose', 'dimensions_mm'}:
        raise ValueError('场景布置缺少模型朝向依据')
    skipped = value.get('skipped', [])
    if not isinstance(skipped, list) or len(skipped) > 40 or any(
            not isinstance(row, dict) or set(row) - {'concept', 'reason', 'asset_id'}
            or any(not isinstance(item, str) or len(item) > 200 for item in row.values()) for row in skipped):
        raise ValueError('场景布置的未放置记录无效')
    return value


def files(value):
    """Every file a scene set reads at render time."""
    paths = [prop['file'] for prop in value.get('props', [])]
    for key in ('surface', 'wall'):
        paths.extend((value.get(key) or {}).get('textures', {}).values())
    return paths

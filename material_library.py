"""Local material library: shipped surface details and decals, chosen by rules.

The Poly Haven agent spends a model call to pick a texture and downloads it
for every request. A few surfaces come up again and again on tools (a moulded
toolbox grain, powder coat, reflective safety tape), so these ship with the
repo in ``materials/library`` and are chosen here by a fixed rule table:
no tokens, no network, the same answer every time.

Nothing here touches Blender. The worker resolves an override such as
``{'surface_detail': {'id': 'textured_coarse_stipple'}}`` through
``surface_detail`` / ``decal`` below, so a plan can only ever name files
that the manifest lists.

The texture pack came from the user without a licence: every entry says
``user-supplied, internal use``, and tile sizes are estimates (the source
files carry no physical scale).
"""
import json
import math
import re
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent / 'materials' / 'library'
_CACHE = {}

# Surface detail is invisible once denoised at small sizes, and a preview that
# cannot show it must not pretend it was applied.
DETAIL_QUALITIES = ('reference', 'showcase', 'standard', 'fine')
MIN_DETAIL_MM = 30
# Words in a part name that rule out a grain: it would ruin a label, a lens or
# a gloss part. ASCII words match whole words only.
AVOID_WORDS = ('logo', 'label', 'decal', 'sticker', 'text', 'glass', 'lens', 'clear', 'transparent', 'chrome',
               'mirror', 'gloss', 'glossy', 'rubber', 'silicone', 'screen', 'display',
               '标签', '标志', '商标', '铭牌', '印字', '贴纸', '文字', '玻璃', '镜片', '透明', '镀铬', '电镀', '镜面',
               '亮面', '亮光', '高光', '橡胶', '硅胶', '屏幕', '显示')
REFLECTIVE_WORDS = ('反光', 'reflective', 'retroreflective', 'reflector')


def _words(name, words):
    text = str(name or '').casefold().replace('_', ' ')
    return [word for word in words
            if (re.search(r'(?<![a-z])' + re.escape(word) + r'(?![a-z])', text) if word.isascii() else word in text)]


def manifest():
    """The shipped manifest, read once."""
    if 'manifest' not in _CACHE:
        _CACHE['manifest'] = json.loads((LIBRARY / 'manifest.json').read_text(encoding='utf-8'))
    return _CACHE['manifest']


def entry(ident, kind=None):
    for item in manifest().get('materials', []):
        if item.get('id') == ident and (kind is None or item.get('kind') == kind):
            return item
    return None


def map_path(item, key):
    """Absolute path of one map; only manifest paths inside the library resolve."""
    relative = (item.get('maps') or {}).get(key)
    if not isinstance(relative, str):
        return None
    path = (LIBRARY / relative).resolve()
    if LIBRARY.resolve() not in path.parents:
        return None
    return path


def pick(finish, part_name='', size_mm=None, quality='standard', style='white'):
    """The surface detail for one part, or None. A pure rule table.

    ``finish`` is a lookdev finish (``satin_plastic``) or worker preset
    (``matte_plastic``). A part name that says textured/powder coat/stipple
    selects the grain even when the finish is unknown, unless the finish is
    metal or gloss. ``style`` is accepted for callers that pass the plan's
    style; no current rule depends on it.
    """
    if quality not in DETAIL_QUALITIES:
        return None
    if size_mm is not None and (not isinstance(size_mm, (int, float)) or size_mm < MIN_DETAIL_MM):
        return None
    if _words(part_name, AVOID_WORDS):
        return None
    finish = str(finish or '')
    for item in manifest().get('materials', []):
        if item.get('kind') != 'surface_detail':
            continue
        if finish in item.get('applies_to_finishes', []):
            return item
        if 'metal' in finish or 'gloss' in finish or finish in ('glass', 'rubber', 'ceramic'):
            continue
        if _words(part_name, item.get('tags', [])):
            return item
    return None


def _number(value, lo, hi, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError('%s 必须为 %s–%s 的有限数值' % (key, lo, hi))
    return float(value)


def surface_detail(value):
    """Validated surface-detail settings with the library file resolved."""
    if not isinstance(value, dict) or set(value) - {'id', 'strength', 'tile_mm'}:
        raise ValueError('表面细节参数无效')
    item = entry(value.get('id'), 'surface_detail')
    path = map_path(item, 'height') if item else None
    if not path or not path.is_file():
        raise ValueError('材质库中没有该表面细节：' + str(value.get('id')))
    return {'id': item['id'], 'path': str(path),
            'strength': _number(value.get('strength', item.get('bump_strength', .6)), 0, 2, 'surface_detail.strength'),
            'tile_mm': _number(value.get('tile_mm', item.get('tile_mm', 80)), 1, 2000, 'surface_detail.tile_mm'),
            'height_mm': float(item.get('height_mm', .1)),
            'estimate': bool(item.get('tile_mm_estimate') or item.get('height_mm_estimate'))}


def decal(value):
    """Validated decal settings (reflective tape) with the library file resolved."""
    if not isinstance(value, dict) or set(value) - {'id', 'tile_mm'}:
        raise ValueError('贴膜参数无效')
    item = entry(value.get('id'), 'decal')
    path = map_path(item, 'base_color') if item else None
    if not path or not path.is_file():
        raise ValueError('材质库中没有该贴膜：' + str(value.get('id')))
    tile = value.get('tile_mm', item.get('tile_mm', [50, 114]))
    if isinstance(tile, (int, float)) and not isinstance(tile, bool):
        tile = [tile, tile]
    if not isinstance(tile, list) or len(tile) != 2:
        raise ValueError('decal.tile_mm 需要一个或两个数值')
    return {'id': item['id'], 'path': str(path), 'tile_mm': [_number(v, 1, 2000, 'decal.tile_mm') for v in tile],
            'roughness': float(item.get('roughness', .35)), 'estimate': bool(item.get('tile_mm_estimate'))}


def is_reflective_part(name):
    """A part whose own name says it is reflective tape (反光条, reflective strip)."""
    return bool(_words(name, REFLECTIVE_WORDS))


def decal_for(part_name, color=None):
    """Which reflective tape a 反光 part gets: its name first, then its colour."""
    for item in manifest().get('materials', []):
        if item.get('kind') == 'decal' and _words(part_name, item.get('tags', [])):
            return item['id']
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        r, g, b = [float(v) for v in color[:3]]
        if r > max(g, b) * 1.6:
            return 'reflective_tape_red'
    return 'reflective_tape_yellowgreen'

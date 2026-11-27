"""Material IDs and packaged resources shared by the viewport and Blender.

The runtime only reads materials/keyshot. To rebuild it from a local KeyShot
extraction, run python -m keyshot_catalog [--source DIRECTORY].
"""
import copy
import io
import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parent / 'materials' / 'keyshot'
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}
API_ROOT = '/api/material-library'


@lru_cache(maxsize=1)
def _index():
    rows = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
    return {row['id']: row for row in rows}


def require_id(ident):
    if not isinstance(ident, str) or ident not in _index():
        raise ValueError('材质库中没有该材质')
    return ident


def _asset(folder, relative):
    parent = (ROOT / folder).resolve()
    path = (parent / relative).resolve()
    if parent not in path.parents or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError('材质库图像不存在')
    return path if path.is_file() else None


def thumbnail_path(ident):
    row = _index()[require_id(ident)]
    return _asset('previews', row['thumbnail'])


def swatch_png(ident):
    """A base-color swatch for installations without the original preview."""
    from PIL import Image, ImageDraw, ImageOps

    linear = _index()[require_id(ident)]['pbr']['color']
    rgb = tuple(round(255 * (12.92*c if c <= .0031308 else 1.055*c**(1/2.4)-.055)) for c in linear)
    falloff = Image.radial_gradient('L').resize((138,138)).crop((37,44,133,140))
    shade = tuple(round(c*.40) for c in rgb)
    light = tuple(round(c + (255-c)*.45) for c in rgb)
    ball = ImageOps.colorize(falloff, black=light, mid=rgb, white=shade, midpoint=96)
    mask = Image.new('L',ball.size)
    ImageDraw.Draw(mask).ellipse((6,6,89,89),fill=255)
    ball.putalpha(mask)
    data = io.BytesIO()
    ball.resize((64,64),Image.Resampling.LANCZOS).save(data,format='PNG')
    data.seek(0)
    return data


def _installed_texture(filename):
    full = _asset('textures', filename)
    small = _asset('viewport-textures', filename)
    return full or small, small or full


def texture_path(ident, channel):
    row = _index()[require_id(ident)]
    textures = row['pbr']['textures']
    if not isinstance(channel, str) or channel not in textures:
        raise ValueError('材质中没有该通道的贴图')
    _, path = _installed_texture(textures[channel])
    if path is None:
        raise FileNotFoundError('尚未安装该材质的本地贴图')
    return path


def render_spec(ident):
    row = _index()[require_id(ident)]
    spec = copy.deepcopy(row['pbr'])
    spec['textures'] = {key: str(path) for key, filename in spec['textures'].items()
                        if (path := _installed_texture(filename)[0]) is not None}
    return spec


def catalog():
    """Public presentation only. Each texture URL resolves one identified channel."""
    materials = []
    previews = {p.name for p in (ROOT/'previews').glob('*') if p.is_file()}
    installed = {}
    for row in _index().values():
        ident = row['id']
        url = f'{API_ROOT}/{ident}'
        public = {key: row[key] for key in ('id', 'name', 'category', 'subcategory')}
        public['thumbnail_url'] = f'{url}/thumbnail'
        if row['thumbnail'] not in previews:
            public['thumbnail_is_swatch'] = True
        maps = {}
        for channel, filename in row['pbr']['textures'].items():
            if filename not in installed:
                installed[filename] = _installed_texture(filename)[0] is not None
            if installed[filename]:
                maps[channel] = f'{url}/textures/{channel}'
        if len(maps) < len(row['pbr']['textures']):
            public['missing_textures'] = True
        public['pbr'] = {**row['pbr'], 'textures': maps}
        materials.append(public)
    return {'materials': materials, 'categories': sorted({row['category'] for row in materials}),
            'total': len(materials)}


if __name__ == '__main__':
    from scripts.build_keyshot_catalog import main
    main()

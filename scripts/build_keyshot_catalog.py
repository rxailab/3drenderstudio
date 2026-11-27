"""Install the catalog images from a local KeyShot library, or rebuild its PBR index."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from keyshot_catalog import IMAGE_SUFFIXES, ROOT


def _install_image(source, destination):
    """Link original bytes locally, or copy across volumes. Rebuilds are atomic."""
    if destination.exists() and source.samefile(destination):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        staged = Path(tmp) / destination.name
        try:
            os.link(source, staged)
        except OSError:
            shutil.copyfile(source, staged)
        staged.replace(destination)


def _viewport_copy(source, destination):
    from PIL import Image

    with Image.open(source) as image:
        if max(image.size) <= 1024:
            _install_image(source, destination)
            return
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
            staged = Path(tmp) / destination.name
            options = {'quality': 92} if image.format == 'JPEG' else {}
            image.save(staged, **options)
            staged.replace(destination)


def _material_id(relative):
    return 'ks_' + hashlib.sha256(relative.as_posix().encode('utf-8')).hexdigest()[:20]


def _thumbnail_bytes(material, name):
    """An MTL's embedded JPEG or PNG is identified by the catalog's content hash."""
    expected = Path(name)
    markers = {'.jpg': (b'\xff\xd8\xff', b'\xff\xd9'),
               '.png': (b'\x89PNG\r\n\x1a\n', b'IEND\xaeB`\x82')}
    first, last = markers[expected.suffix]
    data = material.read_bytes()
    start = data.find(first)
    while start >= 0:
        end = data.find(last, start + len(first))
        while end >= 0:
            image = data[start:end + len(last)]
            if hashlib.sha256(image).hexdigest() == expected.stem:
                return image
            end = data.find(last, end + len(last))
        start = data.find(first, start + len(first))
    raise ValueError(f'本机材质的预览图与物影目录不匹配：{material}')


def install(installation, output):
    """Populate the shipped index using only the user's Materials and Textures folders."""
    index = ROOT / 'catalog.json'
    rows = json.loads(index.read_text(encoding='utf-8'))
    materials = installation / 'Materials'
    textures = installation / 'Textures'
    if not materials.is_dir() or not textures.is_dir():
        raise ValueError(f'请选择包含 Materials 和 Textures 的 KeyShot 资源目录：{installation}')
    material_paths = {_material_id(p.relative_to(materials)): p for p in materials.rglob('*.mtl')}
    previews = {}
    texture_names = set()
    for row in rows:
        material = material_paths.get(row['id'])
        if material is None:
            raise FileNotFoundError(f'本机材质库缺少 {row["category"]} / {row["name"]}')
        name = row['thumbnail']
        if name not in previews:
            previews[name] = _thumbnail_bytes(material, name)
        texture_names.update(row['pbr']['textures'].values())
    for relative in sorted(texture_names):
        if not (textures / relative).is_file():
            raise FileNotFoundError(f'本机材质库缺少贴图：{textures / relative}')

    directory = output / 'previews'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        for name, image in previews.items():
            dest = directory / name
            if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == Path(name).stem:
                continue
            staged = Path(tmp) / name
            staged.write_bytes(image)
            staged.replace(dest)
    for relative in sorted(texture_names):
        src = textures / relative
        _install_image(src, output / 'textures' / relative)
        _viewport_copy(src, output / 'viewport-textures' / relative)
    if index.resolve() != (output / 'catalog.json').resolve():
        _install_image(index, output / 'catalog.json')
    return len(rows)


def build(source, output):
    from keyshot_surface import describe

    library = source / '材质库'
    textures = (library / 'Textures').resolve()
    previews = (library / 'Previews').resolve()
    materials = library / 'Materials'
    parameters = source / '材质参数' / '分类'
    lookup = {}
    for path in textures.rglob('*'):
        if path.is_file():
            name = path.name.casefold()
            if name in lookup:
                raise ValueError(f'不唯一的贴图文件名 {path.name}')
            lookup[name] = str(path.resolve())

    compiled = []
    ids = set()
    used_images = {}
    with (source / '材质清单.csv').open(encoding='utf-8-sig', newline='') as source_csv:
        for row in csv.DictReader(source_csv):
            relative = (source / row['材质路径']).relative_to(materials)
            graph = json.loads((parameters / relative.with_suffix('.json')).read_text(encoding='utf-8'))
            if graph['source'] != relative.as_posix() or graph['sha256'] != row['材质SHA256']:
                raise ValueError(f'参数记录与索引不一致 {relative}')
            ident = _material_id(relative)
            if ident in ids:
                raise ValueError(f'重复的材质 ID {ident}')
            ids.add(ident)
            preview = (source / row['预览路径']).resolve()
            if previews not in preview.parents or preview.suffix.lower() not in IMAGE_SUFFIXES:
                raise ValueError(f'无效预览图 {preview}')
            # Multiple original presets can share one identical thumbnail.
            thumbnail = row['预览SHA256'] + preview.suffix.lower()
            used_images[output / 'previews' / thumbnail] = preview
            spec = describe(graph, lookup, preview)
            maps = {}
            for channel, filename in spec['textures'].items():
                image = Path(filename).resolve()
                if textures not in image.parents or image.suffix.lower() not in IMAGE_SUFFIXES:
                    raise ValueError(f'无法在视口和渲染中共同使用贴图 {image}')
                maps[channel] = image.relative_to(textures).as_posix()
                used_images[output / 'textures' / maps[channel]] = image
            compiled.append({'id': ident, 'name': row['材质名称'], 'category': row['一级分类'],
                             'subcategory': row['子分类'], 'thumbnail': thumbnail,
                             'pbr': {**spec, 'textures': maps}})
    for destination, image in used_images.items():
        _install_image(image, destination)
        if destination.is_relative_to(output / 'textures'):
            relative = destination.relative_to(output / 'textures')
            _viewport_copy(image, output / 'viewport-textures' / relative)
    # Publish the index only after every referenced asset exists.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        staged = Path(tmp) / 'catalog.json'
        staged.write_text(json.dumps(compiled, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')), encoding='utf-8')
        staged.replace(output / staged.name)
    return len(compiled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--installation', type=Path, help='从本机 KeyShot 资源目录安装物影的材质缩略图与贴图')
    group.add_argument('--source', type=Path, default=ROOT.parent.parent / 'KeyShot提取', help='从已提取的图表和参数重新编译材质目录')
    parser.add_argument('--output', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.installation:
        print(f'Installed images for {install(args.installation.resolve(), args.output.resolve())} materials.')
    else:
        print(f'Compiled {build(args.source.resolve(), args.output.resolve())} materials.')

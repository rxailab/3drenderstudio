"""Integration validation against a real existing Blender scene (no fixture render).

python validation/scene-export/validate_export.py --blender D:/blender/blender.exe \
  --source path/to/existing.blend --output validation/scene-export/check

Omit --blender to inspect a previously generated export without exporting again.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def validate(source, output):
    result = json.loads((output / 'result.json').read_text(encoding='utf-8'))
    assert result['success'], result
    assert result['verified']['blend_reopened']
    for filename in result['files'].values():
        target = (output / filename).resolve()
        assert target.is_relative_to(output.resolve()), filename
        assert target.is_file() and target.stat().st_size > 100, filename
    data = json.loads((output / result['files']['scene']).read_text(encoding='utf-8'))
    assert data['schema_version'] == 1
    assert data['units'] == 'meters' and data['axis'] == 'blender_z_up'
    names = {o['name'] for o in data['objects']}
    material_names = {m['name'] for m in data['materials']}
    assert len(names) == len(data['objects'])
    assert data['render']['active_camera'] in names
    assert data['render']['width'] > 0 and data['render']['height'] > 0
    assert isinstance(data['plan'], dict) and isinstance(data['shot'], dict)
    assert result['counts']['objects'] == len(data['objects'])
    assert result['counts']['materials'] == len(data['materials'])
    face_count = 0
    for obj in data['objects']:
        assert obj['parent'] is None or obj['parent'] in names
        assert len(obj['matrix_world']) == 4 and all(len(row) == 4 for row in obj['matrix_world'])
        assert isinstance(obj['hide_render'], bool)
        if 'camera' in obj:
            assert obj['camera']['lens'] > 0
        if 'light' in obj:
            assert len(obj['light']['color']) == 3
        if 'mesh' not in obj:
            continue
        mesh = obj['mesh']
        assert len(mesh['faces']) == len(mesh['material_indices'])
        assert len(mesh['normals']) == len(mesh['faces'])
        assert len(mesh['smooth']) == len(mesh['faces'])
        assert len(mesh['uvs']) in (0, len(mesh['faces']))
        assert all(name is None or name in material_names for name in mesh['materials'])
        for index, face in enumerate(mesh['faces']):
            assert len(face) in (3, 4), (obj['name'], face)
            assert all(0 <= vertex < len(mesh['vertices']) for vertex in face)
            assert mesh['material_indices'][index] < max(1, len(mesh['materials']))
            assert len(mesh['normals'][index]) == len(face)
            if mesh['uvs']:
                assert len(mesh['uvs'][index]) == len(face)
        face_count += len(mesh['faces'])
    textures = [tex for mat in data['materials'] for tex in mat['textures'].values()]
    textures += list(data['world']['textures'].values())
    for texture in textures:
        target = (output / texture['path']).resolve()
        assert target.is_relative_to(output.resolve() / 'tex'), texture
        assert target.is_file() and target.stat().st_size > 0, texture
    if 'fbx' in result['files']:
        with (output / result['files']['fbx']).open('rb') as stream:
            assert stream.read(20).startswith(b'Kaydara FBX Binary')
    return {'source': str(source), 'counts': result['counts'], 'faces': face_count,
            'textures': len({tex['path'] for tex in textures}), 'warnings': data['warnings']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--blender', type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    before = digest(source)
    if args.blender:
        output.mkdir(parents=True, exist_ok=True)
        request_path = output / 'request.json'
        request_path.write_text(json.dumps({'blend_path': str(source), 'output_dir': str(output)}), encoding='utf-8')
        exporter = Path(__file__).resolve().parents[2] / 'blender' / 'export_scene.py'
        completed = subprocess.run([str(args.blender), '-b', '--factory-startup', '--disable-autoexec',
                                    '--python-exit-code', '1', '--python', str(exporter), '--', str(request_path)],
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        (output / 'blender.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
        assert completed.returncode == 0, completed.stdout + completed.stderr
    assert digest(source) == before, 'Original source .blend was modified'
    report = dict(validate(source, output), source_sha256=before, source_unchanged=True)
    (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

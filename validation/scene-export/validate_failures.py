"""Check explicit failures and collision protection using a copy of a real scene."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--blender', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    exporter = Path(__file__).resolve().parents[2] / 'blender' / 'export_scene.py'
    clone = output / 'collision' / 'scene.blend'
    clone.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source, clone)
    before = hashlib.sha256(clone.read_bytes()).hexdigest()
    results = []
    for name, source, destination in [('collision', clone, clone.parent),
                                      ('missing', output / 'not-present.blend', output / 'missing')]:
        request = output / (name + '.json')
        request.write_text(json.dumps({'blend_path': str(source), 'output_dir': str(destination)}), encoding='utf-8')
        completed = subprocess.run([str(args.blender), '-b', '--factory-startup', '--disable-autoexec',
                                    '--python-exit-code', '1', '--python', str(exporter), '--', str(request)],
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        assert completed.returncode != 0, 'Invalid request unexpectedly succeeded'
        result = json.loads((destination / 'result.json').read_text(encoding='utf-8'))
        assert result['success'] is False and result['error'] and result['files'] == {}
        results.append({'case': name, 'error': result['error'], 'exit_code': completed.returncode})
    assert hashlib.sha256(clone.read_bytes()).hexdigest() == before
    (output / 'validation.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'source_unchanged': True, 'cases': results}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()

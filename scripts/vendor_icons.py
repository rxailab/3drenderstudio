"""Vendor open-licensed line icons for the text layer's icon agent.

Downloads the Iconify packages of two icon sets from the npm registry, checks
each tarball against the integrity hash the registry publishes, and turns
every icon into plain path data the application can validate and draw:

    lucide   Lucide (ISC)      https://lucide.dev
    tabler   Tabler Icons (MIT) https://tabler.io/icons

Only line icons are kept: Tabler's filled and "-off" variants and icons marked
hidden are left out, as is anything using elements other than path, circle,
ellipse, rect, line, polyline, polygon and g. Circles, rectangles and lines
become path commands, so a glyph is a list of ["s" | "f", d] pairs: a path
drawn with the set's stroke, or filled.

The result is written to static/vendor/icons/<set>.json.gz together with the
set's licence and a VENDORED.json listing versions and hashes. Run it again
with other versions to update:

    python scripts/vendor_icons.py
"""
import base64
import gzip
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'static' / 'vendor' / 'icons'
REGISTRY = 'https://registry.npmjs.org/'
SETS = {'lucide': '1.2.154', 'tabler': '1.2.45'}
LICENSE_TEXT = {
    'lucide': 'https://github.com/lucide-icons/lucide/blob/main/LICENSE',
    'tabler': 'https://github.com/tabler/tabler-icons/blob/master/LICENSE',
}
# The Iconify packages carry no licence file; the sets' own packages do.
LICENSE_PACKAGES = {'lucide': 'lucide-static', 'tabler': '@tabler/icons'}
sys.path.insert(0, str(ROOT))
from layer_icons import convert_body as convert          # the same reading the app applies to icons found online


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'studio-vendor-icons'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def vendor(prefix, version):
    name = '@iconify-json/' + prefix
    meta = json.loads(fetch(REGISTRY + name.replace('/', '%2f') + '/' + version))
    tarball = fetch(meta['dist']['tarball'])
    algorithm, expected = meta['dist']['integrity'].split('-', 1)
    if algorithm != 'sha512' or base64.b64encode(hashlib.sha512(tarball).digest()).decode() != expected:
        raise SystemExit('%s %s: tarball does not match the registry integrity hash' % (name, version))
    with tarfile.open(fileobj=io.BytesIO(tarball), mode='r:gz') as archive:
        def member(path):
            return json.loads(archive.extractfile('package/' + path).read().decode('utf-8'))
        icons = member('icons.json'); info = member('info.json')
    if icons.get('width', 24) != 24 or icons.get('height', 24) != 24:
        raise SystemExit('%s: not a 24 px set' % prefix)
    kept = {}; skipped = 0
    for icon_name, icon in sorted(icons['icons'].items()):
        if icon.get('hidden') or icon.get('width', 24) != 24 or icon.get('height', 24) != 24 or icon_name.endswith(('-filled', '-off')):
            skipped += 1; continue
        ops = convert(icon['body'])
        if ops is None:
            skipped += 1; continue
        kept[icon_name] = ops
    aliases = {alias: value['parent'] for alias, value in icons.get('aliases', {}).items()
               if value.get('parent') in kept and set(value) == {'parent'}}
    catalogue = {'prefix': prefix, 'name': info['name'], 'version': version, 'author': info.get('author'), 'license': info['license'],
                 'stroke_width': 2, 'grid': 24, 'icons': kept, 'aliases': aliases}
    TARGET.mkdir(parents=True, exist_ok=True)
    data = json.dumps(catalogue, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    path = TARGET / (prefix + '.json.gz')
    path.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    print('%s %s: %d icons kept, %d left out, %d aliases, %d bytes' % (prefix, version, len(kept), skipped, len(aliases), path.stat().st_size))
    return {'package': name, 'version': version, 'tarball': meta['dist']['tarball'], 'integrity': meta['dist']['integrity'],
            'license': info['license'], 'author': info.get('author'), 'icons': len(kept), 'file': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def licence_file(prefix):
    """The set's own LICENSE, copied from its upstream npm package (integrity-checked like the icons)."""
    name = LICENSE_PACKAGES[prefix]
    meta = json.loads(fetch(REGISTRY + name.replace('/', '%2f') + '/latest'))
    tarball = fetch(meta['dist']['tarball'])
    algorithm, expected = meta['dist']['integrity'].split('-', 1)
    if algorithm != 'sha512' or base64.b64encode(hashlib.sha512(tarball).digest()).decode() != expected:
        raise SystemExit('%s: tarball does not match the registry integrity hash' % name)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode='r:gz') as archive:
        text = archive.extractfile('package/LICENSE').read().decode('utf-8')
    (TARGET / ('LICENSE-%s.txt' % prefix)).write_text('From %s %s\n\n%s' % (name, meta['version'], text), encoding='utf-8')


def main():
    record = {'purpose': 'Line icons searched by the text layer\'s icon agent (layer_icons.py). Generated by scripts/vendor_icons.py.',
              'sets': [vendor(prefix, version) for prefix, version in SETS.items()]}
    (TARGET / 'VENDORED.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    licences = ['Icons in this folder come from two open-source sets, converted to plain path data by scripts/vendor_icons.py.', '']
    for row in record['sets']:
        licences.append('%s %s - %s licence (%s), %s' % (row['package'], row['version'], row['license']['title'],
                                                          LICENSE_TEXT[row['package'].split('/')[-1]], (row.get('author') or {}).get('name', '')))
    licences += ['', 'Both licences permit use, copying, modification and distribution, including commercially, provided the',
                 'copyright notice and permission notice are kept with the icons. Their full texts are in LICENSE-lucide.txt',
                 'and LICENSE-tabler.txt beside this file.']
    for prefix in SETS:
        licence_file(prefix)
    (TARGET / 'LICENSES.txt').write_text('\n'.join(licences) + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())

"""Design resources for the brand-kit templates, fetched when they are missing.

The templates are set in Archivo (heavy extended display), Barlow Condensed (big numbers) and Inter (text), with
Noto Sans SC for Chinese sets, and draw line icons from Tabler. None of these ship with Windows or most Linux
installs, so a page laid out on another machine would fall back to whatever sans-serif is there. This module keeps
them in ``brandkit/vendor`` (not committed): on first use each missing file is downloaded from its pinned source,
checked (a font must open as a font, an icon must be a plain SVG with no script or links) and written atomically,
with the licence beside it. ``fonts.css`` declares the faces the templates name.

Icons are fetched one by one by their Tabler name the first time a page asks for one; a name Tabler does not have is
remembered so it is not asked for again. Without a network connection nothing fails: the pages are laid out with
the fonts the machine has, and the QA record says which faces were missing.

Beyond those default faces, ``brandkit/library/fonts.json`` is a font library: OFL families grouped into pairings (display,
numbers, text, Chinese) that a brand picks. The Latin ones ship in ``brandkit/fonts``; the Chinese ones are fetched the
same way as Noto Sans SC. ``job_fonts`` gives a page its pairing's CSS stacks and the faces to wait for.

Run ``python brandkit_assets.py`` to fetch everything now (``--zh`` adds the Chinese face).
"""
import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'brandkit'
BUNDLED = ROOT / 'fonts'          # the Latin faces ship with the project (OFL): English sets never wait for a download
VENDOR = Path(os.environ.get('BRANDKIT_VENDOR') or ROOT / 'vendor')
FONTS = VENDOR / 'fonts'
ICONS = VENDOR / 'icons'
GOOGLE = 'https://github.com/google/fonts/raw/main/ofl/'
# Mirrors tried in order: jsDelivr's CDNs answer where github.com is slow or blocked; GitHub itself last.
FONT_MIRRORS = ('https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/', 'https://fastly.jsdelivr.net/gh/google/fonts@main/ofl/',
                'https://gcore.jsdelivr.net/gh/google/fonts@main/ofl/', GOOGLE)
TABLER_VERSION = '3.31.0'
TABLER = 'https://cdn.jsdelivr.net/npm/@tabler/icons@%s/' % TABLER_VERSION
ICON_MIRRORS = (TABLER, 'https://fastly.jsdelivr.net/npm/@tabler/icons@%s/' % TABLER_VERSION,
                'https://gcore.jsdelivr.net/npm/@tabler/icons@%s/' % TABLER_VERSION, 'https://unpkg.com/@tabler/icons@%s/' % TABLER_VERSION)
TIMEOUT = 120
MAX_BYTES = 40 * 1024 * 1024
LOCK = threading.Lock()

LIBRARY = json.loads((ROOT / 'library' / 'fonts.json').read_text(encoding='utf-8'))
FAMILIES = LIBRARY['families']
PAIRINGS = {row['id']: row for row in LIBRARY['pairings']}
DEFAULT_PAIRING = LIBRARY['default']
ROLES = ('display', 'cond', 'text', 'cjk')
# The last resort after a pairing's own faces: the default faces, then what the machine has.
FALLBACK = {'display': '"Archivo","Noto Sans Display","Noto Sans",sans-serif', 'cond': '"Barlow Condensed","Archivo",sans-serif',
            'text': '"Inter","Noto Sans",sans-serif', 'cjk': '"Noto Sans SC","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei",sans-serif'}
PROBE = {'display': 800, 'cond': 700, 'text': 400, 'cjk': 700}


def _faces_of(pairing_id):
    """(family, file, source URL, CSS descriptors, languages) for every face a pairing names; the CJK face is for zh only."""
    row = PAIRINGS.get(pairing_id) or PAIRINGS[DEFAULT_PAIRING]
    faces, seen = [], set()
    for role in ROLES:
        key = row[role]
        if key in seen:
            continue
        seen.add(key)
        family = FAMILIES[key]
        languages = ('zh',) if family['script'] == 'cjk' else ('en', 'zh')
        faces.extend((family['family'], face['file'], GOOGLE + face['src'], face['css'], languages) for face in family['files'])
    return tuple(faces)


def _licences_of(pairing_id, language=None):
    row = PAIRINGS.get(pairing_id) or PAIRINGS[DEFAULT_PAIRING]
    names = []
    for key in dict.fromkeys(row[role] for role in ROLES):
        if language == 'en' and FAMILIES[key]['script'] == 'cjk':
            continue
        name = FAMILIES[key]['files'][0]['file'].split('-')[0] + '-OFL.txt'
        names.append((name, GOOGLE + FAMILIES[key]['licence']))
    return tuple(names)


# family, file, source, CSS descriptors, languages it is for (the default pairing)
FACES = _faces_of(DEFAULT_PAIRING)
LICENCES = _licences_of(DEFAULT_PAIRING)
ICON_NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_checked = {}


def _get(url, tries=3):
    """The bytes at ``url``; a dropped connection or a busy server is tried again, a 404 is not."""
    import time
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'brandkit-assets/1.0'})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('file too large')
            return data
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429 or attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))


def _fetch(urls, accept):
    """The first answer from ``urls`` that ``accept`` takes; a 404 everywhere raises HTTPError 404, anything else URLError."""
    missing = 0; last = None
    for url in urls:
        try:
            data = _get(url)
        except urllib.error.HTTPError as exc:
            missing += exc.code == 404; last = exc; continue
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc; continue
        if accept(data):
            return data
    if missing == len(urls):
        raise urllib.error.HTTPError(urls[-1], 404, 'not found', {}, None)
    raise urllib.error.URLError(str(last) if last else 'no source answered')


def _write(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix='.' + target.name)
    with os.fdopen(handle, 'wb') as stream:
        stream.write(data)
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)


def _font_ok(data):
    """A real TrueType/OpenType file that a font engine can open."""
    if data[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
        return False
    try:
        import io
        from PIL import ImageFont
        ImageFont.truetype(io.BytesIO(data), 24)
        return True
    except Exception:
        return False


def _svg_ok(data):
    """A plain SVG drawing: no scripts, event handlers, external references or foreign content."""
    try:
        text = data.decode('utf-8')
        root = ET.fromstring(text)
    except Exception:
        return False
    if not root.tag.endswith('svg') or '<script' in text.lower() or '<foreignobject' in text.lower():
        return False
    for node in root.iter():
        for key, value in node.attrib.items():
            name = key.split('}')[-1].lower()
            if name.startswith('on') or (name == 'href' and not str(value).startswith('#')):
                return False
    return True


def _font_file(name):
    """Where a face is on disk: shipped with the project, else downloaded; None when neither."""
    for folder in (BUNDLED, FONTS):
        if (folder / name).is_file():
            return folder / name
    return None


def _write_css():
    rules, seen = [], set()
    for family_key, family in FAMILIES.items():
        for face in family['files']:
            path = _font_file(face['file'])
            if path and face['file'] not in seen:
                seen.add(face['file'])
                relative = os.path.relpath(path, VENDOR).replace(os.sep, '/')
                rules.append("@font-face{font-family:'%s';src:url('%s') format('truetype');%s;font-display:block}" % (family['family'], relative, face['css']))
    _write(VENDOR / 'fonts.css', ('\n'.join(rules) + '\n').encode())


def ensure_fonts(language='en', fetch=True, pairing=None):
    """The faces a set in ``language`` needs (in the brand's font ``pairing``), downloaded if missing.
    Returns {'present': [...], 'missing': [...]}."""
    pairing = pairing if pairing in PAIRINGS else DEFAULT_PAIRING
    key = (language, fetch, pairing)
    if key in _checked:
        return _checked[key]
    present, missing = [], []
    with LOCK:
        changed = False
        for family, name, url, _, languages in _faces_of(pairing):
            if language not in languages:
                continue
            if not _font_file(name) and fetch:
                path = url[len(GOOGLE):] if url.startswith(GOOGLE) else None
                try:
                    data = _fetch([mirror + path for mirror in FONT_MIRRORS] if path else [url], _font_ok)
                    _write(FONTS / name, data); changed = True
                except (urllib.error.URLError, OSError, ValueError):
                    pass
            (present if _font_file(name) else missing).append(name)
        if changed or not (VENDOR / 'fonts.css').is_file():
            if fetch:
                for name, url in _licences_of(pairing, language):
                    if not (FONTS / name).is_file() and not (BUNDLED / name).is_file():
                        try:
                            _write(FONTS / name, _fetch([mirror + url[len(GOOGLE):] for mirror in FONT_MIRRORS], lambda data: bool(data)))
                        except (urllib.error.URLError, OSError, ValueError):
                            pass
            _write_css()
    result = {'present': present, 'missing': missing}
    if not missing:
        _checked[key] = result
    return result


def faces(language='en', pairing=None):
    """The faces on disk a page in ``language`` and ``pairing`` uses, as (family, path, descriptors): the pairing's own,
    then the default ones it falls back on."""
    found, seen = [], set()
    for pid in dict.fromkeys((pairing if pairing in PAIRINGS else DEFAULT_PAIRING, DEFAULT_PAIRING)):
        for family, name, _, descriptors, languages in _faces_of(pid):
            path = _font_file(name)
            if language in languages and path and name not in seen:
                seen.add(name); found.append((family, path, descriptors))
    return found


def licence_files(pairing=None):
    """The OFL texts on disk for a pairing's families (and the defaults)."""
    names = {name for pid in {pairing if pairing in PAIRINGS else DEFAULT_PAIRING, DEFAULT_PAIRING} for name, _ in _licences_of(pid)}
    return [path for path in (_font_file(name) for name in sorted(names)) if path]


def font_face_css(language='en', pairing=None, url=None):
    """@font-face rules for a page; ``url(path)`` gives each file's address (default: relative to brandkit/)."""
    url = url or (lambda path: os.path.relpath(path, ROOT).replace(os.sep, '/'))
    return '\n'.join("@font-face{font-family:'%s';src:url('%s') format('truetype');%s;font-display:block}" % (family, url(path), descriptors)
                     for family, path, descriptors in faces(language, pairing))


def job_fonts(pairing=None, language='en'):
    """What a job carries about its type: the pairing, each role's CSS font stack and the faces to wait for and check."""
    pid = pairing if pairing in PAIRINGS else DEFAULT_PAIRING
    row = PAIRINGS[pid]
    stacks, probe = {}, []
    for role in ROLES:
        family = FAMILIES[row[role]]['family']
        stacks[role] = '"%s",%s' % (family, FALLBACK[role])
        if role == 'cjk' and row['cjk'] != 'notosanssc':
            # a decorative Chinese face draws Latin letters poorly: model codes and numbers on a Chinese page take
            # the pairing's text face, the Han characters fall through to the Chinese face
            stacks[role] = '"%s",%s' % (FAMILIES[row['text']]['family'], stacks[role])
        if role != 'cjk' or language == 'zh':
            sample = 'Hamburg控制部件' if role == 'cjk' else 'Hamburgefonstiv'
            for weight in ((PROBE[role], 400) if role == 'text' else (PROBE[role],)):
                if [family, weight, sample] not in probe:
                    probe.append([family, weight, sample])
    return {'pairing': pid, 'stacks': stacks, 'probe': probe}


def pairings():
    """The pairings for the brand panel: id, labels and each role's family name."""
    return [{'id': row['id'], 'label': row['label'], 'note': row['note'],
             **{role: FAMILIES[row[role]]['family'] for role in ROLES}} for row in LIBRARY['pairings']]


def ensure_icon(name, style='outline', fetch=True):
    """The path of a Tabler icon by name (downloaded the first time), or None when Tabler has no such icon."""
    if not isinstance(name, str) or not ICON_NAME.match(name) or style not in ('outline', 'filled'):
        return None
    target = ICONS / 'tabler' / style / (name + '.svg')
    if target.is_file():
        return target
    absent = ICONS / 'tabler' / 'absent.json'
    with LOCK:
        known_absent = set(json.loads(absent.read_text())) if absent.is_file() else set()
        if '%s/%s' % (style, name) in known_absent or not fetch:
            return None
        try:
            data = _fetch([mirror + 'icons/%s/%s.svg' % (style, name) for mirror in ICON_MIRRORS], _svg_ok)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                known_absent.add('%s/%s' % (style, name)); _write(absent, json.dumps(sorted(known_absent)).encode())
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
        if not _svg_ok(data):
            return None
        _write(target, data)
        if not (ICONS / 'tabler' / 'LICENSE').is_file():
            try:
                _write(ICONS / 'tabler' / 'LICENSE', _fetch([mirror + 'LICENSE' for mirror in ICON_MIRRORS], lambda data: bool(data)))
            except (urllib.error.URLError, OSError, ValueError):
                pass
    return target


COMMON_ICONS = ('battery-4', 'bolt', 'ruler-measure', 'weight', 'gauge', 'package', 'stack-2', 'shield-check', 'clock-hour-4', 'droplet',
                'bulb', 'rotate-clockwise-2', 'arrow-autofit-height', 'arrow-autofit-width', 'arrows-diagonal', 'box')


def prefetch(languages=('en', 'zh')):
    """Fetch the faces and the common icons in the background (at app start), so a first set does not wait or go without."""
    def run():
        for language in languages:
            ensure_fonts(language)
        for pid in PAIRINGS:            # every pairing in the library, so a brand that picks one does not wait
            for language in languages:
                ensure_fonts(language, pairing=pid)
        for name in COMMON_ICONS:
            ensure_icon(name)
    worker = threading.Thread(target=run, name='brandkit-assets', daemon=True)
    worker.start()
    return worker


def status(language='en', pairing=None):
    """Which faces a set in ``language`` has and which it lacks, without fetching."""
    names = [name for _, name, _, _, languages in _faces_of(pairing or DEFAULT_PAIRING) if language in languages]
    return {'present': [name for name in names if _font_file(name)], 'missing': [name for name in names if not _font_file(name)]}


def icon_markup(name, style='outline'):
    """The inner SVG of an icon (for inlining into a page), or ''."""
    path = ensure_icon(name, style)
    if not path:
        return ''
    root = ET.fromstring(path.read_text(encoding='utf-8'))
    return ET.tostring(root, encoding='unicode').replace('ns0:', '').replace(':ns0', '')


if __name__ == '__main__':
    report = ensure_fonts('en')
    if '--zh' in sys.argv:
        report = {'en': report, 'zh': ensure_fonts('zh')}
    for icon in ('battery-4', 'bolt', 'ruler-measure', 'weight', 'gauge', 'package', 'stack-2', 'shield-check'):
        ensure_icon(icon)
    print(json.dumps(report, indent=1))

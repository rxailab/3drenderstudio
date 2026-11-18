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
VENDOR = Path(os.environ.get('BRANDKIT_VENDOR') or ROOT / 'vendor')
FONTS = VENDOR / 'fonts'
ICONS = VENDOR / 'icons'
GOOGLE = 'https://github.com/google/fonts/raw/main/ofl/'
TABLER_VERSION = '3.31.0'
TABLER = 'https://cdn.jsdelivr.net/npm/@tabler/icons@%s/' % TABLER_VERSION
TIMEOUT = 60
MAX_BYTES = 40 * 1024 * 1024
LOCK = threading.Lock()

# family, file, source, CSS descriptors, languages it is for
FACES = (
    ('Archivo', 'Archivo-Variable.ttf', GOOGLE + 'archivo/Archivo%5Bwdth,wght%5D.ttf', 'font-weight:100 900;font-stretch:62% 125%', ('en', 'zh')),
    ('Barlow Condensed', 'BarlowCondensed-SemiBold.ttf', GOOGLE + 'barlowcondensed/BarlowCondensed-SemiBold.ttf', 'font-weight:600', ('en', 'zh')),
    ('Barlow Condensed', 'BarlowCondensed-Bold.ttf', GOOGLE + 'barlowcondensed/BarlowCondensed-Bold.ttf', 'font-weight:700', ('en', 'zh')),
    ('Barlow Condensed', 'BarlowCondensed-ExtraBold.ttf', GOOGLE + 'barlowcondensed/BarlowCondensed-ExtraBold.ttf', 'font-weight:800', ('en', 'zh')),
    ('Inter', 'Inter-Variable.ttf', GOOGLE + 'inter/Inter%5Bopsz,wght%5D.ttf', 'font-weight:100 900', ('en', 'zh')),
    ('Noto Sans SC', 'NotoSansSC-Variable.ttf', GOOGLE + 'notosanssc/NotoSansSC%5Bwght%5D.ttf', 'font-weight:100 900', ('zh',)),
)
LICENCES = (('Archivo-OFL.txt', GOOGLE + 'archivo/OFL.txt'), ('BarlowCondensed-OFL.txt', GOOGLE + 'barlowcondensed/OFL.txt'),
            ('Inter-OFL.txt', GOOGLE + 'inter/OFL.txt'), ('NotoSansSC-OFL.txt', GOOGLE + 'notosanssc/OFL.txt'))
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


def _write_css():
    rules = []
    for family, name, _, descriptors, _ in FACES:
        if (FONTS / name).is_file():
            rules.append("@font-face{font-family:'%s';src:url('fonts/%s') format('truetype');%s;font-display:block}" % (family, name, descriptors))
    _write(VENDOR / 'fonts.css', ('\n'.join(rules) + '\n').encode())


def ensure_fonts(language='en', fetch=True):
    """The faces a set in ``language`` needs, downloaded if missing. Returns {'present': [...], 'missing': [...]}."""
    key = (language, fetch)
    if key in _checked:
        return _checked[key]
    present, missing = [], []
    with LOCK:
        changed = False
        for family, name, url, _, languages in FACES:
            if language not in languages:
                continue
            target = FONTS / name
            if not target.is_file() and fetch:
                try:
                    data = _get(url)
                    if _font_ok(data):
                        _write(target, data); changed = True
                except (urllib.error.URLError, OSError, ValueError):
                    pass
            (present if target.is_file() else missing).append(name)
        if changed or not (VENDOR / 'fonts.css').is_file():
            if fetch:
                for name, url in LICENCES:
                    if not (FONTS / name).is_file():
                        try:
                            _write(FONTS / name, _get(url))
                        except (urllib.error.URLError, OSError, ValueError):
                            pass
            _write_css()
    result = {'present': present, 'missing': missing}
    if not missing:
        _checked[key] = result
    return result


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
            data = _get(TABLER + 'icons/%s/%s.svg' % (style, name))
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
                _write(ICONS / 'tabler' / 'LICENSE', _get(TABLER + 'LICENSE'))
            except (urllib.error.URLError, OSError, ValueError):
                pass
    return target


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

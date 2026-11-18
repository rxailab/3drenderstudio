"""Brand-kit templates (``brandkit/templates/T01..T12.html``) rendered to PNG without a visible window.

Each template is HTML/CSS/SVG whose layout is worked out by rules in the page itself; the job (a JSON
object) is injected as ``window.JOB``. After layout the shared runtime (``brandkit/engine/runtime.js``)
measures the real DOM and writes a QA record, which is read back through ``--dump-dom``. On top of that
this module checks, pixel by pixel, that no text sits on the product.

Job assets carry two locations: ``file`` (a file:// URI the page loads) and ``path`` (the same file on
disk, read here for the pixel check). Use :func:`asset` to describe one.
"""
import concurrent.futures as cf
import html
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent / 'brandkit'
TEMPLATES = ROOT / 'templates'
TIMEOUT = 90
FLAGS = ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--allow-file-access-from-files',
         '--disable-extensions', '--no-first-run', '--mute-audio', '--no-default-browser-check']
# Checks that mean the layout itself is wrong; the others (a font fallback, a mild upscale) are only reported.
LAYOUT_CHECKS = ('phrase_break', 'leader_over_product', 'frame_cuts_part', 'text_overflow', 'text_max_lines', 'overlap', 'leader_crosses_text', 'leader_crossing', 'text_over_product', 'safe_margin',
                 'product_cropped', 'out_of_canvas', 'product_distortion', 'image_loaded', 'render_error', 'js_error')
_HEADLESS = {}


class RenderError(RuntimeError):
    pass


def chrome_path():
    """A Chromium-family browser to lay the pages out with, or None."""
    for key in ('BRANDKIT_CHROME', 'CHROME_PATH', 'CHROME'):
        value = os.environ.get(key)
        if value and (Path(value).is_file() or shutil.which(value)):
            return shutil.which(value) or value
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome', 'msedge', 'microsoft-edge'):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    for base in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')):
        if base:
            candidates += [Path(base) / 'Google/Chrome/Application/chrome.exe', Path(base) / 'Microsoft/Edge/Application/msedge.exe']
    candidates += [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), Path('/Applications/Chromium.app/Contents/MacOS/Chromium'),
                   Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')]
    return next((str(path) for path in candidates if path.is_file()), None)


def available():
    return chrome_path() is not None


def template_info(template_id):
    """The template's own record: tokens (palette, margins), elements and reference map."""
    return json.loads((TEMPLATES / (template_id + '.json')).read_text(encoding='utf-8'))


def asset(path, **extra):
    """A job asset for a picture on disk: its URI for the page, its path for the pixel check, its size."""
    path = Path(path).resolve()
    with Image.open(path) as image:
        width, height = image.size
    return {'file': path.as_uri(), 'path': str(path), 'width': width, 'height': height, **extra}


def compose(template_id, job):
    source = (TEMPLATES / (template_id + '.html')).read_text(encoding='utf-8')
    public = {**job, 'assets': {key: {k: v for k, v in value.items() if k != 'path' and not k.startswith('_')} for key, value in (job.get('assets') or {}).items()}}
    data = json.dumps(public, ensure_ascii=False).replace('</', '<\\/')
    inject = '<base href="%s/">\n<script>window.JOB = %s;</script>' % (ROOT.as_uri(), data)
    return source.replace('<!--INJECT-->', inject)


def _run(command):
    try:
        return subprocess.run(command, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RenderError('排版浏览器超时') from None


def _headless(executable):
    """'--headless=old' where the browser still has it (exact window-size screenshots), else the new mode."""
    if executable not in _HEADLESS:
        mode = '--headless=new'
        try:
            out = subprocess.run([executable, '--version'], capture_output=True, timeout=20).stdout.decode('utf-8', 'replace')
            major = int(re.search(r'(\d+)\.\d+\.\d+', out).group(1))
            mode = '--headless=old' if major < 132 else '--headless=new'
        except Exception:
            pass
        _HEADLESS[executable] = mode
    return _HEADLESS[executable]


def pixel_checks(qa, job):
    """Text must not sit on product pixels: sample each text box, map it back into the product picture and
    count pixels that are not backdrop (darker than near-white)."""
    elements = qa.get('elements', [])
    images = [row for row in elements if row.get('kind') == 'product_image' and row.get('layout')]
    found = []; cache = {}
    for image_row in images:
        record = (job.get('assets') or {}).get(image_row.get('asset')) or {}
        path = record.get('path')
        if not path:
            continue
        if path not in cache:
            cache[path] = Image.open(path).convert('RGB')
        picture = cache[path]; pixels = picture.load()
        lx, ly, lw, lh = image_row['layout']; cx, cy = lx + lw / 2, ly + lh / 2
        theta = -math.radians(image_row.get('rotate', 0))
        for text in elements:
            if text.get('kind') != 'text' or text.get('exempt'):
                continue
            hits = total = 0
            for i in range(17):
                for j in range(17):
                    x = text['x'] + text['w'] * i / 16; y = text['y'] + text['h'] * j / 16
                    dx, dy = x - cx, y - cy
                    u = dx * math.cos(theta) - dy * math.sin(theta); v = dx * math.sin(theta) + dy * math.cos(theta)
                    ax, ay = (u + lw / 2) / lw * picture.width, (v + lh / 2) / lh * picture.height
                    total += 1
                    if 0 <= ax < picture.width and 0 <= ay < picture.height and min(pixels[int(ax), int(ay)]) < 228:
                        hits += 1
            if hits:
                ratio = hits / total
                found.append({'text': text['id'], 'image': image_row['id'], 'product_pixel_ratio': round(ratio, 4)})
                if ratio > 0.01:
                    qa['issues'].append({'check': 'text_over_product', 'id': '%s×%s' % (text['id'], image_row['id']),
                                         'detail': '%.1f%% of sampled points on product pixels' % (ratio * 100)})
    for image_row in images:
        record = (job.get('assets') or {}).get(image_row.get('asset')) or {}
        if record.get('path') in cache and not image_row.get('rotate'):
            _leader_checks(qa, image_row, cache[record['path']], job)
    for picture in cache.values():
        picture.close()
    qa['pixel_checks'] = found


LEADER_OVER_PRODUCT = .45      # of the product's drawn width


def _leader_checks(qa, image_row, picture, job):
    """A line to a part that shows as separate patches (one of two rivets) should not run across the product to the far
    one: the length drawn over product pixels, against the product's width on the page. Only features marked
    ``other_patch`` are checked; a line to the middle of one large part has to cross that part."""
    pixels = picture.load(); lx, ly, lw, lh = image_row['layout']
    marked = {'ldr%d' % (index + 1) for index, row in enumerate(job.get('features') or []) if isinstance(row, dict) and row.get('other_patch')}
    for leader in qa.get('leaders') or []:
        if leader.get('id') not in marked:
            continue
        points = leader.get('pts') or []
        over = 0.0
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2 - x1, y2 - y1); steps = max(1, int(length / 6))
            for step in range(steps):
                x = x1 + (x2 - x1) * (step + .5) / steps; y = y1 + (y2 - y1) * (step + .5) / steps
                ax, ay = (x - lx) / lw * picture.width, (y - ly) / lh * picture.height
                if 0 <= ax < picture.width and 0 <= ay < picture.height and min(pixels[int(ax), int(ay)]) < 228:
                    over += length / steps
        if over > LEADER_OVER_PRODUCT * lw:
            qa['issues'].append({'check': 'leader_over_product', 'id': leader.get('id'),
                                 'detail': '%.0f px of the line over the product (%.0f px wide)' % (over, lw)})


def frame_checks(qa, job):
    """A close-up frame may cut a large part, but not slice through a small separate one (a badge, a rivet): one whose
    box is partly inside what the frame shows and partly outside. The part map comes with the asset (``_parts``)."""
    elements = qa.get('elements', []); frames = {row.get('id'): row for row in elements}
    for row in elements:
        if row.get('kind') != 'detail_image' or not row.get('w') or not row.get('h'):
            continue
        parts = ((job.get('assets') or {}).get(row.get('asset')) or {}).get('_parts') or []
        frame = frames.get(str(row.get('id')) + '_frame')
        if not parts or not frame:
            continue
        # A feathered edge hides the picture from about the middle of its fade: that is where a part looks cut.
        fade = {side: float(size) / 2 for side, size in (frame.get('feather') or {}).items()}
        window = [(frame['x'] + fade.get('left', 0) - row['x']) / row['w'], (frame['y'] + fade.get('top', 0) - row['y']) / row['h'],
                  (frame['x'] + frame['w'] - fade.get('right', 0) - row['x']) / row['w'],
                  (frame['y'] + frame['h'] - fade.get('bottom', 0) - row['y']) / row['h']]
        for part in parts:
            box = part['box']; area = (box[2] - box[0]) * (box[3] - box[1])
            inside = max(0, min(box[2], window[2]) - max(box[0], window[0])) * max(0, min(box[3], window[3]) - max(box[1], window[1]))
            share = inside / area if area > 0 else 0
            if .04 < share < .88:
                qa['issues'].append({'check': 'frame_cuts_part', 'id': '%s×%s' % (row['id'], part['name']),
                                     'detail': '%.0f%% of %s inside the frame' % (share * 100, part['name'])})


def phrase_checks(qa, job):
    """A Chinese headline wraps only where its author broke it: one more line than written means a phrase was split."""
    if job.get('lang') != 'zh':
        return
    for row in qa.get('elements', []):
        if row.get('kind') == 'text' and row.get('tier') == 'headline' and not row.get('exempt'):
            written = str(row.get('text', '')).count('\n') + 1
            if row.get('lines', 0) > written:
                qa['issues'].append({'check': 'phrase_break', 'id': row['id'], 'detail': '%d lines for %d written' % (row['lines'], written)})


def render(template_id, job, out, work=None):
    """Lay out one page and write it to ``out`` (PNG). Returns the QA record (``issues`` empty when it passed)."""
    executable = chrome_path()
    if not executable:
        raise RenderError('没有找到 Chrome / Edge 浏览器，无法排版品牌模板。请安装 Chrome，或在 .env 中设置 BRANDKIT_CHROME。')
    if not (TEMPLATES / (template_id + '.html')).is_file():
        raise RenderError('未知的模板 %s' % template_id)
    width, height = job['canvas']['width'], job['canvas']['height']
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work or out.parent); work.mkdir(parents=True, exist_ok=True)
    page = work / ('%s-%s.html' % (out.stem, uuid.uuid4().hex[:8]))
    page.write_text(compose(template_id, job), encoding='utf-8')
    url = page.as_uri(); size = '--window-size=%d,%d' % (width, height)
    flags = [_headless(executable), *FLAGS, size]
    try:
        with cf.ThreadPoolExecutor(2) as pool:
            shot = pool.submit(_run, [executable, *flags, '--screenshot=%s' % out, url])
            dump = pool.submit(_run, [executable, *flags, '--virtual-time-budget=6000', '--dump-dom', url])
            shot_result, dump_result = shot.result(), dump.result()
    finally:
        page.unlink(missing_ok=True)
    dom = dump_result.stdout.decode('utf-8', 'replace')
    match = re.search(r'<script type="application/json" id="qa-result">(.*?)</script>', dom, re.S)
    qa = json.loads(html.unescape(match.group(1))) if match else {'issues': [{'check': 'render_error', 'detail': 'no qa-result (script error?)'}]}
    qa.setdefault('issues', [])
    if not out.is_file():
        raise RenderError('排版浏览器没有输出图片：%s' % shot_result.stderr.decode('utf-8', 'replace')[-300:])
    pixel_checks(qa, job)
    frame_checks(qa, job)
    phrase_checks(qa, job)
    with Image.open(out) as image:
        if image.size != (width, height):
            # The new headless mode can add the browser's frame; the page itself starts at the top left.
            if image.width < width or image.height < height:
                raise RenderError('排版结果尺寸不对：%dx%d' % image.size)
            image.crop((0, 0, width, height)).save(out)
    qa['render'] = {'png': str(out), 'size': [width, height], 'browser': Path(executable).name}
    return qa


def layout_issues(qa):
    """The QA issues that mean the page is laid out wrongly (an upscale over 1.5x counts too)."""
    return [row for row in qa.get('issues', []) if row.get('check') in LAYOUT_CHECKS or (row.get('check') == 'upscale' and row.get('severity') == 'fail')]

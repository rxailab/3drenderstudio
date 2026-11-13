"""E-commerce delivery built only on images this application really rendered.

Three things live here:

* ``overview``   - measures a rendered shot against a marketplace preset and says,
                   per rule, whether it already passes, is fixed automatically by
                   the export, or needs the user (for example a 4K render).
* ``compose``    - turns a render plus its product matte into a marketplace image:
                   pure-white or transparent background, product centred at the
                   preset's share of the frame, padded to the preset's ratio.
* export tasks   - a background job that writes the sized, size-limited files, a
                   manifest of what was verified on the written files, and a ZIP.

The product matte comes from ``blender/render_matte.py``, which re-opens the
scene saved beside each render. No pixel of the product is generated or
retouched here: compositing only replaces what is outside the product.
"""
import atexit
import copy
import hashlib
import io
import json
import queue
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageMath, ImageOps

import ecommerce_platforms as presets
from studio_store import ROOT, DATA, LOCK, get, put, all_records, atomic_json

KIND = 'ecommerce_export'
MATTE_SCRIPT = ROOT / 'blender/render_matte.py'
REVISION = 1
BACKGROUNDS = ('main_white', 'all_white', 'original')
KEEP_EXPORTS = 6
QUEUE = queue.Queue()
PROCESSES = {}
RUNNING = set()
_MEASUREMENTS = {}
_evaluate = getattr(ImageMath, 'unsafe_eval', None) or getattr(ImageMath, 'eval')


class ExportCancelled(Exception):
    pass


# ---------------------------------------------------------------- sources

def _ratio(aspect):
    width, height = (int(part) for part in aspect.split(':'))
    return width / height


def _project_file(project_id, url, suffixes):
    if not isinstance(url, str) or not url.startswith('/files/'):
        return None
    path = (DATA / url[7:]).resolve()
    if (DATA / project_id).resolve() not in path.parents or path.suffix.lower() not in suffixes or not path.is_file():
        return None
    return path


def file_url(path):
    return '/files/' + Path(path).relative_to(DATA).as_posix()


def best_output(project, shot_id):
    """Newest plan version first; within it, the render with the most pixels."""
    usable = [output for output in project.get('outputs', [])
              if output.get('shot_id') == shot_id and output.get('quality')!='thumbnail'
              and _project_file(project['id'], output.get('png_url'), ('.png',))]
    if not usable:
        return None
    return max(usable, key=lambda output: (output.get('version', 0), output.get('width', 0) * output.get('height', 0),
                                           output.get('created_at', 0)))


def _shot_style(output, shot):
    plan = output.get('effective_plan') if isinstance(output.get('effective_plan'), dict) else {}
    rendered = next((item for item in plan.get('shots', []) if item.get('id') == shot['id']), shot)
    return rendered.get('style', plan.get('style', 'soft'))


def main_shot_id(project):
    shots = project.get('plan', {}).get('shots', [])
    hero = next((shot for shot in shots if shot['id'] == 'hero'), None)
    first_full = next((shot for shot in shots if shot.get('kind') == 'full'), None)
    chosen = hero or first_full or (shots[0] if shots else None)
    return chosen['id'] if chosen else None


def matte_available():
    from engine import blender_path
    return bool(blender_path()) and MATTE_SCRIPT.is_file()


def settings(project):
    """Remembered choices, always valid even for projects created before v19."""
    saved = project.get('ecommerce') if isinstance(project.get('ecommerce'), dict) else {}
    ident = saved.get('platform')
    try:
        presets.platform(ident)
    except ValueError:
        ident = presets.DEFAULT_PLATFORM
    background = saved.get('background') if saved.get('background') in BACKGROUNDS else 'main_white'
    return {'platform': ident, 'background': background, 'shadow': saved.get('shadow') is not False,
            'transparent': saved.get('transparent') is True}


# ------------------------------------------------------------ measurement

def _min_channel(image):
    red, green, blue = image.split()
    return ImageChops.darker(ImageChops.darker(red, green), blue)


def border_whiteness(image):
    """How white the outer 2% of the frame is; marketplaces sample this area."""
    image = image.convert('RGB')
    width, height = image.size
    band = max(2, round(min(width, height) * .02))
    low = _min_channel(image)
    histogram = [0] * 256
    for box in ((0, 0, width, band), (0, height - band, width, height),
                (0, band, band, height - band), (width - band, band, width, height - band)):
        for value, count in enumerate(low.crop(box).histogram()):
            histogram[value] += count
    total = sum(histogram) or 1
    darkest = next((value for value, count in enumerate(histogram) if count), 255)
    pure = histogram[255] / total
    near = sum(histogram[250:]) / total
    level = 'pure' if pure >= .999 else 'near' if near >= .999 else 'not_white'
    return {'level': level, 'pure_fraction': round(pure, 5), 'near_fraction': round(near, 5), 'darkest': darkest}


def product_box(matte, threshold=24):
    """Pixel box of real product coverage; faint anti-aliasing is ignored."""
    return matte.point(lambda value: 255 if value > threshold else 0).getbbox()


def measure_output(project_id, output):
    """Background and framing of one saved render, cached by file identity."""
    path = _project_file(project_id, output.get('png_url'), ('.png',))
    if path is None:
        return None
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _MEASUREMENTS.get(key)
    if cached is None:
        with Image.open(path) as image:
            image.load()
            cached = {'width': image.width, 'height': image.height, 'border': border_whiteness(image)}
        if len(_MEASUREMENTS) > 400:
            _MEASUREMENTS.clear()
        _MEASUREMENTS[key] = cached
    result = copy.deepcopy(cached)
    checks = output.get('checks') if isinstance(output.get('checks'), dict) else {}
    rect = checks.get('projected_bounds')
    if (isinstance(rect, list) and len(rect) == 4
            and all(type(value) in (int, float) for value in rect) and rect[2] > rect[0] and rect[3] > rect[1]):
        result['extent'] = [round(rect[2] - rect[0], 4), round(rect[3] - rect[1], 4)]
        result['fill'] = round(min(1.5, max(result['extent'])), 4)
        result['center'] = [round((rect[0] + rect[2]) / 2, 4), round((rect[1] + rect[3]) / 2, 4)]
        # Renders made before the vertex-accurate check used loose bounding boxes.
        result['fill_exact'] = 'vertices' in str(checks.get('method', ''))
    return result


# ---------------------------------------------------------------- overview

def _percent(value):
    return '%d%%' % round(value * 100)


def _megabytes(value):
    return '%.0f MB' % (value / presets.MB) if value >= presets.MB else '%d KB' % round(value / 1024)


def planned_size(preset, source, extent, cut, reframe, aspect):
    """Pixels the export can deliver without enlarging the render."""
    width, height = source
    ratio = _ratio(aspect)
    if cut and reframe and extent:
        # The crop window is sized from the product, so the product keeps its pixels.
        window_width = max(extent[0] * width, extent[1] * height * ratio) / preset['fill_target']
        window_long = max(window_width, window_width / ratio)
    elif cut:
        window_long = max(width, width / ratio) if width / max(height, 1) >= ratio else max(height * ratio, height)
    else:
        window_long = max(width, height)
    long_side = max(1, int(min(preset['target_px'], window_long)))
    return (long_side, round(long_side / ratio)) if ratio >= 1 else (round(long_side * ratio), long_side)


def shot_report(project, shot, preset, options, capability):
    """Rule-by-rule state of one shot for one marketplace."""
    main = shot['id'] == main_shot_id(project)
    output = best_output(project, shot['id'])
    report = {'shot_id': shot['id'], 'label': shot.get('label', shot['id']), 'kind': shot.get('kind', 'full'),
              'main': main, 'output': None, 'items': [], 'status': 'missing'}
    if output is None:
        report['items'].append({'id': 'render', 'status': 'action', 'action': 'preview', 'label': '渲染图', 'value': '未出图',
                                'detail': '这个镜头还没有渲染图片。'})
        return report
    measured = measure_output(project['id'], output) or {}
    width, height = output.get('width', 0), output.get('height', 0)
    stale = output.get('version') != project.get('version')
    style = _shot_style(output, shot)
    white_source = style == 'white'
    cut = options['background'] != 'original' and capability and (main or white_source or options['background'] == 'all_white')
    reframe = cut and shot.get('kind') != 'detail'
    aspect_now = next((name for name in ('1:1', '4:3', '3:4') if height and abs(width / height - _ratio(name)) < .01), None)
    aspect = preset['aspect'] if aspect_now not in preset['aspects'] else aspect_now
    report['output'] = {'job_id': output.get('job_id'), 'quality': output.get('quality'), 'version': output.get('version'),
                        'width': width, 'height': height, 'png_url': output.get('png_url'), 'stale': stale,
                        'style': style, 'aspect': aspect_now}
    report['export'] = {'aspect': aspect, 'cut': bool(cut), 'reframe': bool(reframe)}
    items = report['items']
    fill = measured.get('fill')
    size = planned_size(preset, (width, height), measured.get('extent'), cut, reframe, aspect)
    report['export']['size'] = list(size)
    long_side = max(size)
    if long_side >= preset['min_px']:
        items.append({'id': 'resolution', 'status': 'pass', 'label': '分辨率', 'value': '%d × %d' % size,
                      'detail': '将导出 %d × %d px（平台最低 %d px）。' % (size[0], size[1], preset['min_px'])})
    else:
        preview = output.get('quality') == 'preview'
        items.append({'id': 'resolution', 'status': 'action', 'action': 'render', 'label': '分辨率',
                      'value': '%d px，需渲染 4K' % max(width, height),
                      'detail': ('当前只有 %d px 预览图，' % max(width, height) if preview else '当前图片只有 %d px，' % max(width, height))
                                + '平台最低 %d px。渲染 4K 后即可导出 %d px。' % (preset['min_px'], preset['target_px'])})
    if stale:
        items.append({'id': 'version', 'status': 'warn', 'action': 'preview', 'label': '方案版本',
                      'value': 'v%s，当前 v%s' % (output.get('version'), project.get('version')),
                      'detail': '图片来自方案 v%s，当前方案 v%s 还没有渲染这个镜头。' % (output.get('version'), project.get('version'))})
    if aspect_now in preset['aspects']:
        items.append({'id': 'aspect', 'status': 'pass', 'label': '画幅', 'value': aspect_now, 'detail': '%s 符合平台要求。' % aspect_now})
    elif cut:
        items.append({'id': 'aspect', 'status': 'auto', 'label': '画幅', 'value': '%s → %s' % (aspect_now or '其他', aspect),
                      'detail': '当前 %s，导出时自动重新构图为 %s。' % (aspect_now or '非标准画幅', aspect)})
    else:
        items.append({'id': 'aspect', 'status': 'auto', 'label': '画幅', 'value': '%s → %s' % (aspect_now or '其他', aspect),
                      'detail': '当前 %s，导出时围绕产品裁切为 %s；产品放不下时保持原画幅并提示。' % (aspect_now or '非标准画幅', aspect)})
    if main:
        border = measured.get('border', {})
        level = border.get('level')
        if cut:
            items.append({'id': 'background', 'status': 'pass' if level == 'pure' else 'auto', 'label': '纯白背景',
                          'value': '纯白' if level == 'pure' else '导出时处理',
                          'detail': '边缘已是纯白 RGB 255。' if level == 'pure' else
                                    '渲染图边缘有阴影或灰度（最暗 %s），导出时自动处理为纯白 RGB 255。' % border.get('darkest', '—')})
        elif preset['white'] == 'required' and level != 'pure':
            items.append({'id': 'background', 'status': 'action', 'action': 'background', 'label': '纯白背景', 'value': '不是纯白',
                          'detail': '平台要求主图纯白底，而当前设置保持渲染原样（边缘最暗 %s）。请把背景处理改为「主图纯白底」。' % border.get('darkest', '—')
                                    if capability else '平台要求主图纯白底；纯白处理需要 Blender 运行环境完整可用。'})
        elif preset['white'] != 'optional':
            items.append({'id': 'background', 'status': 'pass' if level == 'pure' else 'info', 'label': '纯白背景',
                          'value': '纯白' if level == 'pure' else '保持原样',
                          'detail': '边缘已是纯白。' if level == 'pure' else '平台建议主图使用纯白底；当前设置保持渲染原样。'})
        if shot.get('kind') != 'detail':
            target = preset['fill_target']
            if reframe:
                items.append({'id': 'fill', 'status': 'auto' if fill is None or abs(fill - target) > .02 else 'pass', 'label': '产品占比',
                              'value': ('%s → %s' % (_percent(fill), _percent(target))) if fill and abs(fill - target) > .02 else _percent(target),
                              'detail': ('渲染图中约占 %s，' % _percent(fill) if fill else '') +
                                        '导出时自动居中并构图到 %s%s。' % (_percent(target), '（平台要求不低于 %s）' % _percent(preset['fill_min']) if preset.get('fill_min') else '')})
            elif preset.get('fill_min') and fill is not None and fill + .005 < preset['fill_min']:
                items.append({'id': 'fill', 'status': 'action', 'action': 'background', 'label': '产品占比',
                              'value': '%s，需 ≥ %s' % (_percent(fill), _percent(preset['fill_min'])),
                              'detail': '约占 %s，平台要求不低于 %s。把背景处理改为「主图纯白底」即可自动构图。' % (_percent(fill), _percent(preset['fill_min']))})
            elif fill is not None:
                items.append({'id': 'fill', 'status': 'pass', 'label': '产品占比', 'value': _percent(fill), 'detail': '约占画面 %s。' % _percent(fill)})
        items.append({'id': 'file', 'status': 'pass', 'label': '文件', 'value': 'JPG ≤ ' + _megabytes(preset['max_bytes']),
                      'detail': '导出 JPG，自动压缩到 %s 以内；渲染图不含水印、边框或附加文字。' % _megabytes(preset['max_bytes'])})
    report['status'] = 'action' if any(item['status'] == 'action' for item in items) else 'ready'
    return report


def overview(project, platform_id=None, options=None):
    """Everything the 电商出图 panel needs for one marketplace."""
    chosen = settings(project)
    if options:
        chosen.update({key: options[key] for key in ('background', 'shadow', 'transparent') if key in options})
    if platform_id:
        chosen['platform'] = platform_id
    preset = presets.platform(chosen['platform'])
    capability = matte_available()
    shots = [shot_report(project, shot, preset, chosen, capability) for shot in project.get('plan', {}).get('shots', [])]
    rendered = [shot for shot in shots if shot['output']]
    main = next((shot for shot in shots if shot['main']), None)
    exportable = [shot for shot in rendered if shot['status'] != 'action']
    if not main or not main['output']:
        step = 'preview'
    elif any(item['id'] == 'resolution' and item['status'] == 'action' for item in main['items']):
        step = 'render'
    else:
        step = 'export'
    return {'platform': preset, 'settings': chosen, 'matte_available': capability, 'main_shot_id': main['shot_id'] if main else None,
            'shots': shots, 'step': step,
            'counts': {'shots': len(shots), 'rendered': len(rendered), 'exportable': len(exportable)},
            'plan_recommendation': presets.plan_recommendation(preset, project.get('plan', {}))}


# --------------------------------------------------------------- compose

def _window(image, left, top, size, fill):
    canvas = Image.new(image.mode, size, fill)
    canvas.paste(image, (-left, -top))
    return canvas


def _edge_falloff(size, clear=.012, fade=.07):
    """1 inside the frame, easing to exactly 0 at the border."""
    side = 192
    mask = Image.new('L', (side, side))
    pixels = mask.load()
    for y in range(side):
        dy = min(y + .5, side - y - .5) / side
        for x in range(side):
            distance = min(min(x + .5, side - x - .5) / side, dy)
            t = max(0., min(1., (distance - clear) / fade))
            pixels[x, y] = round(255 * t * t * (3 - 2 * t))
    return mask.resize(size, Image.Resampling.BILINEAR)


def _proximity(matte, reach=.07):
    """Soft mask of the area close to the product, where a grounding shadow belongs."""
    width, height = matte.size
    scale = 320 / max(width, height)
    small = matte.resize((max(8, round(width * scale)), max(8, round(height * scale))), Image.Resampling.BILINEAR)
    spread = small.filter(ImageFilter.GaussianBlur(320 * reach))
    return spread.point(lambda value: min(255, round(value * 3.2))).resize(matte.size, Image.Resampling.BILINEAR)


def _separate(beauty, matte):
    """Estimate the product colour and the backdrop behind anti-aliased edges.

    A normalised blur of the backdrop-only pixels extends the backdrop under the
    product edge, so the edge can be un-mixed whatever the studio colour is.
    """
    radius = max(2., max(beauty.size) / 1024)
    outside = ImageOps.invert(matte)
    weight = outside.filter(ImageFilter.GaussianBlur(radius))
    foreground, backdrop = [], []
    for band in beauty.split():
        spread = ImageChops.multiply(band, outside).filter(ImageFilter.GaussianBlur(radius))
        behind = _evaluate('convert(min(float(s)*255/max(float(w),1),255),"L")', s=spread, w=weight)
        backdrop.append(behind)
        foreground.append(_evaluate(
            'convert(min(max((float(c)*255-(255-float(a))*float(g))/max(float(a),1),0),255),"L")',
            c=band, a=matte, g=behind))
    return Image.merge('RGB', foreground), Image.merge('RGB', backdrop)


def compose(beauty, matte, aspect, target_px, background='white', shadow=True, fill_target=.85,
            reframe=True, white_source=True):
    """Place a rendered shot on a marketplace canvas.

    ``background``: 'white' and 'transparent' cut the product out with ``matte``;
    'original' keeps the rendered backdrop. Returns (image, info). The render is
    never enlarged: ``info['size']`` can be smaller than ``target_px``.
    """
    beauty = beauty.convert('RGB')
    width, height = beauty.size
    ratio = _ratio(aspect)
    cut = background in ('white', 'transparent') and matte is not None
    box = product_box(matte) if matte is not None else None
    inside = box is not None and box[0] > 1 and box[1] > 1 and box[2] < width - 1 and box[3] < height - 1
    info = {'background': background if cut or background == 'original' else 'original', 'aspect': aspect}
    if cut and reframe and inside:
        box_width, box_height = box[2] - box[0], box[3] - box[1]
        window_width = max(box_width, box_height * ratio) / fill_target
        window_height = window_width / ratio
        left = (box[0] + box[2]) / 2 - window_width / 2
        top = (box[1] + box[3]) / 2 - window_height / 2
        info['framing'] = 'reframed'
    elif cut:
        window_width, window_height = (width, width / ratio) if width / height >= ratio else (height * ratio, height)
        left, top = (width - window_width) / 2, (height - window_height) / 2
        info['framing'] = 'padded' if abs(width / height - ratio) > .01 else 'kept'
    else:
        window_width, window_height, left, top = width, height, 0, 0
        info['framing'] = 'kept'
        if abs(width / height - ratio) > .01:
            crop_width, crop_height = (height * ratio, height) if width / height > ratio else (width, width / ratio)
            fits = inside and box[2] - box[0] <= crop_width * .96 and box[3] - box[1] <= crop_height * .96
            if fits:
                center_x, center_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                left = min(max(center_x - crop_width / 2, 0), width - crop_width)
                top = min(max(center_y - crop_height / 2, 0), height - crop_height)
                window_width, window_height = crop_width, crop_height
                info['framing'] = 'cropped'
            else:
                info['aspect'] = None
                info['warning'] = '保持渲染原样的背景无法补白；产品放不进 %s 裁切范围，已保持原画幅。' % aspect
    left, top = round(left), round(top)
    window = (max(1, round(window_width)), max(1, round(window_height)))
    if cut:
        plate = _window(beauty, left, top, window, (255, 255, 255))
        alpha = _window(matte, left, top, window, 0)
        product, backdrop = _separate(plate, alpha)
        darkness = None
        if shadow and white_source:
            # Only a neutral darkening of the white floor counts as shadow, kept
            # near the product and faded out before it can reach the border.
            darkness = ImageOps.invert(backdrop.convert('L')).point(lambda value: 0 if value <= 5 else min(255, round((value - 5) * 1.02)))
            darkness = ImageChops.multiply(ImageChops.multiply(darkness, _proximity(alpha)), _edge_falloff(window))
        info['shadow'] = darkness is not None
        if background == 'white':
            floor = Image.merge('RGB', [ImageOps.invert(darkness)] * 3) if darkness is not None else Image.new('RGB', window, (255, 255, 255))
            result = Image.composite(product, floor, alpha)
        else:
            if darkness is None:
                result = product.copy()
                result.putalpha(alpha)
            else:
                total = _evaluate('convert(min(float(a)+(255-float(a))*float(s)/255+0.5,255),"L")', a=alpha, s=darkness)
                bands = [_evaluate('convert(min(float(c)*float(a)/max(float(t),1),255),"L")', c=band, a=alpha, t=total)
                         for band in product.split()]
                result = Image.merge('RGBA', bands + [total])
        framed = product_box(alpha)
        if framed:
            info['fill'] = round(max((framed[2] - framed[0]) / window[0], (framed[3] - framed[1]) / window[1]), 4)
            info['center'] = [round((framed[0] + framed[2]) / 2 / window[0], 4), round((framed[1] + framed[3]) / 2 / window[1], 4)]
    else:
        result = beauty.crop((left, top, left + window[0], top + window[1])) if info['framing'] == 'cropped' else beauty
        if box is not None and info['framing'] in ('kept', 'cropped'):
            info['fill'] = round(max((box[2] - box[0]) / window[0], (box[3] - box[1]) / window[1]), 4)
    current_ratio = result.width / result.height
    long_side = min(int(target_px), max(result.size))
    if info.get('aspect'):
        size = (long_side, round(long_side / ratio)) if ratio >= 1 else (round(long_side * ratio), long_side)
    else:
        size = (long_side, round(long_side / current_ratio)) if current_ratio >= 1 else (round(long_side * current_ratio), long_side)
    if size != result.size:
        result = result.resize(size, Image.Resampling.LANCZOS)
    info['size'] = list(result.size)
    return result, info


def encode_jpeg(image, max_bytes, min_px=0):
    """Highest JPEG quality that fits ``max_bytes``; shrink only as a last resort."""
    image = image.convert('RGB')
    while True:
        for quality in (95, 92, 90, 87, 84, 80, 75, 70, 64, 58):
            buffer = io.BytesIO()
            image.save(buffer, 'JPEG', quality=quality, optimize=True, subsampling=0 if quality >= 90 else 2, dpi=(72, 72))
            if not max_bytes or buffer.tell() <= max_bytes:
                return buffer.getvalue(), quality, image
        smaller = (round(image.width * .85), round(image.height * .85))
        if max(smaller) < max(min_px, 64):
            return buffer.getvalue(), quality, image
        image = image.resize(smaller, Image.Resampling.LANCZOS)


# ------------------------------------------------------------------ matte

def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _matte_path(project_id, output, blend):
    identity = '|'.join([output.get('blend_sha256') or _digest(blend), _digest(MATTE_SCRIPT),
                         str(output.get('width')), str(output.get('height'))])
    return DATA / project_id / 'ecommerce' / 'mattes' / (hashlib.sha256(identity.encode()).hexdigest()[:32] + '.png')


def _load_matte(path, size):
    with Image.open(path) as image:
        image.load()
        if image.size != tuple(size) or 'A' not in image.getbands():
            raise RuntimeError('产品蒙版与图片尺寸不一致')
        return image.getchannel('A')


def product_matte(task_id, project_id, output, cancelled=lambda: False):
    """Coverage of the product in one saved render; rendered once, then reused."""
    from engine import blender_path, PROCESSES as RENDERS
    blend = _project_file(project_id, output.get('blend_url'), ('.blend',))
    if blend is None:
        raise RuntimeError('这张图片没有保存的 Blender 场景，无法分离产品与背景')
    size = (output['width'], output['height'])
    target = _matte_path(project_id, output, blend)
    if target.is_file():
        try:
            return _load_matte(target, size)
        except (OSError, RuntimeError):
            target.unlink(missing_ok=True)
    executable = blender_path()
    if not executable or not MATTE_SCRIPT.is_file():
        raise RuntimeError('纯白底与透明底处理需要 Blender 和 blender/render_matte.py')
    folder = target.parent / (target.stem + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    try:
        request = {'blend_path': str(blend), 'matte_path': str(folder / 'matte.png'), 'result_path': str(folder / 'result.json'),
                   'size': list(size), 'samples': 16}
        # A 4K render may already own the GPU; flat coverage is cheap on the CPU.
        if RENDERS:
            request['device'] = 'CPU'
        atomic_json(folder / 'request.json', request)
        command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '4', '--python-exit-code', '1',
                   '--python', str(MATTE_SCRIPT), '--', str(folder / 'request.json')]
        started = time.monotonic()
        with (folder / 'blender.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            PROCESSES[task_id] = process
            try:
                while process.poll() is None:
                    if cancelled():
                        process.terminate()
                        raise ExportCancelled()
                    if time.monotonic() - started > 900:
                        process.terminate()
                        raise RuntimeError('生成产品蒙版超时')
                    time.sleep(.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                PROCESSES.pop(task_id, None)
        result_path = folder / 'result.json'
        result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
        if process.returncode or result.get('success') is not True:
            raise RuntimeError('未能生成产品蒙版：' + str(result.get('error', 'Blender 没有返回结果'))[:200])
        matte = _load_matte(folder / 'matte.png', size)
        (folder / 'matte.png').replace(target)
        return matte
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ----------------------------------------------------------- export tasks

def public(task):
    return {key: copy.deepcopy(task.get(key)) for key in (
        'id', 'project_id', 'platform', 'platform_name', 'options', 'status', 'stage', 'progress', 'error', 'warnings',
        'files', 'download_url', 'created_at', 'finished_at', 'project_version')}


def project_exports(project_id, limit=3):
    tasks = sorted((task for task in all_records(KIND) if task.get('project_id') == project_id),
                   key=lambda task: task.get('created_at', 0), reverse=True)
    return [public(task) for task in tasks[:limit]]


def resolve_options(body, project):
    chosen = settings(project)
    for key in ('background', 'shadow', 'transparent', 'platform'):
        if key in body:
            chosen[key] = body[key]
    preset = presets.platform(chosen['platform'])
    if chosen['background'] not in BACKGROUNDS:
        raise ValueError('背景处理方式无效')
    if type(chosen['shadow']) is not bool or type(chosen['transparent']) is not bool:
        raise ValueError('阴影与透明底选项必须为布尔值')
    return chosen, preset


def save_settings(project_id, body):
    """Remember the marketplace and export choices with the project."""
    if not isinstance(body, dict):
        raise ValueError('电商设置必须是参数对象')
    with LOCK:
        project = get('project', project_id)
        chosen, _ = resolve_options(body, project)
        project['ecommerce'] = chosen
        put('project', project)
    return chosen


def _prune(project_id):
    finished = sorted((task for task in all_records(KIND)
                       if task.get('project_id') == project_id and task.get('status') not in ('queued', 'running')),
                      key=lambda task: task.get('created_at', 0), reverse=True)
    from studio_store import connection
    for task in finished[KEEP_EXPORTS:]:
        shutil.rmtree(DATA / project_id / 'ecommerce' / 'exports' / task['id'], ignore_errors=True)
        with connection() as database:
            database.execute('DELETE FROM records WHERE kind=? AND id=?', (KIND, task['id']))


def submit(project_id, body):
    if not isinstance(body, dict):
        raise ValueError('导出请求必须是参数对象')
    with LOCK:
        project = get('project', project_id)
        chosen, preset = resolve_options(body, project)
        known = [shot['id'] for shot in project['plan']['shots']]
        wanted = body.get('shot_ids')
        if wanted is not None and (not isinstance(wanted, list) or not wanted or any(shot not in known for shot in wanted)):
            raise ValueError('请选择至少一个有效镜头')
        sources = []
        for shot_id in (known if wanted is None else [shot for shot in known if shot in wanted]):
            output = best_output(project, shot_id)
            if output is not None:
                sources.append({key: output.get(key) for key in ('shot_id', 'job_id', 'quality', 'version', 'png_url', 'blend_url',
                                                                 'blend_sha256', 'width', 'height')})
        if not sources:
            raise ValueError('还没有可导出的渲染图片，请先生成预览或 4K 成品')
        if chosen['background'] != 'original' and not matte_available():
            raise ValueError('纯白底处理需要 Blender 和 blender/render_matte.py；也可以把背景处理改为「保持渲染原样」')
        fingerprint = hashlib.sha256(json.dumps([REVISION, project_id, chosen, sources, preset], sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
        for task in all_records(KIND):
            if task.get('fingerprint') != fingerprint:
                continue
            if task['status'] in ('queued', 'running') or (
                    task['status'] == 'completed' and _project_file(project_id, task.get('download_url'), ('.zip',))):
                return public(task)
        project['ecommerce'] = chosen
        put('project', project)
        task = {'id': uuid.uuid4().hex, 'project_id': project_id, 'platform': preset['id'], 'platform_name': preset['name'],
                'preset': preset, 'options': chosen, 'sources': sources, 'fingerprint': fingerprint, 'status': 'queued',
                'stage': '等待导出', 'progress': 0, 'error': None, 'warnings': [], 'files': [], 'download_url': None,
                'created_at': time.time(), 'project_version': project.get('version'), 'slug': project.get('slug') or 'product'}
        put(KIND, task)
        _prune(project_id)
        QUEUE.put(task['id'])
        return public(task)


def update(ident, **fields):
    with LOCK:
        task = get(KIND, ident)
        if task['status'] == 'cancelled':
            return task
        task.update(fields)
        put(KIND, task)
        return task


def cancel(ident):
    with LOCK:
        task = get(KIND, ident)
        if task['status'] in ('queued', 'running'):
            task.update(status='cancelled', stage='已取消导出', finished_at=time.time())
            put(KIND, task)
            process = PROCESSES.get(ident)
            if process and process.poll() is None:
                process.terminate()
        return public(task)


def _verify(image_bytes, expected_white):
    """Re-open the written file: what is reported is what was saved."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        checked = {'width': image.width, 'height': image.height, 'format': image.format}
        if expected_white:
            checked['border'] = border_whiteness(image)
    return checked


def _export_shot(task, project, shot, source, index, folder, cancelled):
    preset, options = task['preset'], task['options']
    project_id = task['project_id']
    main = shot['id'] == main_shot_id(project)
    png = _project_file(project_id, source.get('png_url'), ('.png',))
    if png is None:
        raise RuntimeError('渲染图片已不存在，请重新出图后导出')
    with Image.open(png) as opened:
        beauty = opened.convert('RGB')
    style = _shot_style(next((item for item in project.get('outputs', []) if item.get('png_url') == source['png_url']), source), shot)
    white_source = style == 'white'
    cut = options['background'] != 'original' and (main or white_source or options['background'] == 'all_white')
    aspect_now = next((name for name in ('1:1', '4:3', '3:4') if abs(beauty.width / beauty.height - _ratio(name)) < .01), None)
    aspect = aspect_now if aspect_now in preset['aspects'] else preset['aspect']
    wants_slot = main and bool(preset.get('white_slot')) and options['background'] != 'original'
    matte = None
    if cut or options['transparent'] or wants_slot or aspect_now != aspect:
        try:
            matte = product_matte(task['id'], project_id, source, cancelled)
        except ExportCancelled:
            raise
        except (RuntimeError, OSError) as exc:
            if cut or options['transparent'] or wants_slot:
                raise
            update(task['id'], warnings=get(KIND, task['id'])['warnings'] + ['%s：%s' % (shot.get('label', shot['id']), exc)])
    reframe = shot.get('kind') != 'detail'
    written = []

    def save(name, data, record):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        record.update(name=name, bytes=len(data), url=file_url(folder / name))
        written.append(record)

    image, info = compose(beauty, matte, aspect, preset['target_px'], 'white' if cut else 'original',
                          shadow=options['shadow'], fill_target=preset['fill_target'], reframe=reframe, white_source=white_source)
    data, quality, image = encode_jpeg(image, preset['max_bytes'], preset['min_px'])
    checked = _verify(data, cut)
    role = 'main' if main else 'detail' if shot.get('kind') == 'detail' else 'angle'
    record = {'shot_id': shot['id'], 'label': shot.get('label', shot['id']), 'role': role, 'kind': 'image', 'quality': quality,
              'width': checked['width'], 'height': checked['height'], 'framing': info.get('framing'), 'fill': info.get('fill'),
              'background': 'white' if cut else 'original', 'shadow': bool(info.get('shadow')),
              'source': {key: source.get(key) for key in ('quality', 'version', 'width', 'height')}, 'issues': []}
    if cut:
        record['pure_white'] = checked['border']['level'] == 'pure'
        if not record['pure_white']:
            record['issues'].append('边缘不是纯白（最暗 %s）' % checked['border']['darkest'])
    if max(checked['width'], checked['height']) < preset['min_px']:
        record['issues'].append('长边 %d px，低于平台最低 %d px；请先渲染 4K' % (max(checked['width'], checked['height']), preset['min_px']))
    if preset['max_bytes'] and len(data) > preset['max_bytes']:
        record['issues'].append('文件 %s，超过平台上限 %s' % (_megabytes(len(data)), _megabytes(preset['max_bytes'])))
    if info.get('warning'):
        record['issues'].append(info['warning'])
    if main and preset.get('fill_min') and info.get('fill') is not None and info['fill'] + .005 < preset['fill_min']:
        record['issues'].append('产品占比 %s，低于平台要求的 %s' % (_percent(info['fill']), _percent(preset['fill_min'])))
    if source.get('version') != task.get('project_version'):
        record['issues'].append('图片来自旧方案 v%s' % source.get('version'))
    save('%02d_%s_%s_%dx%d.jpg' % (index, shot['id'], role, checked['width'], checked['height']), data, record)
    if wants_slot:
        slot = preset['white_slot']
        image, info = compose(beauty, matte, slot['aspect'], slot['px'], 'white', shadow=bool(slot.get('shadow')) and options['shadow'],
                              fill_target=slot['fill_target'], reframe=reframe, white_source=white_source)
        data, quality, image = encode_jpeg(image, slot['max_bytes'], slot['px'])
        checked = _verify(data, True)
        record = {'shot_id': shot['id'], 'label': preset['name'] + slot['label'], 'role': 'white_slot', 'kind': 'image', 'quality': quality,
                  'width': checked['width'], 'height': checked['height'], 'framing': info.get('framing'), 'fill': info.get('fill'),
                  'background': 'white', 'shadow': bool(info.get('shadow')), 'pure_white': checked['border']['level'] == 'pure', 'issues': []}
        if max(checked['width'], checked['height']) < slot['px']:
            record['issues'].append('长边 %d px，低于白底图要求的 %d px；请先渲染 4K' % (max(checked['width'], checked['height']), slot['px']))
        if len(data) > slot['max_bytes']:
            record['issues'].append('文件 %s，超过白底图上限 %s' % (_megabytes(len(data)), _megabytes(slot['max_bytes'])))
        save('white/%02d_%s_white_%dx%d.jpg' % (index, shot['id'], checked['width'], checked['height']), data, record)
    if options['transparent']:
        image, info = compose(beauty, matte, aspect, 4096, 'transparent', shadow=options['shadow'],
                              fill_target=preset['fill_target'], reframe=reframe, white_source=white_source)
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        record = {'shot_id': shot['id'], 'label': shot.get('label', shot['id']) + ' · 透明底', 'role': 'transparent', 'kind': 'image',
                  'width': image.width, 'height': image.height, 'framing': info.get('framing'), 'fill': info.get('fill'),
                  'background': 'transparent', 'shadow': bool(info.get('shadow')), 'issues': []}
        save('transparent/%02d_%s_%dx%d.png' % (index, shot['id'], image.width, image.height), buffer.getvalue(), record)
    return written


def _readme(task, files):
    preset, options = task['preset'], task['options']
    lines = ['物影 Studio · %s 套图' % preset['name'], '',
             '平台要点：' + ' '.join(preset['notes']), '',
             '背景处理：' + {'main_white': '主图纯白底，其余镜头保持渲染原样（白底镜头同样清理为纯白）',
                          'all_white': '全部镜头纯白底', 'original': '保持渲染原样'}[options['background']]
             + ('；保留产品附近的柔和阴影' if options['shadow'] else '；不保留阴影'), '', '文件：']
    for item in files:
        facts = ['%d × %d px' % (item['width'], item['height']), _megabytes(item['bytes'])]
        if item.get('pure_white') is True:
            facts.append('纯白底已校验')
        if item.get('fill'):
            facts.append('产品占比 ' + _percent(item['fill']))
        lines.append('  %s  —  %s  （%s）' % (item['name'], item['label'], '，'.join(facts)))
        lines.extend('      ! ' + issue for issue in item['issues'])
    lines += ['', '纯白底与透明底由本机 Blender 重新渲染的产品蒙版合成：只替换产品以外的背景，产品像素来自原渲染图。',
              '尺寸、文件大小与纯白底结论均来自对已写入文件的重新读取，记录在 manifest.json。',
              presets.catalog()['disclaimer']]
    return '\n'.join(lines) + '\n'


def execute(ident):
    with LOCK:
        try:
            task = get(KIND, ident)
        except KeyError:
            return
        if task['status'] != 'queued':
            return
        RUNNING.add(ident)
    try:
        _execute(ident)
    finally:
        with LOCK:
            RUNNING.discard(ident)


def _execute(ident):
    task = update(ident, status='running', stage='读取渲染图片', started_at=time.time())
    project_id = task['project_id']
    folder = DATA / project_id / 'ecommerce' / 'exports' / ident
    package = folder / 'package'

    def cancelled():
        return get(KIND, ident)['status'] == 'cancelled'

    try:
        project = get('project', project_id)
        shots = {shot['id']: shot for shot in project['plan']['shots']}
        package.mkdir(parents=True, exist_ok=True)
        files = []
        sources = [source for source in task['sources'] if source['shot_id'] in shots]
        for index, source in enumerate(sources, 1):
            if cancelled():
                raise ExportCancelled()
            shot = shots[source['shot_id']]
            update(ident, stage='处理「%s」（%d/%d）' % (shot.get('label', shot['id']), index, len(sources)),
                   progress=round((index - 1) / len(sources) * 100))
            files.extend(_export_shot(task, project, shot, source, index, package, cancelled))
            update(ident, files=files)
        if cancelled():
            raise ExportCancelled()
        update(ident, stage='写入清单并打包', progress=96)
        current = get(KIND, ident)
        manifest = {'application': '物影 Studio', 'platform': task['preset'], 'options': task['options'],
                    'project': {'id': project_id, 'name': project.get('name'), 'plan_version': task.get('project_version')},
                    'created_at': time.time(), 'files': [{key: value for key, value in item.items() if key != 'url'} for item in files],
                    'warnings': current.get('warnings', []), 'disclaimer': presets.catalog()['disclaimer']}
        atomic_json(package / 'manifest.json', manifest)
        (package / 'README.txt').write_text(_readme(task, files), encoding='utf-8')
        archive = folder / ('%s_%s.zip' % (task['slug'], task['platform']))
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(package.rglob('*')):
                if path.is_file():
                    bundle.write(path, path.relative_to(package).as_posix())
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise RuntimeError('套图 ZIP 校验失败')
        issues = sum(1 for item in files if item['issues'])
        update(ident, status='completed', progress=100, download_url=file_url(archive), finished_at=time.time(),
               stage='已导出 %d 个文件' % len(files) + ('，其中 %d 个需要留意' % issues if issues else '，全部通过校验'))
    except ExportCancelled:
        shutil.rmtree(folder, ignore_errors=True)
    except Exception as exc:
        update(ident, status='failed', stage='导出未完成', error=str(exc)[:500], finished_at=time.time())


def worker():
    while True:
        ident = QUEUE.get()
        try:
            execute(ident)
        except Exception as exc:
            try:
                update(ident, status='failed', stage='导出异常', error=str(exc)[:500], finished_at=time.time())
            except KeyError:
                pass
        finally:
            QUEUE.task_done()


def shutdown():
    for process in list(PROCESSES.values()):
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass


atexit.register(shutdown)


def start():
    for task in all_records(KIND):
        if task.get('status') in ('queued', 'running'):
            task.update(status='interrupted', stage='应用重启，请重新导出', error='上次导出被中断；渲染图片不受影响。', finished_at=time.time())
            put(KIND, task)
    threading.Thread(target=worker, daemon=True, name='ecommerce-export-worker').start()

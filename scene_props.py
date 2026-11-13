"""Ready-made models and surfaces for usage scenes, from Poly Haven.

Poly Haven publishes scanned and hand-made assets under CC0: no attribution
or permission is required, including for commercial product images. Every
asset used is still recorded with its name and page, so a scene can always say
where each thing in it came from.

Only two hosts are contacted: the catalogue API and the file server. Files are
taken from the API's own listing, checked against the size and MD5 it states,
and written under a project folder with names this module chose. A model's
glTF may only refer to the files that were downloaded with it.
"""
import hashlib
import json
import math
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from studio_store import DATA, atomic_json

PROVIDER = 'Poly Haven'
LICENSE = 'CC0 1.0'
LICENSE_URL = 'https://polyhaven.com/license'
API = 'https://api.polyhaven.com'
PAGE = 'https://polyhaven.com/a/'
HEADERS = {'User-Agent': 'Wuying-Studio/1.0 (local product scene props)', 'Accept': 'application/json,image/*,*/*'}
API_HOSTS = {'api.polyhaven.com'}
FILE_HOSTS = {'dl.polyhaven.org', 'dl.polyhaven.com'}
FILE_PREFIXES = ('/file/ph-assets/Models/', '/file/ph-assets/Textures/')
MAX_JSON = 16 * 1024 * 1024
MAX_FILE = 24 * 1024 * 1024
MAX_MODEL = 64 * 1024 * 1024
MAX_TRIANGLES = 400000
CATALOGUE_TTL = 6 * 3600
KINDS = {'models': 2, 'textures': 1}
_MEMORY = {}
_WORD = re.compile(r'[a-z0-9]+')
_STOP = {'a', 'an', 'the', 'of', 'and', 'with', 'for', 'old', 'small', 'large', 'set', 'model', 'prop', '01', '02', '03', '04', '05'}


class PropError(ValueError):
    """A prop could not be found or fetched. The message is safe to show."""


def _check(url, files=False):
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in (FILE_HOSTS if files else API_HOSTS) or parts.port not in (None, 443)
            or parts.username or parts.password or parts.fragment):
        raise PropError('道具来源地址不在已核验的 Poly Haven 服务中')
    if files and not parts.path.startswith(FILE_PREFIXES):
        raise PropError('下载地址不是 Poly Haven 的模型或贴图文件')
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PropError('道具服务返回重定向，下载已停止')


def _fetch(url, limit, files=False, cancelled=None):
    _check(url, files)
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
            _check(response.geturl(), files)
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > limit:
                raise PropError('道具文件超过大小限制')
            chunks = []; size = 0
            while True:
                if cancelled and cancelled():
                    raise PropError('已取消')
                if time.monotonic() - started > 180:
                    raise PropError('道具下载超时')
                chunk = response.read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk); size += len(chunk)
                if size > limit:
                    raise PropError('道具文件超过大小限制')
            return b''.join(chunks)
    except PropError:
        raise
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError):
        raise PropError('无法连接 Poly Haven，请检查网络后重试') from None


def _cache_folder():
    folder = DATA / 'props_cache'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def catalogue(kind='models', refresh=False):
    """The whole catalogue of one kind, from memory, disk or the network (in that order)."""
    if kind not in KINDS:
        raise PropError('未知的道具类型')
    cached = _MEMORY.get(kind)
    if cached and not refresh and time.time() - cached[0] < CATALOGUE_TTL:
        return cached[1]
    path = _cache_folder() / ('catalogue-%s.json' % kind)
    stored = None
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            stored = None
    if stored and not refresh and time.time() - path.stat().st_mtime < CATALOGUE_TTL:
        _MEMORY[kind] = (time.time(), stored); return stored
    try:
        value = json.loads(_fetch(API + '/assets?t=' + kind, MAX_JSON).decode('utf-8'))
        if not isinstance(value, dict):
            raise PropError('道具目录返回格式无效')
        assets = {key: item for key, item in value.items()
                  if re.fullmatch(r'[A-Za-z0-9_]{1,100}', key) and isinstance(item, dict) and item.get('type') == KINDS[kind]}
        if not assets:
            raise PropError('Poly Haven 当前没有返回可用的目录')
        atomic_json(path, assets)
    except (PropError, ValueError, UnicodeError):
        if stored:              # an older copy is better than no scene at all
            _MEMORY[kind] = (time.time(), stored); return stored
        raise
    _MEMORY[kind] = (time.time(), assets)
    return assets


def _tokens(text):
    return [word for word in _WORD.findall(str(text).lower().replace('-', ' ')) if word not in _STOP]


def _stem(word):
    for suffix in ('es', 's'):
        if len(word) > 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def dimensions(asset):
    value = asset.get('dimensions')
    if isinstance(value, list) and 2 <= len(value) <= 3 and all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in value):
        return [float(v) for v in value]
    return None


def describe(asset_id, asset):
    return {'asset_id': asset_id, 'name': str(asset.get('name', asset_id))[:200],
            'categories': [str(value) for value in asset.get('categories', []) if not str(value).startswith('collection:')][:8],
            'tags': [str(value) for value in asset.get('tags', [])][:12], 'dimensions_mm': dimensions(asset),
            'triangles': asset.get('polycount') if type(asset.get('polycount')) is int else None,
            'provider': PROVIDER, 'license': LICENSE, 'source_url': PAGE + asset_id}


def search(concept, kind='models', limit=6, require=(), exclude=(), largest_mm=None, smallest_mm=None, assets=None):
    """Assets that match a short description, best first. Pure ranking, no network when ``assets`` is given.

    A match on the asset's own name counts most, then its tags, then its
    categories. ``require`` / ``exclude`` are category names; the size limits
    apply to the asset's largest dimension.
    """
    assets = catalogue(kind) if assets is None else assets
    wanted = [_stem(word) for word in _tokens(concept)]
    if not wanted:
        return []
    phrase = ' '.join(_tokens(concept))
    results = []
    for asset_id, asset in assets.items():
        categories = {str(value).lower() for value in asset.get('categories', [])}
        if any(value not in categories for value in require) or any(value in categories for value in exclude):
            continue
        size = dimensions(asset)
        if kind == 'models':
            if type(asset.get('polycount')) is int and asset['polycount'] > MAX_TRIANGLES:
                continue
            if size and ((largest_mm and max(size) > largest_mm) or (smallest_mm and max(size) < smallest_mm)):
                continue
        name = [_stem(word) for word in _tokens(asset.get('name', '')) + _tokens(asset_id.replace('_', ' '))]
        tags = [_stem(word) for tag in asset.get('tags', []) for word in _tokens(tag)]
        cats = [_stem(word) for value in categories for word in _tokens(value)]
        score = 0.0; matched = 0
        for word in wanted:
            best = 3.0 if word in name else 1.6 if word in tags else .8 if word in cats else 0
            score += best; matched += bool(best)
        if not matched:
            continue
        if matched == len(wanted): score += 2
        if phrase and phrase in ' '.join(_tokens(asset.get('name', ''))): score += 1.5
        score -= .15 * max(0, len(set(name)) - len(wanted))          # the plain thing before the elaborate one
        results.append((score / len(wanted), asset_id))
    results.sort(key=lambda item: (-item[0], item[1]))
    return [{**describe(asset_id, assets[asset_id]), 'score': round(score, 3)} for score, asset_id in results[:limit]]


def _safe_relative(name, suffixes):
    path = Path(name)
    if (path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[A-Za-z0-9_.\-/ ]{1,200}', name)
            or path.suffix.lower() not in suffixes):
        raise PropError('道具文件名不安全')
    return path


def _download(item, target, cancelled=None):
    if (not isinstance(item, dict) or not isinstance(item.get('url'), str) or type(item.get('size')) is not int
            or not 0 < item['size'] <= MAX_FILE or not re.fullmatch(r'[0-9a-f]{32}', str(item.get('md5', '')))):
        raise PropError('道具文件清单无效')
    if target.is_file() and target.stat().st_size == item['size'] and hashlib.md5(target.read_bytes()).hexdigest() == item['md5']:
        return item['size']
    data = _fetch(item['url'], item['size'], files=True, cancelled=cancelled)
    if len(data) != item['size'] or hashlib.md5(data).hexdigest() != item['md5']:
        raise PropError('道具文件校验失败，已停止使用')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.part')
    temporary.write_bytes(data); temporary.replace(target)
    return item['size']


def _listing(asset_id):
    if not re.fullmatch(r'[A-Za-z0-9_]{1,100}', str(asset_id)):
        raise PropError('道具标识无效')
    value = json.loads(_fetch(API + '/files/' + asset_id, MAX_JSON).decode('utf-8'))
    if not isinstance(value, dict):
        raise PropError('道具文件清单无效')
    return value


def _references(path, allowed):
    """A downloaded glTF may point only at the files that came with it."""
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        raise PropError('道具模型文件无法读取') from None
    for group in ('buffers', 'images'):
        for entry in document.get(group, []) if isinstance(document.get(group, []), list) else []:
            uri = entry.get('uri') if isinstance(entry, dict) else None
            if uri is None or (isinstance(uri, str) and uri.startswith('data:')):
                continue
            if not isinstance(uri, str) or urllib.parse.unquote(uri) not in allowed:
                raise PropError('道具模型引用了未随附的文件')


def fetch_model(asset_id, folder, resolution='1k', cancelled=None):
    """Download one model as glTF with its textures into ``folder/<asset_id>/``."""
    assets = catalogue('models')
    if asset_id not in assets:
        raise PropError('目录中没有这个模型')
    entry = _listing(asset_id).get('gltf', {}).get(resolution, {}).get('gltf')
    if not isinstance(entry, dict) or not isinstance(entry.get('include', {}), dict):
        raise PropError('这个模型没有可用的 glTF 文件')
    included = {}
    for name, item in entry.get('include', {}).items():
        included[str(_safe_relative(name, {'.bin', '.jpg', '.jpeg', '.png'}).as_posix())] = item
    total = entry.get('size', 0) + sum(item.get('size', 0) for item in included.values() if isinstance(item, dict))
    if type(total) is not int or total > MAX_MODEL:
        raise PropError('这个模型超过大小限制')
    target = Path(folder) / asset_id
    main = target / (asset_id + '.gltf')
    size = _download(entry, main, cancelled)
    for name, item in included.items():
        size += _download(item, target / name, cancelled)
    _references(main, set(included))
    record = {**describe(asset_id, assets[asset_id]), 'file': str(main), 'bytes': size, 'resolution': resolution,
              'license_url': LICENSE_URL, 'downloaded_at': time.time()}
    atomic_json(target / 'source.json', record)
    return record


def fetch_surface(asset_id, folder, resolution='2k', cancelled=None):
    """Download the colour, roughness and normal maps of one surface texture."""
    assets = catalogue('textures')
    if asset_id not in assets:
        raise PropError('目录中没有这个表面材质')
    listing = _listing(asset_id)
    target = Path(folder) / asset_id
    textures = {}; size = 0
    for key, group in (('base_color', 'Diffuse'), ('roughness', 'Rough'), ('normal', 'nor_gl')):
        item = listing.get(group, {}).get(resolution, {}).get('jpg')
        if not isinstance(item, dict):
            if key == 'base_color':
                raise PropError('这个表面材质没有可用的颜色贴图')
            continue
        path = target / ('%s_%s_%s.jpg' % (asset_id, key, resolution))
        size += _download(item, path, cancelled)
        textures[key] = str(path)
    tile = dimensions(assets[asset_id])
    record = {'asset_id': asset_id, 'name': str(assets[asset_id].get('name', asset_id))[:200], 'textures': textures,
              'tile_m': round(max(tile) / 1000, 4) if tile else 2.0, 'source_url': PAGE + asset_id, 'license': LICENSE}
    atomic_json(target / 'source.json', {**record, 'bytes': size, 'resolution': resolution, 'license_url': LICENSE_URL,
                                         'downloaded_at': time.time()})
    return record


def adopt(record, project_folder):
    """Copy a cached download into a project so the project is self-contained."""
    source = Path(record['file']).parent if 'file' in record else Path(next(iter(record['textures'].values()))).parent
    target = Path(project_folder) / source.name
    if source.resolve() != target.resolve():
        shutil.copytree(source, target, dirs_exist_ok=True)
    result = dict(record)
    if 'file' in record:
        result['file'] = str(target / Path(record['file']).name)
    if 'textures' in record:
        result['textures'] = {key: str(target / Path(value).name) for key, value in record['textures'].items()}
    return result

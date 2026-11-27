"""Trusted catalog of the photographic setups shared by the viewport and Cycles."""
import json
from functools import lru_cache
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent / 'static' / 'studio-backdrops'


def catalog_path():
    installed = DIRECTORY / 'catalog.json'
    return installed if installed.is_file() else DIRECTORY / 'builtin' / 'catalog.json'


@lru_cache(maxsize=1)
def _read(path, modified):
    with path.open(encoding='utf-8') as stream:
        document = json.load(stream)
    return document['default'], {p['id']: p for p in document['presets']}


def _installed():
    path = catalog_path()
    return _read(path, path.stat().st_mtime_ns)


def catalog():
    return _installed()[1]


def default_backdrop():
    return require_id(_installed()[0])


def require_id(value):
    if not isinstance(value, str) or value not in catalog():
        raise ValueError('请选择物影背景目录中已有的背景和布光。')
    return value

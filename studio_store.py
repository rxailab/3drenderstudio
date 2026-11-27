"""Small transactional store. Every render keeps an immutable plan snapshot."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from asset_provenance import enrich_project

ROOT = Path(__file__).resolve().parent

def data_root():
    """The one place the data folder is chosen: STUDIO_DATA when set (the tests point it at a temporary folder before any
    module is imported), else <repo>/data. Modules that do 'from studio_store import DATA' read it once, at import."""
    chosen = os.environ.get('STUDIO_DATA', '').strip()
    return Path(chosen).expanduser().resolve() if chosen else ROOT / 'data'

DATA = data_root()
LOCK = threading.RLock()
SCHEMA = 'CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))'
_READY = set()          # database files whose table this process has already made sure of

def connection():
    # The folder and the table are made on the first connection, not at import, so importing a module never writes
    # into a data folder. DATA is read here, at call time: the tests swap it for their own folders.
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / 'studio.sqlite'
    fresh = str(path) not in _READY or not path.exists()
    c = sqlite3.connect(str(path), timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    # With WAL this stays crash-safe for the database; a power cut can lose only the last moments of progress text.
    c.execute('PRAGMA synchronous=NORMAL')
    if fresh:
        with c: c.execute(SCHEMA)
        _READY.add(str(path))
    return c

def put(kind, obj):
    if kind=='project' and '_material_redo' in obj and obj['_material_redo']['version']!=obj.get('version'):
        obj.pop('_material_redo')
    obj['updated_at'] = time.time()
    with LOCK, connection() as c:
        c.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind,obj['id'],json.dumps(obj,ensure_ascii=False)))
    return obj

def get(kind, ident):
    with connection() as c:
        r = c.execute('SELECT payload FROM records WHERE kind=? AND id=?',(kind,ident)).fetchone()
    if not r:
        raise KeyError(ident)
    obj=json.loads(r[0])
    return enrich_project(obj,DATA) if kind=='project' else obj

def all_records(kind):
    with connection() as c:
        rows = c.execute('SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC',(kind,)).fetchall()
    return [json.loads(r[0]) for r in rows]

def prune(kind, keep, older_than=None):
    """Drop the oldest records of ``kind`` beyond the newest ``keep``, and any written before ``older_than`` (a time).
    For caches only: their records are rebuilt when needed. Returns how many were dropped."""
    with LOCK, connection() as c:
        dropped = c.execute('DELETE FROM records WHERE kind=? AND rowid NOT IN '
                            '(SELECT rowid FROM records WHERE kind=? ORDER BY rowid DESC LIMIT ?)', (kind, kind, int(keep))).rowcount
        if older_than is not None:
            dropped += c.execute("DELETE FROM records WHERE kind=? AND json_extract(payload,'$.updated_at')<?", (kind, float(older_than))).rowcount
    return dropped

def atomic_json(path, obj):
    path = Path(path)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

"""Small transactional store. Every render keeps an immutable plan snapshot."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from asset_provenance import enrich_project

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
LOCK = threading.RLock()

def connection():
    c = sqlite3.connect(str(DATA / 'studio.sqlite'), timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    return c

with connection() as c:
    c.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')

def put(kind, obj):
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

def atomic_json(path, obj):
    path = Path(path)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

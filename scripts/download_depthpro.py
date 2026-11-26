"""Resumable multi-threaded downloader for apple/DepthPro-hf weights into data/models/hf.

Uses 8 concurrent HTTP Range streams from ModelScope (with Hugging Face fallback),
verifies SHA256, and installs the blob + snapshot link expected by HuggingFace/transformers
offline loading (scene_backdrop.can_measure()).
"""
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
HF_HUB = ROOT / 'data' / 'models' / 'hf' / 'hub' / 'models--apple--DepthPro-hf'
REVISION = 'de816c8ce7168afcb231f96d501d72b869d0beda'
BLOB_SHA256 = '9c6811e3165485b9a94a204329860cb333a79877e757eb795a179a4ea34bbcf7'
TOTAL_BYTES = 1904259516
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB chunks
WORKERS = 8

URLS = (
    'https://www.modelscope.cn/models/apple/DepthPro-hf/resolve/master/model.safetensors',
    'https://huggingface.co/apple/DepthPro-hf/resolve/main/model.safetensors',
)


def main():
    blobs = HF_HUB / 'blobs'
    snap = HF_HUB / 'snapshots' / REVISION
    blobs.mkdir(parents=True, exist_ok=True)
    snap.mkdir(parents=True, exist_ok=True)
    final_blob = blobs / BLOB_SHA256
    snap_link = snap / 'model.safetensors'

    if final_blob.is_file() and final_blob.stat().st_size == TOTAL_BYTES:
        _link_snapshot(final_blob, snap_link)
        print('Already downloaded and ready:', snap_link, flush=True)
        return

    # Query exact content-length from server
    url = URLS[0]
    total = TOTAL_BYTES
    for candidate in URLS:
        try:
            r = requests.head(candidate, allow_redirects=True, timeout=20)
            cl = int(r.headers.get('content-length') or 0)
            if r.status_code in (200, 206) and cl > 100_000_000:
                url = candidate
                total = cl
                break
        except Exception:
            continue

    part_path = blobs / (BLOB_SHA256 + '.part')
    state_path = blobs / (BLOB_SHA256 + '.chunks.json')
    num_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE

    done = set()
    if state_path.is_file() and part_path.is_file() and part_path.stat().st_size == total:
        try:
            done = set(json.loads(state_path.read_text(encoding='utf-8')))
        except Exception:
            done = set()
    else:
        with part_path.open('wb') as f:
            f.truncate(total)
        state_path.write_text('[]', encoding='utf-8')

    pending = [i for i in range(num_chunks) if i not in done]
    print(f'Downloading {total / 1048576:.1f} MB ({len(done)}/{num_chunks} chunks done) from {url}', flush=True)

    lock = threading.Lock()
    started = time.time()
    session_bytes = [0]
    last_log = [time.time()]

    def fetch_chunk(idx):
        start = idx * CHUNK_SIZE
        end = min(total - 1, start + CHUNK_SIZE - 1)
        expected = end - start + 1
        for attempt in range(8):
            try:
                cur_url = URLS[0] if attempt < 6 else URLS[1]
                r = requests.get(cur_url, headers={'Range': f'bytes={start}-{end}'}, timeout=60)
                if r.status_code in (200, 206) and len(r.content) == expected:
                    with lock:
                        with part_path.open('r+b') as f:
                            f.seek(start)
                            f.write(r.content)
                        done.add(idx)
                        session_bytes[0] += expected
                        if len(done) % 4 == 0 or len(done) == num_chunks:
                            state_path.write_text(json.dumps(sorted(done)), encoding='utf-8')
                        now = time.time()
                        if now - last_log[0] >= 10.0 or len(done) == num_chunks:
                            dt = max(now - started, 0.1)
                            mb_s = session_bytes[0] / 1048576 / dt
                            pct = len(done) / num_chunks * 100.0
                            rem_mb = (num_chunks - len(done)) * CHUNK_SIZE / 1048576
                            eta_m = (rem_mb / mb_s / 60.0) if mb_s > 0.001 else 999
                            print(f'Progress: {pct:5.1f}% ({len(done)}/{num_chunks})  {mb_s:.2f} MB/s  ETA ~{eta_m:.1f} min', flush=True)
                            last_log[0] = now
                    return True
            except Exception as exc:
                if attempt == 7:
                    raise RuntimeError(f'Chunk {idx} failed: {exc}') from exc
                time.sleep(min(10, 2 ** attempt))
        raise RuntimeError(f'Chunk {idx} exhausted retries')

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        futs = [pool.submit(fetch_chunk, i) for i in pending]
        for fut in concurrent.futures.as_completed(futs):
            fut.result()

    print('Verifying SHA256...', flush=True)
    h = hashlib.sha256()
    with part_path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    digest = h.hexdigest()
    if digest != BLOB_SHA256:
        raise RuntimeError(f'SHA256 mismatch: got {digest}, expected {BLOB_SHA256}')

    part_path.replace(final_blob)
    state_path.unlink(missing_ok=True)
    for leftover in blobs.glob('*.incomplete'):
        leftover.unlink(missing_ok=True)
    _link_snapshot(final_blob, snap_link)
    print('SUCCESS! DepthPro-hf weights verified and installed:', snap_link, flush=True)


def _link_snapshot(blob_path, snap_link):
    if snap_link.exists() or snap_link.is_symlink():
        snap_link.unlink()
    try:
        rel = os.path.relpath(blob_path, snap_link.parent)
        snap_link.symlink_to(rel)
    except OSError:
        shutil.copy2(blob_path, snap_link)


if __name__ == '__main__':
    main()

"""Shared pytest setup.

The data folder comes from STUDIO_DATA (studio_store.data_root). It is pointed at a fresh temporary folder here, before any
project module is imported, because modules copy studio_store.DATA at import time: a test that forgets to patch DATA then
writes into the temporary folder, never into the real data/ (tests once left data/p/articulation/k behind).

Tests that drive the real ai_client._post with a fake transport would otherwise append their made-up token counts to
data/ai_usage.jsonl, the log that is there to measure what real builds cost (v40). The whole run writes it to a
temporary folder instead; a test that checks the log points ai_client.USAGE_DIR at its own folder."""
import os
import sys
import tempfile
from pathlib import Path

_DATA = tempfile.TemporaryDirectory(prefix='studio_data_')
os.environ['STUDIO_DATA'] = _DATA.name

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import ai_client

_FOLDER = tempfile.TemporaryDirectory(prefix='ai_usage_')
ai_client.USAGE_DIR = _FOLDER.name

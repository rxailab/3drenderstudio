"""Shared pytest setup.

Tests that drive the real ai_client._post with a fake transport would otherwise append their made-up token counts to
data/ai_usage.jsonl, the log that is there to measure what real builds cost (v40). The whole run writes it to a
temporary folder instead; a test that checks the log points ai_client.USAGE_DIR at its own folder."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import ai_client

_FOLDER = tempfile.TemporaryDirectory(prefix='ai_usage_')
ai_client.USAGE_DIR = _FOLDER.name

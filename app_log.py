"""The application's log: <data>/logs/studio.log (rotated by size, a few backups kept) plus stderr.

app.py calls configure() once at start-up. Modules take their logger with get(name) and need nothing else: until configure
runs (the tests, scripts) the records go nowhere, so an import never opens a log file.

Secrets never reach the log: every line, tracebacks included, is scrubbed of the values of secret-looking environment
variables (the .env is loaded into os.environ by app.py), of Authorization / Bearer headers and of api_key=... pairs."""
import logging
import logging.handlers
import os
import re
import sys
import threading
from pathlib import Path

NAME = 'studio'
LOG_BYTES = 5 * 1024 * 1024
BACKUPS = 3
FORMAT = '%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'
_SECRET_NAMES = re.compile(r'KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL', re.I)
_PATTERNS = (re.compile(r'(?i)(authorization["\']?\s*[:=]\s*["\']?)(?:bearer\s+|basic\s+)?[^\s"\',}]+'),
             re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+'),
             re.compile(r'(?i)((?:api[_-]?key|x-api-key|access[_-]?token|secret)["\']?\s*[:=]\s*["\']?)[^\s"\',}&]+'),
             re.compile(r'()\bsk-[A-Za-z0-9_-]{12,}'))
_LOCK = threading.Lock()
_CONFIGURED = []

# Library convention: with no handler configured the 'studio' records are dropped instead of printed by logging's last resort.
logging.getLogger(NAME).addHandler(logging.NullHandler())


def get(name):
    return logging.getLogger(NAME + '.' + name)


def scrub(text):
    """``text`` with secrets replaced by ***."""
    for key, value in os.environ.items():
        # Short values ('1', 'true') would blank out ordinary words; real keys are long.
        if value and len(value) >= 8 and _SECRET_NAMES.search(key) and value in text:
            text = text.replace(value, '***')
    for pattern in _PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + '***', text)
    return text


class ScrubbingFormatter(logging.Formatter):
    def format(self, record):
        return scrub(super().format(record))


def configure(data=None):
    """Attach the rotating file under <data>/logs and stderr to the root logger, once. Returns the log file's path."""
    with _LOCK:
        if _CONFIGURED:
            return _CONFIGURED[0]
        if data is None:
            import studio_store
            data = studio_store.DATA
        folder = Path(data) / 'logs'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'studio.log'
        formatter = ScrubbingFormatter(FORMAT)
        handlers = [logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=BACKUPS, encoding='utf-8', delay=True),
                    logging.StreamHandler(sys.stderr)]
        # werkzeug's line per request (the UI polls every second) stays on the console; the file keeps what went wrong.
        handlers[0].addFilter(lambda record: not (record.name.startswith('werkzeug') and record.levelno < logging.WARNING))
        root = logging.getLogger()
        for handler in handlers:
            handler.setFormatter(formatter); root.addHandler(handler)
        root.setLevel(logging.INFO)
        _CONFIGURED.append(path)
        return path

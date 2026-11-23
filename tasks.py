"""Background task threads.

Each module keeps its own task record (listing_task, scene_task, orient_task, material_task, job...) and its own _run that
records its failures in its own words. This only guarantees what none of them could: an exception that escapes _run
(a bug, or a failure inside its own except) is logged with its traceback, and a task it left 'queued' or 'running' is moved
to 'failed' with a short message instead of showing as busy until the next restart."""
import threading
import time

import app_log
import studio_store as store

log = app_log.get('tasks')
STUCK = ('queued', 'running')
MESSAGE = '任务意外中断，请重试。'


def settle(kind, task_id, message=MESSAGE, fields=None, stamp=None, after=None):
    """Move task ``task_id`` of ``kind`` to 'failed' with ``message`` (and ``fields``, and the time in field ``stamp``) when it
    is still queued or running; ``after(task)`` is then called with the failed record. Returns the record, or None."""
    with store.LOCK:
        try: task = store.get(kind, task_id)
        except KeyError: return None
        if task.get('status') not in STUCK: return None
        task.update(status='failed', error=message, **(fields or {}))
        if stamp: task[stamp] = time.time()
        store.put(kind, task)
    if after: after(task)
    return task


def run(task_id, target, kind=None, message=MESSAGE, fields=None, stamp=None, after=None, wrap=None):
    """Call ``target(task_id)`` here and now, under the context manager ``wrap()`` when given. An exception escaping it is
    logged and, when ``kind`` is given, the task is settled (see settle). Never raises."""
    escaped = False
    try:
        if wrap is None:
            return target(task_id)
        with wrap():
            return target(task_id)
    except Exception:
        escaped = True
        log.exception('%s %s stopped on an unexpected error', kind or getattr(target, '__qualname__', 'task'), task_id)
    finally:
        if escaped and kind:
            try: settle(kind, task_id, message, fields, stamp, after)
            except Exception: log.exception('%s %s could not be marked failed', kind, task_id)


def thread(task_id, target, name, **options):
    """A daemon thread, not yet started, that runs ``run(task_id, target, **options)``. ``args[0]`` is the task id (tests read it
    from a patched threading.Thread to run the task themselves)."""
    return threading.Thread(target=run, args=(task_id, target), kwargs=options, name=name, daemon=True)


def start(task_id, target, name, **options):
    worker = thread(task_id, target, name, **options); worker.start()
    return worker


def _forever(loop, name):
    try:
        loop()
    except BaseException:
        log.exception('worker %s stopped', name)
        raise


def daemon(loop, name):
    """Start a long-lived worker loop (the render queue, the components queue) on a daemon thread that logs why it died."""
    worker = threading.Thread(target=_forever, args=(loop, name), name=name, daemon=True); worker.start()
    return worker

"""One gate for every Blender process the application starts.

Renders, inspections, exports and previews used to start Blender each on their own, so a listing build, a 4K render and a
cut-out could all run Cycles on the one GPU at once (out of memory, or three jobs each three times slower), and only the
render queue's children were stopped when the server exited. Every launch now goes through here:

- Two lanes. HEAVY is a full Cycles render on the GPU (shot renders, hero renders, cut-outs, product reference views,
  backdrop compositing): one at a time, so each gets the whole card. LIGHT is the short work a person is usually waiting
  on in the page (scene analysis, thumbnails, picks, part and light inspection, layer probes, orientation candidates,
  scene layout, mattes, viewport and scene exports): it never waits behind a twenty-minute render, and two may run
  beside each other and beside the render because they are small (few samples, small pictures, mostly CPU).
  BLENDER_HEAVY_SLOTS / BLENDER_LIGHT_SLOTS change the widths (a second GPU, a weaker machine).
- One registry of live children, terminated at exit (atexit) and by id (terminate()), whatever module started them.
- A lane is held from launch until the process has really exited, so a cancelled render's GPU memory is free before
  the next one starts. A caller's own timeout starts when its process starts, not while it waits in the lane.

Callers keep their own command lines, timeouts, progress parsing and their module's PROCESSES dict (others read those).
"""
import atexit
import os
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager

from studio_store import ROOT

HEAVY = 'heavy'
LIGHT = 'light'


def _width(name, default):
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


SLOTS = {HEAVY: threading.BoundedSemaphore(_width('BLENDER_HEAVY_SLOTS', 1)),
         LIGHT: threading.BoundedSemaphore(_width('BLENDER_LIGHT_SLOTS', 2))}
WAIT_STEP = .25                  # seconds between cancellation checks while waiting for a lane
CHILDREN = {}                    # registry key -> (caller's id or None, Popen): every live child started here
_REGISTRY = threading.Lock()     # not studio_store.LOCK: callers register while holding that one


def command(executable, script, request, threads=None, extra=()):
    """The usual background command line: no user preferences or scene scripts, a failing script fails the process."""
    return ([executable, '-b', '--factory-startup', '--disable-autoexec'] + (['-t', str(threads)] if threads is not None else [])
            + list(extra) + ['--python-exit-code', '1', '--python', str(script), '--', str(request)])


@contextmanager
def slot(lane, cancelled=None, on_wait=None, cancel_error=InterruptedError):
    """Hold one place in the lane (None: no gate). Waiting is cancellable: cancelled() true raises cancel_error.
    on_wait() is called once when the lane is busy, so a job can say it is waiting rather than look stuck."""
    if lane is None:
        yield
        return
    gate = SLOTS[lane]
    if not gate.acquire(blocking=False):
        if on_wait:
            on_wait()
        while not gate.acquire(timeout=WAIT_STEP):
            if cancelled and cancelled():
                raise cancel_error()
    try:
        yield
    finally:
        gate.release()


def register(process, ident=None):
    key = uuid.uuid4().hex
    with _REGISTRY:
        CHILDREN[key] = (ident, process)
    return key


def unregister(key):
    with _REGISTRY:
        CHILDREN.pop(key, None)


def running(ident=None):
    """Live registered children, all of them or those started for one id."""
    with _REGISTRY:
        rows = list(CHILDREN.values())
    return [process for owner, process in rows if (ident is None or owner == ident) and process.poll() is None]


def _stop(process, grace=10):
    """Terminate, then kill what does not exit within the grace period. Returns once the process is gone."""
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def terminate(ident):
    """Ask every child started for this id to stop (cancellation). Returns how many were running."""
    found = running(ident)
    for process in found:
        try:
            process.terminate()
        except OSError:
            pass
    return len(found)


def shutdown(grace=5):
    """At exit: no Blender is left rendering for a server that is gone."""
    found = running()
    for process in found:
        try:
            process.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + grace
    for process in found:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass


atexit.register(shutdown)


def _options(kwargs):
    kwargs.setdefault('cwd', str(ROOT))
    kwargs.setdefault('creationflags', getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return kwargs


@contextmanager
def launched(command, ident=None, lane=HEAVY, cancelled=None, on_wait=None, cancel_error=InterruptedError, **popen):
    """Start the command once its lane is free and yield the Popen (stdout may be a pipe to stream from).
    On leaving, a process still running is stopped and waited for, then the lane is released."""
    with slot(lane, cancelled, on_wait, cancel_error):
        process = subprocess.Popen(command, **_options(popen))
        key = register(process, ident)
        try:
            yield process
        finally:
            try:
                _stop(process)
            finally:
                unregister(key)


def run(command, timeout=None, ident=None, lane=LIGHT, cancelled=None, **popen):
    """subprocess.run through the gate: a CompletedProcess, or TimeoutExpired once the child is killed.
    The timeout counts from the launch, as it did when each caller ran Blender itself."""
    with launched(command, ident=ident, lane=lane, cancelled=cancelled, **popen) as process:
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)

"""Per-request, thread-local progress notifications for instruction execution."""
import threading

_local = threading.local()

def bind(callback):
    _local.callback = callback

def clear():
    if hasattr(_local, 'callback'):
        del _local.callback

def report(agent, stage):
    callback = getattr(_local, 'callback', None)
    if callback:
        callback(agent, stage)

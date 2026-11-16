"""Pass Cycles' sample count on while a render runs.

The app shows how far a render is from lines such as "Sample 12/128" in
Blender's output. Up to Blender 4 the program printed them itself in
background mode; Blender 5 moved that output to its logging system and no
longer shows it by default, so the percentage stayed at zero. The statistics
handler still receives every update on every version, so the scripts print
the count themselves.
"""
import re
import time

import bpy

MARK = 'studio_render_progress'
INTERVAL = .4          # seconds between two lines: enough for a moving percentage, not a flood


def report():
    """Print "PROGRESS: Sample n/m" as the render advances. Safe to call before every render."""
    state = {'at': 0.0, 'last': None}

    def on_stats(message, *_):
        found = re.search(r'Sample\s+(\d+)\s*/\s*(\d+)', str(message))
        if not found:
            return
        pair = (found[1], found[2]); now = time.time()
        if pair != state['last'] and (now - state['at'] >= INTERVAL or found[1] == found[2]):
            state.update(at=now, last=pair)
            print('PROGRESS: Sample %s/%s' % pair, flush=True)

    on_stats.__name__ = MARK
    handlers = bpy.app.handlers.render_stats
    for old in [handler for handler in handlers if getattr(handler, '__name__', '') == MARK]:
        handlers.remove(old)
    handlers.append(on_stats)
    return on_stats

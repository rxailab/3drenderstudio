"""Pass Cycles' sample count on while a render runs.

The app shows how far a render is from lines such as "Sample 12/128" in
Blender's output. Up to Blender 4 the program printed them itself in
background mode; Blender 5 moved that output to its logging system and no
longer shows it by default. The statistics handler still receives every
update on every version, so the scripts print the count themselves.

A large picture is rendered in tiles, and Blender then counts the samples of
each tile from zero again ("Rendered 1/4 Tiles, Sample 12/128"). What is
printed here is the count over the whole picture, so the percentage rises
once from 0 to 100 however many tiles there are.
"""
import re
import time

INTERVAL = .4          # seconds between two lines: enough for a moving percentage, not a flood
MARK = 'studio_render_progress'


def new_state():
    return {'tile': None, 'restarted': False, 'best': 0, 'at': 0.0, 'last': None}


def overall(message, state):
    """(samples done, samples in all) over the whole picture for one statistics message, or None.

    ``state`` remembers the tile being rendered. When a tile finishes, Blender
    raises the tile count while still showing the finished tile's last sample;
    that sample is not counted again for the next tile. The result never goes
    backwards.
    """
    text = str(message)
    sample = re.search(r'Sample\s+(\d+)\s*/\s*(\d+)', text)
    if not sample:
        return None
    done, per_tile = int(sample[1]), int(sample[2])
    if per_tile <= 0:
        return None
    tiles = re.search(r'Rendered\s+(\d+)\s*/\s*(\d+)\s+Tiles', text)
    finished, count = (int(tiles[1]), int(tiles[2])) if tiles else (0, 1)
    if count <= 0:
        return None
    finished = min(finished, count); done = min(done, per_tile)
    if finished != state['tile']:
        state['tile'] = finished; state['restarted'] = False
    if not state['restarted'] and done < per_tile:
        state['restarted'] = True            # the counter began again for this tile
    within = done if state['restarted'] and finished < count else 0
    state['best'] = max(state['best'], finished * per_tile + within)
    return state['best'], count * per_tile


def report():
    """Print "PROGRESS: Sample n/m" for the whole picture as the render advances. Safe to call before every render."""
    import bpy
    state = new_state()

    def on_stats(message, *_):
        pair = overall(message, state)
        if pair is None:
            return
        now = time.time()
        if pair != state['last'] and (now - state['at'] >= INTERVAL or pair[0] == pair[1]):
            state.update(at=now, last=pair)
            print('PROGRESS: Sample %d/%d' % pair, flush=True)

    on_stats.__name__ = MARK
    handlers = bpy.app.handlers.render_stats
    for old in [handler for handler in handlers if getattr(handler, '__name__', '') == MARK]:
        handlers.remove(old)
    handlers.append(on_stats)
    return on_stats

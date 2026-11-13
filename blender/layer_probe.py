"""Read-only probe of a saved render scene, for laying out the text and icon layer.

Rays through a grid over the picture say what each cell shows: the product
(and which of its parts), scenery standing in the set, or background. Text can
then be placed where the picture is free, and a callout can point at a spot
where its part is really visible. Nothing is rendered and nothing is saved.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bpy
from mathutils import Vector

from pick_surface import product_names

PROP = 'Studio prop '
FREE, SCENERY = -1, -2


def probe(longest):
    scene = bpy.context.scene
    camera = scene.camera
    if not camera or camera.data.type not in {'PERSP', 'ORTHO'}:
        raise ValueError('保存的场景没有可用的出图相机')
    width = scene.render.resolution_x; height = scene.render.resolution_y
    nx = max(8, round(longest * width / max(width, height))); ny = max(8, round(longest * height / max(width, height)))
    graph = bpy.context.evaluated_depsgraph_get()
    frame = camera.data.view_frame(scene=scene)
    left, right = min(v.x for v in frame), max(v.x for v in frame)
    bottom, top = min(v.y for v in frame), max(v.y for v in frame)
    rotation = camera.matrix_world.to_3x3()
    ortho = camera.data.type == 'ORTHO'
    forward = (rotation @ Vector((0, 0, -1))).normalized()
    names = sorted(product_names(scene)); index = {name: position for position, name in enumerate(names)}
    cells = []
    for iy in range(ny):
        for ix in range(nx):
            local = Vector((left + (right - left) * (ix + .5) / nx, top - (top - bottom) * (iy + .5) / ny, frame[0].z))
            if ortho:
                origin = camera.matrix_world @ Vector((local.x, local.y, 0)); direction = forward
            else:
                origin = camera.matrix_world.translation.copy(); direction = (rotation @ local.normalized()).normalized()
            value = FREE
            for _ in range(32):
                hit, location, _, _, ob, _ = scene.ray_cast(graph, origin, direction, distance=camera.data.clip_end)
                if not hit:
                    break
                original = ob.original
                if original.hide_render or not getattr(original, 'visible_camera', True):
                    origin = location + direction * .00001
                    continue
                if original.name in index:
                    value = index[original.name]
                elif original.name.startswith(PROP):
                    value = SCENERY
                break
            cells.append(value)
    used = sorted({value for value in cells if value >= 0}); compact = {value: position for position, value in enumerate(used)}
    return {'nx': nx, 'ny': ny, 'cells': [compact.get(value, value) for value in cells], 'objects': [names[value] for value in used],
            'resolution': [width, height], 'projection': 'orthographic' if ortho else 'perspective'}


def main():
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    try:
        bpy.ops.wm.open_mainfile(filepath=request['blend_path'], load_ui=False, use_scripts=False)
        result = {'success': True, **probe(max(16, min(160, int(request.get('grid', 96)))))}
    except Exception as exc:
        result = {'success': False, 'error': str(exc)[:500]}
    Path(request['result_path']).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    if not result['success']:
        raise RuntimeError(result['error'])


if __name__ == '__main__':
    main()

"""Solve the layout of a usage scene once, headless. Validated data in, transforms out.

The product is imported and prepared exactly as for a render, the downloaded
props are imported at real size, and blender/scene_dress.py decides by
measurement where each can rest. The result is plain numbers: a matrix per
prop, the parts that slide out, and a camera that frames product and props.
Nothing is rendered and the uploaded model is not modified.
"""
import json
import math
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import bpy
import numpy as np
from mathutils import Vector

import scene_dress
from blender.camera_projection import apply_camera, world_points
from worker import load_asset, prepare

PRODUCT_ROOM = .1         # free frame around the product: it fills most of the picture


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def frame(shot, products, extras, aspect):
    """A camera for this shot, set on the product and what rests on or in it.

    The product is the picture. Whatever else is in the scene does not move
    the camera and may be cut by the frame.
    """
    scene = bpy.context.scene
    x, y = [int(value) for value in aspect.split(':')]
    scene.render.resolution_x = round(1024 * x / max(x, y)); scene.render.resolution_y = round(1024 * y / max(x, y))
    camera = scene.objects.get('Layout camera')
    if camera is None:
        camera = bpy.data.objects.new('Layout camera', bpy.data.cameras.new('Layout camera')); scene.collection.objects.link(camera)
    scene.camera = camera; data = camera.data
    view = {key: shot[key] for key in ('id', 'kind', 'projection', 'azimuth', 'elevation', 'focal_length') if key in shot}
    own = world_points(products)
    subject = np.concatenate([own, world_points(extras)]) if extras else own
    apply_camera(scene, view, products, PRODUCT_ROOM, points=subject)
    azimuth = math.radians(view.get('azimuth', 35)); elevation = math.radians(view.get('elevation', 20))
    direction = Vector((math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation), math.sin(elevation)))
    distance = float(camera['wuying_camera_distance'])
    target = camera.matrix_world.translation - direction * distance
    low = own.min(axis=0); high = own.max(axis=0)
    centre = Vector((low + high) / 2); diagonal = max(float(np.linalg.norm(high - low)), 1e-6)
    result = {'target_offset': [float(value) for value in (target - centre) / diagonal], 'elevation': view.get('elevation', 20)}
    if data.type == 'ORTHO':
        result['ortho_scale'] = float(data.ortho_scale)
    else:
        result['camera_distance'] = distance
    return result


def run(request):
    objects, meshes = load_asset(request['model_path'])
    plan = request['plan']
    prepare(objects, meshes, plan)
    units = float(request['units_per_meter'])
    props = []
    for item in request['props']:
        try:
            root, parts = scene_dress.import_prop(item['file'], item['id'])
        except (ValueError, RuntimeError) as exc:
            props.append({'id': item['id'], 'placed': False, 'reason': 'import_failed', 'detail': str(exc)[:200]}); continue
        props.append({'id': item['id'], 'root': root, 'meshes': parts, 'zone': item.get('zone', 'beside'),
                      'pose': item.get('pose', 'auto')})
    usable = [prop for prop in props if 'root' in prop]
    def aim(resting):
        shot = dict(request['shot'])
        if any(prop.get('zone_used') == 'inside' for prop in usable) and request.get('elevation_open') is not None:
            shot['elevation'] = float(request['elevation_open'])        # high enough to look into the drawer
        return frame(shot, meshes, resting, plan.get('aspect', '1:1'))

    opening, report, camera = scene_dress.solve(meshes, usable, request['shot'], units, seed=int(request.get('seed', 1)),
                                                open_keywords=request.get('open_keywords', ()), frame=aim,
                                                room=scene_dress.wall_gap(2.0, float(request.get('wall_distance_m', .6)), units) / 2.0 - .2)
    for prop in usable:
        if not prop['placed']:
            for part in prop['meshes']:
                part.hide_render = True
    rows = []
    for prop in props:
        row = {'id': prop['id'], 'placed': bool(prop.get('placed')), 'zone_used': prop.get('zone_used'), 'reason': prop.get('reason')}
        if prop.get('placed'):
            matrix = prop['root'].matrix_world
            row['matrix'] = [float(matrix[r][c]) for r in range(4) for c in range(4)]
            low, high = scene_dress.world_box(prop['meshes'])
            row['box'] = [[float(v) for v in low], [float(v) for v in high]]
            row['size_mm'] = [round(float(v) / units * 1000, 1) for v in prop['size']]
        if 'detail' in prop:
            row['detail'] = prop['detail']
        rows.append(row)
    low, high = scene_dress.world_box(meshes)
    return {'success': True, 'props': rows, 'open': opening, 'camera': camera, 'report': report,
            'product_box': [[float(v) for v in low], [float(v) for v in high]], 'blender_version': bpy.app.version_string}


def main():
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    out = Path(request['output_dir']); out.mkdir(parents=True, exist_ok=True)
    try:
        _write(out / 'result.json', run(request))
    except Exception as exc:
        traceback.print_exc()
        _write(out / 'result.json', {'success': False, 'error': str(exc)[:500]})
        raise


if __name__ == '__main__':
    main()

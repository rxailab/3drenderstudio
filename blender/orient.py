"""Candidate poses of a model, measured and pictured, to decide which way up it stands.

A model file can arrive lying on its side or upside-down: its axes are whatever
the CAD program used. The model is imported once. For each candidate rotation
this script reports how the model would rest on the floor (what it stands on,
where its weight is, how easily it tips) and where each named part ends up, and
renders one small picture of that pose from a fixed camera. The server, or a
vision model looking at the pictures, then picks the pose; see orientation.py.

Nothing is saved to the project and the uploaded model is not modified.
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
from mathutils import Euler, Vector

from worker import bounds, event, load_asset, prepare

ROOT_NAME = 'Studio asset transform'
MAX_POINTS = 120000        # vertices kept for measuring; more say nothing new about how a model rests
SLAB = .02                 # share of the height that counts as "touching the floor"
GRID = 256                 # footprint resolution for hulls


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def cloud(meshes):
    """World-space vertices, the surface's centre of area, and each part's centre."""
    graph = bpy.context.evaluated_depsgraph_get()
    vertices = []; weighted = np.zeros(3); total = 0.0; parts = []
    for ob in meshes:
        evaluated = ob.evaluated_get(graph); mesh = evaluated.to_mesh()
        try:
            count = len(mesh.vertices)
            if not count:
                continue
            local = np.empty(count * 3, dtype=np.float64); mesh.vertices.foreach_get('co', local)
            matrix = np.array(evaluated.matrix_world, dtype=np.float64)
            world = local.reshape(count, 3) @ matrix[:3, :3].T + matrix[:3, 3]
            mesh.calc_loop_triangles()
            triangles = len(mesh.loop_triangles)
            if triangles:
                index = np.empty(triangles * 3, dtype=np.int32); mesh.loop_triangles.foreach_get('vertices', index)
                index = index.reshape(triangles, 3)
                a, b, c = world[index[:, 0]], world[index[:, 1]], world[index[:, 2]]
                area = np.linalg.norm(np.cross(b - a, c - a), axis=1) * .5
                weighted += ((a + b + c) / 3 * area[:, None]).sum(axis=0); total += float(area.sum())
            parts.append((ob.name, (world.min(axis=0) + world.max(axis=0)) / 2))
            vertices.append(world)
        finally:
            evaluated.to_mesh_clear()
    if not vertices:
        raise ValueError('模型没有可测量的网格')
    points = np.concatenate(vertices)
    if len(points) > MAX_POINTS:
        points = points[np.random.default_rng(7).choice(len(points), MAX_POINTS, replace=False)]
    centre = weighted / total if total > 1e-12 else points.mean(axis=0)
    return points, centre, parts


def matrix_of(rotation):
    return np.array(Euler([math.radians(value) for value in rotation]).to_matrix(), dtype=np.float64)


def hull(points):
    """Convex hull of 2D points (counter-clockwise), by Andrew's monotone chain."""
    rows = sorted(set(map(tuple, points)))
    if len(rows) < 3:
        return rows
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for row in rows:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], row) <= 0: lower.pop()
        lower.append(row)
    upper = []
    for row in reversed(rows):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], row) <= 0: upper.pop()
        upper.append(row)
    return lower[:-1] + upper[:-1]


def area_of(polygon):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))) / 2 if len(polygon) >= 3 else 0.0


def inset(polygon, point):
    """How far inside a convex polygon a point lies; negative when it is outside."""
    if len(polygon) < 3:
        return -1.0
    least = None
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        edge = (b[0] - a[0], b[1] - a[1]); length = math.hypot(*edge)
        if length < 1e-12:
            continue
        distance = (edge[0] * (point[1] - a[1]) - edge[1] * (point[0] - a[0])) / length
        least = distance if least is None else min(least, distance)
    return -1.0 if least is None else least


def measure(points, centre, parts, rotation):
    """How the model rests on the floor in this pose."""
    turn = matrix_of(rotation)
    posed = points @ turn.T; weight = turn @ centre
    low = posed.min(axis=0); high = posed.max(axis=0); size = high - low
    extent = float(max(size.max(), 1e-12)); height = float(max(size[2], extent * 1e-6))
    cell = extent / GRID
    flat = np.unique(np.round((posed[:, :2] - low[:2]) / cell).astype(np.int64), axis=0)
    footprint = hull(flat.tolist())
    touching = posed[posed[:, 2] <= low[2] + max(height * SLAB, extent * .004)]
    support = hull(np.unique(np.round((touching[:, :2] - low[:2]) / cell).astype(np.int64), axis=0).tolist())
    spot = ((weight[0] - low[0]) / cell, (weight[1] - low[1]) / cell)
    lift = max(float(weight[2] - low[2]), extent * 1e-6)
    margin = inset(support, spot) * cell
    centre_box = (low + high) / 2; half = np.maximum(size / 2, extent * 1e-6)
    return {'dimensions': [round(float(value), 6) for value in size],
            'support_ratio': round(area_of(support) / max(area_of(footprint), 1e-9), 4),
            'weight_height': round(lift / height, 4),
            # The angle the model can lean before its weight leaves what it stands on.
            'tip_angle': round(math.degrees(math.atan2(margin, lift)), 2),
            'parts': [{'name': name, 'height': round(float(((turn @ position) - centre_box)[2] / half[2]), 3)} for name, position in parts[:400]]}


def pose(root, meshes, rotation):
    """Stand the model in this rotation exactly as a render would: longest side 2 units, base on the floor, centred."""
    root.location = (0, 0, 0); root.scale = (1, 1, 1)
    root.rotation_euler = Euler([math.radians(value) for value in rotation])
    bpy.context.view_layer.update()
    low, high, _ = bounds(meshes); scale = 2 / max(high - low)
    root.scale = (scale,) * 3
    bpy.context.view_layer.update(); low, high, _ = bounds(meshes)
    root.location = -Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
    bpy.context.view_layer.update()
    return bounds(meshes)[:2]


def stage(size, samples):
    """A plain floor, soft daylight and one camera, the same for every pose."""
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'
    scene.cycles.samples = samples; scene.cycles.use_adaptive_sampling = False
    scene.cycles.max_bounces = 4; scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = size; scene.render.resolution_percentage = 100
    scene.render.film_transparent = False; scene.render.use_persistent_data = True
    scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGB'
    try:
        scene.view_settings.view_transform = 'Standard'
    except TypeError:
        pass
    world = bpy.data.worlds.new('Orientation light'); scene.world = world; world.use_nodes = True
    background = world.node_tree.nodes.get('Background')
    if background:
        background.inputs['Color'].default_value = (.93, .94, .96, 1); background.inputs['Strength'].default_value = .9
    floor_material = bpy.data.materials.new('Orientation floor'); floor_material.use_nodes = True
    shader = next((node for node in floor_material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
    if shader:
        shader.inputs['Base Color'].default_value = (.62, .64, .68, 1); shader.inputs['Roughness'].default_value = .9
    bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, 0))
    floor = bpy.context.active_object; floor.name = 'Orientation floor'; floor.data.materials.append(floor_material)
    sun_data = bpy.data.lights.new('Orientation sun', 'SUN'); sun_data.energy = 2.6; sun_data.angle = math.radians(18)
    sun = bpy.data.objects.new('Orientation sun', sun_data); scene.collection.objects.link(sun)
    sun.rotation_euler = Euler((math.radians(48), 0, math.radians(-32)))
    data = bpy.data.cameras.new('Orientation camera'); data.lens = 60; data.sensor_width = 36; data.clip_start = .01; data.clip_end = 1000
    camera = bpy.data.objects.new('Orientation camera', data); scene.collection.objects.link(camera); scene.camera = camera
    return camera


def aim(camera, low, high, azimuth, elevation):
    """Frame the posed model's bounding sphere from the standard three-quarter view."""
    target = (low + high) / 2; radius = (high - low).length / 2
    az = math.radians(azimuth); el = math.radians(elevation)
    direction = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    half = math.atan(camera.data.sensor_width / 2 / camera.data.lens)
    camera.location = target + direction * (radius / math.sin(half) * 1.06)
    camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()


def run(request):
    out = Path(request['output_dir'])
    candidates = request['candidates']
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 12:
        raise ValueError('候选姿态数量无效')
    event('正在导入模型')
    objects, meshes = load_asset(request['model_path'])
    plan = {key: value for key, value in (request.get('plan') or {}).items() if key not in ('pose', 'scene_set', 'dimensions_mm')}
    plan['rotation'] = [0, 0, 0]
    try:
        prepare(objects, meshes, plan)
    except Exception:                     # the model's own materials are enough to tell which way up it is
        traceback.print_exc()
        objects, meshes = load_asset(request['model_path'])
        prepare(objects, meshes, {'rotation': [0, 0, 0]})
    root = bpy.data.objects[ROOT_NAME]
    # Measured once in the file's own frame; every pose is that cloud turned.
    pose(root, meshes, [0, 0, 0])
    points, centre, parts = cloud(meshes)
    size = max(128, min(640, int(request.get('size', 320)))); samples = max(4, min(64, int(request.get('samples', 16))))
    camera = stage(size, samples) if request.get('render', True) else None
    rows = []
    for index, candidate in enumerate(candidates, 1):
        rotation = [float(value) for value in candidate['rotation']]
        row = {'id': str(candidate['id']), 'rotation': rotation, **measure(points, centre, parts, rotation)}
        if camera:
            event('正在查看候选姿态 %d / %d' % (index, len(candidates)))
            low, high = pose(root, meshes, rotation)
            aim(camera, low, high, float(request.get('azimuth', 35)), float(request.get('elevation', 22)))
            target = out / ('pose_%s.png' % row['id'])
            bpy.context.scene.render.filepath = str(target)
            bpy.ops.render.render(write_still=True)
            if not target.is_file():
                raise RuntimeError('候选姿态图片未生成')
            row['image'] = target.name
        rows.append(row)
    return {'success': True, 'candidates': rows, 'size': size, 'blender_version': bpy.app.version_string}


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

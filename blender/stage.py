"""Stage one model on transparent film for listing pages, alone or with a plain workpiece.

The request is written by the application from validated settings, never from model output.
The product is prepared exactly as for a studio render (pose, finishes, two units long) and
lit by the white-background studio, so a cut-out matches the project's own images. On top of
that this script can

* put a parametric workpiece (pipe, rod, board, sheet, block) through the tool's opening,
  under it or in front of it,
* choose a view that shows a named part or the workpiece, and
* report where things are in the picture: every part's outline, a visibility grid for
  callouts, and the end points of dimension lines in picture coordinates.

Nothing is written back to the uploaded model.
"""
import json
import math
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import bpy
from mathutils import Vector, Euler
from bpy_extras.object_utils import world_to_camera_view

from blender import worker
from blender import stage_geometry as geometry

FREE = -1
FLOOR = 'Studio floor'
WORKPIECE = 'Listing workpiece'
MATERIALS = {            # colour, roughness, metallic
    'pvc_white': ((.86, .86, .83), .34, 0), 'pvc_grey': ((.36, .38, .4), .4, 0), 'plastic_black': ((.03, .03, .035), .45, 0),
    'copper': ((.93, .5, .33), .24, 1), 'steel': ((.6, .62, .64), .3, 1), 'aluminium': ((.8, .81, .82), .35, 1),
    'wood': ((.62, .43, .24), .55, 0), 'concrete': ((.5, .5, .48), .92, 0), 'drywall': ((.84, .83, .8), .9, 0)}
ROUND = ('pipe', 'rod')


def material(name):
    color, rough, metal = MATERIALS.get(name, MATERIALS['pvc_white'])
    mat = bpy.data.materials.new('Workpiece ' + name); mat.use_nodes = True
    shader = worker.bsdf_of(mat)
    shader.inputs['Base Color'].default_value = (*color, 1); shader.inputs['Roughness'].default_value = rough
    shader.inputs['Metallic'].default_value = metal
    if name in ('wood', 'concrete'):
        nodes = mat.node_tree.nodes; links = mat.node_tree.links
        coords = nodes.new('ShaderNodeTexCoord'); mapping = nodes.new('ShaderNodeMapping'); noise = nodes.new('ShaderNodeTexNoise')
        ramp = nodes.new('ShaderNodeValToRGB')
        mapping.inputs['Scale'].default_value = (.35, 6, 6) if name == 'wood' else (5, 5, 5)
        noise.inputs['Scale'].default_value = 4 if name == 'wood' else 9; noise.inputs['Detail'].default_value = 8
        dark = tuple(value * (.62 if name == 'wood' else .8) for value in color)
        ramp.color_ramp.elements[0].color = (*dark, 1); ramp.color_ramp.elements[1].color = (*color, 1)
        links.new(coords.outputs['Object'], mapping.inputs['Vector']); links.new(mapping.outputs['Vector'], noise.inputs['Vector'])
        links.new(noise.outputs['Fac'], ramp.inputs['Fac']); links.new(ramp.outputs['Color'], shader.inputs['Base Color'])
    return mat


def build_workpiece(spec, unit_mm):
    """The workpiece lying along its own X axis, centred on the origin. Sizes come in millimetres."""
    kind = spec['kind']; scale = 1 / unit_mm
    length = spec['length_mm'] * scale
    if kind in ROUND:
        radius = spec['diameter_mm'] / 2 * scale
        hollow = kind == 'pipe'
        bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=radius, depth=length, end_fill_type='NOTHING' if hollow else 'NGON',
                                            rotation=(0, math.pi / 2, 0))
        ob = bpy.context.active_object
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        for polygon in ob.data.polygons:
            polygon.use_smooth = True
        if hollow:
            wall = ob.modifiers.new('Wall', 'SOLIDIFY')
            wall.thickness = min(radius * .6, max(radius * .06, spec.get('wall_mm', spec['diameter_mm'] * .08) * scale)); wall.offset = -1
        split = ob.modifiers.new('Edges', 'EDGE_SPLIT'); split.split_angle = math.radians(40)
        half = Vector((length / 2, radius, radius))
    else:
        width = spec['width_mm'] * scale; thickness = spec['thickness_mm'] * scale
        bpy.ops.mesh.primitive_cube_add(size=1)
        ob = bpy.context.active_object; ob.scale = (length, width, thickness)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bevel = ob.modifiers.new('Edges', 'BEVEL'); bevel.width = min(length, width, thickness) * .03; bevel.segments = 2
        half = Vector((length / 2, width / 2, thickness / 2))
    ob.name = WORKPIECE
    ob.data.materials.append(material(spec.get('material', 'pvc_white')))
    return ob, half


def find_opening(lo, hi, cells=56):
    """The most enclosed gap that runs right through the product along one of its axes, or None."""
    scene = bpy.context.scene; graph = bpy.context.evaluated_depsgraph_get(); size = hi - lo; best = None
    for axis in range(3):
        u, v = [index for index in range(3) if index != axis]
        step = max(size[u], size[v]) / cells
        if step <= 0 or size[axis] <= 0:
            continue
        nu = max(4, math.ceil(size[u] / step)); nv = max(4, math.ceil(size[v] / step))
        direction = Vector([1.0 if index == axis else 0.0 for index in range(3)])
        occupied = []
        for iv in range(nv):
            for iu in range(nu):
                origin = Vector((0, 0, 0)); origin[axis] = lo[axis] - size[axis] * .01 - 1e-4
                origin[u] = lo[u] + (iu + .5) * step; origin[v] = lo[v] + (iv + .5) * step
                occupied.append(bool(scene.ray_cast(graph, origin, direction, distance=size[axis] * 1.02 + 2e-4)[0]))
        found = geometry.largest_opening(occupied, nu, nv)
        if not found:
            continue
        iu, iv, radius, enclosure = found
        if radius * step < max(size) * geometry.MIN_OPENING:
            continue
        centre = (lo + hi) / 2; centre[u] = lo[u] + (iu + .5) * step; centre[v] = lo[v] + (iv + .5) * step
        score = radius * step * (enclosure - .5)
        if best is None or score > best['score']:
            best = {'axis': axis, 'centre': centre, 'radius': radius * step, 'enclosure': enclosure, 'score': score}
    return best


def place_workpiece(spec, lo, hi, unit_mm, root):
    """Build the workpiece and put it where the request says. Returns (object, facts about it)."""
    place = spec.get('place') or {}; mode = place.get('mode', 'through'); notes = []
    size = hi - lo; centre = (lo + hi) / 2; opening = None
    spec = dict(spec)
    long_axis = 0 if size.x >= size.y else 1
    if mode == 'through':
        opening = find_opening(lo, hi)
        if opening is None:
            notes.append('no_opening'); mode = 'manual'
    if mode == 'through':
        axis = opening['axis']; limit_mm = opening['radius'] * 2 * unit_mm
        if spec['kind'] in ROUND:
            if not spec.get('diameter_mm'):
                spec['diameter_mm'] = geometry.stock_size(limit_mm)
            elif spec['diameter_mm'] > limit_mm * 1.02:
                notes.append('wider_than_opening')
        else:
            if not spec.get('thickness_mm'):
                spec['thickness_mm'] = geometry.stock_size(limit_mm, geometry.BOARD_THICKNESSES)
            spec.setdefault('width_mm', max(spec['thickness_mm'] * 4, size[long_axis] * unit_mm * .6))
        spec['length_mm'] = spec.get('length_mm') or size[axis] * unit_mm + max(size) * unit_mm * 6
        position = opening['centre'].copy(); direction = axis
    else:
        if spec['kind'] in ROUND:
            spec['diameter_mm'] = spec.get('diameter_mm') or geometry.stock_size(min(size.x, size.y, size.z) * unit_mm * .5)
        else:
            spec['thickness_mm'] = spec.get('thickness_mm') or {'sheet': 3.0, 'block': 60.0}.get(spec['kind'], 18.0)
            spec['width_mm'] = spec.get('width_mm') or (size[1 - long_axis] * unit_mm * 1.5 + max(size) * unit_mm * .3 if mode == 'under'
                                                        else max(size[1 - long_axis] * unit_mm * 2.2, size.z * unit_mm * 1.6))
        # Under the tool the stock runs out of the picture both ways; in front of it, it stands a little taller than the tool.
        spec['length_mm'] = spec.get('length_mm') or (max(size) * unit_mm * 5 if mode == 'under' else max(size[1 - long_axis], size.z) * unit_mm * 2.2)
        direction = long_axis; position = centre.copy()
    ob, half = build_workpiece(spec, unit_mm)
    thickness = half.z * 2
    if mode == 'under':
        # The product stands on the workpiece: lift it, and lay the workpiece on the floor beneath.
        root.location.z += thickness; position = Vector((centre.x, centre.y, thickness / 2))
    elif mode == 'front':
        # Standing across the product's long axis, touching one end of it, like a wall or a board being worked.
        side = -1 if place.get('side') == '-' else 1
        across = 1 - long_axis; direction = across
        position = Vector((centre.x, centre.y, max(half.y, centre.z)))
        position[long_axis] = (hi if side > 0 else lo)[long_axis] + side * half.z
    turn = Euler((0, 0, 0))
    if direction == 1:
        turn = Euler((0, 0, math.pi / 2))
    elif direction == 2:
        turn = Euler((0, -math.pi / 2, 0))
    if mode == 'front':
        turn = Euler((math.pi / 2, 0, math.pi / 2 if direction == 1 else 0))
    ob.rotation_euler = turn
    extra = place.get('rotate') or [0, 0, 0]
    if any(extra):
        ob.rotation_euler.rotate(Euler([math.radians(value) for value in extra]))
    offset = place.get('offset_mm') or [0, 0, 0]
    ob.location = position + Vector([value / unit_mm for value in offset])
    bpy.context.view_layer.update()
    axis_vector = (ob.matrix_world.to_3x3() @ Vector((1, 0, 0))).normalized()
    facts = {'kind': spec['kind'], 'material': spec.get('material', 'pvc_white'), 'mode': mode, 'notes': notes, 'length_mm': round(spec['length_mm'], 1),
             'axis': list(axis_vector), 'centre': list(ob.location), 'half': list(half)}
    for key in ('diameter_mm', 'width_mm', 'thickness_mm', 'wall_mm'):
        if spec.get(key):
            facts[key] = round(float(spec[key]), 1)
    if opening:
        facts['opening_mm'] = round(opening['radius'] * 2 * unit_mm, 1); facts['enclosure'] = round(opening['enclosure'], 3)
    return ob, facts


def direction_of(azimuth, elevation):
    az = math.radians(azimuth); el = math.radians(elevation)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def corners(objects):
    graph = bpy.context.evaluated_depsgraph_get()
    return [ob.evaluated_get(graph).matrix_world @ Vector(corner) for ob in objects for corner in ob.evaluated_get(graph).bound_box]


def fit(camera, points, azimuth, elevation, margin):
    """Frame the points from this direction, as close as the margin allows."""
    scene = bpy.context.scene; render = scene.render
    lo = Vector([min(point[i] for point in points) for i in range(3)]); hi = Vector([max(point[i] for point in points) for i in range(3)])
    target = (lo + hi) / 2; direction = direction_of(azimuth, elevation); reach = max((hi - lo).length, 1e-6)
    usable = max(.05, 1 - 2 * margin)
    if camera.data.type == 'ORTHO':
        camera.location = target + direction * reach * 4; worker.aim(camera, target); bpy.context.view_layer.update()
        inverse = camera.matrix_world.inverted(); local = [inverse @ point for point in points]
        xs = [point.x for point in local]; ys = [point.y for point in local]
        rotation = camera.matrix_world.to_3x3()
        camera.location += rotation @ Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0))
        width = max(xs) - min(xs); height = max(ys) - min(ys); ratio = render.resolution_x / render.resolution_y
        camera.data.ortho_scale = max(width / usable, height / usable * ratio) if ratio >= 1 else max(height / usable, width / usable / ratio)
    else:
        low = .01; high = reach * 40
        for _ in range(48):
            middle = (low + high) / 2
            camera.location = target + direction * middle; worker.aim(camera, target); bpy.context.view_layer.update()
            seen = [world_to_camera_view(scene, camera, point) for point in points]
            if all(co.z > 0 and margin <= co.x <= 1 - margin and margin <= co.y <= 1 - margin for co in seen):
                high = middle
            else:
                low = middle
        camera.location = target + direction * high * 1.003; worker.aim(camera, target)
    bpy.context.view_layer.update()
    return target


def project(camera, point):
    co = world_to_camera_view(bpy.context.scene, camera, Vector(point))
    return [round(co.x, 5), round(1 - co.y, 5)]


def grid_probe(camera, names, longest):
    """Which object each cell of the picture shows: an index into ``names`` or FREE."""
    scene = bpy.context.scene; width = scene.render.resolution_x; height = scene.render.resolution_y
    nx = max(8, round(longest * width / max(width, height))); ny = max(8, round(longest * height / max(width, height)))
    graph = bpy.context.evaluated_depsgraph_get(); frame = camera.data.view_frame(scene=scene)
    left, right = min(v.x for v in frame), max(v.x for v in frame); bottom, top = min(v.y for v in frame), max(v.y for v in frame)
    rotation = camera.matrix_world.to_3x3(); ortho = camera.data.type == 'ORTHO'
    forward = (rotation @ Vector((0, 0, -1))).normalized(); index = {name: position for position, name in enumerate(names)}
    cells = []
    for iy in range(ny):
        for ix in range(nx):
            local = Vector((left + (right - left) * (ix + .5) / nx, top - (top - bottom) * (iy + .5) / ny, frame[0].z))
            if ortho:
                origin = camera.matrix_world @ Vector((local.x, local.y, 0)); direction = forward
            else:
                origin = camera.matrix_world.translation.copy(); direction = (rotation @ local.normalized()).normalized()
            value = FREE
            for _ in range(16):
                hit, location, _, _, ob, _ = scene.ray_cast(graph, origin, direction, distance=camera.data.clip_end)
                if not hit:
                    break
                original = ob.original
                if original.hide_render or not getattr(original, 'visible_camera', True) or original.name == FLOOR:
                    origin = location + direction * .00001
                    continue
                value = index.get(original.name, FREE)
                break
            cells.append(value)
    return nx, ny, cells


def outlines(camera, objects):
    """Each object's outline in the picture, from all of its vertices: {'name', 'rect': [x0, y0, x1, y1]} with y down."""
    import numpy
    scene = bpy.context.scene; graph = bpy.context.evaluated_depsgraph_get()
    matrix = numpy.array(camera.calc_matrix_camera(graph, x=scene.render.resolution_x, y=scene.render.resolution_y) @ camera.matrix_world.inverted())
    rows = []
    for ob in objects:
        evaluated = ob.evaluated_get(graph)
        try:
            mesh = evaluated.to_mesh()
        except RuntimeError:
            continue
        count = len(mesh.vertices)
        if count:
            flat = numpy.empty(count * 3, dtype=numpy.float32); mesh.vertices.foreach_get('co', flat)
            world = numpy.array(evaluated.matrix_world); points = flat.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
            clip = numpy.c_[points, numpy.ones(count)] @ matrix.T
            depth = numpy.where(numpy.abs(clip[:, 3]) < 1e-9, 1e-9, clip[:, 3])
            x = (clip[:, 0] / depth + 1) / 2; y = 1 - (clip[:, 1] / depth + 1) / 2
            rows.append({'name': ob.name, 'rect': [round(float(value), 5) for value in (x.min(), y.min(), x.max(), y.max())]})
        evaluated.to_mesh_clear()
    return rows


def union(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)] if rects else None


def dimension_edges(camera, lo, hi, unit_mm):
    """For each axis the box edge that reads best in the picture: lowest for the floor axes, outermost for the height."""
    rows = []
    for axis, name in enumerate('xyz'):
        others = [index for index in range(3) if index != axis]; candidates = []
        for a in (lo, hi):
            for b in (lo, hi):
                start = Vector((0, 0, 0)); start[others[0]] = a[others[0]]; start[others[1]] = b[others[1]]
                end = start.copy(); start[axis] = lo[axis]; end[axis] = hi[axis]
                p1 = project(camera, start); p2 = project(camera, end)
                middle = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                candidates.append((middle[1] if axis < 2 else abs(middle[0] - .5), p1, p2))
        _, p1, p2 = max(candidates, key=lambda row: row[0])
        rows.append({'axis': name, 'mm': round((hi[axis] - lo[axis]) * unit_mm, 1), 'p1': p1, 'p2': p2,
                     'apparent': round(math.hypot(p2[0] - p1[0], p2[1] - p1[1]), 5)})
    return rows


def workpiece_anchors(camera, facts, lo, hi):
    """Where the workpiece's own size is read off: across a round one, through the thickness of a flat one."""
    axis = Vector(facts['axis']); centre = Vector(facts['centre']); half = Vector(facts['half'])
    eye = camera.matrix_world.translation
    if facts.get('shown'):
        # Between the tool and the cut end that is in the picture.
        side = facts['side']; point = Vector(facts['middle']) + axis * side * (facts['reach'] + (facts['shown'] - facts['reach']) * .58)
    else:
        side = 1 if (eye - centre).dot(axis) >= 0 else -1
        point = centre + axis * side * half.x * .8
    view = (eye - point).normalized() if camera.data.type != 'ORTHO' else (camera.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
    anchors = {}
    if facts['kind'] in ROUND:
        across = axis.cross(view)
        if across.length > 1e-6:
            across.normalize(); radius = half.y
            anchors['diameter'] = {'mm': facts['diameter_mm'], 'p1': project(camera, point + across * radius), 'p2': project(camera, point - across * radius)}
    else:
        ob = bpy.data.objects[WORKPIECE]; rotation = ob.matrix_world.to_3x3()
        up = (rotation @ Vector((0, 0, 1))).normalized(); wide = (rotation @ Vector((0, 1, 0))).normalized()
        near = facts.get('near') or (1 if (eye - centre).dot(wide) >= 0 else -1)
        if facts.get('reach'):
            corner = Vector(facts['middle']) + axis * side * facts['reach'] * .6 + wide * near * half.y
        else:
            corner = centre + axis * side * half.x + wide * near * half.y
        anchors['thickness'] = {'mm': facts['thickness_mm'], 'p1': project(camera, corner + up * half.z), 'p2': project(camera, corner - up * half.z)}
    return anchors


def visible_share(camera, names, wanted, longest=40):
    _, _, cells = grid_probe(camera, names, longest)
    targets = {index for index, name in enumerate(names) if name in wanted}
    return sum(1 for cell in cells if cell in targets) / max(1, len(cells))


def _import(path):
    """Import one more model into the scene without clearing it. Returns its new mesh objects and their root objects."""
    before = set(bpy.data.objects)
    suffix = Path(path).suffix.lower()
    if suffix == '.obj':
        bpy.ops.wm.obj_import(filepath=str(path), forward_axis='NEGATIVE_Z', up_axis='Y')
    elif suffix == '.glb':
        bpy.ops.import_scene.gltf(filepath=str(path), import_pack_images=True)
    elif suffix == '.stl':
        if hasattr(bpy.ops.wm, 'stl_import'):
            bpy.ops.wm.stl_import(filepath=str(path))
        else:
            bpy.ops.import_mesh.stl(filepath=str(path))
    else:
        raise ValueError('组件模型格式不受支持')
    added = [ob for ob in bpy.data.objects if ob not in before]
    for ob in added:
        if ob.type in ('LIGHT', 'CAMERA'):
            ob.hide_render = True
    return [ob for ob in added if ob.type == 'MESH' and len(ob.data.polygons)], [ob for ob in added if ob.parent is None]


def _remapped(plan, meshes):
    """The component's own finishes, with material names as they came in here (an import renames clashes 'Name.001')."""
    import re
    present = {m.name for ob in meshes for m in ob.data.materials if m}
    base = lambda name: re.sub(r'(?:\.\d{3})+$', '', name)

    def actual(name):
        if name in ('*', 'all') or name in present:
            return name
        return next((candidate for candidate in sorted(present) if base(candidate) == name), None)
    overrides = []
    for override in plan.get('material_overrides') or []:
        name = actual(override.get('name', '*'))
        if name:
            overrides.append({**override, 'name': name})
    return {**plan, 'material_overrides': overrides, 'object_material_overrides': []}


KIT_SCALE = .72                  # kit images show the product larger than its accessories, as the examples do


def add_companions(rows, unit_mm, lo, hi, azimuth, kit_scale=KIT_SCALE):
    """The other things in the box, each at its own real size, arranged round the product the way a kit is shown.

    Containers (a case, a bag) stand behind the product; everything else flanks it, larger things nearer, a little in
    front so nothing hides the product; a quantity of two or more shows two. Returns [{'name', 'meshes', 'role'}].
    """
    toward = Vector((math.sin(math.radians(azimuth)), -math.cos(math.radians(azimuth)), 0))     # from the product to the camera, level
    across = Vector((math.cos(math.radians(azimuth)), math.sin(math.radians(azimuth)), 0))     # to the camera's right
    radius = max((hi - lo).x, (hi - lo).y) / 2; gap = max(hi - lo) * .06
    placed = []; front = []; back = []
    for row in rows:
        for copy in range(min(2, int(row.get('quantity') or 1))):
            meshes, roots = _import(row['model_path'])
            if not meshes:
                continue
            holder = bpy.data.objects.new('Companion %s %d' % (row.get('name', ''), copy), None); bpy.context.scene.collection.objects.link(holder)
            for ob in roots:
                matrix = ob.matrix_world.copy(); ob.parent = holder; ob.matrix_world = matrix
            holder.rotation_euler = Euler([math.radians(value) for value in row.get('rotation') or [0, 0, 0]])
            bpy.context.view_layer.update()
            c_lo, c_hi, _ = worker.bounds(meshes)
            size_mm = float(row.get('size_mm') or 100)
            scale = size_mm / unit_mm / max(max(c_hi - c_lo), 1e-9) * kit_scale
            holder.scale = (scale,) * 3; bpy.context.view_layer.update()
            c_lo, c_hi, _ = worker.bounds(meshes)
            # Stand it on the floor with its longer side across the picture.
            if (c_hi - c_lo).y > (c_hi - c_lo).x * 1.15:
                holder.rotation_euler.rotate(Euler((0, 0, math.radians(90)))); bpy.context.view_layer.update(); c_lo, c_hi, _ = worker.bounds(meshes)
            holder.location -= Vector(((c_lo.x + c_hi.x) / 2, (c_lo.y + c_hi.y) / 2, c_lo.z)); bpy.context.view_layer.update()
            c_lo, c_hi, _ = worker.bounds(meshes)
            try:
                worker.apply_material_overrides(meshes, _remapped(row.get('plan') or {}, meshes))
            except ValueError:
                pass
            entry = {'name': row.get('name', ''), 'role': row.get('role', 'other'), 'meshes': meshes, 'holder': holder, 'size': c_hi - c_lo}
            (back if row.get('role') in ('case', 'bag') else front).append(entry); placed.append(entry)
    # Behind: containers side by side, close behind the product and a little to the left, so the product overlaps them.
    offset = -radius * .35
    for entry in back:
        depth = (entry['size'].x * abs(toward.x) + entry['size'].y * abs(toward.y)) / 2
        width = entry['size'].x * abs(across.x) + entry['size'].y * abs(across.y)
        entry['holder'].location += -toward * (radius * .2 + depth) + across * (offset + width / 2 - width * .5)
        offset += width + gap
    # In front: the smaller things in the two front corners, larger first, so the product stays whole between and above them.
    half = max(abs(Vector((x, y, 0)).dot(across)) for x in (lo.x, hi.x) for y in (lo.y, hi.y))
    front.sort(key=lambda entry: -max(entry['size']))
    sides = {1: half * .35, -1: half * .35}; rows = {1: 0.0, -1: 0.0}
    for index, entry in enumerate(front):
        side = -1 if index % 2 == 0 else 1
        width = entry['size'].x * abs(across.x) + entry['size'].y * abs(across.y)
        depth = entry['size'].x * abs(toward.x) + entry['size'].y * abs(toward.y)
        if sides[side] + width > half * 1.9 and sides[side] > half * .35:
            sides[side] = half * .35; rows[side] += depth + gap * .5        # a second, nearer row on that side
        entry['holder'].location += toward * (radius * .7 + depth / 2 + rows[side]) + across * side * (sides[side] + width / 2)
        sides[side] += width + gap * .6
    bpy.context.view_layer.update()
    return [{'name': entry['name'], 'role': entry['role'], 'meshes': entry['meshes']} for entry in placed]


def run(request):
    out = Path(request['output_dir']); out.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene; started = time.time()
    plan = dict(request.get('plan') or {})
    plan.setdefault('rotation', [0, 0, 0]); plan.setdefault('exposure', 0); plan.setdefault('light_softness', 1)
    plan['style'] = 'white'; plan.pop('scene_set', None)
    worker.event('正在导入模型')
    objects, meshes = worker.load_asset(request['model_path'])
    operations, warnings = worker.prepare(objects, meshes, plan)
    if plan.get('look') == 'showcase':
        worker.soften_edges(meshes)
    root = bpy.data.objects['Studio asset transform']
    lo, hi, _ = worker.bounds(meshes)
    unit_mm = float(request['size_mm']) / max(hi - lo)
    names = [ob.name for ob in meshes]
    width, height = [int(value) for value in request.get('size', [1600, 1600])]
    scene.render.resolution_x = width; scene.render.resolution_y = height; scene.render.resolution_percentage = 100
    piece = None; facts = None
    companions = []
    if request.get('companions'):
        worker.event('正在摆放套装组件')
        companions = add_companions(request['companions'], unit_mm, lo, hi, float((request.get('camera') or {}).get('azimuth', 35)))
    if request.get('workpiece'):
        worker.event('正在放置工件')
        piece, facts = place_workpiece(request['workpiece'], lo, hi, unit_mm, root)
        bpy.context.view_layer.update(); lo, hi, _ = worker.bounds(meshes)
    view = request.get('camera') or {}
    data = bpy.data.cameras.new('Listing camera'); camera = bpy.data.objects.new('Listing camera', data)
    scene.collection.objects.link(camera); scene.camera = camera
    data.sensor_width = 36; data.clip_start = .01; data.clip_end = 1000; data.lens = float(view.get('focal_length', 85))
    if view.get('projection') == 'orthographic':
        data.type = 'ORTHO'
    margin = float(view.get('margin', .05)); elevation = float(view.get('elevation', 18)); azimuth = float(view.get('azimuth', 35))
    focus = [ob for ob in meshes if ob.name in set(view.get('focus') or [])]
    framed = corners(focus or meshes); passing = None
    if not focus and plan.get('articulation_envelope'):
        # every state of the product framed alike: the camera takes in all of them, so they are drawn at one scale
        from blender import articulate
        framed += articulate.ENVELOPE
    for entry in companions:
        framed += corners(entry['meshes'])
    if piece is not None and facts['mode'] == 'under' and view.get('frame') != 'all':
        # The stock's near edge, under the tool, belongs in the picture: its thickness is read there.
        rotation = piece.matrix_world.to_3x3(); centre = Vector(facts['centre']); half = Vector(facts['half']); axis = Vector(facts['axis'])
        wide = (rotation @ Vector((0, 1, 0))).normalized(); up = (rotation @ Vector((0, 0, 1))).normalized()
        near = 1 if direction_of(azimuth, elevation).dot(wide) >= 0 else -1
        reach = sum(abs(axis[i]) * (hi[i] - lo[i]) / 2 for i in range(3)); middle = centre + axis * ((lo + hi) / 2 - centre).dot(axis)
        framed += [middle + axis * reach * turn + wide * near * half.y - up * half.z for turn in (-1, 1)]
        facts.update(near=near, reach=reach, middle=list(middle))
    elif piece is not None and (view.get('frame') == 'all' or facts['mode'] == 'front'):
        framed += corners([piece])
    elif piece is not None and not focus:
        # A stretch of the workpiece either side of the tool; the camera is chosen on that.
        axis = Vector(facts['axis']); centre = Vector(facts['centre'])
        reach = sum(abs(axis[i]) * (hi[i] - lo[i]) / 2 for i in range(3)); shown = reach + max(hi - lo) * .34
        middle = centre + axis * ((lo + hi) / 2 - centre).dot(axis)
        framed += [middle + axis * shown, middle - axis * shown]; passing = (axis, middle, reach, shown)
    candidates = [float(value) for value in view.get('azimuths') or []]
    if piece is not None and view.get('auto') == 'workpiece':
        axis = Vector(facts['axis'])
        if abs(axis.z) < .9:
            along = math.degrees(math.atan2(axis.x, -axis.y))
            candidates = sorted(((along + turn + 180) % 360 - 180 for turn in (55, -55, 125, -125)), key=lambda value: abs(value - azimuth))
    if candidates:
        wanted = {ob.name for ob in focus} or set(names); scores = []
        for candidate in candidates:
            fit(camera, framed, candidate, elevation, margin)
            scores.append((visible_share(camera, names, wanted), -abs(candidate - azimuth), candidate))
        best = max(scores)
        # Views within a tenth of the best show as much: keep the one nearest the asked direction.
        azimuth = max((row for row in scores if row[0] >= best[0] * .9), key=lambda row: row[1])[2]
    if passing and not request['workpiece'].get('length_mm'):
        # Its cut end stays in the picture on the camera's side; the other way it runs out of the frame.
        axis, middle, reach, shown = passing; half = Vector(facts['half'])
        side = 1 if direction_of(azimuth, elevation).dot(axis) >= 0 else -1
        piece.location = middle + axis * side * (shown - half.x)
        bpy.context.view_layer.update()
        facts.update(centre=list(piece.location), side=side, shown=shown, reach=reach, middle=list(middle))
        framed = corners(focus or meshes) + [middle + axis * side * shown * 1.03, middle - axis * side * shown]
    fit(camera, framed, azimuth, elevation, margin)
    offset = view.get('offset') or [0, 0]
    if any(offset):
        # Slide the picture inside its frame without changing the view, to leave room for words.
        larger = max(width, height)
        data.shift_x = -float(offset[0]) * width / larger; data.shift_y = float(offset[1]) * height / larger
        bpy.context.view_layer.update()
    shot = {'id': 'listing', 'kind': 'full', 'style': 'white', 'azimuth': azimuth, 'elevation': elevation, 'focal_length': data.lens}
    studio = worker.studio(plan, shot, meshes)
    floor = bpy.data.objects.get(FLOOR)
    if floor is not None and (request.get('ground') != 'shadow' or (facts and facts['mode'] in ('through', 'manual'))):
        floor.hide_render = True; floor.hide_viewport = True
    bpy.context.view_layer.update()
    known = names + ([WORKPIECE] if piece is not None else [])
    nx, ny, cells = grid_probe(camera, known, int(request.get('grid', 96)))
    parts = outlines(camera, meshes)
    kit = [{'name': entry['name'], 'role': entry['role'], 'rect': union([row['rect'] for row in outlines(camera, entry['meshes'])])} for entry in companions]
    result = {'success': True, 'width': width, 'height': height, 'unit_mm': unit_mm, 'azimuth': azimuth, 'elevation': elevation,
              'projection': 'orthographic' if data.type == 'ORTHO' else 'perspective',
              'dimensions_mm': [round((hi[i] - lo[i]) * unit_mm, 1) for i in range(3)],
              'product_rect': union([row['rect'] for row in parts]), 'parts': parts,
              **({'companions': kit, 'kit_rect': union([row['rect'] for row in parts] + [row['rect'] for row in kit if row['rect']])} if kit else {}),
              'probe': {'success': True, 'nx': nx, 'ny': ny, 'cells': cells, 'objects': known},
              'dimension_edges': dimension_edges(camera, lo, hi, unit_mm), 'warnings': warnings,
              'lights': len(studio.get('lights', [])), 'blender_version': bpy.app.version_string}
    if piece is not None:
        result['workpiece'] = {key: value for key, value in facts.items() if key not in ('centre', 'half', 'middle', 'side', 'shown', 'reach', 'near')}
        result['workpiece']['rect'] = union([row['rect'] for row in outlines(camera, [piece])])
        result['workpiece']['anchors'] = workpiece_anchors(camera, facts, lo, hi)
    if request.get('action') != 'measure':
        device = worker.device(bool(request.get('force_cpu')))
        scene.cycles.samples = int(request.get('samples', 64)); scene.cycles.use_adaptive_sampling = True
        scene.cycles.adaptive_threshold = float(request.get('noise', .03)); scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 8; scene.cycles.transmission_bounces = 8
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGBA'; scene.render.image_settings.color_depth = '8'
        scene.render.filepath = str(out / 'picture.png')
        worker.event('Cycles 正在渲染', device=device, sample_target=scene.cycles.samples, render_width=width, render_height=height)
        from blender.render_progress import report
        report()
        bpy.ops.render.render(write_still=True)
        result.update(image='picture.png', device=device.get('type'), samples=scene.cycles.samples)
    result['seconds'] = round(time.time() - started, 2)
    return result


def main():
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    out = Path(request['output_dir']); out.mkdir(parents=True, exist_ok=True)
    try:
        result = run(request)
    except Exception as exc:
        traceback.print_exc()
        worker.atomic(out / 'result.json', {'success': False, 'error': str(exc)[:500]})
        raise
    worker.atomic(out / 'result.json', result)
    worker.event('执行完成')


if __name__ == '__main__':
    main()

"""Read-only, bounded geometry catalog from an existing rendered .blend.

Run with Blender --background --factory-startup --disable-autoexec --python.
The original file is never saved.  Neutral isolated views show actual evaluated
geometry; these images do not assert any semantic part name or final material.
"""
import hashlib
import json
import math
from pathlib import Path
import sys


MAX_TARGETS = 160
TARGETS_PER_PAGE = 40
MAX_TRIANGLES_PER_TARGET = 100000
MAX_TOTAL_TRIANGLES = 1000000
MAX_COMPONENTS = 4096
PAGE_WIDTH, PAGE_HEIGHT = 1600, 1200
TILE_WIDTH, TILE_HEIGHT = 200, 240
SURFACE_INPUTS = {'roughness': ('Roughness',), 'metallic': ('Metallic',),
                  'transmission': ('Transmission Weight', 'Transmission')}


def _finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def _surface_measurement(materials):
    """Read only constant inputs of the active, direct Principled surface.

    A socket's default is not its rendered value when linked. Complex output
    networks and differing values across mapped slots remain unknown instead
    of being averaged or inferred from an imported material preset.
    """
    measured = []; linked = set()
    for material in materials:
        tree = getattr(material, 'node_tree', None)
        if not material or not tree or not getattr(material, 'use_nodes', True):
            measured.append({}); continue
        outputs = [node for node in tree.nodes if node.type == 'OUTPUT_MATERIAL'
                   and getattr(node, 'is_active_output', False)]
        surface = outputs[0].inputs.get('Surface') if len(outputs) == 1 else None
        links = list(surface.links) if surface is not None else []
        node = links[0].from_node if len(links) == 1 else None
        if (node is None or node.type != 'BSDF_PRINCIPLED'
                or getattr(node, 'mute', False)):
            measured.append({}); continue
        values = {}
        for field, names in SURFACE_INPUTS.items():
            socket = next((node.inputs.get(name) for name in names if node.inputs.get(name) is not None), None)
            if socket is None: continue
            if socket.is_linked:
                linked.add(field); continue
            value = socket.default_value
            if _finite(value) and 0 <= value <= 1: values[field] = float(value)
        measured.append(values)
    values = {}
    if measured:
        for field in SURFACE_INPUTS:
            if (field not in linked and all(field in row for row in measured)
                    and all(abs(row[field] - measured[0][field]) <= 1e-8 for row in measured[1:])):
                values[field] = measured[0][field]
    return {'surface_values': values, 'linked_surface_fields': sorted(linked)}


def _bounds(points):
    return {'min': [min(point[axis] for point in points) for axis in range(3)],
            'max': [max(point[axis] for point in points) for axis in range(3)]}


def _digest(vertices, triangles):
    payload = json.dumps([vertices, triangles], separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _components(vertices, triangles):
    """Connectivity from actual shared vertices, welding only exact duplicates."""
    parents = list(range(len(vertices)))

    def find(value):
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left, right):
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left

    positions = {}
    for index, point in enumerate(vertices):
        key = tuple(point)
        if key in positions:
            union(index, positions[key])
        else:
            positions[key] = index
    for triangle in triangles:
        union(triangle[0], triangle[1]); union(triangle[0], triangle[2])
    groups = {}
    for triangle in triangles:
        group = groups.setdefault(find(triangle[0]), {'indices': set(), 'triangles': 0})
        group['indices'].update(triangle); group['triangles'] += 1
    if len(groups) > MAX_COMPONENTS:
        raise ValueError('too_many_disconnected_components')
    return [{'bounds': _bounds([vertices[index] for index in sorted(group['indices'])]),
             'triangle_count': group['triangles']} for group in groups.values()]


def _slot_map(material_names, expected, observed, single_slot_identity=False):
    """Use exact current names or unique measured aliases; never name similarity."""
    claims = {}; conflicts = set()
    for original in expected:
        indices = {index for index, name in enumerate(material_names) if name == original}
        for sample in observed:
            if sample.get('material_name') != original:
                continue
            alias = sample.get('material_render_name')
            index = sample.get('slot_index')
            if type(index) is int and (not 0 <= index < len(material_names)
                    or (isinstance(alias, str) and alias and material_names[index] != alias)):
                conflicts.add(original)
                continue
            if isinstance(alias, str) and alias:
                alias_indices = {i for i, name in enumerate(material_names) if name == alias}
                if not alias_indices:
                    conflicts.add(original)
                if type(index) is int:
                    if 0 <= index < len(material_names) and index in alias_indices:
                        indices.add(index)
                elif len(alias_indices) == 1:
                    indices.update(alias_indices)
            elif type(index) is int and 0 <= index < len(material_names):
                # The trusted ray hit proves this exact slot even when a worker
                # omits rendered material aliases.
                indices.add(index)
        claims[original] = indices
    if single_slot_identity and len(expected) == 1 and len(material_names) == 1:
        original = expected[0]
        contradiction = any(sample.get('material_name') not in (None, original)
                            and (sample.get('slot_index') == 0 or sample.get('material_render_name') == material_names[0])
                            for sample in observed)
        if not claims[original] and original not in conflicts and not contradiction:
            claims[original] = {0}
    owners = {}
    for original, indices in claims.items():
        for index in indices:
            owners.setdefault(index, set()).add(original)
    return {original: sorted(indices) if indices and original not in conflicts and all(len(owners[index]) == 1 for index in indices) else []
            for original, indices in claims.items()}


def _rank_known_objects(known, dimensions, priority):
    """Choose inspection order only; shape similarity never authorizes edits."""
    order = {name: index for index, name in enumerate(priority)}

    def shape(name):
        value = dimensions.get(name)
        if (not isinstance(value, (list, tuple)) or len(value) != 3
                or not all(_finite(item) and item >= 0 for item in value) or max(value) <= 0):
            return None
        return sorted(value)

    seed_shapes = [value for name in priority if (value := shape(name)) is not None]

    def score(name):
        value = shape(name)
        if value is None or not seed_shapes: return float('inf')
        scores = []
        for seed in seed_shapes:
            epsilon = max(max(value), max(seed)) * 1e-9
            scores.append(sum(abs(math.log((actual + epsilon) / (reference + epsilon)))
                              for actual, reference in zip(value, seed)))
        return min(scores)

    if not priority: return list(known)
    indexed = list(enumerate(known))
    indexed.sort(key=lambda item: (0, order[item[1]['name']], item[0]) if item[1]['name'] in order
                 else (1, score(item[1]['name']), item[0]))
    return [item for index, item in indexed]


def _nondegenerate(vertices, triangle):
    a, b, c = [vertices[index] for index in triangle]
    ab = [b[index] - a[index] for index in range(3)]
    ac = [c[index] - a[index] for index in range(3)]
    cross = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]]
    return any(value != 0 for value in cross)


def _request(path):
    raw = Path(path).read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('request_too_large')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid_request')
    source = Path(value.get('blend_path', '')).resolve()
    result = Path(value.get('result_path', '')).resolve()
    output = Path(value.get('output_dir', '')).resolve()
    if source.suffix.lower() != '.blend' or not source.is_file():
        raise ValueError('source_scene_missing')
    if result.parent != output or result.suffix.lower() != '.json' or source == result or output == source.parent:
        raise ValueError('inspection_output_must_be_separate')
    known = value.get('known_objects')
    if not isinstance(known, list) or not known or len(known) > 10000:
        raise ValueError('invalid_known_objects')
    names = set()
    for item in known:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'] or item['name'] in names:
            raise ValueError('invalid_known_object_identity')
        names.add(item['name'])
        materials = item.get('materials', [])
        if not isinstance(materials, list) or len(materials) > 512 or any(not isinstance(name, str) or not name for name in materials):
            raise ValueError('invalid_known_materials')
    observed = value.get('observed_slots', [])
    if not isinstance(observed, list) or len(observed) > 50000 or any(not isinstance(item, dict) for item in observed):
        raise ValueError('invalid_observed_slots')
    priority = value.get('priority_objects', [])
    if (not isinstance(priority, list) or len(priority) > 12
            or any(not isinstance(name, str) or name not in names for name in priority)
            or len(set(priority)) != len(priority)):
        raise ValueError('invalid_priority_objects')
    limits = value.get('limits', {})
    if not isinstance(limits, dict):
        raise ValueError('invalid_limits')
    max_targets = limits.get('max_targets', MAX_TARGETS)
    max_triangles = limits.get('max_triangles_per_target', MAX_TRIANGLES_PER_TARGET)
    if type(max_targets) is not int or not 1 <= max_targets <= MAX_TARGETS:
        raise ValueError('invalid_target_limit')
    if type(max_triangles) is not int or not 1 <= max_triangles <= MAX_TRIANGLES_PER_TARGET:
        raise ValueError('invalid_triangle_limit')
    value.update(blend_path=str(source), result_path=str(result), output_dir=str(output),
                 known_objects=known, observed_slots=observed, priority_objects=priority,
                 max_targets=max_targets, max_triangles=max_triangles)
    return value


def _extract(request):
    import bpy
    scene = bpy.context.scene
    # The saved renderer may use different viewport/render modifier switches.
    # These changes affect this private process only; no .blend is saved.
    for known in request['known_objects']:
        obj = scene.objects.get(known['name'])
        if obj is None or obj.type != 'MESH' or obj.hide_render:
            continue
        for modifier in obj.modifiers:
            modifier.show_viewport = modifier.show_render
            if modifier.type == 'SUBSURF':
                modifier.levels = modifier.render_levels
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    dimensions = {item['name']: list(scene.objects[item['name']].dimensions)
                  for item in request['known_objects'] if scene.objects.get(item['name']) is not None}
    ordered = _rank_known_objects(request['known_objects'], dimensions, request.get('priority_objects', []))
    targets, shapes, unresolved = [], [], []
    triangle_total = 0
    limited = False
    observations = {}
    for sample in request['observed_slots']:
        observations.setdefault(sample.get('object_name'), []).append(sample)
    for known in ordered:
        if known.get('type', 'MESH') != 'MESH':
            continue
        object_name = known['name']
        expected = list(dict.fromkeys(known.get('materials') or ['Default material']))
        obj = scene.objects.get(object_name)
        reason = None
        if obj is None or obj.type != 'MESH':
            reason = 'object_missing_or_not_mesh'
        elif obj.hide_render:
            reason = 'object_disabled_for_render'
        if reason:
            unresolved.extend({'object_name': object_name, 'material_name': original, 'reason': reason} for original in expected)
            continue
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            if len(mesh.loop_triangles) > MAX_TOTAL_TRIANGLES:
                limited = True
                unresolved.extend({'object_name': object_name, 'material_name': original, 'reason': 'object_geometry_limit'} for original in expected)
                continue
            actual_slot_count = len(evaluated.material_slots)
            material_names = [slot.material.name if slot.material else 'Default material' for slot in evaluated.material_slots]
            if not material_names:
                material_names = ['Default material']
            if any(triangle.material_index >= len(material_names) for triangle in mesh.loop_triangles):
                unresolved.extend({'object_name': object_name, 'material_name': original, 'reason': 'invalid_surface_slot_index'} for original in expected)
                continue
            samples = observations.get(object_name, [])
            single_slot_identity = actual_slot_count == 1 and all(triangle.material_index == 0 for triangle in mesh.loop_triangles)
            strict_mapped = _slot_map(material_names, expected, samples)
            mapped = _slot_map(material_names, expected, samples, single_slot_identity=single_slot_identity)
            for original in expected:
                slots = mapped[original]
                identity = {'object_name': object_name, 'material_name': original}
                if not slots:
                    unresolved.append({**identity, 'reason': 'material_slot_identity_unresolved'})
                    continue
                selected = [triangle for triangle in mesh.loop_triangles if triangle.material_index in slots]
                if not selected:
                    unresolved.append({**identity, 'reason': 'empty_surface'})
                    continue
                if len(targets) >= request['max_targets'] or len(selected) > request['max_triangles'] or triangle_total + len(selected) > MAX_TOTAL_TRIANGLES:
                    limited = True
                    unresolved.append({**identity, 'reason': 'catalog_geometry_limit'})
                    continue
                used = sorted({index for triangle in selected for index in triangle.vertices})
                remap = {old: new for new, old in enumerate(used)}
                vertices = [list(evaluated.matrix_world @ mesh.vertices[index].co) for index in used]
                if any(not all(_finite(value) for value in point) for point in vertices):
                    unresolved.append({**identity, 'reason': 'nonfinite_geometry'})
                    continue
                triangles = [[remap[index] for index in triangle.vertices] for triangle in selected]
                # Degenerate polygons cannot provide surface evidence.  One
                # empty imported part must not cancel other valid wheel parts.
                triangles = [triangle for triangle in triangles if _nondegenerate(vertices, triangle)]
                if not triangles:
                    unresolved.append({**identity, 'reason': 'degenerate_surface'})
                    continue
                kept = sorted({index for triangle in triangles for index in triangle})
                remap = {old: new for new, old in enumerate(kept)}
                vertices = [vertices[index] for index in kept]
                triangles = [[remap[index] for index in triangle] for triangle in triangles]
                try:
                    components = _components(vertices, triangles)
                except ValueError as exc:
                    limited = True
                    unresolved.append({**identity, 'reason': str(exc)})
                    continue
                target = {'id': 't' + str(len(targets) + 1), **identity, 'slot_indices': slots,
                          'bounds': _bounds(vertices), 'components': components, 'components_complete': True,
                          'triangle_count': len(triangles), 'geometry_digest': _digest(vertices, triangles)}
                target.update(_surface_measurement([
                    evaluated.material_slots[index].material if index < actual_slot_count else None
                    for index in slots]))
                target['mapping_basis'] = ('single_slot_identity' if not strict_mapped[original] else
                                           'exact_material_name' if all(material_names[index] == original for index in slots) else
                                           'observed_slot_identity')
                targets.append(target); shapes.append((vertices, triangles))
                triangle_total += len(triangles)
        finally:
            evaluated.to_mesh_clear()
    return targets, shapes, unresolved, limited


def _palette():
    import bpy
    materials = []
    for index in range(24):
        value = .12 + index / 23 * .58
        material = bpy.data.materials.new('PartCatalogShade%02d' % index)
        material.use_nodes = True
        material.node_tree.nodes.clear()
        shader = material.node_tree.nodes.new('ShaderNodeEmission')
        shader.inputs['Color'].default_value = (value, value * 1.025, value * 1.05, 1)
        shader.inputs['Strength'].default_value = 1
        output = material.node_tree.nodes.new('ShaderNodeOutputMaterial')
        material.node_tree.links.new(shader.outputs['Emission'], output.inputs['Surface'])
        materials.append(material)
    return materials


def _view_mesh(vertices, triangles, direction, rect, name, collection, palette):
    import bpy
    from mathutils import Vector
    forward = Vector(direction).normalized()
    right = forward.cross(Vector((0, 0, 1))).normalized()
    up = right.cross(forward).normalized()
    center = Vector([sum(point[axis] for point in vertices) / len(vertices) for axis in range(3)])
    transformed = []
    for point in vertices:
        position = Vector(point) - center
        transformed.append((position.dot(right), position.dot(up), position.dot(forward)))
    low = [min(point[axis] for point in transformed) for axis in range(3)]
    high = [max(point[axis] for point in transformed) for axis in range(3)]
    span = max(high[axis] - low[axis] for axis in range(3))
    if span <= 0:
        raise ValueError('zero_extent_geometry')
    x, y, width, height = rect
    scale = min((width - 12) / max(high[0] - low[0], span * 1e-8),
                (height - 12) / max(high[1] - low[1], span * 1e-8)) / 100
    midpoint = [(low[axis] + high[axis]) * .5 for axis in range(3)]
    offsets = ((x + width / 2) / 100, (PAGE_HEIGHT - y - height / 2) / 100, 0)
    placed = [tuple((point[axis] - midpoint[axis]) * scale + offsets[axis] for axis in range(3)) for point in transformed]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(placed, [], triangles); mesh.update()
    obj = bpy.data.objects.new(name, mesh); collection.objects.link(obj)
    for material in palette:
        mesh.materials.append(material)
    light = Vector((-0.45, .65, .65)).normalized()
    for polygon in mesh.polygons:
        # Use both sides of a real surface, allowing a tire's back face to be
        # inspected even when an imported model has inconsistent normals.
        amount = abs(polygon.normal.dot(light))
        polygon.material_index = min(len(palette) - 1, int(amount * (len(palette) - 1)))


def _render_catalog(request, targets, shapes):
    import bpy
    palette = _palette()
    pages = []
    for page_number, start in enumerate(range(0, len(targets), TARGETS_PER_PAGE)):
        scene = bpy.data.scenes.new('PartCatalogPage%d' % page_number)
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'CPU'; scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.render.resolution_x = PAGE_WIDTH; scene.render.resolution_y = PAGE_HEIGHT
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGB'
        scene.render.film_transparent = False
        scene.render.use_file_extension = True
        scene.render.threads_mode = 'FIXED'; scene.render.threads = 4
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.exposure = 0; scene.view_settings.gamma = 1
        world = bpy.data.worlds.new('PartCatalogWhite%d' % page_number); world.use_nodes = True
        world.node_tree.nodes['Background'].inputs['Color'].default_value = (1, 1, 1, 1)
        world.node_tree.nodes['Background'].inputs['Strength'].default_value = 1
        scene.world = world
        camera_data = bpy.data.cameras.new('PartCatalogCamera%d' % page_number)
        camera_data.type = 'ORTHO'; camera_data.sensor_fit = 'HORIZONTAL'; camera_data.ortho_scale = PAGE_WIDTH / 100
        camera_data.clip_start = .01; camera_data.clip_end = 1000
        camera = bpy.data.objects.new('PartCatalogCamera%d' % page_number, camera_data)
        scene.collection.objects.link(camera); camera.location = (PAGE_WIDTH / 200, PAGE_HEIGHT / 200, 100)
        camera.rotation_euler = (0, 0, 0); scene.camera = camera
        for offset, (target, shape) in enumerate(zip(targets[start:start + TARGETS_PER_PAGE], shapes[start:start + TARGETS_PER_PAGE])):
            x, y = (offset % 8) * TILE_WIDTH, (offset // 8) * TILE_HEIGHT
            views = []
            for angle, (direction, top) in enumerate((((3, -4, 3), y + 26), ((-3, 4, 3), y + 132))):
                rect = (x + 4, top, TILE_WIDTH - 8, 100)
                _view_mesh(shape[0], shape[1], direction, rect, target['id'] + '_view' + str(angle), scene.collection, palette)
                views.append({'rect': [rect[0] / PAGE_WIDTH, rect[1] / PAGE_HEIGHT, rect[2] / PAGE_WIDTH, rect[3] / PAGE_HEIGHT],
                              'direction': list(direction)})
            target['thumbnail'] = {'page': page_number,
                                   'rect': [x / PAGE_WIDTH, y / PAGE_HEIGHT, TILE_WIDTH / PAGE_WIDTH, TILE_HEIGHT / PAGE_HEIGHT],
                                   'views': views}
        destination = Path(request['output_dir']) / ('parts_%02d.png' % page_number)
        scene.render.filepath = str(destination)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        pages.append({'path': str(destination), 'width': PAGE_WIDTH, 'height': PAGE_HEIGHT})
        for obj in list(scene.objects):
            data = obj.data; kind = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if kind == 'MESH': bpy.data.meshes.remove(data)
                elif kind == 'CAMERA': bpy.data.cameras.remove(data)
        bpy.data.scenes.remove(scene)
    return pages


def inspect(request):
    import bpy
    source = Path(request['blend_path'])
    before = source.stat()
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    targets, shapes, unresolved, truncated = _extract(request)
    Path(request['output_dir']).mkdir(parents=True, exist_ok=True)
    pages = _render_catalog(request, targets, shapes) if targets else []
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('source_changed_during_inspection')
    return {'success': True, 'method': 'scene_part_catalog', 'generation_version': 1,
            'targets': targets, 'pages': pages, 'unresolved': unresolved, 'truncated': truncated,
            'coverage': 'isolated_evaluated_surfaces', 'source_unchanged': True,
            'geometry_limits': {'max_targets': request['max_targets'], 'max_triangles_per_target': request['max_triangles'],
                                'max_total_triangles': MAX_TOTAL_TRIANGLES}}


def main():
    request = None
    try:
        args = sys.argv[sys.argv.index('--') + 1:]
        if len(args) != 1:
            raise ValueError('one_request_path_required')
        request = _request(args[0])
        result = inspect(request)
        Path(request['result_path']).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    except Exception as exc:
        if request:
            Path(request['output_dir']).mkdir(parents=True, exist_ok=True)
            Path(request['result_path']).write_text(json.dumps({'success': False, 'error': str(exc)[:500]}, ensure_ascii=False), encoding='utf-8')
        print('PART_INSPECTION_ERROR:', str(exc), flush=True)
        raise


if __name__ == '__main__':
    main()

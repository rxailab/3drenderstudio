"""Export an existing render snapshot; never rebuild a scene or execute its scripts.

blender -b --factory-startup --disable-autoexec --python-exit-code 1 \
    --python blender/export_scene.py -- request.json

The .blend is the authoritative editable project. scene.json is a static,
best-effort interchange representation for the Cinema 4D converter.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import traceback

import bpy


GEOMETRY_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)


def unique_warnings(items):
    return list(dict.fromkeys(items))


def file_digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def value(node, names, default):
    for name in names if isinstance(names, tuple) else (names,):
        if node and name in node.inputs:
            result = node.inputs[name].default_value
            return list(result) if hasattr(result, '__len__') else result
    return default


def socket(node, names):
    for name in names if isinstance(names, tuple) else (names,):
        if node and name in node.inputs:
            return node.inputs[name]
    return None


def upstream(input_socket):
    """Bounded traversal, including reusable node groups without evaluating code."""
    found, visited, pending = [], set(), []
    if input_socket:
        pending.extend(link.from_node for link in input_socket.links)
    while pending and len(visited) < 128:
        node = pending.pop()
        key = node.as_pointer()
        if key in visited:
            continue
        visited.add(key)
        found.append(node)
        pending.extend(link.from_node for item in node.inputs for link in item.links)
        if node.type == 'GROUP' and node.node_tree:
            pending.extend(n for n in node.node_tree.nodes if n.type == 'TEX_IMAGE')
    return found


class TextureWriter:
    def __init__(self, output_dir):
        self.root = output_dir
        self.cache = {}

    def write(self, image, warnings):
        if image.name in self.cache:
            return self.cache[image.name]
        if image.source in {'MOVIE', 'SEQUENCE', 'TILED', 'VIEWER'}:
            warnings.append(f'Texture {image.name}: {image.source} cannot be represented as a single static texture.')
            return None
        suffixes = {'PNG': '.png', 'JPEG': '.jpg', 'JPEG2000': '.jp2',
                    'TARGA': '.tga', 'TARGA_RAW': '.tga', 'TIFF': '.tif',
                    'OPEN_EXR': '.exr', 'OPEN_EXR_MULTILAYER': '.exr',
                    'HDR': '.hdr', 'BMP': '.bmp', 'WEBP': '.webp'}
        source = Path(bpy.path.abspath(image.filepath, library=image.library)) if image.filepath else None
        suffix = suffixes.get(image.file_format, source.suffix.lower() if source else '.png')
        if not suffix or not re.fullmatch(r'\.[a-zA-Z0-9]{1,8}', suffix):
            suffix = '.png'
        name = re.sub(r'[^A-Za-z0-9_-]', '_', Path(image.name).stem)[:60] or 'texture'
        name += '_' + hashlib.sha256(image.name.encode('utf-8')).hexdigest()[:10] + suffix
        target = self.root / 'tex' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if image.packed_file and not image.is_dirty:
                target.write_bytes(bytes(image.packed_file.data))
            elif source and source.is_file() and not image.is_dirty:
                shutil.copyfile(source, target)
            elif image.has_data:
                # Image.save preserves texture pixels; save_render would apply the
                # scene view transform and is deliberately not used here.
                original_path, original_format = image.filepath_raw, image.file_format
                try:
                    image.filepath_raw = str(target)
                    if image.file_format not in suffixes:
                        image.file_format = 'PNG'
                    image.save()
                finally:
                    image.filepath_raw, image.file_format = original_path, original_format
            else:
                raise ValueError('image pixels and source file are unavailable')
            if not target.is_file() or target.stat().st_size == 0:
                raise ValueError('texture file is empty')
            result = {'path': target.relative_to(self.root).as_posix(),
                      'colorspace': image.colorspace_settings.name}
            self.cache[image.name] = result
            return result
        except Exception as exc:
            warnings.append(f'Texture {image.name}: export failed: {exc}')
            return None


def texture_channel(input_socket, writer, warnings, channel):
    if not input_socket or not input_socket.is_linked:
        return None
    nodes = upstream(input_socket)
    image_nodes = [n for n in nodes if n.type == 'TEX_IMAGE' and n.image]
    if not image_nodes:
        warnings.append(f'{channel}: procedural/linked nodes use an approximate constant in interchange; original nodes remain in scene.blend.')
        return None
    if len(image_nodes) != 1:
        warnings.append(f'{channel}: multiple image textures are mixed; interchange exports the first image only.')
    permitted = {'TEX_IMAGE', 'REROUTE', 'NORMAL_MAP', 'TEX_COORD', 'UVMAP'}
    complex_types = sorted({n.type for n in nodes if n.type not in permitted})
    if complex_types:
        warnings.append(f'{channel}: node operations {", ".join(complex_types)} are not reproduced in interchange.')
    tex_node = image_nodes[0]
    texture = writer.write(tex_node.image, warnings)
    if texture:
        texture = dict(texture, projection=tex_node.projection, extension=tex_node.extension)
        if input_socket.links and input_socket.links[0].from_node == tex_node:
            texture['output'] = input_socket.links[0].from_socket.name
        if tex_node.projection != 'FLAT':
            warnings.append(f'{channel}: {tex_node.projection} projection requires manual adjustment in Cinema 4D.')
        normal_nodes = [n for n in nodes if n.type == 'NORMAL_MAP']
        if normal_nodes:
            texture['normal_space'] = normal_nodes[0].space
            texture['strength'] = float(value(normal_nodes[0], 'Strength', 1.0))
        return texture
    return None


def material_record(mat, writer):
    warnings = []
    output = next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None) if mat.use_nodes else None
    connected = upstream(socket(output, 'Surface'))
    principals = [n for n in connected if n.type == 'BSDF_PRINCIPLED']
    node = principals[0] if principals else None
    if mat.use_nodes and not node:
        warnings.append('No connected Principled BSDF: interchange material uses viewport defaults.')
    if len(principals) > 1 or any(n.type in {'MIX_SHADER', 'ADD_SHADER', 'GROUP'} for n in connected):
        warnings.append('Combined/grouped shaders are approximated; full shader graph remains in scene.blend.')
    record = {'name': mat.name,
              'base_color': value(node, 'Base Color', list(mat.diffuse_color)),
              'roughness': float(value(node, 'Roughness', mat.roughness)),
              'metallic': float(value(node, 'Metallic', mat.metallic)),
              'transmission': float(value(node, ('Transmission Weight', 'Transmission'), 0.0)),
              'ior': float(value(node, 'IOR', 1.45)),
              'emission': value(node, ('Emission Color', 'Emission'), [0, 0, 0, 1]),
              'emission_strength': float(value(node, 'Emission Strength', 0.0)),
              'alpha': float(value(node, 'Alpha', mat.diffuse_color[3])),
              'textures': {}, 'warnings': warnings}
    for channel, names in {'base_color': 'Base Color', 'roughness': 'Roughness',
                           'metallic': 'Metallic', 'transmission': ('Transmission Weight', 'Transmission'),
                           'emission': ('Emission Color', 'Emission'), 'alpha': 'Alpha',
                           'normal': 'Normal'}.items():
        texture = texture_channel(socket(node, names), writer, warnings, channel)
        if texture:
            record['textures'][channel] = texture
    for channel in ('Displacement', 'Volume'):
        if socket(output, channel) and socket(output, channel).is_linked:
            warnings.append(f'{channel} shader is not converted; edit it in the original Blender project.')
    if node:
        for channel in ('Coat Weight', 'Anisotropic IOR Level', 'Sheen Weight', 'Subsurface Weight'):
            current = socket(node, channel)
            if current and (current.is_linked or abs(float(current.default_value)) > 1e-6):
                warnings.append(f'{channel} is not reproduced in the basic interchange material.')
    record['warnings'] = unique_warnings(warnings)
    return record


def matrix_record(matrix, scale):
    result = [list(row) for row in matrix]
    for index in range(3):
        result[index][3] *= scale
    return result


def mesh_record(evaluated, depsgraph, scale):
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    if mesh is None:
        return None, []
    try:
        mesh.calc_loop_triangles()
        # Keep editable quads; Blender's tessellator safely handles concave n-gons.
        triangles = {}
        for tri in mesh.loop_triangles:
            triangles.setdefault(tri.polygon_index, []).append(list(tri.loops))
        uv = mesh.uv_layers.active.data if mesh.uv_layers.active else None
        faces, indices, uvs, normals, smooth = [], [], [], [], []
        for poly in mesh.polygons:
            groups = [list(poly.loop_indices)] if len(poly.vertices) <= 4 else triangles[poly.index]
            for loops in groups:
                faces.append([mesh.loops[i].vertex_index for i in loops])
                indices.append(poly.material_index)
                smooth.append(poly.use_smooth)
                if uv is not None:
                    uvs.append([list(uv[i].uv) for i in loops])
                normals.append([list(mesh.corner_normals[i].vector) for i in loops])
        materials = list(mesh.materials)
        # Object-linked slots override the material on the shared mesh datablock.
        for index, slot in enumerate(evaluated.material_slots):
            if index < len(materials) and slot.link == 'OBJECT':
                materials[index] = slot.material
        used_indices = set(indices)
        materials = [mat if index in used_indices else None for index, mat in enumerate(materials)]
        return {'vertices': [[float(c) * scale for c in v.co] for v in mesh.vertices],
                'faces': faces, 'material_indices': indices,
                'materials': [m.name if m else None for m in materials],
                'uvs': uvs, 'normals': normals, 'smooth': smooth}, materials
    finally:
        evaluated.to_mesh_clear()


def world_record(world, writer, warnings):
    record = {'name': world.name if world else None, 'color': list(world.color) if world else [0, 0, 0],
              'strength': 1.0, 'textures': {}}
    if world and world.use_nodes:
        output = next((n for n in world.node_tree.nodes if n.type == 'OUTPUT_WORLD' and n.is_active_output), None)
        nodes = upstream(socket(output, 'Surface'))
        background = next((n for n in nodes if n.type == 'BACKGROUND'), None)
        if background:
            record['color'] = value(background, 'Color', record['color'])
            record['strength'] = float(value(background, 'Strength', 1.0))
            image_nodes = [n for n in upstream(socket(background, 'Color')) if n.type in {'TEX_ENVIRONMENT', 'TEX_IMAGE'} and n.image]
            if image_nodes:
                tex = writer.write(image_nodes[0].image, warnings)
                if tex:
                    record['textures']['color'] = tex
                warnings.append('World environment texture mapping and lighting need review in Cinema 4D.')
            elif socket(background, 'Color').is_linked:
                warnings.append('Procedural world shader is approximated by a constant in interchange.')
    return record


def stored_metadata(scene, key, fallback):
    try:
        result = json.loads(scene.get(key, '{}'))
        return result if isinstance(result, dict) else fallback
    except (ValueError, TypeError):
        return fallback


def render_visible_names(layer_collection, parent_visible=True):
    visible = parent_visible and not layer_collection.exclude and not layer_collection.collection.hide_render
    names = {obj.name for obj in layer_collection.collection.objects} if visible else set()
    for child in layer_collection.children:
        names.update(render_visible_names(child, visible))
    return names


def scene_record(output_dir, request, warnings):
    scene = bpy.context.scene
    scale = float(scene.unit_settings.scale_length) or 1.0
    writer = TextureWriter(output_dir)
    # Only change this in memory after the original packed project was saved.
    # A viewport depsgraph otherwise ignores modifiers enabled only for render.
    for obj in scene.objects:
        for modifier in obj.modifiers:
            modifier.show_viewport = modifier.show_render
            if modifier.type == 'SUBSURF':
                modifier.levels = modifier.render_levels
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    visible_names = render_visible_names(bpy.context.view_layer.layer_collection)
    if sum(layer.use for layer in scene.view_layers) > 1:
        warnings.append('Interchange uses the active view layer; multiple render-layer compositing remains in scene.blend.')
    if bpy.context.view_layer.material_override:
        warnings.append('View-layer material override remains in scene.blend; interchange lists object materials.')
    materials, records = {}, []
    for obj in scene.objects:
        evaluated = obj.evaluated_get(depsgraph)
        record = {'name': obj.name, 'type': obj.type,
                  'parent': obj.parent.name if obj.parent else None,
                  'matrix_world': matrix_record(evaluated.matrix_world, scale),
                  'hide_render': bool(obj.hide_render or obj.name not in visible_names),
                  'hide_viewport': bool(obj.hide_viewport)}
        if obj.type in GEOMETRY_TYPES:
            mesh, used = mesh_record(evaluated, depsgraph, scale)
            if mesh is not None:
                record['type'] = 'MESH'
                record['source_type'] = obj.type
                record['mesh'] = mesh
                materials.update({m.name: m for m in used if m})
        elif obj.type == 'CAMERA':
            cam = evaluated.data
            focus_distance = cam.dof.focus_distance
            if cam.dof.focus_object:
                focus_distance = (evaluated.matrix_world.translation - cam.dof.focus_object.evaluated_get(depsgraph).matrix_world.translation).length
            record['camera'] = {'type': cam.type, 'lens': cam.lens, 'sensor_width': cam.sensor_width,
                                'sensor_height': cam.sensor_height, 'sensor_fit': cam.sensor_fit,
                                'clip_start': cam.clip_start * scale, 'clip_end': cam.clip_end * scale,
                                'focus_distance': focus_distance * scale, 'fstop': cam.dof.aperture_fstop,
                                'use_dof': cam.dof.use_dof, 'shift_x': cam.shift_x, 'shift_y': cam.shift_y,
                                'ortho_scale': cam.ortho_scale * scale}
        elif obj.type == 'LIGHT':
            light = evaluated.data
            record['light'] = {'type': light.type, 'energy': light.energy, 'color': list(light.color),
                               'size': getattr(light, 'size', 0.0) * scale,
                               'size_y': getattr(light, 'size_y', 0.0) * scale,
                               'shape': getattr(light, 'shape', None),
                               'spot_size': getattr(light, 'spot_size', 0.0),
                               'spot_blend': getattr(light, 'spot_blend', 0.0),
                               'angle': getattr(light, 'angle', 0.0),
                               'shadow_soft_size': getattr(light, 'shadow_soft_size', 0.0) * scale,
                               'use_shadow': getattr(light, 'use_shadow', True)}
        elif obj.type not in {'EMPTY', 'ARMATURE'}:
            warnings.append(f'{obj.name}: object type {obj.type} is exported as a transform only.')
        if obj.animation_data or obj.constraints or obj.type == 'ARMATURE':
            warnings.append('Interchange contains the current static pose; animation, constraints and rigs remain editable in scene.blend.')
        if obj.instance_type != 'NONE' or (obj.type == 'MESH' and any(m.type == 'NODES' for m in obj.modifiers)):
            warnings.append(f'{obj.name}: instanced geometry may require realization in Blender before cross-application editing.')
        records.append(record)
    settings = scene.view_settings
    render = scene.render
    result = {'schema_version': 1, 'units': 'meters', 'axis': 'blender_z_up',
              'objects': records, 'materials': [material_record(m, writer) for m in materials.values()],
              'render': {'width': render.resolution_x, 'height': render.resolution_y,
                         'resolution_percentage': render.resolution_percentage,
                         'pixel_aspect': [render.pixel_aspect_x, render.pixel_aspect_y],
                         'fps': render.fps / render.fps_base, 'frame': scene.frame_current,
                         'engine': render.engine, 'active_camera': scene.camera.name if scene.camera else None,
                         'color_management': {'display_device': scene.display_settings.display_device,
                                              'view_transform': settings.view_transform, 'look': settings.look,
                                              'exposure': settings.exposure, 'gamma': settings.gamma}},
              'world': world_record(scene.world, writer, warnings),
              'plan': stored_metadata(scene, 'studio_plan', request.get('plan', {})),
              'shot': stored_metadata(scene, 'studio_shot', request.get('shot', {})),
              'warnings': []}
    for material in result['materials']:
        warnings.extend(f'{material["name"]}: {warning}' for warning in material['warnings'])
    warnings.append('Cinema 4D uses different material, light and color-management systems; visual parity requires review. The packed Blender file preserves the original render setup.')
    result['warnings'] = unique_warnings(warnings)
    return result


def fingerprint():
    scene = bpy.context.scene
    return {'objects': sorted((o.name, o.type, o.parent.name if o.parent else None) for o in scene.objects),
            'geometry': sorted((o.name, len(o.data.vertices), len(o.data.polygons)) for o in scene.objects if o.type == 'MESH'),
            'camera': scene.camera.name if scene.camera else None,
            'lights': sorted(o.name for o in scene.objects if o.type == 'LIGHT'),
            'camera_settings': {o.name: {'lens': o.data.lens, 'type': o.data.type,
                                        'sensor_width': o.data.sensor_width, 'clip_start': o.data.clip_start,
                                        'clip_end': o.data.clip_end, 'shift_x': o.data.shift_x,
                                        'shift_y': o.data.shift_y, 'fstop': o.data.dof.aperture_fstop,
                                        'focus_distance': o.data.dof.focus_distance}
                                for o in scene.objects if o.type == 'CAMERA'},
            'light_settings': {o.name: {'type': o.data.type, 'energy': o.data.energy,
                                       'color': list(o.data.color), 'size': getattr(o.data, 'size', None),
                                       'size_y': getattr(o.data, 'size_y', None)}
                               for o in scene.objects if o.type == 'LIGHT'},
            'frame': scene.frame_current,
            'transforms': {o.name: [list(row) for row in o.matrix_world] for o in scene.objects}}


def export(request):
    source = Path(request['blend_path']).resolve(strict=True)
    output_dir = Path(request['output_dir']).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / 'scene.blend'
    if source == destination.resolve():
        raise ValueError('Output scene.blend must be different from the original source project.')
    if source.suffix.lower() != '.blend':
        raise ValueError('blend_path must point to an existing .blend project.')
    source_hash = file_digest(source)
    warnings = []
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    before = fingerprint()
    if not any(obj.type in GEOMETRY_TYPES for obj in bpy.context.scene.objects):
        raise ValueError('Source project has no exportable geometry.')
    if not before['camera']:
        raise ValueError('Source project has no active render camera.')
    if not before['lights']:
        warnings.append('Source project has no light objects; it may use world or emissive lighting.')
    # Pack while source-relative paths still point to the original location.
    try:
        bpy.ops.file.pack_all()
        if bpy.data.libraries:
            bpy.ops.file.pack_libraries()
    except RuntimeError as exc:
        warnings.append(f'Some external assets could not be packed: {exc}')
    for image in bpy.data.images:
        if image.source == 'FILE' and not image.packed_file:
            warnings.append(f'Texture {image.name} could not be packed; missing external assets cannot be restored automatically.')
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(destination), check_existing=False, relative_remap=True)
    if not destination.is_file() or destination.stat().st_size < 100:
        raise RuntimeError('Blender did not write a valid project file.')
    bpy.ops.wm.open_mainfile(filepath=str(destination), load_ui=False, use_scripts=False)
    after = fingerprint()
    if before != after:
        raise RuntimeError('Saved Blender project failed object, geometry, camera, light, frame or transform verification.')
    if file_digest(source) != source_hash:
        raise RuntimeError('Original source Blender file changed during export.')
    data = scene_record(output_dir, request, warnings)
    scene_path = output_dir / 'scene.json'
    files = {'blend': destination.name, 'scene': scene_path.name}
    # FBX is a secondary editable fallback; failure must not discard the native
    # verified project or our fully documented scene interchange.
    if request.get('export_fbx', True):
        try:
            fbx_path = output_dir / 'scene.fbx'
            bpy.ops.export_scene.fbx(filepath=str(fbx_path), check_existing=False,
                                     use_selection=False, object_types={'MESH', 'EMPTY', 'CAMERA', 'LIGHT', 'ARMATURE', 'OTHER'},
                                     use_mesh_modifiers=True, bake_anim=False, add_leaf_bones=False,
                                     path_mode='COPY', embed_textures=True, axis_forward='-Z', axis_up='Y')
            if not fbx_path.is_file() or fbx_path.stat().st_size == 0:
                raise RuntimeError('FBX exporter produced no file')
            files['fbx'] = fbx_path.name
        except Exception as exc:
            warnings.append(f'Optional FBX export unavailable: {exc}')
    data['warnings'] = unique_warnings(data['warnings'] + warnings)
    atomic_json(scene_path, data)
    counts = {'objects': len(data['objects']),
              'meshes': sum('mesh' in obj for obj in data['objects']),
              'cameras': sum('camera' in obj for obj in data['objects']),
              'lights': sum('light' in obj for obj in data['objects']),
              'materials': len(data['materials'])}
    return {'success': True, 'files': files, 'counts': counts,
            'warnings': data['warnings'], 'blender_version': bpy.app.version_string,
            'verified': {'blend_reopened': True, 'source_unchanged': True,
                         'source_sha256': source_hash}}


def main():
    request = None
    try:
        args = sys.argv[sys.argv.index('--') + 1:]
        if len(args) != 1:
            raise ValueError('Expected one request.json path after --')
        request = json.loads(Path(args[0]).read_text(encoding='utf-8-sig'))
        result = export(request)
        atomic_json(Path(request['output_dir']) / 'result.json', result)
        print('EXPORT_RESULT:' + json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        result = {'success': False, 'error': str(exc), 'files': {}, 'warnings': [],
                  'blender_version': bpy.app.version_string}
        if request and request.get('output_dir'):
            output = Path(request['output_dir'])
            output.mkdir(parents=True, exist_ok=True)
            atomic_json(output / 'result.json', result)
        print('EXPORT_RESULT:' + json.dumps(result, ensure_ascii=False), flush=True)
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()

"""Audit saved automatic material trials in Blender; never render or save a scene.

blender --background --factory-startup --disable-autoexec --python
validation/verify_auto_material_scene.py -- --task TASK_ID
Use --project PROJECT_ID instead to audit its latest completed automatic trial.
Audits hero (or the first shot); use --shot SHOT_ID or --all-shots to change scope.
Only validation/auto-material-scene-results.json is written; SQLite uses mode=ro.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import bpy

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data'
REPORT = ROOT/'validation/auto-material-scene-results.json'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return [plain(v) for v in value]
    except TypeError:
        return str(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def image_signature(image):
    packed = image.packed_file
    return {'size': list(image.size), 'color_space': image.colorspace_settings.name,
            'packed': bool(packed), 'sha256': hashlib.sha256(packed.data).hexdigest() if packed else None}


def socket_signature(socket):
    return {'default': plain(getattr(socket, 'default_value', None)),
            'links': [(link.from_node.name, link.from_socket.identifier) for link in socket.links]}


def node_signature(node):
    value = {'name': node.name, 'type': node.type,
             'inputs': [(s.identifier, socket_signature(s)) for s in node.inputs]}
    for key in ('operation', 'blend_type', 'projection', 'projection_blend', 'interpolation', 'extension', 'uv_map', 'space'):
        if hasattr(node, key):
            value[key] = plain(getattr(node, key))
    if node.type == 'TEX_IMAGE' and node.image:
        value['image'] = image_signature(node.image)
    return value


def upstream(socket):
    found = {}
    def visit(node):
        if node.name in found:
            return
        found[node.name] = node
        for value in node.inputs:
            for link in value.links:
                visit(link.from_node)
    for link in socket.links:
        visit(link.from_node)
    return list(found.values())


def branch(socket):
    return {'socket': socket_signature(socket),
            'nodes': sorted((node_signature(node) for node in upstream(socket)), key=lambda n: n['name'])}


def material_signature(material):
    if material is None:
        return None
    return {'diffuse_color': list(material.diffuse_color), 'metallic': material.metallic,
            'roughness': material.roughness, 'use_nodes': material.use_nodes,
            'nodes': sorted((node_signature(n) for n in material.node_tree.nodes), key=lambda n: n['name']) if material.use_nodes else []}


def shader(material):
    require(material and material.use_nodes, 'Target has no shader nodes')
    output = next((n for n in material.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
    require(output and output.inputs['Surface'].is_linked, 'Target surface is not connected')
    result = output.inputs['Surface'].links[0].from_node
    require(result.type == 'BSDF_PRINCIPLED', 'Audit supports a directly connected Principled shader')
    return result


def product_snapshot(names):
    result = {}
    for name in names:
        ob = bpy.data.objects.get(name)
        require(ob is not None, 'Missing product object '+name)
        value = {'type': ob.type, 'matrix_world': plain(ob.matrix_world),
                 'materials': [material_signature(slot.material) for slot in ob.material_slots]}
        if ob.type == 'MESH':
            value['geometry_sha256'] = digest({'vertices': [list(v.co) for v in ob.data.vertices],
                'polygons': [list(p.vertices) for p in ob.data.polygons],
                'material_indices': [p.material_index for p in ob.data.polygons],
                'uv_layers': [[list(v.uv) for v in layer.data] for layer in ob.data.uv_layers]})
        result[name] = value
    return result


def photography():
    scene = bpy.context.scene
    camera = scene.camera
    require(camera is not None, 'Scene camera missing')
    return {'camera': {'matrix_world': plain(camera.matrix_world), 'lens': camera.data.lens,
        'shift': [camera.data.shift_x, camera.data.shift_y], 'type': camera.data.type,
        'ortho_scale': camera.data.ortho_scale, 'dof': camera.data.dof.use_dof,
        'focus_distance': camera.data.dof.focus_distance, 'fstop': camera.data.dof.aperture_fstop},
        'lights': {ob.name: {'matrix_world': plain(ob.matrix_world), 'type': ob.data.type,
            'energy': ob.data.energy, 'color': list(ob.data.color), 'size': getattr(ob.data, 'size', None),
            'size_y': getattr(ob.data, 'size_y', None)} for ob in scene.objects if ob.type == 'LIGHT'},
        'exposure': scene.view_settings.exposure, 'view_transform': scene.view_settings.view_transform,
        'look': scene.view_settings.look, 'gamma': scene.view_settings.gamma}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--task')
    scope.add_argument('--project')
    shots = parser.add_mutually_exclusive_group()
    shots.add_argument('--shot')
    shots.add_argument('--all-shots', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    with sqlite3.connect((DATA/'studio.sqlite').as_uri()+'?mode=ro', uri=True) as connection:
        def record(kind, ident):
            row = connection.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, ident)).fetchone()
            require(row is not None, 'Missing '+kind+' '+ident)
            return json.loads(row[0])
        if args.task:
            task = record('material_task', args.task)
        else:
            tasks = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM records WHERE kind=?', ('material_task',))]
            candidates = [t for t in tasks if t['project_id'] == args.project and t.get('origin') == 'automatic' and t.get('job_id') and t['status'] == 'completed']
            require(candidates, 'Project has no completed automatic material trial')
            task = max(candidates, key=lambda t: t.get('created_at', 0))
        project = record('project', task['project_id'])
        job = record('job', task.get('job_id'))
    require(task.get('origin') == 'automatic' and task['status'] == 'completed' and job['status'] == 'completed', 'Automatic trial has not completed')
    require(task['use_base_color'] is False, 'This preservation audit requires original base color')
    require(task.get('downloaded_files') and set(task['downloaded_files']) <= {'roughness', 'normal'}, 'Expected only roughness/normal surface maps')

    def local_file(url):
        route = unquote(urlsplit(url).path)
        require(route.startswith('/files/'), 'Expected local artifact URL')
        path = (DATA/route[7:]).resolve()
        require(path.is_relative_to((DATA/project['id']).resolve()) and path.is_file(), 'Missing or out-of-project artifact')
        return path

    targets = {(t['object_name'], t['material_name']) for t in task['targets']}
    names = [item['name'] for item in project['analysis']['objects']]
    shot_ids = job['shot_ids'] if args.all_shots else [args.shot or ('hero' if 'hero' in job['shot_ids'] else job['shot_ids'][0])]
    require(all(shot in job['shot_ids'] for shot in shot_ids), 'Requested shot is not part of the actual trial')
    checks = []
    for shot in shot_ids:
        baseline = next(o for o in task['baseline_outputs'] if o['shot_id'] == shot)
        candidates = [o for o in project['outputs'] if o['job_id'] == job['id'] and o['shot_id'] == shot and not o.get('review_candidate')]
        require(candidates, 'Missing actual trial output '+shot)
        candidate = max(candidates, key=lambda o: (o['version'], o.get('created_at', 0)))
        before_file, after_file = local_file(baseline['blend_url']), local_file(candidate['blend_url'])
        bpy.ops.wm.open_mainfile(filepath=str(before_file), load_ui=False, use_scripts=False)
        before, before_photo = product_snapshot(names), photography()
        slots = {}
        for object_name, material_name in sorted(targets):
            ob = bpy.data.objects[object_name]
            selected = [(i, slot.material) for i, slot in enumerate(ob.material_slots) if slot.material and slot.material.get('_studio_source_material', slot.material.name) == material_name]
            require(selected, 'No target material slot '+object_name+'/'+material_name)
            for index, mat in selected:
                bs = shader(mat)
                slots[(object_name, index)] = {'base_color': branch(bs.inputs['Base Color']), 'metallic': branch(bs.inputs['Metallic']),
                    'other_inputs': {s.identifier: branch(s) for s in bs.inputs if s.name not in ('Normal', 'Roughness')}}
        bpy.ops.wm.open_mainfile(filepath=str(after_file), load_ui=False, use_scripts=False)
        after, after_photo = product_snapshot(names), photography()
        require(before_photo['camera'] == after_photo['camera'], 'Actual camera changed in '+shot)
        require(all(before_photo[k] == after_photo[k] for k in ('view_transform', 'look', 'gamma')), 'Color management changed')
        light_change = before_photo['lights'] != after_photo['lights']
        exposure_change = before_photo['exposure'] != after_photo['exposure']
        adjustment = job.get('automatic_adjustments', {}).get(shot, {})
        require(not (light_change or exposure_change) or adjustment, 'Lighting/exposure changed without a recorded automatic adjustment')
        channel_checks = []
        for name in names:
            require(before[name]['matrix_world'] == after[name]['matrix_world'], 'Product transform changed '+name)
            require(before[name].get('geometry_sha256') == after[name].get('geometry_sha256'), 'Product geometry/UV changed '+name)
            require(len(before[name]['materials']) == len(after[name]['materials']), 'Material slot count changed')
            for index, original in enumerate(before[name]['materials']):
                if (name, index) not in slots:
                    require(original == after[name]['materials'][index], 'Unselected material changed '+name)
        for (name, index), original in slots.items():
            mat = bpy.data.objects[name].material_slots[index].material
            bs = shader(mat)
            require(original['base_color'] == branch(bs.inputs['Base Color']), 'Original base color graph/default changed')
            require(original['metallic'] == branch(bs.inputs['Metallic']), 'Original metallic graph/default changed')
            require(original['other_inputs'] == {s.identifier: branch(s) for s in bs.inputs if s.name not in ('Normal', 'Roughness')}, 'Unrequested shader input changed')
            provenance = json.loads(mat['provenance'])
            require(provenance['resource']['asset_id'] == task['selection']['asset_id'] and provenance['license'] == 'CC0-1.0', 'Resource/license provenance missing')
            require(provenance['preserves_original_color'] is True, 'Color preservation not recorded')
            for channel, download in task['downloaded_files'].items():
                nodes = upstream(bs.inputs[{'normal': 'Normal', 'roughness': 'Roughness'}[channel]])
                images = [n for n in nodes if n.type == 'TEX_IMAGE' and n.name == 'External '+channel]
                require(len(images) == 1 and images[0].image, 'Downloaded '+channel+' is not linked to the actual surface')
                if channel == 'normal':
                    require(any(n.type == 'NORMAL_MAP' for n in nodes), 'Normal image has no normal conversion')
                image = image_signature(images[0].image)
                require(image['packed'] and image['color_space'] == 'Non-Color', channel+' is not packed Non-Color data')
                path = Path(download['path']).resolve()
                require(path.is_relative_to((DATA/project['id']).resolve()), 'Downloaded file outside project')
                require(image['sha256'] == download['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest(), channel+' packed bytes do not match the verified download')
                require(any(m['channel'] == channel and m['applied'] for m in provenance['maps']), 'Applied channel provenance missing')
                channel_checks.append({'object': name, 'slot': index, 'channel': channel, **image})
        require(all(i.packed_file for i in bpy.data.images if i.source == 'FILE'), 'Some file images are unpacked')
        checks.append({'shot_id': shot, 'before_scene': str(before_file), 'candidate_scene': str(after_file),
            'geometry_uv_transforms_camera_unchanged': True, 'base_color_and_metallic_preserved': True,
            'base_color_was_linked': any(v['base_color']['socket']['links'] for v in slots.values()),
            'unselected_materials_unchanged': True, 'all_file_images_packed': True, 'channels': channel_checks,
            'lights_unchanged': not light_change, 'exposure_unchanged': not exposure_change,
            'recorded_automatic_adjustment': adjustment, 'before_photography': before_photo, 'candidate_photography': after_photo})
    return {'passed': True, 'checked_at': datetime.now(timezone.utc).isoformat(), 'blender_version': bpy.app.version_string,
        'project_id': project['id'], 'task_id': task['id'], 'candidate_job_id': job['id'], 'decision': task.get('decision'),
        'comparison': task.get('comparison'), 'shots': checks, 'requested_job_shots': job['shot_ids'], 'read_only_scene_audit': True,
        'limitations': ['Checks the selected actual trial scenes, not all product categories or 4K output.',
            'Verifies graph/data preservation and links; does not establish subjective realism or texture-scale quality.',
            'Candidate is audited even when automatic comparison retained the baseline.']}


try:
    report = main()
except Exception as error:
    REPORT.write_text(json.dumps({'passed': False, 'error': str(error)}, ensure_ascii=False, indent=2), encoding='utf-8')
    raise
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('AUTO_MATERIAL_SCENE_VERIFIED', report['task_id'], len(report['shots']), 'actual candidate/baseline scene pairs')

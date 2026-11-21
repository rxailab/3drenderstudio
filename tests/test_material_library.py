"""Shipped material library, its rule table and the baked-roughness gate (v40)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ai_client
import lookdev
import material_library


class LibraryFilesTests(unittest.TestCase):
    def test_manifest_lists_only_shipped_files_with_matching_hashes(self):
        items = material_library.manifest()['materials']
        self.assertEqual({item['id'] for item in items},
                         {'textured_coarse_stipple', 'reflective_tape_red', 'reflective_tape_yellowgreen'})
        for item in items:
            self.assertEqual(item['license'], 'user-supplied, internal use')
            self.assertTrue(item['tile_mm_estimate'])
            for key, digest in item['sha256'].items():
                path = material_library.map_path(item, key)
                self.assertTrue(path.is_file(), path)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        shipped = {p for p in material_library.LIBRARY.rglob('*') if p.is_file()}
        self.assertFalse([p for p in shipped if p.suffix.lower() in ('.exr', '.hdr')])
        self.assertLess(sum(p.stat().st_size for p in shipped), 4 * 1024 * 1024)

    def test_height_map_is_16_bit(self):
        path = material_library.map_path(material_library.entry('textured_coarse_stipple'), 'height')
        header = path.read_bytes()[:33]
        self.assertEqual(header[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(header[24], 16)            # IHDR bit depth

    def test_manifest_does_not_offer_grain_to_gloss(self):
        item = material_library.entry('textured_coarse_stipple')
        self.assertFalse([finish for finish in item['applies_to_finishes'] if 'gloss' in finish or 'metal' in finish])

    def test_paths_cannot_leave_the_library(self):
        self.assertIsNone(material_library.map_path({'maps': {'height': '../../app.py'}}, 'height'))


class PickTests(unittest.TestCase):
    def test_satin_plastic_gets_the_coarse_grain(self):
        self.assertEqual(material_library.pick('satin_plastic', '箱体', 400)['id'], 'textured_coarse_stipple')
        self.assertEqual(material_library.pick('matte_plastic')['id'], 'textured_coarse_stipple')

    def test_part_name_selects_grain_when_finish_is_unknown(self):
        self.assertEqual(material_library.pick('', '喷塑箱体')['id'], 'textured_coarse_stipple')
        self.assertEqual(material_library.pick(None, 'Toolbox_lid')['id'], 'textured_coarse_stipple')

    def test_rules_that_keep_the_surface_clean(self):
        self.assertIsNone(material_library.pick('satin_plastic', quality='preview'))
        self.assertIsNone(material_library.pick('satin_plastic', quality='thumbnail'))
        self.assertIsNone(material_library.pick('satin_plastic', size_mm=20))
        self.assertIsNone(material_library.pick('satin_plastic', 'Logo_plate'))
        self.assertIsNone(material_library.pick('satin_plastic', '铭牌'))
        self.assertIsNone(material_library.pick('gloss_plastic', 'toolbox'))
        self.assertIsNone(material_library.pick('brushed_metal', 'powder coat'))
        self.assertIsNone(material_library.pick('rubber', 'grip'))
        self.assertIsNone(material_library.pick('', 'body'))

    def test_decals_are_never_picked_by_finish(self):
        for finish in ('satin_plastic', 'gloss_paint', 'rubber', ''):
            picked = material_library.pick(finish, '反光条')
            self.assertTrue(picked is None or picked['kind'] == 'surface_detail')


class ValidationTests(unittest.TestCase):
    def test_surface_detail_defaults_and_limits(self):
        spec = material_library.surface_detail({'id': 'textured_coarse_stipple'})
        self.assertEqual((spec['strength'], spec['tile_mm']), (.6, 80))
        self.assertTrue(Path(spec['path']).is_file())
        self.assertEqual(material_library.surface_detail({'id': 'textured_coarse_stipple', 'strength': 1, 'tile_mm': 40})['tile_mm'], 40)
        for bad in ({'id': 'nope'}, {'id': 'reflective_tape_red'}, {'id': 'textured_coarse_stipple', 'strength': 9},
                    {'id': 'textured_coarse_stipple', 'tile_mm': True}, {'id': 'textured_coarse_stipple', 'path': '/etc/passwd'}, 'x'):
            with self.assertRaises(ValueError):
                material_library.surface_detail(bad)

    def test_decal_validation(self):
        self.assertEqual(material_library.decal({'id': 'reflective_tape_red'})['tile_mm'], [50, 114])
        self.assertEqual(material_library.decal({'id': 'reflective_tape_red', 'tile_mm': 30})['tile_mm'], [30, 30])
        for bad in ({'id': 'textured_coarse_stipple'}, {'id': 'reflective_tape_red', 'tile_mm': [1, 2, 3]}):
            with self.assertRaises(ValueError):
                material_library.decal(bad)

    def test_reflective_parts_and_colour(self):
        self.assertTrue(material_library.is_reflective_part('红色反光条_2'))
        self.assertTrue(material_library.is_reflective_part('Reflective_Strip'))
        self.assertFalse(material_library.is_reflective_part('reflection_probe'))
        # a lamp reflector or mirror is polished, not taped
        for name in ('反光杯', '手电筒反光罩', '后视反光镜', 'Reflector', 'reflective_mirror', 'LED_reflective_cup'):
            self.assertFalse(material_library.is_reflective_part(name), name)
        self.assertEqual(material_library.decal_for('红色反光条'), 'reflective_tape_red')
        self.assertEqual(material_library.decal_for('reflective strip', [.8, .05, .04]), 'reflective_tape_red')
        self.assertEqual(material_library.decal_for('reflective strip', [.5, .6, .1]), 'reflective_tape_yellowgreen')
        self.assertEqual(material_library.decal_for('反光条'), 'reflective_tape_yellowgreen')


LID = {'name': 'Cube.001_Baked', 'transmission': 0,
       'roughness_stats': {'p5': .16, 'p50': .22, 'p95': .29, 'block_std': .0161, 'metallic_p50': 0}}


class BakedRoughnessGateTests(unittest.TestCase):
    def stats(self, **change):
        return {**LID, 'roughness_stats': {**LID['roughness_stats'], **change}}

    def test_low_noisy_nameless_plastic_is_remapped(self):
        fix = lookdev.baked_roughness_fix(LID, ['Cube.001'])
        self.assertEqual((fix['from'], fix['to']), ([.1, .4], [.42, .52]))
        self.assertIn('p95 0.29', fix['reason'])
        self.assertTrue(lookdev.baked_roughness_fix({**LID, 'name': 'ABS_shell_Baked'}))

    def test_every_test_must_agree(self):
        self.assertIsNone(lookdev.baked_roughness_fix(self.stats(p95=.36)))            # not low
        self.assertIsNone(lookdev.baked_roughness_fix(self.stats(block_std=.010)))     # smooth: meant to be glossy
        self.assertIsNone(lookdev.baked_roughness_fix(self.stats(metallic_p50=.5)))    # metal
        self.assertIsNone(lookdev.baked_roughness_fix({**LID, 'transmission': 1}))
        self.assertIsNone(lookdev.baked_roughness_fix({'name': 'x'}))
        self.assertIsNone(lookdev.baked_roughness_fix({**LID, 'roughness_stats': {'p95': 'x'}}))

    def test_named_gloss_or_other_families_are_kept(self):
        for name in ('Gloss_shell_Baked', '亮面外壳', 'Chrome_trim', 'Rubber_grip', '橡胶握把', 'Steel_body', 'Glass'):
            self.assertIsNone(lookdev.baked_roughness_fix({**LID, 'name': name}), name)
        self.assertIsNone(lookdev.baked_roughness_fix(LID, ['screw_1']))


class AutomaticChainGuardTests(unittest.TestCase):
    def test_library_detail_blocks_poly_haven_on_top(self):
        analysis = {'objects': [{'name': 'Lid', 'type': 'MESH', 'materials': ['Plastic'], 'uv_layers': ['UV']}],
                    'materials': [{'name': 'Plastic'}]}
        metrics = {'visible_material_targets': [{'object_name': 'Lid', 'material_name': 'Plastic', 'material_info': {'editable': True}}]}
        plain = ai_client._automatic_material_context({'material_overrides': []}, analysis, metrics)
        self.assertEqual([t['object_name'] for t in plain['eligible_targets']], ['Lid'])
        plan = {'material_overrides': [{'name': 'Plastic', 'preset': 'matte_plastic', 'preserve_original': True,
                                        'surface_detail': {'id': 'textured_coarse_stipple'}}]}
        guarded = ai_client._automatic_material_context(plan, analysis, metrics)
        self.assertEqual(guarded['eligible_targets'], [])
        self.assertEqual([t['object_name'] for t in guarded['excluded_targets']], ['Lid'])


BLENDER = os.getenv('BLENDER_PATH') or shutil.which('blender')
SCRIPT = r'''
import bpy, sys, json
import numpy as np
sys.path.insert(0, sys.argv[sys.argv.index('--') + 1])
from blender import worker
mode = sys.argv[-1]
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)

def baked(name, noisy=True, metal=0.0):
    mat = bpy.data.materials.new(name); mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bs = nodes.get('Principled BSDF')
    size = 4 if mode == 'tiny' else 256
    image = bpy.data.images.new(name + '_orm', size, size, float_buffer=True); image.colorspace_settings.name = 'Non-Color'
    rng = np.random.default_rng(1); px = np.zeros((size, size, 4), np.float32); px[..., 3] = 1
    blocks = rng.random((max(size // 8, 1),) * 2) * (.12 if noisy else 0)
    px[..., 1] = .16 + np.kron(blocks, np.ones((8, 8)))[:size, :size]
    px[..., 1] += rng.random((size, size)) * (.06 if noisy else 0)
    px[..., 2] = metal
    image.pixels.foreach_set(px.ravel())
    tex = nodes.new('ShaderNodeTexImage'); tex.image = image
    split = nodes.new('ShaderNodeSeparateColor')
    links.new(tex.outputs['Color'], split.inputs[0])
    links.new(split.outputs['Green'], bs.inputs['Roughness']); links.new(split.outputs['Blue'], bs.inputs['Metallic'])
    normal = nodes.new('ShaderNodeNormalMap'); normal.name = 'Baked normal'
    links.new(normal.outputs['Normal'], bs.inputs['Normal'])
    return mat

def cube(name, mat):
    bpy.ops.mesh.primitive_cube_add(); ob = bpy.context.object; ob.name = name
    ob.data.materials.append(mat); return ob

objects = [cube('Lid', baked('Lid_Baked')), cube('Body', baked('Body_Baked', noisy=False)),
           cube('Chuck', baked('Chuck_Baked', metal=1.0))]
if mode == 'reflective':
    objects.append(cube('红色反光条', baked('Tape_Baked', noisy=False)))
scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = 512 if mode == 'preview' else 2048
plan = {'rotation': [0, 0, 0], 'repair': {}, 'object_material_overrides': [],
        'material_overrides': [{'name': 'Lid_Baked', 'preset': 'matte_plastic', 'preserve_original': True,
                                'surface_detail': {'id': 'textured_coarse_stipple'}}]}
if mode == 'off': plan['roughness_sanitise'] = False
if mode == 'star': plan['material_overrides'].append({'name': '*', 'roughness_sanitise': False})
operations, warnings = worker.prepare(objects, objects, plan)
out = {'sanitised': [o['material'] for o in operations if o['operation'] == 'roughness_sanitise'],
       'library': [r for o in operations for res in o.get('resources', []) for r in res.get('library', [])],
       'decals': [o for o in operations if o['operation'] == 'reflective_decal']}
lid = bpy.data.materials['Lid_Baked']; nodes = lid.node_tree.nodes
bs = worker.bsdf_of(lid)
bump = nodes.get('Studio surface detail bump')
out['normal_from'] = bs.inputs['Normal'].links[0].from_node.name if bs.inputs['Normal'].is_linked else None
out['bump_normal_from'] = bump.inputs['Normal'].links[0].from_node.name if bump and bump.inputs['Normal'].is_linked else None
out['bump_strength_linked'] = bool(bump and bump.inputs['Strength'].is_linked)
out['roughness_from'] = bs.inputs['Roughness'].links[0].from_node.name
out['mm_per_unit'] = objects[0].get(worker.SD_ATTRIBUTE)
for ob in objects[3:]:
    mat = ob.material_slots[0].material; b = worker.bsdf_of(mat)
    out['tape'] = {'material': mat.name, 'base': b.inputs['Base Color'].links[0].from_node.name,
                   'image': b.inputs['Base Color'].links[0].from_node.image.name, 'roughness': round(b.inputs['Roughness'].default_value, 2),
                   'shared_left_alone': bpy.data.materials['Tape_Baked'].node_tree.nodes.get('Studio decal') is None}
print('RESULT:' + json.dumps(out, ensure_ascii=False))
'''


@unittest.skipUnless(BLENDER, 'Blender unavailable')
class WorkerLibraryRuntimeTests(unittest.TestCase):
    def run_scene(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'fixture.py'; script.write_text(SCRIPT, encoding='utf-8')
            result = subprocess.run([BLENDER, '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(script), '--', str(ROOT), mode],
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            line = next(l for l in result.stdout.splitlines() if l.startswith('RESULT:'))
            return json.loads(line[7:])

    def test_final_quality_sanitises_and_layers_detail_over_baked_normal(self):
        out = self.run_scene('final')
        self.assertEqual(out['sanitised'], ['Lid_Baked'])        # smooth body and metal chuck are kept
        self.assertEqual(out['roughness_from'], 'Studio roughness metal guard')
        self.assertEqual(out['normal_from'], 'Studio surface detail bump')
        self.assertEqual(out['bump_normal_from'], 'Baked normal')
        self.assertTrue(out['bump_strength_linked'])              # strength × (1 − metallic map)
        self.assertEqual([(r['id'], r['applied']) for r in out['library']], [('textured_coarse_stipple', True)])
        self.assertAlmostEqual(out['mm_per_unit'], 1000, delta=1)
        self.assertEqual(out['decals'], [])

    def test_preview_keeps_the_remap_but_skips_detail(self):
        out = self.run_scene('preview')
        self.assertEqual(out['sanitised'], ['Lid_Baked'])
        self.assertEqual(out['normal_from'], 'Baked normal')
        self.assertEqual([(r['applied'], r['reason']) for r in out['library']], [(False, 'preview')])

    def test_plan_flag_turns_the_remap_off(self):
        out = self.run_scene('off')
        self.assertEqual(out['sanitised'], [])
        self.assertNotEqual(out['roughness_from'], 'Studio roughness metal guard')

    def test_flag_on_an_all_materials_override_turns_the_remap_off(self):
        self.assertEqual(self.run_scene('star')['sanitised'], [])

    def test_tiny_roughness_image_is_left_alone(self):
        # smaller than one 8 px block: no statistics, no remap, no crash
        self.assertEqual(self.run_scene('tiny')['sanitised'], [])

    def test_reflective_part_gets_tape_on_its_own_copy(self):
        out = self.run_scene('reflective')
        self.assertEqual(out['decals'][0]['decal'], 'reflective_tape_red')
        self.assertEqual(out['tape']['base'], 'Studio decal')
        self.assertEqual(out['tape']['image'], 'basecolor_srgb.jpg')
        self.assertEqual(out['tape']['roughness'], .35)
        self.assertTrue(out['tape']['shared_left_alone'])


if __name__ == '__main__':
    unittest.main()

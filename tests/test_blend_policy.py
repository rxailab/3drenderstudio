"""Which renders keep a native .blend, and how it is saved.

Real shot outputs keep a compressed scene that references the project's own texture files; thumbnails and AI reference
views keep none (a hooked thumbnail's proof scene is deleted once verified); a correction candidate that lost gives its
scene up; consumers that need a scene say so when there is none.
"""
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import engine
import planner

ROOT = Path(__file__).resolve().parents[1]
BLENDER = shutil.which('blender')


def worker_without_blender():
    """blender/worker.py imported with stand-ins for Blender's modules: only its pure functions are used."""
    fakes = {name: MagicMock() for name in ('bpy', 'bmesh', 'mathutils', 'bpy_extras', 'bpy_extras.object_utils')}
    fakes['mathutils'] = types.SimpleNamespace(Vector=MagicMock(), Euler=MagicMock())
    with patch.dict(sys.modules, fakes):
        sys.modules.pop('blender.worker', None)
        module = importlib.import_module('blender.worker')
    sys.modules.pop('blender.worker', None)
    return module


class ScenePolicyTests(unittest.TestCase):
    def test_real_shot_outputs_keep_their_scene_and_throwaway_renders_do_not(self):
        policy = worker_without_blender().scene_file_policy
        for quality in ('preview', 'showcase', 'standard', 'fine'):
            self.assertEqual(policy({'quality': quality}), 'keep', quality)
            self.assertEqual(policy({'quality': quality, 'camera_projection_contract': {'version': 1}}), 'keep', quality)
        self.assertEqual(policy({}), 'keep')                                           # the worker's own default is a preview
        self.assertEqual(policy({'quality': 'reference'}), 'none')                     # scene_imagegen's reference views
        self.assertEqual(policy({'quality': 'thumbnail'}), 'none')
        # the camera / light hooks reopen the saved scene to prove the render: a hooked thumbnail saves a proof only
        self.assertEqual(policy({'quality': 'thumbnail', 'camera_projection_contract': {'version': 1}}), 'proof')
        self.assertEqual(policy({'quality': 'thumbnail', 'light_direction_contract': {'version': 1}}), 'proof')
        self.assertEqual(policy({'quality': 'thumbnail', 'scene_file': 'keep'}), 'keep')
        self.assertEqual(policy({'quality': 'preview', 'scene_file': 'none'}), 'none')
        self.assertEqual(policy({'quality': 'preview', 'scene_file': 'bogus'}), 'keep')


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.data = Path(self.temp.name)
        self.old = engine.DATA; engine.DATA = self.data
        self.plan = planner.default_plan(); self.shot = self.plan['shots'][0]

    def tearDown(self):
        engine.DATA = self.old; self.temp.cleanup()

    def render(self, name, quality='preview'):
        out = self.data / 'p' / 'runs' / 'j' / name; out.mkdir(parents=True)
        size = (256, 256) if quality == 'thumbnail' else (512, 512)
        Image.new('RGB', size, 'white').save(out / (name + '.png')); Image.new('RGB', size, 'white').save(out / (name + '.jpg'))
        (out / (name + '.blend')).write_bytes(b'fixture scene')
        return out

    def job(self, quality='preview'):
        return {'id': 'j', 'quality': quality, 'version': 1, 'plan': self.plan}

    def test_a_verified_proof_scene_is_deleted_and_the_record_has_no_scene(self):
        out = self.render('thumb', 'thumbnail')
        record = engine.output_record(self.job('thumbnail'), self.shot, out, {'scene_file': 'proof'})
        self.assertIsNone(record['blend_url']); self.assertIsNone(record['blend_sha256'])
        self.assertFalse(list(out.glob('*.blend')))
        self.assertTrue(record['png_url'].endswith('thumb.png'))

    def test_a_kept_scene_is_recorded(self):
        for result in ({'scene_file': 'keep'}, {}):                              # {}: a result from before the policy
            out = self.render('shot' + str(len(result)))
            record = engine.output_record(self.job(), self.shot, out, result)
            self.assertEqual(record['blend_url'], '/files/p/runs/j/%s/%s.blend' % (out.name, out.name))
            self.assertTrue(record['blend_sha256'])

    def test_losing_correction_candidates_give_up_their_scene(self):
        records = [engine.output_record(self.job(), self.shot, self.render(name), {'scene_file': 'keep'}) for name in ('a', 'b', 'c')]
        engine.discard_candidate_scenes(records, records[1])
        self.assertIsNone(records[0]['blend_url']); self.assertIsNone(records[2]['blend_url'])
        self.assertFalse((self.data / 'p/runs/j/a/a.blend').exists()); self.assertFalse((self.data / 'p/runs/j/c/c.blend').exists())
        self.assertTrue((self.data / 'p/runs/j/b/b.blend').is_file()); self.assertTrue(records[1]['blend_url'])
        engine.discard_candidate_scenes([{'blend_url': None}], records[1])          # a candidate without a scene is left alone

    def test_a_review_candidate_without_a_scene_is_not_recovered(self):
        record = {'review_candidate': {'record': {'png_url': '/files/x.png', 'jpg_url': '/files/x.jpg', 'blend_url': None}, 'adjustment': {}}}
        with patch.object(engine, 'verify_image'):
            self.assertEqual(engine.recover_review_candidate(record), (record, None))

    def test_part_picking_on_an_output_without_a_scene_says_to_regenerate(self):
        project = {'id': 'p', 'outputs': [{'job_id': 'j', 'shot_id': 'hero', 'quality': 'preview', 'version': 1, 'blend_url': None}]}
        body = {'output_id': 'j', 'shot_id': 'hero', 'quality': 'preview', 'version': 1, 'points': [{'id': 'a', 'x': .5, 'y': .5}]}
        with patch.object(engine, 'get', return_value=project):
            with self.assertRaisesRegex(ValueError, '没有保存 Blender 工程'):
                engine.pick_output('p', body)


MAKE = '''
import bpy, sys
from pathlib import Path
folder = Path(sys.argv[-1]); folder.mkdir(parents=True, exist_ok=True); (folder / 'tex').mkdir(exist_ok=True)
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
image = bpy.data.images.new('weave', 64, 64); image.pixels = [0.5] * (64 * 64 * 4)
image.filepath_raw = str(folder / 'tex' / 'weave.png'); image.file_format = 'PNG'; image.save()
bpy.ops.mesh.primitive_cube_add(); cube = bpy.context.object; cube.name = 'Box'
material = bpy.data.materials.new('Cloth'); material.use_nodes = True
node = material.node_tree.nodes.new('ShaderNodeTexImage'); node.image = image
material.node_tree.links.new(node.outputs['Color'], material.node_tree.nodes['Principled BSDF'].inputs['Base Color'])
cube.data.materials.append(material)
bpy.ops.wm.obj_export(filepath=str(folder / 'box.obj'), export_materials=True, path_mode='RELATIVE')
'''
OPEN = '''
import bpy, json
from pathlib import Path
rows = [{'name': i.name, 'packed': bool(i.packed_file), 'path': i.filepath, 'found': Path(bpy.path.abspath(i.filepath)).is_file()}
        for i in bpy.data.images if i.source == 'FILE']
print('OPENED:' + json.dumps({'meshes': [o.name for o in bpy.data.objects if o.type == 'MESH'], 'camera': bool(bpy.context.scene.camera), 'images': rows}))
'''


@unittest.skipUnless(BLENDER and Path(BLENDER).is_file(), 'Blender is not installed')
class SavedSceneRuntimeTests(unittest.TestCase):
    """The worker as the app runs it, on a textured OBJ in a project's originals folder."""

    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp()); cls.originals = cls.folder / 'project' / 'originals'
        script = cls.folder / 'make.py'; script.write_text(MAKE)
        subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(script), '--', str(cls.originals)], capture_output=True, timeout=300, check=True)
        cls.results = {quality: cls.render(quality) for quality in ('preview', 'thumbnail')}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    @classmethod
    def render(cls, quality):
        plan = planner.default_plan(); plan['visual_review'] = False
        out = cls.folder / 'project' / 'runs' / 'job' / ('box_' + quality); out.mkdir(parents=True)
        (out / 'request.json').write_text(json.dumps({'action': 'render', 'model_path': str(cls.originals / 'box.obj'), 'plan': plan,
                                                      'shot': plan['shots'][0], 'quality': quality, 'output_dir': str(out)}), encoding='utf-8')
        ran = subprocess.run([BLENDER, '-b', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python',
                              str(ROOT / 'blender' / 'worker.py'), '--', str(out / 'request.json')], capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if ran.returncode:
            raise AssertionError(ran.stdout[-2500:] + ran.stderr[-1500:])
        return out, json.loads((out / 'result.json').read_text(encoding='utf-8'))

    def test_a_preview_keeps_a_compressed_scene_that_references_the_original_texture(self):
        out, result = self.results['preview']
        self.assertEqual(result['scene_file'], 'keep')
        blend = out / result['files']['blend']
        self.assertEqual(blend.read_bytes()[:4], b'\x28\xb5\x2f\xfd')                     # zstd: saved compressed
        script = self.folder / 'open.py'; script.write_text(OPEN)
        ran = subprocess.run([BLENDER, '-b', str(blend), '--python', str(script)], capture_output=True, text=True, timeout=300)
        opened = json.loads(next(line for line in ran.stdout.splitlines() if line.startswith('OPENED:'))[7:])
        self.assertTrue(opened['camera']); self.assertTrue(opened['meshes'])
        weave = [row for row in opened['images'] if row['path'].endswith('weave.png')]
        self.assertEqual(len(weave), 1, opened['images'])
        self.assertTrue(weave[0]['packed'])                                             # carried in the file: it is downloaded and copied
        self.assertTrue(weave[0]['found'])

    def test_a_thumbnail_keeps_no_scene(self):
        out, result = self.results['thumbnail']
        self.assertEqual(result['scene_file'], 'none'); self.assertIsNone(result['files']['blend'])
        self.assertFalse(list(out.glob('*.blend')))
        self.assertTrue((out / result['files']['png']).is_file())


if __name__ == '__main__':
    unittest.main()

"""Actual Blender catalog -> real planner/service integration.

Only the external AI response is simulated.  Scene loading, geometry catalog,
image labelling/hashes, local material validation, SQLite commits, replay and
confirmation guards run their real code against an isolated synthetic cabinet.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import ai_client
import engine
import instruction_service as service
import planner
import scene_parts
import studio_store as store
import target_evidence
from test_scene_inspector import FIXTURE


CONFIG = {'enabled': True, 'provider': 'anthropic', 'base_url': 'https://example.invalid',
          'api_key': 'fixture-token-never-sent', 'model': 'fixture-vision'}
TEXT = '四个轮子都要是黑色橡胶'
CAMERA_TEXT = '镜头对准这个部位'


def response(value):
    return {'stop_reason': 'tool_use', 'content': [
        {'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': copy.deepcopy(value)}]}


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: real scene flow not run')
class ScenePartFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.source_temp.cleanup)
        cls.source_root = Path(cls.source_temp.name)
        code = FIXTURE + r'''
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
scene.render.resolution_x=512;scene.render.resolution_y=512;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(1,1,1,1)
scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=1
scene.render.filepath=str(folder/'hero.png');bpy.ops.render.render(write_still=True)
'''
        script = cls.source_root / 'build.py'; script.write_text(code, encoding='utf-8')
        command = [shutil.which('blender'), '-b', '--factory-startup', '--disable-autoexec', '-t', '4',
                   '--python-exit-code', '1', '--python', str(script), '--', str(cls.source_root)]
        ran = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        if ran.returncode: raise AssertionError(ran.stdout[-5000:])
        cls.fixture = json.loads((cls.source_root / 'fixture.json').read_text())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module in (store, engine, service):
            mocked = patch.object(module, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear(); self.addCleanup(service._INFLIGHT.clear)
        self.folder = self.root / 'fixture/runs/source'; self.folder.mkdir(parents=True)
        self.scene = self.folder / 'hero.blend'; shutil.copy2(self.source_root / 'synthetic_cabinet.blend', self.scene)
        self.png = self.folder / 'hero.png'; shutil.copy2(self.source_root / 'hero.png', self.png)
        samples = [{**row, 'x': .5, 'y': .5} for row in self.fixture['observed_slots']]
        self.samples = {'success': True, 'product_samples': samples}
        (self.folder / 'result.json').write_text(json.dumps(self.samples), encoding='utf-8')
        self.source = {'output_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3}
        self.output = {'job_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3,
                       'png_url': '/files/fixture/runs/source/hero.png',
                       'blend_url': '/files/fixture/runs/source/hero.blend',
                       'result_url': '/files/fixture/runs/source/result.json'}
        self.project = {'id': 'fixture', 'name': 'Synthetic cabinet — actual Blender integration',
                        'version': 3, 'status': 'ready', 'history': [], 'metadata': {},
                        'analysis': {'objects': copy.deepcopy(self.fixture['known_objects']),
                                     'materials': [{'name': 'Shared', 'suggested_preset': 'matte_plastic'}]},
                        'plan': planner.default_plan(), 'outputs': [copy.deepcopy(self.output)]}
        store.put('project', copy.deepcopy(self.project))
        for name in ('get_config', 'get_vision_config'):
            mocked = patch('ai_config.' + name, return_value=copy.deepcopy(CONFIG)); mocked.start(); self.addCleanup(mocked.stop)
        for name in ('record_result', 'record_vision_result'):
            mocked = patch('ai_config.' + name); mocked.start(); self.addCleanup(mocked.stop)
        self.original_hash = hashlib.sha256(self.scene.read_bytes()).hexdigest()

    def prepare(self):
        return scene_parts.prepare(self.root, self.project, self.output)

    def binding_and_patch(self, prepared):
        rows = [row for row in prepared['evidence']['targets'] if row['object_name'] in self.fixture['wheel_names']]
        self.assertEqual(len(rows), 8)
        self.assertEqual(target_evidence.spatial_instance_count(rows), 4)
        binding = {'phrase': '四个轮子', 'purpose': 'material', 'target_ids': [row['id'] for row in rows]}
        changes = {'object_material_overrides': [
            {'object_name': row['object_name'], 'material_name': row['material_name'],
             'preset': 'rubber', 'color': '#101010'} for row in rows]}
        return binding, changes

    def decision(self, prepared, partial=False):
        binding, changes = self.binding_and_patch(prepared)
        value = {'status': 'apply', 'patch': changes, 'summary': '将四组完整轮胎设为黑色橡胶',
                 'notes': [], 'unhandled': [], 'target_bindings': [binding]}
        if partial:
            value.update(status='partial_available', patch={}, target_bindings=[], requirement_results=[
                {'id': 'r1', 'status': 'ready', 'summary': '四组完整轮胎设为黑色橡胶',
                 'patch': changes, 'target_bindings': [binding]},
                {'id': 'r2', 'status': 'needs_clarification', 'summary': '请点选镜头需要对准的部位',
                 'patch': {}, 'target_bindings': []},
            ])
        return response(value)

    def apply(self, partial=False, key='prepare'):
        # Native scene fixtures below return the explicit v1 model contract.
        return service.apply_instruction('fixture', {'text': TEXT + ('；' + CAMERA_TEXT if partial else ''),
            'expected_version': 3, 'shot_id': 'hero', 'source': self.source, 'protocol': 'legacy_v1',
            **({'partial_mode':'review'} if partial else {})}, key)

    def confirm(self, prepared, key='confirm'):
        return service.confirm_instruction('fixture', {
            'proposal_id': prepared['instruction_result']['proposal_id'], 'expected_version': 3}, key)

    def assert_unmodified(self, version=3):
        saved = store.get('project', 'fixture')
        self.assertEqual(saved['version'], version)
        self.assertEqual(saved['plan'], self.project['plan'])
        self.assertEqual(saved['history'], [])
        self.assertEqual(store.all_records('job'), [])
        self.assertNotIn('fixture', service._INFLIGHT)

    def test_real_catalog_is_labelled_verified_and_reused_without_second_process(self):
        with patch.object(scene_parts.subprocess, 'run', wraps=subprocess.run) as run:
            prepared = self.prepare(); cached = self.prepare()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(prepared['cache_hit']); self.assertTrue(cached['cache_hit'])
        self.assertEqual(prepared['evidence'], cached['evidence'])
        self.assertTrue(scene_parts.verify(self.root, self.project, self.output, prepared['evidence']))
        with Image.open(prepared['images'][0]['path']) as page:
            self.assertEqual(page.size, (1600, 1200))
            self.assertEqual(page.convert('RGB').getpixel((0, 0)), (237, 241, 247))
        self.assertEqual(hashlib.sha256(self.scene.read_bytes()).hexdigest(), self.original_hash)
        self.assert_unmodified()

    def test_real_seed_priority_has_separate_guarded_cache_and_correct_target_labels(self):
        normal = self.prepare()
        seeds = [{'object_name': 'Mesh.009', 'material_name': 'Shared'}]
        seeded = scene_parts.prepare(self.root, self.project, self.output, seeds=seeds)
        self.assertEqual(seeded['evidence']['targets'][0]['object_name'], 'Mesh.009')
        self.assertEqual(seeded['evidence']['targets'][0]['id'], 't1')
        self.assertNotEqual(normal['evidence']['_inspection_guard'], seeded['evidence']['_inspection_guard'])
        self.assertNotEqual(normal['images'][0]['path'], seeded['images'][0]['path'])
        with patch.object(scene_parts.subprocess, 'run', side_effect=AssertionError('Seed cache must be reused')):
            cached = scene_parts.prepare(self.root, self.project, self.output, seeds=seeds)
        self.assertTrue(cached['cache_hit'])
        self.assertTrue(scene_parts.verify(self.root, self.project, self.output, seeded['evidence']))
        self.assertEqual(scene_parts.guard(self.root, self.project, self.output, seeds=seeds)['identity']['target_seeds'], seeds)
        self.assert_unmodified()

    def test_real_hidden_split_wheels_apply_all_eight_local_pairs_once(self):
        prepared = self.prepare(); answer = self.decision(prepared)
        with patch.object(ai_client, '_post', return_value=answer) as send, \
                patch.object(scene_parts.subprocess, 'run', side_effect=AssertionError('Cached scene must not rerun')):
            result = self.apply(); repeated = self.apply()
        self.assertEqual(send.call_count, 1)
        content = [part for message in send.call_args.args[1]['messages'] for part in message.get('content', []) if isinstance(part, dict)]
        self.assertEqual(sum(part.get('type') == 'image' for part in content), 2)
        self.assertTrue(any('PART_CATALOG_1' in part.get('text', '') for part in content))
        receipt = result['instruction_result']
        self.assertEqual(receipt['status'], 'applied'); self.assertEqual(result['version'], 4)
        self.assertEqual(receipt['target_verification'], 'scene_geometry_catalog')
        self.assertEqual(receipt['target_bindings'][0]['instance_count'], 4)
        self.assertEqual(len(receipt['target_bindings'][0]['targets']), 8)
        edits = result['plan']['object_material_overrides']
        self.assertEqual({entry['object_name'] for entry in edits}, set(self.fixture['wheel_names']))
        self.assertTrue(all(entry['preset'] == 'rubber' and entry['color'].lower() == '#101010' for entry in edits))
        self.assertEqual(result['plan']['material_overrides'], [])
        for key in ('shots', 'lighting', 'exposure', 'light_softness', 'rotation'):
            self.assertEqual(result['plan'][key], self.project['plan'][key])
        self.assertTrue(repeated['instruction_result']['replayed']); self.assertEqual(len(repeated['history']), 1)
        self.assertEqual(store.all_records('job'), [])
        self.assertEqual(hashlib.sha256(self.scene.read_bytes()).hexdigest(), self.original_hash)

    def tampered_during_inference(self, mutate, expected_version=3):
        prepared = self.prepare(); answer = self.decision(prepared)
        def fake_model(*args, **kwargs):
            mutate(prepared)
            return answer
        with patch.object(ai_client, '_post', side_effect=fake_model) as send:
            with self.assertRaises(service.InstructionError) as caught:
                self.apply()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(send.call_count, 1)
        self.assert_unmodified(expected_version)

    def test_tampered_catalog_during_inference_cannot_commit(self):
        def mutate(prepared):
            path = prepared['images'][0]['path']
            with Image.open(path) as original: image = original.convert('RGB')
            image.putpixel((0, 0), (0, 0, 0)); image.save(path)
        self.tampered_during_inference(mutate)

    def test_tampered_scene_during_inference_cannot_commit(self):
        self.tampered_during_inference(lambda prepared: self.scene.write_bytes(self.scene.read_bytes() + b'changed'))

    def test_tampered_preview_during_inference_cannot_commit(self):
        self.tampered_during_inference(lambda prepared: Image.new('RGB', (512, 512), 'red').save(self.png))

    def test_concurrent_project_version_change_cannot_commit_old_targets(self):
        def mutate(prepared):
            current = store.get('project', 'fixture'); current['version'] = 4; store.put('project', current)
        self.tampered_during_inference(mutate, expected_version=4)

    def test_partial_proposal_confirms_without_ai_or_blender_reexecution(self):
        inspected = self.prepare()
        with patch.object(ai_client, '_post', return_value=self.decision(inspected, partial=True)) as send:
            proposed = self.apply(partial=True)
        correction_text = [part.get('text', '') for message in send.call_args.args[1].get('messages', [])
                           for part in message.get('content', []) if isinstance(part, dict) and part.get('type') == 'text']
        self.assertEqual(send.call_count, 1, '\n'.join(correction_text)[-2400:]); self.assert_unmodified()
        self.assertEqual(proposed['instruction_result']['status'], 'needs_confirmation')
        with patch.object(ai_client, '_post', side_effect=AssertionError('Confirmation must not call AI')), \
                patch.object(scene_parts.subprocess, 'run', side_effect=AssertionError('Confirmation must not inspect again')):
            applied = self.confirm(proposed); replayed = self.confirm(proposed, key='new-confirm-key')
        self.assertEqual(applied['version'], 4)
        self.assertEqual(applied['instruction_result']['remaining_text'], CAMERA_TEXT)
        self.assertTrue(applied['instruction_result']['partial'])
        self.assertEqual(len(applied['plan']['object_material_overrides']), 8)
        self.assertEqual(len(replayed['history']), 1); self.assertTrue(replayed['instruction_result']['replayed'])
        self.assertEqual(applied['plan']['last_instruction']['text'], TEXT)
        self.assertEqual(store.all_records('job'), [])

    def test_partial_proposal_rejects_changed_catalog_before_confirmation(self):
        inspected = self.prepare()
        with patch.object(ai_client, '_post', return_value=self.decision(inspected, partial=True)):
            proposed = self.apply(partial=True)
        inspected['images'][0]['path'].write_bytes(b'changed-page')
        with patch.object(ai_client, '_post', side_effect=AssertionError('No AI during confirmation')):
            with self.assertRaises(service.InstructionError) as caught:
                self.confirm(proposed)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.error_code, 'instruction_proposal_stale')
        self.assert_unmodified()

    def test_nonconfined_oversized_and_unknown_pair_results_are_rejected(self):
        prepared = self.prepare(); folder = prepared['images'][0]['path'].parent
        payload = json.loads((folder / 'result.json').read_text()); identity = prepared['evidence']['_scene_identity']
        outside = copy.deepcopy(payload); outside['pages'][0]['path'] = str(self.png)
        unknown = copy.deepcopy(payload); unknown['targets'][0]['object_name'] = 'NotInThisModel'
        oversized = copy.deepcopy(payload); image = folder / 'oversized.png'
        Image.new('RGB', (1601, 64), 'white').save(image)
        oversized['pages'][0] = {'path': str(image), 'width': 1601, 'height': 64}
        for name, value in (('nonconfined', outside), ('unknown-pair', unknown), ('oversized', oversized)):
            with self.subTest(name=name), self.assertRaises(scene_parts.InspectionUnavailable):
                scene_parts._finish(folder, identity, self.project['analysis'], value)
        self.assert_unmodified()

    def test_wrong_version_or_outside_scene_is_rejected_before_blender(self):
        for change in ({'version': 2}, {'blend_url': '/files/elsewhere/hero.blend'}):
            with self.subTest(change=change), patch.object(scene_parts.subprocess, 'run') as run:
                with self.assertRaises(scene_parts.InspectionUnavailable):
                    scene_parts.prepare(self.root, self.project, {**self.output, **change})
                run.assert_not_called()
        self.assert_unmodified()


if __name__ == '__main__':
    unittest.main()

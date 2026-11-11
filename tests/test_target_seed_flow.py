"""Real Flask/planner regressions for reference-point recovery.

The saved image, ray samples and SQLite project are isolated temporary fixtures.
The AI transport, scene inspection and surface picker return explicit fixtures;
no Blender process, external service, user project or credentials are accessed.
"""
import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
import engine
import instruction_service as service
import planner
import scene_parts
import studio_store as store


TEXT = '四个轮子都要是黑色橡胶'
CONFIG = {'enabled': True, 'provider': 'anthropic', 'base_url': 'https://example.invalid',
          'api_key': 'fixture-token', 'model': 'fixture-vision'}


def material_patch(names=('Wheel1', 'Wheel2')):
    return {'object_material_overrides': [
        {'object_name': name, 'material_name': 'default', 'preset': 'rubber', 'color': '#151515'}
        for name in names]}


def ai_response(modern=True):
    value = {'patch': material_patch()}
    if modern:
        value.update(status='apply', summary='准备修改轮胎。', notes=[], unhandled=[],
                     target_bindings=[{'phrase': '四个轮子', 'purpose': 'material', 'target_ids': ['t1', 't2']}])
    return {'stop_reason': 'tool_use', 'content': [
        {'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': value}]}


class TargetSeedFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module in (store, engine, service):
            mocked = patch.object(module, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear(); self.addCleanup(service._INFLIGHT.clear)
        self.folder = self.root / 'fixture/runs/source'; self.folder.mkdir(parents=True)
        Image.new('RGB', (24, 24), 'white').save(self.folder / 'hero.png')
        samples = [{'object_name': name, 'material_name': 'default', 'x': .2 + index * .3, 'y': .85}
                   for index, name in enumerate(('Wheel1', 'Wheel2'))]
        (self.folder / 'result.json').write_text(json.dumps({'success': True, 'product_samples': samples}), encoding='utf-8')
        self.source = {'output_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3}
        self.points = [{'id': 'point-1', 'x': .2, 'y': .85}]
        self.project = {'id': 'fixture', 'name': '轮胎定位回归样例', 'version': 3, 'status': 'ready', 'history': [],
                        'metadata': {}, 'plan': planner.default_plan(),
                        'analysis': {'objects': [{'name': 'Wheel' + str(index), 'type': 'MESH', 'materials': ['default']}
                                                 for index in range(1, 5)],
                                     'materials': [{'name': 'default', 'suggested_preset': 'matte_plastic'}]},
                        'outputs': [{'job_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3,
                                     'png_url': '/files/fixture/runs/source/hero.png',
                                     'result_url': '/files/fixture/runs/source/result.json'}]}
        store.put('project', copy.deepcopy(self.project))
        self.project = store.get('project', 'fixture')
        for name in ('get_config', 'get_vision_config'):
            mocked = patch('ai_config.' + name, return_value=copy.deepcopy(CONFIG)); mocked.start(); self.addCleanup(mocked.stop)
        for name in ('record_result', 'record_vision_result'):
            mocked = patch('ai_config.' + name); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_parts, 'prepare', side_effect=scene_parts.InspectionUnavailable('Fixture has no full scene.'))
        self.inspect = mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'pick_output', side_effect=self.pick)
        self.picker = mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'submit', side_effect=AssertionError('Incomplete targets must not start a render'))
        self.submit = mocked.start(); self.addCleanup(mocked.stop)
        env = Path(__file__).resolve().parents[1] / '.env'; original_exists = Path.exists
        with patch.object(Path, 'exists', lambda path: False if path.absolute() == env else original_exists(path)):
            module = importlib.import_module('app')
        mocked = patch.object(module, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        # app may have been imported while another fixture mocked planner;
        # the legacy /feedback route must use the actual planner in this test.
        mocked = patch.object(module, 'instruct', planner.instruct); mocked.start(); self.addCleanup(mocked.stop)
        self.client = module.app.test_client()

    def pick(self, project_id, body):
        return {'snapshot': {'job_id': body['output_id'], **{key: body[key] for key in ('shot_id', 'quality', 'version')}},
                'hits': [{**point, 'hit': True, 'object_name': 'Wheel' + str(index + 1), 'material_name': 'default',
                          'material_info': {'editable': True}}
                         for index, point in enumerate(body['points'])]}

    def request(self, body=None, key='target-seed'):
        # Model fixtures and assertions retain the v1 seed-recovery contract.
        payload = body or {
            'text': TEXT, 'expected_version': 3, 'shot_id': 'hero', 'source': self.source,
            'target_points': copy.deepcopy(self.points)}
        return self.client.post('/api/projects/fixture/instruct', json={'protocol':'legacy_v1', **payload},
                                headers={'Idempotency-Key': key})

    def assert_unchanged(self):
        self.assertEqual(store.get('project', 'fixture'), self.project)
        self.assertEqual(store.all_records('job'), [])
        self.submit.assert_not_called()
        self.assertNotIn('fixture', service._INFLIGHT)

    def test_example_does_not_turn_four_wheels_into_two_selected_overrides(self):
        with patch.object(ai_client, '_post', return_value=ai_response()) as send:
            result = self.request()
        self.assertEqual(result.status_code, 400)
        payload = result.get_json()
        self.assertEqual(payload['error_code'], 'instruction_targets_unresolved')
        self.assertEqual(payload['pending'][0]['quote'], TEXT)
        self.assertEqual(payload['pending'][0]['action'], 'locate_part')
        self.assertEqual(send.call_count, 2)
        self.picker.assert_called_once()
        self.assertEqual(self.picker.call_args.args[1]['points'], self.points)
        first_body = send.call_args_list[0].args[1]
        facts = next(json.loads(item['text']) for item in first_body['messages'][-1]['content']
                     if item.get('type') == 'text' and item.get('text', '').startswith('{"current_plan"'))
        self.assertEqual(facts['selections'], [])
        self.assertEqual(facts['request_context']['target_seeds'], [{'object_name': 'Wheel1', 'material_name': 'default'}])
        self.assert_unchanged()

    def test_seed_outside_evidence_is_rejected_before_model_or_mutation(self):
        hit = self.pick('fixture', {**self.source, 'points': self.points})
        hit['hits'][0]['object_name'] = 'Wheel4'
        self.picker.side_effect = None; self.picker.return_value = hit
        with patch.object(ai_client, '_post') as send:
            result = self.request()
        self.assertEqual(result.status_code, 400)
        self.assertIn('尚未进入可核验', result.get_json()['error'])
        send.assert_not_called(); self.assert_unchanged()

    def test_client_object_names_cannot_be_injected_as_target_points(self):
        body = {'text': TEXT, 'expected_version': 3, 'shot_id': 'hero', 'source': self.source,
                'target_points': [{**self.points[0], 'object_name': 'Wheel4', 'material_name': 'default'}]}
        with patch.object(ai_client, '_post') as send:
            result = self.request(body)
        self.assertEqual(result.status_code, 400)
        send.assert_not_called(); self.picker.assert_not_called(); self.assert_unchanged()

    def test_stale_seed_snapshot_is_rejected_without_repick_or_model_call(self):
        body = {'text': TEXT, 'expected_version': 3, 'shot_id': 'hero',
                'source': {**self.source, 'version': 2}, 'target_points': self.points}
        with patch.object(ai_client, '_post') as send:
            result = self.request(body)
        self.assertIn(result.status_code, (400, 409))
        send.assert_not_called(); self.picker.assert_not_called(); self.assert_unchanged()

    def test_legacy_local_feedback_also_refuses_incomplete_four_wheel_requirement(self):
        points = self.points + [{'id': 'point-2', 'x': .5, 'y': .85}]
        with patch.object(ai_client, '_post', return_value=ai_response(modern=False)) as send:
            result = self.client.post('/api/projects/fixture/feedback', json={**self.source, 'points': points, 'text': TEXT})
        self.assertEqual(result.status_code, 400)
        self.assertIn('数量尚未完整覆盖', result.get_json()['error'])
        self.assertEqual(send.call_count, 1)
        self.assert_unchanged()


if __name__ == '__main__':
    unittest.main()

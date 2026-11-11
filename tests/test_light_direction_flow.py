"""Light directions through the actual planner, service and SQLite store.

The saved PNG, renderer measurement records and AI replies are isolated test
fixtures. Only provider transport and external rendering are replaced; no
credentials, network requests, user projects or Blender worker are used here.
"""
import copy
import hashlib
import json
import os
import unittest
from unittest.mock import patch

import ai_client
import engine
import instruction_service as service
import studio_store as store
import test_material_decision_flow as fixture


WHEELS = '四个轮子都是黑色橡胶'
CAMERA_LOCK = '镜头角度不变'
MEASUREMENTS = [
    {'role': 'key', 'object_name': 'Studio_Key', 'azimuth': 125.0,
     'elevation': 28.0, 'position': [2.1, -1.4, 2.3],
     'target': [0.0, 0.0, 0.6], 'camera': 'Hero_Camera'},
    {'role': 'fill', 'object_name': 'Studio_Fill', 'azimuth': -55.0,
     'elevation': 22.0, 'position': [-1.8, 1.2, 1.5],
     'target': [0.0, 0.0, 0.6], 'camera': 'Hero_Camera'},
]


def direction_row(ident='r1', value=30, *, bindings=True):
    row = {
        'id': ident, 'status': 'ready', 'summary': '调整当前镜头的主光方位角，准备新预览。',
        'patch': {'lighting': {'key_azimuth': value}},
        'intent': {'kind': 'adjustment', 'interpretation': '按原要求设置主光方位角。',
                   'domains': ['lighting'], 'protections': []},
    }
    if bindings:
        row['target_bindings'] = []
    return row


class LightDirectionFlowTests(unittest.TestCase):
    def setUp(self):
        fixture.MaterialDecisionFlowTests.setUp(self)
        self.body['text'] = '主光方位角设为30度'

    apply = fixture.MaterialDecisionFlowTests.apply

    def set_measurements(self, values=MEASUREMENTS):
        project = store.get('project', 'fixture')
        project['outputs'][0]['metrics'] = {'studio_light_directions': copy.deepcopy(values)}
        store.put('project', project)
        self.project = copy.deepcopy(project)

    def facts(self, send):
        for item in send.call_args.args[1]['messages'][0]['content']:
            if item.get('type') != 'text':
                continue
            try:
                value = json.loads(item['text'])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and 'current_plan' in value and 'instruction' in value:
                return value
        self.fail('The provider request must include the service-selected current context.')

    def assert_scoped_direction(self, result, value=30, status='applied'):
        self.assertEqual(result['version'], 4)
        self.assertEqual(result['instruction_result']['status'], status)
        self.assertEqual(result['instruction_result']['preview_shot_ids'], ['hero'])
        self.assertEqual(result['plan']['lighting'], self.project['plan']['lighting'])
        self.assertEqual(result['plan']['shot_adjustments']['hero']['lighting']['key_azimuth'], value)
        self.assertEqual(set(result['plan']['shot_adjustments']), {'hero'})
        self.assertEqual(engine.effective_plan(result['plan'], 'hero')['lighting']['key_azimuth'], value)
        self.assertEqual(engine.effective_plan(result['plan'], 'campaign')['lighting'],
                         engine.effective_plan(self.project['plan'], 'campaign')['lighting'])
        for field in ('shots', 'material_overrides', 'object_material_overrides',
                      'exposure', 'light_softness', 'margin', 'aspect', 'style'):
            self.assertEqual(result['plan'][field], self.project['plan'][field], field)
        self.assertEqual(len(result['history']), 1)
        self.assertEqual(store.all_records('job'), [])
        self.assertNotIn('fixture', service._INFLIGHT)

    def direction_change(self, result):
        changes = [item for item in result['instruction_result']['changes']
                   if item['path'] == 'shot_adjustments.hero.lighting.key_azimuth']
        self.assertEqual(len(changes), 1)
        return changes[0]

    def inspection_fixture(self):
        """Use fake scene bytes only to exercise source binding, never Blender."""
        scene = self.root / 'fixture/runs/source/product.blend'
        scene.write_bytes(b'isolated-light-scene-A')
        project = store.get('project', 'fixture')
        project['outputs'][0]['blend_url'] = '/files/fixture/runs/source/product.blend'
        project['outputs'][0].pop('metrics', None)
        store.put('project', project)
        self.project = copy.deepcopy(project)
        output = project['outputs'][0]
        stat = scene.stat()
        inspected = {
            'directions': copy.deepcopy(MEASUREMENTS), 'available_roles': ['key', 'fill'],
            'unavailable_roles': {'rim': '当前场景无法唯一确认轮廓光。'},
            'source': {**{key: output[key] for key in ('job_id', 'shot_id', 'version', 'quality', 'blend_url')},
                       'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                       'sha256': hashlib.sha256(scene.read_bytes()).hexdigest()},
        }
        return scene, inspected

    def test_first_one_degree_direction_is_a_real_current_shot_edit_and_replay_calls_ai_once(self):
        self.body['text'] = '主光方位角设为1度'
        with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire([
                direction_row(value=1)]))) as send:
            first = self.apply()
            replay = self.apply()
        self.assert_scoped_direction(first, value=1)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn('key_azimuth', self.facts(send)['current_plan']['lighting'])
        self.assertEqual(self.facts(send)['request_context']['current_light_directions'], [])
        change = self.direction_change(first)
        self.assertIsNone(change['before'])
        self.assertIsNone(change['effective_before'], 'an absent angle is not a multiplier of 1')
        self.assertEqual(change['after'], 1)
        self.assertEqual(change['effective_after'], 1)
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(replay['instruction_result']['id'], first['instruction_result']['id'])
        self.assertEqual(replay['plan'], first['plan'])
        self.assertEqual(replay['version'], 4)
        self.assertEqual(len(replay['history']), 1)

    def test_current_saved_image_directions_reach_llm_and_supply_measured_receipt_before(self):
        self.set_measurements()
        with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire([
                direction_row()]))) as send:
            result = self.apply()
        self.assert_scoped_direction(result)
        facts = self.facts(send)
        self.assertEqual(facts['request_context']['current_light_directions'], MEASUREMENTS)
        self.assertEqual(facts['request_context']['active_shot_id'], 'hero')
        self.assertEqual(facts['image_evidence']['current_render'], True)
        self.assertNotIn('key_azimuth', facts['current_plan']['lighting'],
                         'observations must stay separate from saved plan setters')
        change = self.direction_change(result)
        self.assertIsNone(change['before'])
        self.assertEqual(change['effective_before'], 125.0)
        self.assertEqual(change['after'], 30)
        self.assertEqual(change['effective_after'], 30)
        self.assertEqual(send.call_count, 1)

    def test_missing_stale_or_invalid_direction_measurements_are_not_invented(self):
        original = copy.deepcopy(self.project)
        for condition in ('missing', 'stale_image', 'invalid_measurement'):
            with self.subTest(condition=condition):
                project = copy.deepcopy(original)
                source = copy.deepcopy(fixture.SOURCE)
                if condition != 'missing':
                    measurements = copy.deepcopy(MEASUREMENTS)
                    if condition == 'invalid_measurement':
                        measurements[0]['azimuth'] = 'unknown'
                    project['outputs'][0]['metrics'] = {'studio_light_directions': measurements}
                if condition == 'stale_image':
                    project['outputs'][0]['version'] = 2
                    source['version'] = 2
                store.put('project', project)
                self.project = copy.deepcopy(project)
                self.body['source'] = source
                answer = fixture.wire([direction_row(bindings=condition != 'stale_image')])
                with patch.object(ai_client, '_post', return_value=fixture.reply(answer)) as send:
                    result = self.apply(key='directions-' + condition)
                self.assert_scoped_direction(result)
                self.assertEqual(self.facts(send)['request_context']['current_light_directions'], [])
                self.assertEqual(self.facts(send)['image_evidence']['current_render'], condition != 'stale_image')
                self.assertIsNone(self.direction_change(result)['effective_before'])
                self.assertEqual(send.call_count, 1)

    def test_independent_direction_applies_automatically_and_keeps_pending_wheels_and_camera_lock(self):
        original = copy.deepcopy(self.project)
        for constraint, semantic in ((CAMERA_LOCK, False), ('别把镜头原来的角度折腾了', True)):
            with self.subTest(constraint=constraint):
                store.put('project', copy.deepcopy(original))
                self.body['text'] = '主光方位角设为30度，' + WHEELS + '，' + constraint
                parts = [direction_row(),
                    {'id': 'r2', 'status': 'needs_clarification',
                     'summary': '当前采样不足以确认全部四个轮胎，请补充轮胎位置。',
                     'patch': {}, 'target_bindings': []}]
                if semantic:
                    parts.append({'id': 'r3', 'status': 'already_satisfied', 'summary': '保持镜头角度不变。',
                        'patch': {}, 'target_bindings': [],
                        'intent': {'kind': 'preservation', 'interpretation': '保留现有镜头角度。',
                                   'domains': [], 'protections': ['preserve_camera']}})
                with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire(parts))) as send:
                    try:
                        result = self.apply(key='partial-' + constraint)
                    except ai_client.ResponseContractError as exc:
                        self.fail('Partial direction reply rejected: ' + repr(exc.feedback)
                                  + '; parsed requirements: ' + repr(ai_client._instruction_requirements(self.body['text'])))
                self.assert_scoped_direction(result, status='partially_applied')
                receipt = result['instruction_result']
                self.assertTrue(receipt['partial'])
                self.assertEqual([item['quote'] for item in receipt['pending']], [WHEELS])
                self.assertEqual(receipt['remaining_text'], WHEELS + '，' + constraint)
                self.assertIn('preserve_camera', receipt['protections'])
                self.assertIn('preserve_camera', result['plan']['last_instruction']['protections'])
                self.assertIn(constraint, result['plan']['last_instruction']['text'])
                if semantic:
                    self.assertIn(constraint, result['plan']['last_instruction']['semantic_constraints'])
                self.assertNotIn('proposal_id', receipt)
                self.assertNotIn(service.PROPOSALS_KEY, store.get('project', 'fixture'))
                self.assertEqual(send.call_count, 1)

    def test_measurement_metadata_change_during_inference_blocks_commit(self):
        original = copy.deepcopy(self.project)
        for condition in ('modified', 'appeared', 'removed'):
            with self.subTest(condition=condition):
                project = copy.deepcopy(original)
                if condition != 'appeared':
                    project['outputs'][0]['metrics'] = {'studio_light_directions': copy.deepcopy(MEASUREMENTS)}
                store.put('project', project)
                self.project = copy.deepcopy(project)

                def changed(*_args):
                    current = store.get('project', 'fixture')
                    output = current['outputs'][0]
                    if condition == 'removed':
                        output.pop('metrics', None)
                    else:
                        output['metrics'] = {'studio_light_directions': copy.deepcopy(MEASUREMENTS)}
                        output['metrics']['studio_light_directions'][0]['azimuth'] = 150
                    store.put('project', current)
                    return fixture.reply(fixture.wire([direction_row()]))

                with patch.object(ai_client, '_post', side_effect=changed) as send:
                    with self.assertRaises(service.InstructionError) as caught:
                        self.apply(key='changed-measurement-' + condition)
                self.assertEqual(caught.exception.error_code, 'instruction_proposal_stale')
                self.assertEqual(send.call_count, 1)
                current = store.get('project', 'fixture')
                self.assertEqual(current['version'], 3)
                self.assertEqual(current['plan'], project['plan'])
                self.assertEqual(current['history'], [])
                self.assertNotIn('instruction_receipts', current)
                self.assertNotIn(service.PROPOSALS_KEY, current)
                self.assertNotIn('fixture', service._INFLIGHT)
                self.assertEqual(store.all_records('job'), [])

    def test_read_only_inspection_supplies_old_image_measurements_and_replay_does_not_reinspect(self):
        scene, inspected = self.inspection_fixture()
        self.assertNotIn('metrics', self.project['outputs'][0])
        runtime = {'capabilities': {'inspect_parts': False, 'pick': False, 'light_direction': True}}
        with patch.object(engine, 'runtime_status', return_value=runtime), \
                patch.object(engine, 'inspect_lighting', return_value=inspected) as inspect, \
                patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire([direction_row()]))) as send:
            first = self.apply()
            replay = self.apply()
        self.assert_scoped_direction(first)
        facts = self.facts(send)
        self.assertEqual(facts['request_context']['current_light_directions'], MEASUREMENTS)
        support = facts['request_context']['light_direction_support']
        self.assertTrue(support['inspected'])
        self.assertEqual(support['available_roles'], ['key', 'fill'])
        self.assertIn('rim', support['unavailable_roles'])
        self.assertEqual(self.direction_change(first)['effective_before'], 125.0)
        self.assertEqual(self.direction_change(first)['effective_after'], 30)
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(inspect.call_args.args[1]['blend_url'], inspected['source']['blend_url'])
        self.assertEqual(send.call_count, 1)
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(replay['plan'], first['plan'])
        self.assertEqual(hashlib.sha256(scene.read_bytes()).hexdigest(), inspected['source']['sha256'])
        self.assertNotIn('metrics', first['outputs'][0], 'read-only inspection must not invent a new render record')

    def test_stale_inspection_or_same_stat_scene_replacement_blocks_direction_commit(self):
        original = copy.deepcopy(self.project)
        for moment in ('during_inspection', 'during_inference'):
            with self.subTest(moment=moment):
                store.put('project', copy.deepcopy(original))
                scene, inspected = self.inspection_fixture()
                runtime = {'capabilities': {'inspect_parts': False, 'pick': False, 'light_direction': True}}

                def replacement(*_args):
                    before = scene.stat()
                    scene.write_bytes(b'isolated-light-scene-B')
                    os.utime(scene, ns=(before.st_atime_ns, before.st_mtime_ns))
                    self.assertEqual(scene.stat().st_size, before.st_size)
                    self.assertEqual(scene.stat().st_mtime_ns, before.st_mtime_ns)
                    self.assertNotEqual(hashlib.sha256(scene.read_bytes()).hexdigest(), inspected['source']['sha256'])
                    return fixture.reply(fixture.wire([direction_row()]))

                probe_error = engine.LightingInspectionStale('fixture scene changed during inspection') if moment == 'during_inspection' else None
                with patch.object(engine, 'runtime_status', return_value=runtime), \
                        patch.object(engine, 'inspect_lighting', return_value=inspected, side_effect=probe_error) as inspect, \
                        patch.object(ai_client, '_post', side_effect=replacement) as send:
                    with self.assertRaises(service.InstructionError) as caught:
                        self.apply(key='scene-' + moment)
                self.assertEqual(caught.exception.error_code, 'instruction_proposal_stale')
                self.assertEqual(inspect.call_count, 1)
                self.assertEqual(send.call_count, 0 if moment == 'during_inspection' else 1)
                current = store.get('project', 'fixture')
                self.assertEqual(current['version'], 3)
                self.assertEqual(current['plan'], self.project['plan'])
                self.assertEqual(current['history'], [])
                self.assertNotIn('instruction_receipts', current)
                self.assertNotIn(service.PROPOSALS_KEY, current)
                self.assertNotIn('fixture', service._INFLIGHT)
                self.assertEqual(store.all_records('job'), [])


if __name__ == '__main__':
    unittest.main()

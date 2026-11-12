"""Public error/retry regressions for definitely uncommitted AI responses.

The real Flask route and instruction service use temporary SQLite/image fixtures.
Only inference is replaced; no user configuration, renderer or network is used.
"""
import copy
import importlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
import engine
import instruction_service as service
import planner
import studio_store as store
from intelligence_rules import InstructionOutcome


TEXT = '上表面是反光的拉丝不锈钢，箱体是红色金属有光泽'
MESSAGE = '本次未应用：返回的修改项互相冲突。原要求已保留。'


class InstructionResponseErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module in (store, engine, service):
            mocked = patch.object(module, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear(); self.addCleanup(service._INFLIGHT.clear)
        folder = self.root / 'fixture/runs/source'; folder.mkdir(parents=True)
        Image.new('RGB', (20, 20), 'white').save(folder / 'hero.png')
        self.source = {'output_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3}
        self.project = {'id': 'fixture', 'name': '协议回归样例', 'slug': 'fixture', 'version': 3,
                        'status': 'ready', 'history': [], 'metadata': {}, 'analysis': {'objects': []},
                        'plan': planner.default_plan(),
                        'outputs': [{'job_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3,
                                     'png_url': '/files/fixture/runs/source/hero.png'}]}
        store.put('project', copy.deepcopy(self.project)); self.project = store.get('project', 'fixture')
        self.body = {'text': TEXT, 'expected_version': 3, 'shot_id': 'hero', 'source': self.source}
        mocked = patch.object(service.planner, 'instruct'); self.model = mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'submit', side_effect=AssertionError('A response error must not render'))
        self.submit = mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'runtime_status', return_value={'capabilities': {'inspect_parts': False}})
        mocked.start(); self.addCleanup(mocked.stop)
        env = Path(__file__).resolve().parents[1] / '.env'; original_exists = Path.exists
        with patch.object(Path, 'exists', lambda path: False if path.absolute() == env else original_exists(path)):
            self.web = importlib.import_module('app')
        mocked = patch.object(self.web, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        self.client = self.web.app.test_client()

    def request(self, key='request-1', body=None):
        return self.client.post('/api/projects/fixture/instruct', json=body or self.body,
                                headers={'Idempotency-Key': key})

    def assert_unchanged(self):
        self.assertEqual(store.get('project', 'fixture'), self.project)
        self.assertEqual(store.all_records('job'), [])
        self.assertEqual(store.all_records('material_task'), [])
        self.assertNotIn('fixture', service._INFLIGHT)
        self.submit.assert_not_called()

    def assert_response_error(self, result):
        self.assertEqual(result.status_code, 422)
        payload = result.get_json()
        self.assertEqual({key: payload[key] for key in ('error', 'error_code', 'retryable', 'applied')},
                         {'error': MESSAGE, 'error_code': 'instruction_response_invalid',
                          'retryable': True, 'applied': False})
        self.assertLessEqual(set(payload), {'error', 'error_code', 'retryable', 'applied', 'diagnostic_id', 'response_issue'})
        self.assertEqual(payload['response_issue'], {'kind': 'conflict'})
        if 'diagnostic_id' in payload:
            self.assertRegex(payload['diagnostic_id'], r'^[0-9a-f]{32}$')
        return payload

    def test_protocol_error_is_explicitly_unapplied_without_mutating_plan_or_inventing_a_pending_target(self):
        error = ai_client.ResponseContractError('AI 未给出一致的可执行决定，请明确调整目标。',
                                                code='partial_top_level_patch', path='$.patch')
        error.pending = [{'label': '上表面', 'action': 'locate_part'}]
        error.remaining_text = '模型错误残留'
        self.model.side_effect = error
        self.assert_response_error(self.request()); self.assert_unchanged()
        call = self.model.call_args
        self.assertEqual(call.args[1], TEXT)
        self.assertEqual(call.kwargs['image_path'], self.root / 'fixture/runs/source/hero.png')

    def test_rejected_requests_leave_no_receipt_or_inflight_lock_and_can_be_regenerated(self):
        def reject(*args, **kwargs):
            raise ai_client.ResponseContractError()
        self.model.side_effect = reject
        for key in ('request-1', 'request-1', 'request-2'):
            self.assert_response_error(self.request(key)); self.assert_unchanged()
        self.assertEqual(self.model.call_count, 3)

    def test_pending_no_effect_records_only_safe_effect_and_evidence_digest(self):
        outcome=InstructionOutcome('needs_clarification','没有应用')
        outcome.pending=[{'diagnostic_code':'semantic_status_effect','diagnostic_effect':'known_unchanged',
                          'diagnostic_plan_changed':False,'diagnostic_target_ids':['t1','t9','private-model-response'],
                          'raw_response':'private-model-response'}]
        project=copy.deepcopy(self.project)
        project['analysis']={'objects':[{'name':'Mesh','type':'MESH','materials':['Shared']} ]}
        evidence={'method':'camera_ray_samples','source_snapshot':self.source,'targets':[
            {'id':'t1','object_name':'Mesh','material_name':'Shared','samples':[{'x':.5,'y':.5}],
             'private':'private-evidence'}]}
        public=service.target_evidence.public_evidence(evidence,project['analysis'],3)
        service._record_response_error(project,TEXT,self.source,outcome,evidence)
        record=json.loads((self.root/'fixture/instruction-errors'/f'{outcome.diagnostic_id}.json').read_text(encoding='utf-8'))
        self.assertEqual(record['failure_details'],[{'code':'semantic_status_effect','effect':'known_unchanged','plan_changed':False,'target_ids':['t1']}])
        self.assertEqual(record['evidence_digest'],service._digest(public))
        self.assertNotIn('private-model-response',json.dumps(record))
        self.assertNotIn('private-evidence',json.dumps(record))
        self.assertEqual(service.error_diagnostics('fixture')[0][1],record)
        self.assert_unchanged()
        self.assertTrue(all(call.args[1] == TEXT for call in self.model.call_args_list))
        saved = store.get('project', 'fixture')
        self.assertNotIn('instruction_receipts', saved)
        self.assertNotIn(service.PROPOSALS_KEY, saved)

    def test_success_after_rejection_and_its_replay_apply_a_relative_change_once(self):
        body = {**self.body, 'text': '亮一点'}
        self.model.side_effect = ai_client.ResponseContractError()
        self.assert_response_error(self.request('rejected', body)); self.assert_unchanged()
        def brighten(plan, text, analysis, **kwargs):
            candidate = copy.deepcopy(plan); candidate['exposure'] += .25
            return candidate
        self.model.side_effect = brighten
        applied = self.request('regenerated', body); replay = self.request('regenerated', body)
        self.assertEqual(applied.status_code, 200); self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.get_json()['instruction_result']['replayed'])
        saved = store.get('project', 'fixture')
        self.assertEqual(saved['version'], 4); self.assertEqual(len(saved['history']), 1)
        self.assertEqual(engine.effective_plan(saved['plan'], 'hero')['exposure'], .25)
        self.assertEqual(len(saved['instruction_receipts']), 1); self.assertEqual(self.model.call_count, 2)
        self.assertEqual(store.all_records('job'), []); self.submit.assert_not_called()

    def test_a_genuine_unknown_part_keeps_its_actionable_clarification(self):
        error = InstructionOutcome('needs_clarification', '请点选实际轮胎表面。', source='ai')
        error.pending = [{'id': 'wheels', 'quote': '四个轮子改成黑色橡胶', 'label': '四个轮子',
                          'status': 'needs_clarification', 'action': 'locate_part'}]
        error.remaining_text = error.pending[0]['quote']; self.model.side_effect = error
        result = self.request(); self.assertEqual(result.status_code, 400)
        payload = result.get_json(); self.assertEqual(payload['error_code'], 'instruction_targets_unresolved')
        self.assertEqual(payload['pending'][0]['action'], 'locate_part')
        self.assertEqual(payload['remaining_text'], error.remaining_text)
        self.assertNotIn('applied', payload); self.assertNotIn('diagnostic_id', payload)
        self.assertEqual(service.error_diagnostics('fixture'), []); self.assert_unchanged()

    def test_public_error_serialization_exposes_only_valid_opaque_diagnostic_ids(self):
        for diagnostic_id in ('a' * 32, '../private.json', 'A' * 32, 1, None):
            error = ai_client.ResponseContractError('raw upstream text', code='decision_conflict', path='$.patch')
            error.diagnostic_id = diagnostic_id; error.pending = [{'action': 'locate_part'}]
            with self.web.app.app_context():
                result, status = self.web.instruction_error_response(error)
            self.assertEqual(status, 422); payload = result.get_json()
            self.assertEqual(payload['error'], MESSAGE); self.assertNotIn('pending', payload)
            self.assertEqual(payload.get('diagnostic_id'), diagnostic_id if diagnostic_id == 'a' * 32 else None)
            self.assertNotIn('feedback', payload)
        self.assert_unchanged()

    def test_a_diagnostic_write_failure_keeps_the_known_unapplied_retry_contract(self):
        self.model.side_effect = ai_client.ResponseContractError()
        with patch.object(store, 'atomic_json', side_effect=OSError('fixture disk failure')):
            payload = self.assert_response_error(self.request())
        self.assertNotIn('diagnostic_id', payload); self.assert_unchanged()

    def test_known_validation_reasons_and_schema_fields_are_curated_without_raw_feedback(self):
        cases=[
            ('number_range','$.requirement_results[0].patch.object_material_overrides[2].roughness',
             {'kind':'range','field':'roughness'},'返回的参数超出可用范围'),
            ('semantic_scope_conflict','$.requirement_results[0].intent',
             {'kind':'scope'},'要求与修改范围未能对应'),
            ('material_property_conflict','$.patch',
             {'kind':'material'},'材质参数与要求不一致'),
            ('unknown_fields','$.patch.lighting',
             {'kind':'unsupported_parameter'},'返回了当前不支持的修改参数'),
            ('type','$.patch.lighting.key_power',
             {'kind':'parameters','field':'key_power'},'修改参数格式不正确'),
        ]
        for code,path,issue,reason in cases:
            with self.subTest(code=code):
                error=ai_client.ResponseContractError('raw upstream fixture',code=code,path=path,
                                                       reason='private fixture',raw_response='data:image/private')
                with self.web.app.app_context():
                    result,status=self.web.instruction_error_response(error)
                payload=result.get_json()
                self.assertEqual(status,422);self.assertEqual(payload['response_issue'],issue)
                self.assertEqual(payload['error'],'本次未应用：'+reason+'。原要求已保留。')
                self.assertNotIn('raw upstream',result.get_data(as_text=True))
                self.assertNotIn('private',result.get_data(as_text=True))
                self.assertNotIn('path',payload);self.assertNotIn('feedback',payload)
        self.assert_unchanged()

    def test_unknown_and_malformed_feedback_never_becomes_visible_content(self):
        for feedback in (None,[],{'code':[],'path':{}},
                         {'code':'private fixture','path':'$.patch.private.roughness'},
                         {'code':'type','path':'$.patch.material_overrides[0].private.roughness'},
                         {'code':'unknown_fields','path':'$.patch.lighting.<script>private</script>'}):
            with self.subTest(feedback=feedback):
                error=ai_client.ResponseContractError('raw upstream fixture');error.feedback=feedback
                with self.web.app.app_context():
                    result,status=self.web.instruction_error_response(error)
                payload=result.get_json()
                self.assertEqual(status,422);self.assertNotIn('field',payload['response_issue'])
                self.assertNotIn('private',result.get_data(as_text=True))
                self.assertNotIn('raw upstream',result.get_data(as_text=True))
                self.assertNotIn('script',result.get_data(as_text=True))
        self.assert_unchanged()

    def test_project_archive_includes_only_sanitized_error_records(self):
        error = ai_client.ResponseContractError(code='partial_top_level_patch', path='$.patch',
                                                raw_response='credential-like-fixture')
        error.response_shapes = [{'attempt': 1, 'format': 'legacy', 'status': 'partial_available',
                                  'patch_keys': ['object_material_overrides'],
                                  'raw_response': 'data:image/png;base64,fixture-only',
                                  'requirement_rows': [{'id': 'r1', 'status': 'ready',
                                                        'patch_keys': ['object_material_overrides'],
                                                        'value': 'must-not-export'}]}]
        self.model.side_effect = error
        payload = self.assert_response_error(self.request()); self.assert_unchanged()
        folder = self.root / 'fixture/instruction-errors'
        diagnostic_path = folder / (payload['diagnostic_id'] + '.json')
        record = json.loads(diagnostic_path.read_text(encoding='utf-8'))
        record['raw_response'] = 'data:image/png;base64,fixture-only'
        record['feedback']['raw_response'] = 'credential-like-fixture'
        diagnostic_path.write_text(json.dumps(record), encoding='utf-8')
        (folder / 'private.txt').write_text('must-not-export', encoding='utf-8')
        (folder / ('b' * 32 + '.json')).write_text('{"raw_response":"must-not-export"}', encoding='utf-8')
        outside = self.root / 'outside.json'; outside.write_text('must-not-export', encoding='utf-8')
        (folder / ('c' * 32 + '.json')).symlink_to(outside)
        result = self.client.get('/api/projects/fixture/bundle')
        self.assertEqual(result.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            diagnostics = [name for name in archive.namelist() if name.startswith('instruction-errors/')]
            self.assertEqual(diagnostics, ['instruction-errors/' + diagnostic_path.name])
            exported = json.loads(archive.read(diagnostics[0]))
            self.assertEqual(exported['text'], TEXT); self.assertEqual(exported['applied'], False)
            self.assertEqual(exported['feedback'], {'code': 'partial_top_level_patch', 'path': '$.patch'})
            self.assertEqual(exported['response_shapes'][0]['patch_keys'], ['object_material_overrides'])
            text = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist() if name.endswith('.json'))
            self.assertNotIn('credential-like-fixture', text); self.assertNotIn('data:image', text)
            self.assertNotIn('must-not-export', text)
        self.assert_unchanged()


if __name__ == '__main__':
    unittest.main()

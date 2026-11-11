"""Real local evidence fixtures; every AI response and ray recheck is mocked."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import ai_client
import engine
import instruction_service as service
import studio_store as store
import target_evidence
from intelligence_rules import InstructionOutcome
from planner import default_plan


CONFIG = {'enabled': True, 'provider': 'anthropic', 'base_url': 'https://example.invalid',
          'api_key': 'fixture-token', 'model': 'fixture-vision'}
ANALYSIS = {'objects': [{'name': 'Mesh.%03d' % number, 'type': 'MESH', 'materials': ['Shared'],
                         'parent': 'Assembly', 'dimensions': [1, 1, 1]} for number in range(1, 8)],
            'materials': [{'name': 'Shared', 'suggested_preset': 'matte_plastic'}]}
SOURCE = {'output_id': 'preview-job', 'shot_id': 'hero', 'quality': 'preview', 'version': 3}


def decision(value=None, bindings=None, status='apply', unhandled=None):
    data = {'status': status, 'patch': value or {}, 'summary': '按已定位的部位调整参数。',
            'notes': [], 'unhandled': unhandled or []}
    if bindings is not None:
        data['target_bindings'] = bindings
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': data}]}


def binding(phrase='轮子', ids=None, purpose='material'):
    return {'phrase': phrase, 'purpose': purpose, 'target_ids': ['t1', 't2', 't3', 't4'] if ids is None else ids}


def local(number=1, **values):
    return {'object_name': 'Mesh.%03d' % number, 'material_name': 'Shared', **(values or {'preset': 'rubber'})}


class EvidenceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.folder = self.root / 'fixture' / 'runs' / 'preview-job'
        self.folder.mkdir(parents=True)
        self.png = self.folder / 'hero.png'; Image.new('RGB', (24, 24), 'white').save(self.png)
        self.result_path = self.folder / 'result.json'
        self.result = {'success': True, 'product_samples': [
            {'object_name': 'Mesh.%03d' % number, 'material_name': 'Shared', 'x': number / 9, 'y': .5}
            for number in range(1, 8)]}
        self.result_path.write_text(json.dumps(self.result), encoding='utf-8')
        self.output = {'job_id': SOURCE['output_id'], **{k: SOURCE[k] for k in ('shot_id', 'quality', 'version')},
                       'png_url': '/files/fixture/runs/preview-job/hero.png',
                       'result_url': '/files/fixture/runs/preview-job/result.json'}
        self.project = {'id': 'fixture', 'name': '柜子', 'version': 3, 'status': 'ready', 'history': [],
                        'plan': default_plan(), 'analysis': copy.deepcopy(ANALYSIS), 'outputs': [self.output]}
        self.evidence = target_evidence.load_evidence(self.root, self.project, self.output)
        self.context = {'active_shot_id': 'hero', 'current_version': 3,
                        'target_evidence': target_evidence.public_evidence(self.evidence, ANALYSIS, 3)}

    def request(self, text, value, bindings=None, context=None, image=True):
        with patch('ai_client._post', return_value=decision(value, bindings)) as send:
            result = ai_client.request_plan(default_plan(), text, ANALYSIS, CONFIG,
                                            image_path=self.png if image else None,
                                            context=self.context if context is None else context)
        return result, send


class TargetEvidenceTests(EvidenceFixture):
    def test_reader_exposes_only_actual_pairs_and_sampled_pixels(self):
        self.result['product_samples'] += [
            {'object_name': 'Mesh.001', 'material_name': 'Forged', 'x': .2, 'y': .2},
            {'object_name': 'Unknown', 'material_name': 'Shared', 'x': .2, 'y': .2},
            {'object_name': 'Mesh.001', 'material_name': 'Shared', 'x': True, 'y': .2},
            {'object_name': 'Mesh.001', 'material_name': 'Shared', 'x': .12, 'y': .5},
            {'object_name': 'Mesh.001', 'material_name': 'Shared', 'x': .13, 'y': .5},
            {'object_name': 'Mesh.001', 'material_name': 'Shared', 'x': .14, 'y': .5}]
        self.result_path.write_text(json.dumps(self.result), encoding='utf-8')
        evidence = target_evidence.load_evidence(self.root, self.project, self.output)
        self.assertEqual(len(evidence['targets']), 7)
        self.assertEqual(len(evidence['targets'][0]['samples']), 3)
        actual = {(sample['x'], sample['y']) for sample in self.result['product_samples']}
        self.assertTrue(all((point['x'], point['y']) in actual for row in evidence['targets'] for point in row['samples']))
        public = target_evidence.public_evidence(evidence, ANALYSIS, 3)
        self.assertNotIn('result_digest', public)
        self.assertNotIn(str(self.root), json.dumps(public))

    def test_stale_outside_and_missing_results_supply_no_evidence(self):
        for change in ({'version': 2}, {'result_url': '/files/elsewhere/result.json'},
                       {'result_url': '/files/../result.json'}, {'result_url': None}):
            with self.subTest(change=change):
                self.assertEqual(target_evidence.load_evidence(self.root, self.project, {**self.output, **change}), {})

    def test_invalid_or_unsuccessful_results_supply_no_evidence(self):
        for value in ('not json', '[]', '{"success":false}', '{"success":true,"product_samples":{}}'):
            self.result_path.write_text(value, encoding='utf-8')
            with self.subTest(value=value):
                self.assertEqual(target_evidence.load_evidence(self.root, self.project, self.output), {})

    def test_result_identity_fields_when_present_must_match_output(self):
        for change in ({'version': 2}, {'shot_id': 'back'}, {'job_id': 'another-job'},
                       {'quality': 'fine'}, {'snapshot': {'version': 2}}, {'shot': {'id': 'back'}}):
            self.result_path.write_text(json.dumps({**self.result, **change}), encoding='utf-8')
            with self.subTest(change=change):
                self.assertEqual(target_evidence.load_evidence(self.root, self.project, self.output), {})
        self.result_path.write_text(json.dumps({**self.result, 'version': 3, 'shot_id': 'hero'}), encoding='utf-8')
        self.assertTrue(target_evidence.load_evidence(self.root, self.project, self.output))

    def test_visual_chinese_parts_map_to_opaque_meshes_in_one_request(self):
        text = '四个轮子都要是橡胶黑色材质，轮子支架是镀铬反光，顶部面板是不锈钢拉丝材质，柜体是红色金属，调整灯光镜头'
        overrides = [local(number, preset='rubber', color='#151515') for number in range(1, 5)]
        overrides += [local(5, preset='polished_metal'), local(6, preset='brushed_metal'),
                      local(7, preset='painted_metal', color='#B93832')]
        bindings = [binding('四个轮子'), binding('轮子支架', ['t5']), binding('顶部面板', ['t6']), binding('柜体', ['t7'])]
        result, send = self.request(text, {'object_material_overrides': overrides, 'lighting': {'rim_power': 1.3},
                                           'shots': [{'id': 'hero', 'elevation': 22}]}, bindings)
        self.assertEqual(len(result['object_material_overrides']), 7)
        self.assertEqual(result['material_overrides'], [])
        self.assertEqual(result['lighting']['rim_power'], 1.3)
        self.assertEqual(len(result['last_instruction']['target_bindings']), 4)
        self.assertEqual(send.call_count, 1)
        facts = json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['request_context']['target_evidence']['targets'][0]['id'], 't1')
        self.assertEqual(facts['selections'], [])

    def test_binding_does_not_authorize_another_object_sharing_material(self):
        with self.assertRaisesRegex(InstructionOutcome, '未可靠定位'):
            self.request('轮子要黑色，柜体是红色金属', {'object_material_overrides': [local(1), local(7)]}, [binding('轮子', ['t1'])])

    def test_binding_does_not_authorize_global_shared_material_edit(self):
        with self.assertRaisesRegex(InstructionOutcome, '共用材质'):
            self.request('轮子要黑色', {'material_overrides': [{'name': 'Shared', 'color': '#000000'}]}, [binding('轮子', ['t1'])])

    def test_visible_target_without_binding_still_requires_grounding(self):
        with self.assertRaisesRegex(InstructionOutcome, '轮子'):
            self.request('轮子要黑色', {'object_material_overrides': [local()]}, [])

    def test_forged_id_is_never_grounded(self):
        with self.assertRaises(ValueError):
            self.request('轮子要黑色', {'object_material_overrides': [local()]}, [binding('轮子', ['t999'])])

    def test_binding_must_quote_current_instruction_and_not_repeat_ids(self):
        for proposed in (binding('柜子', ['t1']), binding('轮子', ['t1', 't1'])):
            with self.subTest(binding=proposed), self.assertRaises(InstructionOutcome):
                self.request('轮子要黑色', {'object_material_overrides': [local()]}, [proposed])

    def test_four_requested_wheels_cannot_be_satisfied_with_two_objects(self):
        for phrase in ('四个轮子', '轮子'):
            with self.subTest(phrase=phrase), self.assertRaisesRegex(InstructionOutcome, '要求定位 4 个.*只确认了 2 个'):
                self.request('四个轮子都要是黑色橡胶', {'object_material_overrides': [local(1), local(2)]}, [binding(phrase, ['t1', 't2'])])

    def test_binding_four_but_editing_only_two_rejects_entire_patch(self):
        with self.assertRaisesRegex(InstructionOutcome, '四个轮子.*2 个已定位目标未包含'):
            self.request('四个轮子都要是黑色橡胶，调整灯光',
                         {'object_material_overrides': [local(1), local(2)], 'exposure': .3}, [binding('四个轮子')])

    def test_identity_only_entries_do_not_count_as_material_assignments(self):
        changes = [local(1), local(2)] + [{'object_name': 'Mesh.%03d' % n, 'material_name': 'Shared'} for n in (3, 4)]
        with self.assertRaisesRegex(InstructionOutcome, '2 个已定位目标未包含'):
            self.request('四个轮子都要是黑色橡胶', {'object_material_overrides': changes}, [binding('四个轮子')])

    def test_property_phrase_cannot_bypass_counted_subject(self):
        with self.assertRaisesRegex(InstructionOutcome, '没有明确绑定.*四个轮子'):
            self.request('四个轮子都要是橡胶黑色材质', {'object_material_overrides': [local(1), local(2)]},
                         [binding('橡胶黑色材质', ['t1', 't2'])])

    def test_empty_bindings_and_lighting_patch_cannot_omit_material_assignment(self):
        with self.assertRaisesRegex(InstructionOutcome, '四个轮子.*尚未定位或未包含'):
            self.request('四个轮子都要是橡胶黑色材质，调整灯光凸显质感', {'exposure': .3}, [])

    def test_another_explicit_material_clause_cannot_disappear(self):
        with self.assertRaisesRegex(InstructionOutcome, '柜体.*尚未定位或未包含'):
            self.request('轮子是黑色橡胶，柜体是红色金属', {'object_material_overrides': [local()]}, [binding('轮子', ['t1'])])

    def test_material_property_word_is_not_global_shared_material_authorization(self):
        analysis=copy.deepcopy(ANALYSIS)
        for obj in analysis['objects']:
            obj['materials']=['橡胶']
        analysis['materials']=[{'name':'橡胶','suggested_preset':'rubber'}]
        response=decision({'material_overrides':[{'name':'橡胶','preset':'rubber','color':'#000000'}]})
        with patch('ai_client._post',return_value=response),self.assertRaisesRegex(InstructionOutcome,'共用材质'):
            ai_client.request_plan(default_plan(),'四个轮子都要是橡胶黑色材质',analysis,CONFIG,context={'active_shot_id':'hero'})

    def test_explicit_global_material_name_and_whole_product_remain_supported(self):
        for text in ('把全局材质“Shared”改为黑色','整个产品改成黑色'):
            with self.subTest(text=text):
                result,_=self.request(text,{'material_overrides':[{'name':'Shared','color':'#000000'}]},[])
            self.assertEqual(result['material_overrides'][0]['color'],'#000000')

    def test_negative_material_clause_does_not_create_missing_assignment(self):
        self.assertEqual(target_evidence.material_assignments('不要把轮子换成黑色橡胶'),[])
        result,_=self.request('不要改材质，曝光提高0.3EV',{'exposure':.3},[])
        self.assertEqual(result['exposure'],.3)

    def test_white_background_is_a_photography_setting_not_a_product_material(self):
        self.assertEqual(target_evidence.material_assignments('背景改成白色'),[])
        self.assertEqual(target_evidence.material_assignments('灯光改成柔和白色'),[])
        self.assertEqual(target_evidence.material_assignments('柜体颜色改成红色')[0]['phrase'],'柜体颜色')
        result,_=self.request('背景改成白色',{'style':'white'},[])
        self.assertEqual(result['style'],'white')

    def test_part_qualified_all_materials_is_not_whole_product_authorization(self):
        for text in ('柜体的所有材质改成红色','柜体的全局材质“Shared”改成红色'):
            with self.subTest(text=text),self.assertRaisesRegex(InstructionOutcome,'共用材质'):
                self.request(text,{'material_overrides':[{'name':'Shared','color':'#B93832'}]},[])

    def test_no_image_or_stale_context_cannot_ground_opaque_part(self):
        for context, image in ((self.context, False), ({**self.context, 'current_version': 4}, True)):
            with self.subTest(image=image), self.assertRaises(InstructionOutcome):
                self.request('轮子要黑色', {'object_material_overrides': [local()]}, context=context, image=image)

    def test_visual_binding_can_target_camera_but_not_guess_pose(self):
        result, _ = self.request('镜头对准轮子', {'shots': [{'id': 'hero', 'target_object': 'Mesh.001'}]}, [binding('轮子', ['t1'],purpose='camera')])
        self.assertEqual(result['shots'][0]['target_object'], 'Mesh.001')
        with self.assertRaisesRegex(InstructionOutcome, '姿态'):
            self.request('轮子转90度', {'pose': {'object': 'Mesh.001', 'rotation': [0, 0, 90]}}, [binding('轮子', ['t1'])])


class GroundedInstructionServiceTests(EvidenceFixture):
    def setUp(self):
        super().setUp()
        for module in (store, engine, service):
            mocked = patch.object(module, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project', copy.deepcopy(self.project))
        service._INFLIGHT.clear(); self.addCleanup(service._INFLIGHT.clear)
        for name in ('get_config', 'get_vision_config'):
            mocked = patch('ai_config.' + name, return_value=CONFIG); mocked.start(); self.addCleanup(mocked.stop)
        for name in ('record_result', 'record_vision_result'):
            mocked = patch('ai_config.' + name); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'runtime_status', return_value={'capabilities': {'pick': False}})
        self.runtime = mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'pick_output', side_effect=AssertionError('Real Blender must never run'))
        self.pick = mocked.start(); self.addCleanup(mocked.stop)
        # These grounding replies use the v1 interpretation/response contract.
        self.body = {'text': '四个轮子都要是黑色橡胶，增加留白', 'expected_version': 3, 'shot_id': 'hero', 'source': SOURCE,
                     'protocol': 'legacy_v1'}
        self.response = decision({'object_material_overrides': [local(number,preset='rubber',color='#151515') for number in range(1, 5)], 'margin': .2}, [binding('四个轮子')])
        mocked = patch('ai_client._post', return_value=self.response)
        self.send = mocked.start(); self.addCleanup(mocked.stop)

    def apply(self, body=None):
        return service.apply_instruction('fixture', self.body if body is None else body, 'grounded-request')

    def ready_pick(self):
        blend = self.folder / 'hero.blend'; blend.write_bytes(b'fixture-only-not-a-real-blend')
        project = store.get('project', 'fixture'); project['outputs'][0]['blend_url'] = '/files/fixture/runs/preview-job/hero.blend'
        store.put('project', project)
        self.runtime.return_value = {'capabilities': {'pick': True}}
        def picked(project_id, body):
            rows = {row['id']: row for row in self.evidence['targets']}
            return {'snapshot': {'job_id': 'preview-job', 'shot_id': 'hero', 'quality': 'preview', 'version': 3},
                    'hits': [{'id': point['id'], 'hit': True, **{k: rows[point['id']][k] for k in ('object_name', 'material_name')}} for point in body['points']]}
        self.pick.side_effect = picked

    def test_evidence_backed_material_and_lighting_apply_atomically_without_blender(self):
        result = self.apply()
        self.assertEqual(result['version'], 4)
        self.assertEqual(len(result['plan']['object_material_overrides']), 4)
        self.assertTrue(all(row['preset']=='rubber' and row['color']=='#151515' for row in result['plan']['object_material_overrides']))
        self.assertEqual(result['plan']['margin'], .2)
        self.assertEqual(result['instruction_result']['target_verification'], 'render_samples')
        self.assertEqual(result['instruction_result']['target_bindings'][0]['phrase'], '四个轮子')
        self.assertEqual(result['plan']['last_instruction']['target_bindings'][0]['target_ids'], ['t1', 't2', 't3', 't4'])
        self.pick.assert_not_called(); self.send.assert_called_once()

    def test_complete_pick_capability_rechecks_one_batch(self):
        self.ready_pick(); result = self.apply()
        self.assertEqual(result['instruction_result']['target_verification'], 'raycast')
        self.pick.assert_called_once()
        self.assertEqual(len(self.pick.call_args.args[1]['points']), 4)

    def test_missing_pick_component_uses_existing_real_samples(self):
        self.ready_pick(); self.runtime.return_value = {'capabilities': {'pick': False}}
        result = self.apply()
        self.assertEqual(result['instruction_result']['target_verification'], 'render_samples')
        self.pick.assert_not_called()

    def test_mismatched_real_hit_rejects_every_change(self):
        self.ready_pick(); original = self.pick.side_effect
        def mismatch(*args):
            result = original(*args); result['hits'][0]['object_name'] = 'Mesh.007'; return result
        self.pick.side_effect = mismatch
        with self.assertRaisesRegex(service.InstructionError, '四个轮子.*不一致'):
            self.apply()
        project = store.get('project', 'fixture')
        self.assertEqual(project['version'], 3); self.assertEqual(project['plan'], self.project['plan'])

    def test_changed_result_during_inference_rejects_every_change(self):
        def changed(*args):
            self.result['product_samples'][0]['x'] = .9
            self.result_path.write_text(json.dumps(self.result), encoding='utf-8')
            return self.response
        self.send.side_effect = changed
        with self.assertRaisesRegex(service.InstructionError, '采样已失效'):
            self.apply()
        self.assertEqual(store.get('project', 'fixture')['version'], 3)

    def test_changed_result_after_pick_is_rechecked_at_commit(self):
        self.ready_pick(); original = self.pick.side_effect
        def changed(*args):
            result = original(*args); self.result_path.write_text('{}', encoding='utf-8'); return result
        self.pick.side_effect = changed
        with self.assertRaisesRegex(service.InstructionError, '采样在处理期间已变化'):
            self.apply()
        self.assertEqual(store.get('project', 'fixture')['version'], 3)

    def test_stale_preview_cannot_supply_bindings(self):
        project = store.get('project', 'fixture'); project['outputs'][0]['version'] = 2; store.put('project', project)
        body = {**self.body, 'source': {**SOURCE, 'version': 2}}
        self.response['content'][0]['input'].pop('target_bindings')
        with self.assertRaises(InstructionOutcome):
            self.apply(body)
        self.assertEqual(store.get('project', 'fixture')['version'], 3)
        self.pick.assert_not_called()

    def test_lighting_only_with_evidence_never_calls_pick(self):
        self.ready_pick(); self.send.return_value = decision({'exposure': .3}, [])
        result = self.apply({**self.body, 'text': '曝光提高0.3EV'})
        self.assertAlmostEqual(engine.effective_plan(result['plan'], 'hero')['exposure'], .3)
        self.pick.assert_not_called(); self.runtime.assert_not_called(); self.send.assert_called_once()

    def test_lighting_without_result_keeps_existing_single_request_behavior(self):
        self.result_path.unlink(); self.send.return_value = decision({'exposure': .3})
        result = self.apply({**self.body, 'text': '曝光提高0.3EV'})
        self.assertEqual(result['version'], 4)
        self.pick.assert_not_called(); self.send.assert_called_once()

    def test_unresolved_compound_does_not_silently_apply_known_part(self):
        self.send.return_value = decision({'margin': .2}, [], unhandled=['后侧两个轮子没有可确认的采样'])
        with self.assertRaisesRegex(InstructionOutcome, '后侧两个轮子'):
            self.apply()
        project = store.get('project', 'fixture')
        self.assertEqual(project['version'], 3); self.assertEqual(project['history'], [])


if __name__ == '__main__':
    unittest.main()

"""Instruction evidence, scope, and intent regression tests; no external calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
from intelligence_rules import (InstructionOutcome, extract_protections, check_unsupported_requests,
                                preservation_only, unresolved_negative_clause)
from planner import default_plan, instruct, apply_material_note


DISABLED = {'enabled': False, 'api_key': '', 'model': ''}
CONFIG = {'enabled': True, 'provider': 'anthropic', 'base_url': 'https://example.invalid', 'api_key': 'fixture-token', 'model': 'fixture-text'}
VISION = {**CONFIG, 'model': 'fixture-vision'}
ANALYSIS = {
    'objects': [{'name': 'Bottle', 'type': 'MESH', 'materials': ['Plastic']}, {'name': 'Cap', 'type': 'MESH', 'materials': ['Plastic']}],
    'materials': [{'name': 'Plastic', 'suggested_preset': 'matte_plastic', 'color': '#112233'}],
    'animations': [],
}
CONTEXT = {'active_shot_id': 'campaign', 'project_name': 'fixture', 'product_category': '容器', 'material_note': '盖子不改颜色', 'recent_instructions': [], 'evidence_notes': []}


def decision(patch_value=None, status='apply', summary='计划调整参数。', notes=None, unhandled=None):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': ai_client.TOOL_NAME,
             'input': {'status': status, 'patch': patch_value or {}, 'summary': summary, 'notes': notes or [], 'unhandled': unhandled or []}}]}


def legacy(patch_value):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': {'patch': patch_value}}]}


def requirement_decision(rows):
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':ai_client.TOOL_NAME,
            'input':{'requirement_results':rows,'notes':[]}}]}


class InstructionIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.plan = default_plan()
        self.config = patch('ai_config.get_config', return_value=copy.deepcopy(DISABLED)).start()
        self.vision = patch('ai_config.get_vision_config', return_value=copy.deepcopy(DISABLED)).start()
        self.record = patch('ai_config.record_result').start()
        self.record_vision = patch('ai_config.record_vision_result').start()
        self.addCleanup(patch.stopall)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / 'current.png'
        self.reference = Path(self.temp.name) / 'reference.png'
        Image.new('RGB', (24, 16), '#eef1f7').save(self.image)
        Image.new('RGB', (16, 24), '#aabbcc').save(self.reference)

    def rules(self, text, **kwargs):
        return instruct(self.plan, text, ANALYSIS, context=copy.deepcopy(CONTEXT), **kwargs)

    def test_negative_clause_preserves_margin_while_positive_softness_applies(self):
        result = self.rules('不要增加留白，反光柔和一点')
        self.assertEqual(result['margin'], self.plan['margin'])
        self.assertEqual(result['light_softness'], 1.4)
        self.assertIn('preserve_margin', result['last_instruction']['protections'])
        self.assertIn('margin', result['last_instruction']['protected_fields'])

    def test_pure_negative_and_preservation_are_no_change(self):
        for text in ('不要增加留白', '不要改颜色', '保持颜色和角度不变', '只改曝光'):
            with self.subTest(text=text), self.assertRaises(InstructionOutcome) as raised:
                self.rules(text)
            self.assertEqual(raised.exception.status, 'no_change')
            self.assertFalse(raised.exception.used_image)

    def test_negative_visual_goals_are_not_preservation_or_guessed_locks(self):
        for text in ('不要让物体背光', '别让正面黑成一片', '不要太刺眼', '不要显得廉价',
                     '保持灯光自然', '保持颜色鲜艳', '保持', '保留当前'):
            with self.subTest(text=text):
                self.assertFalse(preservation_only(text))
                self.assertEqual(extract_protections(text), [])
        self.assertTrue(unresolved_negative_clause('灯光再亮一些，不要让物体背光'))
        self.assertTrue(unresolved_negative_clause('不要改这个'))
        self.assertFalse(unresolved_negative_clause('不要改颜色'))
        self.assertFalse(unresolved_negative_clause('不锈钢台面要有反光'))

    def test_offline_visual_negation_is_not_silently_ignored(self):
        for text in ('不要让物体背光', '灯光再亮一些，不要让物体背光', '曝光提高0.2EV，别让正面黑成一片'):
            original=copy.deepcopy(self.plan)
            with self.subTest(text=text), patch('ai_client.request_plan') as send, self.assertRaises(InstructionOutcome) as raised:
                self.rules(text)
            self.assertIn(raised.exception.status, ('unsupported','needs_clarification'))
            self.assertEqual(self.plan,original)
            send.assert_not_called()

    def test_semantic_preflight_defers_language_but_keeps_capability_limits(self):
        text='灯光再亮一些，不要让物体背光'
        with self.assertRaises(InstructionOutcome):
            check_unsupported_requests(text)
        check_unsupported_requests(text,allow_semantic=True)
        with self.assertRaises(InstructionOutcome) as raised:
            check_unsupported_requests('增加倒角',allow_semantic=True)
        self.assertEqual(raised.exception.status,'unsupported')

    def test_configured_llm_receives_original_negative_goal_with_visual_context(self):
        self.vision.return_value=VISION
        text='灯光再亮一些，不要让物体背光'
        candidate=copy.deepcopy(self.plan)
        candidate['lighting']['fill_power']=1.3
        context=copy.deepcopy(CONTEXT)
        with patch('ai_client.request_plan',return_value=candidate) as send:
            result=instruct(self.plan,text,ANALYSIS,image_path=self.image,context=context)
        self.assertEqual(send.call_args.args[1],text)
        self.assertEqual(send.call_args.args[3],VISION)
        self.assertEqual(send.call_args.kwargs['context'],context)
        self.assertEqual(send.call_args.kwargs['image_path'],self.image)
        self.assertEqual(result['lighting']['fill_power'],1.3)
        self.assertEqual(result['last_instruction']['protections'],[])

    def test_pure_negative_goal_reaches_configured_llm(self):
        self.config.return_value=CONFIG
        candidate=copy.deepcopy(self.plan)
        candidate['light_softness']=1.3
        with patch('ai_client.request_plan',return_value=candidate) as send:
            result=self.rules('不要太刺眼')
        send.assert_called_once()
        self.assertEqual(result['light_softness'],1.3)

    def test_preservation_and_known_unsupported_requests_do_not_spend_model_calls(self):
        self.config.return_value=CONFIG
        for text,status in [('保持颜色和角度不变','no_change'),('增加倒角','unsupported')]:
            with self.subTest(text=text), patch('ai_client.request_plan') as send, self.assertRaises(InstructionOutcome) as raised:
                self.rules(text)
            self.assertEqual(raised.exception.status,status)
            send.assert_not_called()

    def test_legacy_patch_only_contract_keeps_strict_unknown_negation(self):
        self.config.return_value=CONFIG
        with patch('ai_client.request_plan') as send, self.assertRaises(InstructionOutcome) as raised:
            instruct(self.plan,'灯光再亮一些，不要让物体背光',ANALYSIS)
        self.assertEqual(raised.exception.status,'needs_clarification')
        send.assert_not_called()

    def test_planner_preserves_validated_interpretations_and_additional_locks(self):
        self.config.return_value=CONFIG
        candidate=copy.deepcopy(self.plan)
        candidate['lighting']['fill_power']=1.3
        intents=[{'id':'r2','quote':'别动它原来的拍摄位置','kind':'preservation',
                  'interpretation':'保持当前镜头位置和产品角度。','domains':['camera'],
                  'protections':['preserve_camera']}]
        constraints=['别动它原来的拍摄位置']
        candidate['last_instruction']={'protections':['preserve_camera'],'semantic_intents':intents,
                                       'semantic_constraints':constraints}
        with patch('ai_client.request_plan',return_value=candidate):
            result=self.rules('保持颜色，别动它原来的拍摄位置，灯光再亮一些')
        explanation=result['last_instruction']
        self.assertEqual(explanation['protections'],['preserve_camera','preserve_color'])
        self.assertEqual(explanation['semantic_intents'],intents)
        self.assertEqual(explanation['semantic_constraints'],constraints)
        self.assertIn('shots',explanation['protected_fields'])
        self.assertEqual(explanation['protected_material_fields'],['color','preset'])

    def test_planner_keeps_semantic_metadata_on_no_change_and_partial_outcomes(self):
        self.config.return_value=CONFIG
        text='保持颜色，别动它原来的拍摄位置，灯光再亮一些'
        intents=[{'id':'r2','quote':'别动它原来的拍摄位置','kind':'preservation',
                  'interpretation':'保持当前镜头位置和产品角度。','domains':['camera'],
                  'protections':['preserve_camera']}]
        metadata={'protections':['preserve_camera'],'semantic_intents':intents,
                  'semantic_constraints':['别动它原来的拍摄位置']}
        unchanged=copy.deepcopy(self.plan)
        unchanged['last_instruction']=copy.deepcopy(metadata)
        with patch('ai_client.request_plan',return_value=unchanged), self.assertRaises(InstructionOutcome) as raised:
            self.rules(text)
        self.assertEqual(raised.exception.status,'no_change')
        self.assertEqual(raised.exception.protections,['preserve_camera','preserve_color'])
        self.assertEqual(raised.exception.semantic_intents,intents)
        self.assertEqual(raised.exception.semantic_constraints,metadata['semantic_constraints'])
        candidate=copy.deepcopy(self.plan)
        candidate['lighting']['fill_power']=1.3
        candidate['last_instruction']=copy.deepcopy(metadata)
        pending=InstructionOutcome('partial_available','部分要求仍需定位。',source='ai',protections=['preserve_camera'])
        pending.proposal={'candidate_plan':candidate,'ready':[{'status':'ready'}]}
        with patch('ai_client.request_plan',side_effect=pending), self.assertRaises(InstructionOutcome) as raised:
            self.rules(text)
        explanation=raised.exception.proposal['candidate_plan']['last_instruction']
        self.assertEqual(raised.exception.protections,['preserve_camera','preserve_color'])
        self.assertEqual(explanation['protections'],raised.exception.protections)
        self.assertEqual(explanation['semantic_intents'],intents)
        self.assertEqual(explanation['semantic_constraints'],metadata['semantic_constraints'])

    def test_planner_keeps_metadata_from_direct_nonapplying_ai_outcome(self):
        self.config.return_value=CONFIG
        outcome=InstructionOutcome('no_change','画面已符合当前要求。',source='ai',protections=['preserve_camera'])
        outcome.semantic_intents=[{'id':'r2','quote':'别动它原来的拍摄位置','kind':'preservation',
                                  'interpretation':'保持当前镜头。','domains':['camera'],
                                  'protections':['preserve_camera']}]
        outcome.semantic_constraints=['别动它原来的拍摄位置']
        with patch('ai_client.request_plan',side_effect=outcome), self.assertRaises(InstructionOutcome) as raised:
            self.rules('保持颜色，别动它原来的拍摄位置，灯光再亮一些')
        self.assertEqual(raised.exception.protections,['preserve_camera','preserve_color'])
        self.assertEqual(raised.exception.semantic_intents,outcome.semantic_intents)
        self.assertIn('shots',raised.exception.protected_fields)

    def test_numeric_controls_and_camera_are_scoped(self):
        result = self.rules('曝光提高0.5EV，留白20%，补光降低20%，环境光设为0.5，焦距85mm')
        self.assertEqual(result['exposure'], .5)
        self.assertEqual(result['margin'], .2)
        self.assertEqual(result['lighting']['fill_power'], .8)
        self.assertEqual(result['lighting']['environment'], .5)
        self.assertEqual(next(s for s in result['shots'] if s['id'] == 'campaign')['focal_length'], 85)
        self.assertEqual(result['shots'][0], self.plan['shots'][0])
        self.assertEqual(result['last_instruction']['changes'], result['last_instruction']['parameter_diff'])
        self.assertTrue(all(isinstance(item, dict) for item in result['last_instruction']['changes']))

    def test_existing_angle_phrase_only_changes_current_shot(self):
        result = self.rules('换成俯拍')
        self.assertEqual([s['id'] for s in result['shots'] if s['elevation'] == 80], ['campaign'])

    def test_lighting_preserved_for_margin_only_request(self):
        result = self.rules('灯光不变，仅增加留白')
        self.assertEqual(result['margin'], .17)
        self.assertEqual(result['lighting'], self.plan['lighting'])
        self.assertTrue({'lighting', 'exposure', 'light_softness'}.issubset(result['last_instruction']['protected_fields']))

    def test_unsupported_compound_never_partially_executes(self):
        for text in ('增加留白，增加倒角', '增加留白，变得像木星'):
            with self.subTest(text=text), self.assertRaises(InstructionOutcome) as raised:
                self.rules(text)
            self.assertEqual(raised.exception.status, 'unsupported')
            self.assertEqual(self.plan['margin'], .12)

    def test_conflicting_positive_commands_are_explained(self):
        with self.assertRaises(InstructionOutcome) as raised:
            self.rules('反光柔和一点，突出表面纹理')
        self.assertEqual(raised.exception.status, 'needs_clarification')

    def test_noop_at_parameter_limit_is_not_success(self):
        self.plan['light_softness'] = 4
        with self.assertRaises(InstructionOutcome) as raised:
            self.rules('反光柔和一点')
        self.assertEqual(raised.exception.status, 'no_change')

    def test_explicit_numeric_out_of_range_does_not_clamp_or_apply(self):
        with self.assertRaises(ValueError):
            self.rules('曝光设为9EV，留白20%')
        self.assertEqual(self.plan['margin'], .12)

    def test_rule_fallback_does_not_claim_to_have_seen_image(self):
        result = self.rules('曝光设为0.5EV', image_path=self.image)
        self.assertFalse(result['last_instruction']['used_image'])
        self.assertIn('本次使用内置规则，未分析图片内容。', result['last_instruction']['notes'])

    def test_reference_without_vision_service_is_not_faked(self):
        with self.assertRaises(InstructionOutcome) as raised:
            self.rules('参考图片，亮一点', image_path=self.image, reference_path=self.reference)
        self.assertEqual(raised.exception.status, 'unsupported')

    def test_modern_multimodal_payload_labels_roles_and_caps_history(self):
        context = {**CONTEXT, 'recent_instructions': [{'text': str(i)} for i in range(8)], 'evidence_notes': ['图片来自旧版本，不代表尚未渲染的新参数。']}
        with patch('ai_client._post', return_value=decision({'exposure': .2})) as send:
            result = ai_client.request_plan(self.plan, '按参考图调整曝光', ANALYSIS, VISION, image_path=self.image, reference_path=self.reference, context=context)
        body = send.call_args.args[1]
        content = body['messages'][0]['content']
        self.assertIn('CURRENT_RENDER', content[0]['text'])
        self.assertEqual(content[1]['type'], 'image')
        self.assertIn('REFERENCE_IMAGE', content[2]['text'])
        self.assertEqual(content[3]['type'], 'image')
        facts = json.loads(content[-1]['text'])
        self.assertEqual(facts['image_evidence'], {'current_render': True, 'reference_image': True})
        self.assertEqual([entry['text'] for entry in facts['request_context']['recent_instructions']], ['3', '4', '5', '6', '7'])
        self.assertIn('already-completed history', body['system'])
        self.assertIn('read-only hypothesis', body['system'])
        self.assertEqual(result['exposure'], .2)

    def test_modern_image_request_uses_visual_route_and_records_it(self):
        self.config.return_value = CONFIG
        self.vision.return_value = VISION
        with patch('ai_client._post', return_value=decision({'exposure': .3})) as send:
            result = self.rules('提亮', image_path=self.image)
        self.assertEqual(send.call_args.args[0]['model'], 'fixture-vision')
        self.record_vision.assert_called_once()
        self.record.assert_not_called()
        self.assertTrue(result['last_instruction']['used_image'])

    def test_legacy_selected_part_keeps_main_route_and_old_schema(self):
        self.config.return_value = CONFIG
        self.vision.return_value = VISION
        pairs = [{'object_name': 'Cap', 'material_name': 'Plastic'}]
        with patch('ai_client._post', return_value=legacy({'object_material_overrides': [{'object_name': 'Cap', 'material_name': 'Plastic', 'roughness': .7}]})) as send:
            result = instruct(self.plan, '所选部件粗糙一些', ANALYSIS, image_path=self.image, selections=pairs)
        self.assertEqual(send.call_args.args[0]['model'], 'fixture-text')
        self.record.assert_called_once()
        self.assertTrue(result['last_instruction']['used_image'])

    def test_hard_color_constraint_is_in_schema_and_enforced_after_return(self):
        changed = {'object_material_overrides': [{'object_name': 'Cap', 'material_name': 'Plastic', 'color': '#ffffff'}]}
        with patch('ai_client._post', return_value=decision(changed)) as send, self.assertRaises(InstructionOutcome) as raised:
            ai_client.request_plan(self.plan, '保持原色，只让反光柔和一点', ANALYSIS, CONFIG, context=CONTEXT)
        schema = send.call_args.args[1]['tools'][0]['input_schema']['properties']['requirement_results']['items']['properties']['patch']
        fields = schema['properties']['object_material_overrides']['items']['properties']
        self.assertNotIn('color', fields)
        self.assertNotIn('preset', fields)
        self.assertEqual(raised.exception.status, 'needs_clarification')

    def test_only_exposure_schema_is_narrow_and_rejects_extra_changes(self):
        with patch('ai_client._post', return_value=decision({'exposure': .2, 'margin': .2})) as send, self.assertRaises(InstructionOutcome):
            ai_client.request_plan(self.plan, '只改曝光到0.2EV', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(set(send.call_args.args[1]['tools'][0]['input_schema']['properties']['requirement_results']['items']['properties']['patch']['properties']), {'exposure'})

    def test_current_shot_is_only_camera_enum_and_other_shot_is_rejected(self):
        with patch('ai_client._post', return_value=decision({'shots': [{'id': 'hero', 'elevation': 80}]})) as send, self.assertRaises(InstructionOutcome):
            ai_client.request_plan(self.plan, '换成俯拍', ANALYSIS, CONFIG, context=CONTEXT)
        schema = send.call_args.args[1]['tools'][0]['input_schema']['properties']['requirement_results']['items']['properties']['patch']['properties']['shots']
        self.assertEqual(schema['maxItems'], 1)
        self.assertEqual(schema['items']['properties']['id']['enum'], ['campaign'])

    def test_unknown_visual_part_is_not_guessed_from_valid_mesh_name(self):
        data = decision({'object_material_overrides': [{'object_name': 'Cap', 'material_name': 'Plastic', 'roughness': .7}]})
        with patch('ai_client._post', return_value=data), self.assertRaises(InstructionOutcome) as raised:
            ai_client.request_plan(self.plan, '这个盖子粗糙一点', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(raised.exception.status, 'needs_clarification')

    def test_unknown_camera_target_cannot_guess_a_valid_mesh(self):
        data = decision({'shots': [{'id': 'campaign', 'target_object': 'Cap'}]})
        with patch('ai_client._post', return_value=data), self.assertRaises(InstructionOutcome) as raised:
            ai_client.request_plan(self.plan, '保留原色，镜头对准这个盖子', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(raised.exception.status, 'needs_clarification')
        self.assertIn('preserve_color', raised.exception.protections)

    def test_unhandled_request_prevents_partial_ai_patch(self):
        data = decision({'margin': .2}, unhandled=['缺少可确认的目标部件'])
        with patch('ai_client._post', return_value=data) as send, self.assertRaises(ai_client.ResponseContractError) as raised:
            ai_client.request_plan(self.plan, '增加留白并改一下这里', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(send.call_count,2)
        self.assertFalse(raised.exception.applied)
        self.assertEqual(raised.exception.error_code,'instruction_response_invalid')
        self.assertEqual(self.plan['margin'], .12)

    def test_no_change_ai_outcome_preserves_actual_image_use(self):
        data = decision(status='no_change', summary='当前曝光已满足要求。')
        with patch('ai_client._post', return_value=data), self.assertRaises(InstructionOutcome) as raised:
            ai_client.request_plan(self.plan, '按参考图检查是否需要调整曝光', ANALYSIS, VISION, image_path=self.image, reference_path=self.reference, context=CONTEXT)
        self.assertTrue(raised.exception.used_image)
        self.assertTrue(raised.exception.used_reference)
        self.assertEqual(raised.exception.source, 'ai')
        self.assertEqual(raised.exception.model, 'fixture-vision')

    def test_schema_retry_is_bounded_and_keeps_original_evidence(self):
        bad = decision({'margin': 999})
        good = decision({'margin': .2})
        with patch('ai_client._post', side_effect=[bad, good]) as send:
            result = ai_client.request_plan(self.plan, '增加留白', ANALYSIS, CONFIG, image_path=self.image, context=CONTEXT)
        self.assertEqual(send.call_count, 2)
        first, second = [call.args[1] for call in send.call_args_list]
        self.assertEqual(first['messages'][0]['content'][:2], second['messages'][0]['content'][:2])
        self.assertIn('schema_correction', second['messages'][0]['content'][-1]['text'])
        self.assertEqual(result['margin'], .2)

    def test_network_failure_never_retries_without_image(self):
        with patch('ai_client._post', side_effect=ValueError('网络不可用')) as send, self.assertRaises(ValueError):
            ai_client.request_plan(self.plan, '提亮', ANALYSIS, CONFIG, image_path=self.image, context=CONTEXT)
        self.assertEqual(send.call_count, 1)

    def test_material_notes_preserve_local_negative_and_uncertain_intent(self):
        for note in ('盖子是玻璃', '不是玻璃', '哑光塑料', '整个产品可能是玻璃', '整个产品是玻璃，保留原色', '整个产品包含玻璃', '整个产品使用了玻璃和钢', '整个产品除某部分外都是玻璃'):
            with self.subTest(note=note):
                result = apply_material_note(self.plan, ANALYSIS, note)
                self.assertEqual(result['material_overrides'], [])

    def test_only_explicit_whole_product_note_can_apply_globally(self):
        result = apply_material_note(self.plan, ANALYSIS, '所有部件统一为红色哑光塑料')
        self.assertEqual(result['material_overrides'][0]['preset'], 'matte_plastic')
        self.assertEqual(result['material_overrides'][0]['color'], '#B93832')

    def test_history_is_not_automatically_a_constraint_and_release_is_explicit(self):
        self.assertEqual(extract_protections('曝光设为0.3EV'), [])
        self.assertEqual(extract_protections('这次可以改颜色', ['preserve_color']), [])
        self.assertIn('preserve_camera', extract_protections('保持颜色和角度不变'))

    def test_shared_preservation_predicate_protects_every_named_subject(self):
        for text, expected in [
            ('颜色和角度不变', {'preserve_color', 'preserve_camera'}),
            ('颜色/材质都不变', {'preserve_color', 'preserve_material'}),
            ('不要改变颜色和材质', {'preserve_color', 'preserve_material'}),
            ('保持颜色与角度', {'preserve_color', 'preserve_camera'}),
        ]:
            with self.subTest(text=text):
                self.assertTrue(expected.issubset(extract_protections(text)))
                with self.assertRaises(InstructionOutcome) as raised:
                    self.rules(text)
                self.assertEqual(raised.exception.status, 'no_change')
                result = self.rules(text + '，曝光提高0.2EV')
                self.assertEqual(result['exposure'], .2)
                self.assertTrue(expected.issubset(result['last_instruction']['protections']))

    def test_only_current_camera_changes_and_locks_every_other_root(self):
        result = self.rules('只让当前镜头俯拍，其他不变')
        self.assertEqual(next(s for s in result['shots'] if s['id'] == 'campaign')['elevation'], 80)
        self.assertIn('only_camera', result['last_instruction']['protections'])
        self.assertTrue({'exposure', 'lighting', 'margin', 'material_overrides'}.issubset(result['last_instruction']['protected_fields']))
        rows=[{'id':'r1','status':'ready','summary':'将当前镜头调整为俯拍。',
               'patch':{'shots':[{'id':'campaign','elevation':80}]}},
              {'id':'r2','status':'already_satisfied','summary':'仅修改当前镜头参数，其余设置保留。','patch':{},
               'intent':{'kind':'preservation','interpretation':'只修改当前镜头，其他参数保持不变。',
                         'domains':[],'protections':['only_camera']}}]
        with patch('ai_client._post', return_value=requirement_decision(rows)) as send:
            actual=ai_client.request_plan(self.plan, '只让当前镜头俯拍，其他不变', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(next(s for s in actual['shots'] if s['id']=='campaign')['elevation'],80)
        props = send.call_args.args[1]['tools'][0]['input_schema']['properties']['requirement_results']['items']['properties']['patch']['properties']
        self.assertEqual(set(props), {'shots'})
        self.assertEqual(set(props['shots']['items']['properties']), {'id', 'azimuth', 'elevation', 'focal_length', 'target_object'})
        with patch('ai_client._post', return_value=decision({'exposure': .2})), self.assertRaises(InstructionOutcome) as raised:
            ai_client.request_plan(self.plan, '只让当前镜头俯拍，其他不变', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(raised.exception.status,'needs_clarification')
        self.assertIn('only_camera',raised.exception.protections)

    def test_unclear_negative_scope_cannot_be_silently_dropped_from_compound(self):
        with self.assertRaises(InstructionOutcome) as raised:
            self.rules('不要改这个，曝光提高0.2EV')
        self.assertEqual(raised.exception.status, 'needs_clarification')
        # The configured semantic route sees the full request, but an answer
        # that omits the unresolved prohibition still cannot authorise a patch.
        with patch('ai_client._post',return_value=decision({'exposure':.2})) as send, self.assertRaises(ai_client.ResponseContractError) as raised:
            ai_client.request_plan(self.plan, '不要改这个，曝光提高0.2EV', ANALYSIS, CONFIG, context=CONTEXT)
        self.assertEqual(send.call_count,2)
        self.assertEqual(raised.exception.feedback['code'],'semantic_interpretation_required')
        self.assertEqual(self.plan['exposure'],0)

    def test_negative_exposure_direction_becomes_conservative_hard_lock(self):
        result = self.rules('曝光不要提高，补光降低20%')
        self.assertEqual(result['exposure'], 0)
        self.assertEqual(result['lighting']['fill_power'], .8)
        self.assertIn('exposure', result['last_instruction']['protected_fields'])

    def test_invalid_image_fails_before_network_without_imageless_retry(self):
        for kwargs, expected in [
            ({'image_path': self.image.with_name('missing.png')}, '当前画面'),
            ({'image_path': self.image, 'reference_path': self.image.with_name('missing.png')}, '参考图片'),
        ]:
            with self.subTest(kwargs=kwargs), patch('ai_client._post') as send, self.assertRaisesRegex(ValueError, expected):
                ai_client.request_plan(self.plan, '提亮', ANALYSIS, CONFIG, context=CONTEXT, **kwargs)
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

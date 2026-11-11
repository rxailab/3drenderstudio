"""Grounded follow-up material/lighting requests; no live model calls."""
import copy
import json
from unittest.mock import patch

import ai_client
import material_intent
import planner
import target_evidence
from intelligence_rules import InstructionOutcome, material_clause
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, binding, local


TEXT = '柜体光泽不够，灯光再亮点'


def reply(rows):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': ai_client.TOOL_NAME,
            'input': {'requirement_results': copy.deepcopy(rows), 'notes': []}}]}


def rows(roughness=.23):
    values = {} if roughness is None else {'roughness': roughness}
    return [
        {'id': 'r1', 'status': 'ready', 'summary': '增加柜体表面光泽，保留原色与材质。',
         'intent': {'kind': 'adjustment', 'interpretation': '降低柜体表面的粗糙度，增加光泽。',
                    'domains': ['material'], 'protections': []},
         'patch': {'object_material_overrides': [dict(object_name='Mesh.007', material_name='Shared', **values)]},
         'target_bindings': [binding('柜体', ['t7'])]},
        {'id': 'r2', 'status': 'ready', 'summary': '增加正面主光和补光。',
         'patch': {'lighting': {'key_power': 1.6, 'fill_power': 1.4}}, 'target_bindings': []},
    ]


class FollowupFinishTests(EvidenceFixture):
    def setUp(self):
        super().setUp()
        self.context['current_material_values'] = [
            {'object_name': 'Mesh.007', 'material_name': 'Shared', 'values': {'roughness': .35, 'metallic': .6},
             'linked_fields': [], 'basis': 'saved_scene_principled'}]
        self.context['execution_capabilities'] = {
            'material_fields': ['roughness', 'metallic', 'transmission', 'color'],
            'material_preset_replacement': False}

    def ask(self, parts=None, text=TEXT, plan=None, responses=None):
        plan = plan or planner.default_plan()
        before = copy.deepcopy(plan)
        responses = responses or [reply(parts or rows())] * 2
        with patch.object(ai_client, '_post', side_effect=responses) as send:
            try:
                value = ai_client.request_plan(plan, text, ANALYSIS, CONFIG, image_path=self.png, context=self.context)
            except ValueError as exc:
                value = exc
        self.assertEqual(plan, before)
        return value, send

    def test_exact_screenshot_clause_has_material_scope_without_assignment_verb(self):
        self.assertEqual(material_clause('柜体光泽不够')['subjects'], ['柜体'])
        self.assertEqual(target_evidence.material_assignments('柜体光泽不够'), [{'phrase': '柜体', 'value': '光泽不够'}])
        self.assertEqual(ai_client._requirement_domains('柜体光泽不够'), {'material'})
        self.assertEqual(ai_client._requirement_domains('灯光再亮点'), {'lighting'})
        self.assertIsNone(material_clause('灯光反光不够'))
        self.assertIsNone(material_clause('让整个画面更亮一点'))

    def test_correct_llm_material_interpretation_applies_both_clauses_in_one_call(self):
        result, send = self.ask()
        self.assertIsInstance(result, dict)
        material = result['object_material_overrides'][0]
        self.assertEqual(material['roughness'], .23)
        self.assertTrue(material['preserve_original'])
        self.assertNotIn('preset', material)
        self.assertNotIn('color', material)
        self.assertEqual(result['lighting']['key_power'], 1.6)
        self.assertEqual(result['lighting']['fill_power'], 1.4)
        self.assertEqual(result['shots'], planner.default_plan()['shots'])
        self.assertEqual(send.call_count, 1)
        schema = send.call_args.args[1]['tools'][0]['input_schema']
        fields = schema['properties']['requirement_results']['items']['properties']['patch']['properties']['object_material_overrides']['items']['properties']
        self.assertNotIn('preset', fields)
        self.assertNotIn('specular', fields)
        facts = json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['request_context']['current_material_values'][0]['values']['roughness'], .35)
        self.assertEqual(facts['user_requirements'][0]['material_targets'], [{'phrase': '柜体', 'value': '光泽不够'}])
        self.assertNotIn('_current_material_values', result)

    def test_missing_roughness_uses_grounded_actual_scene_not_stale_plan(self):
        plan = planner.default_plan()
        plan['object_material_overrides'] = [local(7, roughness=.9, color='#B93832', preserve_original=True)]
        result, send = self.ask(rows(None), plan=plan)
        self.assertIsInstance(result, dict)
        self.assertAlmostEqual(result['object_material_overrides'][0]['roughness'], .2275)
        self.assertEqual(result['object_material_overrides'][0]['color'], '#B93832')
        self.assertNotIn('preset', result['object_material_overrides'][0])
        self.assertEqual(send.call_count, 1)

    def test_relative_gloss_cannot_be_satisfied_by_unchanged_actual_value(self):
        result, send = self.ask(rows(.35))
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'partial_available')
        self.assertEqual(send.call_count, 2)
        proposal = result.proposal
        self.assertEqual(proposal['candidate_plan']['object_material_overrides'], [])
        self.assertEqual(proposal['candidate_plan']['lighting']['key_power'], 1.6)
        self.assertEqual(proposal['remaining_text'], '柜体光泽不够')
        self.assertEqual(proposal['pending'][0]['action'], 'clarify_parameters')
        self.assertIn('参数未通过校验', proposal['pending'][0]['reason'])
        self.assertEqual([row['id'] for row in proposal['ready']], ['r2'])

    def test_tiny_gloss_change_is_corrected_within_existing_two_call_budget(self):
        result, send = self.ask(responses=[reply(rows(.349)), reply(rows(.23))])
        self.assertIsInstance(result, dict)
        self.assertEqual(result['object_material_overrides'][0]['roughness'], .23)
        self.assertEqual(send.call_count, 2)
        correction = json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'], 'material_property_conflict')
        self.assertEqual(correction['schema_correction']['requirement_ids'], ['r1'])

    def test_gloss_only_never_silently_recolors_or_changes_metallic(self):
        for fields in ({'color': '#FFFFFF'}, {'metallic': 1}, {'transmission': .2}):
            with self.subTest(fields=fields):
                parts = rows();parts[0]['patch']['object_material_overrides'][0].update(fields)
                result, send = self.ask(parts)
                self.assertIsInstance(result, InstructionOutcome)
                self.assertEqual(result.status, 'partial_available')
                self.assertEqual(result.proposal['candidate_plan']['object_material_overrides'], [])
                self.assertEqual(send.call_count, 2)

    def test_unknown_native_field_is_not_silently_coerced_or_executed(self):
        parts = rows();parts[0]['patch']['object_material_overrides'][0]['specular'] = .8
        result, send = self.ask(parts)
        self.assertIsInstance(result, ai_client.ResponseContractError)
        self.assertFalse(result.applied)
        self.assertEqual(send.call_count, 2)
        self.assertFalse(hasattr(result, 'proposal'))

    def test_missing_target_binding_cannot_be_filled_by_current_material_values(self):
        parts = rows(None);parts[0]['target_bindings'] = []
        result, send = self.ask(parts)
        self.assertIsInstance(result, InstructionOutcome)
        self.assertNotEqual(getattr(result, 'status', None), 'applied')
        self.assertFalse(hasattr(result, 'proposal'))
        self.assertEqual(send.call_count, 2)

    def test_unrelated_known_values_are_not_used_for_the_requested_target(self):
        self.context['current_material_values'][0]['object_name'] = 'Mesh.006'
        result, _ = self.ask(rows(None))
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'partial_available')
        self.assertEqual(result.proposal['candidate_plan']['object_material_overrides'], [])

    def test_linked_socket_is_not_treated_as_current_roughness(self):
        self.context['current_material_values'][0]['linked_fields'] = ['roughness']
        result, _ = self.ask(rows(None))
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'partial_available')
        self.assertEqual(result.proposal['candidate_plan']['object_material_overrides'], [])

    def test_duplicate_current_measurements_do_not_choose_an_arbitrary_baseline(self):
        self.context['current_material_values'].append(copy.deepcopy(self.context['current_material_values'][0]))
        self.context['current_material_values'][1]['values']['roughness'] = .9
        result, _ = self.ask(rows(None))
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'partial_available')
        self.assertEqual(result.proposal['candidate_plan']['object_material_overrides'], [])

    def test_explicit_gentle_and_numeric_material_requests_keep_priority(self):
        values = {'object_material_overrides': [dict(object_name='Mesh.007', material_name='Shared')]}
        resolved = [{'phrase': '柜体', 'purpose': 'material', 'targets': [values['object_material_overrides'][0]]}]
        current = self.context['current_material_values']
        for text, expected in (('柜体光泽稍微再强一点', .315), ('柜体是有光泽粗糙度设为0.5', .5)):
            with self.subTest(text=text):
                result = material_intent.complete_missing(values, text, resolved, current_values=current)
                self.assertAlmostEqual(result['object_material_overrides'][0]['roughness'], expected)
                material_intent.validate(result, text, resolved, current_values=current)

    def test_relative_request_preserves_all_explicit_locks(self):
        result, _ = self.ask(text='保持材质不变，' + TEXT)
        self.assertIsInstance(result, ValueError)
        self.assertFalse(hasattr(result, 'proposal'))

    def test_invalid_duplicate_execution_wrapper_is_never_salvaged(self):
        response = reply(rows())
        data = response['content'][0]['input']
        data.update(status='apply', patch={'exposure': 1}, target_bindings=[], summary='调整。', unhandled=[])
        result, send = self.ask(responses=[response, response])
        self.assertIsInstance(result, ai_client.ResponseContractError)
        self.assertFalse(hasattr(result, 'proposal'))
        self.assertEqual(result.feedback['code'], 'decision_conflict')
        self.assertEqual(send.call_count, 2)

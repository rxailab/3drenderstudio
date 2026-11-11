"""Semantic protocol regressions. Transport is mocked; no paid calls occur."""
import copy
import json
from contextlib import ExitStack
from unittest.mock import patch

from PIL import Image
import ai_client
import semantic_planner
from intelligence_rules import InstructionOutcome
from planner import default_plan
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, local


def row(quote, patch_value=None, *, ident='r1', kind='visual_goal', domains=None,
        status='ready', bindings=None, protections=None, authorizations=None, depends_on=None):
    return {'id':ident,'quote':quote,'interpretation':'根据原要求选择可执行的摄影调整。',
            'kind':kind,'domains':['lighting'] if domains is None else domains,
            'depends_on':depends_on or [],'status':status,'protections':protections or [],
            'authorizations':authorizations or [],'patch':patch_value or {},
            'target_bindings':bindings or [],'summary':'调整具体参数并保留原有产品信息。'}


def binding(ids=None, count=0):
    return {'phrase':'模型识别的当前目标','purpose':'material','target_ids':ids or ['t1'],
            'expected_instances':count}


def wire(*rows, **extra):
    return {'protocol':'semantic_v2','requirements':list(rows),'notes':[],
            'summary':'按当前要求更新可执行参数。',**extra}


def response(value):
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':ai_client.TOOL_NAME,'input':value}]}


class SemanticPlannerTests(EvidenceFixture):
    def setUp(self):
        super().setUp()
        self.context['instruction_protocol']='semantic_v2'

    def request_semantic(self, text, reply, *, context=None, reference_path=None, plan=None):
        replies=reply if isinstance(reply,list) else [reply,reply]
        with patch('ai_client._post',side_effect=[response(value) for value in replies]) as send:
            result=ai_client.request_plan(default_plan() if plan is None else plan,text,ANALYSIS,CONFIG,
                image_path=self.png,context=self.context if context is None else context,
                reference_path=reference_path)
        return result,send

    def test_unfamiliar_multilingual_goal_reaches_model_whole_without_language_helpers(self):
        text='正面像被闷住了，给它透口气；make the front welcoming, sans changer la teinte'
        reply=wire(row(text,{'lighting':{'key_power':1.9,'fill_power':1.5,'key_azimuth':-35,'key_elevation':48}}))
        names=['ai_client.extract_protections','ai_client._instruction_requirements',
               'ai_client._requirement_domains','ai_client.material_clause','ai_client.general_image_goal',
               'ai_client.rule_patch','target_evidence.material_assignments',
               'target_evidence.resolve_bindings','target_evidence.validate_binding_coverage']
        with ExitStack() as stack:
            for name in names:stack.enter_context(patch(name,side_effect=AssertionError('Language keyword guard used: '+name)))
            result,send=self.request_semantic(text,reply)
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_azimuth'],-35)
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['instruction'],text)
        self.assertNotIn('requirements',facts)
        self.assertEqual(result['last_instruction']['source_spans'],[{'id':'r1','start':0,'end':len(text),'quote':text}])

    def test_model_selects_material_and_direction_without_finish_keyword_completion(self):
        text='柜体别像蒙了一层灰，让前脸精神些'
        reply=wire(row(text,{'object_material_overrides':[local(1,roughness=.22)],
                            'lighting':{'key_power':1.8,'key_azimuth':-30}},
                       domains=['material','lighting'],bindings=[binding()]))
        with patch('ai_client._validate_requirement_patch',side_effect=AssertionError('Legacy intent veto')):
            result,send=self.request_semantic(text,reply)
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['object_material_overrides'][0]['roughness'],.22)
        self.assertTrue(result['object_material_overrides'][0]['preserve_original'])
        self.assertNotIn('color',result['object_material_overrides'][0])

    def test_omitted_negation_or_number_cannot_pass_source_coverage(self):
        for text,quote in [('不要改颜色，亮一点','亮一点'),('灯光增加到1.8','灯光增加到'),
                           ('-0.5EV','0.5EV'),('50%','50')]:
            with self.subTest(text=text),patch('ai_client._post',return_value=response(wire(row(quote,{'exposure':.4})))) as send:
                with self.assertRaises(ai_client.ResponseContractError) as caught:
                    ai_client.request_plan(default_plan(),text,ANALYSIS,CONFIG,image_path=self.png,context=self.context)
                self.assertEqual(caught.exception.feedback['code'],'semantic_source_coverage')
                self.assertEqual(send.call_count,2)

    def test_structural_correction_reuses_original_unmodified_request_and_budget(self):
        text='前脸更立体一点'
        bad=wire(row(text,{'lighting':{'key_power':99}}))
        good=wire(row(text,{'lighting':{'key_power':1.85,'key_azimuth':-25}}))
        result,send=self.request_semantic(text,[bad,good])
        self.assertEqual(send.call_count,2)
        self.assertEqual(result['lighting']['key_power'],1.85)
        second=send.call_args_list[1].args[1]['messages'][0]['content']
        facts=json.loads(next(part['text'] for part in second if part['type']=='text' and '"instruction"' in part['text'] and 'current_plan' in part['text']))
        self.assertEqual(facts['instruction'],text)
        self.assertEqual(facts['current_plan']['lighting']['key_power'],default_plan()['lighting']['key_power'])
        correction=json.loads(second[-1]['text'])
        self.assertEqual(correction['previous_decision'],bad)

    def test_ready_lighting_is_partial_when_unlocated_material_row_cannot_execute(self):
        text='把那个边角修得更利落，前脸给点亮度'
        reply=wire(row('把那个边角修得更利落',domains=['material'],status='needs_clarification'),
                   row('前脸给点亮度',{'lighting':{'key_power':1.9}},ident='r2'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        outcome=caught.exception
        self.assertEqual(outcome.status,'partial_available')
        self.assertEqual(outcome.proposal['candidate_plan']['lighting']['key_power'],1.9)
        self.assertEqual(outcome.proposal['pending'][0]['quote'],'把那个边角修得更利落')

    def test_separate_grounded_material_can_apply_with_another_material_pending(self):
        text='这个门板收紧高光，那个内侧装饰再看看'
        reply=wire(row('这个门板收紧高光',{'object_material_overrides':[local(1,roughness=.18)]},
                       domains=['material'],bindings=[binding()]),
                   row('那个内侧装饰再看看',ident='r2',domains=['material'],status='needs_clarification'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'partial_available')
        self.assertEqual(caught.exception.proposal['candidate_plan']['object_material_overrides'][0]['roughness'],.18)

    def test_explicit_semantic_dependency_blocks_dependent_work(self):
        text='定位内侧装饰，再以它为中心打光'
        reply=wire(row('定位内侧装饰',domains=['material'],status='needs_clarification'),
                   row('再以它为中心打光',{'lighting':{'key_power':1.9}},ident='r2',depends_on=['r1']))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'needs_clarification')
        self.assertFalse(hasattr(caught.exception,'proposal'))
        self.assertEqual(len(caught.exception.pending),2)

    def test_dependency_cycle_or_unknown_id_is_a_contract_failure(self):
        for dependency in ['r1','r9']:
            with self.subTest(dependency=dependency),self.assertRaises(ai_client.ResponseContractError):
                self.request_semantic('前脸更亮',wire(row('前脸更亮',{'exposure':.4},depends_on=[dependency])))

    def test_satisfied_dependency_is_blocked_without_rejecting_independent_lighting(self):
        text='定位内侧装饰，再保持对应留白，前脸亮一些'
        reply=wire(row('定位内侧装饰',domains=['material'],status='needs_clarification'),
                   row('再保持对应留白',{'margin':default_plan()['margin']},ident='r2',domains=['framing'],
                       status='already_satisfied',depends_on=['r1']),
                   row('前脸亮一些',{'exposure':.4},ident='r3'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'partial_available')
        self.assertEqual([item['id'] for item in caught.exception.proposal['ready']],['r3'])
        self.assertEqual([item['id'] for item in caught.exception.proposal['pending']],['r1','r2'])

    def test_preservation_blocked_by_dependency_still_stops_all_mutation(self):
        text='定位原漆面，然后保持它的颜色，前脸亮一些'
        reply=wire(row('定位原漆面',domains=['material'],status='needs_clarification'),
                   row('然后保持它的颜色',ident='r2',kind='preservation',domains=[],
                       status='already_satisfied',protections=['preserve_color'],depends_on=['r1']),
                   row('前脸亮一些',{'exposure':.4},ident='r3'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'needs_clarification')
        self.assertFalse(hasattr(caught.exception,'proposal'))

    def test_global_preservation_stays_attached_to_both_partial_texts(self):
        text='不要动颜色，未知部分还需定位，前脸更亮'
        reply=wire(row('不要动颜色',kind='preservation',domains=[],status='already_satisfied',protections=['preserve_color']),
                   row('未知部分还需定位',ident='r2',domains=['material'],status='needs_clarification'),
                   row('前脸更亮',{'lighting':{'key_power':1.8}},ident='r3'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        proposal=caught.exception.proposal
        self.assertIn('不要动颜色',proposal['execution_text'])
        self.assertIn('不要动颜色',proposal['remaining_text'])
        self.assertEqual(proposal['candidate_plan']['last_instruction']['protections'],['preserve_color'])

    def test_unknown_preservation_blocks_all_ready_rows(self):
        text='原来的感觉别变，前脸亮一些'
        reply=wire(row('原来的感觉别变',kind='preservation',domains=[],status='needs_clarification'),
                   row('前脸亮一些',{'exposure':.4},ident='r2'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'needs_clarification')
        self.assertFalse(hasattr(caught.exception,'proposal'))

    def test_color_permission_cannot_be_borrowed_from_another_row(self):
        text='面板改成蓝色，柜门保持质感'
        reply=wire(row('面板改成蓝色',{'object_material_overrides':[local(1,color='#0000FF')]},
                       domains=['material'],bindings=[binding()],authorizations=[{'capability':'color_change','quote':'面板改成蓝色'}]),
                   row('柜门保持质感',{'object_material_overrides':[local(2,color='#0000FF')]},ident='r2',
                       domains=['material'],bindings=[binding(['t2'])],authorizations=[{'capability':'color_change','quote':'面板改成蓝色'}]))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'partial_available')
        changes=caught.exception.proposal['candidate_plan']['object_material_overrides']
        self.assertEqual([change['object_name'] for change in changes],['Mesh.001'])

    def test_preset_requires_material_and_color_permissions(self):
        text='轮胎改为黑色橡胶'
        wanted=row(text,{'object_material_overrides':[local(1,preset='rubber')]},domains=['material'],bindings=[binding()],
                   authorizations=[{'capability':'material_replacement','quote':'黑色橡胶'}])
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,wire(wanted))
        self.assertEqual(caught.exception.status,'needs_clarification')
        wanted['authorizations'].append({'capability':'color_change','quote':'黑色橡胶'})
        result,send=self.request_semantic(text,wire(wanted))
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['object_material_overrides'][0]['preset'],'rubber')

    def test_numeric_count_cannot_be_claimed_from_ray_object_rows(self):
        text='四个小轮都哑光一点，灯光亮一些'
        reply=wire(row('四个小轮都哑光一点',{'object_material_overrides':[local(n,roughness=.7) for n in range(1,5)]},
                       domains=['material'],bindings=[binding(['t1','t2','t3','t4'],count=4)]),
                   row('灯光亮一些',{'exposure':.5},ident='r2'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'partial_available')
        self.assertEqual(caught.exception.proposal['candidate_plan']['object_material_overrides'],[])
        self.assertEqual(caught.exception.proposal['candidate_plan']['exposure'],.5)

    def test_forged_target_and_extra_patch_fields_never_apply(self):
        text='这个表面再柔和一点，亮一些'
        for material in ({'object_material_overrides':[local(1,roughness=.2)],'code':'run_python()'},
                         {'object_material_overrides':[local(1,roughness=.2)]}):
            selected=binding(['t999']) if 'code' not in material else binding()
            reply=wire(row('这个表面再柔和一点',material,domains=['material'],bindings=[selected]),
                       row('亮一些',{'exposure':.3},ident='r2'))
            with self.subTest(material=material),self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
            self.assertEqual(caught.exception.status,'partial_available')
            self.assertEqual(caught.exception.proposal['candidate_plan']['object_material_overrides'],[])

    def test_material_identity_without_parameters_is_not_applied(self):
        text='这个表面更精神一点'
        reply=wire(row(text,{'object_material_overrides':[{'object_name':'Mesh.001','material_name':'Shared'}]},
                       domains=['material'],bindings=[binding()]))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,reply)
        self.assertEqual(caught.exception.status,'needs_clarification')

    def test_reference_is_not_read_or_sent_when_model_can_answer_without_it(self):
        reference=self.root/'private-reference.png';Image.new('RGB',(24,24),'blue').save(reference)
        text='不要用参考图，前脸透亮一些'
        reply=wire(row(text,{'lighting':{'key_power':1.8}}))
        with patch('semantic_planner._reference_evidence',side_effect=AssertionError('Reference unexpectedly read')):
            result,send=self.request_semantic(text,reply,reference_path=reference)
        contents=send.call_args.args[1]['messages'][0]['content']
        self.assertEqual(sum(part['type']=='image' for part in contents),1)
        self.assertFalse(result['last_instruction']['used_reference'])
        self.assertNotIn('reference_evidence',result['last_instruction'])

    def test_reference_context_request_uses_second_call_and_records_actual_file(self):
        reference=self.root/'reference.png';Image.new('RGB',(24,24),'blue').save(reference)
        text='请按参考图的柔和光照调整'
        context_request=wire(context_request='reference_image')
        complete=wire(row(text,{'lighting':{'key_power':1.8},'light_softness':1.7}))
        result,send=self.request_semantic(text,[context_request,complete],reference_path=reference)
        self.assertEqual(send.call_count,2)
        counts=[sum(part['type']=='image' for part in call.args[1]['messages'][0]['content']) for call in send.call_args_list]
        self.assertEqual(counts,[1,2])
        last=result['last_instruction']
        self.assertTrue(last['used_reference'])
        self.assertEqual(last['reference_evidence'],semantic_planner._reference_evidence(reference))

    def test_reference_context_cannot_gain_a_third_model_call(self):
        reference=self.root/'reference.png';Image.new('RGB',(24,24),'blue').save(reference)
        text='请按参考图调整'
        replies=[response(wire(context_request='reference_image'))]*3
        with patch('ai_client._post',side_effect=replies) as send,self.assertRaises(ai_client.ResponseContractError):
            ai_client.request_plan(default_plan(),text,ANALYSIS,CONFIG,image_path=self.png,
                                   context=self.context,reference_path=reference)
        self.assertEqual(send.call_count,2)

    def test_conflicting_absolute_values_fail_without_mutating_input(self):
        text='左边更亮，右边也更亮'
        reply=wire(row('左边更亮',{'lighting':{'key_power':1.8}}),
                   row('右边也更亮',{'lighting':{'key_power':1.4}},ident='r2'))
        plan=default_plan();before=copy.deepcopy(plan)
        with self.assertRaises(ai_client.ResponseContractError):self.request_semantic(text,reply,plan=plan)
        self.assertEqual(plan,before)

    def test_repeated_target_binding_across_rows_is_deduplicated(self):
        text='柜门光泽收紧，柜门金属感加强'
        reply=wire(row('柜门光泽收紧',{'object_material_overrides':[local(1,roughness=.2)]},
                       domains=['material'],bindings=[binding()]),
                   row('柜门金属感加强',{'object_material_overrides':[local(1,metallic=.45)]},
                       ident='r2',domains=['material'],bindings=[binding()]))
        result,send=self.request_semantic(text,reply)
        self.assertEqual(send.call_count,1)
        self.assertEqual(len(result['last_instruction']['target_bindings']),1)
        self.assertEqual(result['object_material_overrides'][0]['metallic'],.45)

    def test_conflicting_same_label_bindings_are_corrected_within_budget(self):
        text='柜门光泽收紧，柜门金属感加强'
        reply=wire(row('柜门光泽收紧',{'object_material_overrides':[local(1,roughness=.2)]},
                       domains=['material'],bindings=[binding()]),
                   row('柜门金属感加强',{'object_material_overrides':[local(2,metallic=.45)]},
                       ident='r2',domains=['material'],bindings=[binding(['t2'])]))
        with patch('ai_client._post',return_value=response(reply)) as send,self.assertRaises(ai_client.ResponseContractError) as caught:
            ai_client.request_plan(default_plan(),text,ANALYSIS,CONFIG,image_path=self.png,context=self.context)
        self.assertEqual(send.call_count,2)
        self.assertEqual(caught.exception.feedback['code'],'semantic_binding_conflict')

    def test_malformed_requirement_container_gets_contract_error_instead_of_exception(self):
        for value in [None,{},'unexpected',3]:
            with self.subTest(value=value),self.assertRaises(ai_client.ResponseContractError):
                self.request_semantic('透亮些',{**wire(),'requirements':value})

    def test_old_keyword_protocol_is_not_an_online_fallback(self):
        reply={'status':'apply','patch':{'exposure':.3},'summary':'change','notes':[],'unhandled':[]}
        with self.assertRaises(ai_client.ResponseContractError):self.request_semantic('通透些',reply)

    def measured_material_context(self,roughness=.22,linked=None):
        return {**self.context,'current_material_values':[{
            'object_name':'Mesh.001','material_name':'Shared','basis':'saved_scene_principled',
            'values':{'roughness':roughness},'linked_fields':linked or []}]}

    def test_new_override_equal_to_saved_scene_is_not_counted_as_changed(self):
        text='柜门的高光再收紧一点'
        unchanged=wire(row(text,{'object_material_overrides':[local(1,roughness=.22)]},
                           domains=['material'],bindings=[binding()]))
        context=self.measured_material_context(.2199999988)
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,unchanged,context=context)
        self.assertEqual(caught.exception.status,'needs_clarification')
        self.assertFalse(hasattr(caught.exception,'proposal'))
        changed=wire(row(text,{'object_material_overrides':[local(1,roughness=.14)]},
                         domains=['material'],bindings=[binding()]))
        result,send=self.request_semantic(text,[unchanged,changed],context=context)
        self.assertEqual(send.call_count,2)
        self.assertEqual(result['object_material_overrides'][0]['roughness'],.14)
        feedback=json.loads(send.call_args_list[1].args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(feedback['execution_correction']['issues'][0]['effect'],'known_unchanged')

    def test_actual_matching_material_can_be_satisfied_without_adding_override(self):
        text='柜门粗糙度设为0.22'
        reply=wire(row(text,{'object_material_overrides':[local(1,roughness=.22)]},
                       status='already_satisfied',domains=['material'],bindings=[binding()]))
        plan=default_plan();before=copy.deepcopy(plan)
        with self.assertRaises(InstructionOutcome) as caught:
            self.request_semantic(text,reply,context=self.measured_material_context(),plan=plan)
        self.assertEqual(caught.exception.status,'no_change')
        self.assertEqual(plan,before)

    def test_missing_or_linked_material_reading_cannot_prove_already_satisfied(self):
        text='柜门粗糙度设为0.22'
        plan=default_plan();plan['object_material_overrides']=[local(1,roughness=.22)]
        reply=wire(row(text,{'object_material_overrides':[local(1,roughness=.22)]},
                       status='already_satisfied',domains=['material'],bindings=[binding()]))
        for context in [self.context,self.measured_material_context(linked=['roughness'])]:
            with self.subTest(context=context),self.assertRaises(InstructionOutcome) as caught:
                self.request_semantic(text,reply,context=context,plan=plan)
            self.assertEqual(caught.exception.status,'needs_clarification')

    def test_measured_light_direction_overrules_missing_or_stale_plan(self):
        text='主光移到相机左前方'
        context={**self.context,'current_light_directions':[{'role':'key','azimuth':-35,'elevation':45}]}
        unchanged=wire(row(text,{'lighting':{'key_azimuth':-35}}))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,unchanged,context=context)
        self.assertEqual(caught.exception.status,'needs_clarification')
        satisfied=wire(row(text,{'lighting':{'key_azimuth':-35}},status='already_satisfied'))
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,satisfied,context=context)
        self.assertEqual(caught.exception.status,'no_change')
        with self.assertRaises(InstructionOutcome) as caught:self.request_semantic(text,satisfied)
        self.assertEqual(caught.exception.status,'needs_clarification')
        changed=wire(row(text,{'lighting':{'key_azimuth':-20}}))
        result,send=self.request_semantic(text,changed,context=context)
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_azimuth'],-20)

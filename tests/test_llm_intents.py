"""Semantic planning remains one bounded, offline request against real controls."""
import copy
import json
from unittest.mock import patch

import ai_client
from intelligence_rules import InstructionOutcome
from planner import default_plan
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, binding, decision, local
from test_partial_intent_planning import requirement, proposal
from test_decision_contract import wire


SCREENSHOT='灯光再亮一些，不要让物体背光'


def intent(kind='visual_goal',domains=None,protections=None,interpretation='补足主体正面的光线，让受光层次更清楚；以新预览检查效果。'):
    return {'kind':kind,'interpretation':interpretation,
            'domains':['lighting'] if domains is None else domains,'protections':protections or []}


def row(ident,value=None,semantic=None,status='ready',summary='已准备对应参数。',bindings=None):
    result=requirement(ident,status,value,bindings,summary)
    if semantic is not None:result['intent']=semantic
    return result


def screenshot_rows():
    return [row('r1',{'lighting':{'key_power':1.2}},intent()),
            row('r2',{'lighting':{'fill_power':1.3}},intent())]


def preserved(ident,locks=None,status=None,summary='保留产品原有配色。'):
    locks=['preserve_color'] if locks is None else locks
    return row(ident,semantic=intent('preservation',[],locks,summary),
               status=status or ('already_satisfied' if locks else 'needs_clarification'),summary=summary)


class LLMIntentTests(EvidenceFixture):
    def ask(self,text,response,plan=None):
        plan=default_plan() if plan is None else plan;before=copy.deepcopy(plan)
        responses=response if isinstance(response,list) else [response,response]
        with patch('ai_client._post',side_effect=responses) as send:
            try:
                result=ai_client.request_plan(plan,text,ANALYSIS,CONFIG,image_path=self.png,context=self.context)
            except ValueError as exc:result=exc
        self.assertEqual(plan,before);self.assertLessEqual(send.call_count,2)
        return result,send

    def test_exact_screenshot_is_interpreted_and_applied_in_one_call(self):
        result,send=self.ask(SCREENSHOT,wire(screenshot_rows()))
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_power'],1.2)
        self.assertEqual(result['lighting']['fill_power'],1.3)
        self.assertEqual(result['lighting']['rim_power'],default_plan()['lighting']['rim_power'])
        self.assertEqual(result['exposure'],default_plan()['exposure'])
        self.assertEqual([item['quote'] for item in result['last_instruction']['semantic_intents']],SCREENSHOT.split('，'))
        self.assertEqual(result['last_instruction']['semantic_constraints'],[])
        body=send.call_args.args[1]
        self.assertIn('Do not mistake every negative sentence',body['system'])
        self.assertIn('front key/fill',body['system'])
        schema=body['tools'][0]['input_schema']['properties']['requirement_results']['items']
        self.assertIn('intent',schema['properties']);self.assertNotIn('intent',schema['required'])

    def test_unknown_negative_visual_goals_use_model_semantics_without_new_synonyms(self):
        for text in ('别像剪影一样','不要拍得像躲在阴影里','别让正面黑','不要过曝'):
            with self.subTest(text=text):
                result,send=self.ask(text,wire([row('r1',{'lighting':{'fill_power':1.3}},intent())]))
                self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
                self.assertEqual(result['last_instruction']['semantic_intents'][0]['quote'],text)
                self.assertEqual(result['shots'],default_plan()['shots'])

    def test_literal_old_brightness_reply_can_choose_fill_without_forced_exposure(self):
        for semantic in (None,intent()):
            with self.subTest(semantic=semantic):
                result,send=self.ask('亮一点',wire([row('r1',{'lighting':{'fill_power':1.3}},semantic)]))
                self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
                self.assertEqual(result['exposure'],default_plan()['exposure'])
                self.assertEqual(result['lighting']['fill_power'],1.3)

    def test_unclassified_positive_goal_needs_explanation_then_runs(self):
        bad=wire([row('r1',{'lighting':{'fill_power':1.3}})])
        good=wire([row('r1',{'lighting':{'fill_power':1.3}},intent())])
        result,send=self.ask('稍微更有层次一点',[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'],'semantic_interpretation_required')

    def test_missing_negative_semantics_is_corrected_in_same_shared_budget(self):
        bad=screenshot_rows();bad[1].pop('intent')
        result,send=self.ask(SCREENSHOT,[wire(bad),wire(screenshot_rows())])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        first=send.call_args_list[0].args[1]['messages'][0]['content']
        second=send.call_args_list[1].args[1]['messages'][0]['content']
        self.assertEqual(second[:-1],first)
        self.assertEqual(result['lighting']['key_power'],1.2)

    def test_legacy_patch_cannot_silently_drop_unknown_negative_meaning(self):
        result,send=self.ask(SCREENSHOT,decision({'lighting':{'key_power':1.2,'fill_power':1.3}},[]))
        self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
        self.assertEqual(result.feedback['code'],'semantic_interpretation_required')
        self.assertFalse(hasattr(result,'proposal'))

    def test_unknown_real_preservation_blocks_conflicting_partial_work(self):
        text='灯光再亮一些，别破坏原来的感觉'
        pending=preserved('r2',[],summary='希望保留的是现有配色，还是镜头角度？')
        result,send=self.ask(text,wire([screenshot_rows()[0],pending]))
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'needs_clarification')
        self.assertEqual(send.call_count,1);self.assertFalse(hasattr(result,'proposal'))
        self.assertEqual(result.remaining_text,text)
        self.assertEqual(result.pending[0]['quote'],'别破坏原来的感觉')
        self.assertEqual(result.semantic_intents[-1]['kind'],'preservation')

    def test_resolved_preservation_alone_is_acknowledged_without_a_fake_edit(self):
        text='别把产品本来的配色折腾了'
        result,send=self.ask(text,wire([preserved('r1')]))
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'no_change')
        self.assertEqual(send.call_count,1);self.assertIn('preserve_color',result.protections)
        self.assertEqual(result.semantic_constraints,[text])
        self.assertFalse(hasattr(result,'proposal'))

    def test_empty_protection_list_cannot_claim_preservation_is_satisfied(self):
        bad=wire([preserved('r1',[],status='already_satisfied')])
        result,send=self.ask('别破坏原来的感觉',bad)
        self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
        self.assertEqual(result.feedback['code'],'semantic_preservation_unresolved')

    def test_visual_goal_cannot_claim_already_satisfied_from_current_values(self):
        bad=wire([row('r1',{'lighting':{'fill_power':1}},intent(),status='already_satisfied')])
        result,send=self.ask('别像剪影一样',bad)
        self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
        self.assertEqual(result.feedback['code'],'semantic_visual_unverified')

    def test_explicit_numeric_action_is_not_weakened_by_visual_goal_label(self):
        bad=wire([row('r1',{'lighting':{'key_power':1.1}},intent())])
        good=wire([row('r1',{'lighting':{'key_power':1.2}},intent())])
        result,send=self.ask('主光提高20%',[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['lighting']['key_power'],1.2)

    def test_named_nonnumeric_direction_changes_that_control(self):
        for text,wrong,good in (
            ('主光再亮一些',{'fill_power':1.3},{'key_power':1.2}),
            ('主光强度降低一点',{'key_power':1.2},{'key_power':.8}),
        ):
            with self.subTest(text=text):
                result,send=self.ask(text,[wire([row('r1',{'lighting':wrong},intent())]),
                                           wire([row('r1',{'lighting':good},intent())])])
                self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
                self.assertEqual(result['lighting']['key_power'],good['key_power'])

    def test_explicit_framing_number_cannot_be_laundered_as_visual_goal(self):
        semantic=intent(domains=['framing'])
        bad=wire([row('r1',{'margin':.3},semantic)])
        good=wire([row('r1',{'margin':.2},semantic)])
        result,send=self.ask('留白设为20%',[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['margin'],.2)

    def test_explicit_framing_action_cannot_be_relabeled_as_preservation(self):
        bad=wire([preserved('r1',['preserve_margin'])])
        good=wire([row('r1',{'margin':.2},intent('adjustment',['framing']))])
        result,send=self.ask('留白设为20%',[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['last_instruction']['protections'],[])

    def test_unknown_visual_phrase_cannot_authorize_material_replacement(self):
        bad=wire([row('r1',{'object_material_overrides':[local(1,preset='rubber')]},
                      intent(domains=['material']),bindings=[binding('产品',['t1'])])])
        result,send=self.ask('别让它看起来怪怪的',bad)
        self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
        self.assertEqual(result.feedback['code'],'semantic_scope_conflict')

    def test_explicit_material_requirement_cannot_be_substituted_by_lighting(self):
        bad=wire([row('r1',{'lighting':{'fill_power':1.2}},intent())])
        result,send=self.ask('上表面是拉丝不锈钢',bad)
        self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
        self.assertEqual(result.feedback['code'],'semantic_scope_conflict')

    def test_literal_part_rotation_still_requires_real_pose_identity(self):
        value={'pose':{'object':'Mesh.001','rotation':[0,0,90]}}
        result,send=self.ask('轮子转90度',decision(value,[binding('轮子',['t1'])]))
        self.assertIsInstance(result,InstructionOutcome);self.assertIn('姿态',str(result))
        self.assertFalse(hasattr(result,'proposal'));self.assertEqual(send.call_count,2)

    def test_program_camera_lock_cannot_be_removed_by_model_metadata(self):
        rows=[row('r1',{'shots':[{'id':'hero','elevation':22}]},intent(domains=['camera']))]
        result,send=self.ask('镜头不要动，别让正面黑',wire(rows))
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,1)
        self.assertIn('preserve_camera',result.protections);self.assertFalse(hasattr(result,'proposal'))

    def test_failed_attempt_semantic_locks_do_not_contaminate_corrected_response(self):
        text='优化灯光和镜头，别把产品本来的配色折腾了'
        photo=row('r1',{'shots':[{'id':'hero','elevation':22}],'lighting':{'key_power':1.2}},
                  intent(domains=['lighting','camera']))
        bad=wire([photo,preserved('r2',['preserve_camera'])])
        good=wire([photo,preserved('r2',['preserve_color'])])
        result,send=self.ask(text,[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['shots'][0]['elevation'],22)
        self.assertIn('preserve_color',result['last_instruction']['protections'])
        self.assertNotIn('preserve_camera',result['last_instruction']['protections'])

    def test_model_locks_cover_even_a_discardable_legacy_wrapper(self):
        text='亮一点，别改掉周围空出来的位置'
        rows=[row('r1',{'lighting':{'fill_power':1.2}},intent()),preserved('r2',['preserve_margin'])]
        bad=proposal(rows,patch={'lighting':{'fill_power':1.2},'margin':.3})
        result,send=self.ask(text,[bad,wire(rows)])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['margin'],default_plan()['margin'])
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'],'semantic_protection_conflict')

    def test_visual_intent_does_not_introduce_an_unnecessary_confirmation(self):
        bad=wire([row('r1',semantic=intent(),status='needs_clarification',summary='请说明要改哪一种光。')])
        good=wire([row('r1',{'lighting':{'fill_power':1.3}},intent())])
        result,send=self.ask('别像剪影一样',[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)

    def test_safe_diagnostics_record_enum_scopes_without_interpretation_text(self):
        secret='private user supplied interpretation'
        bad=wire([row('r1',semantic=intent(interpretation=secret),status='already_satisfied')])
        result,send=self.ask('别像剪影一样',bad)
        self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
        shapes=result.response_shapes
        self.assertNotIn(secret,json.dumps(shapes));self.assertEqual(shapes[0]['requirement_rows'][0]['intent_kind'],'visual_goal')
        self.assertEqual(shapes[0]['requirement_rows'][0]['intent_domains'],['lighting'])

    def test_malformed_semantic_data_uses_the_same_two_call_contract(self):
        for change in ({'kind':'run_code'},{'domains':['all']},{'protections':['ignore_guards']},
                       {'interpretation':''},{'interpretation':'x'*241}):
            with self.subTest(change=change):
                semantic=intent();semantic.update(change)
                bad=wire([row('r1',{'lighting':{'fill_power':1.3}},semantic)])
                result,send=self.ask('别像剪影一样',bad)
                self.assertIsInstance(result,ai_client.ResponseContractError);self.assertEqual(send.call_count,2)
                self.assertFalse(hasattr(result,'proposal'))

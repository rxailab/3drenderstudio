"""Actual light directions are distinct from intensity and camera controls."""
import copy
import json
from unittest.mock import patch

import ai_client
from intelligence_rules import (InstructionOutcome, extract_protections, enforce_protections,
                                instruction_protected_fields, restrict_patch_schema, rule_patch)
from lighting_controls import LIGHT_DIRECTION_LIMITS
from planner import default_plan
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG
from test_llm_intents import row, intent
from test_decision_contract import wire


class LightDirectionIntentTests(EvidenceFixture):
    def ask(self,text,response,plan=None,context=None):
        plan=default_plan() if plan is None else plan;before=copy.deepcopy(plan)
        responses=response if isinstance(response,list) else [response,response]
        with patch('ai_client._post',side_effect=responses) as send:
            try:
                result=ai_client.request_plan(plan,text,ANALYSIS,CONFIG,image_path=self.png,
                                             context=self.context if context is None else context)
            except ValueError as exc:result=exc
        self.assertEqual(plan,before);self.assertLessEqual(send.call_count,2)
        return result,send

    def direction(self,value,ident='r1',kind='adjustment'):
        return row(ident,{'lighting':value},intent(kind,interpretation='调整真实光源方向，保持镜头与产品姿态。'))

    def test_literal_light_angles_preserve_camera_and_do_not_create_defaults(self):
        plan=default_plan();before=copy.deepcopy(plan)
        value,_,locks=rule_patch(plan,'只改灯光，主光方位角30度，补光俯仰角35度，镜头角度不变',{'active_shot_id':'hero'})
        self.assertEqual(value,{'lighting':{'key_azimuth':30,'fill_elevation':35}})
        self.assertEqual(locks,['only_lighting','preserve_camera'])
        self.assertEqual(plan,before);self.assertFalse(set(plan['lighting']) & set(LIGHT_DIRECTION_LIMITS))
        self.assertIsNone(ai_client._photo_value(plan,'lighting.key_azimuth'))

    def test_light_direction_lock_is_narrow_and_camera_lock_allows_lamps(self):
        for phrase in ('灯光角度不变','保持光源方向','不要调整主光方位角'):
            with self.subTest(phrase=phrase):
                locks=extract_protections(phrase)
                self.assertEqual(locks,['preserve_light_direction'])
                self.assertEqual(set(instruction_protected_fields(locks)),{'lighting.'+k for k in LIGHT_DIRECTION_LIMITS})
                schema=restrict_patch_schema(ai_client.patch_schema(default_plan(),ANALYSIS),locks,'hero')
                self.assertFalse(set(schema['properties']['lighting']['properties']) & set(LIGHT_DIRECTION_LIMITS))
                enforce_protections({'lighting':{'key_power':1.4}},locks,'hero')
                with self.assertRaises(InstructionOutcome):enforce_protections({'lighting':{'key_azimuth':30}},locks,'hero')
        enforce_protections({'lighting':{'key_azimuth':30}},['preserve_camera'],'hero')

    def test_one_degree_is_an_actual_new_direction_not_default_multiplier_one(self):
        result,send=self.ask('主光方位角设为1度，镜头角度不变',wire([self.direction({'key_azimuth':1})]))
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_azimuth'],1);self.assertEqual(result['shots'],default_plan()['shots'])
        self.assertFalse({'key_elevation','fill_azimuth','fill_elevation','rim_azimuth','rim_elevation'} & set(result['lighting']))

    def test_model_cannot_replace_an_explicit_angle_by_brightness(self):
        bad=wire([self.direction({'key_power':1.4})]);good=wire([self.direction({'key_azimuth':20})])
        result,send=self.ask('调整主光角度',[bad,good])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['lighting']['key_azimuth'],20);self.assertEqual(result['lighting']['key_power'],1)

    def test_light_on_front_is_not_a_camera_move(self):
        self.assertEqual(ai_client._requirement_domains('把主光移到正面'),{'lighting'})
        result,send=self.ask('把主光移到正面，镜头角度不变',wire([self.direction({'key_azimuth':0})]))
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_azimuth'],0);self.assertEqual(result['shots'],default_plan()['shots'])

    def test_explicit_angle_and_range_are_corrected_in_the_existing_budget(self):
        for wrong in (20,181):
            with self.subTest(wrong=wrong):
                result,send=self.ask('主光方位角设为30度',[wire([self.direction({'key_azimuth':wrong})]),wire([self.direction({'key_azimuth':30})])])
                self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
                self.assertEqual(result['lighting']['key_azimuth'],30)

    def test_relative_angle_uses_measured_pose_without_persisting_other_axes(self):
        context={**self.context,'current_light_directions':[{'role':'key','azimuth':-20,'elevation':35,'object_name':'Key','camera':'Hero','position':[1,2,3],'target':[0,0,0]}]}
        result,send=self.ask('主光方位角增加20度',[wire([self.direction({'key_azimuth':5})]),wire([self.direction({'key_azimuth':0})])],context=context)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['lighting']['key_azimuth'],0);self.assertNotIn('key_elevation',result['lighting'])
        facts=json.loads(send.call_args_list[0].args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['request_context']['current_light_directions'],context['current_light_directions'])
        self.assertNotIn('key_azimuth',facts['current_plan']['lighting'])

    def test_unknown_relative_angle_never_assumes_one_or_zero_degrees(self):
        result,send=self.ask('主光方位角增加20度',wire([self.direction({'key_azimuth':21})]))
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'needs_clarification')
        self.assertIn('尚未测得',str(result));self.assertEqual(send.call_count,1)

    def test_inspected_missing_lamp_can_remain_pending_while_other_power_applies(self):
        context={**self.context,'light_direction_support':{'inspected':True,'available_roles':['fill'],'unavailable_roles':{'key':'无法确认主光'}}}
        response=wire([row('r1',status='needs_clarification',summary='实际场景检查尚未找到可核验的主光。'),self.direction({'fill_power':1.2},'r2')])
        result,send=self.ask('主光方位角设为30度，补光增强20%',response,context=context)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'partial_available');self.assertEqual(send.call_count,1)
        self.assertEqual(result.proposal['candidate_plan']['lighting']['fill_power'],1.2)
        self.assertEqual(result.proposal['remaining_text'],'主光方位角设为30度')
        props=send.call_args.args[1]['tools'][0]['input_schema']['properties']['requirement_results']['items']['properties']['patch']['properties']['lighting']['properties']
        self.assertIn('fill_azimuth',props);self.assertNotIn('key_azimuth',props);self.assertNotIn('rim_elevation',props)

    def test_no_inspection_record_does_not_fake_capability_restrictions(self):
        result,send=self.ask('主光方位角设为30度',wire([self.direction({'key_azimuth':30})]),context={**self.context,'light_direction_support':{'inspected':False,'available_roles':[]}})
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_azimuth'],30)

    def test_clear_brightness_request_retries_an_invisible_nudge(self):
        response=[wire([row('r1',{'lighting':{'key_power':1.03}},intent())]),wire([row('r1',{'lighting':{'key_power':1.4}},intent())])]
        result,send=self.ask('灯光明显亮一些',response)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['lighting']['key_power'],1.4);self.assertEqual(result['lighting']['fill_power'],1)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'],'photography_adjustment_too_small')

    def test_gentle_or_explicit_small_change_keeps_user_priority(self):
        for text,value in (('灯光微调亮一些',1.03),('主光提高5%',1.05)):
            with self.subTest(text=text):
                result,send=self.ask(text,wire([row('r1',{'lighting':{'key_power':value}},intent())]))
                self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
                self.assertEqual(result['lighting']['key_power'],value)

    def test_too_small_light_change_keeps_independent_framing_after_two_calls(self):
        response=wire([row('r1',{'lighting':{'key_power':1.03}},intent()),
                       row('r2',{'margin':.2},intent('adjustment',['framing']))])
        result,send=self.ask('灯光明显亮一些，留白设为20%',response)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'partial_available')
        self.assertEqual(send.call_count,2);self.assertEqual(result.proposal['candidate_plan']['margin'],.2)
        self.assertEqual(result.proposal['candidate_plan']['lighting']['key_power'],1)
        self.assertEqual(result.proposal['remaining_text'],'灯光明显亮一些')

    def test_direction_delta_uses_measured_camera_relative_angle_not_a_fake_base(self):
        context={**self.context,'current_light_directions':[{'role':'key','azimuth':170,'elevation':35}]}
        response=[wire([row('r1',{'lighting':{'key_azimuth':172}},intent())]),wire([row('r1',{'lighting':{'key_azimuth':-170}},intent())])]
        result,send=self.ask('灯光效果要明显一些',response,context=context)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        self.assertEqual(result['lighting']['key_azimuth'],-170)

    def test_directions_and_visible_change_guidance_are_in_the_same_prompt(self):
        result,send=self.ask('调整灯光角度',wire([self.direction({'fill_azimuth':-25})]))
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        prompt=send.call_args.args[1]['system']
        for phrase in ('DEGREES, never power multipliers','25-50 percent','15-35 degree','Absent light directions have NO default','preserve_light_direction','light_direction_support.inspected'):
            self.assertIn(phrase,prompt)

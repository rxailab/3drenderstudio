"""Shared Chinese predicates and actionable visual goals, without live AI."""
import copy
import json
import unittest
from unittest.mock import patch

import ai_client
import target_evidence
from intelligence_rules import InstructionOutcome, instruction_clauses, general_image_goal
from planner import default_plan
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, binding, local


GROUP='钥匙孔、钥匙、轮子的支架都是不锈钢反光的'
QUOTES=['钥匙孔都是不锈钢反光的','钥匙都是不锈钢反光的','轮子的支架都是不锈钢反光的']


def row(ident,patch_value=None,bindings=None,status='ready',summary='已准备具体参数，效果以预览为准。'):
    return {'id':ident,'status':status,'summary':summary,'patch':patch_value or {},'target_bindings':bindings or []}


def response(rows):
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':ai_client.TOOL_NAME,
            'input':{'requirement_results':rows,'notes':[]}}]}


class SharedPredicateParsingTests(unittest.TestCase):
    def test_three_subjects_inherit_the_exact_explicit_material(self):
        for separator in ('、','，',',','和','以及'):
            with self.subTest(separator=separator):
                text=separator.join(['钥匙孔','钥匙','轮子的支架'])+'都是不锈钢反光的'
                self.assertEqual(target_evidence.material_assignments(text),[
                    {'phrase':'钥匙孔','value':'不锈钢反光的'},
                    {'phrase':'钥匙','value':'不锈钢反光的'},
                    {'phrase':'轮子的支架','value':'不锈钢反光的'}])
                requirements,constraints=ai_client._instruction_requirements(text)
                self.assertEqual([item['id'] for item in requirements],['r1','r2','r3'])
                self.assertEqual([item['quote'] for item in requirements],QUOTES)
                self.assertTrue(all(item['source_quote']==text for item in requirements))
                self.assertEqual(constraints,[])

    def test_source_quote_is_literal_while_expanded_retry_clauses_stay_complete(self):
        text='让整个物体更好看，钥匙孔， 钥匙，轮子的支架都是不锈钢反光的，灯光柔和一点'
        requirements,_=ai_client._instruction_requirements(text)
        self.assertEqual(len(requirements),5)
        self.assertEqual([item['quote'] for item in requirements][1:4],QUOTES)
        for item in requirements[1:4]:self.assertIn(item['source_quote'],text)
        pending=','.join(item['quote'] for item in requirements[2:4])
        retry,_=ai_client._instruction_requirements(pending)
        self.assertEqual([item['quote'] for item in retry],QUOTES[1:])

    def test_explicit_different_materials_never_inherit_the_next_predicate(self):
        text='钥匙孔是黑色塑料，钥匙和轮子的支架都是不锈钢反光的'
        assignments=target_evidence.material_assignments(text)
        self.assertEqual([item['value'] for item in assignments],['黑色塑料','不锈钢反光的','不锈钢反光的'])
        requirements,_=ai_client._instruction_requirements(text)
        self.assertEqual(requirements[0]['quote'],'钥匙孔是黑色塑料')
        self.assertNotIn('source_quote',requirements[0])

    def test_counts_stay_on_their_original_subject(self):
        text='两个钥匙孔、四个轮子的支架都是不锈钢反光的'
        requirements,_=ai_client._instruction_requirements(text)
        self.assertEqual([item['quote'] for item in requirements],['两个钥匙孔都是不锈钢反光的','四个轮子的支架都是不锈钢反光的'])
        self.assertEqual(target_evidence._requested_count(requirements[0]['quote'],'两个钥匙孔'),{2})
        self.assertEqual(target_evidence._requested_count(requirements[1]['quote'],'四个轮子的支架'),{4})

    def test_protection_and_other_action_are_not_material_list_members(self):
        text='保留原色，优化灯光，钥匙孔，钥匙都是不锈钢反光的'
        requirements,constraints=ai_client._instruction_requirements(text)
        self.assertEqual(constraints,['保留原色'])
        self.assertEqual([item['quote'] for item in requirements],['优化灯光','钥匙孔都是不锈钢反光的','钥匙都是不锈钢反光的'])
        self.assertEqual([item['phrase'] for item in target_evidence.material_assignments(text)],['钥匙孔','钥匙'])

    def test_material_predicate_does_not_cross_sentence_or_negative_scope(self):
        for text in ('钥匙孔；钥匙是不锈钢反光的','钥匙孔。钥匙是不锈钢反光的','钥匙孔不要改，钥匙是不锈钢反光的'):
            with self.subTest(text=text):
                self.assertEqual(target_evidence.material_assignments(text),[{'phrase':'钥匙','value':'不锈钢反光的'}])

    def test_photography_subjects_do_not_become_material_targets(self):
        self.assertEqual(target_evidence.material_assignments('背景是白色，灯光，钥匙是不锈钢反光的'),
                         [{'phrase':'钥匙','value':'不锈钢反光的'}])

    def test_generic_image_goals_choose_photography_without_authorizing_product_changes(self):
        for text in ('让整个物体更好看','请让产品更真实一点','优化画面的整体观感'):
            with self.subTest(text=text):
                self.assertTrue(general_image_goal(text))
                self.assertEqual(ai_client._requirement_domains(text),{'lighting','camera'})
                self.assertTrue(ai_client._flexible_photography(text))
        for text in ('不要让整个物体更好看','让钥匙变成红色','让这个部件更好看','让整个物体更好看但不要调整灯光'):
            self.assertFalse(general_image_goal(text))


class SharedPredicateDecisionTests(EvidenceFixture):
    def ask(self,text,rows,plan=None):
        before=copy.deepcopy(plan or default_plan())
        current=copy.deepcopy(before)
        replies=rows if isinstance(rows,tuple) else (rows,rows)
        with patch.object(ai_client,'_post',side_effect=[response(value) for value in replies]) as send:
            try:result=ai_client.request_plan(current,text,ANALYSIS,CONFIG,image_path=self.png,context=self.context)
            except ValueError as exc:result=exc
        self.assertEqual(current,before);self.assertLessEqual(send.call_count,2)
        return result,send

    def test_grounded_key_and_lock_can_complete_while_bracket_stays_pending(self):
        rows=[row('r1',{'object_material_overrides':[local(5,preset='polished_metal')]},[binding('钥匙孔',['t5'])]),
              row('r2',{'object_material_overrides':[local(6,preset='polished_metal')]},[binding('钥匙',['t6'])]),
              row('r3',status='needs_clarification',summary='还有一处支架表面无法确认到可独立修改的材质槽。')]
        result,send=self.ask(GROUP,rows)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'partial_available')
        proposal=result.proposal
        self.assertEqual([item['quote'] for item in proposal['ready']],QUOTES[:2])
        self.assertTrue(all(item['source_quote']==GROUP for item in proposal['ready']+proposal['pending']))
        self.assertEqual(proposal['remaining_text'],QUOTES[2])
        self.assertEqual({item['object_name'] for item in proposal['candidate_plan']['object_material_overrides']},{'Mesh.005','Mesh.006'})
        self.assertTrue(all(item['roughness']==.09 for item in proposal['candidate_plan']['object_material_overrides']))
        self.assertEqual(send.call_count,1)
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['user_requirements'][0]['material_targets'],[{'phrase':'钥匙孔','value':'不锈钢反光的'}])
        prompt=send.call_args.args[1]['system']
        self.assertIn('mounting plate and axle',prompt);self.assertIn('exclude rubber tires/treads',prompt)
        self.assertNotIn('applies NOTHING until',prompt)

    def test_complete_list_requires_grounding_each_independent_subject(self):
        rows=[row('r1',{'object_material_overrides':[local(5,preset='polished_metal')]},[binding('钥匙孔',['t5'])]),
              row('r2',{'object_material_overrides':[local(6,preset='polished_metal')]},[binding('钥匙',['t6'])]),
              row('r3',{'object_material_overrides':[local(2,preset='polished_metal'),local(3,preset='polished_metal')]},[binding('轮子的支架',['t2','t3'])])]
        result,send=self.ask(GROUP,rows)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(len(result['object_material_overrides']),4)
        self.assertEqual(result['material_overrides'],[])

    def test_a_counted_subject_is_never_silently_shortened(self):
        text='两个钥匙孔、钥匙都是不锈钢反光的'
        rows=[row('r1',{'object_material_overrides':[local(5,preset='polished_metal')]},[binding('两个钥匙孔',['t5'])]),
              row('r2',{'object_material_overrides':[local(6,preset='polished_metal')]},[binding('钥匙',['t6'])])]
        result,send=self.ask(text,rows)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,2)
        self.assertNotIsInstance(result,dict)
        self.assertFalse(hasattr(result,'proposal'))

    def test_broad_beauty_request_accepts_a_real_lighting_attempt(self):
        rows=[row('r1',{'lighting':{'rim_power':1.25}})]
        result,send=self.ask('让整个物体更好看',rows)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['rim_power'],1.25)
        self.assertEqual(result['shots'],default_plan()['shots'])
        self.assertEqual(result['object_material_overrides'],[])
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['user_requirements'][0]['goal_type'],'visual_improvement_attempt')

    def test_broad_pending_goal_reuses_ready_lighting_once(self):
        text='让整个物体更好看，主光强度设为1.3'
        rows=[row('r1',status='needs_clarification',summary='好看是主观要求，无法确定性验证，请说明具体方面。'),
              row('r2',{'lighting':{'key_power':1.3}})]
        result,send=self.ask(text,rows)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['lighting']['key_power'],1.3)
        self.assertNotIn('好看是主观',json.dumps(result,ensure_ascii=False))

    def test_broad_pending_goal_still_keeps_true_material_uncertainty(self):
        text='让整个物体更好看，灯光柔和一点，钥匙是不锈钢反光的'
        rows=[row('r1',status='needs_clarification',summary='好看是主观要求，请说明目标。'),
              row('r2',{'light_softness':1.4}),
              row('r3',status='needs_clarification',summary='钥匙的独立表面暂未定位。')]
        result,send=self.ask(text,rows)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'partial_available')
        self.assertEqual(len(result.proposal['ready']),2)
        self.assertEqual(result.proposal['remaining_text'],'钥匙是不锈钢反光的')
        self.assertEqual(result.proposal['candidate_plan']['light_softness'],1.4)
        self.assertEqual(send.call_count,1)

    def test_subjective_quality_cannot_be_falsely_claimed_already_satisfied(self):
        rows=[row('r1',{'lighting':{'key_power':1}},status='already_satisfied')]
        result,send=self.ask('让整个物体更好看',rows)
        self.assertIsInstance(result,ValueError);self.assertEqual(send.call_count,2)
        self.assertFalse(hasattr(result,'proposal'))

    def test_all_satisfied_materials_keep_pending_and_provenance_without_a_fake_diff(self):
        plan=default_plan();plan['object_material_overrides']=[local(5,preset='polished_metal',roughness=.09,preserve_original=False,source='user')]
        text='钥匙孔、钥匙都是不锈钢反光的'
        rows=[row('r1',{'object_material_overrides':[local(5,preset='polished_metal',roughness=.09)]},[binding('钥匙孔',['t5'])],status='already_satisfied'),
              row('r2',status='needs_clarification',summary='钥匙的表面暂未定位。')]
        result,send=self.ask(text,rows,plan)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(result.status,'partial_available')
        self.assertEqual(result.proposal['ready'][0]['status'],'already_satisfied')
        self.assertEqual(result.proposal['ready'][0]['source_quote'],text)
        self.assertEqual(result.proposal['remaining_text'],'钥匙都是不锈钢反光的')
        self.assertEqual(result.proposal['candidate_plan']['object_material_overrides'],plan['object_material_overrides'])
        self.assertEqual(send.call_count,1)


if __name__=='__main__':unittest.main()

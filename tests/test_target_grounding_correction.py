"""Bounded target corrections use real local fixtures and mocked AI transport."""
import copy
import json
from unittest.mock import patch

import ai_client
from intelligence_rules import InstructionOutcome
from planner import default_plan
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, binding, decision, local
from test_partial_intent_planning import proposal, requirement


TEXT='四个轮子都要是黑色橡胶'


def wheel_patch(count=4):
    return {'object_material_overrides':[local(number,preset='rubber',color='#151515')
                                         for number in range(1,count+1)]}


class TargetCorrectionTests(EvidenceFixture):
    def ask(self,responses,text=TEXT,selections=None,context=None):
        plan=default_plan();before=copy.deepcopy(plan)
        with patch('ai_client._post',side_effect=responses) as send:
            try:
                result=ai_client.request_plan(plan,text,ANALYSIS,CONFIG,image_path=self.png,
                                             context=context or self.context,selections=selections)
            except ValueError as exc:
                result=exc
        self.assertEqual(plan,before)
        return result,send

    def assert_pending(self,result,text=TEXT):
        self.assertIsInstance(result,InstructionOutcome)
        self.assertEqual(result.status,'needs_clarification')
        self.assertEqual(result.remaining_text,text)
        self.assertTrue(result.pending)
        self.assertEqual(result.pending[0]['quote'],text.split('，')[0])
        self.assertEqual(result.pending[0]['action'],'locate_part')
        self.assertFalse(hasattr(result,'proposal'))

    def test_missing_bindings_are_corrected_before_returning_failure(self):
        bad=decision(wheel_patch(),[]);good=decision(wheel_patch(),[binding('四个轮子')])
        result,send=self.ask([bad,good])
        self.assertIsInstance(result,dict)
        self.assertEqual(len(result['object_material_overrides']),4)
        self.assertEqual(send.call_count,2)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['target_correction']['code'],'target_grounding_incomplete')
        self.assertEqual(correction['target_correction']['requirements'],[{'id':'r1','quote':TEXT}])
        self.assertIn('SAME original current_plan',correction['instruction'])

    def test_repeated_missing_bindings_return_actionable_single_clause(self):
        bad=decision(wheel_patch(),[])
        result,send=self.ask([bad,bad])
        self.assert_pending(result)
        self.assertEqual(send.call_count,2)
        self.assertEqual(result.model,CONFIG['model'])
        self.assertTrue(result.used_image)

    def test_empty_no_change_cannot_claim_four_material_targets_already_satisfied(self):
        bad=decision({},[],status='no_change')
        result,send=self.ask([bad,bad])
        self.assert_pending(result)
        self.assertEqual(send.call_count,2)

    def test_false_no_change_can_be_corrected_to_complete_grounded_material_patch(self):
        result,send=self.ask([decision({},[],status='no_change'),
                              decision(wheel_patch(),[binding('四个轮子')])])
        self.assertIsInstance(result,dict)
        self.assertEqual(len(result['object_material_overrides']),4)
        self.assertEqual(send.call_count,2)

    def test_matching_grounded_material_patch_still_returns_verified_no_change(self):
        # Use a previously applied real plan, including preserve_original=False;
        # a raw sparse override is not yet the same effective material state.
        plan=ai_client.apply_patch(default_plan(),wheel_patch(),ANALYSIS)
        with patch('ai_client._post',return_value=decision(wheel_patch(),[binding('四个轮子')])) as send:
            with self.assertRaises(InstructionOutcome) as caught:
                ai_client.request_plan(plan,TEXT,ANALYSIS,CONFIG,image_path=self.png,context=self.context)
        self.assertEqual(caught.exception.status,'no_change')
        self.assertEqual(send.call_count,1)

    def test_missing_patch_for_bound_wheel_still_cannot_apply_subset(self):
        bad=decision(wheel_patch(2),[binding('四个轮子')])
        result,send=self.ask([bad,bad])
        self.assert_pending(result)
        self.assertIn('已定位目标未包含',str(result))
        self.assertEqual(send.call_count,2)

    def test_partial_ready_grounding_uses_same_correction_budget(self):
        text=TEXT+'，顶部平面是拉丝不锈钢'
        bad_rows=[requirement('r1',value=wheel_patch(),bindings=[]),
                  requirement('r2','needs_clarification',summary='请确认顶部平面。')]
        good_rows=copy.deepcopy(bad_rows);good_rows[0]['target_bindings']=[binding('四个轮子')]
        result,send=self.ask([proposal(bad_rows),proposal(good_rows)],text)
        self.assertEqual(result.status,'partial_available')
        self.assertEqual(result.proposal['remaining_text'],'顶部平面是拉丝不锈钢')
        self.assertEqual(len(result.proposal['candidate_plan']['object_material_overrides']),4)
        self.assertEqual(send.call_count,2)

    def test_bad_partial_ready_does_not_create_a_proposal(self):
        text=TEXT+'，顶部平面是拉丝不锈钢'
        rows=[requirement('r1',value=wheel_patch(),bindings=[]),
              requirement('r2','needs_clarification',summary='请确认顶部平面。')]
        bad=proposal(rows);result,send=self.ask([bad,bad],text)
        self.assert_pending(result,text)
        self.assertEqual(send.call_count,2)

    def test_honest_unresolved_response_gets_action_without_an_extra_call(self):
        result,send=self.ask([decision({},[],status='needs_clarification')])
        self.assert_pending(result)
        self.assertEqual(send.call_count,1)

    def test_selected_full_apply_cannot_turn_four_wheels_into_two(self):
        selected=[{'object_name':'Mesh.%03d'%n,'material_name':'Shared'} for n in range(1,5)]
        bad=decision(wheel_patch(2),[]);result,send=self.ask([bad,bad],selections=selected)
        self.assert_pending(result)
        self.assertIn('数量尚未完整覆盖',str(result))
        self.assertEqual(send.call_count,2)

    def test_correct_selected_count_still_requires_all_material_types(self):
        selected=[{'object_name':'Mesh.%03d'%n,'material_name':'Shared'} for n in range(1,5)]
        value=wheel_patch();value['object_material_overrides'][3]['preset']='polished_metal'
        bad=decision(value,[]);result,send=self.ask([bad,bad],selections=selected)
        self.assert_pending(result)
        self.assertIn('材质类型',str(result))
        self.assertEqual(send.call_count,2)

    def test_correct_grounding_still_requires_requested_color(self):
        value=wheel_patch();value['object_material_overrides'][3].pop('color')
        bad=decision(value,[binding('四个轮子')]);result,send=self.ask([bad,bad])
        self.assert_pending(result)
        self.assertIn('颜色要求',str(result))
        self.assertEqual(send.call_count,2)

    def test_black_rubber_cannot_be_a_white_or_metallic_parameter_patch(self):
        for fields in ({'color':'#FFFFFF'},{'color':'#10104F'},{'metallic':1}):
            with self.subTest(fields=fields):
                value=wheel_patch();value['object_material_overrides'][0].update(fields)
                bad=decision(value,[binding('四个轮子')]);result,send=self.ask([bad,bad])
                self.assert_pending(result)
                self.assertEqual(send.call_count,2)

    def test_wrong_black_value_gets_one_bounded_correction(self):
        value=wheel_patch();value['object_material_overrides'][0]['color']='#FFFFFF'
        result,send=self.ask([decision(value,[binding('四个轮子')]),
                              decision(wheel_patch(),[binding('四个轮子')])])
        self.assertEqual(result['object_material_overrides'][0]['color'],'#151515')
        self.assertEqual(send.call_count,2)

    def test_black_and_white_accept_neutral_shades_but_not_each_other(self):
        self.assertTrue(ai_client._neutral_color_matches('#202024','黑色'))
        self.assertTrue(ai_client._neutral_color_matches('#F7F3EF','白色'))
        self.assertFalse(ai_client._neutral_color_matches('#F7F3EF','黑色'))
        self.assertFalse(ai_client._neutral_color_matches('#202024','白色'))

    def test_replacing_metal_preset_drops_inherited_metal_channels(self):
        plan=default_plan()
        plan['object_material_overrides']=[local(1,preset='polished_metal',metallic=1,
                                                transmission=.2,roughness=.03,color='#AAAAAA')]
        changed=ai_client.apply_patch(plan,{'object_material_overrides':[local(1,preset='rubber',color='#202024')]},ANALYSIS)
        slot=changed['object_material_overrides'][0]
        self.assertEqual(slot['preset'],'rubber')
        self.assertEqual(slot['color'],'#202024')
        self.assertFalse(slot['preserve_original'])
        for key in ('metallic','transmission','roughness'):self.assertNotIn(key,slot)
        self.assertEqual(plan['object_material_overrides'][0]['metallic'],1)

    def test_global_preset_replacement_drops_inherited_channels_and_textures(self):
        plan=default_plan()
        plan['material_overrides']=[{'name':'Shared','preset':'polished_metal','color':'#AAAAAA',
                                    'metallic':1,'roughness':.02,'textures':{'normal':'fixture-normal.png'}}]
        changed=ai_client.apply_patch(plan,{'material_overrides':[{'name':'Shared','preset':'rubber'}]},ANALYSIS)
        slot=changed['material_overrides'][0]
        self.assertEqual(slot['color'],'#AAAAAA')
        self.assertNotIn('textures',slot)
        self.assertNotIn('metallic',slot)
        self.assertNotIn('roughness',slot)

    def test_scalar_only_and_same_preset_edits_preserve_unrelated_channels(self):
        plan=default_plan()
        plan['object_material_overrides']=[local(1,preset='rubber',metallic=0,roughness=.65,color='#202024')]
        changed=ai_client.apply_patch(plan,{'object_material_overrides':[local(1,roughness=.7)]},ANALYSIS)
        slot=changed['object_material_overrides'][0]
        self.assertEqual((slot['preset'],slot['metallic'],slot['color']),('rubber',0,'#202024'))
        same=ai_client.apply_patch(plan,{'object_material_overrides':[local(1,preset='rubber',color='#151515')]},ANALYSIS)
        self.assertEqual(same['object_material_overrides'][0]['roughness'],.65)

    def test_same_rubber_preset_removes_invalid_inherited_metallic_only(self):
        plan=default_plan()
        plan['object_material_overrides']=[local(1,preset='rubber',metallic=1,roughness=.65,color='#202024')]
        changed=ai_client.apply_patch(plan,{'object_material_overrides':[local(1,preset='rubber')]},ANALYSIS)
        slot=changed['object_material_overrides'][0]
        self.assertNotIn('metallic',slot)
        self.assertEqual(slot['roughness'],.65)

    def test_same_preset_color_edit_preserves_normal_map_and_replaces_color_channel(self):
        plan=default_plan()
        plan['object_material_overrides']=[local(1,preset='rubber',roughness=.65,color='#888888',
                                                textures={'normal':'fixture-normal.png','base_color':'fixture-color.png'})]
        changed=ai_client.apply_patch(plan,{'object_material_overrides':[local(1,preset='rubber',color='#151515')]},ANALYSIS)
        slot=changed['object_material_overrides'][0]
        self.assertEqual(slot['textures'],{'normal':'fixture-normal.png'})
        self.assertEqual(slot['roughness'],.65)

    def test_schema_and_target_corrections_share_two_calls_total(self):
        first=decision(wheel_patch(),[binding('四个轮子')]);first['content'][0]['input']['surprise']='not permitted'
        second=decision(wheel_patch(),[])
        result,send=self.ask([first,second])
        self.assert_pending(result)
        self.assertEqual(send.call_count,2)

    def test_invalid_target_ids_end_as_pending_without_relaxing_identity(self):
        bad=decision(wheel_patch(),[binding('四个轮子',['t1','t2','t3','t999'])])
        result,send=self.ask([bad,bad])
        self.assert_pending(result)
        self.assertEqual(send.call_count,2)

    def test_target_retry_does_not_repeat_relative_exposure(self):
        text=TEXT+'，曝光提高0.3EV'
        value={**wheel_patch(),'exposure':.3}
        result,send=self.ask([decision(value,[]),decision(value,[binding('四个轮子')])],text)
        self.assertEqual(result['exposure'],.3)
        self.assertEqual(send.call_count,2)
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-2]['text'])
        self.assertEqual(facts['current_plan']['exposure'],0)

    def test_private_catalog_paths_are_not_sent_as_facts(self):
        context={**self.context,'_target_image_paths':[{'label':'PART_CATALOG_1','path':'/private/local/image.png'}],
                 'target_seeds':[{'object_name':'Mesh.001','material_name':'Shared'}]}
        result,send=self.ask([decision(wheel_patch(),[binding('四个轮子')])],context=context)
        self.assertIsInstance(result,dict)
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertNotIn('_target_image_paths',facts['request_context'])
        self.assertEqual(facts['request_context']['target_seeds'],context['target_seeds'])

    def test_catalog_images_are_labeled_without_exposing_file_paths(self):
        body=ai_client._payload(CONFIG,'system',{'fact':'value'},evidence_images=[{'label':'PART_CATALOG_1','path':self.png}])
        content=body['messages'][0]['content']
        self.assertTrue(content[0]['text'].startswith('PART_CATALOG_1:'))
        self.assertEqual(content[1]['type'],'image')
        self.assertNotIn(str(self.png),json.dumps(body))

    def test_invalid_or_missing_catalog_pages_never_reach_transport(self):
        for images in ([{'label':'USER_INSTRUCTION','path':self.png}],
                       [{'label':'PART_CATALOG_1','path':self.png}]*2,
                       [{'label':'PART_CATALOG_1','path':self.png.parent/'absent.png'}]):
            with self.subTest(images=images),self.assertRaises(ValueError):
                ai_client._payload(CONFIG,'system',{},evidence_images=images)

    def catalog_context(self,placements):
        targets=[]
        for index,centers in enumerate(placements,1):
            components=[{'bounds':{'min':[center,0,0],'max':[center+1,1,1]},'triangle_count':24}
                        for center in centers]
            targets.append({'id':'t'+str(index),'object_name':'Mesh.%03d'%index,'material_name':'Shared',
                            'geometry_digest':'a'*64,'components':components,
                            'bounds':{'min':[min(centers),0,0],'max':[max(centers)+1,1,1]},
                            'thumbnail':{'page':0,'rect':[0,(index-1)/10,1,.1]}})
        evidence={'method':'scene_part_catalog','generation_version':1,
                  'source_snapshot':self.evidence['source_snapshot'],
                  'catalog_pages':[{'label':'PART_CATALOG_1','width':1600,'height':1200}],
                  'targets':targets,'truncated':False}
        return {**self.context,'target_evidence':evidence,
                '_target_image_paths':[{'label':'PART_CATALOG_1','path':self.png}]}

    def test_catalog_four_disconnected_tires_in_one_mesh_use_physical_count(self):
        context=self.catalog_context([[0,3,6,9]])
        result,send=self.ask([decision(wheel_patch(1),[binding('四个轮子',['t1'])])],context=context)
        self.assertIsInstance(result,dict)
        self.assertEqual(result['last_instruction']['target_bindings'][0]['instance_count'],4)
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        target=facts['request_context']['target_evidence']['targets'][0]
        self.assertEqual(target['component_count'],4)
        self.assertNotIn('geometry_digest',target)
        self.assertNotIn('components',target)
        self.assertEqual(len(target['component_bounds']),4)
        self.assertNotIn('_target_image_paths',facts['request_context'])

    def test_catalog_split_tires_count_four_groups_across_six_meshes(self):
        context=self.catalog_context([[0],[0],[3],[3],[6],[9]])
        value=wheel_patch(6);ids=['t'+str(index) for index in range(1,7)]
        result,send=self.ask([decision(value,[binding('四个轮子',ids)])],context=context)
        self.assertIsInstance(result,dict)
        self.assertEqual(len(result['object_material_overrides']),6)
        self.assertEqual(result['last_instruction']['target_bindings'][0]['instance_count'],4)
        self.assertEqual(send.call_count,1)

    def test_catalog_split_meshes_cannot_fake_four_spatially_distinct_tires(self):
        context=self.catalog_context([[0],[0],[3],[3]])
        bad=decision(wheel_patch(),[binding('四个轮子')])
        result,send=self.ask([bad,bad],context=context)
        self.assert_pending(result)
        self.assertIn('只确认了 2 组独立部件',str(result))
        self.assertEqual(send.call_count,2)

    def test_selected_feedback_does_not_inherit_catalog_count_authority(self):
        context=self.catalog_context([[0,3,6,9]])
        bad=decision(wheel_patch(1),[binding('四个轮子',['t1'])])
        result,send=self.ask([bad,bad],context=context,
                             selections=[{'object_name':'Mesh.001','material_name':'Shared'}])
        self.assert_pending(result)
        self.assertIn('数量尚未完整覆盖',str(result))
        self.assertEqual(send.call_count,2)

    def test_catalog_without_images_does_not_send_unsupported_geometry_claim(self):
        context=self.catalog_context([[0,3,6,9]]);context.pop('_target_image_paths')
        result,send=self.ask([],context=context)
        self.assert_pending(result)
        self.assertIn('缺少本次部件检查图',str(result))
        send.assert_not_called()

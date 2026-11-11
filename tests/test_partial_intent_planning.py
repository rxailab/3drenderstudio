"""Offline intent/proposal contracts; no provider or renderer is contacted."""
import copy
import json
from unittest.mock import patch

import ai_client
import planner
from intelligence_rules import InstructionOutcome
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, binding, local, decision


SCREENSHOT_TEXT='四个轮子都是黑色橡胶，顶部平面是拉丝不锈钢，柜体是红色金属，改善灯光镜头凸显物体材质反光'


def requirement(ident,status='ready',value=None,bindings=None,summary='已准备相应参数。'):
    return {'id':ident,'status':status,'patch':value or {},'target_bindings':bindings or [],'summary':summary}


def proposal(rows,**extra):
    data={'status':'partial_available','patch':{},'summary':'可先应用完整的子要求。',
          'notes':[],'unhandled':[],'target_bindings':[],'requirement_results':rows}
    data.update(extra)
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':ai_client.TOOL_NAME,'input':data}]}


def screenshot_rows():
    return [
        requirement('r1','needs_clarification',summary='请补充能看清四个轮子的预览，或确认四个轮子的部位。'),
        requirement('r2',value={'object_material_overrides':[local(6,preset='brushed_metal')]},bindings=[binding('顶部平面',['t6'])]),
        requirement('r3',value={'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')]},bindings=[binding('柜体',['t7'])]),
        requirement('r4',value={'lighting':{'rim_power':1.3},'shots':[{'id':'hero','elevation':22}]}),
    ]


class PartialIntentTests(EvidenceFixture):
    def ask(self,text,response,plan=None,selections=None):
        plan=plan or planner.default_plan()
        before=copy.deepcopy(plan)
        # An invalid first decision can now receive one bounded target
        # correction as well as a schema correction. A singleton fixture means
        # the provider repeats that same decision, never an exhausted mock.
        responses=response if isinstance(response,list) else [response,response]
        with patch('ai_client._post',side_effect=responses) as send:
            try:
                result=ai_client.request_plan(plan,text,ANALYSIS,CONFIG,image_path=self.png,
                                              context=self.context,selections=selections)
            except (ValueError,InstructionOutcome) as exc:
                result=exc
        self.assertEqual(plan,before)
        return result,send

    def assert_proposal(self,result):
        self.assertIsInstance(result,InstructionOutcome)
        self.assertEqual(result.status,'partial_available')
        return result.proposal

    def test_screenshot_request_prepares_three_complete_items_without_applying(self):
        result,send=self.ask(SCREENSHOT_TEXT,proposal(screenshot_rows()))
        value=self.assert_proposal(result)
        self.assertEqual(len(value['ready']),3)
        self.assertEqual(value['pending'][0]['quote'],'四个轮子都是黑色橡胶')
        self.assertEqual(value['pending'][0]['label'],'四个轮子')
        self.assertEqual(value['pending'][0]['status'],'needs_clarification')
        self.assertNotIn('四个轮子',value['execution_text'])
        self.assertEqual(value['remaining_text'],'四个轮子都是黑色橡胶')
        self.assertEqual([row['object_name'] for row in value['candidate_plan']['object_material_overrides']],['Mesh.006','Mesh.007'])
        self.assertEqual(value['candidate_plan']['lighting']['rim_power'],1.3)
        self.assertEqual(value['candidate_plan']['shots'][0]['elevation'],22)
        self.assertEqual(len(value['candidate_plan']['last_instruction']['target_bindings']),2)
        self.assertEqual(result.model,CONFIG['model'])
        self.assertTrue(result.used_image)
        self.assertEqual(send.call_count,1)
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual([row['quote'] for row in facts['user_requirements']],SCREENSHOT_TEXT.split('，'))
        self.assertIn('existing lighting/camera controls',send.call_args.args[1]['system'])

    def test_old_screenshot_style_unhandled_is_corrected_within_existing_two_calls(self):
        bad=decision({'lighting':{'rim_power':1.3}},bindings=[],unhandled=[
            '四个轮子仍需确认。', '接触阴影加强、地面阴影接收属于未提供的独立阴影控制。',
            '红色金属柜体的微纹理和真实倒角无法实现。'])
        result,send=self.ask(SCREENSHOT_TEXT,[bad,proposal(screenshot_rows())])
        value=self.assert_proposal(result)
        self.assertEqual(send.call_count,2)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'],'unrequested_blocker')
        self.assertEqual(len(value['pending']),1)
        self.assertNotIn('微纹理',json.dumps(value,ensure_ascii=False))

    def test_repeated_invented_blockers_end_with_short_error_and_no_third_call(self):
        bad=decision({},bindings=[],status='unsupported',unhandled=['Mesh.unknown 红色金属的微纹理和真实倒角未支持。'])
        result,send=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertIsInstance(result,ai_client.SchemaValidationError)
        self.assertEqual(result.feedback['code'],'unrequested_blocker')
        self.assertNotIn('Mesh.unknown',str(result))
        self.assertLess(len(str(result)),70)
        self.assertEqual(send.call_count,2)

    def test_invented_pending_capability_is_not_accepted_for_reflection_goal(self):
        rows=screenshot_rows()
        rows[-1]=requirement('r4','unsupported',summary='独立阴影和倒角暂不支持。')
        bad=proposal(rows)
        result,send=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertIsInstance(result,ai_client.SchemaValidationError)
        self.assertEqual(result.feedback['code'],'unrequested_blocker')
        self.assertEqual(send.call_count,2)

    def test_generic_unsupported_cannot_hide_supported_camera_lighting_request(self):
        rows=screenshot_rows();rows[-1]=requirement('r4','unsupported',summary='当前不支持这种调整。')
        bad=proposal(rows)
        result,_=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertEqual(result.feedback['code'],'unsupported_supported_requirement')

    def test_generic_clarification_cannot_fabricate_blocker_for_existing_controls(self):
        rows=screenshot_rows();rows[-1]=requirement('r4','needs_clarification',summary='需要进一步确认。')
        bad=proposal(rows)
        result,_=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertEqual(result.feedback['code'],'unsupported_photography_blocker')

    def test_material_request_cannot_become_unsupported_without_requested_capability(self):
        rows=screenshot_rows();rows[2]=requirement('r3','unsupported',summary='当前不能完成该材质效果。')
        bad=proposal(rows)
        result,_=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertEqual(result.feedback['code'],'unsupported_supported_requirement')

    def test_every_original_requirement_must_be_present_once(self):
        for rows in (screenshot_rows()[:-1],screenshot_rows()+[screenshot_rows()[0]]):
            with self.subTest(rows=len(rows)):
                bad=proposal(rows);result,_=self.ask(SCREENSHOT_TEXT,[bad,bad])
                self.assertIsInstance(result,ai_client.SchemaValidationError)
                self.assertEqual(result.feedback['code'],'requirement_coverage')

    def test_model_cannot_add_a_new_requirement_id_for_unrequested_bevel(self):
        rows=screenshot_rows()+[requirement('r5','unsupported',summary='倒角不支持。')]
        bad=proposal(rows);result,_=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertIsInstance(result,ai_client.SchemaValidationError)
        self.assertEqual(result.feedback['code'],'enum')

    def test_partial_top_level_patch_is_always_empty(self):
        bad=proposal(screenshot_rows(),patch={'exposure':.2})
        result,send=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertIsInstance(result,ai_client.ResponseContractError)
        self.assertEqual(result.feedback['code'],'decision_conflict')
        self.assertEqual(send.call_count,2);self.assertFalse(hasattr(result,'proposal'))

    def test_blocked_item_cannot_smuggle_patch(self):
        rows=screenshot_rows();rows[0]['patch']={'exposure':.2}
        bad=proposal(rows);result,_=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertEqual(result.feedback['code'],'blocked_requirement_patch')

    def test_four_wheels_still_require_four_distinct_evidenced_objects(self):
        rows=screenshot_rows();rows[0]=requirement('r1',value={'object_material_overrides':[
            local(1,preset='rubber',color='#151515'),local(2,preset='rubber',color='#151515')]},bindings=[binding('四个轮子',['t1','t2'])])
        rows[1]=requirement('r2','needs_clarification',summary='请确认顶部平面。')
        result,_=self.ask(SCREENSHOT_TEXT,proposal(rows))
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('要求定位 4 个',str(result))
        self.assertFalse(hasattr(result,'proposal'))

    def test_four_bindings_with_only_two_overrides_cannot_be_ready(self):
        rows=screenshot_rows();rows[0]=requirement('r1',value={'object_material_overrides':[
            local(1,preset='rubber',color='#151515'),local(2,preset='rubber',color='#151515')]},bindings=[binding('四个轮子')])
        rows[1]=requirement('r2','needs_clarification',summary='请确认顶部平面。')
        result,_=self.ask(SCREENSHOT_TEXT,proposal(rows))
        self.assertIn('2 个已定位目标未包含',str(result))

    def test_unrequested_shared_material_edit_still_cannot_be_proposed(self):
        rows=screenshot_rows();rows[2]['patch']={'material_overrides':[{'name':'Shared','preset':'painted_metal','color':'#B93832'}]}
        result,_=self.ask(SCREENSHOT_TEXT,proposal(rows))
        self.assertIn('共用材质',str(result))

    def test_material_ready_must_include_explicit_color_and_material_type(self):
        for entry in (local(7,preset='painted_metal'),local(7,color='#B93832')):
            with self.subTest(entry=entry):
                rows=screenshot_rows();rows[2]['patch']={'object_material_overrides':[entry]}
                result,_=self.ask(SCREENSHOT_TEXT,proposal(rows))
                self.assertIsInstance(result,InstructionOutcome)
                self.assertIn('尚未完整包含',str(result))

    def test_general_photography_goal_can_use_lighting_without_forcing_camera(self):
        rows=screenshot_rows();rows[3]['patch']={'lighting':{'rim_power':1.3}}
        result,_=self.ask(SCREENSHOT_TEXT,proposal(rows))
        value=self.assert_proposal(result)
        self.assertEqual(len(value['ready']),3)
        self.assertEqual(value['candidate_plan']['shots'],planner.default_plan()['shots'])
        self.assertIn('镜头参数保持原值',''.join(value['candidate_plan']['last_instruction']['notes']))

    def test_material_clause_does_not_authorize_unrelated_camera_change(self):
        rows=screenshot_rows();rows[2]['patch']['shots']=[{'id':'hero','elevation':25}]
        result,send=self.ask(SCREENSHOT_TEXT,proposal(rows))
        self.assertIsInstance(result,ai_client.ResponseContractError)
        self.assertEqual(result.feedback['code'],'decision_conflict')
        self.assertEqual(send.call_count,2);self.assertFalse(hasattr(result,'proposal'))

    def test_conflicting_ready_values_do_not_get_last_write_wins(self):
        text='四个轮子都是黑色橡胶，曝光设为0.2EV，曝光设为0.4EV'
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2',value={'exposure':.2}),requirement('r3',value={'exposure':.4})]
        result,send=self.ask(text,proposal(rows))
        self.assertIsInstance(result,ai_client.ResponseContractError)
        self.assertEqual(result.feedback['code'],'decision_conflict')
        self.assertEqual(send.call_count,2);self.assertFalse(hasattr(result,'proposal'))

    def test_original_global_preservation_is_enforced_for_every_ready_item(self):
        text='保留原色，'+SCREENSHOT_TEXT
        bad=proposal(screenshot_rows());result,_=self.ask(text,[bad,bad])
        self.assertIsInstance(result,ValueError)
        self.assertFalse(hasattr(result,'proposal'))

    def test_only_active_shot_can_be_in_proposal(self):
        rows=screenshot_rows();rows[3]['patch']['shots'][0]['id']='campaign'
        bad=proposal(rows);result,send=self.ask(SCREENSHOT_TEXT,[bad,bad])
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('当前镜头',str(result))
        self.assertEqual(send.call_count,1);self.assertFalse(hasattr(result,'proposal'))

    def test_selected_pair_subset_does_not_bypass_four_wheel_count(self):
        rows=[requirement('r1',value={'object_material_overrides':[
            local(1,preset='rubber',color='#151515'),local(2,preset='rubber',color='#151515')]}),
            requirement('r2','needs_clarification',summary='请确认顶部平面。')]
        result,_=self.ask('四个轮子都是黑色橡胶，顶部平面是拉丝不锈钢',proposal(rows),
                          selections=[{'object_name':'Mesh.001','material_name':'Shared'},{'object_name':'Mesh.002','material_name':'Shared'}])
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('数量尚未完整覆盖',str(result))

    def test_all_pending_returns_structured_clarification_without_proposal(self):
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2','needs_clarification',summary='请确认顶部平面。')]
        result,_=self.ask('四个轮子都是黑色橡胶，顶部平面是拉丝不锈钢',proposal(rows))
        self.assertEqual(result.status,'needs_clarification')
        self.assertEqual(len(result.pending),2)
        self.assertEqual(result.remaining_text,'四个轮子都是黑色橡胶，顶部平面是拉丝不锈钢')
        self.assertFalse(hasattr(result,'proposal'))

    def test_already_satisfied_is_verified_against_current_values(self):
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2','already_satisfied',{'exposure':0}),requirement('r3',value={'margin':.2})]
        result,_=self.ask('四个轮子都是黑色橡胶，曝光设为0EV，增加留白',proposal(rows))
        value=self.assert_proposal(result)
        self.assertEqual(value['ready'][0]['status'],'already_satisfied')
        self.assertEqual(value['candidate_plan']['exposure'],0)
        self.assertEqual(value['candidate_plan']['margin'],.2)

    def test_false_or_empty_already_satisfied_cannot_hide_unperformed_work(self):
        for value in ({},{'exposure':.3}):
            with self.subTest(value=value):
                rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
                      requirement('r2','already_satisfied',value),requirement('r3',value={'margin':.2})]
                result,_=self.ask('四个轮子都是黑色橡胶，曝光设为0EV，增加留白',proposal(rows))
                self.assertIsInstance(result,InstructionOutcome)
                self.assertFalse(hasattr(result,'proposal'))

    def test_explicit_unavailable_clause_can_coexist_with_safe_photography_proposal(self):
        rows=[requirement('r1',value={'lighting':{'rim_power':1.3}}),
              requirement('r2','unsupported',summary='当前无法添加真实倒角，请在建模软件中处理。')]
        result,_=self.ask('改善灯光，并增加倒角',proposal(rows))
        value=self.assert_proposal(result)
        self.assertEqual(value['execution_text'],'改善灯光')
        self.assertEqual(value['remaining_text'],'增加倒角')
        self.assertEqual(value['pending'][0]['status'],'unsupported')

    def test_explicit_unavailable_clause_cannot_be_relabelled_ready(self):
        rows=[requirement('r1',value={'lighting':{'rim_power':1.3}}),
              requirement('r2',value={'light_softness':1.4})]
        bad=proposal(rows);result,_=self.ask('改善灯光，并增加倒角',[bad,bad])
        self.assertIsInstance(result,ai_client.SchemaValidationError)
        self.assertEqual(result.feedback['code'],'known_unsupported_requirement')

    def test_full_apply_cannot_omit_explicit_unavailable_clause(self):
        bad=decision({'lighting':{'rim_power':1.3}},bindings=[])
        result,_=self.ask('改善灯光，并增加倒角',[bad,bad])
        self.assertEqual(result.feedback['code'],'omitted_unsupported_requirement')

    def test_planner_carries_validated_proposal_without_saving_or_replaying(self):
        before=planner.default_plan();original=copy.deepcopy(before)
        with patch('ai_config.get_vision_config',return_value=CONFIG),patch('ai_config.record_vision_result') as record, \
             patch('ai_client._post',return_value=proposal(screenshot_rows())),self.assertRaises(InstructionOutcome) as raised:
            planner.instruct(before,SCREENSHOT_TEXT,ANALYSIS,image_path=self.png,context=self.context)
        value=self.assert_proposal(raised.exception)
        candidate=value['candidate_plan']
        self.assertEqual(before,original)
        self.assertEqual(candidate['last_instruction']['model'],CONFIG['model'])
        self.assertTrue(candidate['last_instruction']['parameter_diff'])
        self.assertEqual(candidate['source'],'ai')
        self.assertEqual(len(candidate['last_instruction']['target_bindings']),2)
        record.assert_called_once()

    def test_remaining_instruction_keeps_original_global_constraints(self):
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2',value={'exposure':.2})]
        result,_=self.ask('四个轮子是黑色橡胶；调整灯光；保持镜头不变',proposal(rows))
        value=self.assert_proposal(result)
        self.assertIn('保持镜头不变',value['execution_text'])
        self.assertIn('保持镜头不变',value['remaining_text'])
        self.assertIn('preserve_camera',result.protections)

    def test_full_general_goal_uses_an_effective_subset_and_reports_unchanged_controls(self):
        for values in ({'lighting':{'rim_power':1.3}},{'shots':[{'id':'hero','elevation':25}]}):
            with self.subTest(values=values):
                response=decision(values,bindings=[])
                result,send=self.ask('改善灯光镜头凸显物体材质反光',response)
                self.assertIsInstance(result,dict)
                self.assertEqual(send.call_count,1)
                note='镜头参数保持原值' if 'lighting' in values else '灯光参数保持原值'
                self.assertIn(note,''.join(result['last_instruction']['notes']))

    def test_general_goal_respects_camera_preservation_without_impossible_coverage(self):
        result,send=self.ask('改善灯光镜头凸显材质反光，镜头不变',decision({'light_softness':1.2},bindings=[]))
        self.assertIsInstance(result,dict)
        self.assertEqual(result['light_softness'],1.2)
        self.assertEqual(result['shots'],planner.default_plan()['shots'])
        facts=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(facts['user_requirements'][0]['photography_mode'],'general_goal')
        self.assertEqual(facts['user_requirements'][0]['eligible_domains'],['lighting'])

    def test_explicit_both_controls_are_not_relaxed_as_a_general_goal(self):
        bad=decision({'light_softness':1.2},bindings=[])
        result,send=self.ask('灯光和镜头都要调整',[bad,bad])
        self.assertIsInstance(result,ai_client.SchemaValidationError)
        self.assertEqual(result.feedback['code'],'photography_requirement_coverage')
        self.assertEqual(send.call_count,2)
        self.assertNotIn('可先确认',str(result))

    def test_missing_explicit_camera_is_completed_in_the_same_request(self):
        result,send=self.ask('灯光柔和一点，镜头改成俯拍',decision({'light_softness':1.2},bindings=[]))
        self.assertIsInstance(result,dict)
        self.assertEqual(result['light_softness'],1.2)
        self.assertEqual(result['shots'][0]['elevation'],80)
        self.assertEqual(result['shots'][1],planner.default_plan()['shots'][1])
        self.assertEqual(send.call_count,1)

    def test_conflicting_explicit_number_uses_one_targeted_correction_not_override(self):
        bad=decision({'light_softness':1.2,'shots':[{'id':'hero','focal_length':50}]},bindings=[])
        good=decision({'light_softness':1.2,'shots':[{'id':'hero','focal_length':85}]},bindings=[])
        result,send=self.ask('灯光柔和一点，焦距设为85mm',[bad,good])
        self.assertIsInstance(result,dict)
        self.assertEqual(result['shots'][0]['focal_length'],85)
        self.assertEqual(result['light_softness'],1.2)
        self.assertEqual(send.call_count,2)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['expected_parameters'],{'shots.hero.focal_length':85})

    def test_vague_relative_direction_must_change_in_requested_direction(self):
        bad=decision({'light_softness':.8},bindings=[])
        good=decision({'light_softness':1.15},bindings=[])
        result,send=self.ask('灯光柔和一点',[bad,good])
        self.assertEqual(result['light_softness'],1.15)
        self.assertEqual(send.call_count,2)

    def test_empty_lighting_and_repeated_camera_do_not_complete_general_goal(self):
        text='柜体是红色金属，改善灯光镜头凸显材质反光'
        bad=decision({'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')],
                      'lighting':{},'shots':[{'id':'hero','elevation':planner.default_plan()['shots'][0]['elevation']}]},
                     bindings=[binding('柜体',['t7'])])
        result,send=self.ask(text,[bad,bad]);value=self.assert_proposal(result)
        self.assertEqual(len(value['ready']),1)
        self.assertEqual(value['remaining_text'],'改善灯光镜头凸显材质反光')
        self.assertEqual(value['pending'][0]['action'],'clarify_parameters')
        self.assertEqual(value['candidate_plan']['shots'],planner.default_plan()['shots'])
        self.assertEqual(value['candidate_plan']['lighting'],planner.default_plan()['lighting'])
        self.assertEqual(send.call_count,2)

    def test_second_missing_camera_keeps_independent_material_and_lighting_for_confirmation(self):
        text='柜体是红色金属，曝光提高0.2EV，镜头稍微拉近，保持背景不变'
        bad=decision({'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')],
                      'exposure':.2},bindings=[binding('柜体',['t7'])])
        result,send=self.ask(text,[bad,bad]);value=self.assert_proposal(result)
        self.assertEqual([row['quote'] for row in value['ready']],['柜体是红色金属','曝光提高0.2EV'])
        self.assertEqual(value['remaining_text'],'镜头稍微拉近，保持背景不变')
        self.assertEqual(value['candidate_plan']['exposure'],.2)
        self.assertEqual(value['candidate_plan']['shots'],planner.default_plan()['shots'])
        self.assertIn('保持背景不变',value['execution_text'])
        self.assertEqual(send.call_count,2)

    def test_failed_combined_clause_loses_all_its_parameters_in_recovery(self):
        text='柜体是红色金属，灯光和镜头都要调整'
        bad=decision({'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')],
                      'light_softness':1.2},bindings=[binding('柜体',['t7'])])
        result,_=self.ask(text,[bad,bad]);value=self.assert_proposal(result)
        self.assertEqual(value['remaining_text'],'灯光和镜头都要调整')
        self.assertEqual(value['candidate_plan']['light_softness'],1)
        self.assertEqual(len(value['ready']),1)

    def test_conflicting_same_domain_clauses_cannot_share_parameters_with_recovered_ready(self):
        text='曝光设为1EV，曝光设为2EV，柜体是红色金属'
        bad=decision({'exposure':1,'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')]},
                     bindings=[binding('柜体',['t7'])])
        result,_=self.ask(text,[bad,bad]);value=self.assert_proposal(result)
        self.assertEqual([row['quote'] for row in value['ready']],['柜体是红色金属'])
        self.assertEqual(value['remaining_text'],'曝光设为1EV，曝光设为2EV')
        self.assertEqual(value['candidate_plan']['exposure'],0)

    def test_partial_response_with_second_camera_omission_keeps_only_independent_items(self):
        text='四个轮子都是黑色橡胶，柜体是红色金属，灯光和镜头都要调整'
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2',value={'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')]},bindings=[binding('柜体',['t7'])]),
              requirement('r3',value={'light_softness':1.2})]
        bad=proposal(rows);result,send=self.ask(text,[bad,bad])
        # Both material clauses share a domain with a pending material target;
        # recovery deliberately refuses to infer independence in this case.
        self.assertIsInstance(result,ai_client.SchemaValidationError)
        self.assertFalse(hasattr(result,'proposal'))
        self.assertEqual(send.call_count,2)

    def test_structured_partial_photography_pending_can_recover_independent_exposure(self):
        text='曝光提高0.2EV，镜头稍微拉近，颜色不变'
        rows=[requirement('r1',value={'exposure':.2}),
              requirement('r2','needs_clarification',summary='请说明希望拉近到什么构图。')]
        bad=proposal(rows);result,send=self.ask(text,[bad,bad]);value=self.assert_proposal(result)
        self.assertEqual(value['candidate_plan']['exposure'],.2)
        self.assertEqual(value['remaining_text'],'镜头稍微拉近，颜色不变')
        self.assertEqual(value['pending'][0]['action'],'clarify_parameters')
        self.assertEqual(send.call_count,2)

    def test_distinct_materials_in_one_clause_validate_against_their_own_pairs(self):
        text='四个轮子都是黑色橡胶，顶部平面是拉丝不锈钢 柜体是红色金属'
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2',value={'object_material_overrides':[
                  local(6,preset='brushed_metal'),local(7,preset='painted_metal',color='#B93832')]},
                          bindings=[binding('顶部平面',['t6']),binding('柜体',['t7'])])]
        result,_=self.ask(text,proposal(rows))
        value=self.assert_proposal(result)
        self.assertEqual(len(value['candidate_plan']['object_material_overrides']),2)

    def test_plain_material_reflection_goal_uses_photography_controls(self):
        for quote in ('改善反光','材质反光柔和一点','改善材质的反光'):
            with self.subTest(quote=quote):
                rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
                      requirement('r2',value={'light_softness':1.4})]
                result,_=self.ask('四个轮子都是黑色橡胶，'+quote,proposal(rows))
                value=self.assert_proposal(result)
                self.assertEqual(value['candidate_plan']['object_material_overrides'],[])
                self.assertEqual(value['candidate_plan']['light_softness'],1.4)

    def test_full_apply_material_bindings_cannot_substitute_roughness_for_type_and_color(self):
        values={'object_material_overrides':[local(number,roughness=.6) for number in (1,2,3,4,6,7)],
                'lighting':{'rim_power':1.3},'shots':[{'id':'hero','elevation':22}]}
        bindings=[binding('四个轮子'),binding('顶部平面',['t6']),binding('柜体',['t7'])]
        result,_=self.ask(SCREENSHOT_TEXT,decision(values,bindings=bindings))
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('材质类型尚未完整包含',str(result))
        self.assertFalse(hasattr(result,'proposal'))

    def test_full_apply_validates_each_material_clause_and_all_explicit_colors(self):
        complete=[local(number,preset='rubber',color='#151515') for number in range(1,5)]
        complete += [local(6,preset='brushed_metal'),local(7,preset='painted_metal',color='#B93832')]
        bindings=[binding('四个轮子'),binding('顶部平面',['t6']),binding('柜体',['t7'])]
        for changed,field in ((0,'preset'),(4,'preset'),(5,'preset'),(0,'color'),(5,'color')):
            with self.subTest(changed=changed,field=field):
                entries=copy.deepcopy(complete);entries[changed].pop(field)
                entries[changed]['roughness']=.6
                values={'object_material_overrides':entries,'lighting':{'rim_power':1.3},
                        'shots':[{'id':'hero','elevation':22}]}
                result,_=self.ask(SCREENSHOT_TEXT,decision(values,bindings=bindings))
                self.assertIsInstance(result,InstructionOutcome)
                self.assertIn('尚未完整包含',str(result))

    def test_full_apply_preserves_valid_multiple_material_types_with_photography(self):
        entries=[local(number,preset='rubber',color='#151515') for number in range(1,5)]
        entries += [local(6,preset='brushed_metal'),local(7,preset='painted_metal',color='#B93832')]
        values={'object_material_overrides':entries,'lighting':{'rim_power':1.3},
                'shots':[{'id':'hero','elevation':22}]}
        bindings=[binding('四个轮子'),binding('顶部平面',['t6']),binding('柜体',['t7'])]
        result,_=self.ask(SCREENSHOT_TEXT,decision(values,bindings=bindings))
        self.assertIsInstance(result,dict)
        self.assertEqual(len(result['object_material_overrides']),6)

    def test_already_satisfied_cannot_copy_current_value_instead_of_explicit_target(self):
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2','already_satisfied',{'exposure':0}),requirement('r3',value={'margin':.2})]
        result,_=self.ask('四个轮子都是黑色橡胶，曝光设为1EV，增加留白',proposal(rows))
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('仍需要参数变化',str(result))
        self.assertFalse(hasattr(result,'proposal'))

    def test_already_satisfied_recomputes_relative_exposure_intent(self):
        for quote in ('亮一点','再亮一点','曝光提高0.2EV'):
            with self.subTest(quote=quote):
                rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
                      requirement('r2','already_satisfied',{'exposure':0}),requirement('r3',value={'margin':.2})]
                result,_=self.ask('四个轮子都是黑色橡胶，'+quote+'，增加留白',proposal(rows))
                self.assertIsInstance(result,InstructionOutcome)
                self.assertIn('不能把未变化的参数标为已满足',str(result))
                self.assertFalse(hasattr(result,'proposal'))

    def test_already_satisfied_recomputes_relative_light_softness(self):
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2','already_satisfied',{'light_softness':1}),requirement('r3',value={'margin':.2})]
        result,_=self.ask('四个轮子都是黑色橡胶，反光柔和一点，增加留白',proposal(rows))
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('不能把未变化的参数标为已满足',str(result))

    def test_relative_request_at_control_limit_is_not_already_satisfied(self):
        for field,value,quote in (('exposure',4,'再亮一点'),('light_softness',4,'反光更柔和'),('margin',.4,'增加留白')):
            with self.subTest(quote=quote):
                current=planner.default_plan();current[field]=value
                rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
                      requirement('r2','already_satisfied',{field:value}),
                      requirement('r3',value={'lighting':{'rim_power':1.3}})]
                result,_=self.ask('四个轮子都是黑色橡胶，'+quote+'，调整灯光',proposal(rows),plan=current)
                self.assertIsInstance(result,InstructionOutcome)
                self.assertEqual(result.status,'needs_clarification')
                self.assertIn('不能把未变化的参数标为已满足',str(result))
                self.assertFalse(hasattr(result,'proposal'))

    def test_unverifiable_already_satisfied_claim_is_kept_unapplied(self):
        rows=[requirement('r1','needs_clarification',summary='请确认四个轮子。'),
              requirement('r2','already_satisfied',{'lighting':{'rim_power':1}}),requirement('r3',value={'margin':.2})]
        result,_=self.ask('四个轮子都是黑色橡胶，灯光更有高级感，增加留白',proposal(rows))
        self.assertIsInstance(result,InstructionOutcome)
        self.assertIn('无法核实',str(result))
        self.assertFalse(hasattr(result,'proposal'))


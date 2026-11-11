"""One model decision per original clause; all provider replies are offline fixtures."""
import copy
import json
from unittest.mock import patch

import ai_client
from intelligence_rules import InstructionOutcome
from planner import default_plan
from test_target_grounding import EvidenceFixture, ANALYSIS, CONFIG, binding, decision, local
from test_partial_intent_planning import proposal, requirement


TEXT='上表面是反光的拉丝不锈钢，箱体是红色金属有光泽'


def material_rows():
    return [requirement('r1',value={'object_material_overrides':[local(6,preset='brushed_metal')]},
                        bindings=[binding('上表面',['t6'])]),
            requirement('r2',value={'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')]},
                        bindings=[binding('箱体',['t7'])])]


def wire(rows=None,**extra):
    value={'requirement_results':material_rows() if rows is None else rows,'notes':[]}
    value.update(extra)
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':ai_client.TOOL_NAME,'input':value}]}


def merged_rows(rows):
    result={}
    for row in rows:
        if row['status'] in ('ready','already_satisfied'):
            ai_client._merge_requirement_patch(result,row['patch'])
    return result


class DecisionContractTests(EvidenceFixture):
    def ask(self,responses,text=TEXT,plan=None,selections=None):
        plan=default_plan() if plan is None else plan;before=copy.deepcopy(plan)
        if not isinstance(responses,list):responses=[responses,responses]
        with patch('ai_client._post',side_effect=responses) as send:
            try:
                result=ai_client.request_plan(plan,text,ANALYSIS,CONFIG,image_path=self.png,
                                             context=self.context,selections=selections)
            except ValueError as exc:result=exc
        self.assertEqual(plan,before)
        self.assertLessEqual(send.call_count,2)
        return result,send

    def assert_contract_error(self,result,send,code='decision_conflict'):
        self.assertIsInstance(result,ai_client.ResponseContractError)
        self.assertEqual(result.status_code,422)
        self.assertEqual(result.error_code,'instruction_response_invalid')
        self.assertTrue(result.retryable);self.assertFalse(result.applied)
        self.assertEqual(result.feedback['code'],code)
        self.assertTrue(result.feedback['path'].startswith('$'))
        self.assertFalse(hasattr(result,'pending'));self.assertFalse(hasattr(result,'proposal'))
        self.assertEqual(send.call_count,2)
        self.assertEqual(len(result.response_shapes),2)
        self.assertNotIn('请明确',str(result))

    def test_screenshot_uses_one_wire_representation_and_server_derives_apply(self):
        result,send=self.ask(wire())
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        top,body=result['object_material_overrides']
        self.assertEqual((top['preset'],top['roughness']),('brushed_metal',.3))
        self.assertEqual((body['preset'],body['color'],body['roughness']),('painted_metal','#B93832',.28))
        schema=send.call_args.args[1]['tools'][0]['input_schema']
        self.assertEqual(set(schema['properties']),{'requirement_results','notes','summary'})
        self.assertEqual(schema['required'],['requirement_results','notes'])
        self.assertEqual(schema['properties']['requirement_results']['minItems'],2)
        self.assertEqual(schema['properties']['requirement_results']['maxItems'],2)
        self.assertNotIn('status=partial_available',send.call_args.args[1]['system'])

    def test_duplicate_legacy_top_patch_is_accepted_after_exact_identity_comparison(self):
        rows=material_rows();value=merged_rows(rows)
        value['object_material_overrides'].reverse()
        top_bindings=list(reversed([entry for row in rows for entry in row['target_bindings']]))
        old=proposal(rows,patch=value,target_bindings=top_bindings)
        result,send=self.ask(old)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual({row['object_name'] for row in result['object_material_overrides']},{'Mesh.006','Mesh.007'})

    def test_legacy_top_patch_can_be_a_matching_subset_of_ready_rows(self):
        rows=material_rows()
        result,send=self.ask(proposal(rows,patch=copy.deepcopy(rows[0]['patch']),target_bindings=rows[0]['target_bindings']))
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)

    def test_conflicting_legacy_value_gets_one_correction_against_original_plan(self):
        rows=material_rows();value=merged_rows(rows)
        value['object_material_overrides'][1]['color']='#0044CC'
        bad=proposal(rows,patch=value)
        result,send=self.ask([bad,wire()])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'],'decision_conflict')
        self.assertIn('EVERY',correction['instruction'])
        self.assertEqual(result['object_material_overrides'][1]['color'],'#B93832')

    def test_conflicting_legacy_values_are_known_retryable_protocol_failure(self):
        rows=material_rows();value=merged_rows(rows)
        value['object_material_overrides'][1]['color']='#0044CC'
        result,send=self.ask(proposal(rows,patch=value))
        self.assert_contract_error(result,send)
        self.assertEqual(result.feedback['path'],'$.patch')

    def test_extra_top_field_and_identifier_only_target_cannot_disappear(self):
        for extra in ({'exposure':.2}, {'lighting':{}}, {'shots':[]}, {'object_material_overrides':[local(1)]},
                      {'object_material_overrides':[{'object_name':'Mesh.001','material_name':'Shared'}]}):
            with self.subTest(extra=extra):
                result,send=self.ask(proposal(material_rows(),patch=extra))
                self.assert_contract_error(result,send)

    def test_redundant_binding_cannot_add_or_replace_a_target(self):
        for extra in ([binding('上表面',['t5'])],[binding('四个轮子')],[binding('上表面',['t6','t6'])]):
            with self.subTest(extra=extra):
                result,send=self.ask(proposal(material_rows(),target_bindings=extra))
                self.assert_contract_error(result,send)

    def test_unexplained_top_unhandled_is_not_silently_discarded(self):
        result,send=self.ask(proposal(material_rows(),unhandled=['箱体还需要另做处理。']))
        self.assert_contract_error(result,send)
        self.assertEqual(result.feedback['path'],'$.unhandled')

    def test_matching_pending_summary_may_be_deduplicated(self):
        rows=material_rows();rows[1]=requirement('r2','needs_clarification',summary='请确认箱体的完整部位。')
        result,send=self.ask(proposal(rows,patch=copy.deepcopy(rows[0]['patch']),
                                      target_bindings=rows[0]['target_bindings'],unhandled=[rows[1]['summary']]))
        self.assertEqual(result.status,'partial_available');self.assertEqual(send.call_count,1)
        self.assertEqual(len(result.proposal['ready']),1)
        self.assertEqual(result.proposal['remaining_text'],TEXT.split('，')[1])

    def test_protected_changes_are_checked_even_in_discardable_top_duplicate(self):
        rows=[requirement('r1',value={'exposure':.2})]
        bad=proposal(rows,patch={'exposure':.2,'margin':.3})
        result,send=self.ask(bad,text='只改曝光到0.2EV')
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,1)
        self.assertIn('冲突',str(result));self.assertFalse(hasattr(result,'proposal'))

    def test_protected_changes_are_checked_in_every_row_before_normalization(self):
        rows=[requirement('r1',value={'exposure':.2,'margin':.3})]
        result,send=self.ask(wire(rows),text='只改曝光到0.2EV')
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,1)
        self.assertFalse(hasattr(result,'proposal'))

    def test_lone_nonapply_with_patch_is_corrected_in_existing_two_calls(self):
        rows=material_rows();bad=decision(merged_rows(rows),[],status='needs_clarification')
        result,send=self.ask([bad,wire()])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
        correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(correction['schema_correction']['code'],'decision_status_patch')

    def test_repeated_lone_nonapply_with_patch_is_never_autopromoted(self):
        for status in ('needs_clarification','no_change','unsupported','partial_available'):
            with self.subTest(status=status):
                bad=decision(merged_rows(material_rows()),[],status=status)
                result,send=self.ask(bad)
                self.assert_contract_error(result,send,'decision_status_patch')

    def test_missing_duplicate_and_unknown_requirement_ids_are_protocol_failures(self):
        examples=[(material_rows()[:1],'array_length'),
                  ([material_rows()[0],material_rows()[0]],'requirement_coverage'),
                  ([material_rows()[0],{**material_rows()[1],'id':'r99'}],'enum')]
        for rows,code in examples:
            with self.subTest(code=code):
                result,send=self.ask(wire(rows))
                self.assert_contract_error(result,send,code)

    def test_malformed_row_does_not_become_a_request_to_locate_a_part(self):
        for mutate,code in ((lambda row:row.pop('patch'),'missing_fields'),
                            (lambda row:row.update(patch=[]),'type'),
                            (lambda row:row.update(extra_field='ignored?'),'unknown_fields')):
            rows=material_rows();mutate(rows[0])
            with self.subTest(code=code):
                result,send=self.ask(wire(rows));self.assert_contract_error(result,send,code)

    def test_all_pending_remains_genuine_target_clarification(self):
        rows=[requirement('r1','needs_clarification',summary='请确认上表面。'),
              requirement('r2','needs_clarification',summary='请确认箱体。')]
        result,send=self.ask(wire(rows))
        self.assertIsInstance(result,InstructionOutcome)
        self.assertEqual(result.status,'needs_clarification');self.assertEqual(send.call_count,1)
        self.assertEqual([row['action'] for row in result.pending],['locate_part','locate_part'])
        self.assertEqual(result.remaining_text,TEXT);self.assertFalse(hasattr(result,'proposal'))

    def test_genuine_target_omission_can_be_corrected_with_row_only_reply(self):
        rows=material_rows();rows[0]['target_bindings']=[]
        result,send=self.ask([wire(rows),wire()])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)

    def test_schema_and_target_failures_share_one_correction_budget(self):
        bad=decision(merged_rows(material_rows()),[],status='needs_clarification')
        rows=material_rows();rows[0]['target_bindings']=[]
        result,send=self.ask([bad,wire(rows)])
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,2)
        self.assertEqual(result.status,'needs_clarification');self.assertEqual(result.pending[0]['action'],'locate_part')

    def test_mixed_material_already_satisfied_has_exact_target_and_zero_diff_proof(self):
        rows=material_rows();rows[0]['status']='already_satisfied'
        matching=copy.deepcopy(rows[0]['patch']);matching['object_material_overrides'][0]['roughness']=.3
        plan=ai_client.apply_patch(default_plan(),matching,ANALYSIS)
        result,send=self.ask(wire(rows),plan=plan)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['object_material_overrides'][0],plan['object_material_overrides'][0])
        self.assertEqual(result['object_material_overrides'][1]['roughness'],.28)

    def test_already_satisfied_material_with_pending_clause_keeps_verified_partial_work(self):
        rows=material_rows();rows[0]['status']='already_satisfied'
        rows.append(requirement('r3','needs_clarification',summary='请确认四个轮子。'))
        matching=copy.deepcopy(rows[0]['patch']);matching['object_material_overrides'][0]['roughness']=.3
        plan=ai_client.apply_patch(default_plan(),matching,ANALYSIS)
        result,send=self.ask(wire(rows),text=TEXT+'，四个轮子是黑色橡胶',plan=plan)
        self.assertEqual(result.status,'partial_available');self.assertEqual(send.call_count,1)
        self.assertEqual([row['status'] for row in result.proposal['ready']],['already_satisfied','ready'])
        self.assertEqual(result.proposal['pending'][0]['id'],'r3')

    def test_false_material_already_satisfied_never_discards_actual_changes(self):
        rows=material_rows();rows[0]['status']='already_satisfied'
        result,send=self.ask(wire(rows))
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,2)
        self.assertFalse(hasattr(result,'proposal'))

    def test_zero_diff_for_every_material_row_derives_no_change(self):
        value=merged_rows(material_rows())
        value['object_material_overrides'][0]['roughness']=.3
        value['object_material_overrides'][1]['roughness']=.28
        plan=ai_client.apply_patch(default_plan(),value,ANALYSIS)
        result,send=self.ask(wire(),plan=plan)
        self.assertEqual(result.status,'no_change');self.assertEqual(send.call_count,1)

    def test_matching_user_material_source_is_not_rewritten_or_counted_as_change(self):
        value=merged_rows(material_rows())
        value['object_material_overrides'][0]['roughness']=.3
        value['object_material_overrides'][1]['roughness']=.28
        plan=ai_client.apply_patch(default_plan(),value,ANALYSIS)
        for row in plan['object_material_overrides']:row['source']='user'
        unchanged=ai_client.apply_patch(plan,value,ANALYSIS)
        self.assertEqual(unchanged,plan);self.assertEqual(ai_client.parameter_diff(plan,unchanged),[])
        rows=material_rows()
        for row in rows:row['status']='already_satisfied'
        result,send=self.ask(wire(rows),plan=plan)
        self.assertEqual(result.status,'no_change');self.assertEqual(send.call_count,1)

    def test_global_material_noop_preserves_source_but_actual_change_records_ai(self):
        plan=default_plan()
        plan['material_overrides']=[{'name':'Shared','preset':'painted_metal','color':'#B93832',
                                    'roughness':.28,'source':'user','preserve_original':False}]
        same={'material_overrides':[{'name':'Shared','preset':'painted_metal','color':'#B93832','roughness':.28}]}
        self.assertEqual(ai_client.apply_patch(plan,same,ANALYSIS),plan)
        changed=copy.deepcopy(same);changed['material_overrides'][0]['roughness']=.4
        result=ai_client.apply_patch(plan,changed,ANALYSIS)
        self.assertEqual(result['material_overrides'][0]['source'],'ai')
        self.assertTrue(ai_client.parameter_diff(plan,result))

    def test_preserved_explicit_type_and_color_are_read_without_forbidden_writes(self):
        plan=ai_client.apply_patch(default_plan(),{'object_material_overrides':[
            local(7,preset='painted_metal',color='#B93832',roughness=.95)]},ANALYSIS)
        plan['object_material_overrides'][0]['source']='user'
        rows=[requirement('r1',value={'object_material_overrides':[local(7,roughness=.28)]},
                          bindings=[binding('箱体',['t7'])])]
        response=wire(rows);before=copy.deepcopy(response)
        result,send=self.ask(response,text='保持原色，箱体是红色金属有光泽',plan=plan)
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        actual=result['object_material_overrides'][0]
        self.assertEqual((actual['preset'],actual['color'],actual['roughness']),('painted_metal','#B93832',.28))
        self.assertEqual(response,before)
        fields=send.call_args.args[1]['tools'][0]['input_schema']['properties']['requirement_results']['items']['properties']['patch']['properties']['object_material_overrides']['items']['properties']
        self.assertNotIn('preset',fields);self.assertNotIn('color',fields)

    def test_importer_suggestion_is_not_proof_of_preserved_material_type(self):
        plan=default_plan();plan['material_overrides']=[{'name':'Shared','preset':'painted_metal',
              'color':'#B93832','preserve_original':True,'source':'model'}]
        rows=[requirement('r1',value={'object_material_overrides':[local(7,roughness=.28)]},
                          bindings=[binding('箱体',['t7'])])]
        result,send=self.ask(wire(rows),text='保持原色，箱体是红色金属有光泽',plan=plan)
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,2)
        self.assertIn('材质类型',str(result));self.assertFalse(hasattr(result,'proposal'))

    def test_old_matte_current_values_do_not_survive_explicit_gloss_request(self):
        old={'object_material_overrides':[local(6,preset='brushed_metal',roughness=.95),
                                            local(7,preset='painted_metal',color='#707070',roughness=.95)]}
        result,send=self.ask(wire(),plan=ai_client.apply_patch(default_plan(),old,ANALYSIS))
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual([row['roughness'] for row in result['object_material_overrides']],[.3,.28])

    def test_conflicting_blue_or_matte_material_values_receive_bounded_correction(self):
        for field,value in (('color','#0044CC'),('roughness',.95)):
            rows=material_rows();rows[1]['patch']['object_material_overrides'][0][field]=value
            with self.subTest(field=field):
                result,send=self.ask([wire(rows),wire()])
                self.assertIsInstance(result,dict);self.assertEqual(send.call_count,2)
                self.assertEqual(result['object_material_overrides'][1]['roughness'],.28)
                self.assertEqual(result['object_material_overrides'][1]['color'],'#B93832')
                correction=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
                self.assertEqual(correction['schema_correction']['code'],'material_property_conflict')
                self.assertEqual(correction['schema_correction']['requirement_ids'],['r2'])
                self.assertNotIn('target_correction',correction)

    def test_repeated_grounded_wrong_color_or_finish_is_protocol_failure_not_locate_part(self):
        for field,value in (('color','#0044CC'),('roughness',.95)):
            rows=material_rows();rows[1]['patch']['object_material_overrides'][0][field]=value
            with self.subTest(field=field):
                result,send=self.ask(wire(rows))
                self.assert_contract_error(result,send,'material_property_conflict')
                self.assertEqual(result.feedback['requirement_ids'],['r2'])
                self.assertEqual(result.feedback['path'],'$.requirement_results[1].patch')

    def test_legacy_apply_wrong_finish_uses_the_same_protocol_failure(self):
        rows=material_rows();rows[1]['patch']['object_material_overrides'][0]['roughness']=.95
        value=merged_rows(rows);bindings=[entry for row in rows for entry in row['target_bindings']]
        result,send=self.ask(decision(value,bindings))
        self.assert_contract_error(result,send,'material_property_conflict')
        self.assertEqual(result.feedback['requirement_ids'],['r1','r2'])

    def test_explicit_invalid_user_roughness_is_parameter_clarification(self):
        rows=[requirement('r1',value={'object_material_overrides':[local(7,preset='painted_metal',color='#B93832',roughness=.28)]},
                          bindings=[binding('箱体',['t7'])])]
        # Keep the numeric constraint inside its original material clause.
        result,send=self.ask(wire(rows),text='箱体是粗糙度为2的红色金属')
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,1)
        self.assertTrue(result.material_user_constraint)
        self.assertFalse(hasattr(result,'pending'));self.assertFalse(hasattr(result,'target_correction'))

    def test_single_real_selected_material_can_complete_its_finish(self):
        rows=[requirement('r1',value={'object_material_overrides':[local(7,preset='painted_metal',color='#B93832')]})]
        result,send=self.ask(wire(rows),text='箱体是红色金属有光泽',
                             selections=[{'object_name':'Mesh.007','material_name':'Shared'}])
        self.assertIsInstance(result,dict);self.assertEqual(send.call_count,1)
        self.assertEqual(result['object_material_overrides'][0]['roughness'],.28)

    def test_multiple_selected_subjects_still_need_separate_identity_grounding(self):
        rows=material_rows()
        for row in rows:row['target_bindings']=[]
        result,send=self.ask(wire(rows),selections=[{'object_name':'Mesh.006','material_name':'Shared'},
                                                  {'object_name':'Mesh.007','material_name':'Shared'}])
        self.assertIsInstance(result,InstructionOutcome);self.assertEqual(send.call_count,2)
        self.assertFalse(hasattr(result,'proposal'))

    def test_response_diagnostic_contains_shape_only(self):
        rows=material_rows();value=merged_rows(rows)
        value['object_material_overrides'][1]['color']='#0044CC'
        bad=proposal(rows,patch=value,notes=['PRIVATE-NOTE-NEVER-LOG'])
        result,send=self.ask(bad);self.assert_contract_error(result,send)
        saved=json.dumps(result.response_shapes,ensure_ascii=False)
        for private in ('PRIVATE-NOTE-NEVER-LOG','#0044CC','#B93832','Mesh.007',TEXT,CONFIG['api_key']):
            self.assertNotIn(private,saved)
        self.assertEqual([row['attempt'] for row in result.response_shapes],[1,2])
        self.assertEqual(result.response_shapes[0]['format'],'legacy')
        self.assertEqual(result.response_shapes[0]['requirement_rows'][0]['patch_keys'],['object_material_overrides'])

    def test_invalid_provider_format_is_bounded_protocol_failure(self):
        result,send=self.ask({'stop_reason':'tool_use','content':[]})
        self.assert_contract_error(result,send,'response_parse')
        self.assertEqual(result.response_shapes[0]['format'],'invalid')

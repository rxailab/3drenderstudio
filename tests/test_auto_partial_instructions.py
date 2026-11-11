"""Automatic independent instruction work, with real storage and HTTP clients.

The planner responses are controlled fixtures. No test reads user configuration,
contacts a model, renders images, or mutates a real project.
"""
import copy
import json
import unittest
from unittest.mock import patch

from PIL import Image
import engine
import instruction_service as service
import studio_store as store
from intelligence_rules import InstructionOutcome
import test_instruction_proposals as review_fixture


class AutoPartialInstructionTests(unittest.TestCase):
    partial=review_fixture.InstructionProposalTests.partial
    web_client=review_fixture.InstructionProposalTests.web_client
    use_real_planner=review_fixture.InstructionProposalTests.use_real_planner
    ai_proposal=review_fixture.InstructionProposalTests.ai_proposal

    def setUp(self):
        review_fixture.InstructionProposalTests.setUp(self)
        self.body.pop('partial_mode')
        mocked=patch.object(engine,'runtime_status',return_value={'capabilities':{'inspect_parts':False,'pick':False}})
        mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'submit',side_effect=AssertionError('The instruction service returns a preview intent; it does not submit duplicate rendering'))
        self.submit=mocked.start();self.addCleanup(mocked.stop)

    def apply(self,key='apply-auto',body=None,**kwargs):
        return service.apply_instruction('fixture',self.body if body is None else body,key,**kwargs)

    def outcome(self,*args,**kwargs):
        try:self.partial(*args,**kwargs)
        except InstructionOutcome as error:return error
        raise AssertionError('Expected a partial planner outcome')

    def assert_unmodified(self):
        current=store.get('project','fixture')
        self.assertEqual(current['version'],3)
        self.assertEqual(current['history'],[])
        self.assertEqual(current['plan'],self.project['plan'])
        self.assertNotIn(service.PROPOSALS_KEY,current)
        self.assertNotIn('instruction_receipts',current)
        self.assertEqual(store.all_records('job'),[])
        self.assertNotIn('fixture',service._INFLIGHT)
        self.submit.assert_not_called()

    def assert_applied(self,result):
        receipt=result['instruction_result']
        self.assertEqual(result['version'],4)
        self.assertEqual(len(result['history']),1)
        self.assertEqual(receipt['status'],'partially_applied')
        self.assertTrue(receipt['partial'])
        self.assertEqual(receipt['base_version'],3)
        self.assertEqual(receipt['applied_version'],4)
        self.assertEqual(receipt['preview_shot_ids'],['hero'])
        self.assertEqual(receipt['remaining_text'],'四个轮子是黑色橡胶')
        self.assertEqual(receipt['pending'][0]['quote'],'四个轮子是黑色橡胶')
        self.assertEqual(receipt['ready'][0]['quote'],'调整灯光')
        self.assertEqual(receipt['text'],self.text)
        self.assertTrue(receipt['changes'])
        self.assertNotIn('proposed_changes',receipt)
        self.assertNotIn('proposal_id',receipt)
        self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))
        self.assertEqual(result['plan']['exposure'],self.project['plan']['exposure'])
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['exposure'],.35)
        self.assertEqual(result['plan']['object_material_overrides'],self.project['plan']['object_material_overrides'])
        self.assertEqual(store.all_records('job'),[])
        self.submit.assert_not_called()

    def test_default_commits_verified_work_without_an_extra_confirmation(self):
        result=self.apply();self.assert_applied(result)
        self.assertEqual(self.model.call_count,1);self.http.assert_not_called()
        self.assertEqual(result['plan']['last_instruction']['text'],'调整灯光')
        self.assertEqual(result['plan']['last_instruction']['original_text'],self.text)
        self.assertEqual(service._recent(result)[-1]['text'],'调整灯光')

    def test_explicit_apply_ready_mode_matches_the_default(self):
        self.assert_applied(self.apply(body={**self.body,'partial_mode':'apply_ready'}))
        self.assertEqual(self.model.call_count,1)

    def test_invalid_partial_mode_is_rejected_before_inference(self):
        for value in (None,True,'force','confirm',{},[]):
            with self.subTest(value=value),self.assertRaises(service.InstructionError):
                self.apply(body={**self.body,'partial_mode':value})
        self.model.assert_not_called();self.assert_unmodified()

    def test_automatic_commit_writes_history_plan_and_receipt_once(self):
        with patch.object(service,'put',wraps=store.put) as write:result=self.apply()
        self.assertEqual(write.call_count,1)
        written=write.call_args.args[1];receipt=result['instruction_result']
        self.assertEqual(written['history'][0]['plan'],self.project['plan'])
        self.assertEqual(written['plan']['last_instruction']['id'],receipt['id'])
        self.assertEqual(written['instruction_receipts'][0]['receipt']['id'],receipt['id'])
        self.assertEqual(written['version'],receipt['applied_version'])

    def test_network_replay_returns_the_committed_subset_without_reapplying(self):
        first=self.apply();again=self.apply()
        self.assert_applied(again)
        self.assertTrue(again['instruction_result']['replayed'])
        self.assertEqual(again['instruction_result']['id'],first['instruction_result']['id'])
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(len(store.get('project','fixture')['instruction_receipts']),1)

    def test_same_key_cannot_change_text_or_execution_mode(self):
        self.apply()
        for body in ({**self.body,'text':'曝光设为2'}, {**self.body,'partial_mode':'review'}):
            with self.subTest(body=body),self.assertRaises(service.InstructionError) as caught:
                self.apply(body=body)
            self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(len(store.get('project','fixture')['history']),1)

    def test_undo_and_replay_do_not_reapply_old_relative_lighting(self):
        first=self.apply()
        undone=self.web_client().post('/api/projects/fixture/undo').get_json()
        self.assertEqual(undone['version'],5)
        self.assertEqual(undone['plan'],self.project['plan'])
        again=self.apply()
        self.assertTrue(again['instruction_result']['replayed'])
        self.assertEqual(again['instruction_result']['id'],first['instruction_result']['id'])
        self.assertEqual(again['version'],5)
        self.assertEqual(again['plan'],self.project['plan'])
        self.assertEqual(self.model.call_count,1)

    def test_inflight_retry_keeps_the_same_request_and_does_not_start_inference_again(self):
        errors=[]
        def running(*args,**kwargs):
            try:self.apply()
            except service.InstructionError as error:errors.append(error)
            return self.partial(*args,**kwargs)
        self.model.side_effect=running
        self.assert_applied(self.apply())
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0].error_code,'instruction_in_progress')
        self.assertTrue(errors[0].retryable)
        self.assertEqual(self.model.call_count,1)

    def test_real_http_response_commits_and_limits_preview_to_current_shot(self):
        client=self.web_client()
        response=client.post('/api/projects/fixture/instruct',json=self.body,headers={'Idempotency-Key':'auto-http'})
        self.assertEqual(response.status_code,200)
        self.assert_applied(response.get_json())
        replay=client.post('/api/projects/fixture/instruct',json=self.body,headers={'Idempotency-Key':'auto-http'})
        self.assertEqual(replay.status_code,200)
        self.assertTrue(replay.get_json()['instruction_result']['replayed'])
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(store.all_records('job'),[])

    def test_effective_light_values_are_reported_without_replacing_storage_diff(self):
        current=store.get('project','fixture')
        current['plan']['lighting']['key_power']=.8
        current['plan']['lighting']['fill_power']=.9
        current['plan']['shot_adjustments']={'hero':{'lighting':{'rim_power':1.1}}}
        store.put('project',current)
        before=engine.effective_plan(current['plan'],'hero')
        desired={'key_power':1.3,'fill_power':1.2,'rim_power':1.2,'environment':1.2}
        def lights(*args,**kwargs):
            outcome=self.outcome(*args,**kwargs);candidate=outcome.proposal['candidate_plan']
            candidate['exposure']=args[0]['exposure'];candidate['lighting'].update(desired)
            raise outcome
        self.model.side_effect=lights
        result=self.apply();receipt=result['instruction_result']
        rows={row['path']:row for row in receipt['changes']}
        for key,value in desired.items():
            row=rows['shot_adjustments.hero.lighting.'+key]
            self.assertEqual(row['effective_before'],before['lighting'][key])
            self.assertEqual(row['effective_after'],value)
            self.assertEqual(row['after'],value)
        self.assertIsNone(rows['shot_adjustments.hero.lighting.key_power']['before'])
        self.assertEqual(rows['shot_adjustments.hero.lighting.rim_power']['before'],1.1)
        self.assertEqual(result['plan']['lighting'],current['plan']['lighting'])

    def test_verified_already_satisfied_subset_keeps_pending_without_a_fake_version(self):
        def satisfied(*args,**kwargs):
            outcome=self.outcome(*args,**kwargs)
            outcome.proposal['candidate_plan']=copy.deepcopy(args[0])
            outcome.proposal['ready'][0].update(status='already_satisfied',summary='已有参数已满足灯光要求')
            raise outcome
        self.model.side_effect=satisfied
        result=self.apply();receipt=result['instruction_result']
        self.assertEqual(result['version'],3);self.assertEqual(result['history'],[])
        self.assertEqual(result['plan'],self.project['plan'])
        self.assertEqual(receipt['status'],'no_change');self.assertTrue(receipt['partial'])
        self.assertEqual(receipt['changes'],[]);self.assertEqual(receipt['preview_shot_ids'],[])
        self.assertEqual(receipt['remaining_text'],'四个轮子是黑色橡胶')
        self.assertEqual(receipt['ready'][0]['status'],'already_satisfied')
        self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))
        self.assertEqual(store.all_records('job'),[])
        replay=self.apply();self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(self.model.call_count,1)

    def test_empty_ready_patch_is_not_misreported_as_completed(self):
        def empty(*args,**kwargs):
            outcome=self.outcome(*args,**kwargs);outcome.proposal['candidate_plan']=copy.deepcopy(args[0])
            raise outcome
        self.model.side_effect=empty
        with self.assertRaises(service.InstructionError) as caught:self.apply()
        self.assertEqual(caught.exception.error_code,'instruction_targets_unresolved')
        self.assert_unmodified()

    def test_all_pending_keeps_the_plan_and_does_not_create_a_fake_proposal(self):
        outcome=InstructionOutcome('needs_clarification','轮胎还没有完整定位')
        outcome.pending=[{'id':'r1','quote':'四个轮子是黑色橡胶','label':'轮胎','reason':'还缺少完整轮胎目标',
                          'status':'needs_clarification','action':'locate_part'}]
        outcome.remaining_text='四个轮子是黑色橡胶';self.model.side_effect=outcome
        response=self.web_client().post('/api/projects/fixture/instruct',json=self.body,
                                       headers={'Idempotency-Key':'all-pending'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.get_json()['error_code'],'instruction_targets_unresolved')
        self.assertEqual(response.get_json()['pending'][0]['action'],'locate_part')
        self.assert_unmodified()

    def test_source_bytes_change_during_inference_blocks_even_lighting_only_commit(self):
        def changed(*args,**kwargs):
            Image.new('RGB',(24,24),'black').save(self.png)
            return self.partial(*args,**kwargs)
        self.model.side_effect=changed
        with self.assertRaises(service.InstructionError) as caught:self.apply()
        self.assertEqual(caught.exception.status_code,409)
        self.assert_unmodified()

    def test_same_version_plan_change_during_inference_is_not_overwritten(self):
        def changed(*args,**kwargs):
            current=store.get('project','fixture');current['plan']['margin']=.23;store.put('project',current)
            return self.partial(*args,**kwargs)
        self.model.side_effect=changed
        with self.assertRaises(service.InstructionError) as caught:self.apply()
        self.assertEqual(caught.exception.status_code,409)
        current=store.get('project','fixture')
        self.assertEqual(current['plan']['margin'],.23);self.assertEqual(current['version'],3)
        self.assertEqual(current['history'],[]);self.assertNotIn('instruction_receipts',current)
        self.assertNotIn('fixture',service._INFLIGHT)

    def test_metadata_change_during_inference_preserves_new_metadata_without_committing(self):
        def changed(*args,**kwargs):
            current=store.get('project','fixture');current['metadata']['material_note']='新的材质说明';store.put('project',current)
            return self.partial(*args,**kwargs)
        self.model.side_effect=changed
        with self.assertRaises(service.InstructionError) as caught:self.apply()
        self.assertEqual(caught.exception.status_code,409)
        current=store.get('project','fixture')
        self.assertEqual(current['metadata']['material_note'],'新的材质说明')
        self.assert_unmodified()

    def test_unrelated_read_only_diagnosis_refresh_does_not_block_auto_commit(self):
        def reviewed(*args,**kwargs):
            current=store.get('project','fixture')
            current['outputs'][0]['review']={'checked_at':42,'summary':'更新只读诊断'}
            store.put('project',current)
            return self.partial(*args,**kwargs)
        self.model.side_effect=reviewed
        self.assert_applied(self.apply())
        self.assertEqual(store.get('project','fixture')['outputs'][0]['review']['checked_at'],42)

    def test_validator_cannot_expand_the_verified_partial_subset(self):
        def expand(project_id,candidate):candidate['margin']=.3;return candidate
        with self.assertRaises(service.InstructionError):self.apply(validate_plan=expand)
        self.assert_unmodified()

    def test_invented_pending_work_is_rejected_before_auto_application(self):
        def invented(*args,**kwargs):
            outcome=self.outcome(*args,**kwargs);outcome.proposal['pending'][0]['quote']='增加真实倒角'
            raise outcome
        self.model.side_effect=invented
        with self.assertRaises(service.InstructionError):self.apply()
        self.assert_unmodified()

    def test_completed_and_pending_clauses_cannot_cross_the_execution_boundary(self):
        for field,extra in (('remaining_text','调整灯光'),('execution_text','四个轮子是黑色橡胶')):
            with self.subTest(field=field):
                def crossed(*args,**kwargs):
                    outcome=self.outcome(*args,**kwargs);outcome.proposal[field]+='；'+extra
                    raise outcome
                self.model.side_effect=crossed
                with self.assertRaises(service.InstructionError):self.apply()
                self.assert_unmodified()

    def test_global_protection_survives_into_remaining_text(self):
        def protected(*args,**kwargs):
            outcome=self.outcome(*args,**kwargs)
            outcome.proposal['execution_text']+='；保持镜头不变'
            outcome.proposal['remaining_text']+='；保持镜头不变'
            raise outcome
        self.model.side_effect=protected
        result=self.apply(body={**self.body,'text':self.text+'；保持镜头不变'})
        self.assertEqual(result['version'],4)
        self.assertIn('保持镜头不变',result['instruction_result']['remaining_text'])
        self.assertEqual(result['plan']['shots'],self.project['plan']['shots'])
        self.assertNotIn('调整灯光',result['instruction_result']['remaining_text'])

    def test_real_planner_hard_color_protection_cannot_be_bypassed_by_partial_execution(self):
        self.use_real_planner()
        reply=self.ai_proposal([
            {'id':'r1','status':'needs_clarification','summary':'轮胎还没有完整定位','patch':{},'target_bindings':[]},
            {'id':'r2','status':'ready','summary':'调整灯光',
             'patch':{'exposure':.35,'material_overrides':[{'name':'Steel','color':'#FF0000'}]},'target_bindings':[]}])
        with patch.object(service.ai_client,'_post',return_value=reply) as send:
            with self.assertRaises(ValueError):self.apply(body={**self.body,'text':'保持颜色不变；'+self.text})
        self.assertEqual(send.call_count,1);self.assert_unmodified()

    def test_other_shot_camera_changes_are_rejected_before_auto_commit(self):
        def foreign(*args,**kwargs):
            outcome=self.outcome(*args,**kwargs)
            shot=next(row for row in outcome.proposal['candidate_plan']['shots'] if row['id']!='hero')
            shot['azimuth']+=10
            raise outcome
        self.model.side_effect=foreign
        with self.assertRaises(service.InstructionError):self.apply()
        self.assert_unmodified()

    def test_busy_project_refuses_new_auto_work_before_inference(self):
        store.put('job',{'id':'active','project_id':'fixture','status':'running'})
        with self.assertRaises(service.InstructionError) as caught:self.apply()
        self.assertEqual(caught.exception.status_code,409)
        self.model.assert_not_called()
        current=store.get('project','fixture')
        self.assertEqual(current['version'],3);self.assertEqual(current['plan'],self.project['plan'])
        self.assertNotIn('instruction_receipts',current)


if __name__=='__main__':unittest.main()

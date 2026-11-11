"""Explicit partial-instruction confirmation with isolated local fixtures.

No test reads user settings, contacts an AI provider, runs Blender, or touches
an actual project. Candidate plans are supplied by a mocked validated planner.
"""
import copy
import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import engine
import instruction_service as service
import studio_store as store
from intelligence_rules import InstructionOutcome
from planner import default_plan,instruct as real_instruct


class InstructionProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for module in (store,engine,service):
            mocked=patch.object(module,'DATA',self.root);mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear();self.addCleanup(service._INFLIGHT.clear)
        self.folder=self.root/'fixture/runs/source';self.folder.mkdir(parents=True)
        self.png=self.folder/'hero.png';Image.new('RGB',(24,24),'white').save(self.png)
        self.result_file=self.folder/'result.json'
        self.result={'success':True,'product_samples':[{'object_name':'Panel','material_name':'Steel','x':.4,'y':.3}]}
        self.result_file.write_text(json.dumps(self.result),encoding='utf-8')
        self.source={'output_id':'source','shot_id':'hero','quality':'preview','version':3}
        self.project={'id':'fixture','name':'测试柜体','version':3,'status':'ready','history':[],
            'metadata':{'material_note':'保留标识'},
            'analysis':{'objects':[{'name':'Panel','type':'MESH','materials':['Steel']}],
                        'materials':[{'name':'Steel','suggested_preset':'brushed_metal'}]},
            'plan':default_plan(),'outputs':[{'job_id':'source','shot_id':'hero','quality':'preview','version':3,
                'png_url':'/files/fixture/runs/source/hero.png','result_url':'/files/fixture/runs/source/result.json'}]}
        store.put('project',copy.deepcopy(self.project))
        self.text='四个轮子是黑色橡胶；调整灯光'
        # The mocked proposal and real-model replies below use the v1 contract.
        self.body={'text':self.text,'expected_version':3,'shot_id':'hero','source':self.source,'partial_mode':'review','protocol':'legacy_v1'}
        mocked=patch.object(service.planner,'instruct',side_effect=self.partial)
        self.model=mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(service.ai_client,'_post',side_effect=AssertionError('No real AI call is permitted'))
        self.http=mocked.start();self.addCleanup(mocked.stop)

    def partial(self,plan,text,analysis,**kwargs):
        candidate=copy.deepcopy(plan);candidate['exposure']+=.35;candidate['source']='ai'
        candidate['last_instruction']={'text':text,'summary':'提高当前镜头曝光','source':'ai',
            'provider':'fixture','model':'fixture-vision','used_image':bool(kwargs.get('image_path')),
            'used_reference':bool(kwargs.get('reference_path')),'notes':[],
            'protections':['preserve_color'],'protected_fields':['color']}
        outcome=InstructionOutcome('partial_available','有可先完成的调整',source='ai',used_image=True,
                                   provider='fixture',model='fixture-vision')
        outcome.proposal={'candidate_plan':candidate,
            'ready':[{'id':'r2','quote':'调整灯光','summary':'提高当前镜头曝光','label':'灯光'}],
            'pending':[{'id':'r1','quote':'四个轮子是黑色橡胶','reason':'请确认四个轮胎的部位','label':'轮胎'}],
            'execution_text':'调整灯光','remaining_text':'四个轮子是黑色橡胶'}
        raise outcome

    def prepare(self,key='prepare-1',body=None):
        return service.apply_instruction('fixture',self.body if body is None else body,key)

    def confirmation(self,prepared):
        return {'proposal_id':prepared['instruction_result']['proposal_id'],'expected_version':3}

    def confirm(self,prepared,key='confirm-1',**kwargs):
        return service.confirm_instruction('fixture',self.confirmation(prepared),key,**kwargs)

    def web_client(self):
        env=Path(__file__).resolve().parents[1]/'.env';original_exists=Path.exists
        with patch.object(Path,'exists',lambda path:False if path.absolute()==env else original_exists(path)):
            module=importlib.import_module('app')
        mocked=patch.object(module,'DATA',self.root);mocked.start();self.addCleanup(mocked.stop)
        return module.app.test_client()

    def use_real_planner(self):
        self.model.side_effect=real_instruct
        config={'enabled':True,'provider':'anthropic','base_url':'https://example.invalid',
                'api_key':'fixture-token','model':'fixture-vision'}
        for name in ('get_config','get_vision_config'):
            mocked=patch('ai_config.'+name,return_value=copy.deepcopy(config));mocked.start();self.addCleanup(mocked.stop)
        for name in ('record_result','record_vision_result'):
            mocked=patch('ai_config.'+name);mocked.start();self.addCleanup(mocked.stop)

    def ai_proposal(self,rows):
        return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':service.ai_client.TOOL_NAME,
            'input':{'status':'partial_available','patch':{},'summary':'可先应用已确认项',
                     'notes':[],'unhandled':[],'target_bindings':[],'requirement_results':rows}}]}

    def assertStale(self,prepared):
        with self.assertRaises(service.InstructionError) as caught: self.confirm(prepared)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(caught.exception.error_code,'instruction_proposal_stale')
        self.assertNotIn('fixture',service._INFLIGHT)
        self.assertEqual(self.model.call_count,1)

    def test_first_response_is_reviewable_and_does_not_apply_or_preview(self):
        result=self.prepare();receipt=result['instruction_result'];stored=store.get('project','fixture')
        self.assertEqual(result['plan'],self.project['plan']);self.assertEqual(result['version'],3)
        self.assertEqual(result['history'],[]);self.assertEqual(receipt['status'],'needs_confirmation')
        self.assertEqual(receipt['changes'],[]);self.assertEqual(receipt['preview_shot_ids'],[])
        self.assertEqual(receipt['base_version'],receipt['applied_version'])
        self.assertEqual(receipt['text'],self.text);self.assertEqual(receipt['shot_id'],'hero')
        self.assertEqual(receipt['remaining_text'],'四个轮子是黑色橡胶')
        self.assertEqual(receipt['pending'][0]['label'],'轮胎')
        self.assertEqual(receipt['proposed_changes'],[
            {'path':'shot_adjustments.hero.exposure_delta','before':None,'after':.35}])
        self.assertEqual(len(stored[service.PROPOSALS_KEY]),1)
        self.assertEqual(receipt['id'],stored['instruction_receipts'][0]['receipt']['id'])
        self.assertNotIn(service.PROPOSALS_KEY,result)

    def test_initial_network_retry_returns_same_proposal_without_inference(self):
        first=self.prepare();second=self.prepare()
        self.assertEqual(first['instruction_result']['proposal_id'],second['instruction_result']['proposal_id'])
        self.assertTrue(second['instruction_result']['replayed'])
        self.assertEqual(second['version'],3);self.assertEqual(self.model.call_count,1)
        self.assertEqual(len(store.get('project','fixture')[service.PROPOSALS_KEY]),1)

    def test_confirmation_applies_saved_actual_diff_and_preserves_pending(self):
        prepared=self.prepare();result=self.confirm(prepared);receipt=result['instruction_result']
        self.assertEqual(self.model.call_count,1);self.http.assert_not_called()
        self.assertEqual(result['version'],4);self.assertEqual(len(result['history']),1)
        self.assertEqual(receipt['status'],'applied');self.assertTrue(receipt['partial'])
        self.assertEqual(receipt['pending'],prepared['instruction_result']['pending'])
        self.assertEqual(receipt['remaining_text'],'四个轮子是黑色橡胶')
        self.assertEqual(receipt['changes'],prepared['instruction_result']['proposed_changes'])
        self.assertEqual(receipt['preview_shot_ids'],['hero'])
        self.assertEqual(result['plan']['object_material_overrides'],self.project['plan']['object_material_overrides'])
        self.assertEqual(result['plan']['exposure'],0)
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['exposure'],.35)
        self.assertEqual(result['plan']['last_instruction']['text'],'调整灯光')
        self.assertEqual(result['plan']['last_instruction']['original_text'],self.text)
        self.assertEqual(service._recent(result)[-1]['text'],'调整灯光')
        self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))

    def test_real_planner_structured_proposal_preserves_constraints_without_second_ai_call(self):
        self.use_real_planner()
        data=self.ai_proposal([
            {'id':'r1','status':'needs_clarification','summary':'请确认四个轮胎的位置','patch':{},'target_bindings':[]},
            {'id':'r2','status':'ready','summary':'提高曝光','patch':{'exposure':.35},'target_bindings':[]}])
        with patch.object(service.ai_client,'_post',return_value=data) as send:
            prepared=self.prepare(body={**self.body,'text':self.text+'；保持镜头不变'})
            self.assertEqual(prepared['version'],3)
            self.assertIn('保持镜头不变',prepared['instruction_result']['remaining_text'])
            result=self.confirm(prepared)
            self.assertEqual(send.call_count,1)
        self.assertEqual(result['version'],4);self.assertEqual(result['instruction_result']['model'],'fixture-vision')
        self.assertTrue(result['instruction_result']['used_image'])
        self.assertEqual(result['plan']['shots'],self.project['plan']['shots'])

    def test_real_planner_confirmed_material_can_apply_while_complete_wheels_stay_pending(self):
        self.use_real_planner()
        text='四个轮子是黑色橡胶；顶部是拉丝不锈钢；调整灯光'
        data=self.ai_proposal([
            {'id':'r1','status':'needs_clarification','summary':'请确认四个轮胎的位置','patch':{},'target_bindings':[]},
            {'id':'r2','status':'ready','summary':'顶部拉丝材质',
             'patch':{'object_material_overrides':[{'object_name':'Panel','material_name':'Steel','preset':'brushed_metal'}]},
             'target_bindings':[{'phrase':'顶部','purpose':'material','target_ids':['t1']}]},
            {'id':'r3','status':'ready','summary':'提高曝光','patch':{'exposure':.35},'target_bindings':[]}])
        with patch.object(service.ai_client,'_post',return_value=data) as send:
            prepared=self.prepare(body={**self.body,'text':text})
            self.assertEqual(prepared['plan']['object_material_overrides'],[])
            result=self.confirm(prepared)
            self.assertEqual(send.call_count,1)
        receipt=result['instruction_result']
        self.assertEqual(receipt['target_verification'],'render_samples')
        self.assertEqual(receipt['remaining_text'],'四个轮子是黑色橡胶')
        self.assertEqual(len(result['plan']['object_material_overrides']),1)
        self.assertEqual(result['plan']['object_material_overrides'][0]['object_name'],'Panel')

    def test_confirmation_and_history_receipt_commit_atomically(self):
        prepared=self.prepare()
        with patch.object(service,'put',wraps=store.put) as commit:
            result=self.confirm(prepared)
        self.assertEqual(commit.call_count,1)
        written=commit.call_args.args[1]
        self.assertEqual(written['version'],4)
        self.assertEqual(written['instruction_receipts'][-1]['receipt']['id'],result['instruction_result']['id'])
        self.assertEqual(written['plan']['last_instruction']['id'],result['instruction_result']['id'])
        self.assertEqual(written['history'][-1]['plan'],self.project['plan'])

    def test_confirmation_network_retry_and_new_key_both_apply_only_once(self):
        prepared=self.prepare();first=self.confirm(prepared)
        same=self.confirm(prepared);different=self.confirm(prepared,key='confirm-again')
        for replay in (same,different):
            self.assertEqual(replay['version'],4);self.assertEqual(len(replay['history']),1)
            self.assertTrue(replay['instruction_result']['replayed'])
            self.assertEqual(replay['instruction_result']['id'],first['instruction_result']['id'])
        self.assertEqual(self.model.call_count,1)
        with self.assertRaises(service.InstructionError):
            service.confirm_instruction('fixture',{'proposal_id':'e'*32,'expected_version':4},'confirm-again')

    def test_prepare_key_cannot_be_reused_as_confirmation(self):
        prepared=self.prepare()
        with self.assertRaises(service.InstructionError) as caught: self.confirm(prepared,key='prepare-1')
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_confirmation_requires_key_and_accepts_no_client_patch_or_text(self):
        prepared=self.prepare();body=self.confirmation(prepared)
        for invalid in ({**body,'patch':{'exposure':3}},{**body,'text':'应用所有材质'},
                        {**body,'expected_version':True},{'proposal_id':body['proposal_id']},[],
                        {**body,'proposal_id':'../another-project'}):
            with self.subTest(invalid=invalid),self.assertRaises(service.InstructionError):
                service.confirm_instruction('fixture',invalid,'confirm-invalid')
        with self.assertRaises(service.InstructionError): service.confirm_instruction('fixture',body)
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_proposal_id_is_scoped_to_its_project(self):
        prepared=self.prepare();other=copy.deepcopy(self.project);other['id']='other';store.put('project',other)
        with self.assertRaises(service.InstructionError) as caught:
            service.confirm_instruction('other',self.confirmation(prepared),'other-confirm')
        self.assertEqual(caught.exception.error_code,'instruction_proposal_stale')
        self.assertEqual(store.get('project','other')['version'],3)

    def test_changed_project_version_invalidates_proposal(self):
        prepared=self.prepare();project=store.get('project','fixture');project['version']=4;store.put('project',project)
        self.assertStale(prepared);self.assertEqual(store.get('project','fixture')['history'],[])

    def test_changed_plan_without_version_invalidates_proposal(self):
        prepared=self.prepare();project=store.get('project','fixture');project['plan']['margin']=.3;store.put('project',project)
        self.assertStale(prepared);self.assertEqual(store.get('project','fixture')['plan']['margin'],.3)

    def test_changed_analysis_invalidates_proposal(self):
        prepared=self.prepare();project=store.get('project','fixture');project['analysis']['objects'][0]['name']='Different';store.put('project',project)
        self.assertStale(prepared)

    def test_changed_image_at_same_url_invalidates_proposal(self):
        prepared=self.prepare();Image.new('RGB',(24,24),'red').save(self.png)
        self.assertStale(prepared);self.assertEqual(store.get('project','fixture')['version'],3)

    def test_changed_sample_file_at_same_url_invalidates_proposal(self):
        prepared=self.prepare();self.result['product_samples'][0]['y']=.7
        self.result_file.write_text(json.dumps(self.result),encoding='utf-8')
        self.assertStale(prepared)

    def test_changed_source_record_invalidates_proposal(self):
        prepared=self.prepare();project=store.get('project','fixture')
        project['outputs'][0]['png_url']='/files/fixture/runs/source/missing.png';store.put('project',project)
        self.assertStale(prepared)

    def test_read_only_diagnosis_update_does_not_expire_proposal(self):
        prepared=self.prepare();project=store.get('project','fixture')
        project['outputs'][0]['review']={'checked_at':1234,'source':'rules','summary':'新的只读诊断'}
        store.put('project',project)
        self.assertEqual(self.confirm(prepared)['version'],4)
        self.assertEqual(self.model.call_count,1)

    def test_changed_reference_image_invalidates_proposal(self):
        reference=self.root/'fixture/references/front.png';reference.parent.mkdir()
        Image.new('RGB',(24,24),'blue').save(reference)
        project=store.get('project','fixture');project['metadata']['reference']=str(reference);store.put('project',project)
        prepared=self.prepare(body={**self.body,'text':self.text+'；按参考图调整'})
        Image.new('RGB',(24,24),'green').save(reference)
        self.assertStale(prepared)

    def test_changed_scene_snapshot_invalidates_proposal(self):
        blend=self.folder/'hero.blend';blend.write_bytes(b'fixture-only-not-a-Blender-file')
        project=store.get('project','fixture');project['outputs'][0]['blend_url']='/files/fixture/runs/source/hero.blend'
        store.put('project',project)
        prepared=self.prepare();blend.write_bytes(b'changed-fixture-only')
        self.assertStale(prepared)

    def test_source_change_during_inference_prevents_proposal_receipt(self):
        def change_source(*args,**kwargs):
            Image.new('RGB',(24,24),'black').save(self.png)
            return self.partial(*args,**kwargs)
        self.model.side_effect=change_source
        with self.assertRaises(service.InstructionError) as caught: self.prepare()
        self.assertEqual(caught.exception.error_code,'instruction_proposal_stale')
        project=store.get('project','fixture')
        self.assertNotIn('instruction_receipts',project);self.assertEqual(project['version'],3)

    def test_version_change_during_confirmation_is_not_overwritten(self):
        prepared=self.prepare()
        def validate(project_id,candidate):
            current=store.get('project',project_id);current['plan']['margin']=.25;current['version']=4;store.put('project',current)
            return candidate
        with self.assertRaises(service.InstructionError): self.confirm(prepared,validate_plan=validate)
        current=store.get('project','fixture')
        self.assertEqual(current['plan']['margin'],.25);self.assertEqual(current['history'],[])
        self.assertNotIn('fixture',service._INFLIGHT)

    def test_confirmation_revalidation_cannot_expand_the_reviewed_diff(self):
        prepared=self.prepare()
        def expand(project_id,candidate): candidate['margin']=.3;return candidate
        with self.assertRaises(service.InstructionError): self.confirm(prepared,validate_plan=expand)
        self.assertEqual(store.get('project','fixture')['plan'],self.project['plan'])
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_busy_project_does_not_apply_the_proposal(self):
        prepared=self.prepare();store.put('job',{'id':'running','project_id':'fixture','status':'running'})
        with self.assertRaises(service.InstructionError) as caught: self.confirm(prepared)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(store.get('project','fixture')['version'],3);self.assertEqual(self.model.call_count,1)

    def test_inflight_confirmation_is_retryable_and_does_not_repeat(self):
        prepared=self.prepare();errors=[]
        def validate(project_id,candidate):
            try: self.confirm(prepared)
            except service.InstructionError as exc: errors.append(exc)
            return candidate
        result=self.confirm(prepared,validate_plan=validate)
        self.assertEqual(result['version'],4);self.assertEqual(len(errors),1)
        self.assertEqual(errors[0].error_code,'instruction_in_progress');self.assertTrue(errors[0].retryable)

    def test_confirmation_validation_does_not_hold_store_lock(self):
        prepared=self.prepare();acquired=threading.Event()
        def validate(project_id,candidate):
            def other_operation():
                with store.LOCK: acquired.set()
            worker=threading.Thread(target=other_operation);worker.start()
            self.assertTrue(acquired.wait(1));worker.join(1)
            return candidate
        self.assertEqual(self.confirm(prepared,validate_plan=validate)['version'],4)

    def test_no_real_diff_does_not_offer_a_confirmation_button(self):
        def unchanged(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['candidate_plan']=copy.deepcopy(args[0])
                raise
        self.model.side_effect=unchanged
        with self.assertRaises(service.InstructionError) as caught: self.prepare()
        self.assertEqual(caught.exception.error_code,'instruction_targets_unresolved')
        self.assertEqual(caught.exception.remaining_text,'四个轮子是黑色橡胶')
        self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))

    def test_no_real_diff_with_parameter_pending_asks_for_parameters_not_parts(self):
        def unchanged(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['candidate_plan']=copy.deepcopy(args[0])
                outcome.proposal['pending']=[{'id':'r1','quote':'焦距设为85mm','reason':'镜头参数未完整返回',
                                             'status':'needs_clarification','action':'clarify_parameters'}]
                outcome.proposal['remaining_text']='焦距设为85mm'
                raise
        self.model.side_effect=unchanged
        response=self.web_client().post('/api/projects/fixture/instruct',json={**self.body,'text':'焦距设为85mm；调整灯光'},
                                       headers={'Idempotency-Key':'no-diff-parameters'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.get_json()['error'],'尚未得到可应用的参数，请补充或重新分析要求。')
        self.assertEqual(response.get_json()['pending'][0]['action'],'clarify_parameters')
        self.assertEqual(response.get_json()['remaining_text'],'焦距设为85mm')
        current=store.get('project','fixture')
        self.assertEqual(current['version'],3);self.assertEqual(current['plan'],self.project['plan'])
        self.assertNotIn(service.PROPOSALS_KEY,current)

    def test_invented_pending_quote_cannot_be_saved_as_user_intent(self):
        def invented(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['pending'][0]['quote']='添加真实倒角'
                raise
        self.model.side_effect=invented
        with self.assertRaises(service.InstructionError): self.prepare()
        self.assertEqual(store.get('project','fixture')['plan'],self.project['plan'])

    def test_pending_text_keeps_global_protection_for_the_next_attempt(self):
        def preserve(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['execution_text']+='；保持镜头不变'
                outcome.proposal['remaining_text']+='；保持镜头不变'
                raise
        self.model.side_effect=preserve
        prepared=self.prepare(body={**self.body,'text':self.text+'；保持镜头不变'})
        self.assertEqual(prepared['instruction_result']['remaining_text'],'四个轮子是黑色橡胶；保持镜头不变')
        applied=self.confirm(prepared)
        self.assertIn('保持镜头不变',applied['instruction_result']['remaining_text'])

    def test_pending_text_cannot_silently_drop_the_original_protection(self):
        with self.assertRaises(service.InstructionError):
            self.prepare(body={**self.body,'text':self.text+'；保持镜头不变'})
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_private_candidate_and_guard_are_not_in_any_project_response(self):
        prepared=self.prepare();client=self.web_client()
        responses=[prepared,engine.project_view('fixture'),client.get('/api/projects/fixture').get_json(),
                   client.get('/api/projects').get_json()]
        for result in responses:
            serialized=json.dumps(result)
            for private in ('_instruction_proposals','candidate_plan','"guard"','"execution_text"'):
                self.assertNotIn(private,serialized)
        self.assertIn(service.PROPOSALS_KEY,store.get('project','fixture'))

    def test_http_confirmation_contract_and_replay(self):
        client=self.web_client()
        initial=client.post('/api/projects/fixture/instruct',json=self.body,headers={'Idempotency-Key':'prepare-http'})
        self.assertEqual(initial.status_code,200);prepared=initial.get_json();body=self.confirmation(prepared)
        first=client.post('/api/projects/fixture/instruct/confirm',json=body,headers={'Idempotency-Key':'confirm-http'})
        retry=client.post('/api/projects/fixture/instruct/confirm',json=body,headers={'Idempotency-Key':'confirm-http'})
        self.assertEqual(first.status_code,200);self.assertEqual(retry.status_code,200)
        self.assertTrue(retry.get_json()['instruction_result']['replayed']);self.assertEqual(self.model.call_count,1)
        self.assertEqual(first.get_json()['version'],4)

    def test_http_stale_proposal_has_a_machine_readable_error(self):
        prepared=self.prepare();Image.new('RGB',(24,24),'red').save(self.png)
        response=self.web_client().post('/api/projects/fixture/instruct/confirm',json=self.confirmation(prepared),
                                        headers={'Idempotency-Key':'confirm-http'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.get_json()['error_code'],'instruction_proposal_stale')

    def test_http_all_pending_returns_structured_short_items(self):
        outcome=InstructionOutcome('needs_clarification','请先确认轮胎位置')
        outcome.pending=[{'id':'r1','quote':'四个轮子是黑色橡胶','reason':'需确认四个轮胎','label':'轮胎','status':'needs_clarification'}]
        outcome.remaining_text='四个轮子是黑色橡胶';self.model.side_effect=outcome
        response=self.web_client().post('/api/projects/fixture/instruct',json=self.body,
                                        headers={'Idempotency-Key':'pending-http'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.get_json()['error_code'],'instruction_targets_unresolved')
        self.assertEqual(response.get_json()['pending'][0]['label'],'轮胎')
        self.assertEqual(response.get_json()['pending'][0]['status'],'needs_clarification')
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_explicit_unsupported_item_stays_distinct_from_a_part_to_locate(self):
        def unsupported(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['pending']=[{'id':'r1','quote':'增加真实倒角','reason':'当前没有几何倒角操作',
                                             'label':'真实倒角','status':'unsupported'}]
                outcome.proposal['remaining_text']='增加真实倒角'
                raise
        self.model.side_effect=unsupported;client=self.web_client()
        response=client.post('/api/projects/fixture/instruct',json={**self.body,'text':'增加真实倒角；调整灯光'},
                             headers={'Idempotency-Key':'prepare-unsupported'})
        self.assertEqual(response.status_code,200);prepared=response.get_json()
        self.assertEqual(prepared['instruction_result']['pending'][0]['status'],'unsupported')
        applied=self.confirm(prepared)
        self.assertEqual(applied['instruction_result']['pending'][0]['status'],'unsupported')
        self.assertEqual(applied['instruction_result']['remaining_text'],'增加真实倒角')

    def test_parameter_clarification_action_survives_confirmation_and_replay(self):
        def parameters(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['pending']=[{'id':'r1','quote':'焦距设为85mm','reason':'镜头参数尚未完整返回',
                                             'label':'镜头','status':'needs_clarification','action':'clarify_parameters'}]
                outcome.proposal['remaining_text']='焦距设为85mm'
                raise
        self.model.side_effect=parameters;client=self.web_client()
        response=client.post('/api/projects/fixture/instruct',json={**self.body,'text':'焦距设为85mm；调整灯光'},
                             headers={'Idempotency-Key':'prepare-parameters'})
        self.assertEqual(response.status_code,200);prepared=response.get_json()
        self.assertEqual(prepared['version'],3)
        self.assertEqual(prepared['instruction_result']['pending'][0]['action'],'clarify_parameters')
        applied=self.confirm(prepared)
        self.assertEqual(applied['instruction_result']['pending'][0]['action'],'clarify_parameters')
        self.assertEqual(applied['instruction_result']['remaining_text'],'焦距设为85mm')
        replay=self.confirm(prepared,key='confirm-parameters-again')
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(replay['version'],4);self.assertEqual(len(replay['history']),1)
        self.assertEqual(self.model.call_count,1);self.http.assert_not_called()

    def test_pending_action_rejects_unknown_operation_without_saving(self):
        def invalid(*args,**kwargs):
            try: self.partial(*args,**kwargs)
            except InstructionOutcome as outcome:
                outcome.proposal['pending'][0]['action']='guess_and_apply'
                raise
        self.model.side_effect=invalid
        with self.assertRaises(service.InstructionError) as caught: self.prepare()
        self.assertIn('后续操作无效',str(caught.exception))
        current=store.get('project','fixture')
        self.assertEqual(current['version'],3);self.assertEqual(current['plan'],self.project['plan'])
        self.assertNotIn(service.PROPOSALS_KEY,current)
        self.assertNotIn('fixture',service._INFLIGHT)

    def test_all_pending_parameter_action_is_public_without_a_fake_part_target(self):
        outcome=InstructionOutcome('needs_clarification','请重新提交镜头参数要求')
        outcome.pending=[{'id':'r1','quote':'焦距设为85mm','reason':'镜头参数未完整返回','label':'镜头',
                          'status':'needs_clarification','action':'clarify_parameters'}]
        outcome.remaining_text='焦距设为85mm';self.model.side_effect=outcome
        response=self.web_client().post('/api/projects/fixture/instruct',json={**self.body,'text':'焦距设为85mm'},
                                       headers={'Idempotency-Key':'pending-parameters-http'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.get_json()['pending'][0]['action'],'clarify_parameters')
        self.assertEqual(response.get_json()['remaining_text'],'焦距设为85mm')
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_undo_restores_the_exact_base_and_old_confirm_does_not_reapply(self):
        prepared=self.prepare();applied=self.confirm(prepared)
        undone=self.web_client().post('/api/projects/fixture/undo').get_json()
        self.assertEqual(undone['version'],5);self.assertEqual(undone['plan'],self.project['plan'])
        replay=self.confirm(prepared,key='confirm-after-undo')
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(replay['instruction_result']['id'],applied['instruction_result']['id'])
        self.assertEqual(replay['version'],5);self.assertEqual(replay['plan'],self.project['plan'])

    def test_deleting_fixture_project_removes_proposals_and_disallows_confirmation(self):
        prepared=self.prepare();client=self.web_client()
        deleted=client.delete('/api/projects/fixture')
        self.assertEqual(deleted.status_code,200)
        with self.assertRaises(KeyError): store.get('project','fixture')
        response=client.post('/api/projects/fixture/instruct/confirm',json=self.confirmation(prepared),
                             headers={'Idempotency-Key':'confirm-deleted'})
        self.assertEqual(response.status_code,404);self.assertEqual(self.model.call_count,1)


if __name__=='__main__': unittest.main()

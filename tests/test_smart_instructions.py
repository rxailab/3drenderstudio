"""Instruction orchestration with real local images and isolated inference.

No test reads user configuration, invokes Blender, or sends an AI request.
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
from planner import default_plan, instruct as real_instruct


class NoChange(ValueError):
    status='no_change'
    notes=['参数已达到要求']
    protections=['preserve_color']


class SmartInstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for module in (store,engine,service):
            mocked=patch.object(module,'DATA',self.root);mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear();self.addCleanup(service._INFLIGHT.clear)
        folder=self.root/'fixture'/'runs'/'source';folder.mkdir(parents=True)
        self.png=folder/'hero.png';Image.new('RGB',(20,20),(90,80,70)).save(self.png)
        self.project={'id':'fixture','name':'测试产品','version':3,'status':'ready','history':[],
            'analysis':{'objects':[]},'plan':default_plan(),
            'metadata':{'product_category':'杯具','material_note':'保留标识'},
            'outputs':[{'job_id':'source','shot_id':'hero','quality':'preview','version':3,
                'png_url':'/files/fixture/runs/source/hero.png','review':{'source':'ai','checked_at':1,
                    'realism':{'summary':'右侧补光较强','causes':[{'aspect':'灯光','evidence':'两侧亮度接近','suggestion':'降低补光'}]}}}]}
        store.put('project',copy.deepcopy(self.project))
        self.body={'text':'亮一点','expected_version':3,'shot_id':'hero',
                   'source':{'output_id':'source','shot_id':'hero','quality':'preview','version':3}}
        mocked=patch.object(service.planner,'instruct',side_effect=self.brighten)
        self.model=mocked.start();self.addCleanup(mocked.stop)

    def brighten(self,plan,text,analysis,**kwargs):
        candidate=copy.deepcopy(plan);candidate['exposure']+=.35;candidate['source']='ai'
        candidate['last_instruction']={'text':text,'summary':'提高当前镜头曝光','source':'ai','notes':[],
            'used_image':bool(kwargs.get('image_path')),'used_reference':bool(kwargs.get('reference_path')),
            'protections':['preserve_color'],'protected_fields':['color']}
        return candidate

    def apply(self,body=None,key='request-1'):
        return service.apply_instruction('fixture',self.body if body is None else body,key)

    def use_real_planner(self, enabled=False):
        self.model.side_effect=real_instruct
        # Only the historical parser/response fixtures opt into v1. The
        # storage, version and idempotency tests keep the production default.
        self.body['protocol']='legacy_v1'
        config={'enabled':enabled,'provider':'anthropic','base_url':'https://example.invalid',
                'api_key':'fixture-token' if enabled else '', 'model':'fixture-vision'}
        for name in ('get_config','get_vision_config'):
            mocked=patch('ai_config.'+name,return_value=copy.deepcopy(config));mocked.start();self.addCleanup(mocked.stop)
        for name in ('record_result','record_vision_result'):
            mocked=patch('ai_config.'+name);mocked.start();self.addCleanup(mocked.stop)

    def ai_decision(self, value=None, status='apply'):
        return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':service.ai_client.TOOL_NAME,
                'input':{'status':status,'patch':value or {},'summary':'依据当前画面检查曝光。','notes':[], 'unhandled':[]}}]}

    def test_context_uses_exact_image_and_real_diagnosis(self):
        result=self.apply();call=self.model.call_args
        self.assertEqual(call.kwargs['image_path'],self.png)
        self.assertIsNone(call.kwargs['reference_path'])
        context=call.kwargs['context']
        self.assertEqual(context['active_shot_id'],'hero')
        self.assertEqual(context['project_name'],'测试产品')
        self.assertEqual(context['product_category'],'杯具')
        self.assertEqual(context['material_note'],'保留标识')
        self.assertEqual(context['diagnosis']['summary'],'右侧补光较强')
        receipt=result['instruction_result']
        self.assertTrue(receipt['used_image']);self.assertFalse(receipt['used_reference'])
        self.assertEqual(receipt['source_snapshot'],self.body['source'])
        self.assertEqual(receipt['protected_fields'],['color'])
        self.assertEqual(result['plan']['last_instruction']['protected_fields'],['color'])

    def test_real_rules_keep_protection_and_scope_in_saved_plan(self):
        self.use_real_planner()
        result=self.apply({**self.body,'text':'不要增加留白，反光柔和一点'})
        receipt=result['instruction_result'];plan=result['plan']
        self.assertEqual(plan['light_softness'],1)
        self.assertEqual(engine.effective_plan(plan,'hero')['light_softness'],1.4)
        self.assertEqual(plan['margin'],self.project['plan']['margin'])
        self.assertIn('margin',receipt['protected_fields'])
        raw=[{key:row[key] for key in ('path','before','after')} for row in receipt['changes']]
        self.assertEqual(raw,plan['last_instruction']['parameter_diff'])
        self.assertEqual(raw,[{'path':'shot_adjustments.hero.softness_multiplier','before':None,'after':1.4}])
        self.assertEqual(receipt['changes'][0]['effective_before'],engine.effective_plan(self.project['plan'],'hero')['light_softness'])
        self.assertEqual(receipt['changes'][0]['effective_after'],engine.effective_plan(plan,'hero')['light_softness'])
        self.assertEqual(receipt['source'],'rules');self.assertFalse(receipt['used_image'])
        self.assertTrue(any('未分析图片内容' in note for note in receipt['notes']))

    def test_real_ai_envelope_is_scoped_and_replayed_once(self):
        self.use_real_planner(enabled=True)
        with patch('ai_client._post',return_value=self.ai_decision({'exposure':.35})) as send:
            result=self.apply();replay=self.apply()
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['plan']['exposure'],0)
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['exposure'],.35)
        self.assertEqual(result['instruction_result']['source'],'ai')
        self.assertTrue(result['instruction_result']['used_image'])
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(replay['version'],4)

    def test_real_ai_no_change_retains_observed_image_without_saving_plan(self):
        self.use_real_planner(enabled=True)
        with patch('ai_client._post',return_value=self.ai_decision(status='no_change')):
            result=self.apply()
        self.assertEqual(result['version'],3);self.assertEqual(result['history'],[])
        self.assertEqual(result['instruction_result']['status'],'no_change')
        self.assertTrue(result['instruction_result']['used_image'])
        self.assertEqual(result['instruction_result']['source'],'ai')
        self.assertEqual(result['instruction_result']['model'],'fixture-vision')

    def test_real_rules_reject_unsupported_compound_without_partial_save(self):
        self.use_real_planner()
        with self.assertRaisesRegex(ValueError,'不支持'):
            self.apply({**self.body,'text':'增加留白，增加倒角'})
        project=store.get('project','fixture')
        self.assertEqual(project['version'],3)
        self.assertEqual(project['plan'],self.project['plan'])
        self.assertNotIn('instruction_receipts',project)
        self.assertNotIn('fixture',service._INFLIGHT)

    def test_effective_values_map_back_only_to_active_shot(self):
        project=store.get('project','fixture');project['plan']['exposure']=.5
        project['plan']['light_softness']=2
        project['plan']['shot_adjustments']={'hero':{'exposure_delta':-.2,'softness_multiplier':.5,'lighting':{'fill_power':.6}},
                                             'side':{'exposure_delta':.1}}
        store.put('project',project)
        def adjust(plan,text,analysis,**kwargs):
            self.assertAlmostEqual(plan['exposure'],.3);self.assertEqual(plan['light_softness'],1)
            self.assertEqual(plan['lighting']['fill_power'],.6)
            result=self.brighten(plan,text,analysis,**kwargs)
            result['light_softness']=1.4;result['lighting']['fill_power']=.4
            result['shots'][0]['elevation']=60
            return result
        self.model.side_effect=adjust
        result=self.apply();plan=result['plan']
        self.assertEqual(plan['exposure'],.5);self.assertEqual(plan['light_softness'],2)
        self.assertEqual(plan['lighting']['fill_power'],1)
        self.assertEqual(plan['shot_adjustments']['side'],{'exposure_delta':.1})
        self.assertAlmostEqual(engine.effective_plan(plan,'hero')['exposure'],.65)
        self.assertEqual(engine.effective_plan(plan,'hero')['light_softness'],1.4)
        self.assertEqual(engine.effective_plan(plan,'hero')['lighting']['fill_power'],.4)
        self.assertEqual(plan['shots'][0]['elevation'],60)
        self.assertEqual(plan['shots'][1:],project['plan']['shots'][1:])

    def test_first_shot_adjustment_receipt_uses_readable_leaf_paths(self):
        result=self.apply({**self.body,'shot_id':'campaign','source':None})
        changes=result['instruction_result']['changes']
        raw=[{key:row[key] for key in ('path','before','after')} for row in changes]
        self.assertEqual(raw,[
            {'path':'shot_adjustments.campaign.exposure_delta','before':None,'after':.35}])
        self.assertEqual(raw,result['plan']['last_instruction']['parameter_diff'])
        self.assertEqual(changes[0]['effective_before'],engine.effective_plan(self.project['plan'],'campaign')['exposure'])
        self.assertEqual(changes[0]['effective_after'],engine.effective_plan(result['plan'],'campaign')['exposure'])
        removal=service._changes(result['plan'],self.project['plan'])
        self.assertEqual(removal,[
            {'path':'shot_adjustments.campaign.exposure_delta','before':.35,'after':None}])

    def test_other_camera_edits_are_rejected_without_saving(self):
        def wrong_shot(plan,text,analysis,**kwargs):
            result=self.brighten(plan,text,analysis,**kwargs);result['shots'][1]['elevation']=80;return result
        self.model.side_effect=wrong_shot
        with self.assertRaisesRegex(service.InstructionError,'当前镜头'):
            self.apply()
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_global_scope_is_explicit_and_dark_hero_previews_campaign(self):
        def dark(plan,text,analysis,**kwargs):
            result=self.brighten(plan,text,analysis,**kwargs);result['style']='dark';result['margin']=.2;return result
        self.model.side_effect=dark
        result=self.apply();receipt=result['instruction_result']
        self.assertEqual(result['plan']['shots'][0]['style'],'white')
        self.assertEqual(receipt['preview_shot_ids'],['campaign'])
        self.assertTrue(any('项目级' in note for note in receipt['notes']))
        self.assertTrue(any('白底主图保持白底' in note for note in receipt['notes']))

    def test_stale_preview_is_text_only_and_diagnosis_not_reused(self):
        project=store.get('project','fixture');project['outputs'][0]['version']=2;store.put('project',project)
        body=copy.deepcopy(self.body);body['source']['version']=2
        result=self.apply(body)
        self.assertIsNone(self.model.call_args.kwargs['image_path'])
        self.assertEqual(self.model.call_args.kwargs['context']['diagnosis'],{})
        self.assertFalse(result['instruction_result']['used_image'])
        self.assertTrue(any('不同方案版本' in note for note in result['instruction_result']['notes']))

    def test_forged_and_mismatched_sources_never_reach_inference(self):
        for change in ({'output_id':'other'},{'shot_id':'side'},{'quality':'fine'},{'version':True}):
            body=copy.deepcopy(self.body);body['source'].update(change)
            with self.subTest(change=change),self.assertRaises(service.InstructionError):
                self.apply(body)
        self.model.assert_not_called()
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_output_path_cannot_escape_project(self):
        project=store.get('project','fixture');project['outputs'][0]['png_url']='/files/../outside.png';store.put('project',project)
        with self.assertRaisesRegex(service.InstructionError,'不属于'):
            self.apply()
        self.model.assert_not_called()

    def test_reference_read_only_when_explicitly_requested(self):
        self.body['protocol']='legacy_v1'  # Historical text-gated reference route.
        reference=self.root/'fixture/references/front.png';reference.parent.mkdir();Image.new('RGB',(20,20),'blue').save(reference)
        project=store.get('project','fixture');project['metadata']['reference']=str(reference);store.put('project',project)
        body={**self.body,'text':'按参考图的灯光调整'}
        result=self.apply(body)
        self.assertEqual(self.model.call_args.kwargs['reference_path'],reference)
        self.assertTrue(result['instruction_result']['used_reference'])

    def test_negative_reference_does_not_read_or_validate_file(self):
        self.body['protocol']='legacy_v1'  # Historical text-gated reference route.
        project=store.get('project','fixture');project['metadata']['reference']='/outside/unreadable.png';store.put('project',project)
        for text in ('不用参考图，亮一点','参考图不要用，亮一点',"Don't use the reference image, brighter"):
            current=store.get('project','fixture')
            body={**self.body,'text':text,'expected_version':current['version'],'source':None}
            with self.subTest(text=text),patch.object(engine,'verify_image',wraps=engine.verify_image) as verify:
                result=self.apply(body,key=text)
            verify.assert_not_called()
            self.assertIsNone(self.model.call_args.kwargs['reference_path'])
            self.assertFalse(result['instruction_result']['used_reference'])

    def test_missing_or_outside_reference_rejects_complete_request(self):
        self.body['protocol']='legacy_v1'  # Historical text-gated reference route.
        body={**self.body,'text':'照参考图调整曝光'}
        for filename in (None,str(self.png)):
            project=store.get('project','fixture');project['metadata']['reference']=filename;store.put('project',project)
            with self.subTest(filename=filename),self.assertRaisesRegex(service.InstructionError,'请先上传'):
                self.apply(body)
        self.model.assert_not_called();self.assertEqual(store.get('project','fixture')['version'],3)

    def test_same_key_replays_without_second_inference_or_version(self):
        first=self.apply();second=self.apply()
        self.assertEqual(first['version'],4);self.assertEqual(second['version'],4)
        self.assertEqual(first['instruction_result']['id'],second['instruction_result']['id'])
        self.assertTrue(second['instruction_result']['replayed'])
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(len(second['history']),1)

    def test_replay_returns_old_receipt_with_new_current_project(self):
        first=self.apply();project=store.get('project','fixture');project['version']=8;store.put('project',project)
        store.put('job',{'id':'later','project_id':'fixture','status':'running'})
        replay=self.apply()
        self.assertEqual(replay['version'],8);self.assertEqual(replay['instruction_result']['applied_version'],4)
        self.assertTrue(replay['instruction_result']['replayed']);self.assertEqual(self.model.call_count,1)
        self.assertEqual(replay['instruction_result']['id'],first['instruction_result']['id'])

    def test_same_key_different_body_conflicts(self):
        self.apply()
        with self.assertRaises(service.InstructionError) as error:
            self.apply({**self.body,'text':'暗一点'})
        self.assertEqual(error.exception.status_code,409);self.assertEqual(self.model.call_count,1)
        self.assertEqual(store.get('project','fixture')['version'],4)

    def test_receipt_and_plan_commit_together_once(self):
        with patch.object(service,'put',wraps=store.put) as commit:
            result=self.apply()
        self.assertEqual(commit.call_count,1)
        written=commit.call_args.args[1]
        self.assertEqual(written['version'],4)
        self.assertEqual(written['instruction_result']['id'],written['instruction_receipts'][0]['receipt']['id'])
        self.assertEqual(result['plan'],written['plan'])

    def test_stale_client_version_rejected_before_inference(self):
        with self.assertRaises(service.InstructionError) as error:
            self.apply({**self.body,'expected_version':2})
        self.assertEqual(error.exception.status_code,409);self.model.assert_not_called()

    def test_version_change_during_inference_does_not_overwrite(self):
        def concurrent_edit(plan,text,analysis,**kwargs):
            project=store.get('project','fixture');project['version']=4;project['plan']['margin']=.3;store.put('project',project)
            return self.brighten(plan,text,analysis,**kwargs)
        self.model.side_effect=concurrent_edit
        with self.assertRaises(service.InstructionError) as error: self.apply()
        self.assertEqual(error.exception.status_code,409)
        project=store.get('project','fixture');self.assertEqual(project['plan']['margin'],.3)
        self.assertNotIn('instruction_receipts',project)

    def test_active_render_or_material_task_rejected_before_inference(self):
        for kind in ('job','material_task'):
            store.put(kind,{'id':'active','project_id':'fixture','status':'running'})
            with self.subTest(kind=kind),self.assertRaises(service.InstructionError) as error: self.apply()
            self.assertEqual(error.exception.status_code,409)
            store.put(kind,{'id':'active','project_id':'fixture','status':'completed'})
        self.model.assert_not_called()

    def test_task_started_during_inference_prevents_commit(self):
        def race(plan,text,analysis,**kwargs):
            store.put('material_task',{'id':'active','project_id':'fixture','status':'running'})
            return self.brighten(plan,text,analysis,**kwargs)
        self.model.side_effect=race
        with self.assertRaises(service.InstructionError) as error: self.apply()
        self.assertEqual(error.exception.status_code,409);self.assertEqual(store.get('project','fixture')['version'],3)

    def test_inflight_same_project_request_is_not_run_twice(self):
        errors=[]
        def duplicate(plan,text,analysis,**kwargs):
            try: self.apply()
            except service.InstructionError as error: errors.append(error.status_code)
            return self.brighten(plan,text,analysis,**kwargs)
        self.model.side_effect=duplicate
        self.apply()
        self.assertEqual(errors,[409]);self.assertEqual(self.model.call_count,1)
        self.assertNotIn('fixture',service._INFLIGHT)

    def test_inference_does_not_hold_the_global_store_lock(self):
        acquired=threading.Event()
        def inference(plan,text,analysis,**kwargs):
            def another_project_can_enter():
                with store.LOCK: acquired.set()
            thread=threading.Thread(target=another_project_can_enter)
            thread.start()
            self.assertTrue(acquired.wait(1),'Inference held the global store lock')
            thread.join(1)
            return self.brighten(plan,text,analysis,**kwargs)
        self.model.side_effect=inference
        self.assertEqual(self.apply()['version'],4)

    def test_no_change_has_receipt_without_new_plan_version(self):
        self.model.side_effect=NoChange('已经符合要求')
        result=self.apply();receipt=result['instruction_result']
        self.assertEqual(receipt['status'],'no_change');self.assertEqual(receipt['changes'],[])
        self.assertEqual(receipt['base_version'],receipt['applied_version']);self.assertEqual(result['version'],3)
        self.assertEqual(result['history'],[]);self.assertEqual(receipt['preview_shot_ids'],[])
        replay=self.apply();self.assertTrue(replay['instruction_result']['replayed']);self.assertEqual(self.model.call_count,1)

    def test_unchanged_candidate_does_not_create_empty_history(self):
        self.model.side_effect=lambda plan,*args,**kwargs:copy.deepcopy(plan)
        result=self.apply()
        self.assertEqual(result['instruction_result']['status'],'no_change');self.assertEqual(result['version'],3)
        self.assertEqual(result['history'],[])

    def test_inference_failure_leaves_no_receipt_and_allows_safe_retry(self):
        self.model.side_effect=ValueError('AI 暂不可用')
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(store.get('project','fixture')['version'],3)
        self.assertNotIn('fixture',service._INFLIGHT)
        self.model.side_effect=self.brighten
        self.assertEqual(self.apply()['version'],4)

    def test_recent_context_contains_at_most_five_actual_intents(self):
        project=store.get('project','fixture');project['history']=[]
        for number in range(8):
            project['history'].append({'plan':{'last_instruction':{'text':'调整'+str(number),
                'changes':[{'path':'margin','before':.1,'after':.2}]}}})
        store.put('project',project);self.apply()
        recent=self.model.call_args.kwargs['context']['recent_instructions']
        self.assertEqual(len(recent),5);self.assertEqual(recent[0]['text'],'调整3');self.assertEqual(recent[-1]['text'],'调整7')

    def test_undone_instruction_is_not_reintroduced_from_audit_receipt(self):
        self.apply()
        self.apply({**self.body,'expected_version':4,'source':None,'text':'第二次调整'},key='second')
        project=store.get('project','fixture');project['plan']=project['history'].pop()['plan'];project['version']+=1;store.put('project',project)
        recent=service._recent(project)
        self.assertEqual([item['text'] for item in recent],['亮一点'])
        self.assertEqual(len(project['instruction_receipts']),2)
        self.assertEqual(project['plan']['last_instruction']['id'],project['instruction_receipts'][0]['receipt']['id'])
        self.assertEqual(project['plan']['last_instruction']['applied_version'],4)

    def test_recent_changes_are_bounded_and_remove_resource_paths(self):
        project=store.get('project','fixture')
        material={'name':'Body','roughness':.4,'textures':{'roughness':'C:/private/roughness.png'},
                  'resource':{'manifest_path':'/private/manifest.json'},'manifest_path':'/private/manifest.json'}
        project['plan']['last_instruction']={'text':'材质微调','changes':[
            {'path':'object_material_overrides','before':[],'after':[material]*20},
            {'path':'object_material_overrides.Body.textures','before':None,'after':'/private/texture.png'}
        ]+[{'path':'margin','before':.1,'after':.2} for _ in range(30)]}
        recent=service._recent(project);serialized=json.dumps(recent,ensure_ascii=False)
        self.assertLessEqual(len(recent[0]['changes']),12)
        self.assertLessEqual(len(recent[0]['changes'][0]['after']),6)
        for forbidden in ('private','textures','manifest_path','resource'):
            self.assertNotIn(forbidden,serialized)

    def test_no_change_can_truthfully_record_ai_image_usage(self):
        outcome=NoChange('画面已符合要求');outcome.used_image=True;outcome.used_reference=False
        outcome.source='ai';outcome.provider='test-provider';outcome.model='test-model'
        self.model.side_effect=outcome
        result=self.apply();receipt=result['instruction_result']
        self.assertEqual(receipt['status'],'no_change');self.assertTrue(receipt['used_image'])
        self.assertEqual(receipt['source'],'ai');self.assertEqual(receipt['model'],'test-model')
        self.assertEqual(result['version'],3)

    def test_legacy_text_only_request_keeps_global_behavior(self):
        result=self.apply({'text':'亮一点','protocol':'legacy_v1'},key=None)
        self.assertEqual(self.model.call_args.kwargs,{})
        self.assertEqual(result['plan']['exposure'],.35)
        self.assertNotIn('shot_adjustments',result['plan'])

    def test_invalid_request_never_reaches_inference(self):
        for body in ({'text':''},{'text':7},{'text':'x'*2001},{**self.body,'expected_version':True},
                     {**self.body,'image_path':'/etc/passwd'},{**self.body,'shot_id':'unknown'}):
            with self.subTest(body=body),self.assertRaises(service.InstructionError): self.apply(body)
        self.model.assert_not_called()

    def web_client(self):
        # app.py normally reads its local .env during import. This test must
        # never use the uploaded configuration or any real credentials.
        env=Path(__file__).resolve().parents[1]/'.env';original_exists=Path.exists
        with patch.object(Path,'exists',lambda path:False if path.absolute()==env else original_exists(path)):
            module=importlib.import_module('app')
        mocked=patch.object(module,'DATA',self.root);mocked.start();self.addCleanup(mocked.stop)
        return module.app.test_client()

    def test_http_instruction_receipt_replay_and_conflict(self):
        client=self.web_client();headers={'Idempotency-Key':'http-operation'}
        first=client.post('/api/projects/fixture/instruct',json=self.body,headers=headers)
        self.assertEqual(first.status_code,200)
        replay=client.post('/api/projects/fixture/instruct',json=self.body,headers=headers)
        self.assertEqual(replay.status_code,200);self.assertTrue(replay.get_json()['instruction_result']['replayed'])
        latest=client.get('/api/projects/fixture').get_json()
        self.assertEqual(latest['instruction_result']['id'],first.get_json()['instruction_result']['id'])
        conflict=client.post('/api/projects/fixture/instruct',json={**self.body,'text':'暗一点'},headers=headers)
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(self.model.call_count,1)

    def test_http_inflight_reconciliation_preserves_original_key(self):
        client=self.web_client();responses=[]
        def in_progress(plan,text,analysis,**kwargs):
            same=client.post('/api/projects/fixture/instruct',json=self.body,headers={'Idempotency-Key':'request-1'})
            other=client.post('/api/projects/fixture/instruct',json=self.body,headers={'Idempotency-Key':'other-request'})
            responses.extend([same,other])
            return self.brighten(plan,text,analysis,**kwargs)
        self.model.side_effect=in_progress
        applied=self.apply()
        self.assertEqual([response.status_code for response in responses],[409,409])
        self.assertEqual(responses[0].get_json()['error_code'],'instruction_in_progress')
        self.assertTrue(responses[0].get_json()['retryable'])
        self.assertNotIn('retryable',responses[1].get_json())
        replay=client.post('/api/projects/fixture/instruct',json=self.body,headers={'Idempotency-Key':'request-1'})
        self.assertEqual(replay.status_code,200)
        self.assertTrue(replay.get_json()['instruction_result']['replayed'])
        self.assertEqual(replay.get_json()['instruction_result']['id'],applied['instruction_result']['id'])
        self.assertEqual(self.model.call_count,1)

    def test_http_jobs_forward_expected_version_and_report_conflict(self):
        client=self.web_client()
        with patch.object(engine,'require_runtime'):
            invalid=client.post('/api/projects/fixture/jobs',json=[])
            self.assertEqual(invalid.status_code,400)
            stale=client.post('/api/projects/fixture/jobs',json={'kind':'preview','shot_ids':['hero'],'expected_version':2})
            self.assertEqual(stale.status_code,409)
            accepted=client.post('/api/projects/fixture/jobs',json={'kind':'preview','shot_ids':['hero'],'expected_version':3},headers={'Idempotency-Key':'preview-1'})
            self.assertEqual(accepted.status_code,202)
            conflicting=client.post('/api/projects/fixture/jobs',json={'kind':'preview','shot_ids':['side'],'expected_version':3},headers={'Idempotency-Key':'preview-2'})
            self.assertEqual(conflicting.status_code,409)


if __name__=='__main__':unittest.main()

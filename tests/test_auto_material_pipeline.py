import copy
import hashlib
import io
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

import engine
import material_agent as agent
import studio_store as store
from planner import default_plan
from camera_controls import shot_request


class AutomaticMaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.old=(store.DATA,agent.DATA,engine.QUEUE)
        store.DATA=agent.DATA=self.root;engine.QUEUE=queue.Queue()
        with store.connection() as c:c.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        folder=self.root/'fixture';folder.mkdir()
        Image.new('RGB',(8,8),'white').save(folder/'image.png');Image.new('RGB',(8,8),'white').save(folder/'image.jpg')
        (folder/'scene.blend').write_bytes(b'fixture saved scene')
        self.request={'targets':[{'object_name':'Body','material_name':'Fabric'}],'query':'fine cotton fabric','reason':'缺少细密织物微表面','confidence':.85}
        self.result={'success':True,'product_samples':[{'object_name':'Body','material_name':'Fabric','x':.5,'y':.5},
            {'object_name':'Handle','material_name':'Leather','x':.2,'y':.2}]}
        (folder/'result.json').write_text(json.dumps(self.result))
        plan=default_plan()
        self.project={'id':'fixture','version':3,'plan':plan,'history':[],
            'analysis':{'objects':[{'name':'Body','materials':['Fabric'],'uv_layers':['UVMap']},{'name':'Handle','materials':['Leather'],'uv_layers':['UVMap']}]},
            'outputs':[self.output('hero'),self.output('campaign')]}
        self.job={'id':'baseline','project_id':'fixture','status':'completed','kind':'preview','version':3,'quality':'preview',
            'plan':copy.deepcopy(plan),'shot_ids':['hero','campaign'],'completed':['hero','campaign']}
        store.put('project',self.project);store.put('job',self.job)
        self.hit={'hit':True,'object_name':'Body','material_name':'Fabric','material_info':{'editable':True}}
        self.selection={'asset_id':'cotton','name':'Cotton','url':'https://polyhaven.com/a/cotton','license':'CC0-1.0','license_url':agent.LICENSE_URL,'mode':'ai'}
        self.resource={'provider':'Poly Haven','asset_id':'cotton','name':'Cotton','url':self.selection['url'],'license_url':agent.LICENSE_URL,'resolution':'1k','downloaded_at':1}
        self.textures={'normal':str(folder/'materials/normal.png')}
        self.patches=[patch.object(agent.threading,'Thread'),patch.object(engine,'pick_output',return_value={'hits':[self.hit]}),
            patch.object(agent,'_catalogue',return_value={}),patch.object(agent,'_choose',return_value=self.selection),
            patch.object(agent,'_download',return_value=(self.textures,self.resource))]
        self.thread,self.pick,self.catalogue,self.choose,self.download=[p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        store.DATA,agent.DATA,engine.QUEUE=self.old;self.temp.cleanup()

    def output(self,shot,job_id='baseline',version=3,score=.7,requests=None):
        return {'job_id':job_id,'shot_id':shot,'quality':'preview','version':version,'png_url':'/files/fixture/image.png',
            'jpg_url':'/files/fixture/image.jpg','blend_url':'/files/fixture/scene.blend','result_url':'/files/fixture/result.json',
            'review':{'source':'ai','provider':'anthropic','model':'vision','score':score,'material_requests':copy.deepcopy([self.request] if requests is None else requests)}}

    def schedule(self):
        agent.schedule_after_preview('fixture','baseline')
        tasks=store.all_records(agent.KIND)
        return tasks[0] if tasks else None

    def apply(self):
        task=self.schedule();agent._run(task['id']);return store.get(agent.KIND,task['id'])

    def complete(self,task,score=.8,requests=None):
        job=store.get('job',task['job_id']);job['status']='completed';store.put('job',job)
        project=store.get('project','fixture')
        project['outputs'] += [self.output(shot,job['id'],project['version'],score,[] if requests is None else requests) for shot in job['shot_ids']]
        store.put('project',project)
        agent.schedule_after_preview('fixture',job['id'])
        return store.get('project','fixture')

    def test_automatic_request_uses_actual_scene_points_without_user_input(self):
        task=self.schedule()
        self.assertEqual(task['origin'],'automatic');self.assertFalse(task['use_base_color'])
        self.assertEqual(task['points'],[{'id':'auto-1','x':.5,'y':.5}])
        self.assertEqual(task['expected_targets'],self.request['targets'])
        self.assertEqual(task['chain']['round'],1);self.assertEqual(task['chain']['max_rounds'],2)
        self.assertEqual(store.get('project','fixture')['version'],3)
        self.schedule();self.assertEqual(len(store.all_records(agent.KIND)),1)

    def test_explicit_instruction_material_lock_prevents_followup_download_and_mutation(self):
        self.job['plan']['last_instruction']={'protected_fields':['material_overrides','object_material_overrides']}
        store.put('job',self.job)
        self.assertIsNone(self.schedule())
        self.thread.assert_not_called();self.download.assert_not_called()
        project=store.get('project','fixture')
        self.assertEqual(project['version'],3)
        self.assertEqual(project['auto_material']['status'],'skipped')
        self.assertIn('保留材质',project['auto_material']['stage'])

    def test_applies_to_real_target_and_renders_original_group(self):
        task=self.apply()
        self.assertEqual(task['status'],'rendering')
        job=store.get('job',task['job_id']);self.assertEqual(job['shot_ids'],['hero','campaign'])
        project=store.get('project','fixture');self.assertEqual(project['version'],4)
        self.assertEqual(project['plan']['source'],'ai')
        self.assertNotIn('base_color',project['plan']['object_material_overrides'][0]['textures'])
        result=self.complete(task)
        self.assertEqual(result['version'],4);self.assertEqual(result['auto_material']['status'],'completed')
        self.assertEqual(len(store.all_records(agent.KIND)),1)

    def test_visible_and_real_pair_required(self):
        for change in [{'targets':[{'object_name':'Body','material_name':'Leather'}]}, {'confidence':-.01},{'confidence':1.01},{'confidence':float('nan')},{'query':'https://evil.test/texture'}]:
            project=copy.deepcopy(self.project)
            for output in project['outputs']:output['review']['material_requests']=[{**self.request,**change}]
            store.put('project',project);self.assertIsNone(self.schedule())
        self.result['product_samples']=[];(self.root/'fixture/result.json').write_text(json.dumps(self.result))
        store.put('project',self.project);self.assertIsNone(self.schedule())

    def test_uncalibrated_confidence_is_recorded_as_reversible_trial(self):
        for output in self.project['outputs']:output['review']['material_requests'][0]['confidence']=.45
        store.put('project',self.project);task=self.schedule()
        self.assertEqual(task['diagnosis_confidence'],.45)
        self.assertIn('自动尝试',task['stage'])
        self.assertFalse(task['use_base_color'])

    def test_highest_confidence_request_across_shots_runs_first(self):
        second={**self.request,'targets':[{'object_name':'Handle','material_name':'Leather'}],'query':'fine leather','confidence':.95}
        self.project['outputs'][1]['review']['material_requests']=[second]
        store.put('project',self.project);task=self.schedule()
        self.assertEqual(task['expected_targets'],second['targets'])
        self.assertEqual(task['diagnosis_confidence'],.95)

    def test_automatic_download_never_changes_metallic_or_color(self):
        self.patches[-1].stop()
        task=self.schedule()
        stream=io.BytesIO();Image.new('RGB',(8,8),'grey').save(stream,format='PNG');raw=stream.getvalue()
        item={'url':'https://dl.polyhaven.org/file/ph-assets/Textures/png/1k/cotton/rough.png','size':len(raw),'md5':hashlib.md5(raw).hexdigest()}
        files={key:{'1k':{'png':item}} for key in ('rough','nor_gl','metal','diff')}
        with patch.object(agent,'_json',return_value=files),patch.object(agent,'_fetch',return_value=raw):
            textures,resource=agent._download(task['id'],self.selection,False)
        self.assertEqual(set(textures),{'roughness','normal'})
        manifest=json.loads(Path(resource['manifest_path']).read_text(encoding='utf-8'))
        self.assertEqual(set(manifest['files']),{'roughness','normal'})

    def test_legacy_preview_refreshes_once_even_when_ai_is_unavailable(self):
        p=copy.deepcopy(self.project);p.update(slug='fixture',model_path='fixture.glb')
        p['plan']['max_corrections']=0;store.put('project',p)
        job=copy.deepcopy(self.job);job.update(id='legacy-refresh',status='queued',plan=p['plan'],shot_ids=['hero']);store.put('job',job)
        def execute(job,request,out):
            out.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(8,8),'white').save(out/'result.png');Image.new('RGB',(8,8),'white').save(out/'result.jpg')
            (out/'result.blend').write_bytes(b'actual fixture artifact')
            result={'success':True,'render_pipeline_version':2,'product_samples':[],'visible_materials':[],
                # This mock represents a newly rendered v17 result. The legacy
                # input intentionally lacks proof; the refreshed output must
                # carry its measured projection before it can be reused.
                'camera_projection_verification':{'saved_scene_verified':True,'render_completed':True,
                    'contract':{'shot':shot_request(request['shot'])},
                    'actual':{'type':'ORTHO','sensor_fit':'HORIZONTAL'}}}
            (out/'result.json').write_text(json.dumps(result));return result
        with patch.object(engine,'DATA',self.root),patch.object(engine,'execute',side_effect=execute) as render,patch('ai_config.get_config',return_value={'enabled':False}),patch('ai_config.get_vision_config',return_value={'enabled':False}):
            engine.run_job(job['id'])
            self.assertEqual(store.get('job',job['id'])['status'],'completed',store.get('job',job['id']).get('error'))
            first=store.get('project','fixture')['outputs'][-1]
            self.assertEqual(first['review']['material_check_version'],1)
            self.assertEqual(first['review']['material_requests'],[])
            next_job=engine.submit('fixture','preview','preview',['hero'])
            engine.run_job(next_job['id'])
            self.assertEqual(render.call_count,1)
            self.assertEqual(store.get('job',next_job['id'])['reused'],['hero'])

    def test_no_ai_no_requests_disabled_or_existing_maps_never_downloads(self):
        for variant in ('rules','disabled','zero','maps','empty'):
            project=copy.deepcopy(self.project);job=copy.deepcopy(self.job)
            if variant=='rules':
                for o in project['outputs']:o['review']['source']='rules'
            if variant=='empty':
                for o in project['outputs']:o['review']['material_requests']=[]
            if variant=='disabled':job['plan']['visual_review']=False
            if variant=='zero':job['plan']['max_corrections']=0
            if variant=='maps':project['plan']['object_material_overrides']=[{'object_name':'Body','material_name':'Fabric','textures':{'normal':'owned.png'}}]
            store.put('project',project);store.put('job',job)
            self.assertIsNone(self.schedule())
        self.download.assert_not_called()

    def test_ray_pick_mismatch_and_non_ai_asset_choice_cannot_apply(self):
        task=self.schedule();self.pick.return_value={'hits':[{**self.hit,'object_name':'Handle'}]}
        agent._run(task['id']);self.assertEqual(store.get(agent.KIND,task['id'])['status'],'failed')
        self.assertEqual(store.get('project','fixture')['version'],3);self.download.assert_not_called()

    def test_provider_fallback_does_not_auto_apply_rule_guess(self):
        self.choose.return_value={**self.selection,'mode':'rules'}
        task=self.apply();self.assertEqual(task['status'],'failed')
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_stale_plan_is_never_overwritten_after_network(self):
        task=self.schedule()
        def change(*args):
            p=store.get('project','fixture');p['version']=4;store.put('project',p);return self.textures,self.resource
        self.download.side_effect=change;agent._run(task['id'])
        self.assertEqual(store.get(agent.KIND,task['id'])['status'],'failed')
        self.assertEqual(store.get('project','fixture')['plan'],self.project['plan'])

    def test_second_distinct_request_uses_same_bounded_group(self):
        task=self.apply()
        second={**self.request,'targets':[{'object_name':'Handle','material_name':'Leather'}],'query':'fine leather'}
        self.complete(task,requests=[self.request,second])
        tasks=store.all_records(agent.KIND);self.assertEqual(len(tasks),2)
        next_task=next(t for t in tasks if t['id']!=task['id'])
        self.assertEqual(next_task['chain']['round'],2);self.assertEqual(next_task['render_shot_ids'],['hero','campaign'])
        self.assertEqual(next_task['expected_targets'],second['targets'])

    def test_score_decline_restores_verified_baseline_and_preserves_candidate(self):
        task=self.apply();result=self.complete(task,score=.4)
        self.assertEqual(result['version'],5)
        self.assertEqual(result['plan'],self.project['plan'])
        self.assertEqual(result['auto_material']['status'],'reverted')
        self.assertEqual(len([o for o in result['outputs'] if o['version']==4]),2)
        restored=[o for o in result['outputs'] if o['version']==5]
        self.assertEqual(len(restored),2);self.assertTrue(all(o['automatic_material_restored_from']==task['id'] for o in restored))
        self.assertEqual(store.get(agent.KIND,task['id'])['decision'],'reverted')
        agent.recover_interrupted()
        self.assertEqual(store.get('project','fixture')['auto_material']['status'],'reverted')

    def test_chain_stops_at_two_rounds(self):
        task=self.apply()
        second={**self.request,'targets':[{'object_name':'Handle','material_name':'Leather'}],'query':'fine leather'}
        self.complete(task,requests=[second])
        next_task=next(t for t in store.all_records(agent.KIND) if t['id']!=task['id'])
        self.pick.return_value={'hits':[{**self.hit,'object_name':'Handle','material_name':'Leather'}]}
        agent._run(next_task['id']);next_task=store.get(agent.KIND,next_task['id'])
        result=self.complete(next_task,score=.9,requests=[self.request])
        self.assertEqual(result['auto_material']['round'],2)
        self.assertIn('上限',result['auto_material']['stage'])
        self.assertEqual(len(store.all_records(agent.KIND)),2)

    def test_equal_scores_restore_baseline_and_keep_candidate(self):
        task=self.apply();result=self.complete(task,score=.7)
        self.assertIn('未明显提升',result['auto_material']['notice'])
        self.assertEqual(result['version'],5)
        self.assertEqual(result['plan'],self.project['plan'])
        self.assertEqual(result['auto_material']['status'],'reverted')
        self.assertEqual(len([o for o in result['outputs'] if o['version']==4]),2)
        self.assertEqual(len(store.all_records(agent.KIND)),1)

    def test_missing_comparable_scores_restore_baseline(self):
        task=self.apply();result=self.complete(task,score=None)
        self.assertEqual(result['version'],5)
        self.assertEqual(result['plan'],self.project['plan'])
        self.assertEqual(result['auto_material']['status'],'reverted')
        self.assertIn('缺少可比较',result['auto_material']['stage'])
        self.assertEqual(len([o for o in result['outputs'] if o['version']==4]),2)
        self.assertEqual(store.get(agent.KIND,task['id'])['decision'],'reverted')
        self.assertEqual(len(store.all_records(agent.KIND)),1)

    def test_cancel_render_stops_chain_and_never_queues_next(self):
        task=self.apply();agent.cancel('fixture',task['id'])
        agent.finish_failed_preview('fixture',task['job_id'])
        self.assertEqual(store.get('project','fixture')['auto_material']['status'],'cancelled')
        self.assertEqual(len(store.all_records(agent.KIND)),1)

    def test_failed_render_restores_original_material(self):
        task=self.apply();job=store.get('job',task['job_id']);job.update(status='failed',error='device unavailable');store.put('job',job)
        agent.finish_failed_preview('fixture',job['id'])
        result=store.get('project','fixture')
        self.assertEqual(result['plan'],self.project['plan']);self.assertEqual(result['auto_material']['status'],'reverted')

    def test_user_change_prevents_automatic_rollback(self):
        task=self.apply();p=store.get('project','fixture');p['version']+=1;p['plan']['margin']=.2;store.put('project',p)
        result=self.complete(task,score=.2)
        self.assertEqual(result['plan']['margin'],.2);self.assertEqual(result['auto_material']['status'],'skipped')

    def test_restart_finishes_pending_comparison_without_duplicate_material(self):
        task=self.apply();job=store.get('job',task['job_id']);job['status']='completed';store.put('job',job)
        p=store.get('project','fixture');p['outputs'] += [self.output(s,job['id'],4,.8,[]) for s in job['shot_ids']];store.put('project',p)
        agent.recover_interrupted()
        self.assertTrue(store.get(agent.KIND,task['id'])['auto_finalized'])
        self.assertEqual(store.get('project','fixture')['auto_material']['status'],'completed')
        self.assertEqual(len(store.all_records(agent.KIND)),1)


if __name__=='__main__':unittest.main()

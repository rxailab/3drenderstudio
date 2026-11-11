import copy
import hashlib
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import engine
import hero_optimizer as hero
import planner
import studio_store as store
import app as web
from hero_state import PRODUCT_FIELDS, state_digest


class HeroWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.patches=[patch.object(module,'DATA',self.root) for module in (store,engine,hero,web)]
        self.patches.extend([patch.object(engine,'QUEUE',queue.Queue()),patch.object(engine,'require_runtime')])
        for item in self.patches:item.start();self.addCleanup(item.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        folder=self.root/'fixture/runs/original';folder.mkdir(parents=True)
        self.png=folder/'image.png';Image.new('RGB',(48,48),'white').save(self.png)
        self.blend=folder/'scene.blend';self.blend.write_bytes(b'BLENDER-unit-test-original')
        self.output={'job_id':'original','shot_id':'hero','shot_label':'白底主图','quality':'preview',
                     'version':1,'png_url':'/files/fixture/runs/original/image.png',
                     'blend_url':'/files/fixture/runs/original/scene.blend','blend_sha256':hero.digest(self.blend),'created_at':1}
        self.project={'id':'fixture','name':'Fixture','slug':'fixture','version':1,'plan':planner.default_plan(),
                      'analysis':{'objects':[{'name':'Product','type':'MESH','materials':['Paint']}]},
                      'outputs':[self.output],'history':[],'status':'ready','model_path':str(self.root/'fixture/originals/model.obj')}
        store.put('project',self.project)
        self.body={'expected_version':1,'shot_id':'hero','source':{'output_id':'original','shot_id':'hero','quality':'preview','version':1},'final_resolution':2000}
        self.catalog={'product_object_names':['Product'],'camera':{'azimuth':28.0,'elevation':14.0,'focal_length':72.0},
                      'exposure':.15,'light_directions':[{'role':'key','object_name':'Key','camera':'Camera',
                        'azimuth':-35.0,'elevation':42.0,'position':[1,2,3],'target':[0,0,0]}]}
        self.catalog.update({key:hashlib.sha256(key.encode()).hexdigest() for key in PRODUCT_FIELDS})
        self.catalog['framing']={'kind':'complete_product','clipped':False,'occupancy':.85}
        identity=[[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,0.0],[0.0,0.0,0.0,1.0]]
        self.catalog['scene_state']={'version':2,'product_scale':2.0,'camera':{'name':'Camera','type':'PERSP','focal_length':70.0,
             'shift_x':0.0,'shift_y':0.0,'ortho_scale':6.0,'matrix_world':identity,'constraints':False,
             'sensor_fit':'AUTO','sensor_width':36.0,'sensor_height':24.0,'clip_start':.1,'clip_end':100.0,
             'dof_enabled':False,'focus_distance':10.0,'aperture_fstop':2.8,'aperture_blades':0,
             'aperture_ratio':1.0,'aperture_rotation':0.0,'focus_object':None},
             'canvas_aspect':[1,1,1.0,1.0],'lights':[{'name':'Key','role':'key','type':'AREA','visible':True,'energy':100.0,
                 'color':[1.0,1.0,1.0],'size':2.0,'size_y':2.0,'shape':'RECTANGLE','diffuse_factor':1.0,
                 'matrix_world':identity,'use_nodes':False,'nodes':None}],
             'world_nodes':{},'ground':{},'ground_nodes':[],'surfaces':[],
             'product':{key:self.catalog[key] for key in PRODUCT_FIELDS},'exposure':.15,
             'view_transform':'AgX','look':'None','gamma':1.0,'unit_scale':1.0,'film_transparent':False}
        self.rehash(self.catalog)
        self.scene_catalogs={hero.digest(self.blend):copy.deepcopy(self.catalog)}
        self.planned={'recipe':{'version':1,'lighting':{'key':{'energy_factor':1.4}}},'summary':'改善主光和构图',
                      'diagnosis':['正面偏暗'],'skipped':[],'source':'ai','llm_calls':1}
        self.compared={'decision':'candidate','confidence':.8,'reason':'正面层次改善，边缘完整','source':'ai','llm_calls':1}
        self.native_requests=[]
        self.client=web.app.test_client()

    def rehash(self,catalog):
        catalog['scene_state_sha256']=state_digest(catalog['scene_state'])
        catalog['actual_state_sha256']=catalog['scene_state_sha256']

    def native(self,job,request,out):
        self.native_requests.append(copy.deepcopy(request));out=Path(out);out.mkdir(parents=True,exist_ok=True)
        source=copy.deepcopy(self.scene_catalogs.get(hero.digest(Path(request['source_blend'])),self.catalog))
        if request['action']=='inspect':
            return {'success':True,'request_id':request['request_id'],'source_sha256':request['source_sha256'],'catalog':source}
        png=out/'image.png';Image.new('RGB',(request['resolution'],request['resolution']),(245,245,245)).save(png)
        blend=out/'scene.blend';blend.write_bytes(b'BLENDER-unit-test-render-'+out.name.encode())
        original={key:self.catalog[key] for key in PRODUCT_FIELDS}
        proof={key:True for key in hero.PROOF_FLAGS}
        proof.update(request_id=request['request_id'],source_sha256=request['source_sha256'],
                     before=original,after=copy.deepcopy(original),png_sha256=hero.digest(png),blend_sha256=hero.digest(blend))
        result={'success':True,'request_id':request['request_id'],'outputs':{'png':'image.png','blend':'scene.blend'},
                'proof':proof,'catalog':source,
                'metrics':{'framing':{'kind':'complete_product','clipped':False,'occupancy':.85},
                           'product':{'method':'product-mask-v1','eligible_opaque_samples':50,'opaque_clipped_fraction':0}}}
        if request.get('recipe'):
            result['catalog']['scene_state']['camera']['focal_length']=72.0
            result['catalog']['scene_state']['lights'][0]['energy']=140.0
        self.rehash(result['catalog'])
        self.scene_catalogs[hero.digest(blend)]=copy.deepcopy(result['catalog'])
        (out/'request.json').write_text(json.dumps({**request,'output_dir':str(out)}))
        (out/'result.json').write_text(json.dumps(result));return result

    def optimize(self,result=None,plan=None,native=None):
        job=hero.submit('fixture',self.body,'hero-test')
        with patch('hero_optimizer._native',side_effect=native or self.native), \
             patch('hero_ai.plan_hero',return_value=self.planned if plan is None else plan), \
             patch('hero_ai.compare_hero',return_value=self.compared if result is None else result), \
             patch('ai_config.get_vision_config',return_value={'enabled':True,'provider':'openai','model':'test-only','api_key':'never-persist'}):
            hero.run_job(job)
        return store.get('job',job['id'])

    def failed_legacy_run(self,final_focal_length=72.0):
        """Saved v13-shaped artifacts: full render succeeded, old hash gate failed."""
        with patch('hero_optimizer._final_state',side_effect=RuntimeError('legacy exact hash mismatch')):
            with self.assertRaisesRegex(RuntimeError,'legacy exact hash mismatch'):
                self.optimize()
        old=next(row for row in store.all_records('job') if row.get('kind')=='hero_optimize')
        old.update(status='failed',error='高清输出与已选候选的场景状态不一致，原方案保留。')
        store.put('job',old);folder=self.root/'fixture/runs'/old['id']
        # V13 regenerated final from the original instead of using the snapshot.
        request=hero._request(self.blend,hero.digest(self.blend),['Product'],'render',recipe=self.planned['recipe'],
            shot_id='hero',target_aspect='1:1',resolution=2000,samples=128)
        result=self.native(old,request,folder/'final')
        result['catalog']['scene_state']['camera']['shift_x']=4.3e-8
        result['catalog']['scene_state']['camera']['focal_length']=final_focal_length
        self.rehash(result['catalog'])
        self.scene_catalogs[hero.digest(folder/'final/scene.blend')]=copy.deepcopy(result['catalog'])
        (folder/'final/result.json').write_text(json.dumps(result))
        for stage in ('baseline','candidate','final'):
            path=folder/stage/'result.json';legacy=json.loads(path.read_text())
            legacy['catalog'].pop('scene_state');legacy['catalog'].pop('scene_state_sha256')
            legacy['catalog']['actual_state_sha256']=hashlib.sha256(stage.encode()).hexdigest()
            path.write_text(json.dumps(legacy))
        self.native_requests=[]
        return old,folder

    def recover(self,old):
        job=hero.retry_job(old)
        with patch('hero_optimizer._native',side_effect=self.native), \
             patch('hero_ai.plan_hero',side_effect=AssertionError('Recovery must not call planning')) as plan, \
             patch('hero_ai.compare_hero',side_effect=AssertionError('Recovery must not call comparison')) as compare:
            hero.run_job(job)
        plan.assert_not_called();compare.assert_not_called()
        return store.get('job',job['id'])

    def test_submission_is_exact_source_scoped_and_idempotent(self):
        first=hero.submit('fixture',self.body,'same');second=hero.submit('fixture',self.body,'same')
        self.assertEqual(first['id'],second['id']);self.assertEqual(engine.QUEUE.qsize(),1)
        self.assertEqual(first['kind'],'hero_optimize');self.assertIsNone(first['sample_progress'])
        with self.assertRaises(engine.JobConflictError):hero.submit('fixture',{**self.body,'final_resolution':4096},'same')
        with self.assertRaises(engine.JobConflictError):hero.submit('fixture',self.body,'another')

    def test_invalid_resolution_source_and_nonwhite_shot_do_not_queue(self):
        for value in (True,0,512,2048,'2000'):
            with self.subTest(value=value),self.assertRaises(ValueError):hero.submit('fixture',{**self.body,'final_resolution':value})
        with self.assertRaises(ValueError):hero.submit('fixture',{**self.body,'source':{**self.body['source'],'output_id':'else'}})
        with self.assertRaises(ValueError):hero.submit('fixture',{**self.body,'shot_id':'campaign'})
        self.assertEqual(engine.QUEUE.qsize(),0)

    def test_adopt_once_saves_replayable_checkpoint_actual_settings_and_download(self):
        job=self.optimize();project=store.get('project','fixture')
        self.assertEqual(job['optimization_decision'],'adopted');self.assertEqual(job['llm_calls'],2)
        self.assertEqual(project['version'],2);self.assertEqual(len(project['history']),1)
        self.assertEqual(len(project['outputs']),3)
        output=project['outputs'][-1];self.assertEqual((output['quality'],output['width'],output['height']),('hero',2000,2000))
        self.assertEqual(output['blend_sha256'],hero.digest(hero._file('fixture',output['blend_url'],'.blend')))
        shot=next(row for row in project['plan']['shots'] if row['id']=='hero')
        self.assertEqual((shot['azimuth'],shot['elevation'],shot['focal_length']),(28,14,72))
        self.assertEqual(project['plan']['shots'][1:],self.project['plan']['shots'][1:])
        token=project['plan']['scene_checkpoints']['hero'];scene,checkpoint=hero._checkpoint('fixture',token)
        self.assertTrue(scene.is_file());self.assertNotIn('scene_checkpoints',checkpoint['baseline_plan'])
        self.assertEqual(checkpoint['baseline_plan']['shots'][0],shot)
        self.assertEqual(project['plan']['shot_adjustments']['hero']['lighting']['key_azimuth'],-35)
        self.assertNotIn('never-persist',json.dumps(job))
        self.assertEqual(self.native_requests[-1]['resolution'],2000)
        self.assertTrue(self.native_requests[-1]['source_blend'].endswith('/candidate/scene.blend'))
        self.assertEqual(self.native_requests[-1]['recipe'],{})
        self.assertNotIn('scene_checkpoints',project['history'][0]['plan'])

    def test_uncertain_comparison_keeps_original_and_only_exposes_candidate_in_job(self):
        job=self.optimize(result={'decision':'uncertain','confidence':0,'source':'unavailable','reason':'视觉检查不可用','llm_calls':0})
        project=store.get('project','fixture')
        self.assertEqual(job['optimization_decision'],'kept_original');self.assertEqual(len(job['hero_candidates']),1)
        self.assertEqual(project['version'],1);self.assertEqual(project['outputs'],[self.output]);self.assertFalse(project['history'])
        self.assertEqual(len([item for item in self.native_requests if item['action']=='render']),2)

    def test_actual_no_change_skips_visual_call_and_never_creates_a_version(self):
        job=hero.submit('fixture',self.body,'no-change')
        def same(job,request,out):
            result=self.native(job,request,out)
            if 'proof' in result:result['catalog']=copy.deepcopy(self.catalog)
            return result
        with patch('hero_optimizer._native',side_effect=same),patch('hero_ai.plan_hero',return_value=self.planned), \
             patch('hero_ai.compare_hero') as compare,patch('ai_config.get_vision_config',return_value={}):
            hero.run_job(job)
        compare.assert_not_called()
        self.assertEqual(store.get('project','fixture')['version'],1)
        self.assertEqual(store.get('job',job['id'])['optimization_decision'],'kept_original')

    def test_source_byte_change_is_rejected_even_with_same_size_and_mtime(self):
        import os
        job=hero.submit('fixture',self.body,'mutation');stat=self.blend.stat();data=self.blend.read_bytes()
        self.blend.write_bytes(b'X'+data[1:]);os.utime(self.blend,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with self.assertRaises(ValueError):hero._check_guard(job)
        self.assertEqual(store.get('project','fixture')['version'],1)

    def test_version_change_during_comparison_cannot_commit_but_keeps_candidate(self):
        job=hero.submit('fixture',self.body,'changed')
        def compare(*args):
            project=store.get('project','fixture');project['version']=2;store.put('project',project)
            return self.compared
        with patch('hero_optimizer._native',side_effect=self.native),patch('hero_ai.plan_hero',return_value=self.planned), \
             patch('hero_ai.compare_hero',side_effect=compare),patch('ai_config.get_vision_config',return_value={}):
            with self.assertRaises(ValueError):hero.run_job(job)
        project=store.get('project','fixture')
        self.assertEqual(project['version'],2);self.assertEqual(len(project['outputs']),1);self.assertFalse(project['history'])
        self.assertEqual(len(store.get('job',job['id'])['hero_candidates']),1)

    def test_cancelled_during_planning_never_renders_or_commits(self):
        job=hero.submit('fixture',self.body,'cancel')
        def plan(*args,**kwargs):engine.cancel(job['id']);return self.planned
        with patch('hero_optimizer._native',side_effect=self.native),patch('hero_ai.plan_hero',side_effect=plan), \
             patch('ai_config.get_vision_config',return_value={}):
            with self.assertRaises(InterruptedError):hero.run_job(job)
        self.assertEqual([item['action'] for item in self.native_requests],['inspect'])
        self.assertEqual(store.get('project','fixture')['version'],1)

    def test_false_preservation_or_clipped_candidate_cannot_be_adopted(self):
        def invalid(job,request,out):
            result=self.native(job,request,out)
            if Path(out).name=='candidate':result['proof']['geometry_preserved']=False
            return result
        with self.assertRaises(RuntimeError):self.optimize(native=invalid)
        self.assertEqual(store.get('project','fixture')['version'],1)

    def test_checkpoint_uses_native_executor_and_undo_restores_original_plan(self):
        self.optimize();project=store.get('project','fixture')
        job={'id':'followup','project_id':'fixture','status':'running','plan':project['plan']}
        store.put('job',job)
        request={'action':'render','plan':project['plan'],'shot':project['plan']['shots'][0],'quality':'preview'}
        with patch('hero_optimizer._native',side_effect=self.native) as native,patch('engine.subprocess.Popen') as old_worker:
            engine.execute(job,request,self.root/'fixture/runs/followup')
            native.assert_called_once();old_worker.assert_not_called()
        actual=self.native_requests[-1]
        self.assertEqual(actual['plan'],project['plan']);self.assertEqual(actual['recipe'],{})
        self.assertEqual(actual['baseline_plan']['shots'][0],project['plan']['shots'][0])
        response=self.client.post('/api/projects/fixture/undo')
        self.assertEqual(response.status_code,200);self.assertNotIn('scene_checkpoints',response.get_json()['plan'])
        restored=response.get_json()['outputs'][-1]
        self.assertEqual(restored['version'],3);self.assertEqual(restored['png_url'],self.output['png_url'])
        self.assertEqual(restored['blend_url'],self.output['blend_url'])

    def test_checkpoint_paths_and_missing_files_are_rejected(self):
        for token in ('../secret','A'*32,'a'*31,'/tmp/a.blend'):
            with self.subTest(token=token),self.assertRaises(ValueError):planner.validate({**self.project['plan'],'scene_checkpoints':{'hero':token}})
        valid={**self.project['plan'],'scene_checkpoints':{'hero':'a'*32}}
        planner.validate(valid)
        with self.assertRaises(ValueError):web.validate_project_plan('fixture',valid)

    def test_route_accepts_hero_task_and_conflicting_version_returns_409(self):
        response=self.client.post('/api/projects/fixture/hero-optimize',json=self.body,headers={'Idempotency-Key':'route'})
        self.assertEqual(response.status_code,202);self.assertEqual(response.get_json()['kind'],'hero_optimize')
        other=self.client.post('/api/projects/fixture/hero-optimize',json={**self.body,'expected_version':0},headers={'Idempotency-Key':'other'})
        self.assertEqual(other.status_code,409)

    def test_retry_preserves_snapshot_but_refuses_changed_source(self):
        old=hero.submit('fixture',self.body,'retry');old.update(status='failed',error='test');store.put('job',old)
        retried=hero.retry_job(old);self.assertNotEqual(retried['id'],old['id']);self.assertEqual(retried['hero_guard'],old['hero_guard'])
        engine.cancel(retried['id']);self.blend.write_bytes(b'BLENDER changed')
        with self.assertRaises(ValueError):hero.retry_job(old)

    def test_updated_plan_uses_fresh_scene_and_fresh_png_for_planning(self):
        project=store.get('project','fixture');project['version']=2;project['plan']['exposure']=.3;store.put('project',project)
        self.body['expected_version']=2;seen=[]
        def worker(job,request,out):
            out=Path(out);out.mkdir(parents=True)
            Image.new('RGB',(64,64),(240,240,240)).save(out/'fresh.png')
            (out/'fresh.blend').write_bytes(b'BLENDER current plan fixture')
            return {'success':True,'checks':{'kind':'complete_product','clipped':False}}
        def plan(catalog,image,*args,**kwargs):seen.append(str(image));return self.planned
        job=hero.submit('fixture',self.body,'fresh')
        with patch('engine.execute',side_effect=worker) as render,patch('hero_optimizer._native',side_effect=self.native), \
             patch('hero_ai.plan_hero',side_effect=plan),patch('hero_ai.compare_hero',return_value=self.compared), \
             patch('ai_config.get_vision_config',return_value={}):
            hero.run_job(job)
        render.assert_called_once();self.assertTrue(seen[0].endswith('/current_plan/fresh.png'))
        self.assertTrue(self.native_requests[0]['source_blend'].endswith('/current_plan/fresh.blend'))
        changed=store.get('project','fixture');self.assertEqual(changed['version'],3)
        self.assertTrue(changed['history'][-1]['hero_restore_output']['png_url'].endswith('/current_plan/fresh.png'))

    def test_scene_change_during_inspection_is_rejected_before_any_model_request(self):
        job=hero.submit('fixture',self.body,'inspect-change')
        def inspect(*args):
            self.blend.write_bytes(b'BLENDER changed during read')
            return {'success':True,'catalog':self.catalog}
        with patch('hero_optimizer._native',side_effect=inspect),patch('hero_ai.plan_hero') as plan:
            with self.assertRaises(ValueError):hero.run_job(job)
        plan.assert_not_called();self.assertEqual(store.get('project','fixture')['version'],1)

    def test_png_hash_and_final_scene_state_must_match_the_selected_candidate(self):
        def mismatch(job,request,out):
            result=self.native(job,request,out)
            if Path(out).name=='final':
                result['catalog']['scene_state']['camera']['focal_length']=90.0
                self.rehash(result['catalog'])
            return result
        with self.assertRaisesRegex(RuntimeError,'场景状态不一致'):self.optimize(native=mismatch)
        self.assertEqual(store.get('project','fixture')['version'],1)
        job={'id':'proof','project_id':'fixture','shot_ids':['hero']};out=self.root/'fixture/proof'
        request=hero._request(self.blend,hero.digest(self.blend),['Product'],'render',resolution=48,recipe={})
        result=self.native(job,request,out)
        Image.new('RGB',(48,48),'red').save(out/'image.png')
        with self.assertRaisesRegex(RuntimeError,'执行证明不一致'):hero._validate_render(result,request,out)

    def test_explicit_checkpoint_color_edit_requires_matching_plan_proof(self):
        self.optimize();project=store.get('project','fixture');plan=copy.deepcopy(project['plan'])
        plan['object_material_overrides']=[{'object_name':'Product','material_name':'Paint','color':'#101010'}]
        job={'id':'color-followup','project_id':'fixture','status':'running','plan':plan};store.put('job',job)
        request={'action':'render','plan':plan,'shot':plan['shots'][0],'quality':'preview'}
        def changed(job,request,out):
            result=self.native(job,request,out);proof=result['proof']
            proof.update(color_preserved=False,request_match=True,request_plan_sha256=hero.fingerprint(request['plan']),
                         request_baseline_plan_sha256=hero.fingerprint(request['baseline_plan']),
                         authorized_color_changes=[{'path':'object_material_overrides[0].color','requested_color':'#101010',
                           'bindings':[{'object_name':'Product','slot_index':0}],'before':[1,0,0,1],'after':[.01,.01,.01,1]}])
            proof['after']['color_sha256']='updated-color'
            return result
        with patch('hero_optimizer._native',side_effect=changed):
            result=engine.execute(job,request,self.root/'fixture/runs/color-followup')
        self.assertFalse(result['proof']['color_preserved'])
        wrong=copy.deepcopy(result['proof']);wrong['request_plan_sha256']='wrong'
        self.assertFalse(hero._authorized_color_change(wrong,self.native_requests[-1]))

    def test_retry_reinspects_legacy_results_and_reuses_final_without_ai_or_render(self):
        old,folder=self.failed_legacy_run()
        before_png=hero.digest(folder/'final/image.png');before_blend=hero.digest(folder/'final/scene.blend')
        recovered=self.recover(old);project=store.get('project','fixture')
        self.assertEqual(recovered['recovery_status'],'reused_final')
        self.assertEqual(recovered['llm_calls'],0);self.assertEqual(recovered['reused_llm_calls'],2)
        self.assertTrue(recovered['scene_verification']['roundoff_only'])
        self.assertEqual([row['action'] for row in self.native_requests],['inspect','inspect','inspect'])
        self.assertEqual((project['version'],len(project['history'])),(2,1))
        self.assertEqual(project['outputs'][-1]['png_url'],hero._url(folder/'final/image.png'))
        self.assertEqual(hero.digest(folder/'final/image.png'),before_png)
        self.assertEqual(hero.digest(folder/'final/scene.blend'),before_blend)
        replay=hero.retry_job(old)
        self.assertEqual(replay['id'],recovered['id']);self.assertEqual(len(store.all_records('job')),2)

    def test_retry_rejects_real_scene_difference_and_only_renders_selected_snapshot(self):
        old,folder=self.failed_legacy_run(final_focal_length=90.0)
        recovered=self.recover(old)
        renders=[row for row in self.native_requests if row['action']=='render']
        self.assertEqual(len(renders),1);self.assertEqual(recovered['recovery_status'],'rerendered_final')
        self.assertEqual(recovered['llm_calls'],0)
        self.assertEqual(renders[0]['recipe'],{})
        self.assertEqual(Path(renders[0]['source_blend']),folder/'candidate/scene.blend')
        self.assertFalse(recovered['recovery_final_mismatch']['equivalent'])
        self.assertTrue(any(row['path'].endswith('focal_length') for row in recovered['recovery_final_mismatch']['differences']))

    def test_retry_rejects_corrupt_final_but_preserves_valid_candidate_and_budget(self):
        old,folder=self.failed_legacy_run()
        (folder/'final/scene.blend').write_bytes(b'BLENDER changed final file')
        recovered=self.recover(old)
        self.assertEqual(recovered['recovery_status'],'rerendered_final')
        self.assertEqual(len([row for row in self.native_requests if row['action']=='render']),1)
        self.assertIn('执行证明不一致',recovered['recovery_final_notice'])

    def test_retry_missing_or_modified_candidate_cannot_be_adopted_or_replanned(self):
        old,folder=self.failed_legacy_run()
        Image.new('RGB',(768,768),'red').save(folder/'candidate/image.png')
        new=hero.retry_job(old)
        with patch('hero_optimizer._native',side_effect=self.native),patch('hero_ai.plan_hero') as plan,patch('hero_ai.compare_hero') as compare:
            with self.assertRaisesRegex(RuntimeError,'执行证明不一致'):hero.run_job(new)
        plan.assert_not_called();compare.assert_not_called()
        self.assertEqual(self.native_requests,[])
        self.assertEqual(store.get('project','fixture')['version'],1)

    def test_retry_request_recipe_cannot_be_detached_from_selected_candidate(self):
        old,folder=self.failed_legacy_run()
        path=folder/'candidate/request.json';request=json.loads(path.read_text());request['recipe']={}
        path.write_text(json.dumps(request))
        new=hero.retry_job(old)
        with patch('hero_optimizer._native',side_effect=self.native),patch('hero_ai.plan_hero') as plan:
            with self.assertRaisesRegex(ValueError,'配方或镜头不一致'):hero.run_job(new)
        plan.assert_not_called();self.assertEqual(store.get('project','fixture')['version'],1)


if __name__=='__main__':unittest.main()

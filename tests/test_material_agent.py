import copy
import hashlib
import io
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


class MaterialAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old_data=store.DATA;self.old_agent_data=agent.DATA;self.old_queue=engine.QUEUE
        store.DATA=agent.DATA=self.root;engine.QUEUE=queue.Queue()
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.project={'id':'fixture','version':3,'plan':default_plan(),'analysis':{'objects':[]},'history':[],
                      'outputs':[{'job_id':'render','shot_id':'hero','quality':'preview','version':3}]}
        store.put('project',self.project)
        self.body={'output_id':'render','shot_id':'hero','quality':'preview','version':3,'points':[{'id':'one','x':.3,'y':.4}],
                   'prompt':'细织棉布','use_base_color':False}
        self.hit={'hit':True,'object_name':'Selected','material_name':'Shared','material_info':{'editable':True}}
        self.selection={'asset_id':'fabric_pattern_07','name':'Fabric Pattern 07','url':'https://polyhaven.com/a/fabric_pattern_07',
                        'license':'CC0-1.0','license_url':agent.LICENSE_URL,'mode':'rules'}
        self.resource={**{k:self.selection[k] for k in ('asset_id','name','url','license_url')},'provider':'Poly Haven','resolution':'1k','downloaded_at':1,
                       'manifest_path':str(self.root/'fixture/materials/resource/manifest.json')}
        self.textures={'roughness':str(self.root/'fixture/materials/resource/roughness.png')}
        self.patches=[patch.object(agent.threading,'Thread'),patch.object(engine,'pick_output',return_value={'hits':[self.hit]}),
                      patch.object(agent,'_query',return_value=('cotton fabric','rules',None)),patch.object(agent,'_catalogue',return_value={}),
                      patch.object(agent,'_choose',return_value=self.selection),patch.object(agent,'_download',return_value=(self.textures,self.resource))]
        self.thread,self.pick,self.query,self.catalogue,self.choose,self.download=[p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        store.DATA=self.old_data;agent.DATA=self.old_agent_data;engine.QUEUE=self.old_queue;self.tmp.cleanup()

    def run_task(self,**changes):
        task=agent.submit('fixture',{**self.body,**changes});agent._run(task['id']);return agent.get_task('fixture',task['id'])

    def test_actual_render_job_required_for_completion(self):
        task=self.run_task();self.assertEqual(task['status'],'rendering')
        project=store.get('project','fixture');self.assertEqual(project['version'],4)
        entry=project['plan']['object_material_overrides'][0]
        self.assertTrue(entry['preserve_original']);self.assertEqual(entry['textures'],self.textures)
        self.assertNotIn('base_color',entry['textures']);self.assertNotIn('preset',entry)
        job=store.get('job',task['job_id']);self.assertEqual(job['shot_ids'],['hero'])
        self.assertEqual(job['version'],4);job['status']='completed';store.put('job',job)
        self.assertEqual(agent.get_task('fixture',task['id'])['status'],'completed')

    def test_duplicate_submit_has_one_job_and_one_version(self):
        task=self.run_task();again=agent.submit('fixture',self.body)
        self.assertEqual(task['id'],again['id']);self.assertEqual(len(store.all_records('job')),1)
        self.assertEqual(store.get('project','fixture')['version'],4)

    def test_preserve_color_keeps_previously_applied_color_texture(self):
        old_color=str(self.root/'fixture/materials/previous/color.png')
        self.project['plan']['object_material_overrides']=[{'object_name':'Selected','material_name':'Shared','preserve_original':True,'textures':{'base_color':old_color}}]
        store.put('project',self.project);task=self.run_task()
        self.assertEqual(task['status'],'rendering')
        textures=store.get('project','fixture')['plan']['object_material_overrides'][0]['textures']
        self.assertEqual(textures['base_color'],old_color)
        self.assertEqual(textures['roughness'],self.textures['roughness'])

    def test_normal_only_resource_without_uv_is_not_reported_applied(self):
        self.project['analysis']['objects']=[{'name':'Selected','uv_layers':[]}];store.put('project',self.project)
        self.download.return_value=({'normal':str(self.root/'fixture/materials/resource/normal.png')},self.resource)
        task=self.run_task();self.assertEqual(task['status'],'failed');self.assertIn('UV',task['error'])
        self.assertEqual(store.get('project','fixture')['version'],3);self.assertFalse(store.all_records('job'))

    def test_different_request_while_active_rejected(self):
        agent.submit('fixture',self.body)
        with self.assertRaisesRegex(ValueError,'已有'):agent.submit('fixture',{**self.body,'prompt':'橡木'})

    def test_stale_submit_rejected_without_task(self):
        with self.assertRaises(ValueError):agent.submit('fixture',{**self.body,'version':2})
        self.assertFalse(store.all_records(agent.KIND))

    def test_stale_during_network_download_does_not_overwrite(self):
        def download(*args):
            project=store.get('project','fixture');project['version']=4;project['plan']['exposure']=1;store.put('project',project)
            return self.textures,self.resource
        self.download.side_effect=download
        task=self.run_task();self.assertEqual(task['status'],'failed')
        self.assertEqual(store.get('project','fixture')['plan']['exposure'],1)
        self.assertFalse(store.all_records('job'))

    def test_background_and_unsupported_shaders_fail_without_mutation(self):
        for hits in [[{'hit':False}],[{**self.hit,'material_info':{'editable':False}}]]:
            self.pick.return_value={'hits':hits}
            task=self.run_task();self.assertEqual(task['status'],'failed')
            self.assertEqual(store.get('project','fixture')['version'],3)
        self.download.assert_not_called()

    def test_cancelled_queued_task_does_no_work(self):
        task=agent.submit('fixture',self.body);agent.cancel('fixture',task['id']);agent._run(task['id'])
        self.pick.assert_not_called();self.assertEqual(store.get('project','fixture')['version'],3)

    def test_cancel_during_download_prevents_application(self):
        def download(task_id,*args):
            agent.cancel('fixture',task_id);return self.textures,self.resource
        self.download.side_effect=download
        task=self.run_task();self.assertEqual(task['status'],'cancelled')
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_cancel_render_preserves_saved_material_and_cancels_job(self):
        task=self.run_task();task=agent.cancel('fixture',task['id'])
        self.assertEqual(store.get('job',task['job_id'])['status'],'cancelled')
        self.assertEqual(store.get('project','fixture')['version'],4)
        self.assertIn('已应用',task['stage'])

    def test_submit_failure_rolls_back_plan(self):
        with patch.object(engine,'submit',side_effect=ValueError('queue unavailable')):task=self.run_task()
        self.assertEqual(task['status'],'failed');self.assertEqual(store.get('project','fixture')['version'],3)

    def test_foreign_task_rejected(self):
        task=agent.submit('fixture',self.body)
        with self.assertRaises(KeyError):agent.get_task('other',task['id'])
        with self.assertRaises(KeyError):agent.cancel('other',task['id'])

    def test_restart_reconciles_saved_render_link(self):
        task=self.run_task();task['status']='running';task.pop('job_id');store.put(agent.KIND,task)
        agent.recover_interrupted();task=agent.get_task('fixture',task['id'])
        self.assertEqual(task['status'],'rendering');self.assertEqual(len(store.all_records('job')),1)
        self.assertEqual(store.get('project','fixture')['version'],4)

    def test_restart_marks_search_interrupted(self):
        task=agent.submit('fixture',self.body);agent.recover_interrupted()
        self.assertEqual(agent.get_task('fixture',task['id'])['status'],'interrupted')

    def test_restart_render_interrupted_is_terminal_and_retains_material(self):
        task=self.run_task();job=store.get('job',task['job_id']);job['status']='interrupted';store.put('job',job)
        agent.recover_interrupted();task=agent.get_task('fixture',task['id'])
        self.assertEqual(task['status'],'interrupted');self.assertIn('重新',task['error'])
        self.assertEqual(store.get('project','fixture')['version'],4)


class MaterialNetworkTests(unittest.TestCase):
    def test_url_allowlist_rejects_local_credentials_and_other_resources(self):
        for url in ['http://dl.polyhaven.org/file/ph-assets/Textures/1.png','https://127.0.0.1/a.png',
                    'https://dl.polyhaven.org.evil.test/file/ph-assets/Textures/a.png','https://user@dl.polyhaven.org/file/ph-assets/Textures/a.png',
                    'https://dl.polyhaven.org/file/ph-assets/Models/scene.blend','https://dl.polyhaven.org:8000/file/ph-assets/Textures/a.png']:
            with self.assertRaises(ValueError):agent._url(url,True)
        self.assertEqual(agent._url('https://dl.polyhaven.org/file/ph-assets/Textures/a.png',True),'https://dl.polyhaven.org/file/ph-assets/Textures/a.png')

    def test_redirects_always_rejected(self):
        with self.assertRaises(ValueError):agent._NoRedirect().redirect_request(None,None,302,'',{},'https://127.0.0.1')

    def test_semantically_unrelated_catalogue_never_selected(self):
        assets={'concrete':{'name':'Smooth Concrete','categories':['concrete'],'tags':['smooth'],'type':1}}
        with self.assertRaises(ValueError):agent._choose('玻璃','smooth glass',assets,'rules')

    def test_ai_cannot_select_invented_asset(self):
        assets={'fabric':{'name':'Cotton Fabric','categories':['fabric'],'tags':['cotton'],'type':1}}
        with patch.object(agent,'_ai_json',return_value={'asset_id':'evil','confidence':1,'reason':'bad'}):
            with self.assertRaises(LookupError):agent._choose('织物','cotton fabric',assets,'ai')

    def test_explicit_no_match_from_ai_does_not_fallback_to_random(self):
        assets={'fabric':{'name':'Cotton Fabric','categories':['fabric'],'tags':['cotton'],'type':1}}
        with patch.object(agent,'_ai_json',return_value={'asset_id':'','confidence':0,'reason':'no'}):
            with self.assertRaises(LookupError):agent._choose('新型纤维','cotton fabric',assets,'ai')

    def test_exact_family_rules_match(self):
        assets={'fabric':{'name':'Cotton Fabric','categories':['fabric'],'tags':['cotton'],'type':1}}
        result=agent._choose('织物','cotton fabric',assets,'rules')
        self.assertEqual(result['asset_id'],'fabric');self.assertEqual(result['mode'],'rules')

    def test_fine_cotton_description_ranks_above_broad_linen_category(self):
        assets={
            'rough_linen':{'name':'Rough Linen','categories':['fabric','cotton','hessian'],'description':'Rough blue linen with fine woven crosshatch, natural matte surface for cloth.'},
            'stretch_poplin':{'name':'Stretch Poplin','categories':['fabric','stretchy'],'description':'Green stretch poplin cotton with a fine tight weave.'}}
        seen=[]
        def choose(system,user,schema,name):
            seen.extend(user['candidates']);return {'asset_id':user['candidates'][0]['asset_id'],'reason':'fine cotton weave','confidence':.9}
        with patch.object(agent,'_ai_json',side_effect=choose):
            result=agent._choose('细织棉布','fine woven cotton fabric',assets,'ai')
        self.assertEqual(seen[0]['asset_id'],'stretch_poplin')
        self.assertEqual(result['asset_id'],'stretch_poplin')

    def test_official_non_square_1k_normal_dimensions_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=io.BytesIO();Image.new('RGB',(1024,1026),(127,127,255)).save(stream,format='PNG');raw=stream.getvalue()
            item={'url':'https://dl.polyhaven.org/file/ph-assets/Textures/png/1k/rough_linen/normal.png','size':len(raw),'md5':hashlib.md5(raw).hexdigest()}
            task={'id':'task','project_id':'fixture','status':'running'}
            selection={'asset_id':'rough_linen','name':'Rough Linen','url':'https://polyhaven.com/a/rough_linen','license_url':agent.LICENSE_URL,'license':'CC0-1.0'}
            with patch.object(agent,'DATA',Path(tmp)),patch.object(agent,'_check',return_value=task),patch.object(agent,'_update'),patch.object(agent,'_json',return_value={'nor_gl':{'1k':{'png':item}}}),patch.object(agent,'_fetch',return_value=raw):
                textures,resource=agent._download('task',selection,False)
                self.assertEqual(engine.verify_image(textures['normal']),(1024,1026))
                import json
                manifest=json.loads(Path(resource['manifest_path']).read_text(encoding='utf-8'))
                self.assertEqual(manifest['files']['normal']['height'],1026)
                with patch.object(engine,'verify_image',return_value=(2048,2048)):
                    with self.assertRaisesRegex(ValueError,'尺寸超过'):agent._download('task',selection,False)

    def test_download_checks_checksum_dimensions_and_preserves_color_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=io.BytesIO();Image.new('RGB',(32,32),'grey').save(stream,format='PNG');raw=stream.getvalue()
            item={'url':'https://dl.polyhaven.org/file/ph-assets/Textures/png/1k/fabric/rough.png','size':len(raw),'md5':hashlib.md5(raw).hexdigest()}
            task={'id':'task','project_id':'fixture','status':'running'}
            selection={'asset_id':'fabric','name':'Fabric','url':'https://polyhaven.com/a/fabric','license_url':agent.LICENSE_URL,'license':'CC0-1.0'}
            files={'rough':{'1k':{'png':item}},'diff':{'1k':{'png':item}}}
            with patch.object(agent,'DATA',Path(tmp)),patch.object(agent,'_check',return_value=task),patch.object(agent,'_update'),patch.object(agent,'_json',return_value=files),patch.object(agent,'_fetch',return_value=raw):
                textures,resource=agent._download('task',selection,False)
                self.assertEqual(set(textures),{'roughness'});self.assertTrue(Path(resource['manifest_path']).is_file())
                item['md5']='0'*32
                with self.assertRaisesRegex(ValueError,'校验'):agent._download('task',selection,False)


if __name__=='__main__':unittest.main()

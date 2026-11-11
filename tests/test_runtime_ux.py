"""Regression coverage for unavailable engines, empty selection and stale edits.

These tests use isolated storage and stub executable discovery. They never run
Blender, contact an AI service, or imply that missing rendering code is present.
"""
import copy
import io
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as web
import engine
import scene_exports
import studio_store as store
from planner import default_plan


class RuntimeUXTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        for module in (store,web,engine,scene_exports):
            mocked=patch.object(module,'DATA',self.data);mocked.start();self.addCleanup(mocked.stop)
        for module in (engine,web):
            mocked=patch.object(module,'ROOT',self.root);mocked.start();self.addCleanup(mocked.stop)
        for module in (engine,scene_exports):
            mocked=patch.object(module,'QUEUE',queue.Queue());mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'blender_path',return_value='/test/blender');mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.project={'id':'fixture','slug':'fixture','name':'Fixture','version':1,'history':[],
                      'status':'ready','analysis':{'objects':[]},'plan':default_plan(),'outputs':[]}
        store.put('project',copy.deepcopy(self.project))
        self.client=web.app.test_client()

    def test_blender_installation_alone_does_not_claim_readiness(self):
        response=self.client.get('/api/system')
        self.assertEqual(response.status_code,200)
        status=response.get_json()
        self.assertTrue(status['blender_available'])
        self.assertFalse(status['available'])
        self.assertIn('blender/worker.py',status['runtime']['missing_components'])
        self.assertIn('组件不完整',status['runtime']['message'])

    def test_optional_components_have_independent_capabilities(self):
        (self.root/'blender').mkdir();(self.root/'blender/worker.py').write_text('# fixture')
        status=engine.runtime_status()
        self.assertTrue(status['available'])
        self.assertFalse(status['capabilities']['pick'])
        self.assertFalse(status['capabilities']['export_blend'])

    def test_upload_rejects_missing_worker_before_any_project_files(self):
        # SQLite may remove its transient WAL/SHM files when a prior connection
        # is collected; only application resources must remain unchanged.
        resources=lambda:{path for path in self.data.iterdir() if path.name not in {'studio.sqlite-wal','studio.sqlite-shm'}}
        before=resources()
        response=self.client.post('/api/projects',data={'files':(io.BytesIO(b'v 0 0 0'),'item.obj')})
        self.assertEqual(response.status_code,400)
        self.assertIn('blender/worker.py',response.get_json()['error'])
        self.assertEqual(resources(),before)
        self.assertEqual(len(store.all_records('project')),1)
        self.assertEqual(engine.QUEUE.qsize(),0)

    def test_unavailable_render_does_not_create_failed_queue_entry(self):
        response=self.client.post('/api/projects/fixture/jobs',json={'kind':'render','shot_ids':['hero']})
        self.assertEqual(response.status_code,400)
        self.assertEqual(store.all_records('job'),[])
        self.assertEqual(engine.QUEUE.qsize(),0)

    def test_unavailable_retry_does_not_create_another_job(self):
        old=engine.submit('fixture','preview')
        engine.update_job(old['id'],status='interrupted')
        response=self.client.post('/api/jobs/'+old['id']+'/retry')
        self.assertEqual(response.status_code,400)
        self.assertEqual(len(store.all_records('job')),1)

    def test_empty_selection_cannot_render_all_shots(self):
        with self.assertRaisesRegex(ValueError,'至少选择'):
            engine.submit('fixture','preview',shot_ids=[])
        self.assertEqual(engine.QUEUE.qsize(),0)
        self.assertEqual(store.all_records('job'),[])

    def test_formal_render_defaults_to_standard_quality(self):
        job=engine.submit('fixture','render',shot_ids=['hero'])
        self.assertEqual(job['quality'],'standard')
        self.assertEqual(job['shot_ids'],['hero'])

    def test_stale_plan_header_cannot_overwrite_saved_work(self):
        response=self.client.post('/api/projects/fixture/plan',json={'margin':.3},headers={'X-Plan-Version':'0'})
        self.assertEqual(response.status_code,409)
        project=store.get('project','fixture')
        self.assertEqual(project['version'],1)
        self.assertEqual(project['plan']['margin'],.12)
        self.assertEqual(project['history'],[])

    def test_current_plan_header_saves_the_edit(self):
        response=self.client.post('/api/projects/fixture/plan',json={'margin':.3},headers={'X-Plan-Version':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['version'],2)
        self.assertEqual(response.get_json()['plan']['margin'],.3)

    def test_wrapped_expected_version_is_also_guarded(self):
        response=self.client.post('/api/projects/fixture/plan',json={'plan':{'margin':.3},'expected_version':0})
        self.assertEqual(response.status_code,409)
        self.assertEqual(store.get('project','fixture')['version'],1)

    def test_scene_export_checks_its_component_before_queueing(self):
        source=self.data/'fixture'/'hero.blend';source.parent.mkdir();source.write_bytes(b'test fixture only')
        output={'job_id':'old','shot_id':'hero','shot_label':'主图','quality':'preview','version':1,'blend_url':'/files/fixture/hero.blend'}
        project=store.get('project','fixture');project['outputs']=[output];store.put('project',project)
        body={key:output[key] for key in ('shot_id','quality','version')};body.update(format='blend',output_id='old')
        response=self.client.post('/api/projects/fixture/exports',json=body)
        self.assertEqual(response.status_code,400)
        self.assertIn('blender/export_scene.py',response.get_json()['error'])
        self.assertEqual(store.all_records('export'),[])

    def test_material_upload_preserves_concurrent_photography_edits(self):
        def concurrent_edit(_path):
            project=store.get('project','fixture');project['plan']['margin']=.3;project['version']=2;store.put('project',project)
            return (8,8)
        with patch.object(engine,'verify_image',side_effect=concurrent_edit):
            response=self.client.post('/api/projects/fixture/materials/import',data={
                'name':'Paint','source':'Test fixture','license':'CC0',
                'roughness':(io.BytesIO(b'test fixture image'),'roughness.png')})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['plan']['margin'],.3)
        self.assertEqual(response.get_json()['version'],3)

    def test_material_upload_rejects_concurrent_change_to_same_slot(self):
        def concurrent_edit(_path):
            project=store.get('project','fixture')
            project['plan']['material_overrides']=[{'name':'Paint','preset':'brushed_metal'}]
            project['version']=2;store.put('project',project)
            return (8,8)
        with patch.object(engine,'verify_image',side_effect=concurrent_edit):
            response=self.client.post('/api/projects/fixture/materials/import',data={
                'name':'Paint','source':'Test fixture','license':'CC0',
                'roughness':(io.BytesIO(b'test fixture image'),'roughness.png')})
        self.assertEqual(response.status_code,409)
        self.assertEqual(store.get('project','fixture')['version'],2)
        self.assertEqual(list((self.data/'fixture/materials').iterdir()),[])

    def test_missing_examples_explain_the_actual_problem(self):
        response=self.client.post('/api/examples/test/import')
        self.assertEqual(response.status_code,400)
        self.assertIn('没有附带示例模型',response.get_json()['error'])

    def test_analysis_cancel_restart_and_retry_keep_project_status_accurate(self):
        project=store.get('project','fixture');project.update(analysis=None,status='analyzing');store.put('project',project)
        job=engine.submit('fixture','analyze')
        engine.cancel(job['id'])
        self.assertEqual(store.get('project','fixture')['status'],'cancelled')
        with patch.object(engine,'require_runtime'):
            response=self.client.post('/api/jobs/'+job['id']+'/retry')
        self.assertEqual(response.status_code,202)
        self.assertEqual(store.get('project','fixture')['status'],'analyzing')
        with patch('engine.threading.Thread'):
            engine.start()
        self.assertEqual(store.get('project','fixture')['status'],'interrupted')
        project=store.get('project','fixture');project.update(analysis={'objects':[]},status='ready');store.put('project',project)
        render=engine.submit('fixture','preview',shot_ids=['hero'])
        engine.cancel(render['id'])
        self.assertEqual(store.get('project','fixture')['status'],'ready')


if __name__=='__main__':unittest.main()

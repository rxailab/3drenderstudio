"""Version, asset provenance, and lifecycle contracts for local viewport tasks."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import planner
import project_lifecycle
import studio_store as store
import viewport_service as viewport


def glb(path,external=False):
    data={'asset':{'version':'2.0'},'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
          'meshes':[{'primitives':[{'attributes':{}}]}]}
    if external:data['images']=[{'uri':'../secret.png'}]
    payload=json.dumps(data).encode();payload+=b' '*((-len(payload))%4)
    path.write_bytes(struct.pack('<4sIIII',b'glTF',2,20+len(payload),len(payload),0x4E4F534A)+payload)


class ViewportServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for module,name,value in ((store,'DATA',self.root),(viewport,'DATA',self.root),
                                   (viewport,'TASKS',{}),(viewport,'PROCESSES',{}),
                                   (project_lifecycle,'OPERATIONS',{})):
            mocked=patch.object(module,name,value);mocked.start();self.addCleanup(mocked.stop)
        self.runtime=patch('engine.blender_path',return_value='blender');self.runtime.start();self.addCleanup(self.runtime.stop)
        self.threads=patch('viewport_service.threading.Thread');self.thread=self.threads.start();self.addCleanup(self.threads.stop)
        with store.connection() as c:c.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.source=self.root/'fixture/runs/a/source.blend';self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'Unit test source identity, not a Blender scene')
        self.plan=planner.default_plan()
        self.output={'job_id':'a','shot_id':'hero','version':1,'blend_url':'/files/fixture/runs/a/source.blend',
                     'blend_sha256':viewport._sha(self.source),'effective_plan':copy.deepcopy(self.plan)}
        self.project={'id':'fixture','version':1,'status':'ready','plan':self.plan,'analysis':{'objects':[{'name':'Body','type':'MESH'}]},
                      'outputs':[self.output],'history':[],'model_path':str(self.source)}
        store.put('project',self.project)
        self.body={'shot_id':'hero','expected_version':1}

    def submit(self):return viewport.submit('fixture',self.body)

    def cache(self,item):
        record=viewport.TASKS[item['id']];folder=Path(record['_folder']);folder.mkdir(parents=True,exist_ok=True)
        glb(folder/'scene.glb')
        manifest={'schema':viewport.SCHEMA,'fingerprint':record['_fingerprint'],
                  'asset_sha256':viewport._sha(folder/'scene.glb'),'warnings':[]}
        store.atomic_json(folder/'manifest.json',manifest)
        record['status']='ready';project_lifecycle.end_operation('fixture')
        return folder

    def test_same_request_dedupes_and_reserves_background_project(self):
        first=self.submit();second=self.submit()
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['status'],'queued')
        self.assertEqual(self.thread.call_count,1);self.assertEqual(project_lifecycle.OPERATIONS['fixture'],1)
        with self.assertRaises(project_lifecycle.ProjectBusyError):project_lifecycle._ensure_idle('fixture')
        self.assertIsNone(first['asset']);self.assertNotIn('_source',first)

    def test_client_paths_versions_and_shots_cannot_bypass_source_resolution(self):
        for patch_body in ({'path':'/secret.blend'},{'expected_version':99},{'shot_id':'elsewhere'},
                           {'expected_version':True},{'shot_id':'../../hero'}):
            with self.subTest(patch_body=patch_body),self.assertRaises(ValueError):
                viewport.submit('fixture',{**self.body,**patch_body})
        self.thread.assert_not_called();self.assertFalse(project_lifecycle.OPERATIONS)

    def test_cross_project_source_is_refused(self):
        project=store.get('project','fixture');project['outputs'][0]['blend_url']='/files/another/source.blend'
        store.put('project',project)
        with self.assertRaises(viewport.ViewportError):self.submit()

    def test_valid_cache_is_verified_and_does_not_start_blender_again(self):
        item=self.submit();folder=self.cache(item)
        ready=self.submit()
        self.assertEqual(ready['status'],'ready');self.assertEqual(self.thread.call_count,1)
        self.assertIn('/viewport/',ready['asset']['url']);self.assertIn('?v=',ready['asset']['url'])
        self.assertFalse(project_lifecycle.OPERATIONS)
        (folder/'scene.glb').write_bytes(b'corrupt')
        retry=self.submit();self.assertEqual(retry['status'],'queued');self.assertEqual(self.thread.call_count,2)

    def test_external_texture_changes_invalidate_cache_identity(self):
        texture=self.root/'fixture/originals/paint.png';texture.parent.mkdir();texture.write_bytes(b'first')
        first=self.submit();self.cache(first)
        texture.write_bytes(b'changed')
        second=self.submit()
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(second['status'],'queued')

    def test_plan_or_version_change_never_returns_previous_ready_asset(self):
        first=self.submit();self.cache(first)
        project=store.get('project','fixture');project['plan']['shots'][0]['azimuth']=70
        store.put('project',project)
        stale=viewport.read('fixture',first['id'])
        self.assertEqual(stale['status'],'stale');self.assertIsNone(stale['asset'])
        with self.assertRaises(viewport.ViewportError):viewport._current(viewport.TASKS[first['id']])

    def test_ready_poll_rechecks_source_and_asset_bytes(self):
        first=self.submit();folder=self.cache(first)
        (folder/'scene.glb').write_bytes(b'externally changed')
        self.assertEqual(viewport.read('fixture',first['id'])['status'],'stale')
        self.cache(first);self.source.write_bytes(b'external scene replacement')
        value=viewport.read('fixture',first['id'])
        self.assertEqual(value['status'],'stale');self.assertIsNone(value['asset'])

    def test_pending_current_plan_keeps_explicit_old_source_baseline(self):
        project=store.get('project','fixture');project['version']=2;project['plan']['shots'][0]['azimuth']=80
        store.put('project',project);self.body['expected_version']=2
        item=self.submit();record=viewport.TASKS[item['id']]
        self.assertEqual(item['source_kind'],'derived_scene');self.assertEqual(item['source_version'],1)
        self.assertEqual(record['_source']['baseline_plan']['shots'][0]['azimuth'],35)
        self.assertEqual(record['_project']['plan']['shots'][0]['azimuth'],80)

    def test_original_model_can_prepare_before_any_offline_output(self):
        project=store.get('project','fixture');project['outputs']=[];project['analysis']=None
        path=self.root/'fixture/originals/model.glb';path.parent.mkdir();glb(path)
        project['model_path']=str(path);store.put('project',project)
        item=self.submit();self.assertEqual(item['source_kind'],'original_model')
        self.assertEqual(item['source_version'],1);self.assertEqual(item['status'],'queued')

    def test_worker_failure_releases_project_and_exposes_no_asset(self):
        item=self.submit();record=viewport.TASKS[item['id']]
        with patch('viewport_service.subprocess.Popen',side_effect=OSError('fixture start failure')):
            viewport._execute(item['id'])
        self.assertEqual(record['status'],'failed');self.assertIsNone(record['asset'])
        self.assertFalse(project_lifecycle.OPERATIONS);self.assertFalse(viewport.PROCESSES)

    def test_stale_source_worker_does_not_start_blender(self):
        item=self.submit();self.source.write_bytes(b'changed source')
        with patch('viewport_service.subprocess.Popen') as process:viewport._execute(item['id'])
        process.assert_not_called();self.assertEqual(viewport.TASKS[item['id']]['status'],'stale')
        self.assertFalse(project_lifecycle.OPERATIONS)

    def test_glb_must_be_self_contained(self):
        path=self.root/'asset.glb';glb(path);self.assertTrue(viewport._glb(path))
        glb(path,external=True);self.assertFalse(viewport._glb(path))

    def test_http_contract_and_cross_project_task_guard(self):
        from app import app
        client=app.test_client()
        reply=client.post('/api/projects/fixture/viewport',json=self.body)
        self.assertEqual(reply.status_code,202);item=reply.get_json()
        self.assertEqual(client.get('/api/projects/fixture/viewport/'+item['id']).status_code,200)
        other=copy.deepcopy(self.project);other['id']='other';store.put('project',other)
        self.assertEqual(client.get('/api/projects/other/viewport/'+item['id']).status_code,404)
        self.assertEqual(client.post('/api/projects/fixture/viewport',json={**self.body,'expected_version':9}).status_code,409)
        self.assertEqual(project_lifecycle.OPERATIONS['fixture'],1)


if __name__=='__main__':unittest.main()

import hashlib
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import studio_store as store
import scene_exports as scenes

class SceneExportTests(unittest.TestCase):
    def setUp(self):
        self.readiness=patch('engine.require_runtime');self.readiness.start();self.addCleanup(self.readiness.stop)
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old_store=store.DATA;self.old_data=scenes.DATA;self.old_q=scenes.QUEUE
        store.DATA=self.root;scenes.DATA=self.root;scenes.QUEUE=queue.Queue()
        with store.connection() as c:c.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        p=self.root/'fixture/runs/old/hero.blend';p.parent.mkdir(parents=True);p.write_bytes(b'BLENDER fixture unit test only')
        (p.parent/'hero.png').write_bytes(b'PNG fixture identity only')
        self.output=dict(job_id='old',shot_id='hero',shot_label='主图',quality='preview',version=1,
                         blend_url='/files/fixture/runs/old/hero.blend',png_url='/files/fixture/runs/old/hero.png',
                         blend_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        store.put('project',dict(id='fixture',version=99,outputs=[self.output]))
        self.body=dict(format='blend',output_id='old',shot_id='hero',quality='preview',version=1)

    def tearDown(self):
        store.DATA=self.old_store;scenes.DATA=self.old_data;scenes.QUEUE=self.old_q;self.tmp.cleanup()

    def test_snapshot_does_not_use_latest_plan(self):
        a=scenes.submit('fixture',self.body);b=scenes.submit('fixture',self.body)
        self.assertEqual(a['version'],1);self.assertEqual(a['id'],b['id']);self.assertEqual(scenes.QUEUE.qsize(),1)

    def test_no_client_file_path_or_unknown_snapshot(self):
        with self.assertRaises(ValueError):scenes.submit('fixture',{**self.body,'output_id':'not-real','source_blend':'C:/secret.blend'})

    def test_identical_file_in_another_revision_keeps_snapshot_identity(self):
        first=scenes.submit('fixture',self.body)
        p=store.get('project','fixture');p['outputs'].append({**self.output,'version':2});store.put('project',p)
        second=scenes.submit('fixture',{**self.body,'version':2})
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(second['version'],2)

    def test_reject_source_outside_project(self):
        p=store.get('project','fixture');p['outputs'][0]['blend_url']='/files/../escape.blend';store.put('project',p)
        with self.assertRaises(ValueError):scenes.submit('fixture',self.body)

    def test_restart_and_cancellation(self):
        e=scenes.submit('fixture',self.body)
        with patch('scene_exports.threading.Thread'):scenes.start()
        self.assertEqual(store.get('export',e['id'])['status'],'interrupted')
        e=scenes.submit('fixture',self.body);scenes.stop(e['id']);scenes.update(e['id'],status='completed',native_url='fake.c4d')
        self.assertEqual(store.get('export',e['id'])['status'],'cancelled')

    def complete(self,item):
        folder=self.root/'fixture/exports'/item['id'];folder.mkdir(parents=True,exist_ok=True)
        archive=folder/'scene.zip';archive.write_bytes(b'archive fixture, no conversion executed')
        native=folder/('scene.'+item['format']);native.write_bytes(b'native fixture, no conversion executed')
        item={**item,'status':'completed','download_url':scenes.file_url(archive),'native_url':scenes.file_url(native)}
        store.put('export',item);return item

    def test_new_records_expose_exact_source_urls_in_public_read(self):
        from app import app
        for fmt in ('blend','c4d'):
            with self.subTest(format=fmt):
                record=scenes.submit('fixture',{**self.body,'format':fmt,
                    'source_png_url':self.output['png_url'],'source_blend_url':self.output['blend_url'],
                    'source_sha256':self.output['blend_sha256']})
                self.assertEqual(record['source_png_url'],self.output['png_url'])
                self.assertEqual(record['source_blend_url'],self.output['blend_url'])
                self.assertEqual(record['source_sha256'],self.output['blend_sha256'])
                response=app.test_client().get('/api/exports/'+record['id'])
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.get_json()['source_png_url'],self.output['png_url'])
                self.assertEqual(response.get_json()['source_blend_url'],self.output['blend_url'])

    def test_matching_legacy_records_backfill_source_urls_before_return(self):
        for fmt in ('blend','c4d'):
            body={**self.body,'format':fmt};record=scenes.submit('fixture',body)
            queued=scenes.QUEUE.qsize()
            for status in ('queued','running','completed'):
                with self.subTest(format=fmt,status=status):
                    if status=='completed':record=self.complete(record)
                    record['status']=status
                    record.pop('source_png_url',None);record.pop('source_blend_url',None);store.put('export',record)
                    result=scenes.submit('fixture',body)
                    self.assertEqual(result['id'],record['id'])
                    self.assertEqual(result['source_png_url'],self.output['png_url'])
                    self.assertEqual(result['source_blend_url'],self.output['blend_url'])
                    saved=store.get('export',record['id'])
                    self.assertEqual(saved['source_png_url'],self.output['png_url'])
                    self.assertEqual(saved['source_blend_url'],self.output['blend_url'])
                    self.assertEqual(scenes.QUEUE.qsize(),queued)

    def test_completed_zip_without_valid_native_is_not_reused(self):
        for fmt in ('blend','c4d'):
            body={**self.body,'format':fmt}
            for invalid in ('missing','wrong_extension','outside_project'):
                with self.subTest(format=fmt,native=invalid):
                    record=self.complete(scenes.submit('fixture',body))
                    native=self.root/record['native_url'][7:]
                    if invalid=='missing':native.unlink()
                    else:
                        bad=native.with_suffix('.txt') if invalid=='wrong_extension' else self.root/'elsewhere'/native.name
                        bad.parent.mkdir(parents=True,exist_ok=True);bad.write_bytes(b'not an authorized native scene')
                        record['native_url']=scenes.file_url(bad);store.put('export',record)
                    queued=scenes.QUEUE.qsize();result=scenes.submit('fixture',body)
                    self.assertNotEqual(result['id'],record['id'])
                    self.assertEqual(result['status'],'queued')
                    self.assertEqual(scenes.QUEUE.qsize(),queued+1)

    def test_queued_and_running_tasks_remain_idempotent_without_native_files(self):
        for fmt in ('blend','c4d'):
            body={**self.body,'format':fmt};record=scenes.submit('fixture',body)
            queued=scenes.QUEUE.qsize()
            for status in ('queued','running'):
                with self.subTest(format=fmt,status=status):
                    record.update(status=status,download_url=None,native_url=None);store.put('export',record)
                    self.assertEqual(scenes.submit('fixture',body)['id'],record['id'])
                    self.assertEqual(scenes.QUEUE.qsize(),queued)

    def test_source_identity_mismatch_cannot_enqueue_or_repair_legacy_record(self):
        record=scenes.submit('fixture',self.body)
        record.pop('source_png_url',None);record.pop('source_blend_url',None);store.put('export',record)
        queued=scenes.QUEUE.qsize()
        for key,value in [('source_png_url','/files/fixture/runs/another.png'),
                          ('source_blend_url','/files/fixture/runs/another.blend'),
                          ('source_sha256','0'*64),('record_digest','0'*64)]:
            with self.subTest(identity=key):
                project=store.get('project','fixture')
                project['outputs'][0]['blend_sha256']=value if key=='record_digest' else self.output['blend_sha256']
                store.put('project',project)
                body=self.body if key=='record_digest' else {**self.body,key:value}
                with self.assertRaises(ValueError):scenes.submit('fixture',body)
                self.assertEqual(scenes.QUEUE.qsize(),queued)
                saved=store.get('export',record['id'])
                self.assertNotIn('source_png_url',saved);self.assertNotIn('source_blend_url',saved)

if __name__=='__main__':unittest.main()

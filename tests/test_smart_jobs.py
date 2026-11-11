import queue
import tempfile
import unittest
from pathlib import Path

import engine
import studio_store as store
from planner import default_plan


class StrictPreviewJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.original=store.DATA;self.old_queue=engine.QUEUE
        store.DATA=Path(self.tmp.name);engine.QUEUE=queue.Queue()
        with store.connection() as conn:
            conn.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project',{'id':'fixture','name':'fixture','version':2,'analysis':{'objects':[]},
                             'plan':default_plan(),'outputs':[]})

    def tearDown(self):
        store.DATA=self.original;engine.QUEUE=self.old_queue;self.tmp.cleanup()

    def submit(self,**kwargs):
        args={'kind':'preview','quality':'preview','shot_ids':['hero'],'request_key':'operation-preview', 'expected_version':2}
        args.update(kwargs)
        return engine.submit('fixture',**args)

    def test_strict_preview_replay_returns_only_its_original_job(self):
        first=self.submit();second=self.submit()
        self.assertEqual(first['id'],second['id']);self.assertEqual(engine.QUEUE.qsize(),1)
        project=store.get('project','fixture');project['version']=3;store.put('project',project)
        self.assertEqual(self.submit()['id'],first['id'])

    def test_reusing_key_for_different_shot_or_version_is_a_conflict(self):
        self.submit()
        for change in ({'shot_ids':['side']},{'expected_version':3},{'kind':'render','quality':'fine'}):
            with self.subTest(change=change),self.assertRaises(engine.JobConflictError):
                self.submit(**change)
        self.assertEqual(engine.QUEUE.qsize(),1)

    def test_unrelated_active_job_is_never_returned_as_new_preview_success(self):
        other=engine.submit('fixture','preview',shot_ids=['side'],request_key='someone-else')
        with self.assertRaisesRegex(engine.JobConflictError,'另一项'):
            self.submit()
        self.assertEqual(len(store.all_records('job')),1)
        self.assertEqual(store.all_records('job')[0]['id'],other['id'])

    def test_intervening_plan_change_prevents_new_job(self):
        with self.assertRaisesRegex(engine.JobConflictError,'更新'):
            self.submit(expected_version=1)
        self.assertEqual(engine.QUEUE.qsize(),0)
        self.assertEqual(store.all_records('job'),[])

    def test_invalid_version_is_rejected_before_queueing(self):
        for version in (True,0,-1,2.0,'2'):
            with self.subTest(version=version),self.assertRaises(ValueError):
                self.submit(expected_version=version)
        self.assertEqual(engine.QUEUE.qsize(),0)


if __name__=='__main__':
    unittest.main()

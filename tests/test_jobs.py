import copy
import queue
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine
import studio_store as store
from planner import default_plan

class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.original=store.DATA;self.old_queue=engine.QUEUE
        store.DATA=Path(self.tmp.name);engine.QUEUE=queue.Queue()
        with store.connection() as c:c.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project',{'id':'fixture','name':'fixture','version':1,'analysis':{'objects':[]},'plan':default_plan(),'outputs':[]})

    def tearDown(self):
        store.DATA=self.original;engine.QUEUE=self.old_queue;self.tmp.cleanup()

    def test_duplicate_submission_and_immutable_snapshot(self):
        first=engine.submit('fixture','preview',request_key='one')
        second=engine.submit('fixture','preview',request_key='one')
        self.assertEqual(first['id'],second['id'])
        p=store.get('project','fixture');p['plan']['margin']=.3;store.put('project',p)
        self.assertEqual(store.get('job',first['id'])['plan']['margin'],.12)
        self.assertEqual(engine.QUEUE.qsize(),1)

    def test_cancel_is_terminal_even_when_progress_arrives(self):
        first=engine.submit('fixture','preview')
        engine.cancel(first['id'])
        engine.update_job(first['id'],status='completed',progress=100)
        self.assertEqual(store.get('job',first['id'])['status'],'cancelled')

    def test_restart_marks_interrupted_and_preserves_completed(self):
        first=engine.submit('fixture','preview')
        engine.update_job(first['id'],status='running',completed=['hero'])
        with patch('engine.threading.Thread') as thread: engine.start()
        j=store.get('job',first['id'])
        self.assertEqual(j['status'],'interrupted')
        self.assertEqual(j['completed'],['hero'])

    def test_retry_clears_attempt_local_metadata(self):
        from app import app
        j=engine.submit('fixture','preview')
        engine.update_job(j['id'],status='interrupted',completed=['hero'],reused=['hero'],finished_at=1,sample_progress=99)
        with patch.object(engine,'require_runtime'):
            response=app.test_client().post('/api/jobs/'+j['id']+'/retry')
        self.assertEqual(response.status_code,202)
        new=response.get_json()
        self.assertEqual(new['completed'],[]);self.assertEqual(new['reused'],[])
        self.assertNotIn('finished_at',new);self.assertNotIn('sample_progress',new)

if __name__=='__main__':unittest.main()

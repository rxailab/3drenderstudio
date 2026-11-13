import copy
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import planner
from PIL import Image


class AutomaticThumbnailTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.data=Path(temp.name);(self.data/'fixture').mkdir()
        for name in ('hero.png','existing.png'):Image.new('RGB',(4,4)).save(self.data/'fixture'/name)
        self.project={'id':'fixture','version':3,'status':'ready','analysis':{'objects':[]},
                      'plan':planner.default_plan(),'outputs':[{'shot_id':'hero','version':3,'png_url':'/files/fixture/hero.png'},
                      {'shot_id':'campaign','version':3,'png_url':'/files/fixture/existing.png','quality':'standard'}]}
        self.jobs={}
        self.patches=[patch.object(engine,'DATA',self.data),patch.object(engine,'get',side_effect=self.get),patch.object(engine,'put',side_effect=self.put),
                      patch.object(engine,'all_records',side_effect=lambda k:list(self.jobs.values()) if k=='job' else []),
                      patch.object(engine,'QUEUE',queue.Queue()),patch.object(engine,'require_render_runtime'),
                      patch.object(engine,'require_runtime')]
        for item in self.patches:item.start();self.addCleanup(item.stop)
    def get(self,kind,ident):return copy.deepcopy(self.project if kind=='project' else self.jobs[ident])
    def put(self,kind,value):
        if kind=='job':self.jobs[value['id']]=copy.deepcopy(value)
    def test_existing_project_schedules_only_missing_once(self):
        job=engine.schedule_thumbnails('fixture',3)
        self.assertEqual(job['origin'],'auto_thumbnails');self.assertEqual(job['quality'],'thumbnail')
        self.assertNotIn('hero',job['shot_ids']);self.assertNotIn('campaign',job['shot_ids'])
        self.assertEqual(engine.schedule_thumbnails('fixture',3)['id'],job['id'])
    def test_user_request_preempts_background_and_pending_work_can_resume(self):
        job=engine.schedule_thumbnails('fixture',3,polish_after_job_id='original-hero')
        user=engine.submit('fixture','preview','preview',['hero'],expected_version=3)
        self.assertEqual(self.jobs[job['id']]['status'],'cancelled');self.assertTrue(self.jobs[job['id']]['yielded'])
        self.jobs[user['id']]['status']='completed'
        done=job['shot_ids'][0];Image.new('RGB',(4,4)).save(self.data/'fixture'/'done.png')
        self.project['outputs'].append({'shot_id':done,'version':3,'quality':'thumbnail','png_url':'/files/fixture/done.png'})
        resumed=engine.schedule_thumbnails('fixture',3)
        self.assertNotIn(done,resumed['shot_ids'])
        self.assertEqual(resumed['shot_ids'],job['shot_ids'][1:])
        self.assertEqual(resumed['polish_after_job_id'],'original-hero')
    def test_stale_version_and_failed_job_do_not_repeat(self):
        with self.assertRaises(engine.JobConflictError):engine.schedule_thumbnails('fixture',2)
        job=engine.schedule_thumbnails('fixture',3);self.jobs[job['id']]['status']='failed'
        self.assertIsNone(engine.schedule_thumbnails('fixture',3))
    def test_does_not_start_without_a_real_hero(self):
        self.project['outputs']=[]
        self.assertIsNone(engine.schedule_thumbnails('fixture',3))

    def test_missing_image_file_does_not_count_as_completed(self):
        (self.data/'fixture'/'existing.png').unlink()
        job=engine.schedule_thumbnails('fixture',3)
        self.assertIn('campaign',job['shot_ids'])

if __name__=='__main__':unittest.main()

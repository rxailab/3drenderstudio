"""The first white hero render is a persisted server-side upload continuation.

All storage is temporary, Blender execution is replaced, and no image quality
claim is made by these lifecycle tests.
"""
import copy
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import studio_store as store
from planner import default_plan


class InitialPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        for module in (store,engine):
            mocked=patch.object(module,'DATA',self.data);mocked.start();self.addCleanup(mocked.stop)
        for name,value in [('QUEUE',queue.Queue()),('RUNNING_JOBS',set()),('PROCESSES',{})]:
            mocked=patch.object(engine,name,value);mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'require_runtime');self.runtime=mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        plan=default_plan();plan['visual_review']=False
        self.project={'id':'fixture','slug':'fixture','name':'Fixture','model_path':str(self.data/'fixture/model.obj'),
                      'version':1,'history':[],'status':'analyzing','analysis':None,'plan':plan,'outputs':[],
                      'metadata':{},'initial_preview':{'status':'pending'}}
        store.put('project',copy.deepcopy(self.project))
        self.analysis={'objects':[{'name':'Body','type':'MESH'},{'name':'Handle','type':'MESH'}],
                       'features':{'fixture_feature':True},'materials':[],'warnings':['Fixture warning']}

    def analysis_job(self):
        job=engine.submit('fixture','analyze')
        self.assertEqual(engine.QUEUE.get_nowait(),job['id']);engine.QUEUE.task_done()
        return job

    def run_analysis(self,job=None):
        job=job or self.analysis_job()
        with patch.object(engine,'execute',return_value=copy.deepcopy(self.analysis)):
            engine.run_job(job['id'])
        return job

    def preview_jobs(self):
        return [job for job in store.all_records('job') if job['kind']=='preview']

    def prepared_gap(self):
        job=self.analysis_job()
        engine.update_job(job['id'],status='completed')
        project=store.get('project','fixture');project.update(analysis=copy.deepcopy(self.analysis),status='ready')
        store.put('project',project)
        return job

    def test_analysis_automatically_queues_one_enriched_white_hero_preview(self):
        analysis=self.run_analysis()
        jobs=self.preview_jobs();self.assertEqual(len(jobs),1)
        preview=jobs[0];project=store.get('project','fixture')
        self.assertEqual(store.get('job',analysis['id'])['status'],'completed')
        self.assertEqual(project['status'],'ready')
        self.assertEqual(preview['shot_ids'],['hero']);self.assertEqual(preview['quality'],'preview')
        self.assertEqual(preview['plan']['shots'][0]['style'],'white')
        self.assertEqual(preview['plan']['model_features'],self.analysis['features'])
        self.assertEqual(preview['plan']['shots'][-1]['target_object'],'Handle')
        self.assertEqual(preview['plan'],project['plan'])
        self.assertEqual(preview['version'],project['version'])
        self.assertEqual(preview['origin'],'initial_preview')
        self.assertEqual(preview['analysis_job_id'],analysis['id'])
        self.assertEqual(project['initial_preview']['job_id'],preview['id'])
        self.assertEqual(project['initial_preview']['status'],'queued')
        self.assertEqual(engine.QUEUE.qsize(),1)

    def test_global_dark_style_does_not_change_the_white_hero_or_render_other_shots(self):
        project=store.get('project','fixture');project['plan']['style']='dark';store.put('project',project)
        self.run_analysis();job=self.preview_jobs()[0]
        self.assertEqual(job['plan']['style'],'dark')
        self.assertEqual(job['plan']['shots'][0]['style'],'white')
        self.assertEqual(job['shot_ids'],['hero'])
        self.assertFalse(any(item['kind']=='render' for item in store.all_records('job')))

    def test_successful_callback_is_idempotent_after_later_plan_changes(self):
        analysis=self.run_analysis();original=copy.deepcopy(self.preview_jobs()[0])
        project=store.get('project','fixture');project['version']=2;project['plan']['margin']=.3;store.put('project',project)
        for _ in range(2):
            result=engine.schedule_initial_preview('fixture',analysis['id'])
            self.assertEqual(result['id'],original['id'])
        self.assertEqual(len(self.preview_jobs()),1);self.assertEqual(engine.QUEUE.qsize(),1)
        self.assertEqual(store.get('job',original['id'])['plan'],original['plan'])
        self.assertEqual(store.get('project','fixture')['version'],2)

    def test_legacy_projects_do_not_start_an_unrequested_initial_preview(self):
        project=store.get('project','fixture');project.pop('initial_preview');store.put('project',project)
        self.run_analysis()
        self.assertEqual(self.preview_jobs(),[]);self.assertEqual(engine.QUEUE.qsize(),0)

    def test_failed_analysis_never_queues_a_preview(self):
        job=self.analysis_job()
        with patch.object(engine,'execute',side_effect=RuntimeError('Invalid fixture model')):
            engine.run_job(job['id'])
        self.assertEqual(store.get('job',job['id'])['status'],'failed')
        self.assertEqual(store.get('project','fixture')['status'],'failed')
        self.assertEqual(self.preview_jobs(),[])
        self.assertEqual(engine.RUNNING_JOBS,set())

    def test_cancellation_after_execute_returns_cannot_commit_analysis_or_queue_preview(self):
        job=self.analysis_job()
        def just_cancelled(*_args):
            self.assertIn(job['id'],engine.RUNNING_JOBS)
            engine.cancel(job['id'])
            return copy.deepcopy(self.analysis)
        with patch.object(engine,'execute',side_effect=just_cancelled):
            engine.run_job(job['id'])
        project=store.get('project','fixture')
        self.assertIsNone(project['analysis']);self.assertEqual(project['status'],'cancelled')
        self.assertEqual(store.get('job',job['id'])['status'],'cancelled')
        self.assertEqual(self.preview_jobs(),[]);self.assertEqual(engine.RUNNING_JOBS,set())

    def test_analysis_retry_after_failure_still_generates_the_initial_preview_once(self):
        original=self.analysis_job()
        with patch.object(engine,'execute',side_effect=RuntimeError('Temporary fixture failure')):
            engine.run_job(original['id'])
        retry=engine.submit('fixture','analyze',retry_of=original['id'])
        self.assertEqual(engine.QUEUE.get_nowait(),retry['id']);engine.QUEUE.task_done()
        self.run_analysis(retry)
        self.assertEqual(len(self.preview_jobs()),1)
        self.assertEqual(self.preview_jobs()[0]['analysis_job_id'],retry['id'])

    def test_scheduling_runtime_failure_preserves_successful_analysis_and_explains_retry(self):
        self.runtime.side_effect=ValueError('Fixture renderer unavailable')
        job=self.run_analysis();project=store.get('project','fixture')
        self.assertEqual(project['status'],'ready');self.assertIsNotNone(project['analysis'])
        self.assertEqual(store.get('job',job['id'])['status'],'completed')
        self.assertEqual(project['initial_preview']['status'],'failed')
        self.assertIn('Fixture renderer unavailable',project['initial_preview']['error'])
        self.assertIn('白底主图未启动',store.get('job',job['id'])['initial_preview_notice'])
        self.assertEqual(self.preview_jobs(),[]);self.assertEqual(engine.QUEUE.qsize(),0)

    def test_startup_recovers_only_the_completed_analysis_scheduling_gap(self):
        analysis=self.prepared_gap()
        with patch('engine.threading.Thread'):
            engine.start();engine.schedule_initial_preview('fixture',analysis['id'])
        self.assertEqual(len(self.preview_jobs()),1);self.assertEqual(engine.QUEUE.qsize(),1)
        self.assertEqual(self.preview_jobs()[0]['status'],'queued')

    def test_startup_does_not_automatically_requeue_an_existing_initial_preview(self):
        self.run_analysis();preview=self.preview_jobs()[0]
        engine.QUEUE=queue.Queue()
        with patch('engine.threading.Thread'):
            engine.start()
        self.assertEqual(engine.QUEUE.qsize(),0);self.assertEqual(len(self.preview_jobs()),1)
        self.assertEqual(store.get('job',preview['id'])['status'],'interrupted')
        self.assertEqual(store.get('project','fixture')['initial_preview']['status'],'interrupted')

    def test_restart_reconciles_a_saved_job_before_its_project_marker_was_written(self):
        self.prepared_gap()
        preview=engine.submit('fixture','preview','preview',['hero'],request_key='initial-white-hero:fixture',expected_version=1)
        engine.QUEUE=queue.Queue()
        with patch('engine.threading.Thread'):
            engine.start()
        self.assertEqual(engine.QUEUE.qsize(),0);self.assertEqual(len(self.preview_jobs()),1)
        project=store.get('project','fixture')
        self.assertEqual(project['initial_preview']['job_id'],preview['id'])
        self.assertEqual(project['initial_preview']['status'],'interrupted')
        self.assertEqual(store.get('job',preview['id'])['origin'],'initial_preview')

    def test_startup_with_missing_runtime_does_not_queue_a_known_unavailable_job(self):
        self.prepared_gap();self.runtime.side_effect=ValueError('Missing fixture worker')
        with patch('engine.threading.Thread'):
            engine.start()
        self.assertEqual(self.preview_jobs(),[])
        self.assertEqual(store.get('project','fixture')['initial_preview']['status'],'failed')

    def test_worker_occupancy_covers_analysis_completion_and_preview_scheduling(self):
        job=self.analysis_job();real_schedule=engine.schedule_initial_preview
        def schedule(*args,**kwargs):
            self.assertIn(job['id'],engine.RUNNING_JOBS)
            self.assertEqual(store.get('job',job['id'])['status'],'completed')
            return real_schedule(*args,**kwargs)
        with patch.object(engine,'schedule_initial_preview',side_effect=schedule):
            self.run_analysis(job)
        self.assertEqual(engine.RUNNING_JOBS,set())

    def test_initial_render_completion_updates_marker_and_keeps_tail_callbacks_occupied(self):
        self.run_analysis();job=self.preview_jobs()[0]
        record={'shot_id':'hero','shot_label':'白底主图','quality':'preview','version':1,'job_id':job['id'],
                'png_url':'/files/fixture/hero.png','jpg_url':'/files/fixture/hero.jpg'}
        def execute(*_args):
            self.assertEqual(store.get('project','fixture')['initial_preview']['status'],'running')
            self.assertIn(job['id'],engine.RUNNING_JOBS)
            return {}
        def tail(*_args):
            self.assertIn(job['id'],engine.RUNNING_JOBS)
            self.assertEqual(store.get('job',job['id'])['status'],'completed')
        with patch.object(engine,'execute',side_effect=execute),patch.object(engine,'output_record',return_value=record), \
             patch('material_agent.schedule_after_preview',side_effect=tail),patch('material_agent.finish_failed_preview',side_effect=tail):
            engine.run_job(job['id'])
        self.assertEqual(store.get('project','fixture')['initial_preview']['status'],'completed')
        self.assertEqual(engine.RUNNING_JOBS,set())

    def test_failed_and_cancelled_initial_jobs_are_not_silently_replaced(self):
        self.run_analysis();job=self.preview_jobs()[0]
        with patch.object(engine,'execute',side_effect=RuntimeError('Fixture render failed')),patch('material_agent.finish_failed_preview'):
            engine.run_job(job['id'])
        self.assertEqual(store.get('project','fixture')['initial_preview']['status'],'failed')
        self.assertEqual(engine.schedule_initial_preview('fixture')['id'],job['id'])
        self.assertEqual(len(self.preview_jobs()),1)
        retry={**store.get('job',job['id']),'id':'retry','retry_of':job['id'],'status':'queued','error':None}
        store.put('job',retry);engine.cancel(retry['id'])
        state=store.get('project','fixture')['initial_preview']
        self.assertEqual(state['job_id'],'retry');self.assertEqual(state['status'],'cancelled')
        self.assertEqual(engine.schedule_initial_preview('fixture')['id'],'retry')
        self.assertEqual(len(self.preview_jobs()),2)

    def test_missing_white_hero_is_reported_without_guessing_another_shot(self):
        project=store.get('project','fixture');project['plan']['shots'][0]['style']='dark';store.put('project',project)
        self.run_analysis()
        self.assertEqual(self.preview_jobs(),[])
        self.assertIn('白底主图镜头',store.get('project','fixture')['initial_preview']['error'])

    def test_deleted_queue_entry_is_consumed_without_breaking_future_jobs(self):
        engine.run_job('deleted-job')
        self.run_analysis()
        self.assertEqual(len(self.preview_jobs()),1);self.assertEqual(engine.RUNNING_JOBS,set())


if __name__=='__main__': unittest.main()

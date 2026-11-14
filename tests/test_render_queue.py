"""The render queue: work that arrives while a render runs waits its turn instead of being refused or thrown away.

No Blender is run: jobs are records whose status the tests move along, and the
scene is built by stand-ins. What is checked is the order of things and what
each piece of work may change.
"""
import copy
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import engine
import scene_imagegen
import scene_props
import scene_stylist
import studio_store as store
from test_scene_images import MODELS, TEXTURES, IDENTITY, project


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for module in (store, engine, scene_props, scene_stylist, scene_imagegen):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        for name, value in [('QUEUE', queue.Queue()), ('RUNNING_JOBS', set()), ('PROCESSES', {})]:
            mocked = patch.object(engine, name, value); mocked.start(); self.addCleanup(mocked.stop)
        for target, name, value in ((engine, 'require_runtime', None), (engine, 'blender_path', '/usr/bin/blender'), (scene_stylist, '_config', None)):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_stylist, 'QUEUE_POLL', .01); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project', project())
        scene_props._MEMORY.clear(); self.addCleanup(scene_props._MEMORY.clear)
        for name, effect in (('catalogue', lambda kind='models', refresh=False: MODELS if kind == 'models' else TEXTURES),
                             ('fetch_model', self.fetch_model), ('fetch_surface', lambda *a, **k: (_ for _ in ()).throw(scene_props.PropError('offline')))):
            mocked = patch.object(scene_props, name, side_effect=effect); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_stylist, '_layout', side_effect=self.solve); mocked.start(); self.addCleanup(mocked.stop)

    def fetch_model(self, asset_id, folder, resolution='1k', cancelled=None):
        target = Path(folder) / asset_id; target.mkdir(parents=True, exist_ok=True)
        (target / (asset_id + '.gltf')).write_text('{}', encoding='utf-8')
        return {**scene_props.describe(asset_id, MODELS[asset_id]), 'file': str(target / (asset_id + '.gltf'))}

    def solve(self, project_record, plan, shot, props, units, seed, keywords, folder, elevation_open, wall_distance_m=.6):
        rows = [{'id': prop['id'], 'placed': True, 'zone_used': prop['zone'], 'matrix': list(IDENTITY), 'size_mm': [10, 10, 10]} for prop in props]
        return {'success': True, 'props': rows, 'open': None, 'camera': {'camera_distance': 6.4, 'target_offset': [0, 0, 0], 'elevation': 24.0}}

    def job(self, kind='preview', quality='preview', status='running', origin=None, created=1.0):
        record = {'id': 'job-%s-%s-%s' % (kind, status, created), 'project_id': 'fixture', 'kind': kind, 'quality': quality, 'status': status,
                  'shot_ids': ['hero'], 'version': store.get('project', 'fixture')['version'], 'created_at': created, 'completed': []}
        if origin: record['origin'] = origin
        store.put('job', record)
        return record

    def finish(self, record, status='completed'):
        current = store.get('job', record['id']); current['status'] = status; store.put('job', current)


class EngineQueueTests(Fixture):
    def test_a_request_made_while_a_job_runs_waits_behind_it(self):
        running = self.job()
        with self.assertRaises(engine.JobConflictError):                 # a strict client is still told, as before
            engine.submit('fixture', 'render', 'standard', ['hero'], expected_version=1)
        queued = engine.submit('fixture', 'render', 'standard', ['hero'], expected_version=1, queue=True)
        self.assertEqual((queued['status'], queued['kind']), ('queued', 'render'))
        self.assertEqual(engine.QUEUE.get_nowait(), queued['id'], 'the worker takes it after the running one')
        again = engine.submit('fixture', 'render', 'standard', ['hero'], expected_version=1, queue=True)
        self.assertEqual(again['id'], queued['id'], 'the same work is not queued twice')
        other = engine.submit('fixture', 'preview', 'preview', ['hero'], expected_version=1, queue=True)
        self.assertNotEqual(other['id'], queued['id'])
        self.assertEqual([row['id'] for row in engine.queue_view('fixture')], [running['id'], queued['id'], other['id']])
        self.assertEqual([row['position'] for row in engine.queue_view('fixture')], [0, 1, 2])

    def test_a_version_that_moved_on_is_still_refused_when_queueing(self):
        self.job()
        with self.assertRaisesRegex(engine.JobConflictError, '方案已在预览提交前更新'):
            engine.submit('fixture', 'preview', 'preview', ['hero'], expected_version=9, queue=True)

    def test_background_work_steps_aside_and_never_counts(self):
        polish = self.job('render', 'showcase', 'running', engine.POLISH_ORIGIN)
        self.assertEqual(engine.active_jobs('fixture'), []); self.assertEqual(engine.plan_busy('fixture'), 0)
        engine.submit('fixture', 'preview', 'preview', ['hero'], expected_version=1, queue=True)
        self.assertEqual(store.get('job', polish['id'])['status'], 'cancelled', 'a real request takes the renderer from the background pass')

    def test_only_work_that_may_change_the_plan_holds_a_plan_change_back(self):
        final = self.job('render', 'standard', 'running')
        self.assertEqual(engine.plan_busy('fixture'), 0, 'a 4K render only adds pictures')
        preview = self.job('preview', 'preview', 'queued', created=2.0)
        self.assertEqual(engine.plan_busy('fixture'), 1, 'a preview may commit automatic corrections when it ends')
        self.finish(preview); self.finish(final)
        self.assertEqual(engine.plan_busy('fixture'), 0)
        store.put('material_task', {'id': 'm1', 'project_id': 'fixture', 'status': 'rendering'})
        self.assertEqual(engine.plan_busy('fixture'), 1, 'a material check in progress counts too')

    def test_the_job_route_queues_on_request_and_checks_the_flag(self):
        import app as application
        client = application.app.test_client()
        self.job()
        refused = client.post('/api/projects/fixture/jobs', json={'kind': 'preview', 'shot_ids': ['hero'], 'expected_version': 1})
        self.assertEqual(refused.status_code, 409)
        queued = client.post('/api/projects/fixture/jobs', json={'kind': 'preview', 'shot_ids': ['hero'], 'expected_version': 1, 'queue': True})
        self.assertEqual((queued.status_code, queued.get_json()['status']), (202, 'queued'))
        self.assertEqual(client.post('/api/projects/fixture/jobs', json={'kind': 'preview', 'queue': 'yes'}).status_code, 400)
        view = client.get('/api/projects/fixture').get_json()
        self.assertEqual([row['status'] for row in view['queue']], ['running', 'queued'])


class SceneQueueTests(Fixture):
    def submit(self, **body):
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            task = scene_stylist.submit('fixture', {'expected_version': store.get('project', 'fixture')['version'], **body})
        return task, thread.call_args.kwargs['args'][0]

    def test_a_scene_asked_for_during_a_preview_is_built_then_saved_when_the_preview_ends(self):
        preview = self.job('preview', 'preview', 'running')
        task, ident = self.submit(seed=3)                                   # no longer refused
        worker = threading.Thread(target=scene_stylist._run, args=(ident,)); worker.start()
        deadline = time.time() + 5
        while 'queued_behind' not in store.get(scene_stylist.KIND, task['id']) and time.time() < deadline: time.sleep(.01)
        waiting = store.get(scene_stylist.KIND, task['id'])
        self.assertEqual((waiting['status'], waiting['queued_behind']), ('running', 1))
        self.assertIn('排队等待保存', waiting['stage'])
        self.assertEqual(store.get('project', 'fixture')['version'], 1, 'nothing is saved while the preview may still correct the plan')
        self.finish(preview); worker.join(5)
        done = store.get(scene_stylist.KIND, task['id'])
        self.assertEqual(done['status'], 'completed', done.get('error')); self.assertGreaterEqual(done['waited_seconds'], 0)
        saved = store.get('project', 'fixture')
        self.assertEqual(saved['version'], 2); self.assertIn('scene_set', saved['plan'])
        self.assertEqual(store.get('job', done['job_id'])['shot_ids'], ['scene'])

    def test_a_final_render_does_not_hold_the_scene_back_and_its_preview_queues_behind_it(self):
        final = self.job('render', 'standard', 'running')
        task, ident = self.submit(seed=4)
        scene_stylist._run(ident)
        done = store.get(scene_stylist.KIND, task['id'])
        self.assertEqual((done['status'], done['stage']), ('completed', '场景已保存，预览已加入渲染队列'))
        preview = store.get('job', done['job_id'])
        self.assertEqual((preview['status'], preview['shot_ids']), ('queued', ['scene']))
        self.assertEqual(store.get('job', final['id'])['status'], 'running', 'the 4K render goes on')
        self.assertEqual([row['id'] for row in engine.queue_view('fixture')], [final['id'], preview['id']])

    def test_a_waiting_scene_can_be_cancelled_and_changes_nothing(self):
        self.job('preview', 'preview', 'running')
        task, ident = self.submit(seed=5)
        worker = threading.Thread(target=scene_stylist._run, args=(ident,)); worker.start()
        deadline = time.time() + 5
        while 'queued_behind' not in store.get(scene_stylist.KIND, task['id']) and time.time() < deadline: time.sleep(.01)
        self.assertEqual(scene_stylist.cancel('fixture', task['id'])['status'], 'cancelled')
        worker.join(5); self.assertFalse(worker.is_alive())
        self.assertEqual(store.get('project', 'fixture')['version'], 1)
        self.assertEqual(store.get(scene_stylist.KIND, task['id'])['status'], 'cancelled')

    def test_a_generated_picture_is_not_put_on_a_product_that_changed_meanwhile(self):
        base = project()['plan']
        built = copy.deepcopy(base)
        built['shots'].append({'id': 'scene', 'label': '使用场景', 'kind': 'full', 'style': 'soft', 'projection': 'perspective',
                               'azimuth': 30, 'elevation': 20, 'focal_length': 55})
        built['scene_brief'] = {'version': 1, 'shot_id': 'scene', 'segment_id': None, 'reference_id': None, 'source': 'rules',
                                'method': 'generate', 'reason': '', 'summary': '', 'people': True, 'complex': False}
        lighter = copy.deepcopy(base); lighter['exposure'] = .3
        plan, why = scene_stylist.rebase(lighter, built, base, 'generate')
        self.assertIsNone(why); self.assertEqual((plan['exposure'], plan['scene_brief']['method']), (.3, 'generate'))
        repainted = copy.deepcopy(base); repainted['material_overrides'] = [{'name': '红色', 'preset': 'matte_plastic', 'color': '#2255aa'}]
        plan, why = scene_stylist.rebase(repainted, built, base, 'generate')
        self.assertIsNone(plan); self.assertIn('材质', why)
        full = copy.deepcopy(base)
        full['shots'] += [{**base['shots'][0], 'id': 'extra%d' % index} for index in range(12 - len(base['shots']))]
        plan, why = scene_stylist.rebase(full, built, base, 'generate')
        self.assertIsNone(plan); self.assertIn('镜头数量', why)


if __name__ == '__main__':
    unittest.main()

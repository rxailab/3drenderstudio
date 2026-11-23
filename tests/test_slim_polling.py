"""The 2-second UI poll: a slim library list and ETag/304 answers while nothing changed.

Every project, job and database in these tests is temporary.
"""
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as web
import engine
import instruction_service
import material_agent
import project_lifecycle as lifecycle
import scene_exports
import studio_store as store
from planner import default_plan


class SlimPollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        for module in (store, web, engine, scene_exports, material_agent, instruction_service):
            self.replace(module, 'DATA', self.data)
        self.replace(lifecycle, 'OPERATIONS', {})
        for module in (engine, scene_exports):
            self.replace(module, 'PROCESSES', {})
            self.replace(module, 'QUEUE', queue.Queue())
        with store.connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        outputs = [{'job_id': 'j%d' % index, 'shot_id': shot, 'quality': quality, 'version': 1, 'created_at': index,
                    'png_url': '/files/alpha/%d.png' % index, 'jpg_url': '/files/alpha/%d.jpg' % index,
                    'checks': {'large': 'x' * 2000}, 'metrics': {'large': list(range(200))}}
                   for index, (shot, quality) in enumerate((('hero', 'preview'), ('detail', 'thumbnail'), ('hero', 'standard')))]
        store.put('project', {'id': 'alpha', 'name': 'Alpha', 'slug': 'alpha', 'status': 'ready', 'version': 3, 'created_at': 5.0,
                              'analysis': {'objects': [{'name': 'part'}] * 50}, 'plan': default_plan(), 'history': [{'version': 2}],
                              'outputs': outputs, 'initial_preview': {'status': 'completed', 'job_id': 'j0'}, 'metadata': {'note': 'n'}})
        store.put('project', {'id': 'beta', 'name': 'Beta', 'slug': 'beta', 'status': 'failed', 'version': 1, 'created_at': 6.0,
                              'analysis': None, 'plan': default_plan(), 'history': [], 'outputs': []})
        self.client = web.app.test_client()

    def replace(self, target, name, value, **kwargs):
        mocked = patch.object(target, name, value, **kwargs)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_summary_list_carries_only_what_the_library_draws(self):
        store.put('job', {'id': 'r', 'project_id': 'alpha', 'status': 'running', 'kind': 'render', 'quality': 'standard',
                          'progress': 40, 'log_tail': ['private'], 'plan': {'private': True}})
        full = self.client.get('/api/projects')
        slim = self.client.get('/api/projects?fields=summary')
        self.assertEqual(slim.status_code, 200)
        rows = {item['id']: item for item in slim.get_json()}
        alpha = rows['alpha']
        self.assertEqual(set(alpha), {'id', 'name', 'status', 'version', 'created_at', 'analyzed', 'initial_preview',
                                      'shot_count', 'outputs', 'active_job'})
        self.assertEqual((alpha['name'], alpha['status'], alpha['created_at'], alpha['analyzed']), ('Alpha', 'ready', 5.0, True))
        self.assertEqual(alpha['initial_preview'], {'status': 'completed'})
        self.assertEqual(alpha['shot_count'], len(default_plan()['shots']))
        # Every output is kept (the sidebar counts them all), cut to the card's image fields.
        self.assertEqual(alpha['outputs'][2], {'shot_id': 'hero', 'quality': 'standard', 'jpg_url': '/files/alpha/2.jpg', 'png_url': '/files/alpha/2.png'})
        self.assertEqual(len(alpha['outputs']), 3)
        self.assertEqual(alpha['active_job'], {'id': 'r', 'kind': 'render', 'quality': 'standard', 'status': 'running', 'progress': 40, 'origin': None})
        self.assertEqual((rows['beta']['analyzed'], rows['beta']['initial_preview'], rows['beta']['active_job']), (False, None, None))
        self.assertLess(len(slim.data) * 4, len(full.data))
        # The default list is unchanged for every other caller.
        self.assertIn('plan', full.get_json()[0])

    def test_project_answers_304_until_anything_it_shows_changes(self):
        first = self.client.get('/api/projects/alpha')
        tag = first.headers['ETag']
        self.assertEqual(first.headers['Cache-Control'], 'no-cache')
        again = self.client.get('/api/projects/alpha', headers={'If-None-Match': tag})
        self.assertEqual((again.status_code, again.data), (304, b''))
        # A running job's progress is part of the view, so a moving progress bar always refetches.
        store.put('job', {'id': 'r', 'project_id': 'alpha', 'status': 'running', 'kind': 'preview', 'quality': 'preview', 'progress': 10})
        moved = self.client.get('/api/projects/alpha', headers={'If-None-Match': tag})
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.get_json()['jobs'][0]['progress'], 10)
        store.put('job', {'id': 'r', 'project_id': 'alpha', 'status': 'running', 'kind': 'preview', 'quality': 'preview', 'progress': 11})
        later = self.client.get('/api/projects/alpha', headers={'If-None-Match': moved.headers['ETag']})
        self.assertEqual((later.status_code, later.get_json()['jobs'][0]['progress']), (200, 11))
        project = store.get('project', 'alpha')
        project['name'] = 'Renamed'
        store.put('project', project)
        renamed = self.client.get('/api/projects/alpha', headers={'If-None-Match': later.headers['ETag']})
        self.assertEqual((renamed.status_code, renamed.get_json()['name']), (200, 'Renamed'))
        # Without a tag the full body comes as before.
        self.assertEqual(self.client.get('/api/projects/alpha').get_json(), renamed.get_json())

    def test_summary_list_answers_304_until_a_card_changes(self):
        first = self.client.get('/api/projects?fields=summary')
        tag = first.headers['ETag']
        self.assertEqual(self.client.get('/api/projects?fields=summary', headers={'If-None-Match': tag}).status_code, 304)
        store.put('job', {'id': 'r', 'project_id': 'beta', 'status': 'running', 'kind': 'analyze', 'progress': 3})
        changed = self.client.get('/api/projects?fields=summary', headers={'If-None-Match': tag})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual({item['id']: item['active_job'] for item in changed.get_json()}['beta']['progress'], 3)

    def test_missing_project_is_still_an_error_not_a_304(self):
        tag = self.client.get('/api/projects/alpha').headers['ETag']
        self.assertEqual(self.client.get('/api/projects/missing', headers={'If-None-Match': tag}).status_code, 404)


if __name__ == '__main__':
    unittest.main()

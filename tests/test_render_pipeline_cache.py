"""A lighting upgrade invalidates preset image reuse, but never native scenes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import engine
import studio_store as store
from planner import default_plan


class RenderPipelineCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(store, 'DATA', self.root), patch.object(engine, 'DATA', self.root),
                        patch.object(engine, 'camera_matches_result', return_value=True),
                        patch.object(engine, 'schedule_thumbnails', return_value=None),
                        patch.object(engine, 'schedule_polish', return_value=None),
                        patch('material_agent.schedule_after_preview', return_value=None),
                        patch('material_agent.finish_failed_preview', return_value=None)]
        for item in self.patches:
            item.start()
        with store.connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.folder = self.root / 'fixture' / 'old'
        self.folder.mkdir(parents=True)
        Image.new('RGB', (8, 8), 'grey').save(self.folder / 'image.png')
        Image.new('RGB', (8, 8), 'grey').save(self.folder / 'image.jpg')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def run_cached(self, kind, quality, pipeline, checkpoint=False):
        plan = default_plan()
        plan['visual_review'] = False
        if checkpoint:
            plan['scene_checkpoints'] = {'hero': {'source_job_id': 'saved-scene'}}
        record = {'version': 1, 'quality': quality, 'shot_id': 'hero', 'job_id': 'old',
                  'png_url': '/files/fixture/old/image.png', 'jpg_url': '/files/fixture/old/image.jpg',
                  'result_url': '/files/fixture/old/result.json'}
        self.folder.joinpath('result.json').write_text(json.dumps({'render_pipeline_version': pipeline}))
        project = {'id': 'fixture', 'slug': 'fixture', 'model_path': 'fixture.glb', 'version': 1,
                   'status': 'ready', 'analysis': {}, 'plan': plan, 'outputs': [record]}
        job = {'id': 'new', 'project_id': 'fixture', 'kind': kind, 'quality': quality, 'status': 'queued',
               'version': 1, 'plan': copy.deepcopy(plan), 'shot_ids': ['hero'], 'completed': []}
        store.put('project', project)
        store.put('job', job)
        # Stop at the actual render boundary. A requested fresh execution proves
        # that the old image was not silently presented as a new render.
        with patch.object(engine, 'execute', side_effect=RuntimeError('fresh rendering required')) as execute:
            engine._run_job(job['id'])
        self.assertEqual(store.get('project', 'fixture')['outputs'], [record])
        self.assertEqual(json.loads(self.folder.joinpath('result.json').read_text())['render_pipeline_version'], pipeline)
        return store.get('job', job['id']), execute.call_count

    def test_old_preset_lighting_is_rerendered_at_all_qualities(self):
        for kind, quality in [('preview', 'preview'), ('preview', 'thumbnail'), ('render', 'showcase'),
                              ('render', 'standard'), ('render', 'fine')]:
            with self.subTest(quality=quality):
                job, calls = self.run_cached(kind, quality, 2)
                self.assertEqual(calls, 1)
                self.assertEqual(job['status'], 'failed')
                self.assertEqual(job.get('reused', []), [])

    def test_current_preset_images_are_reused_without_a_new_render(self):
        for kind, quality in [('preview', 'preview'), ('render', 'standard')]:
            with self.subTest(quality=quality):
                job, calls = self.run_cached(kind, quality, engine.RENDER_PIPELINE_VERSION)
                self.assertEqual(calls, 0)
                self.assertEqual(job['status'], 'completed')
                self.assertEqual(job['reused'], ['hero'])

    def test_saved_scene_images_keep_their_original_pipeline(self):
        for kind, quality in [('preview', 'preview'), ('render', 'standard')]:
            with self.subTest(quality=quality):
                job, calls = self.run_cached(kind, quality, 2, checkpoint=True)
                self.assertEqual(calls, 0)
                self.assertEqual(job['status'], 'completed')
                self.assertEqual(job['reused'], ['hero'])


if __name__ == '__main__':
    unittest.main()

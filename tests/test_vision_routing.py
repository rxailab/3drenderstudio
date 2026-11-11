"""Isolated checks of vision routing and the real-preview test endpoint."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
import ai_config
import engine
import app as application
from planner import default_plan


class VisionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'enabled': True, 'provider': 'openai', 'model': 'vision-test-model',
                       'base_url': 'https://example.test/openai', 'api_key': 'test-only-key'}
        self.client = application.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def preview(self, project, name):
        path = self.root / project / 'runs' / name / 'preview.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (16, 16), (100, 100, 100)).save(path)
        return path

    def test_diagnose_uses_independent_vision_configuration_and_tags_result(self):
        path = self.preview('one', 'job')
        record = {'png_url': '/files/one/runs/job/preview.png'}
        result = {'checks': {}, 'product_samples': [], 'studio': {}}
        answer = {'issues': [], 'patch': {}, 'score': .75, 'needs_user': [],
                  'realism': {'summary': '视觉结果', 'causes': [], 'priority_actions': []}}
        with patch.object(engine, 'DATA', self.root), \
             patch.object(ai_config, 'get_vision_config', return_value=self.config), \
             patch.object(ai_config, 'get_config', side_effect=AssertionError('text config was used')), \
             patch.object(ai_config, 'record_vision_result') as status, \
             patch.object(ai_client, 'review_preview', return_value=answer) as model:
            review = engine.diagnose_render(record, result, default_plan(), {}, question='检查反光')
        self.assertEqual(review['source'], 'ai')
        self.assertEqual(review['provider'], 'openai')
        self.assertEqual(review['model'], 'vision-test-model')
        self.assertEqual(model.call_args.args[2], path)
        self.assertEqual(model.call_args.args[4], self.config)
        self.assertEqual(model.call_args.kwargs['question'], '检查反光')
        status.assert_called_once()
        self.assertTrue(status.call_args.args[1])

    def test_failed_vision_keeps_measured_rule_fallback_and_records_failure(self):
        self.preview('one', 'job')
        record = {'png_url': '/files/one/runs/job/preview.png'}
        with patch.object(engine, 'DATA', self.root), \
             patch.object(ai_config, 'get_vision_config', return_value=self.config), \
             patch.object(ai_config, 'record_vision_result') as status, \
             patch.object(ai_client, 'review_preview', side_effect=ValueError('视觉服务不可用')):
            review = engine.diagnose_render(record, {'checks': {}, 'product_samples': [], 'studio': {}}, default_plan(), {})
        self.assertEqual(review['source'], 'rules')
        self.assertTrue(review['ai_unavailable'])
        self.assertNotIn('model', review)
        self.assertTrue(any('视觉服务不可用' in item for item in review['needs_user']))
        self.assertFalse(status.call_args.args[1])

    def test_endpoint_uses_latest_real_preview_and_records_actual_success(self):
        old = self.preview('one', 'old')
        latest = self.preview('two', 'latest')
        projects = [{'id': 'one', 'outputs': [{'png_url': '/files/one/runs/old/preview.png', 'quality': 'preview', 'created_at': 1}]},
                    {'id': 'two', 'outputs': [{'png_url': '/files/two/runs/latest/preview.png', 'quality': 'preview', 'created_at': 2}]}]
        with patch.object(application, 'DATA', self.root), patch.object(application, 'all_records', return_value=projects), \
             patch.object(ai_config, 'get_vision_config', return_value=self.config), \
             patch.object(ai_config, 'public_config', return_value={'vision_config': {'status': 'verified'}}), \
             patch.object(ai_config, 'record_vision_result') as status, \
             patch.object(ai_client, 'test_vision_connection', return_value={'message': '实际响应', 'model': 'vision-test-model'}) as model:
            response = self.client.post('/api/ai/vision/test', json={})
        self.assertTrue(response.json['success'])
        self.assertEqual(model.call_args.args, (self.config, latest))
        self.assertNotEqual(model.call_args.args[1], old)
        self.assertTrue(status.call_args.args[1])
        self.assertNotIn('test-only-key', response.get_data(as_text=True))

    def test_no_valid_preview_does_not_call_model_or_report_success(self):
        other = self.preview('other', 'private')
        projects = [{'id': 'one', 'outputs': [
            {'png_url': '/files/one/../other/runs/private/preview.png', 'quality': 'preview', 'created_at': 9},
            {'png_url': '/files/other/runs/private/preview.png', 'quality': 'preview', 'created_at': 10},
            {'png_url': '/files/one/runs/missing/preview.png', 'quality': 'preview', 'created_at': 11},
            {'png_url': '/files/other/runs/private/preview.png', 'quality': 'standard', 'created_at': 12}]}]
        with patch.object(application, 'DATA', self.root), patch.object(application, 'all_records', return_value=projects), \
             patch.object(ai_config, 'get_vision_config', return_value=self.config), \
             patch.object(ai_config, 'public_config', return_value={}), \
             patch.object(ai_config, 'record_vision_result') as status, \
             patch.object(ai_client, 'test_vision_connection') as model:
            response = self.client.post('/api/ai/vision/test', json={})
        self.assertTrue(other.is_file())
        self.assertFalse(response.json['success'])
        model.assert_not_called()
        status.assert_not_called()

    def test_endpoint_failure_is_recorded_and_never_reported_as_success(self):
        self.preview('one', 'job')
        projects = [{'id': 'one', 'outputs': [{'png_url': '/files/one/runs/job/preview.png', 'quality': 'preview', 'created_at': 1}]}]
        with patch.object(application, 'DATA', self.root), patch.object(application, 'all_records', return_value=projects), \
             patch.object(ai_config, 'get_vision_config', return_value=self.config), \
             patch.object(ai_config, 'public_config', return_value={}), \
             patch.object(ai_config, 'record_vision_result') as status, \
             patch.object(ai_client, 'test_vision_connection', side_effect=ValueError('接口响应无效')):
            response = self.client.post('/api/ai/vision/test', json={})
        self.assertFalse(response.json['success'])
        self.assertIn('接口响应无效', response.json['message'])
        self.assertFalse(status.call_args.args[1])


if __name__ == '__main__':
    unittest.main()

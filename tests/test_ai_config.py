import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import ai_config
from app import app


class AIConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patches=[patch.object(ai_config,'ENV_PATH',self.root/'.env'),patch.object(ai_config,'STATUS_PATH',self.root/'status.json'),patch.dict('os.environ',{},clear=True)]
        for item in self.patches:item.start()
        self.client=app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def test_secret_never_read_back_and_empty_preserves(self):
        body={'provider':'anthropic','base_url':ai_config.DEFAULT_BASE,'model':'claude-sonnet-4-6','api_key':'test-secret-local-only','enabled':True}
        response=self.client.put('/api/ai/settings',json=body)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('test-secret-local-only',response.get_data(as_text=True))
        self.assertTrue(response.json['key_configured'])
        self.assertEqual(response.json['status'],'configured')
        self.client.put('/api/ai/settings',json={'api_key':''})
        self.assertEqual(ai_config.get_config()['api_key'],'test-secret-local-only')
        self.assertNotIn('test-secret-local-only',self.client.get('/api/system').get_data(as_text=True))
        self.client.put('/api/ai/settings',json={'clear_key':True})
        self.assertEqual(ai_config.public_config()['status'],'missing_key')

    def test_changing_endpoint_clears_old_credential(self):
        ai_config.save_config({'api_key':'local-test-secret'})
        ai_config.save_config({'base_url':'https://another.example/anthropic'})
        self.assertFalse(ai_config.public_config()['key_configured'])

    def test_injection_and_cross_site_rejected(self):
        for change in [{'model':'m\nAI_API_KEY=bad'},{'base_url':'http://remote.example/api'},{'base_url':'https://user:pass@example.test/api'}]:
            self.assertEqual(self.client.put('/api/ai/settings',json=change).status_code,400)
        self.assertEqual(self.client.put('/api/ai/settings',json={},headers={'Origin':'https://foreign.example'}).status_code,403)

    def test_connection_is_verified_only_after_response(self):
        ai_config.save_config({'api_key':'local-secret'})
        with patch('ai_client.test_connection',return_value={'message':'mock response verified','model':'model'}) as call:
            response=self.client.post('/api/ai/test',json={})
            self.assertTrue(response.json['success'])
            self.assertEqual(response.json['settings']['status'],'verified')
            call.assert_called_once()
        ai_config.save_config({'model':'different-model'})
        self.assertEqual(ai_config.public_config()['status'],'configured')

    def test_key_missing_makes_no_network_request(self):
        with patch('ai_client.test_connection') as call:
            result=self.client.post('/api/ai/test',json={})
            self.assertFalse(result.json['success'])
            call.assert_not_called()

    def test_other_environment_settings_are_preserved(self):
        ai_config.ENV_PATH.write_text('BLENDER_PATH=D:/blender/blender.exe\n# comment\nAI_API_KEY=old\n')
        ai_config.save_config({'api_key':'new-secret'})
        text=ai_config.ENV_PATH.read_text()
        self.assertIn('BLENDER_PATH=D:/blender/blender.exe',text)
        self.assertEqual(text.count('AI_API_KEY='),1)


if __name__=='__main__':unittest.main()

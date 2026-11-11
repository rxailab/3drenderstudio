import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import ai_client
import ai_config


class VisionConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.patches=[patch.object(ai_config,'ENV_PATH',root/'.env'),patch.object(ai_config,'STATUS_PATH',root/'main.json'),patch.object(ai_config,'VISION_STATUS_PATH',root/'vision.json'),patch.dict('os.environ',{},clear=True)]
        for p in self.patches:p.start()
        ai_config.save_config({'provider':'anthropic','base_url':'https://api.hai.network/anthropic','model':'text-model','api_key':'private-main'})
        self.root=root

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_vision_is_independent_and_reuses_official_gateway_key(self):
        ai_config.save_config({'vision_provider':'openai','vision_base_url':'https://api.hai.network/openai','vision_model':'gpt-6.1-sol'})
        main=ai_config.get_config(); vision=ai_config.get_vision_config();public=ai_config.public_config()
        self.assertEqual(main['model'],'text-model'); self.assertEqual(vision['model'],'gpt-6.1-sol')
        self.assertEqual(vision['api_key'],'private-main');self.assertNotIn('private-main',json.dumps(public))
        self.assertFalse(public['vision_config']['inherits']);self.assertTrue(public['vision_config']['key_configured'])

    def test_host_and_path_guards_prevent_silent_credential_transfer(self):
        for base in ['https://example.test/openai','https://api.hai.network/other','https://api.hai.network.evil.test/openai']:
            ai_config.save_config({'vision_provider':'openai','vision_base_url':base,'vision_model':'gpt-6.1-sol'})
            self.assertEqual(ai_config.get_vision_config()['api_key'],'')

    def test_inherit_ignores_and_preserves_dedicated_secret(self):
        ai_config.save_config({'vision_provider':'openai','vision_base_url':'https://example.test/openai','vision_model':'gpt-6.1-sol','vision_api_key':'private-vision'})
        self.assertEqual(ai_config.get_vision_config()['api_key'],'private-vision')
        ai_config.save_config({'vision_provider':'','vision_base_url':'','vision_model':'','vision_enabled':''})
        vision=ai_config.get_vision_config(); self.assertTrue(vision['inherits']);self.assertEqual(vision['api_key'],'private-main')
        self.assertIn('AI_VISION_API_KEY=private-vision',ai_config.ENV_PATH.read_text(encoding='utf-8'))

    def test_vision_status_scoped_to_model(self):
        ai_config.save_config({'vision_provider':'openai','vision_base_url':'https://api.hai.network/openai','vision_model':'gpt-6.1-sol'})
        config=ai_config.get_vision_config();ai_config.record_vision_result(config,True,'ok')
        self.assertEqual(ai_config.public_config()['vision_config']['status'],'verified')
        ai_config.save_config({'vision_model':'other-model'})
        self.assertEqual(ai_config.public_config()['vision_config']['status'],'configured')

    def test_gpt61_image_payload_and_real_image_test_contract(self):
        config={'provider':'openai','base_url':'https://api.openai.com/v1','model':'gpt-6.1-sol','api_key':'secret','enabled':True}
        path=self.root/'image.png';Image.new('RGB',(10,10),'red').save(path)
        with patch('ai_client._post',return_value={'choices':[{'finish_reason':'stop','message':{'content':'{"visible":"红色方块"}'}}]}) as request:
            result=ai_client.test_vision_connection(config,path)
        body=request.call_args.args[1];self.assertEqual(body['max_completion_tokens'],4096);self.assertEqual(body['reasoning_effort'],'low')
        self.assertNotIn('max_tokens',body);self.assertEqual(body['response_format'],{'type':'json_object'})
        self.assertTrue(body['messages'][1]['content'][0]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(result['observation'],'红色方块')

    def test_legacy_blocked_vision_choice_remains_visible_and_cannot_be_verified(self):
        ai_config.save_config({'vision_provider':'openai','vision_base_url':'https://api.hai.network/openai',
                               'vision_model':'gpt-5.5-2026-04-23'})
        config=ai_config.get_vision_config(); ai_config.record_vision_result(config,True,'old test')
        visible=ai_config.public_config()['vision_config']
        self.assertEqual(visible['model'],'gpt-5.5-2026-04-23')
        self.assertEqual(visible['status'],'blocked_model')
        self.assertFalse(visible['request_allowed'])
        self.assertIn('AI 设置',visible['blocked_reason'])

if __name__=='__main__':unittest.main()

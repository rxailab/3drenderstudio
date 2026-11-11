"""Transport-level spending gate. Every network operation is replaced by a fake."""
import io
import json
import unittest
from unittest.mock import patch

import ai_client


CONFIG = {'provider': 'openai', 'base_url': 'https://api.openai.com/v1',
          'model': 'gpt-6.1-sol', 'api_key': 'fake-unit-test-key', 'enabled': True}


class RequestPolicyIntegrationTests(unittest.TestCase):
    def test_all_prohibited_aliases_are_stopped_before_building_a_network_opener(self):
        for model in ('gpt-5.5', 'gpt-5.5-2026-04-23', 'openai/gpt-5.5', 'GPT-5.5-LATEST'):
            for provider in ('openai', 'anthropic'):
                config = {**CONFIG, 'model': model, 'provider': provider}
                with self.subTest(model=model, provider=provider), patch('ai_client.urllib.request.build_opener') as opener:
                    with self.assertRaisesRegex(ValueError, '停用 GPT-5.5'):
                        ai_client._post(config, {'model': model})
                    opener.assert_not_called()

    def test_blocked_model_is_rejected_before_loading_or_encoding_user_images(self):
        config = {**CONFIG, 'model': 'gpt-5.5-2026-04-23'}
        with patch('ai_client._image') as encode, patch('ai_client.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ValueError, '停用 GPT-5.5'):
                ai_client._payload(config, 'system', {}, image_path='never-read-this-image.png')
            encode.assert_not_called(); opener.assert_not_called()

    def test_blocked_connection_probe_does_not_call_transport(self):
        with patch('ai_client._post') as send:
            with self.assertRaisesRegex(ValueError, '停用 GPT-5.5'):
                ai_client.test_connection({**CONFIG, 'model': 'gpt-5.5'})
            send.assert_not_called()

    def test_body_model_cannot_bypass_saved_choice(self):
        for model in ('gpt-5.5', 'unexpected-paid-model'):
            with self.subTest(model=model), patch('ai_client.urllib.request.build_opener') as opener:
                with self.assertRaises(ValueError):
                    ai_client._post(CONFIG, {'model': model})
                opener.assert_not_called()

    def test_gpt6_request_is_sanitized_at_the_transport_boundary(self):
        body = {'model': CONFIG['model'], 'max_tokens': 4096, 'temperature': .5,
                'messages': [{'role': 'user', 'content': 'Return JSON.'}],
                'response_format': {'type': 'json_object'}}
        fake = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
        with patch('ai_client.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(fake).encode())
            result = ai_client._post(CONFIG, body)
            request = opener.return_value.open.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent['model'], CONFIG['model'])
        self.assertEqual(sent['max_completion_tokens'], 4096)
        self.assertEqual(sent['reasoning_effort'], 'low')
        self.assertNotIn('temperature', sent); self.assertNotIn('max_tokens', sent)
        self.assertEqual(result, fake)
        self.assertEqual(body['max_tokens'], 4096)

    def test_official_gemini_and_deepseek_paths_reach_the_expected_route(self):
        cases = [
            ('https://api.deepseek.com', 'deepseek-flash', 'https://api.deepseek.com/chat/completions'),
            ('https://generativelanguage.googleapis.com/v1beta/openai', 'gemini-3.8-flash',
             'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
        ]
        for base, model, expected in cases:
            config = {**CONFIG, 'base_url': base, 'model': model}
            with self.subTest(model=model):
                self.assertEqual(ai_client.endpoint(config), expected)


if __name__ == '__main__':
    unittest.main()

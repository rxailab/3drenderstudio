"""Pure compatibility / config checks. Real .env and network are never accessed."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_config
import ai_model_policy as policy


CONFIG = {'provider': 'anthropic', 'base_url': 'https://api.anthropic.com',
          'model': 'claude-sonnet-4-6', 'api_key': 'fake-test-key', 'enabled': True}
SCHEMA = {'type': 'object', 'properties': {'roughness': {'type': 'number', 'minimum': 0, 'maximum': 1}},
          'additionalProperties': False}


class ModelPolicyTests(unittest.TestCase):
    def test_prohibited_family_is_rejected_through_any_provider_namespace(self):
        blocked = ['gpt-5.5', 'gpt-5.5-2026-04-23', 'GPT-5.5-LATEST',
                   'openai/gpt-5.5', 'models/gpt-5.5:latest', 'gpt-5-5', 'gpt_5_5',
                   'ＧＰＴ－５．５', 'gpt-5.5-pro']
        for model in blocked:
            for provider in ('openai', 'anthropic'):
                config = {**CONFIG, 'model': model, 'provider': provider}
                with self.subTest(model=model, provider=provider), self.assertRaisesRegex(ValueError, '停用 GPT-5.5'):
                    policy.assert_model_allowed(config)
        for model in ('gpt-5.4-mini', 'gpt-6.1-sol', 'claude-haiku-5-5', 'qwen3.8-flash', 'deepseek-flash'):
            self.assertFalse(policy.is_blocked_model(model))

    def test_body_cannot_override_config_to_a_prohibited_or_other_paid_model(self):
        config = {**CONFIG, 'model': 'gpt-6.1-sol'}
        with self.assertRaisesRegex(ValueError, '停用 GPT-5.5'):
            policy.assert_model_allowed(config, {'model': 'gpt-5.5'})
        with self.assertRaisesRegex(ValueError, '模型不一致'):
            policy.assert_model_allowed(config, {'model': 'another-model'})
        self.assertEqual(config['model'], 'gpt-6.1-sol')

    def test_disabled_configuration_cannot_send(self):
        with self.assertRaisesRegex(ValueError, 'AI 已关闭'):
            policy.assert_model_allowed({**CONFIG, 'enabled': False})

    def test_endpoint_does_not_add_an_extra_v1_to_official_compatible_routes(self):
        pairs = [
            ('anthropic', 'https://api.anthropic.com', 'https://api.anthropic.com/v1/messages'),
            ('anthropic', 'https://api.hai.network/anthropic', 'https://api.hai.network/anthropic/v1/messages'),
            ('openai', 'https://api.hai.network/openai', 'https://api.hai.network/openai/v1/chat/completions'),
            ('openai', 'https://api.deepseek.com', 'https://api.deepseek.com/chat/completions'),
            ('openai', 'https://api.deepseek.com/beta', 'https://api.deepseek.com/beta/chat/completions'),
            ('openai', 'https://api.deepseek.com/v1', 'https://api.deepseek.com/v1/chat/completions'),
            ('openai', 'https://generativelanguage.googleapis.com/v1beta/openai/',
             'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
            ('openai', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
             'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'),
            ('openai', 'https://api.openai.com/v1/chat/completions',
             'https://api.openai.com/v1/chat/completions'),
        ]
        for provider, base, expected in pairs:
            with self.subTest(base=base):
                self.assertEqual(policy.resolve_endpoint({**CONFIG, 'provider': provider, 'base_url': base}), expected)

    def test_endpoint_rejects_credential_or_query_in_url(self):
        for base in ('http://remote.example/v1', 'https://key@example.test/v1',
                     'https://example.test/v1?key=not-a-real-key', 'https://example.test/v1#fragment'):
            with self.subTest(base=base), self.assertRaises(ValueError):
                policy.resolve_endpoint({**CONFIG, 'base_url': base})

    def _anthropic_body(self, model):
        return {'model': model, 'max_tokens': 8192, 'system': 'Return data only.',
                'messages': [{'role': 'user', 'content': 'Make the rubber rougher.'}],
                'temperature': .2, 'top_p': .9, 'top_k': 10,
                'tools': [{'name': 'submit_patch', 'input_schema': copy.deepcopy(SCHEMA)}],
                'tool_choice': {'type': 'tool', 'name': 'submit_patch'}}

    def test_new_sonnet_uses_auto_tools_without_mutating_optional_patch_schema(self):
        config = {**CONFIG, 'model': 'claude-sonnet-5-5'}
        body = self._anthropic_body(config['model']); original = copy.deepcopy(body)
        body['thinking'] = {'type': 'disabled'}
        actual = policy.prepare_request_body(config, body)
        self.assertEqual(actual['tool_choice'], {'type': 'auto'})
        self.assertEqual(actual['thinking']['type'], 'between_tools')
        self.assertFalse(any(k in actual for k in ('temperature', 'top_p', 'top_k')))
        self.assertEqual(actual['tools'][0]['input_schema'], SCHEMA)
        self.assertNotIn('strict', actual['tools'][0])
        self.assertIn('supplied submission tool', actual['system'])
        self.assertEqual(body['tool_choice'], original['tool_choice'])
        self.assertEqual(body['tools'], original['tools'])
        self.assertEqual(policy.prepare_request_body(config, actual), actual)

    def test_new_haiku_keeps_supported_forced_tool_but_removes_sampling(self):
        config = {**CONFIG, 'model': 'claude-haiku-5-5'}
        body = self._anthropic_body(config['model'])
        actual = policy.prepare_request_body(config, body)
        self.assertEqual(actual['tool_choice'], body['tool_choice'])
        self.assertNotIn('temperature', actual)
        self.assertEqual(actual['tools'][0]['input_schema'], SCHEMA)

    def test_sonnet46_remains_unchanged(self):
        body = self._anthropic_body(CONFIG['model'])
        self.assertEqual(policy.prepare_request_body(CONFIG, body), body)

    def test_small_claude_connection_probes_do_not_consume_budget_on_upfront_thinking(self):
        for model, expected in [('claude-haiku-5-5', 'disabled'), ('claude-sonnet-5-5', 'between_tools')]:
            config = {**CONFIG, 'model': model}
            actual = policy.prepare_request_body(config, {'model': model, 'max_tokens': 64})
            self.assertEqual(actual['max_tokens'], 64)
            self.assertEqual(actual['thinking']['type'], expected)

    def test_gpt6_json_requests_use_reasoning_budget_without_raising_token_limit(self):
        config = {**CONFIG, 'provider': 'openai', 'model': 'gpt-6.1-sol'}
        body = {'model': config['model'], 'max_tokens': 4096, 'temperature': .2, 'top_p': .8,
                'logprobs': True, 'top_logprobs': 3, 'response_format': {'type': 'json_object'}}
        actual = policy.prepare_request_body(config, body)
        self.assertEqual(actual['max_completion_tokens'], 4096)
        self.assertEqual(actual['reasoning_effort'], 'low')
        self.assertEqual(actual['response_format'], {'type': 'json_object'})
        self.assertFalse(any(k in actual for k in ('max_tokens', 'temperature', 'top_p', 'logprobs', 'top_logprobs')))
        self.assertEqual(policy.prepare_request_body(config, actual), actual)
        self.assertEqual(body['max_tokens'], 4096)

    def test_gpt61_cannot_inadvertently_use_unsupported_chat_tools(self):
        config = {**CONFIG, 'provider': 'openai', 'model': 'gpt-6.1-sol'}
        with self.assertRaisesRegex(ValueError, 'Responses API'):
            policy.prepare_request_body(config, {'model': config['model'], 'tools': [{'type': 'function'}]})

    def test_deepseek_json_mode_retains_optional_schema_for_local_validation(self):
        config = {**CONFIG, 'provider': 'openai', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}
        body = {'model': config['model'], 'messages': [{'role': 'user', 'content': 'Adjust roughness.'}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'patch', 'schema': copy.deepcopy(SCHEMA)}}}
        original = copy.deepcopy(body)
        actual = policy.prepare_request_body(config, body)
        self.assertEqual(actual['response_format'], {'type': 'json_object'})
        self.assertEqual(json.loads(actual['messages'][0]['content'].split(': ', 1)[1]), SCHEMA)
        self.assertEqual(body, original)
        self.assertEqual(policy.prepare_request_body(config, actual), actual)

    def test_deepseek_strict_does_not_switch_endpoint_or_force_optional_mutations(self):
        config = {**CONFIG, 'provider': 'openai', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}
        body = {'model': config['model'], 'tools': [{'type': 'function', 'function':
                {'name': 'patch', 'strict': True, 'parameters': copy.deepcopy(SCHEMA)}}]}
        with self.assertRaisesRegex(ValueError, '/beta'):
            policy.prepare_request_body(config, body)
        with self.assertRaisesRegex(ValueError, '所有字段必填'):
            policy.prepare_request_body({**config, 'base_url': 'https://api.deepseek.com/beta'}, body)
        self.assertNotIn('required', body['tools'][0]['function']['parameters'])

    def test_text_only_deepseek_pro_cannot_be_used_for_vision(self):
        config = {**CONFIG, 'provider': 'openai', 'model': 'deepseek-v4-pro'}
        body = {'model': config['model'], 'messages': [{'role': 'user', 'content':
                [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,FAKE'}}]}]}
        for provider in ('openai', 'anthropic'):
            with self.subTest(provider=provider), self.assertRaisesRegex(ValueError, '不支持图片'):
                policy.prepare_request_body({**config, 'provider': provider}, body)
        self.assertFalse(policy.request_policy(config, require_vision=True)['request_allowed'])

    def test_presets_are_independent_display_metadata_not_automatic_connections(self):
        presets = policy.model_presets()
        self.assertGreaterEqual(len(presets), 3)
        self.assertFalse(any('api_key' in p or policy.is_blocked_model(p['model']) for p in presets))
        self.assertTrue(all(p['vision'] and p['price']['verified_at'] == '2026-11-10' for p in presets))
        presets[0]['model'] = 'changed-by-caller'
        self.assertNotEqual(policy.model_presets()[0]['model'], 'changed-by-caller')


class ConfigurationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.patches = [patch.object(ai_config, 'ENV_PATH', self.root / '.env'),
                        patch.object(ai_config, 'STATUS_PATH', self.root / 'main.json'),
                        patch.object(ai_config, 'VISION_STATUS_PATH', self.root / 'vision.json'),
                        patch.dict('os.environ', {}, clear=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_existing_blocked_configuration_is_visible_but_not_rewritten_or_verified(self):
        text = 'AI_PROVIDER=openai\nAI_BASE_URL=https://example.test/v1\nAI_MODEL=gpt-5.5-2026-04-23\nAI_API_KEY=fake-secret\n'
        ai_config.ENV_PATH.write_text(text, encoding='utf-8')
        config = ai_config.get_config()
        ai_config.record_result(config, True, 'Old successful connection')
        ai_config.record_vision_result(ai_config.get_vision_config(), True, 'Old image test')
        public = ai_config.public_config()
        self.assertEqual(public['model'], 'gpt-5.5-2026-04-23')
        self.assertEqual(public['status'], 'blocked_model')
        self.assertFalse(public['request_allowed'])
        self.assertFalse(public['vision_config']['request_allowed'])
        self.assertEqual(public['vision_config']['status'], 'blocked_model')
        self.assertIn('AI 设置', public['message'])
        self.assertNotIn('fake-secret', json.dumps(public))
        self.assertEqual(ai_config.ENV_PATH.read_text(encoding='utf-8'), text)

    def test_fresh_configuration_keeps_existing_gateway_default_and_has_no_claimed_connection(self):
        public = ai_config.public_config()
        self.assertEqual(public['base_url'], ai_config.DEFAULT_BASE)
        self.assertEqual(public['model'], 'claude-sonnet-4-6')
        self.assertEqual(public['status'], 'missing_key')
        self.assertFalse(public['request_allowed'])
        self.assertFalse(ai_config.ENV_PATH.exists())

    def test_official_preset_switch_clears_saved_key_unless_explicitly_replaced(self):
        ai_config.save_config({'api_key': 'fake-old-key'})
        preset = next(p for p in policy.model_presets() if p['id'] == 'claude-haiku-official')
        selected = {name: preset[name] for name in ('provider', 'base_url', 'model')}
        ai_config.save_config(selected)
        self.assertEqual(ai_config.get_config()['api_key'], '')
        self.assertEqual(ai_config.public_config()['status'], 'missing_key')
        ai_config.save_config({'api_key': 'fake-new-key'})
        self.assertTrue(ai_config.public_config()['request_allowed'])

    def test_official_vision_host_does_not_inherit_gateway_secret(self):
        ai_config.save_config({'api_key': 'fake-gateway-key', 'vision_provider': 'openai',
                               'vision_base_url': 'https://api.deepseek.com', 'vision_model': 'deepseek-flash'})
        public = ai_config.public_config()
        self.assertEqual(ai_config.get_vision_config()['api_key'], '')
        self.assertFalse(public['vision_config']['request_allowed'])

    def test_existing_official_hai_vision_key_sharing_is_preserved(self):
        ai_config.save_config({'api_key': 'fake-gateway-key', 'vision_provider': 'openai',
                               'vision_base_url': 'https://api.hai.network/openai', 'vision_model': 'vision-model'})
        self.assertEqual(ai_config.get_vision_config()['api_key'], 'fake-gateway-key')
        self.assertTrue(ai_config.public_config()['vision_config']['request_allowed'])


if __name__ == '__main__':
    unittest.main()

"""LLM semantic intent through the real planner, service, store and HTTP API.

Only paid transport and external infrastructure are replaced.  The fixture
uses an isolated SQLite database and generated PNG, never user configuration,
saved projects, network requests or a renderer.  Import the fixture module,
not its TestCase, so discovery does not run its inherited tests twice.
"""
import copy
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

import ai_client
import engine
import instruction_service as service
import studio_store as store
import test_material_decision_flow as fixture


TEXT = '灯光再亮一些，不要让物体背光'
WHEELS = '四个轮子都是黑色橡胶'
VAGUE_PRESERVATION = '别把产品本来的颜色折腾了'


def intent(kind='visual_goal', domains=('lighting',), protections=(),
           interpretation='补足主体正面光线，避免剪影；调整后检查预览。'):
    return {'kind': kind, 'interpretation': interpretation,
            'domains': list(domains), 'protections': list(protections)}


def row(ident, values=None, *, status='ready', semantic=None, bindings=None,
        summary='调整当前镜头的主光与补光，准备预览。'):
    value = {'id': ident, 'status': status, 'summary': summary,
             'patch': copy.deepcopy(values or {}),
             'target_bindings': copy.deepcopy(bindings or [])}
    if semantic is not None:
        value['intent'] = copy.deepcopy(semantic)
    return value


def lighting_rows(key=1.2, fill=1.3):
    return [row('r1', {'lighting': {'key_power': key}},
                semantic=intent('adjustment', interpretation='适度提高主光强度。')),
            row('r2', {'lighting': {'fill_power': fill}}, semantic=intent())]


def pending_wheels(ident='r3'):
    return row(ident, status='needs_clarification',
               summary='当前图不能确认全部四个轮胎，请补充轮胎位置。')


def preservation_row(ident='r4', resolved=True):
    return row(ident, status='already_satisfied' if resolved else 'needs_clarification',
               semantic=intent('preservation', domains=(),
                               protections=('preserve_color',) if resolved else (),
                               interpretation='保留产品原有颜色。' if resolved else '尚未明确需要保持的对象。'),
               summary='产品原有颜色保持不变。' if resolved else '请说明“这个”指什么。')


class LLMIntentFlowTests(unittest.TestCase):
    def setUp(self):
        fixture.MaterialDecisionFlowTests.setUp(self)
        self.body['text'] = TEXT
        self.png = self.root / 'fixture/runs/source/hero.png'

    apply = fixture.MaterialDecisionFlowTests.apply
    assert_unchanged = fixture.MaterialDecisionFlowTests.assert_unchanged

    def web_client(self):
        env = Path(__file__).resolve().parents[1] / '.env'
        original_exists = Path.exists
        with patch.object(Path, 'exists',
                          lambda path: False if path.absolute() == env else original_exists(path)):
            web = importlib.import_module('app')
        mocked = patch.object(web, 'DATA', self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        return web.app.test_client()

    def facts(self, send, attempt=0):
        content = send.call_args_list[attempt].args[1]['messages'][0]['content']
        for item in content:
            if item.get('type') != 'text':
                continue
            try:
                value = json.loads(item['text'])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and 'current_plan' in value and 'instruction' in value:
                return value
        self.fail('The actual provider request must contain the original plan and instruction.')

    def assert_current_lighting(self, result, *, status='applied', key=1.2, fill=1.3):
        self.assertEqual(result['version'], 4)
        receipt = result['instruction_result']
        self.assertEqual(receipt['status'], status)
        self.assertEqual(receipt['preview_shot_ids'], ['hero'])
        self.assertEqual(receipt['source_snapshot'], fixture.SOURCE)
        self.assertEqual(receipt['source'], 'ai')
        self.assertEqual(receipt['model'], fixture.CONFIG['model'])
        self.assertTrue(receipt['used_image'])
        self.assertEqual(len(result['history']), 1)
        original = self.project['plan']
        effective = engine.effective_plan(result['plan'], 'hero')
        self.assertAlmostEqual(effective['lighting']['key_power'], key)
        self.assertAlmostEqual(effective['lighting']['fill_power'], fill)
        self.assertEqual(effective['lighting']['rim_power'], original['lighting']['rim_power'])
        self.assertEqual(effective['exposure'], original['exposure'])
        self.assertEqual(result['plan']['lighting'], original['lighting'])
        self.assertEqual(set(result['plan']['shot_adjustments']), {'hero'})
        for field in ('material_overrides', 'object_material_overrides', 'shots',
                      'margin', 'aspect', 'style'):
            self.assertEqual(result['plan'][field], original[field], field)
        campaign = engine.effective_plan(result['plan'], 'campaign')
        original_campaign = engine.effective_plan(original, 'campaign')
        for field in ('lighting', 'exposure', 'light_softness'):
            self.assertEqual(campaign[field], original_campaign[field], field)
        self.assertEqual(store.all_records('job'), [])
        self.assertNotIn('fixture', service._INFLIGHT)

    def assert_failed_without_commit(self):
        self.assert_unchanged()
        self.assertNotIn('fixture', service._INFLIGHT)
        self.assertNotIn('instruction_receipts', store.get('project', 'fixture'))
        self.assertNotIn(service.PROPOSALS_KEY, store.get('project', 'fixture'))

    def test_exact_screenshot_reaches_llm_once_and_applies_only_current_shot(self):
        with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire(lighting_rows()))) as send:
            result = self.apply()
            replay = self.apply()
        self.assert_current_lighting(result)
        self.assertEqual(send.call_count, 1)
        facts = self.facts(send)
        self.assertEqual(facts['instruction'], TEXT)
        self.assertEqual([item['quote'] for item in facts['user_requirements']], TEXT.split('，'))
        self.assertEqual(facts['request_context']['active_shot_id'], 'hero')
        self.assertEqual(facts['image_evidence']['current_render'], True)
        self.assertEqual(facts['current_plan']['lighting'], self.project['plan']['lighting'])
        content = send.call_args.args[1]['messages'][0]['content']
        self.assertTrue(any(item.get('type') == 'image' for item in content))
        self.assertEqual(send.call_args.args[0]['model'], fixture.CONFIG['model'])
        interpretations = result['plan']['last_instruction']['semantic_intents']
        self.assertEqual({item['quote'] for item in interpretations}, set(TEXT.split('，')))
        self.assertEqual(interpretations[1]['kind'], 'visual_goal')
        self.assertEqual(interpretations[1]['domains'], ['lighting'])
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(replay['instruction_result']['id'], result['instruction_result']['id'])
        self.assertEqual(replay['version'], 4)
        self.assertEqual(replay['plan'], result['plan'])
        self.assertEqual(len(replay['history']), 1)

    def test_negative_visual_goal_alone_is_not_mistaken_for_preservation(self):
        for phrase in ('不要让物体背光', '别像剪影一样', '不要拍得像躲在阴影里'):
            with self.subTest(phrase=phrase):
                store.put('project', copy.deepcopy(self.project))
                self.body['text'] = phrase
                answer = fixture.wire([row('r1', {'lighting': {'key_power': 1.2, 'fill_power': 1.3}},
                                           semantic=intent())])
                with patch.object(ai_client, '_post', return_value=fixture.reply(answer)) as send:
                    result = self.apply(key=phrase)
                self.assert_current_lighting(result)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(self.facts(send)['instruction'], phrase)
                self.assertEqual(result['plan']['last_instruction']['semantic_intents'][0]['quote'], phrase)

    def test_unknown_negative_requires_interpretation_and_corrects_within_two_calls(self):
        missing = lighting_rows()
        del missing[1]['intent']
        with patch.object(ai_client, '_post', side_effect=[
                fixture.reply(fixture.wire(missing)),
                fixture.reply(fixture.wire(lighting_rows()))]) as send:
            result = self.apply()
        self.assert_current_lighting(result)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.facts(send, 0)['current_plan'], self.facts(send, 1)['current_plan'])
        self.assertEqual(self.facts(send, 1)['instruction'], TEXT)

    def test_known_color_and_camera_preservation_remain_hard_limits(self):
        self.body['text'] = TEXT + '，颜色和角度不变'
        with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire(lighting_rows()))) as send:
            result = self.apply()
        self.assert_current_lighting(result)
        self.assertEqual(send.call_count, 1)
        for key in ('preserve_color', 'preserve_camera'):
            self.assertIn(key, self.facts(send)['hard_protections'])
            self.assertIn(key, result['instruction_result']['protections'])
            self.assertIn(key, result['plan']['last_instruction']['protections'])

    def test_semantic_claim_cannot_bypass_explicit_color_or_camera_locks(self):
        self.body['text'] = TEXT + '，颜色和角度不变'
        for domain, unwanted in (
                ('material', {'object_material_overrides': [fixture.surface(1, color='#FF0000')]}),
                ('camera', {'shots': [{'id': 'hero', 'azimuth': 0}]})):
            with self.subTest(domain=domain):
                parts = lighting_rows()
                parts[1]['patch'].update(unwanted)
                parts[1]['intent'] = intent(domains=('lighting', domain),
                    interpretation='通过改变产品外观或机位解决背光。')
                with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire(parts))) as send:
                    with self.assertRaises(ValueError):
                        self.apply(key='forbidden-' + domain)
                self.assertGreaterEqual(send.call_count, 1)
                self.assertLessEqual(send.call_count, 2)
                self.assert_failed_without_commit()

    def test_semantic_label_cannot_relax_numeric_change_or_repeat_relative_action(self):
        self.body['text'] = '主光提高20%，不要让物体背光'
        wrong = lighting_rows(key=1.6)
        wrong[0]['intent'] = intent(interpretation='主光亮一些即可，不必保留具体比例。')
        correct = lighting_rows(key=1.2)
        correct[0]['intent'] = intent('adjustment', interpretation='将当前主光精确提高20%。')
        with patch.object(ai_client, '_post', side_effect=[
                fixture.reply(fixture.wire(wrong)), fixture.reply(fixture.wire(correct))]) as send:
            result = self.apply()
            replay = self.apply()
        self.assert_current_lighting(result)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.facts(send, 1)['current_plan']['lighting']['key_power'], 1)
        self.assertEqual(replay['plan'], result['plan'])
        self.assertEqual(len(replay['history']), 1)

    def test_unresolved_real_preservation_never_silently_applies_ready_lighting(self):
        self.body['text'] = '灯光再亮一些，不要改这个'
        answer = fixture.wire([lighting_rows()[0], preservation_row('r2', resolved=False)])
        with patch.object(ai_client, '_post', return_value=fixture.reply(answer)) as send:
            with self.assertRaises(ValueError) as caught:
                self.apply()
        self.assertEqual(self.facts(send)['instruction'], self.body['text'])
        self.assertGreaterEqual(send.call_count, 1)
        self.assertLessEqual(send.call_count, 2)
        self.assertEqual(getattr(caught.exception, 'status', None), 'needs_clarification')
        self.assert_failed_without_commit()

    def test_auto_partial_applies_lighting_and_retains_only_unresolved_wheel_clause(self):
        self.body['text'] = TEXT + '，' + WHEELS
        answer = fixture.wire(lighting_rows() + [pending_wheels()])
        with patch.object(ai_client, '_post', return_value=fixture.reply(answer)) as send:
            result = self.apply()
            replay = self.apply()
        self.assert_current_lighting(result, status='partially_applied')
        receipt = result['instruction_result']
        self.assertTrue(receipt['partial'])
        self.assertEqual(receipt['remaining_text'], WHEELS)
        self.assertEqual([item['quote'] for item in receipt['pending']], [WHEELS])
        self.assertEqual({item['quote'] for item in receipt['ready']}, set(TEXT.split('，')))
        self.assertNotIn('proposal_id', receipt)
        self.assertNotIn(service.PROPOSALS_KEY, store.get('project', 'fixture'))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(replay['plan'], result['plan'])
        self.assertTrue(replay['instruction_result']['replayed'])

    def test_auto_partial_carries_llm_resolved_preservation_into_remaining_request(self):
        self.body['text'] = TEXT + '，' + WHEELS + '，' + VAGUE_PRESERVATION
        answer = fixture.wire(lighting_rows() + [pending_wheels(), preservation_row()])
        with patch.object(ai_client, '_post', return_value=fixture.reply(answer)) as send:
            result = self.apply()
        self.assert_current_lighting(result, status='partially_applied')
        receipt = result['instruction_result']
        self.assertEqual(receipt['remaining_text'], WHEELS + '，' + VAGUE_PRESERVATION)
        self.assertEqual([item['quote'] for item in receipt['pending']], [WHEELS])
        self.assertIn('preserve_color', receipt['protections'])
        self.assertIn('preserve_color', result['plan']['last_instruction']['protections'])
        self.assertIn(VAGUE_PRESERVATION, result['plan']['last_instruction']['semantic_constraints'])
        self.assertEqual(send.call_count, 1)
        self.assertNotIn(service.PROPOSALS_KEY, store.get('project', 'fixture'))

    def test_invalid_target_cannot_be_authorized_by_semantics_and_correction_keeps_ready_subset(self):
        self.body['text'] = TEXT + '，' + WHEELS
        invalid = row('r3', {'object_material_overrides': [
            fixture.surface(n, preset='rubber', color='#111111') for n in range(1, 5)]},
            semantic=intent('adjustment', domains=('material',), interpretation='四个轮胎都使用黑色橡胶。'),
            bindings=[fixture.binding('四个轮子', ['t1', 't2', 't3', 't999'])])
        with patch.object(ai_client, '_post', side_effect=[
                fixture.reply(fixture.wire(lighting_rows() + [invalid])),
                fixture.reply(fixture.wire(lighting_rows() + [pending_wheels()]))]) as send:
            result = self.apply()
        self.assert_current_lighting(result, status='partially_applied')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['instruction_result']['remaining_text'], WHEELS)
        self.assertEqual(result['plan']['object_material_overrides'], self.project['plan']['object_material_overrides'])
        self.assertEqual(self.facts(send, 1)['current_plan'], self.facts(send)['current_plan'])

    def test_http_success_replay_and_undo_do_not_repeat_ai_or_submit_preview_jobs(self):
        client = self.web_client()
        headers = {'Idempotency-Key': 'llm-lighting-http'}
        with patch.object(ai_client, '_post', return_value=fixture.reply(fixture.wire(lighting_rows()))) as send:
            first = client.post('/api/projects/fixture/instruct', json=self.body, headers=headers)
            self.assertEqual(first.status_code, 200, first.get_json())
            applied = first.get_json()
            self.assert_current_lighting(applied)
            replay = client.post('/api/projects/fixture/instruct', json=self.body, headers=headers)
            self.assertEqual(replay.status_code, 200)
            self.assertTrue(replay.get_json()['instruction_result']['replayed'])
            undone = client.post('/api/projects/fixture/undo')
            self.assertEqual(undone.status_code, 200)
            self.assertEqual(undone.get_json()['version'], 5)
            self.assertEqual(undone.get_json()['plan'], self.project['plan'])
            after_undo = client.post('/api/projects/fixture/instruct', json=self.body, headers=headers)
        self.assertEqual(after_undo.status_code, 200)
        final = after_undo.get_json()
        self.assertEqual(final['version'], 5)
        self.assertEqual(final['plan'], self.project['plan'])
        self.assertEqual(final['history'], [])
        self.assertTrue(final['instruction_result']['replayed'])
        self.assertEqual(final['instruction_result']['id'], applied['instruction_result']['id'])
        self.assertEqual(final['instruction_result']['applied_version'], 4)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(store.all_records('job'), [])

    def test_image_or_target_evidence_replacement_during_lighting_inference_blocks_commit(self):
        for changed in ('image', 'target_evidence'):
            with self.subTest(changed=changed):
                def respond(*args):
                    if changed == 'image':
                        Image.new('RGB', (32, 32), 'gray').save(self.png)
                    else:
                        altered = copy.deepcopy(self.result)
                        altered['product_samples'][0]['x'] = .9
                        self.result_path.write_text(json.dumps(altered))
                    return fixture.reply(fixture.wire(lighting_rows()))
                with patch.object(ai_client, '_post', side_effect=respond) as send:
                    with self.assertRaises(service.InstructionError) as caught:
                        self.apply(key='source-' + changed)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(caught.exception.error_code, 'instruction_proposal_stale')
                self.assert_failed_without_commit()

    def test_readonly_diagnosis_refresh_during_inference_does_not_block_valid_lighting(self):
        review = {'source': 'rules', 'checked_at': 123,
                  'realism': {'summary': '诊断已更新，方案和来源图未变。', 'causes': []}}
        def respond(*args):
            latest = store.get('project', 'fixture')
            latest['outputs'][0]['review'] = copy.deepcopy(review)
            store.put('project', latest)
            return fixture.reply(fixture.wire(lighting_rows()))
        with patch.object(ai_client, '_post', side_effect=respond) as send:
            result = self.apply()
        self.assert_current_lighting(result)
        self.assertEqual(result['outputs'][0]['review'], review)
        self.assertEqual(send.call_count, 1)


if __name__ == '__main__':
    unittest.main()

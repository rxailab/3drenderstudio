"""What the AI calls cost and what is not asked twice (v40): the usage log, the answer cache, headline candidates,
the stacked close-up for long products and the per-call model settings. No model is called."""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import ai_client
import ai_transport
import brandkit_review
import scene_lock
import studio_store

CONFIG = {'provider': 'anthropic', 'base_url': 'https://api.hai.network/anthropic', 'api_key': 'test-secret-do-not-log',
          'model': 'claude-sonnet-4-6', 'enabled': True}
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['notes'], 'properties': {'notes': {'type': 'string'}}}


def reply(answer, usage=None):
    """A provider response whose tool call carries ``answer``."""
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': answer}],
            'usage': usage or {'input_tokens': 1200, 'output_tokens': 300}}


class Store(unittest.TestCase):
    """Each test with its own data folder: the cache table and the usage log are written there."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        with contextlib.closing(sqlite3.connect(str(self.data / 'studio.sqlite'))) as c:
            c.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))'); c.commit()
        moved = patch.object(studio_store, 'DATA', self.data); moved.start(); self.addCleanup(moved.stop)
        logged = patch.object(ai_transport, 'USAGE_DIR', None); logged.start(); self.addCleanup(logged.stop)     # the log beside it
        knobs = patch('ai_config.read_env', return_value={}); knobs.start(); self.addCleanup(knobs.stop)

    def picture(self, colour=(200, 40, 30)):
        path = self.data / ('page-%d-%d-%d.png' % colour)
        Image.new('RGB', (300, 300), colour).save(path)
        return str(path)


class UsageLog(Store):
    def test_each_answered_request_logs_its_caller_model_tokens_and_images_but_never_the_body_or_key(self):
        raw = json.dumps(reply({'notes': 'ok'}, {'input_tokens': 900, 'output_tokens': 120, 'cache_read_input_tokens': 2000,
                                                 'cache_creation_input_tokens': 0, 'server_tool_use': {'web_search_requests': 0},
                                                 'service_tier': 'standard'})).encode()

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): return False

        class Opener:
            def open(self, request, timeout): return Response(raw)

        body = ai_client._payload(CONFIG, 'system words', {'facts': 'a secret fact'}, SCHEMA, image_path=self.picture())

        def scene_check():                  # stands for a caller such as scene_imagegen.check
            return ai_client._post(CONFIG, body)
        with patch.object(ai_client.urllib.request, 'build_opener', return_value=Opener()), ai_client.tally() as used:
            scene_check()
        lines = (self.data / ai_client.USAGE_LOG).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual((row['caller'], row['model'], row['n_images']), (__name__ + '.scene_check', 'claude-sonnet-4-6', 1))
        self.assertEqual(row['usage'], {'input_tokens': 900, 'output_tokens': 120, 'cache_read_input_tokens': 2000,
                                        'cache_creation_input_tokens': 0, 'server_tool_use': {'web_search_requests': 0}})
        self.assertNotIn('secret', lines[0]); self.assertNotIn('system words', lines[0]); self.assertIn('ts', row)
        self.assertEqual((used['tokens'], used['calls']), (3020, 1))

    def test_a_named_step_and_a_cached_answer_are_logged_as_such(self):
        with patch.object(ai_client, '_post', return_value=reply({'notes': 'ok'})):
            brandkit_review._ask(CONFIG, 'Set review', {'n': 1}, SCHEMA, image_path=self.picture(), caller='brandkit_review.review_set')
            brandkit_review._ask(CONFIG, 'Set review', {'n': 1}, SCHEMA, image_path=self.picture(), caller='brandkit_review.review_set')
        row = json.loads((self.data / ai_client.USAGE_LOG).read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((row['caller'], row['cached'], row['n_images']), ('brandkit_review.review_set', True, 1))

    def test_a_usage_block_in_an_odd_shape_never_fails_a_good_answer(self):
        odd = {'input_tokens': {'text': 5}, 'output_tokens': 7}
        with ai_client.tally() as used:
            ai_client._record_usage(CONFIG, {'model': 'm', 'messages': []}, {'usage': odd})
        self.assertEqual((used['input_tokens'], used['output_tokens'], used['calls']), (0, 7, 1))

    def test_the_log_goes_where_usage_dir_says_so_test_runs_leave_the_real_one_alone(self):
        other = self.data / 'elsewhere'; other.mkdir()
        with patch.object(ai_transport, 'USAGE_DIR', str(other)):
            ai_client.log_usage({'caller': 'x', 'usage': {}})
        self.assertTrue((other / ai_client.USAGE_LOG).is_file())
        self.assertFalse((self.data / ai_client.USAGE_LOG).exists())


class AnswerCache(Store):
    def test_the_key_is_the_exact_request_whatever_the_order_of_its_fields(self):
        one = ai_client._payload(CONFIG, 'Review', {'a': 1, 'b': [1, 2]}, SCHEMA, image_path=self.picture())
        again = ai_client._payload(CONFIG, 'Review', {'a': 1, 'b': [1, 2]}, SCHEMA, image_path=self.picture())
        self.assertEqual(brandkit_review.cache_key(one), brandkit_review.cache_key(again))           # the JPEG of one PNG is the same bytes
        self.assertEqual(brandkit_review.cache_key(one), brandkit_review.cache_key(dict(reversed(list(one.items())))))
        other_picture = ai_client._payload(CONFIG, 'Review', {'a': 1, 'b': [1, 2]}, SCHEMA, image_path=self.picture((60, 40, 200)))
        other_model = ai_client._payload({**CONFIG, 'model': 'claude-haiku-5-5'}, 'Review', {'a': 1, 'b': [1, 2]}, SCHEMA, image_path=self.picture())
        other_prompt = ai_client._payload(CONFIG, 'Review!', {'a': 1, 'b': [1, 2]}, SCHEMA, image_path=self.picture())
        keys = {brandkit_review.cache_key(body) for body in (one, other_picture, other_model, other_prompt)}
        self.assertEqual(len(keys), 4)

    def test_the_same_review_is_answered_from_the_cache_unless_fresh_is_asked(self):
        ask = lambda **extra: brandkit_review._ask(CONFIG, 'Review', {'page': 'T01'}, SCHEMA, image_path=self.picture(),
                                                   caller='brandkit_review.review', **extra)
        with patch.object(ai_client, '_post', side_effect=[reply({'notes': 'first'}), reply({'notes': 'second'}), reply({'notes': 'third'})]) as sent:
            self.assertEqual(ask(), {'notes': 'first'})
            self.assertEqual(ask(), {'notes': 'first'}); self.assertEqual(sent.call_count, 1)
            self.assertEqual(ask(fresh=True), {'notes': 'second'}); self.assertEqual(sent.call_count, 2)
            self.assertEqual(ask(), {'notes': 'second'})                                     # the fresh answer is the one kept
            with brandkit_review.fresh_answers():
                self.assertEqual(ask(), {'notes': 'third'})
            self.assertEqual(sent.call_count, 3)
        stored = studio_store.get(brandkit_review.CACHE_KIND, brandkit_review.cache_key(
            ai_client._payload(CONFIG, 'Review', {'page': 'T01'}, SCHEMA, image_path=self.picture(), label_images=True, max_tokens=3000)))
        self.assertEqual((stored['output'], stored['caller']), ({'notes': 'third'}, 'brandkit_review.review')); self.assertIn('created_at', stored)

    def test_only_answers_that_fit_the_schema_are_kept_and_other_steps_are_always_asked(self):
        with patch.object(ai_client, '_post', side_effect=[reply({'wrong': 1}), reply({'wrong': 2})]), self.assertRaises(ai_client.SchemaValidationError):
            brandkit_review._ask(CONFIG, 'Review', {'page': 'T02'}, SCHEMA, caller='brandkit_review.review')
        self.assertEqual(studio_store.all_records(brandkit_review.CACHE_KIND), [])
        with patch.object(ai_client, '_post', return_value=reply({'notes': 'x'})) as sent:
            for _ in range(2):              # a translation or a scene caption is asked again: its own cache keeps only what it accepts
                brandkit_review._ask(CONFIG, 'Localise', {'line': 'a'}, SCHEMA, caller='brandkit_set.localise')
        self.assertEqual(sent.call_count, 2)

    def test_old_answers_are_pruned(self):
        for index in range(6):
            studio_store.put(brandkit_review.CACHE_KIND, {'id': 'k%d' % index, 'output': {}})
        self.assertEqual(studio_store.prune(brandkit_review.CACHE_KIND, 4), 2)
        self.assertEqual(sorted(row['id'] for row in studio_store.all_records(brandkit_review.CACHE_KIND)), ['k2', 'k3', 'k4', 'k5'])


class FreshBuild(Store):
    def test_a_build_asked_fresh_reaches_build_and_turns_the_cache_off_there(self):
        import listing
        seen = []

        def build(project_id, cancelled, progress, fresh=False):
            seen.append((project_id, fresh, brandkit_review.FRESH.get()))
            with brandkit_review.fresh_answers(fresh):
                seen.append(brandkit_review.FRESH.get())
            return [], []
        for fresh in (True, False):
            task = studio_store.put(listing.KIND, {'id': 'task-%s' % fresh, 'project_id': 'p', 'status': 'queued', 'fresh': fresh})
            with patch.object(listing, 'build', side_effect=build):
                listing._run(task['id'])
        self.assertEqual(seen, [('p', True, False), True, ('p', False, False), False])


class Candidates(Store):
    PRODUCT = {'language': 'zh', 'title': '托特包', 'model': '', 'headline': '', 'subline': '', 'size_mm': 476,
               'features': [{'title': '皮革提手', 'detail': ''}], 'specs': [], 'figures': [{'value': '476', 'unit': 'mm', 'label': '高度'}]}

    def test_the_first_candidate_that_passes_the_checks_is_used_without_a_rewrite(self):
        answer = {'pages': [{'template': 'T04', 'candidates': [{'eyebrow': '参数', 'headline': '高度520mm'},          # a number not in the facts
                                                               {'eyebrow': '尺寸', 'headline': '476mm高\n装得下'},
                                                               {'eyebrow': '尺寸', 'headline': '第三条'}]},
                            {'template': 'T01', 'candidates': [{'eyebrow': '部位导览', 'headline': '卷边提手\n铆钉加固'}]}]}
        with patch.object(brandkit_review, '_configs', return_value=(CONFIG, None)), patch.object(brandkit_review, '_ask', return_value=answer) as asked:
            written, note = brandkit_review.write_copy(self.PRODUCT, ['T01', 'T04'])
        self.assertEqual(written, {'T04': {'eyebrow': '尺寸', 'headline': '476mm高\n装得下'}, 'T01': {'eyebrow': '部位导览', 'headline': '卷边提手\n铆钉加固'}})
        self.assertIsNone(note); self.assertEqual(asked.call_count, 1)
        self.assertEqual(asked.call_args.args[2]['candidates_per_page'], brandkit_review.CANDIDATES)

    def test_a_page_with_no_passing_candidate_is_rewritten_once_told_about_each(self):
        first = {'pages': [{'template': 'T04', 'candidates': [{'eyebrow': '参数', 'headline': '高度520mm'}, {'eyebrow': '细节', 'headline': '细节看得见'}]}]}
        second = {'pages': [{'template': 'T04', 'candidates': [{'eyebrow': '尺寸', 'headline': '476mm高'}]}]}
        with patch.object(brandkit_review, '_configs', return_value=(CONFIG, None)), \
                patch.object(brandkit_review, '_ask', side_effect=[first, second]) as asked:
            written, note = brandkit_review.write_copy(self.PRODUCT, ['T04'])
        self.assertEqual(written, {'T04': {'eyebrow': '尺寸', 'headline': '476mm高'}})
        told = asked.call_args_list[1].args[2]['rewrite_because'][0]['you_wrote']
        self.assertEqual([row['headline'] for row in told], ['高度520mm', '细节看得见'])
        self.assertEqual([row['problems'] for row in told], [['a number the facts do not contain'], ['the eyebrow repeats the headline']])

    def test_the_schema_takes_both_shapes(self):
        schema = brandkit_review._copy_schema(['T01'])
        ai_client._check_schema({'pages': [{'template': 'T01', 'candidates': [{'eyebrow': 'a', 'headline': 'b'}] * 3}]}, schema)
        ai_client._check_schema({'pages': [{'template': 'T01', 'eyebrow': 'a', 'headline': 'b'}]}, schema)
        self.assertEqual(brandkit_review._options({'template': 'T01', 'eyebrow': 'a', 'headline': 'b'}), [{'eyebrow': 'a', 'headline': 'b'}])


class StackedCloseUp(unittest.TestCase):
    def product(self, size, box):
        layer = Image.new('RGBA', size, (0, 0, 0, 0)); pen = ImageDraw.Draw(layer)
        pen.rectangle(box, fill=(200, 35, 30, 255)); pen.rectangle((box[0], box[1], box[0] + 20, box[3]), fill=(20, 20, 20, 255))
        return layer

    def test_a_long_product_is_stacked_render_on_top_each_panel_as_wide_as_the_check_sends(self):
        layer = self.product((2048, 2048), (124, 900, 1924, 1200))            # 1800 x 300: a caliper
        picture = Image.new('RGB', layer.size, (90, 120, 80)); picture.paste(layer, mask=layer.getchannel('A'))
        sheet = scene_lock.side_by_side(layer, picture, {'shift': [0, 0]})
        panel = (sheet.height - 24) // 2
        self.assertEqual(sheet.width, scene_lock.STACK_WIDTH); self.assertEqual(sheet.height, panel * 2 + 24)
        self.assertAlmostEqual(panel / sheet.width, (300 + 2 * 108) / (1800 + 2 * 108), delta=.01)
        self.assertEqual(sheet.getpixel((4, 4)), (255, 255, 255))                    # the render, on white, on top
        self.assertEqual(sheet.getpixel((4, panel + 12)), (255, 255, 255))           # the gap
        self.assertEqual(sheet.getpixel((4, panel + 28)), (90, 120, 80))             # the picture below
        middle = sheet.width // 2
        self.assertEqual(sheet.getpixel((middle, panel // 2)), sheet.getpixel((middle, panel + 24 + panel // 2)))   # parts line up

    def test_a_compact_product_stays_side_by_side(self):
        layer = self.product((1024, 1024), (300, 300, 700, 600))               # 400 x 300
        picture = Image.new('RGB', layer.size, (90, 120, 80)); picture.paste(layer, mask=layer.getchannel('A'))
        sheet = scene_lock.side_by_side(layer, picture, {'shift': [0, 0]}, height=320)
        self.assertEqual(sheet.height, 320); self.assertGreater(sheet.width, 2 * 320)


class ModelKnobs(Store):
    def test_the_cheaper_models_are_used_only_by_the_calls_routed_to_them_and_only_when_named(self):
        self.assertIs(brandkit_review.model_for(CONFIG, 'brandkit_set.localise'), CONFIG)          # off by default
        with patch('ai_config.read_env', return_value={'AI_COPY_MODEL': 'claude-haiku-5-5', 'AI_LIGHT_VISION_MODEL': 'claude-haiku-5-5'}):
            for caller in ('brandkit_set.localise', 'brandkit_review.write_copy', 'brandkit_set.captions', 'brandkit_set.seen_captions',
                           'brandkit_review.review_set'):
                self.assertEqual(brandkit_review.model_for(CONFIG, caller)['model'], 'claude-haiku-5-5')
            for caller in ('brandkit_review.review', 'brandkit_review.verify', 'brandkit_set.locate', 'articulation.analyse'):
                self.assertEqual(brandkit_review.model_for(CONFIG, caller)['model'], 'claude-sonnet-4-6')
            with patch.object(ai_client, '_post', return_value=reply({'notes': 'ok'})) as sent:
                brandkit_review._ask(CONFIG, 'Set', {'n': 1}, SCHEMA, caller='brandkit_review.review_set')
                brandkit_review._ask(CONFIG, 'Review', {'n': 1}, SCHEMA, caller='brandkit_review.review')
            self.assertEqual([call.args[1]['model'] for call in sent.call_args_list], ['claude-haiku-5-5', 'claude-sonnet-4-6'])
        with patch('ai_config.read_env', return_value={'AI_LIGHT_VISION_MODEL': 'claude-haiku-5-5'}):
            self.assertEqual(brandkit_review.model_for(CONFIG, 'brandkit_review.write_copy')['model'], 'claude-sonnet-4-6')

    def test_the_caller_defaults_to_the_function_that_asked(self):
        def localise():
            return brandkit_review._ask(CONFIG, 'Localise', {'n': 1}, SCHEMA)
        with patch('ai_config.read_env', return_value={'AI_COPY_MODEL': 'claude-haiku-5-5'}), \
                patch.object(brandkit_review, 'MODEL_KNOBS', {__name__ + '.localise': 'AI_COPY_MODEL'}), \
                patch.object(ai_client, '_post', return_value=reply({'notes': 'ok'})) as sent:
            localise()
        self.assertEqual(sent.call_args.args[1]['model'], 'claude-haiku-5-5')

    def test_the_scene_captions_follow_the_copy_model_and_a_helper_named_ask_is_not_the_caller(self):
        import brandkit_set, scene_stylist
        briefs = [{'role': 'use', 'proposal': {'scene': 'a workbench'}}]
        product = {'language': 'en', 'title': 'Caliper'}
        raw = json.dumps(reply({'scenes': [{'title': 'At the bench', 'body': 'Measuring a dowel'}]})).encode()

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): return False

        class Opener:
            def open(self, request, timeout): return Response(raw)
        with patch('ai_config.read_env', return_value={'AI_COPY_MODEL': 'claude-haiku-5-5'}), \
                patch.object(scene_stylist, '_config', return_value=CONFIG), \
                patch.object(ai_client.urllib.request, 'build_opener', return_value=Opener()):
            rows = brandkit_set.captions({}, product, briefs)
        self.assertEqual(rows[0]['title'], 'At the bench')
        row = json.loads((self.data / ai_client.USAGE_LOG).read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((row['model'], row['caller']), ('claude-haiku-5-5', 'brandkit_set.captions'))   # not scene_stylist._ask


if __name__ == '__main__':
    unittest.main()

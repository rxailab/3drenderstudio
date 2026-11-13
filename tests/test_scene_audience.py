"""Audience-led usage scenes: the reference pack, the strategist and image generation.

No network and no Blender are used: model answers are scripted, the Images
API is replaced by canned responses and reference renders by small PNG files.
What a generated picture looks like, and whether a real model keeps the
product intact, is judged on real runs, not here.
"""
import base64
import copy
import io
import json
import queue
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import ai_config
import engine
import planner
import scene_audience
import scene_imagegen
import scene_props
import scene_stylist
import studio_store as store
from test_scene_images import MODELS, TEXTURES, IDENTITY, project

IMAGE = {'provider': 'openai', 'base_url': 'https://api.hai.network/openai/v1', 'model': 'gpt-image-2', 'api_key': 'test-key',
         'enabled': True, 'quality': 'medium'}


def png(size=(64, 64), colour=(200, 30, 30)):
    stream = io.BytesIO(); Image.new('RGB', size, colour).save(stream, format='PNG')
    return stream.getvalue()


def answer(**changes):
    """A well-formed strategist answer for the tool cabinet."""
    value = {'segment_id': 'site_team', 'reason': '带脚轮和抽屉的工具柜，适合安装团队。', 'reference_id': '05-06', 'product': 'rolling tool cabinet',
             'max_dimension_mm': 740, 'summary': '安装工人在工地从工具柜抽屉取工具。', 'people': True, 'complex': True,
             'scene': 'A small renovation jobsite with dust sheets. An installer takes a drill from the open drawer of the tool cabinet, which stands in the right foreground.',
             'setting': 'workshop', 'azimuth': 30, 'elevation': 20}
    value.update(changes)
    return value


class ReferencePackTests(unittest.TestCase):
    def test_the_pack_gives_eight_segments_of_ten_numbered_references(self):
        segments = scene_audience.library()
        self.assertEqual([item['id'] for item in segments], [base['id'] for base in scene_audience.SEGMENTS])
        self.assertTrue(all(len(item['references']) == 10 for item in segments))
        self.assertEqual(segments[4]['references'][5]['id'], '05-06')
        self.assertEqual(scene_audience.reference('05-06')['title'], 'EXAL18V2-320 双充电器')
        for item in segments:
            self.assertTrue(item['question'] and item['priority'] and item['focus'] and item['scene'])

    def test_the_pack_holds_links_and_words_only(self):
        files = sorted(path.name for path in scene_audience.PACK.iterdir())
        self.assertEqual(files, ['README.md', 'index.html', 'references.csv'])
        self.assertNotIn('<img', (scene_audience.PACK / 'index.html').read_text(encoding='utf-8'))
        for item in scene_audience.library():
            for row in item['references']:
                self.assertTrue(row['url'].startswith('https://'))
                self.assertEqual(set(row), {'id', 'title', 'url', 'locator', 'attribution', 'note', 'in_use'})

    def test_a_reference_belongs_to_its_segment_only(self):
        self.assertIsNotNone(scene_audience.reference('05-06', 'site_team'))
        self.assertIsNone(scene_audience.reference('05-06', 'home_garden'))
        self.assertIsNone(scene_audience.reference('99-99'))

    def test_a_missing_pack_means_no_segments_not_an_error(self):
        with patch.object(scene_audience, 'PACK', ROOT / 'no-such-pack'):
            self.assertEqual(scene_audience.library(), [])
            self.assertEqual(scene_audience.public()['segments'], [])
            self.assertEqual(scene_audience.propose_rules('红色工具柜', '红色工具柜')[0], None)
        self.assertEqual(len(scene_audience.library()), 8)          # and the real pack is read again afterwards

    def test_rows_that_are_not_web_links_are_left_out(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder)
            (pack / 'references.csv').write_text('segment,title,url,locator,attribution,note\n'
                                                 '小型施工与安装团队,好的,https://example.com/a,顶部,Example,说明\n'
                                                 '小型施工与安装团队,脚本,javascript:alert(1),顶部,Example,说明\n'
                                                 '小型施工与安装团队,文件,file:///etc/passwd,顶部,Example,说明\n', encoding='utf-8')
            with patch.object(scene_audience, 'PACK', pack):
                rows = scene_audience.library()[0]['references']
        self.assertEqual([row['title'] for row in rows], ['好的'])

    def test_products_are_matched_to_segments_by_what_they_are_called(self):
        self.assertEqual(scene_audience.rank('红色工具柜 抽屉 轮子', '红色工具柜')[0], 'site_team')
        self.assertEqual(scene_audience.rank('', '无绳割草机')[0], 'home_garden')
        self.assertEqual(scene_audience.rank('', 'digital multimeter')[0], 'industrial_maintenance')
        self.assertEqual(scene_audience.rank('pump bottle', '哑光按压瓶'), [])

    def test_rules_propose_a_segment_or_say_that_none_fits(self):
        segment_id, row, reason = scene_audience.propose_rules('抽屉 轮子', '红色工具柜')
        self.assertEqual(segment_id, 'site_team'); self.assertTrue(row['id'].startswith('05-')); self.assertIn('小型施工与安装团队', reason)
        self.assertEqual(scene_audience.propose_rules('', '哑光按压瓶')[:2], (None, None))
        self.assertEqual(scene_audience.propose_rules('', '哑光按压瓶', required='home_garden')[0], 'home_garden')
        self.assertEqual(scene_audience.propose_rules('', 'model_final_v3', recipe_segment='site_team')[0], 'site_team')

    def test_publisher_names_never_reach_a_scene_description(self):
        self.assertEqual(scene_audience.brands(), ['Bosch', 'Fluke', 'Husqvarna', 'STIHL'])
        cleaned = scene_audience.scrub('A Bosch drill, a stihl mower and a FLUKE meter by Husqvarna.')
        for name in scene_audience.brands():
            self.assertNotIn(name.lower(), cleaned.lower())
        facts = scene_audience.brief_text('site_team', '05-06')
        self.assertEqual(facts['buying_question'], '如何运输、配置和管理')
        self.assertNotIn('EXAL', json.dumps(facts, ensure_ascii=False))       # the observation, not the model number


class BriefContractTests(unittest.TestCase):
    def plan(self, **brief):
        plan = planner.default_plan()
        plan['shots'].append({'id': 'scene', 'label': '使用场景', 'kind': 'full', 'style': 'soft', 'projection': 'perspective',
                              'azimuth': 35, 'elevation': 22, 'focal_length': 55})
        plan['scene_brief'] = {'version': 1, 'shot_id': 'scene', 'segment_id': 'site_team', 'reference_id': '05-06', 'source': 'ai',
                               'method': 'generate', 'reason': '理由', 'summary': '画面', 'people': True, 'complex': False, **brief}
        return plan

    def test_a_brief_is_part_of_a_valid_plan_and_marks_a_generated_shot(self):
        plan = planner.validate(self.plan())
        self.assertEqual(scene_audience.image_shots(plan), {'scene'})
        self.assertEqual(scene_audience.image_shots(planner.validate(self.plan(method='render'))), set())
        self.assertEqual(scene_audience.image_shots(planner.default_plan()), set())
        planner.validate(self.plan(segment_id=None, reference_id=None, source='rules'))     # no segment fitted

    def test_anything_else_is_rejected(self):
        for change in ({'version': 2}, {'shot_id': 'missing'}, {'segment_id': 'Site Team'}, {'reference_id': '5-6'}, {'source': 'web'},
                       {'method': 'paint'}, {'reason': 'x' * 401}, {'summary': 'a\nb'}, {'people': 'yes'}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                planner.validate(self.plan(**change))


class ImageConfigTests(unittest.TestCase):
    def config(self, **env):
        with patch.object(ai_config, 'read_env', return_value=env), patch.dict('os.environ', {}, clear=True):
            return ai_config.get_image_config(), ai_config.public_image_config()

    def test_on_hai_the_image_model_follows_the_main_account(self):
        value, shown = self.config(AI_API_KEY='token')
        self.assertEqual((value['base_url'], value['model'], value['api_key'], value['quality']),
                         ('https://api.hai.network/openai/v1', 'gpt-image-2', 'token', 'medium'))
        self.assertTrue(shown['available']); self.assertNotIn('api_key', shown)
        self.assertEqual(scene_imagegen.endpoint(value), 'https://api.hai.network/openai/v1/images/edits')

    def test_elsewhere_it_must_be_configured_and_a_key_never_follows_to_another_host(self):
        value, shown = self.config(AI_BASE_URL='https://api.deepseek.com', AI_PROVIDER='openai', AI_MODEL='deepseek-flash', AI_API_KEY='token')
        self.assertEqual((value['base_url'], value['api_key']), ('', '')); self.assertEqual(shown['status'], 'not_configured')
        value, shown = self.config(AI_BASE_URL='https://api.deepseek.com', AI_PROVIDER='openai', AI_MODEL='deepseek-flash', AI_API_KEY='token',
                                   AI_IMAGE_BASE_URL='https://images.example.com/v1', AI_IMAGE_MODEL='some-image-model')
        self.assertEqual(value['api_key'], ''); self.assertEqual(shown['status'], 'missing_key')
        value, shown = self.config(AI_API_KEY='token', AI_IMAGE_BASE_URL='https://images.example.com/v1', AI_IMAGE_MODEL='m', AI_IMAGE_API_KEY='own')
        self.assertEqual(value['api_key'], 'own'); self.assertTrue(shown['available'])

    def test_it_can_be_switched_off_and_follows_the_main_switch(self):
        self.assertEqual(self.config(AI_API_KEY='token', AI_IMAGE_ENABLED='0')[1]['status'], 'disabled')
        self.assertEqual(self.config(AI_API_KEY='token', AI_ENABLED='0')[1]['status'], 'disabled')
        self.assertEqual(self.config(AI_API_KEY='token', AI_IMAGE_QUALITY='HIGH')[0]['quality'], 'high')
        self.assertEqual(self.config(AI_API_KEY='token', AI_IMAGE_QUALITY='ultra')[0]['quality'], 'medium')
        self.assertEqual(self.config(AI_API_KEY='token', AI_IMAGE_BASE_URL='http://images.example.com/v1', AI_IMAGE_MODEL='m')[1]['status'], 'error')


class Response:
    """What urlopen gives back, enough for the image client."""

    def __init__(self, body, content_type):
        self.stream = io.BytesIO(body); self.headers = {'Content-Type': content_type}

    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def readline(self, limit=-1): return self.stream.readline(limit)
    def read(self, limit=-1): return self.stream.read(limit)


class Opener:
    def __init__(self, result): self.result = result; self.request = None

    def open(self, request, timeout=None):
        self.request = request
        if isinstance(self.result, Exception): raise self.result
        return self.result


def events(*rows):
    return b''.join(b'event: ' + row['type'].encode() + b'\ndata: ' + json.dumps(row).encode() + b'\n\n' for row in rows)


class ImageClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.references = []
        for index in range(3):
            path = self.folder / ('ref%d.png' % index); path.write_bytes(png()); self.references.append(path)

    def call(self, result, **options):
        opener = Opener(result)
        with patch.object(scene_imagegen.urllib.request, 'build_opener', return_value=opener):
            made = scene_imagegen.edit(IMAGE, 'a scene', self.references, (2048, 2048), **options)
        return made, opener.request

    def test_the_renders_go_out_as_reference_images_and_the_completed_picture_comes_back(self):
        picture = png((512, 512)); stages = []
        body = events({'type': 'image_edit.partial_image', 'b64_json': base64.b64encode(png()).decode()},
                      {'type': 'image_edit.completed', 'b64_json': base64.b64encode(picture).decode(), 'usage': {'input_tokens': 3, 'output_tokens': 4, 'total_tokens': 7}})
        made, request = self.call(Response(body, 'text/event-stream'), progress=stages.append)
        self.assertEqual((made['png'], made['size'], made['usage']['total_tokens']), (picture, [512, 512], 7))
        self.assertEqual(stages, ['图像模型正在生成（已返回草图）'])
        self.assertEqual(request.full_url, 'https://api.hai.network/openai/v1/images/edits')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
        sent = request.data
        self.assertEqual(sent.count(b'name="image[]"'), 3)
        for field, value in ((b'model', b'gpt-image-2'), (b'size', b'2048x2048'), (b'quality', b'medium'), (b'stream', b'true'), (b'prompt', b'a scene')):
            self.assertIn(b'name="' + field + b'"\r\n\r\n' + value + b'\r\n', sent)
        self.assertNotIn(b'input_fidelity', sent)             # the model refuses the parameter

    def test_a_plain_json_answer_is_read_too(self):
        picture = png((300, 400))
        made, _ = self.call(Response(json.dumps({'data': [{'b64_json': base64.b64encode(picture).decode()}]}).encode(), 'application/json'))
        self.assertEqual(made['size'], [300, 400])

    def test_anything_that_is_not_a_finished_png_is_an_error_with_a_safe_message(self):
        jpeg = io.BytesIO(); Image.new('RGB', (300, 300)).save(jpeg, format='JPEG')
        cases = [Response(events({'type': 'image_edit.partial_image', 'b64_json': 'AAAA'}), 'text/event-stream'),            # never completed
                 Response(events({'type': 'error', 'error': {'message': 'secret upstream text'}}), 'text/event-stream'),
                 Response(events({'type': 'image_edit.completed', 'b64_json': '!!not base64!!'}), 'text/event-stream'),
                 Response(events({'type': 'image_edit.completed', 'b64_json': base64.b64encode(jpeg.getvalue()).decode()}), 'text/event-stream'),
                 Response(b'{"data": []}', 'application/json'), Response(b'<html>', 'application/json')]
        for result in cases:
            with self.subTest(result=result.stream.getvalue()[:40]), self.assertRaises(scene_imagegen.ImageError) as raised:
                self.call(result)
            self.assertNotIn('secret', str(raised.exception))

    def http_error(self, code, body):
        return urllib.error.HTTPError('https://api.hai.network/openai/v1/images/edits', code, 'x', {}, io.BytesIO(json.dumps(body).encode()))

    def test_http_failures_are_explained_without_repeating_what_the_service_said(self):
        for code, body, expected in ((400, {'error': {'code': 'moderation_blocked', 'message': 'the prompt text'}}, '内容审核'),
                                     (402, {'message': 'pay up'}, '额度'), (403, {'error': {'message': 'key sk-123'}}, '模型权限'), (500, {}, '暂时不可用')):
            with self.subTest(code=code), self.assertRaises(scene_imagegen.ImageError) as raised:
                self.call(self.http_error(code, body))
            message = str(raised.exception)
            self.assertIn(expected, message); self.assertNotIn('sk-123', message); self.assertNotIn('prompt text', message)

    def test_a_request_can_be_cancelled_and_needs_a_usable_key_and_references(self):
        body = events({'type': 'image_edit.completed', 'b64_json': base64.b64encode(png((512, 512))).decode()})
        with self.assertRaises(InterruptedError):
            self.call(Response(body, 'text/event-stream'), cancelled=lambda: True)
        with patch.object(scene_imagegen.urllib.request, 'build_opener') as opener:
            for value, images in (({**IMAGE, 'api_key': 'has space'}, self.references), (IMAGE, []), (IMAGE, [self.folder / 'missing.png'])):
                with self.assertRaises(scene_imagegen.ImageError):
                    scene_imagegen.edit(value, 'a scene', images, (1024, 1024))
            opener.assert_not_called()

    def test_the_request_names_what_to_keep_and_carries_no_publisher_names(self):
        text = scene_imagegen.prompt('rolling tool cabinet', 738, 3, 'A Bosch-style jobsite. An installer opens the drawer.', '4:3',
                                     ['top surface is black, should be light brushed steel'])
        self.assertIn('THE PRODUCT is a rolling tool cabinet. Its largest dimension is about 74 cm.', text)
        self.assertIn('Reproduce this product exactly as rendered', text); self.assertIn('landscape 4:3', text)
        self.assertIn('CORRECTIONS.', text); self.assertIn('light brushed steel', text)
        self.assertNotIn('Bosch', text)
        single = scene_imagegen.prompt('small bottle', None, 1, '', '1:1')
        self.assertIn('The attached image is a render of it.', single); self.assertNotIn('largest dimension', single); self.assertNotIn('CORRECTIONS', single)

    def test_sizes_follow_the_plan_and_views_cover_the_other_sides(self):
        self.assertEqual([scene_imagegen.size_for(aspect, 'gpt-image-2') for aspect in ('1:1', '4:3', '3:4')], [(2048, 2048), (2048, 1536), (1536, 2048)])
        self.assertEqual(scene_imagegen.size_for('4:3', 'gpt-image-1'), (1536, 1024))
        self.assertEqual(scene_imagegen.angles(35, 18), [(35.0, 18.0), (-35.0, 18.0), (17.5, 48.0)])
        self.assertEqual(scene_imagegen.angles(5, 50), [(5.0, 50.0), (75.0, 50.0), (2.5, 62.0)])       # a frontal view still gets a second side


class FidelityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.references = [self.folder / 'a.png', self.folder / 'b.png']
        for path in self.references: path.write_bytes(png())
        self.generated = self.folder / 'made.png'; self.generated.write_bytes(png((256, 256)))
        self.vision = {'provider': 'anthropic', 'base_url': 'https://api.hai.network/anthropic', 'model': 'claude-sonnet-4-6', 'api_key': 'k', 'enabled': True}

    def verdict(self, **changes):
        value = {'product_present': True, 'shape_consistent': True, 'parts_consistent': True, 'colours_consistent': True, 'no_added_text': True,
                 'issues': [], 'note': '一致。'}
        value.update(changes)
        return value

    def check(self, reply):
        sent = {}
        def post(config, body):
            sent['body'] = body
            if isinstance(reply, Exception): raise reply
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch', 'input': reply}]}
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            return scene_imagegen.check(self.references, self.generated, 'rolling tool cabinet'), sent.get('body')

    def test_the_picture_is_shown_to_the_vision_model_next_to_every_render(self):
        result, body = self.check(self.verdict())
        self.assertEqual((result['status'], result['issues']), ('passed', []))
        content = body['messages'][-1]['content']
        self.assertEqual([block['type'] for block in content], ['text', 'image', 'text', 'image', 'text', 'image', 'text'])
        self.assertEqual([block['text'][:11] for block in content if block['type'] == 'text'][:3], ['REFERENCE 1', 'REFERENCE 2', 'GENERATED: '])

    def test_any_difference_fails_and_is_named(self):
        result, _ = self.check(self.verdict(colours_consistent=False, issues=['top surface is black, should be light brushed steel'], note='台面颜色不符。'))
        self.assertEqual(result['status'], 'failed'); self.assertEqual(result['issues'], ['top surface is black, should be light brushed steel'])
        self.assertFalse(result['flags']['colours_consistent'])

    def test_a_check_that_cannot_run_keeps_the_picture_and_says_it_was_not_checked(self):
        self.assertEqual(self.check(ValueError('AI 请求超时'))[0]['status'], 'unchecked')
        self.assertEqual(self.check({'product_present': True})[0]['status'], 'unchecked')                # not the agreed answer
        with patch.object(ai_config, 'get_vision_config', return_value={**self.vision, 'api_key': ''}), patch('ai_client._post') as post:
            self.assertEqual(scene_imagegen.check(self.references, self.generated, 'x')['status'], 'unchecked')
            post.assert_not_called()


class CreateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for module in (store, engine, scene_imagegen):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        self.rows = []
        for index, (azimuth, elevation) in enumerate(scene_imagegen.angles(35, 18)):
            path = self.data / 'fixture' / 'refs' / ('ref%d.png' % index); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(png())
            self.rows.append({'path': path, 'azimuth': azimuth, 'elevation': elevation, 'cached': False})
        self.prompts = []; self.verdicts = []
        for target, name, effect in ((scene_imagegen, 'config', lambda: IMAGE), (scene_imagegen, 'references', lambda *a, **k: self.rows),
                                     (scene_imagegen, 'edit', self.edit), (scene_imagegen, 'check', lambda *a, **k: self.verdicts.pop(0))):
            mocked = patch.object(target, name, side_effect=effect); mocked.start(); self.addCleanup(mocked.stop)

    def edit(self, value, text, images, size, cancelled=None, progress=None):
        self.prompts.append(text)
        return {'png': png(tuple(size), (len(self.prompts) * 40, 0, 0)), 'size': list(size), 'usage': {'total_tokens': 100}}

    def create(self, aspect='1:1'):
        record = project(); record['plan']['aspect'] = aspect
        return scene_imagegen.create(record, record['plan'], 35, 18, 'A jobsite.', 'rolling tool cabinet', 738, self.data / 'fixture' / 'scene_ai' / 't1' / 'scene')

    def test_a_picture_that_matches_is_kept_as_png_and_jpg_with_its_record(self):
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        made = self.create('4:3')
        self.assertEqual((made['width'], made['height'], made['png'].name, made['jpg'].name), (2048, 1536, 'scene.png', 'scene.jpg'))
        with Image.open(made['jpg']) as picture: self.assertEqual((picture.format, picture.size), ('JPEG', (2048, 1536)))
        record = made['record']
        self.assertEqual((record['model'], record['attempts'], record['check']['status'], record['usage']), ('gpt-image-2', 1, 'passed', {'total_tokens': 100}))
        self.assertEqual([row['azimuth'] for row in record['references']], [35.0, -35.0, 17.5])
        self.assertTrue(all(row['url'].startswith('/files/fixture/') for row in record['references']))
        self.assertEqual(len(self.prompts), 1); self.assertNotIn('CORRECTIONS', self.prompts[0])

    def test_a_changed_product_is_made_once_more_with_the_differences_named(self):
        self.verdicts = [{'status': 'failed', 'issues': ['top surface is black'], 'note': '不一致。'}, {'status': 'passed', 'issues': [], 'note': '一致。'}]
        made = self.create()
        self.assertEqual((made['record']['attempts'], made['record']['check']['status']), (2, 'passed'))
        self.assertIn('CORRECTIONS.', self.prompts[1]); self.assertIn('top surface is black', self.prompts[1])
        self.assertEqual(made['record']['discarded'][0]['issues'], ['top surface is black'])
        self.assertEqual(made['png'].read_bytes(), png((2048, 2048), (80, 0, 0)))            # the second picture is the one kept
        self.assertEqual(made['record']['usage'], {'total_tokens': 200})

    def test_there_is_one_correction_only_and_the_closer_picture_is_kept_with_its_differences(self):
        self.verdicts = [{'status': 'failed', 'issues': ['one'], 'note': 'a'}, {'status': 'failed', 'issues': ['one', 'two'], 'note': 'b'}]
        made = self.create()
        self.assertEqual(len(self.prompts), scene_imagegen.MAX_ATTEMPTS)
        self.assertEqual((made['record']['check']['status'], made['record']['check']['issues']), ('failed', ['one']))
        self.assertEqual(made['png'].read_bytes(), png((2048, 2048), (40, 0, 0)))

    def test_an_unchecked_picture_is_not_regenerated(self):
        self.verdicts = [{'status': 'unchecked', 'issues': [], 'note': '未核对。'}]
        self.assertEqual(self.create()['record']['attempts'], 1)

    def test_no_image_model_means_no_picture(self):
        with patch.object(scene_imagegen, 'config', return_value=None), self.assertRaises(scene_imagegen.ImageError):
            self.create()
        self.assertEqual(self.prompts, [])


class StrategistTests(unittest.TestCase):
    def propose(self, reply=None, image=True, **options):
        record = options.pop('record', None) or project()
        asked = {}
        def ask(config, system, facts, schema, image_path=None):
            asked.update(facts=facts, schema=schema, system=system)
            if isinstance(reply, Exception): raise reply
            return reply
        with patch.object(scene_stylist, '_config', return_value={'provider': 'anthropic'} if reply is not None else None), \
                patch.object(scene_stylist, '_ask', side_effect=ask), \
                patch.object(scene_imagegen, 'config', return_value=IMAGE if image else None):
            return scene_stylist.propose(record, **options), asked

    def test_the_model_reads_every_segment_and_reference_and_its_plan_is_kept(self):
        proposal, asked = self.propose(answer())
        self.assertEqual(len(asked['facts']['segments']), 8); self.assertEqual(len(asked['facts']['segments'][4]['references']), 10)
        self.assertEqual(asked['facts']['segments'][4]['buying_question'], '如何运输、配置和管理')
        self.assertEqual(asked['schema']['properties']['segment_id']['enum'][-1], '')
        self.assertEqual((proposal['segment_id'], proposal['reference_id'], proposal['source'], proposal['method']), ('site_team', '05-06', 'ai', 'generate'))
        self.assertEqual((proposal['azimuth'], proposal['elevation'], proposal['version'], proposal['audience']), (30.0, 20.0, 1, 'auto'))

    def test_people_or_a_real_place_go_to_the_image_model_and_everything_else_is_built_in_3d(self):
        self.assertEqual(self.propose(answer(people=False, complex=False))[0]['method'], 'render')
        self.assertEqual(self.propose(answer(people=True, complex=False))[0]['method'], 'generate')
        self.assertEqual(self.propose(answer(people=False, complex=True))[0]['method'], 'generate')
        without, _ = self.propose(answer(), image=False)
        self.assertEqual(without['method'], 'render'); self.assertIn('图像模型未配置', without['method_note']); self.assertFalse(without['image_available'])

    def test_the_users_own_choice_of_method_wins_where_it_can(self):
        proposal, _ = self.propose(answer())
        with patch.object(scene_imagegen, 'config', return_value=IMAGE):
            self.assertEqual(scene_stylist.with_method(proposal, 'render')['method'], 'render')
            self.assertEqual(scene_stylist.with_method({**proposal, 'people': False, 'complex': False}, 'generate')['method'], 'generate')
        with patch.object(scene_imagegen, 'config', return_value=None), self.assertRaisesRegex(ValueError, '图像模型未配置'):
            scene_stylist.with_method(proposal, 'generate')

    def test_the_users_segment_and_reference_are_binding(self):
        proposal, asked = self.propose(answer(segment_id='site_team', reference_id='05-06'), audience='home_garden', reference='07-02')
        self.assertEqual((asked['facts']['required_segment'], asked['facts']['required_reference']), ('home_garden', '07-02'))
        self.assertEqual((proposal['segment_id'], proposal['reference_id'], proposal['audience']), ('home_garden', '07-02', 'home_garden'))
        self.assertEqual(scene_stylist._brief(proposal, 'generate')['source'], 'user')
        for options in ({'audience': 'nobody'}, {'audience': 'home_garden', 'reference': '05-06'}, {'reference': '05-06'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.propose(answer(), **options)

    def test_what_the_model_says_is_bounded_and_cleaned(self):
        proposal, _ = self.propose(answer(reference_id='07-01', azimuth=170, elevation=-5, scene='A Bosch jobsite.   The tool cabinet stands at the centre of the frame.'))
        self.assertIsNone(proposal['reference_id'])                       # a reference of another segment is dropped
        self.assertEqual((proposal['azimuth'], proposal['elevation']), (70.0, 8.0))
        self.assertNotIn('Bosch', proposal['scene']); self.assertNotIn('  ', proposal['scene'])
        none, _ = self.propose(answer(segment_id='', reference_id=''), record=project('哑光按压瓶'))
        self.assertEqual((none['segment_id'], none['reference_id']), (None, None))

    def test_rules_answer_when_there_is_no_model_or_it_fails(self):
        for reply in (None, ValueError('AI 请求超时')):
            with self.subTest(reply=reply):
                proposal, _ = self.propose(reply)
                self.assertEqual((proposal['segment_id'], proposal['source'], proposal['method'], proposal['people']), ('site_team', 'rules', 'render', False))
                self.assertTrue(proposal['scene'])          # the segment's stock scene, should the user ask for a generated picture
        bottle, _ = self.propose(None, record=project('哑光按压瓶'))
        self.assertEqual((bottle['segment_id'], bottle['method']), (None, 'render')); self.assertIn('没有判断出', bottle['reason'])

    def test_the_director_is_told_who_the_picture_is_for(self):
        proposal, _ = self.propose(answer())
        seen = {}
        def ask(config, system, facts, schema, image_path=None):
            seen.update(facts); raise ValueError('stop here')
        with patch.object(scene_stylist, '_config', return_value={'provider': 'anthropic'}), patch.object(scene_stylist, '_ask', side_effect=ask):
            scene_stylist.direct(project(), '', None, MODELS, proposal)
        self.assertEqual(seen['audience']['buyer'], '小型施工与安装团队'); self.assertEqual(seen['planned_picture'], proposal['summary'])
        self.assertNotIn('EXAL', json.dumps(seen['audience'], ensure_ascii=False))


class SceneFlowTests(unittest.TestCase):
    """The task from request to saved plan, with the image model and Blender replaced."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for module in (store, engine, scene_props, scene_stylist, scene_imagegen):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        for name, value in [('QUEUE', queue.Queue()), ('RUNNING_JOBS', set()), ('PROCESSES', {})]:
            mocked = patch.object(engine, name, value); mocked.start(); self.addCleanup(mocked.stop)
        for target, name, value in ((engine, 'require_runtime', None), (engine, 'blender_path', '/usr/bin/blender'),
                                    (scene_stylist, '_config', {'provider': 'anthropic'}), (scene_imagegen, 'config', IMAGE)):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project', project())
        self.reply = answer(); self.asked = 0; self.created = []
        mocked = patch.object(scene_stylist, '_ask', side_effect=self.ask); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_imagegen, 'create', side_effect=self.create); mocked.start(); self.addCleanup(mocked.stop)
        scene_props._MEMORY.clear(); self.addCleanup(scene_props._MEMORY.clear)

    def ask(self, config, system, facts, schema, image_path=None):
        if system is not scene_stylist.STRATEGIST_PROMPT: raise ValueError('only the strategist answers here')
        self.asked += 1
        return {**self.reply, **({'segment_id': facts['required_segment']} if 'required_segment' in facts else {})}

    def create(self, record, plan, azimuth, elevation, scene, product, size_mm, folder, cancelled=None, progress=None):
        self.created.append({'azimuth': azimuth, 'elevation': elevation, 'scene': scene, 'product': product, 'size_mm': size_mm, 'plan': copy.deepcopy(plan)})
        folder.mkdir(parents=True, exist_ok=True)
        picture = folder / 'scene.png'; picture.write_bytes(png((2048, 2048))); (folder / 'scene.jpg').write_bytes(b'jpeg')
        return {'png': picture, 'jpg': folder / 'scene.jpg', 'width': 2048, 'height': 2048,
                'record': {'model': 'gpt-image-2', 'quality': 'medium', 'size': [2048, 2048], 'attempts': 1, 'check': {'status': 'passed', 'issues': [], 'note': '一致。'},
                           'prompt': 'p', 'usage': {}, 'seconds': 1.0, 'references': [], 'discarded': []}}

    def run_task(self, **body):
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            task = scene_stylist.submit('fixture', {'expected_version': store.get('project', 'fixture')['version'], **body})
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        return store.get(scene_stylist.KIND, task['id'])

    def test_a_scene_with_people_becomes_the_scene_shots_image_in_one_undoable_version(self):
        task = self.run_task()
        self.assertEqual((task['status'], task['stage'], task['job_id']), ('completed', '场景图已生成', None), task.get('error'))
        saved = store.get('project', 'fixture'); plan = saved['plan']
        self.assertEqual((saved['version'], task['applied_version'], saved['history'][-1]['reason']), (2, 2, '生成使用场景'))
        self.assertEqual(plan['shots'][-1]['id'], 'scene'); self.assertEqual((plan['shots'][-1]['azimuth'], plan['shots'][-1]['elevation']), (30.0, 20.0))
        self.assertEqual((plan['scene_brief']['segment_id'], plan['scene_brief']['reference_id'], plan['scene_brief']['method'], plan['scene_brief']['source']),
                         ('site_team', '05-06', 'generate', 'ai'))
        self.assertNotIn('scene_set', plan)
        output = saved['outputs'][-1]
        self.assertEqual((output['shot_id'], output['quality'], output['version'], output['width'], output['blend_url']), ('scene', 'generated', 2, 2048, None))
        self.assertTrue(output['png_url'].startswith('/files/fixture/scene_ai/' + task['id'] + '/'))
        self.assertEqual(output['generated']['check']['status'], 'passed')
        self.assertEqual((task['result']['method'], task['result']['audience']['label']), ('generate', '小型施工与安装团队'))
        made = self.created[0]
        self.assertEqual((made['product'], made['size_mm'], made['azimuth']), ('rolling tool cabinet', 740.0, 30.0))
        self.assertNotIn('scene', [shot['id'] for shot in made['plan']['shots']])        # the renders show the product alone
        self.assertEqual(store.all_records('job'), [])                                   # nothing is queued for Blender

    def test_blender_is_never_asked_to_render_a_generated_shot(self):
        self.run_task()
        with self.assertRaisesRegex(ValueError, '由图像模型生成'):
            engine.submit('fixture', 'preview', 'preview', ['scene'])
        job = engine.submit('fixture', 'render', 'standard', None)
        self.assertNotIn('scene', job['shot_ids']); self.assertIn('hero', job['shot_ids'])
        engine.cancel(job['id'])
        mixed = engine.submit('fixture', 'preview', 'preview', ['hero', 'scene'], request_key='k1', expected_version=2)
        self.assertEqual(mixed['shot_ids'], ['hero'])
        self.assertEqual(engine.submit('fixture', 'preview', 'preview', ['hero', 'scene'], request_key='k1', expected_version=2)['id'], mixed['id'])

    def test_the_proposal_the_user_saw_is_the_one_that_is_made(self):
        shown = scene_stylist.recommend('fixture', {'hint': '工地'})
        self.assertEqual((self.asked, shown['method'], store.get('project', 'fixture')['scene_proposal']['id']), (1, 'generate', shown['id']))
        self.run_task(hint='工地')
        self.assertEqual(self.asked, 1)                                   # not asked again
        self.run_task(hint='车间')                                         # other words, or another plan version: asked again
        self.assertEqual(self.asked, 2)

    def test_changing_the_audience_or_method_changes_what_is_made(self):
        task = self.run_task(audience='home_garden', method='generate')
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((brief['segment_id'], brief['source'], task['result']['audience']['label']), ('home_garden', 'user', '家庭园艺与庭院维护者'))
        with patch.object(scene_imagegen, 'config', return_value=None):
            for body in ({'method': 'generate'}, {'audience': 'nobody'}, {'audience': 'auto', 'reference': '05-06'}, {'method': 'paint'}):
                with self.subTest(body=body), self.assertRaises(ValueError):
                    scene_stylist.submit('fixture', {'expected_version': 2, **body})

    def test_a_failed_generation_changes_nothing(self):
        with patch.object(scene_imagegen, 'create', side_effect=scene_imagegen.ImageError('图像服务的额度已用尽或账户欠费，请充值后重试。（HTTP 402）')):
            task = self.run_task()
        self.assertEqual(task['status'], 'failed'); self.assertIn('额度', task['error'])
        saved = store.get('project', 'fixture')
        self.assertEqual((saved['version'], saved['outputs']), (1, [])); self.assertNotIn('scene_brief', saved['plan'])

    def test_the_agents_are_shown_the_product_never_a_generated_scene(self):
        folder = self.data / 'fixture' / 'runs'; folder.mkdir(parents=True)
        def output(name, shot, version, quality, at):
            (folder / name).write_bytes(png())
            return {'shot_id': shot, 'version': version, 'quality': quality, 'created_at': at, 'png_url': '/files/fixture/runs/' + name}
        record = store.get('project', 'fixture'); record['version'] = 3
        record['outputs'] = [output('hero1.png', 'hero', 1, 'preview', 1), output('side2.png', 'side', 2, 'preview', 2),
                             output('made3.png', 'scene', 3, 'generated', 3), output('thumb3.png', 'hero', 3, 'thumbnail', 4)]
        self.assertEqual(scene_stylist._source_image(record).name, 'hero1.png')        # the last main image, not the generated scene
        record['outputs'].append(output('side3.png', 'side', 3, 'preview', 5))
        self.assertEqual(scene_stylist._source_image(record).name, 'side3.png')        # a render of the current plan comes first
        record['outputs'].append(output('hero3.png', 'hero', 3, 'showcase', 6))
        self.assertEqual(scene_stylist._source_image(record).name, 'hero3.png')
        record['outputs'] = [output('made3.png', 'scene', 3, 'generated', 3)]
        self.assertIsNone(scene_stylist._source_image(record))

    def test_removing_the_scene_takes_the_brief_with_it_and_the_picture_stays_on_disk(self):
        self.run_task()
        before = store.get('project', 'fixture')
        removed = scene_stylist.remove('fixture', 2)
        self.assertEqual(removed['version'], 3); self.assertNotIn('scene_brief', removed['plan'])
        self.assertNotIn('scene', [shot['id'] for shot in removed['plan']['shots']])
        self.assertTrue((self.data / before['outputs'][-1]['png_url'][7:]).is_file())
        engine.cancel(engine.submit('fixture', 'preview', 'preview', None)['id'])      # every remaining shot renders again

    def test_the_web_layer_serves_the_pack_the_proposal_and_the_task(self):
        import app as application
        client = application.app.test_client()
        listing = client.get('/api/scene/audiences').get_json()
        self.assertEqual((len(listing['segments']), listing['pack']['count']), (8, 80)); self.assertNotIn('api_key', listing['image'])
        self.assertEqual(client.get('/references/eu-tool-ecommerce/index.html').status_code, 200)
        self.assertEqual(client.get('/references/eu-tool-ecommerce/references.csv').status_code, 404)
        self.assertEqual(client.get('/references/other/index.html').status_code, 404)
        proposal = client.post('/api/projects/fixture/scene/recommend', json={'hint': ''}).get_json()
        self.assertEqual((proposal['segment_id'], proposal['method']), ('site_team', 'generate'))
        self.assertEqual(client.post('/api/projects/fixture/scene/recommend', json={'audience': 'nobody'}).status_code, 400)
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            self.assertEqual(client.post('/api/projects/fixture/scene', json={'expected_version': 1}).status_code, 202)
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        view = client.get('/api/projects/fixture').get_json()
        self.assertEqual((view['scene_task']['status'], view['plan']['scene_brief']['method'], view['outputs'][-1]['quality']), ('completed', 'generate', 'generated'))
        self.assertEqual(client.get('/api/projects/fixture/scene').get_json()['scene_brief']['segment_id'], 'site_team')
        with patch.object(application, 'DATA', self.data):           # the picture is served like any render of the project
            self.assertEqual(client.get(view['outputs'][-1]['png_url']).status_code, 200)


if __name__ == '__main__':
    unittest.main()

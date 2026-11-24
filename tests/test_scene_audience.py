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

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import ai_config
import engine
import planner
import scene_audience
import scene_imagegen
import scene_lock
import scene_pose
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
        # A gateway that gave up is told apart from the rest, so the caller can try a quicker picture.
        for code, kind in ((502, True), (504, True), (500, False), (429, False)):
            with self.subTest(code=code), self.assertRaises(scene_imagegen.ImageError) as raised:
                self.call(self.http_error(code, {}))
            self.assertIs(isinstance(raised.exception, scene_imagegen.GatewayError), kind)

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
        text = scene_imagegen.prompt('rolling tool cabinet', 738, 'A Bosch-style jobsite. An installer opens the drawer.', '4:3',
                                     ['top surface is black, should be light brushed steel'])
        self.assertIn('THE PRODUCT is a rolling tool cabinet. Its largest dimension is about 74 cm', text)
        # The product is already in the picture: it is kept, not drawn; everything else is painted round it.
        for phrase in ('It is already in image 1, in its final place', 'It is finished and fixed', 'Do not move, turn, resize, redraw',
                       'PAINT EVERYTHING ELSE', 'Nothing of the grey ground may remain', 'the shadow it casts'):
            self.assertIn(phrase, text)
        self.assertIn('landscape 4:3', text)
        self.assertIn('CORRECTIONS.', text); self.assertIn('light brushed steel', text)
        self.assertNotIn('Bosch', text)
        single = scene_imagegen.prompt('small bottle', None, '', '1:1')
        self.assertNotIn('largest dimension', single); self.assertNotIn('CORRECTIONS', single)
        self.assertNotIn('PEOPLE.', single)                    # nothing was asked: the scene description decides

    def test_the_request_says_whether_a_person_is_in_the_picture(self):
        scene = 'A workshop bench by a window.'
        wanted = scene_imagegen.prompt('angle grinder', 300, scene, '1:1', people=True)
        self.assertIn('PEOPLE. The picture must show a person actually using or handling the product', wanted)
        self.assertLess(wanted.index('THE SCENE.'), wanted.index('PEOPLE.')); self.assertLess(wanted.index('PEOPLE.'), wanted.index('RULES.'))
        barred = scene_imagegen.prompt('angle grinder', 300, scene, '1:1', people=False)
        self.assertIn('PEOPLE. Nobody is in the picture', barred); self.assertNotIn('People are ordinary adults', barred)
        # With no scene of its own, the stock scene follows the choice too.
        self.assertIn('with a person using it', scene_imagegen.prompt('angle grinder', 300, '', '1:1', people=True))
        self.assertNotIn('person using it', scene_imagegen.prompt('angle grinder', 300, '', '1:1', people=False))
        # Hands at work, close up: the tool, the hands and what it works on; no face and no whole figure.
        hands = scene_imagegen.prompt('pliers', 180, 'An electrician in a hallway strips a cable.', '1:1', people=True, framing='hands')
        for phrase in ('close-up of the product at work', 'hold and operate the product where a user grips it', 'at the point of contact',
                       'never change its shape',
                       'no face, no head and no whole figure', 'overrides any wider view', 'bare or in plain work gloves',
                       'The frame of image 1 is final: do not zoom in, crop'):
            self.assertIn(phrase, hands)
        self.assertNotIn('Crop tight', hands)                 # the anchored canvas already sets the frame; a tighter crop moves the product
        self.assertNotIn('arms and upper body in the frame', hands); self.assertNotIn('People are ordinary adults', hands)
        self.assertIn('arms and upper body in the frame', scene_imagegen.prompt('pliers', 180, '', '1:1', people=True, framing='person'))
        self.assertNotIn('close-up of the product at work', scene_imagegen.prompt('pliers', 180, '', '1:1', people=False, framing='hands'))

    def test_sizes_follow_the_plan_and_views_cover_the_other_sides(self):
        self.assertEqual([scene_imagegen.size_for(aspect, 'gpt-image-2') for aspect in ('1:1', '4:3', '3:4')], [(1536, 1536), (1536, 1152), (1152, 1536)])
        # The pages are 1500 px: a scene is asked for no larger than that needs (2048 px cost 3568 output tokens, 1536 px about 2511).
        for width, height in scene_imagegen.SIZES.values():
            self.assertLessEqual(max(width, height), 1536); self.assertGreaterEqual(min(width, height), 1152)
            self.assertEqual((width % 16, height % 16), (0, 0))
        self.assertEqual(scene_imagegen.size_for('4:3', 'gpt-image-1'), (1536, 1024))
        self.assertEqual(scene_imagegen.angles(35, 18), [(30.0, 20.0), (-30.0, 20.0), (15.0, 50.0)])
        self.assertEqual(scene_imagegen.angles(5, 50), [(0.0, 50.0), (75.0, 50.0), (0.0, 60.0)])       # a frontal view still gets a second side
        # Cameras a few degrees apart share their reference renders: rendering them is the longest step of a generated scene.
        self.assertEqual(scene_imagegen.angles(30, 15), scene_imagegen.angles(35, 18)); self.assertEqual(scene_imagegen.angles(33.4, 22), scene_imagegen.angles(35, 18))
        self.assertNotEqual(scene_imagegen.angles(50, 18), scene_imagegen.angles(35, 18))
        self.assertEqual(scene_imagegen.angles(-40, 2)[0], (-45.0, 10.0))                               # never level with the floor


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
                 'nothing_attached': True, 'held_correctly': True, 'contact_plausible': True, 'used_correctly': True, 'rests_stably': True,
                 'scale_plausible': True, 'installed_correctly': True, 'surroundings_new': True, 'person_visible': False, 'face_visible': False, 'issues': [], 'note': '一致。'}
        value.update(changes)
        return value

    def check(self, reply, people=None, framing=None):
        sent = {}
        def post(config, body):
            sent['body'] = body
            if isinstance(reply, Exception): raise reply
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch', 'input': reply}]}
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            return scene_imagegen.check(self.references, self.generated, 'rolling tool cabinet', people, framing), sent.get('body')

    def test_the_picture_is_shown_to_the_vision_model_next_to_every_render(self):
        result, body = self.check(self.verdict())
        self.assertEqual((result['status'], result['issues']), ('passed', []))
        content = body['messages'][-1]['content']
        self.assertEqual([block['type'] for block in content], ['text', 'image', 'text', 'image', 'text', 'image', 'text'])
        self.assertEqual([block['text'][:11] for block in content if block['type'] == 'text'][:3], ['REFERENCE 1', 'REFERENCE 2', 'GENERATED: '])

    def test_the_products_usage_rules_are_checked(self):
        sent = {}
        def post(config, body):
            sent['body'] = body
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch',
                                                            'input': self.verdict(used_correctly=False, issues=['beam tilted'], note='倾斜。')}]}
        rules = ['jaws close squarely on both faces', 'beam perpendicular to the measured faces']
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            result = scene_imagegen.check(self.references, self.generated, 'digital caliper', usage=rules)
        self.assertEqual(result['status'], 'failed'); self.assertFalse(result['flags']['used_correctly'])
        self.assertIn('beam perpendicular to the measured faces', json.dumps(sent['body'], ensure_ascii=False))

    def test_the_prompt_sets_the_moving_parts_and_the_way_of_use(self):
        text = scene_imagegen.prompt('digital caliper', 150, 'A workshop bench.', people=True, framing='hands',
                                     usage=['jaws close squarely on both faces of the workpiece'], setting='Measuring')
        self.assertIn('USE. Its moving parts are set as image 1 shows (Measuring)', text)
        self.assertIn('jaws close squarely on both faces of the workpiece', text)
        resting = scene_imagegen.prompt('digital caliper', 150, 'A bench.', people=False, usage=['jaws square on the work'])
        self.assertNotIn('USE.', resting)                       # nobody uses it in a resting picture

    def test_a_product_set_down_must_rest_stably_and_hand_tools_are_held_by_their_handles(self):
        # The user's pictures: pliers balanced on a jaw tip and a handle end; a gloved hand pinching the pliers' head.
        resting = scene_imagegen.prompt('diagonal cutting pliers', 160, 'A workbench.', people=False)
        self.assertIn(scene_imagegen.REST_RULE, resting)
        self.assertIn('never balanced on a tip, an edge, a jaw point or a handle end', resting)
        self.assertNotIn('standing ready for use', resting)
        held = scene_imagegen.prompt('diagonal cutting pliers', 160, 'A workbench.', people=True, framing='hands')
        self.assertNotIn(scene_imagegen.REST_RULE, held)
        self.assertIn('pliers and cutters by both handles with the fingers wrapped round them, never pinched at the head', held)
        self.assertIn('rests_stably', scene_imagegen.FIDELITY_FLAGS)
        self.assertIn('rests_stably', scene_imagegen.fidelity_schema()['required'])
        for words in ('never pinched at the head, the jaws or the pivot', 'a hammer by the lower half of its handle',
                      'balanced on a tip, an edge, a jaw point or a handle end', 'the nail\'s shank sits in the claw\'s slot'):
            self.assertIn(words, scene_imagegen.FIDELITY_PROMPT)

    def test_up_to_six_usage_rules_reach_the_prompt_and_the_check(self):
        rules = ['rule %d' % number for number in range(1, 9)]
        text = scene_imagegen.prompt('claw hammer', 330, 'A wall.', people=True, framing='hands', usage=rules)
        self.assertIn('rule 6', text); self.assertNotIn('rule 7', text)
        sent = {}
        def post(config, body):
            sent['body'] = body
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch', 'input': self.verdict()}]}
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            scene_imagegen.check(self.references, self.generated, 'claw hammer', usage=rules)
        facts = json.loads(sent['body']['messages'][-1]['content'][-1]['text'])
        self.assertEqual(facts['usage_rules'], rules[:6])

    def test_any_difference_fails_and_is_named(self):
        result, _ = self.check(self.verdict(colours_consistent=False, issues=['top surface is black, should be light brushed steel'], note='台面颜色不符。'))
        self.assertEqual(result['status'], 'failed'); self.assertEqual(result['issues'], ['top surface is black, should be light brushed steel'])
        self.assertFalse(result['flags']['colours_consistent'])

    def test_a_wire_through_the_jaws_a_grip_that_is_not_there_or_a_backwards_hold_fails(self):
        # The defects users found in made scenes: each is a flag the vision model answers, and any one fails the picture.
        for flag, issue in (('contact_plausible', 'the wire passes through the steel of the jaws'), ('nothing_attached', 'an extra black grip on the handle'),
                            ('held_correctly', 'the hand holds the tool by its jaws'), ('scale_plausible', 'the pliers are as long as the van floor'),
                            ('used_correctly', 'the caliper is tilted across the board, its jaws on one edge only'),
                            ('rests_stably', 'the pliers stand on a jaw tip and a handle end')):
            result, body = self.check(self.verdict(**{flag: False, 'issues': [issue], 'note': '不一致。'}))
            self.assertEqual((result['status'], result['issues'], result['flags'][flag]), ('failed', [issue], False), flag)
        self.assertIn('size_mm', json.loads(body['messages'][-1]['content'][-1]['text']))

    def test_a_picture_without_the_person_that_was_asked_for_fails_and_so_does_an_unwanted_one(self):
        missing, _ = self.check(self.verdict(person_visible=False), people=True)
        self.assertEqual((missing['status'], missing['product_consistent'], missing['people']), ('failed', True, {'expected': True, 'visible': False, 'ok': False}))
        self.assertIn('no person is visible', missing['issues'][0]); self.assertIn('没有出现人物', missing['note'])
        unwanted, _ = self.check(self.verdict(person_visible=True), people=False)
        self.assertEqual((unwanted['status'], unwanted['people']['ok']), ('failed', False)); self.assertIn('remove every person', unwanted['issues'][0])
        self.assertEqual(self.check(self.verdict(person_visible=True), people=True)[0]['status'], 'passed')
        self.assertEqual(self.check(self.verdict(person_visible=False), people=False)[0]['status'], 'passed')
        # Nothing asked: who is in the picture is recorded, never a fault.
        for visible in (True, False):
            free, _ = self.check(self.verdict(person_visible=visible))
            self.assertEqual((free['status'], free['people']), ('passed', {'expected': None, 'visible': visible, 'ok': True}))
        # Both wrong: the person comes first, the product's differences follow.
        # Hands were asked for: hands alone pass; a face, or nobody at all, does not.
        hands, _ = self.check(self.verdict(person_visible=True), people=True, framing='hands')
        self.assertEqual((hands['status'], hands['people']), ('passed', {'expected': True, 'visible': True, 'ok': True, 'framing': 'hands', 'face_visible': False}))
        face, _ = self.check(self.verdict(person_visible=True, face_visible=True), people=True, framing='hands')
        self.assertEqual((face['status'], face['people']['ok'], face['product_consistent']), ('failed', False, True))
        self.assertIn('crop to a close-up of the hands', face['issues'][0]); self.assertIn('手部特写', face['note'])
        nobody, _ = self.check(self.verdict(person_visible=False), people=True, framing='hands')
        self.assertEqual(nobody['status'], 'failed'); self.assertIn('no hand is visible', nobody['issues'][0])
        self.assertEqual(self.check(self.verdict(person_visible=True, face_visible=True), people=True, framing='person')[0]['status'], 'passed')
        both, _ = self.check(self.verdict(person_visible=False, parts_consistent=False, issues=['one handle is missing']), people=True)
        self.assertEqual((both['status'], both['product_consistent'], len(both['issues']), both['issues'][1]), ('failed', False, 2, 'one handle is missing'))

    def test_a_worn_or_dirty_place_fails_the_picture_but_not_the_product(self):
        # Users found scenes on old, scored benches and in dirty vans: the place is checked too, and asked to be new.
        result, body = self.check(self.verdict(surroundings_new=False, issues=['the bench top is scored and stained'], note='背景陈旧。'))
        self.assertEqual((result['status'], result['product_consistent'], result['flags']['surroundings_new']), ('failed', True, False))
        self.assertEqual(result['issues'], ['the bench top is scored and stained', scene_imagegen.WORN_FIX])
        self.assertTrue(result['note'].startswith(scene_imagegen.WORN_NOTE))
        self.assertIn('surroundings_new', body['tools'][0]['input_schema']['required'])
        self.assertIn('- surroundings_new:', scene_imagegen.FIDELITY_PROMPT)
        # wrong in both the product and the place: the product is not consistent, and both are said
        both, _ = self.check(self.verdict(surroundings_new=False, parts_consistent=False, issues=['a wheel is missing']))
        self.assertEqual((both['product_consistent'], both['issues']), (False, ['a wheel is missing', scene_imagegen.WORN_FIX]))
        # five product issues: all of them stay ahead of the place's
        many, _ = self.check(self.verdict(surroundings_new=False, parts_consistent=False, issues=['issue %d' % n for n in range(5)]))
        self.assertEqual(many['issues'][:5], ['issue %d' % n for n in range(5)])

    def test_every_scene_asks_for_a_new_place_and_words_asking_for_wear_are_taken_out(self):
        for people in (None, False, True):
            text = scene_imagegen.prompt('tool cabinet', 900, 'An old, oily workbench in a weathered garage.', people=people)
            self.assertIn(scene_imagegen.NEW_PLACE, text)
            self.assertIn('THE SCENE. A workbench in a garage.', text)
        for said, meant in (('An old, oily workbench with a worn and dusty top by a weathered wall.', 'A workbench with a top by a wall.'),
                            ('a weathered, old oak table and an old anvil', 'An oak table and an anvil'),
                            ('The open rear of a well-used work van; rusty tools.', 'The open rear of a work van; tools.'),
                            ('A 40-year-old engineer holds it in a household garage.', 'A 40-year-old engineer holds it in a household garage.'),
                            ('Hold the bold golden handle.', 'Hold the bold golden handle.'),
                            # a negated wear word already asks for a clean place: kept as written
                            ('no dusty or oily surfaces anywhere', 'no dusty or oily surfaces anywhere'),
                            ('never cluttered, a tidy van', 'never cluttered, a tidy van'),
                            ('A bright new workshop, never worn or dirty.', 'A bright new workshop, never worn or dirty.'),
                            ('a clean bench, not dirty or greasy, and an old vise', 'A clean bench, not dirty or greasy, and a vise'),
                            # text without wear words comes back untouched
                            ('plain text. lower start', 'plain text. lower start'),
                            ('AN OLD BENCH', 'A BENCH')):
            self.assertEqual(scene_imagegen.fresh(said), meant)

    def test_an_empty_place_is_looked_at_for_wear(self):
        sent = {}
        def post(config, body):
            sent['body'] = body
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch',
                                                            'input': {'new': False, 'issues': ['the bench top is scored']}}]}
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            self.assertEqual(scene_imagegen.place_check(self.generated), {'new': False, 'issues': ['the bench top is scored']})
        self.assertEqual(sum(block.get('type') == 'image' for block in sent['body']['messages'][-1]['content']), 1)
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=ValueError('down')):
            self.assertIsNone(scene_imagegen.place_check(self.generated))
        with patch.object(ai_config, 'get_vision_config', return_value={**self.vision, 'api_key': ''}):
            self.assertIsNone(scene_imagegen.place_check(self.generated))

    def test_a_wall_product_is_asked_to_hang_on_a_wall_and_fails_when_it_stands(self):
        text = scene_imagegen.prompt('wall-mounted tool cabinet', 850, 'A workshop.', people=False, mounting='wall')
        self.assertIn(scene_imagegen.WALL_RULE, text); self.assertNotIn(scene_imagegen.REST_RULE, text)
        self.assertIn('in front of a plain grey ground', text); self.assertIn('the wall it is mounted on', text)
        self.assertNotIn('lies or stands on its base', text)
        held = scene_imagegen.prompt('wall-mounted tool cabinet', 850, 'A workshop.', people=True, framing='person', mounting='wall')
        self.assertIn(scene_imagegen.WALL_RULE, held)
        standing = scene_imagegen.prompt('tool cart', 900, 'A workshop.', people=False)
        self.assertNotIn(scene_imagegen.WALL_RULE, standing); self.assertIn(scene_imagegen.REST_RULE, standing)
        self.assertNotIn('van', scene_imagegen.NEW_PLACE.lower())                 # no vehicle put in every scene's head
        sent = {}
        def post(config, body):
            sent['body'] = body
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch',
                                                            'input': self.verdict(installed_correctly=False, issues=['it stands on the floor'])}]}
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            result = scene_imagegen.check(self.references, self.generated, 'wall cabinet', False, mounting='wall')
        self.assertEqual((result['status'], result['product_consistent']), ('failed', False))
        self.assertEqual(result['issues'][:2], [scene_imagegen.INSTALL_FIX, 'it stands on the floor'])
        self.assertEqual(json.loads(sent['body']['messages'][-1]['content'][-1]['text'])['mounting'], 'wall')
        self.assertIn('installed_correctly', scene_imagegen.USAGE_FLAGS)          # gets the extra try a wrong use gets

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
        # The product render: a red block on transparent film, as blender/stage.py leaves it.
        self.cutout = self.data / 'fixture' / 'stage' / 'x' / 'picture.png'; self.cutout.parent.mkdir(parents=True, exist_ok=True)
        film = Image.new('RGBA', (400, 400), (0, 0, 0, 0)); film.paste((200, 30, 30, 255), (100, 120, 300, 280)); film.save(self.cutout)
        self.rendered = []; self.render_plans = []
        def product_render(project, plan, azimuth, elevation, size_mm, canvas, cancelled=None, progress=None):
            self.rendered.append((azimuth, elevation, tuple(canvas))); self.render_plans.append(copy.deepcopy(plan))
            return {'path': self.cutout, 'azimuth': azimuth, 'elevation': elevation, 'product_rect': [.25, .3, .75, .7]}
        self.prompts = []; self.verdicts = []; self.checked = []; self.framings = []; self.closeups = []; self.images = []; self.masks = []
        self.locks = []                                        # what the measurement says of each picture; 'kept' when not set
        def check(references, generated, product, people=None, framing=None, closeup=None, size_mm=None):
            self.checked.append(people); self.framings.append(framing); self.closeups.append(closeup)
            return self.verdicts.pop(0)
        def inspect(layer, picture):
            status = self.locks.pop(0) if self.locks else 'kept'
            return {'version': 1, 'status': status, 'score': .6 if status != 'moved' else .3, 'scale': 1.0, 'shift': [0, 0], 'covered': .1,
                    'changed': .2 if status == 'changed' else 0.0, 'regions': [[.6, .3, .7, .4]] if status == 'changed' else []}
        # The pose step asks a vision model about Blender renders: here it keeps the plan's pose unless a test says otherwise.
        self.posed = None; self.pose_calls = []
        def decide(project, plan, azimuth, elevation, scene, product, hold, cancelled=None, progress=None, mode='auto', hint=''):
            self.pose_calls.append({'hold': hold, 'mode': mode, 'hint': hint, 'scene': scene, 'view': (azimuth, elevation)})
            return self.posed or scene_pose.kept(plan.get('rotation', [0, 0, 0]), mode, 'default', '保持默认姿态。')
        for target, name, effect in ((scene_imagegen, 'config', lambda: IMAGE), (scene_imagegen, 'product_render', product_render),
                                     (scene_pose, 'decide', decide),
                                     (scene_imagegen, 'edit', self.edit), (scene_imagegen, 'check', check), (scene_lock, 'inspect', inspect)):
            mocked = patch.object(target, name, side_effect=effect); mocked.start(); self.addCleanup(mocked.stop)

    def edit(self, value, text, images, size, cancelled=None, progress=None, mask=None):
        self.prompts.append(text); self.images.append(list(images)); self.masks.append(mask)
        return {'png': png(tuple(size), (len(self.prompts) * 40, 0, 0)), 'size': list(size), 'usage': {'total_tokens': 100}}

    def create(self, aspect='1:1', **options):
        record = project(); record['plan']['aspect'] = aspect
        return scene_imagegen.create(record, record['plan'], 35, 18, 'A jobsite.', 'rolling tool cabinet', 738,
                                     self.data / 'fixture' / 'scene_ai' / 't1' / 'scene', **options)

    def test_a_picture_that_matches_is_kept_as_png_and_jpg_with_its_record(self):
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        made = self.create('4:3')
        self.assertEqual((made['width'], made['height'], made['png'].name, made['jpg'].name), (1536, 1152, 'scene.png', 'scene.jpg'))
        with Image.open(made['jpg']) as picture: self.assertEqual((picture.format, picture.size), ('JPEG', (1536, 1152)))
        record = made['record']
        self.assertEqual((record['model'], record['attempts'], record['check']['status'], record['usage']), ('gpt-image-2', 1, 'passed', {'total_tokens': 100}))
        self.assertEqual(record['references'], [{'url': '/files/fixture/stage/x/picture.png', 'azimuth': 35, 'elevation': 18}])
        self.assertEqual(self.rendered, [(35, 18, (1536, 1152))])                   # one render, from the scene's own camera
        self.assertEqual(len(self.prompts), 1); self.assertNotIn('CORRECTIONS', self.prompts[0])
        # The model gets one picture, the anchored canvas, and a mask that keeps the product.
        canvas = Path(self.images[0][0]); self.assertEqual(canvas.name, 'canvas.png')
        with Image.open(canvas) as picture:
            self.assertEqual(picture.size, (1536, 1152)); self.assertEqual(picture.getpixel((5, 5)), scene_lock.GROUND)
            self.assertEqual(picture.getpixel((768, 576))[:3], (200, 30, 30))        # the product, in the middle of the frame
        with Image.open(io.BytesIO(self.masks[0])) as mask:
            self.assertEqual((mask.size, mask.mode), ((1536, 1152), 'RGBA'))
            self.assertEqual((mask.getpixel((5, 5))[3], mask.getpixel((768, 576))[3]), (0, 255))       # painted round it, kept on it
        self.assertEqual((record['anchor']['mask'], record['anchor']['canvas_url']), (True, '/files/fixture/scene_ai/t1/scene/canvas.png'))
        self.assertTrue(record['anchor']['closeup_url'].endswith('/closeup1.jpg'))
        self.assertEqual(Path(self.closeups[0]).name, 'closeup1.jpg')                 # the vision model compares the product side by side
        self.assertEqual(record['check']['lock']['status'], 'kept')

    def test_when_nothing_passes_a_worn_place_beats_a_changed_product(self):
        worn = {'status': 'failed', 'product_consistent': True, 'flags': {'surroundings_new': False},
                'issues': ['the bench is scored', scene_imagegen.WORN_FIX], 'note': '背景陈旧。'}
        wrong = {'status': 'failed', 'product_consistent': False, 'flags': {'parts_consistent': False}, 'issues': ['a wheel is missing'], 'note': '少轮。'}
        self.verdicts = [worn, wrong]
        record = self.create()['record']
        self.assertEqual((record['attempts'], record['check']['issues']), (2, worn['issues']))
        self.assertIn('CORRECTIONS', self.prompts[1]); self.assertIn(scene_imagegen.WORN_FIX, self.prompts[1])

    def test_the_product_is_rendered_in_the_pose_chosen_for_the_scene_and_the_prompt_says_how_it_is_held(self):
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        self.posed = {**scene_pose.kept([0, 0, 0], 'auto', 'ai', '手持角磨机时机身水平。'), 'rotation': [0.0, 90.0, 35.0], 'changed': True,
                      'description': 'held level in both hands, its disc tilted down onto the steel plate', 'contact': 'the disc, at the lower left',
                      'work_side': 'below'}
        made = self.create(people=True, framing='hands', hint='grinding a weld')
        self.assertEqual(self.pose_calls, [{'hold': 'hands', 'mode': 'auto', 'hint': 'grinding a weld', 'scene': 'A jobsite.', 'view': (35, 18)}])
        self.assertEqual(self.render_plans[0]['rotation'], [0.0, 90.0, 35.0])          # the render, and so the locked product, take the new pose
        text = self.prompts[0]
        self.assertIn('THE POSE. The product in image 1 is already posed for this moment: held level in both hands', text)
        self.assertIn('It meets the work at the disc, at the lower left.', text); self.assertIn('the hands grip the product', text)
        self.assertLess(text.index('THE POSE.'), text.index('PAINT EVERYTHING ELSE.')); self.assertLess(text.index('PEOPLE.'), text.index('RULES.'))
        self.assertEqual(made['record']['pose']['rotation'], [0.0, 90.0, 35.0])
        # The work lies below the product: it moves up, leaving room under it for the workpiece.
        self.assertLess(made['record']['anchor']['box'][1], .3)
        self.assertIsNotNone(self.masks[0])                                             # still locked by the mask

    def test_the_tools_written_down_rules_come_before_its_own_and_reach_pose_prompt_and_check(self):
        seen = {}
        def decide(project, plan, azimuth, elevation, scene, product, hold, cancelled=None, progress=None, mode='auto', hint='', usage=None):
            seen['pose'] = usage
            return scene_pose.kept(plan.get('rotation', [0, 0, 0]), mode, 'default', '保持默认姿态。')
        def check(references, generated, product, people=None, framing=None, closeup=None, size_mm=None, usage=None):
            seen['check'] = usage
            return {'status': 'passed', 'issues': [], 'note': '一致。'}
        record = project()
        with patch.object(scene_pose, 'decide', side_effect=decide), patch.object(scene_imagegen, 'check', side_effect=check):
            scene_imagegen.create(record, record['plan'], 35, 18, 'Cutting a wire.', 'diagonal cutting pliers', 160,
                                  self.data / 'fixture' / 'scene_ai' / 't9' / 'scene', people=True, framing='hands',
                                  usage_rules=['grip the wire in the jaws'])
        rules = seen['check']
        self.assertEqual(rules, seen['pose'])
        self.assertTrue(rules[0].startswith('both handles are gripped in the palm'))
        self.assertIn('the wire sits deep in the jaws close to the pivot', rules[1])
        self.assertEqual(rules[-1], 'grip the wire in the jaws')                 # the agent's own rule follows
        self.assertIn('never at the jaw tips', self.prompts[0])
        # A product no rule is written for passes its own rules unchanged, and none at all stays none.
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        self.create()
        self.assertNotIn('USE.', self.prompts[-1])

    def test_a_product_kept_in_its_pose_is_placed_and_described_as_before(self):
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        made = self.create(people=False, pose='keep')
        self.assertEqual(self.pose_calls[0]['mode'], 'keep'); self.assertEqual(self.pose_calls[0]['hold'], 'nobody')
        self.assertNotIn('THE POSE.', self.prompts[0]); self.assertEqual(made['record']['pose']['changed'], False)
        self.assertEqual(self.render_plans[0]['rotation'], [0, 0, 0])

    def test_a_changed_product_is_made_once_more_with_the_differences_named(self):
        self.verdicts = [{'status': 'failed', 'issues': ['top surface is black'], 'note': '不一致。'}, {'status': 'passed', 'issues': [], 'note': '一致。'}]
        made = self.create()
        self.assertEqual((made['record']['attempts'], made['record']['check']['status']), (2, 'passed'))
        self.assertIn('CORRECTIONS.', self.prompts[1]); self.assertIn('top surface is black', self.prompts[1])
        self.assertEqual(made['record']['discarded'][0]['issues'], ['top surface is black'])
        self.assertEqual(made['png'].read_bytes(), png((1536, 1536), (80, 0, 0)))            # the second picture is the one kept
        self.assertEqual(made['record']['usage'], {'total_tokens': 200})

    def test_there_is_one_correction_only_and_the_closer_picture_is_kept_with_its_differences(self):
        self.verdicts = [{'status': 'failed', 'issues': ['one'], 'note': 'a'}, {'status': 'failed', 'issues': ['one', 'two'], 'note': 'b'}]
        made = self.create()
        self.assertEqual(len(self.prompts), scene_imagegen.MAX_ATTEMPTS)
        self.assertEqual((made['record']['check']['status'], made['record']['check']['issues']), ('failed', ['one']))
        self.assertEqual(made['png'].read_bytes(), png((1536, 1536), (40, 0, 0)))

    def test_a_wrong_grip_gets_one_try_more_but_a_changed_product_does_not(self):
        flags = lambda **bad: {name: bad.get(name, True) for name in scene_imagegen.FIDELITY_FLAGS}
        grip = {'status': 'failed', 'issues': ['held by one handle'], 'note': 'a', 'flags': flags(held_correctly=False, used_correctly=False)}
        fixed = {'status': 'passed', 'issues': [], 'note': 'ok', 'flags': flags()}
        self.verdicts = [grip, dict(grip), fixed]
        stages = []
        made = self.create(progress=stages.append)
        self.assertEqual((len(self.prompts), made['record']['check']['status'], made['record']['usage_retry']), (3, 'passed', scene_imagegen.USAGE_RETRY))
        self.assertIn('握持、用法或摆放还不对，按核对意见再试一次', stages)
        self.prompts.clear()
        shape = {'status': 'failed', 'issues': ['handle shape changed'], 'note': 'b', 'flags': flags(shape_consistent=False, held_correctly=False)}
        self.verdicts = [shape, dict(shape), fixed]
        self.create()
        self.assertEqual(len(self.prompts), scene_imagegen.MAX_ATTEMPTS)

    def test_a_picture_without_the_requested_person_is_made_once_more_and_told_why(self):
        missing = {'status': 'failed', 'issues': [scene_imagegen.PEOPLE_FIXES[True]], 'note': '没有人物。', 'product_consistent': True,
                   'people': {'expected': True, 'visible': False, 'ok': False}}
        found = {'status': 'passed', 'issues': [], 'note': '一致。', 'product_consistent': True, 'people': {'expected': True, 'visible': True, 'ok': True}}
        self.verdicts = [missing, found]
        stages = []
        made = self.create(people=True, progress=stages.append)
        self.assertEqual(self.checked, [True, True]); self.assertEqual((made['record']['attempts'], made['record']['people']), (2, True))
        self.assertIn('PEOPLE. The picture must show a person', self.prompts[0]); self.assertNotIn('CORRECTIONS', self.prompts[0])
        self.assertIn('CORRECTIONS.', self.prompts[1]); self.assertIn('no person is visible', self.prompts[1])
        self.assertIn('画面里没有人物，正在重新生成', stages)
        self.assertEqual(made['record']['check']['people']['visible'], True)

    def test_when_neither_picture_passes_the_one_that_got_the_people_right_is_kept(self):
        wrong_people = {'status': 'failed', 'issues': ['p'], 'note': 'a', 'product_consistent': True, 'people': {'expected': False, 'visible': True, 'ok': False}}
        wrong_product = {'status': 'failed', 'issues': ['one', 'two', 'three'], 'note': 'b', 'product_consistent': False,
                         'people': {'expected': False, 'visible': False, 'ok': True}}
        self.verdicts = [wrong_people, wrong_product]
        made = self.create(people=False)
        self.assertEqual(made['record']['check']['issues'], ['one', 'two', 'three'])
        self.assertIn('PEOPLE. Nobody is in the picture', self.prompts[0])

    def test_a_wall_product_keeps_its_pose_hangs_higher_and_is_put_back_from_its_render(self):
        seen = []
        def check(references, generated, product, people=None, framing=None, closeup=None, size_mm=None, mounting=None):
            seen.append(mounting); return {'status': 'passed', 'issues': [], 'note': '一致。'}
        def edit(value, text, images, size, cancelled=None, progress=None, mask=None):
            # the image model keeps the product where it is but paints its own version of it: another red, and bars down its middle
            self.prompts.append(text)
            with Image.open(images[0]) as canvas:
                picture = canvas.convert('RGB')
            pixels = picture.load(); width, height = picture.size
            product = [(x, y) for y in range(0, height, 2) for x in range(0, width, 2) if pixels[x, y] != scene_lock.GROUND]
            left = min(x for x, _ in product); right = max(x for x, _ in product); top = min(y for _, y in product); bottom = max(y for _, y in product)
            for y in range(height):
                for x in range(width):
                    pixels[x, y] = (225, 228, 230) if pixels[x, y] == scene_lock.GROUND else (176, 40, 36)
            middle = (left + right) // 2
            ImageDraw.Draw(picture).rectangle((middle - 6, top + 20, middle + 6, bottom - 20), fill=(30, 200, 60))
            buffer = io.BytesIO(); picture.save(buffer, 'PNG')
            return {'png': buffer.getvalue(), 'size': list(size), 'usage': {'total_tokens': 100}}
        with patch.object(scene_imagegen, 'check', side_effect=check), patch.object(scene_imagegen, 'edit', side_effect=edit):
            made = self.create(people=False, mounting='wall')
        self.assertEqual((self.pose_calls[-1]['mode'], seen), ('keep', ['wall']))
        self.assertIn(scene_imagegen.WALL_RULE, self.prompts[-1])
        box = made['record']['anchor']['box']
        self.assertLess((box[1] + box[3]) / 2, .5)                              # higher in the frame than a product set down
        record = made['record']
        self.assertEqual((record['restored'], record['mounting']), (True, 'wall'))
        self.assertTrue(record['original'].endswith('/attempt1.model.png'))
        folder = self.data / 'fixture' / 'scene_ai' / 't1' / 'scene'
        with Image.open(folder / 'scene.png') as picture, Image.open(folder / 'attempt1.model.png') as painted:
            middle = (round((box[0] + box[2]) / 2 * picture.width), round((box[1] + box[3]) / 2 * picture.height))
            self.assertEqual(painted.convert('RGB').getpixel(middle), (30, 200, 60))   # the image model's invented bars
            restored = picture.convert('RGB').getpixel(middle)
            self.assertGreater(restored[0], 150); self.assertLess(restored[1], 80)       # the render's red block again, lit like the picture
            self.assertEqual(picture.convert('RGB').getpixel((5, 5)), (225, 228, 230))  # the wall is the image model's

    def test_a_product_that_moved_or_was_redrawn_is_made_again_without_asking_the_vision_model(self):
        self.locks = ['moved', 'kept']
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        stages = []
        made = self.create(progress=stages.append)
        self.assertEqual((made['record']['attempts'], made['record']['check']['status'], len(self.checked)), (2, 'passed', 1))       # the first was measured, not asked
        self.assertIn('CORRECTIONS.', self.prompts[1]); self.assertIn('must stay exactly where it is in image 1', self.prompts[1])
        self.assertIn('产品被移动或改动，正在重新生成', stages)
        self.assertEqual(made['record']['discarded'], [{'url': '/files/fixture/scene_ai/t1/scene/attempt1.png', 'issues': [scene_imagegen.LOCK_FIXES['moved']], 'lock': 'moved'}])
        # Redrawn every time: no picture shows this product, so there is no scene (a page never gets a redrawn product).
        self.locks = ['changed', 'changed']
        with self.assertRaisesRegex(scene_imagegen.ImageError, '改动了产品的位置、大小或外形'):
            self.create()
        kept = sorted((self.data / 'fixture' / 'scene_ai' / 'rejected').iterdir())          # set aside to be looked at
        self.assertEqual(sorted(path.name for path in kept[-1].iterdir()), ['attempt1.png', 'attempt2.png', 'canvas.png', 'checks.json', 'reference.png'])
        self.assertIn('parts of the product were redrawn (around its', self.prompts[-1])
        # When neither passes, a picture that kept the product beats one that did not, whatever else it got wrong.
        self.locks = ['kept', 'moved']
        self.verdicts = [{'status': 'failed', 'issues': ['one', 'two', 'three'], 'note': 'x', 'people': {'ok': False}}]
        made = self.create()
        self.assertEqual((made['record']['check']['lock']['status'], made['record']['check']['issues']), ('kept', ['one', 'two', 'three']))

    def test_a_service_that_takes_no_mask_still_gets_the_anchored_canvas(self):
        sent = []
        def edit(value, text, images, size, cancelled=None, progress=None, mask=None):
            sent.append(mask is not None)
            if mask is not None: raise scene_imagegen.RejectedError('图像请求被拒绝（HTTP 400）')
            return self.edit(value, text, images, size)
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        with patch.object(scene_imagegen, 'edit', side_effect=edit):
            made = self.create()
        self.assertEqual((sent, made['record']['anchor']['mask']), ([True, False], False))
        self.assertEqual(Path(self.images[0][0]).name, 'canvas.png')

    def test_a_gateway_that_gives_up_gets_one_more_try_at_low_quality_and_the_record_says_so(self):
        asked = []
        def edit(value, text, images, size, cancelled=None, progress=None, mask=None):
            asked.append(value['quality'])
            if value['quality'] != 'low': raise scene_imagegen.GatewayError('图像服务暂时不可用，请稍后重试。（HTTP 502）')
            return self.edit(value, text, images, size)
        self.verdicts = [{'status': 'failed', 'issues': ['one'], 'note': 'a'}, {'status': 'passed', 'issues': [], 'note': '一致。'}]
        stages = []
        with patch.object(scene_imagegen, 'edit', side_effect=edit):
            made = self.create(progress=stages.append)
        self.assertEqual(asked, ['medium', 'low', 'low'])                     # the correction does not go back to the quality that failed
        self.assertEqual((made['record']['quality'], made['record']['quality_lowered_from'], made['record']['attempts']), ('low', 'medium', 2))
        self.assertTrue(any('改用 low 质量重试' in stage for stage in stages))
        self.verdicts = [{'status': 'passed', 'issues': [], 'note': '一致。'}]
        self.assertNotIn('quality_lowered_from', self.create()['record'])
        # Already at low quality, or another kind of failure: the error stands.
        with patch.object(scene_imagegen, 'config', return_value={**IMAGE, 'quality': 'low'}), \
                patch.object(scene_imagegen, 'edit', side_effect=scene_imagegen.GatewayError('（HTTP 502）')), self.assertRaises(scene_imagegen.GatewayError):
            self.create()
        calls = []
        def refused(*arguments, **options):
            calls.append(1); raise scene_imagegen.ImageError('图像服务的额度已用尽或账户欠费，请充值后重试。（HTTP 402）')
        with patch.object(scene_imagegen, 'edit', side_effect=refused), self.assertRaisesRegex(scene_imagegen.ImageError, '额度'):
            self.create()
        self.assertEqual(len(calls), 1)

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

    def test_the_users_choice_about_people_is_binding(self):
        wanted, asked = self.propose(answer(people=False, complex=False), people='include')
        self.assertIs(asked['facts']['required_people'], True)
        self.assertEqual((wanted['people'], wanted['people_choice'], wanted['method']), (True, 'include', 'generate'))
        barred, asked = self.propose(answer(people=True, complex=False), people='exclude')
        self.assertIs(asked['facts']['required_people'], False)
        self.assertEqual((barred['people'], barred['people_choice'], barred['method']), (False, 'exclude', 'render'))
        self.assertEqual(self.propose(answer(people=True, complex=True), people='exclude')[0]['method'], 'generate')     # a real place, nobody in it
        free, asked = self.propose(answer(people=True))
        self.assertNotIn('required_people', asked['facts']); self.assertEqual((free['people'], free['people_choice']), (True, 'auto'))
        self.assertEqual(scene_stylist._brief(wanted, 'generate')['people_choice'], 'include')
        self.assertIs(scene_stylist._brief(wanted, 'render')['people'], False)        # props never include a person
        with self.assertRaisesRegex(ValueError, '人物选项无效'):
            self.propose(answer(), people='maybe')

    def test_a_person_that_was_asked_for_is_never_silently_dropped(self):
        proposal, _ = self.propose(answer(), people='include')
        with patch.object(scene_imagegen, 'config', return_value=IMAGE):
            with self.assertRaisesRegex(ValueError, '3D 道具场景里没有人物'):
                scene_stylist.with_method(proposal, 'render')
            self.assertEqual(scene_stylist.with_method(proposal, 'generate')['people'], True)
        with self.assertRaisesRegex(ValueError, '无法生成含人物的场景图'):
            self.propose(answer(), image=False, people='include')
        with self.assertRaisesRegex(ValueError, '无法生成含人物的场景图'):
            self.propose(None, image=False, people='include')

    def test_fixed_recipes_show_a_person_when_the_image_model_makes_the_picture(self):
        team = scene_audience.segment('site_team')
        auto, _ = self.propose(None)
        self.assertEqual((auto['method'], auto['people'], auto['scene']), ('render', False, team['place']))
        with patch.object(scene_imagegen, 'config', return_value=IMAGE):
            made = scene_stylist.with_method(auto, 'generate')
            self.assertEqual((made['people'], made['scene']), (True, team['scene']))
            self.assertEqual(scene_stylist.with_method(made, None)['method'], 'render')       # asked again without a method: props, as before
        wanted, _ = self.propose(None, people='include')
        self.assertEqual((wanted['method'], wanted['people'], wanted['scene']), ('generate', True, team['scene']))
        barred, _ = self.propose(None, people='exclude')
        self.assertEqual((barred['method'], barred['people'], barred['scene']), ('render', False, team['place']))
        with patch.object(scene_imagegen, 'config', return_value=IMAGE):
            self.assertEqual(scene_stylist.with_method(barred, 'generate')['scene'], team['place'])
        for item in scene_audience.library():
            with self.subTest(segment=item['id']):
                self.assertRegex(item['scene'], r'(?i)person|homeowner|enthusiast|tradesperson|installers|technician|groundskeeper')
                self.assertNotRegex(item['place'], r'(?i)person|people|homeowner|enthusiast|tradesperson|installer|technician|groundskeeper|\bhands?\b(?! tools)')

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
            connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project', project())
        self.reply = answer(); self.asked = 0; self.created = []
        mocked = patch.object(scene_stylist, '_ask', side_effect=self.ask); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_imagegen, 'create', side_effect=self.create); mocked.start(); self.addCleanup(mocked.stop)
        scene_props._MEMORY.clear(); self.addCleanup(scene_props._MEMORY.clear)

    def ask(self, config, system, facts, schema, image_path=None):
        if system is not scene_stylist.STRATEGIST_PROMPT: raise ValueError('only the strategist answers here')
        self.asked += 1
        return {**self.reply, **({'segment_id': facts['required_segment']} if 'required_segment' in facts else {})}

    def create(self, record, plan, azimuth, elevation, scene, product, size_mm, folder, cancelled=None, progress=None, people=None, framing=None,
               pose='auto', hint='', mounting=None):
        self.created.append({'azimuth': azimuth, 'elevation': elevation, 'scene': scene, 'product': product, 'size_mm': size_mm, 'plan': copy.deepcopy(plan),
                             'people': people, 'framing': framing, 'pose': pose, 'hint': hint})
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

    def test_regeneration_preserves_previous_generated_files_and_output_records(self):
        first = self.run_task()
        self.assertEqual(first['status'], 'completed', first.get('error'))
        previous = store.get('project', 'fixture')['outputs'][-1]
        old_png = self.data / previous['png_url'][7:]
        old_jpg = self.data / previous['jpg_url'][7:]
        before = (old_png.read_bytes(), old_jpg.read_bytes())
        second = self.run_task(hint='另一种场景')
        self.assertEqual(second['status'], 'completed', second.get('error'))
        outputs = [o for o in store.get('project', 'fixture')['outputs'] if o['shot_id'] == 'scene']
        self.assertEqual(len(outputs), 2)
        self.assertEqual([o['job_id'] for o in outputs], [first['id'], second['id']])
        self.assertEqual([o['version'] for o in outputs], [2, 3])
        self.assertNotEqual(outputs[0]['png_url'], outputs[1]['png_url'])
        self.assertEqual((old_png.read_bytes(), old_jpg.read_bytes()), before)
        self.assertTrue((self.data / outputs[1]['png_url'][7:]).is_file())
        self.assertTrue((self.data / outputs[1]['jpg_url'][7:]).is_file())

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

    def test_a_person_appears_as_hands_at_work_unless_the_user_asks_to_see_the_person(self):
        self.reply = answer(people=True, complex=True)
        self.run_task(method='generate')                                 # nothing chosen: the planner wants a person, shown as hands
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((self.created[0]['people'], self.created[0]['framing'], brief['framing'], brief['people_choice']), (True, 'hands', 'hands', 'auto'))
        self.run_task(method='generate', people='hands')
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((self.created[1]['people'], self.created[1]['framing'], brief['people'], brief['people_choice']), (True, 'hands', True, 'hands'))
        self.run_task(method='generate', people='include')               # the user wants to see who is using it
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((self.created[2]['people'], self.created[2]['framing'], brief['framing']), (True, 'person', 'person'))
        self.run_task(method='generate', people='exclude')
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((self.created[3]['people'], self.created[3]['framing'], brief['framing']), (False, None, None))
        with self.assertRaisesRegex(ValueError, '3D 道具场景里没有人物'):
            scene_stylist.submit('fixture', {'method': 'render', 'people': 'hands'})

    def test_the_proposal_the_user_saw_is_the_one_that_is_made(self):
        shown = scene_stylist.recommend('fixture', {'hint': '工地'})
        self.assertEqual((self.asked, shown['method'], store.get('project', 'fixture')['scene_proposal']['id']), (1, 'generate', shown['id']))
        self.run_task(hint='工地')
        self.assertEqual(self.asked, 1)                                   # not asked again
        self.run_task(hint='车间')                                         # other words, or another plan version: asked again
        self.assertEqual(self.asked, 2)

    def test_the_choice_about_people_reaches_the_planner_the_image_model_and_the_plan(self):
        self.reply = answer(people=False, complex=True)                  # the planner alone would have left the person out
        task = self.run_task(method='generate', people='include')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((self.created[0]['people'], brief['people'], brief['people_choice']), (True, True, 'include'))
        self.run_task(method='generate', people='exclude')
        brief = store.get('project', 'fixture')['plan']['scene_brief']
        self.assertEqual((self.created[1]['people'], brief['people'], brief['people_choice']), (False, False, 'exclude'))
        self.run_task()                                                  # nothing chosen: the planner's own answer
        self.assertIs(self.created[2]['people'], False)

    def test_the_choice_about_the_pose_reaches_the_image_model_and_a_bad_one_is_refused(self):
        task = self.run_task(method='generate', people='hands', hint='cutting a steel pipe')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual((self.created[0]['pose'], self.created[0]['hint']), ('auto', 'cutting a steel pipe'))     # posed for its use unless told otherwise
        self.run_task(method='generate', pose='keep')
        self.assertEqual(self.created[1]['pose'], 'keep')
        with self.assertRaisesRegex(ValueError, '产品姿态选项无效'):
            scene_stylist.submit('fixture', {'expected_version': store.get('project', 'fixture')['version'], 'pose': 'upside down'})

    def test_a_proposal_made_for_another_choice_about_people_is_not_reused(self):
        shown = scene_stylist.recommend('fixture', {'hint': ''})
        self.assertEqual((self.asked, shown['people_choice']), (1, 'auto'))
        self.run_task(people='include')
        self.assertEqual((self.asked, self.created[0]['people']), (2, True))
        shown = scene_stylist.recommend('fixture', {'people': 'exclude'})
        self.run_task(people='exclude')
        self.assertEqual((self.asked, self.created[1]['people'], shown['people']), (3, False, False))

    def test_a_request_that_cannot_show_the_person_is_refused_before_anything_is_asked(self):
        for body in ({'people': 'include', 'method': 'render'}, {'people': 'sometimes'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                scene_stylist.submit('fixture', {'expected_version': 1, **body})
            with self.subTest(body=body, call='recommend'), self.assertRaises(ValueError):
                scene_stylist.recommend('fixture', body)
        with patch.object(scene_imagegen, 'config', return_value=None):
            with self.assertRaisesRegex(ValueError, '无法生成含人物的场景图'):
                scene_stylist.submit('fixture', {'expected_version': 1, 'people': 'include'})
            with self.assertRaisesRegex(ValueError, '无法生成含人物的场景图'):
                scene_stylist.recommend('fixture', {'people': 'include'})
        self.assertEqual((self.asked, store.all_records(scene_stylist.KIND)), (0, []))

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
        # Scenes are made by the listing set now: the standalone entry points are gone, the stylist itself still works.
        self.assertEqual(client.post('/api/projects/fixture/scene/recommend', json={'hint': ''}).status_code, 404)
        self.assertEqual(client.post('/api/projects/fixture/scene', json={'expected_version': 1}).status_code, 405)
        proposal = scene_stylist.recommend('fixture', {'hint': ''})
        self.assertEqual((proposal['segment_id'], proposal['method']), ('site_team', 'generate'))
        with self.assertRaises(ValueError):
            scene_stylist.recommend('fixture', {'audience': 'nobody'})
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            self.assertEqual(scene_stylist.submit('fixture', {'expected_version': 1})['status'], 'queued')
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        view = client.get('/api/projects/fixture').get_json()
        self.assertEqual((view['scene_task']['status'], view['plan']['scene_brief']['method'], view['outputs'][-1]['quality']), ('completed', 'generate', 'generated'))
        self.assertEqual(client.get('/api/projects/fixture/scene').get_json()['scene_brief']['segment_id'], 'site_team')
        with patch.object(application, 'DATA', self.data):           # the picture is served like any render of the project
            self.assertEqual(client.get(view['outputs'][-1]['png_url']).status_code, 200)


if __name__ == '__main__':
    unittest.main()

"""The text and icon layer: data contract, what to say, proposals and storage.

The compositions themselves are proofed in test_layer_compose.py and the icons
in test_layer_icons.py. No network and no model service: model replies are
scripted. One class runs the real Blender probe on a synthetic cabinet and is
skipped without Blender.
"""
import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import engine
import info_layer
import layer_icons
import planner
import studio_store as store

ROOT = Path(__file__).resolve().parents[1]
_no_model = None


def setUpModule():
    """No test here reaches a model service or the network, whatever this machine's own AI settings are. Tests about the model script its replies."""
    global _no_model
    _no_model = [patch.object(info_layer, '_config', return_value=None), patch.object(layer_icons, '_config', return_value=None),
                 patch.object(layer_icons, 'online_allowed', return_value=False)]
    for mocked in _no_model: mocked.start()


def tearDownModule():
    for mocked in _no_model: mocked.stop()

NAMES = ['G-07C抽屉(2)_8', 'G-不锈钢台面_2', '脚轮_1', '脚轮_2', '锁芯', '钥匙', '柜体']


def item(**changes):
    base = {'id': 't1', 'type': 'text', 'x': .1, 'y': .1, 'w': .4, 'size': .05, 'text': '标题', 'color': '#141414'}
    return {**base, **changes}


def project(names=NAMES, name='红色工具柜', **plan_changes):
    plan = planner.default_plan(); plan.update(plan_changes)
    return {'id': 'p', 'name': name, 'version': 1, 'status': 'ready', 'metadata': {}, 'plan': plan, 'outputs': [], 'history': [],
            'analysis': {'objects': [{'name': value} for value in names]}}


def picture(product=(.3, .25, .7, .85), parts=None, scenery=(), n=48, light=.95, estimated=False, color=None):
    """A picture with one product block; ``parts`` maps a name to its own rectangle inside it. ``color`` paints the product."""
    parts = parts or {}; objects = ['body'] + list(parts)
    cells = []
    for y in range(n):
        for x in range(n):
            px, py = (x + .5) / n, (y + .5) / n; value = -1
            if product[0] <= px < product[2] and product[1] <= py < product[3]:
                value = 0
                for index, rect in enumerate(parts.values()):
                    if rect[0] <= px < rect[2] and rect[1] <= py < rect[3]: value = index + 1
            elif any(rect[0] <= px < rect[2] and rect[1] <= py < rect[3] for rect in scenery):
                value = -2
            cells.append(value)
    result = info_layer.Picture({'nx': n, 'ny': n, 'cells': cells, 'objects': objects, 'estimated': estimated})
    result.luminance = [light] * (n * n); result._light = result._table(result.luminance); result._light2 = result._table([v * v for v in result.luminance])
    if color:
        grey = (round(light * 255),) * 3; result.colors = [color if cell >= 0 else grey for cell in cells]
    return result


def on_canvas(layer, point):
    """A point of the render, where a composition shows it."""
    photo = (layer.get('canvas') or {}).get('photo') or {'x': 0, 'y': 0, 'w': 1, 'h': 1, 'crop': [0, 0, 1, 1]}; crop = photo['crop']
    return (photo['x'] + (point[0] - crop[0]) / (crop[2] - crop[0]) * photo['w'], photo['y'] + (point[1] - crop[1]) / (crop[3] - crop[1]) * photo['h'])


def in_render(layer, point):
    """A point of a composition, where it is in the render."""
    photo = (layer.get('canvas') or {}).get('photo') or {'x': 0, 'y': 0, 'w': 1, 'h': 1, 'crop': [0, 0, 1, 1]}; crop = photo['crop']
    return (crop[0] + (point[0] - photo['x']) / photo['w'] * (crop[2] - crop[0]), crop[1] + (point[1] - photo['y']) / photo['h'] * (crop[3] - crop[1]))


GLYPH = {'source': 'lucide', 'name': 'wrench', 'license': 'ISC', 'ops': [['s', 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77']], 'stroke': 2, 'grid': 24}


class ContractTests(unittest.TestCase):
    def test_a_layer_of_text_icons_and_callouts_is_kept_as_plain_data(self):
        layer = info_layer.validate({'version': 1, 'source': 'manual', 'basis': {'azimuth': 35, 'aspect': '1:1'}, 'items': [
            item(weight=700, align='center', plate={'color': '#FFFFFF', 'opacity': .9}, extra='dropped'),
            {'id': 'i1', 'type': 'icon', 'icon': 'shield', 'x': .5, 'y': .5, 'size': .06, 'color': '#D92D20'},
            {'id': 'c1', 'type': 'callout', 'x': .05, 'y': .4, 'w': .2, 'size': .03, 'title': '带锁', 'detail': '', 'icon': None, 'anchor': [.4, .5]}]})
        text, icon, callout = layer['items']
        self.assertNotIn('extra', text); self.assertEqual((text['weight'], text['align'], text['plate']), (700, 'center', {'color': '#ffffff', 'opacity': .9}))
        self.assertEqual((icon['icon'], icon['color']), ('shield', '#d92d20'))
        self.assertEqual((callout['anchor'], callout['icon'], callout['color'], callout['plate']), ([.4, .5], None, '#141414', None))
        self.assertEqual(layer['basis'], {'azimuth': 35, 'aspect': '1:1'})
        self.assertNotIn('theme', layer); self.assertNotIn('role', text)             # a layer without a style stays the plain drawing
        json.dumps(layer)

    def test_a_style_an_accent_and_text_roles_are_part_of_the_layer(self):
        layer = info_layer.validate({'version': 1, 'theme': 'card', 'accent': '#E8452C', 'items': [
            item(role='headline', mark='工具柜', text='红色工具柜'), item(id='k', role='kicker', text='车间收纳'), item(id='b', role='body')]})
        self.assertEqual((layer['theme'], layer['accent']), ('card', '#e8452c'))
        head, kicker, body = layer['items']
        self.assertEqual((head['role'], head['mark'], kicker['role']), ('headline', '工具柜', 'kicker')); self.assertNotIn('role', body)
        for bad in ({'theme': 'neon'}, {'accent': 'red'}, {'items': [item(role='title')]}, {'items': [item(mark='x' * 61)]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                info_layer.validate({'version': 1, 'items': [], **bad})

    def test_what_is_not_a_known_element_is_refused(self):
        bad = [item(type='html'), item(id='a b'), item(color='red'), item(color='javascript:x'), item(size=3), item(x=float('nan')),
               item(text='a\x00b'), item(text=''), item(weight=900), item(align='justify'), item(plate={'color': '#fff'}),
               {'id': 'i', 'type': 'icon', 'icon': 'skull', 'x': .5, 'y': .5, 'size': .05},
               {'id': 'c', 'type': 'callout', 'x': 0, 'y': 0, 'w': .2, 'size': .03, 'title': 'x', 'anchor': [2, 0]},
               {'id': 'c', 'type': 'callout', 'x': 0, 'y': 0, 'w': .2, 'size': .03, 'title': 'x', 'icon': '<svg>'}]
        for entry in bad:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                info_layer.validate({'version': 1, 'items': [entry]})
        with self.assertRaises(ValueError): info_layer.validate({'version': 1, 'items': [item(), item()]})            # the same id twice
        with self.assertRaises(ValueError): info_layer.validate({'version': 1, 'items': [item(id='t%d' % n) for n in range(41)]})
        with self.assertRaises(ValueError): info_layer.validate({'version': 2, 'items': []})
        with self.assertRaises(ValueError): info_layer.validate({'version': 1, 'items': [], 'basis': {'script': 1}})

    def test_a_composition_keeps_its_canvas_words_and_icons_as_plain_data(self):
        layer = info_layer.validate({'version': 1, 'layout': 'split', 'source': 'ai', 'glyphs': {'g1': GLYPH},
            'canvas': {'color': '#F2F2F0', 'photo': {'x': .4, 'y': 0, 'w': .6, 'h': 1, 'crop': [-.1, 0, .9, 1], 'feather': True, 'radius': .016, 'fill': '#FFFFFF'}},
            'content': {'kicker': '车间收纳', 'headline': '工具柜', 'subline': '', 'free_area': 'left', 'product_box': [.2, .1, .8, .9], 'unknown': 1,
                        'points': [{'title': '抽屉', 'detail': '', 'icon': 'drawer', 'parts': ['抽屉_1'], 'anchor': [.5, .4], 'glyph': 'g1'}],
                        'figures': [{'value': '120', 'unit': 'kg', 'label': '承重', 'icon': 'weight'}],
                        'specs': [{'value': '8–24', 'unit': 'mm', 'label': '扳手尺寸', 'icon': 'wrench', 'values': ['8', '24']}]},
            'items': [{'id': 'headline', 'type': 'text', 'role': 'headline', 'x': .08, 'y': .1, 'w': .3, 'size': .06, 'text': '工具柜', 'color': '#1D1D1F', 'leading': 1.16, 'slot': 'headline'},
                      {'id': 'fig1', 'type': 'figure', 'x': .08, 'y': .7, 'w': .3, 'size': .07, 'value': '120', 'unit': 'kg', 'label': '承重', 'color': '#E62A29', 'soft': '#6E6E73', 'glyph': 'g1', 'slot': 'figures.0'},
                      {'id': 'point1', 'type': 'callout', 'style': 'line', 'x': .7, 'y': .3, 'w': .25, 'size': .024, 'title': '抽屉', 'detail': '', 'anchor': [.5, .4], 'elbow': .62, 'slot': 'points.0'},
                      {'id': 'ladder', 'type': 'chips', 'x': .08, 'y': .85, 'w': .8, 'size': .03, 'values': ['8', '24'], 'unit': 'mm', 'label': '扳手尺寸', 'slot': 'specs.0'},
                      {'id': 'band', 'type': 'panel', 'x': 0, 'y': .8, 'w': 1, 'h': .2, 'size': .02, 'color': '#E62A29', 'fade': 'up'},
                      {'id': 'rule1', 'type': 'rule', 'x': .1, 'y': .5, 'x2': .3, 'y2': .5, 'size': .02, 'color': '#1D1D1F', 'width': .0016}]})
        self.assertEqual((layer['layout'], layer['canvas']['color'], layer['canvas']['photo']['fill']), ('split', '#f2f2f0', '#ffffff'))
        self.assertNotIn('unknown', layer['content']); self.assertEqual(layer['content']['points'][0]['glyph'], 'g1')
        headline, figure, note, chips, band, rule = layer['items']
        self.assertEqual((figure['glyph'], figure['soft'], note['style'], note['elbow']), ('g1', '#6e6e73', 'line', .62))
        self.assertEqual((chips['values'], chips['fill'], band['fade'], rule['x2']), (['8', '24'], '#ffffff', 'up', .3))
        self.assertEqual(layer['glyphs']['g1']['ops'], GLYPH['ops']); json.dumps(layer)
        base = {'version': 1, 'items': [], 'glyphs': {'g1': GLYPH}}
        for bad in ({'layout': 'collage'}, {'canvas': {'color': '#fff'}}, {'canvas': {'color': '#ffffff', 'photo': {'x': 0, 'y': 0, 'w': 1, 'h': 1, 'crop': [0, 0, 1]}}},
                    {'content': {'points': [{'title': 'a', 'glyph': 'g9'}]}}, {'content': {'specs': [{'value': '1', 'values': ['x' * 11]}]}},
                    {'items': [{'id': 'i', 'type': 'text', 'x': 0, 'y': 0, 'w': .2, 'size': .05, 'text': 'a', 'glyph': 'g1'}]},
                    {'items': [{'id': 'i', 'type': 'figure', 'x': 0, 'y': 0, 'w': .2, 'size': .05, 'value': '1', 'glyph': 'g2'}]},
                    {'items': [{'id': 'i', 'type': 'chips', 'x': 0, 'y': 0, 'w': .2, 'size': .05, 'values': []}]},
                    {'items': [{'id': 'i', 'type': 'callout', 'x': 0, 'y': 0, 'w': .2, 'size': .05, 'title': 'a', 'style': 'bubble'}]},
                    {'items': [{'id': 'i', 'type': 'text', 'x': 0, 'y': 0, 'w': .2, 'size': .05, 'text': 'a', 'slot': 'points.9'}]},
                    {'glyphs': {'g1': {**GLYPH, 'ops': [['s', 'M0 0 <script>']]}}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                info_layer.validate({**base, **bad})

    def test_the_page_draws_exactly_the_icons_the_server_accepts(self):
        script = (ROOT / 'static' / 'layer.js').read_text(encoding='utf-8')
        block = script[script.index('const LAYER_ICONS = {'):script.index('};', script.index('const LAYER_ICONS = {'))]
        drawn = re.findall(r'^\s+([a-z]+): \[', block, flags=re.M)
        self.assertEqual(tuple(drawn), info_layer.ICONS)
        for path in re.findall(r"'(M[^']+)'", block):
            self.assertRegex(path, r'^[MmLlHhVvCcSsAaZz0-9 .\-]+$')              # path data only


class KeyPointTests(unittest.TestCase):
    def test_points_name_what_the_parts_are_called_and_never_invent_a_figure(self):
        content, notes = info_layer.copy_rules(project())
        self.assertEqual(content['headline'], '红色工具柜'); self.assertEqual(notes, [])
        titles = [point['title'] for point in content['points']]
        self.assertEqual(titles, ['抽屉收纳', '带轮移动', '带锁', '不锈钢台面'])
        by_title = {point['title']: point for point in content['points']}
        self.assertEqual(by_title['带轮移动']['parts'], ['脚轮_1', '脚轮_2']); self.assertEqual(by_title['带锁']['icon'], 'lock')
        said = content['kicker'] + content['headline'] + content['subline'] + ''.join(point['title'] + point['detail'] for point in content['points'])
        self.assertNotRegex(said, r'\d')                                         # no size, weight, count or grade was made up
        self.assertEqual((content['kicker'], content['emphasis'], content['subline']), ('车间收纳', '工具柜', ''))    # a category tag; the product's own word to set in colour

    def test_a_size_is_stated_only_when_the_user_gave_it(self):
        self.assertFalse(any(point['icon'] == 'ruler' for point in info_layer.copy_rules(project())[0]['points']))
        content, _ = info_layer.copy_rules(project(dimensions_mm=[690, 380.5, 738]))
        size = next(point for point in content['points'] if point['icon'] == 'ruler')
        self.assertEqual((size['detail'], size['parts']), ('690 × 380.5 × 738 mm', []))

    def test_parts_not_seen_in_this_picture_get_no_line(self):
        content, _ = info_layer.copy_rules(project(), visible=['G-07C抽屉(2)_8', '柜体'])
        by_title = {point['title']: point for point in content['points']}
        self.assertEqual(content['points'][0]['title'], '抽屉收纳')              # what the picture shows comes first
        self.assertEqual(by_title['带轮移动']['parts'], [])

    def test_points_the_user_typed_are_kept_in_their_words_and_find_their_part(self):
        content, _ = info_layer.copy_rules(project(), '承重 120 kg：台面可放重物\n抽屉带滑轨；全钢柜体')
        self.assertEqual([(point['title'], point['detail']) for point in content['points']],
                         [('承重 120 kg', '台面可放重物'), ('抽屉带滑轨', ''), ('全钢柜体', '')])
        self.assertEqual([point['parts'] for point in content['points']], [['G-不锈钢台面_2'], ['G-07C抽屉(2)_8'], ['柜体']])
        self.assertEqual(content['subline'], '')                                 # nothing is added beside the user's own points

    def test_a_model_with_no_telling_names_gets_a_headline_and_an_honest_note(self):
        content, notes = info_layer.copy_rules(project(names=['Object_1', 'Object_2'], name='model_final.glb'))
        self.assertEqual((content['headline'], content['points']), ('model_final', []))
        self.assertIn('没有从名称中识别', notes[0])

    def test_a_model_writes_the_words_when_configured_and_only_numbers_that_were_given_survive(self):
        answer = {'kicker': '车间收纳', 'headline': '车间工具柜', 'subline': '收纳与移动', 'layout': 'callouts', 'free_area': 'none', 'product_box': [.2, .1, .8, .9],
                  'points': [{'title': '全拉出抽屉', 'detail': '', 'icon': 'drawer', 'parts': ['G-07C抽屉(2)_8', '不存在的部件']},
                             {'title': '   ', 'detail': '', 'icon': 'check', 'parts': []}],
                  'figures': [{'value': '120', 'unit': 'kg', 'label': '台面承重', 'icon': 'weight'}, {'value': '500', 'unit': 'kg', 'label': '总承重', 'icon': 'weight'}],
                  'specs': [{'value': '8–24', 'unit': 'mm', 'label': '扳手尺寸', 'icon': 'wrench', 'values': ['8', '10', '13', '24']},
                            {'value': '42', 'unit': '件', 'label': '套装', 'icon': 'package', 'values': []}]}
        hint = '突出抽屉\n台面承重 120 kg\n扳手：8、10、12、24 mm'
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', return_value=answer) as ask:
            content, source, notes, layout = info_layer.content_for(project(), hint, None, ['G-07C抽屉(2)_8'], 'zh')
        self.assertEqual((source, layout, content['headline'], content['kicker'], content['language']), ('ai', 'callouts', '车间工具柜', '车间收纳', 'zh'))
        self.assertEqual(content['points'], [{'title': '全拉出抽屉', 'detail': '', 'icon': 'drawer', 'parts': ['G-07C抽屉(2)_8']}])
        self.assertEqual([row['value'] for row in content['figures']], ['120'])            # 500 kg was never given
        self.assertEqual([(row['label'], row['value'], row.get('values')) for row in content['specs']],
                         [('台面承重', '120', None), ('扳手', '8–24', ['8', '10', '12', '24'])])   # the user's own list, in their words; "42 件" was made up
        self.assertEqual(content['product_box'], [.2, .1, .8, .9])
        self.assertIn('2 个数字不在你提供的信息里', notes[0])
        facts = ask.call_args.args[2]
        self.assertEqual((facts['hint'], facts['visible_parts']), (hint, ['G-07C抽屉(2)_8']))
        self.assertEqual(set(facts['layouts']), {'split', 'callouts', 'scene', 'cards', 'spotlight', 'specs'})
        self.assertEqual([row['label'] for row in facts['user_specs']], ['台面承重', '扳手'])
        prompt = ask.call_args.args[1]
        self.assertIn('never estimate, convert, round or invent a number', prompt); self.assertIn('Do not praise', prompt)
        self.assertIn('Write every word in Simplified Chinese.', prompt); self.assertNotIn('{', prompt)

    def test_the_words_are_english_by_default_and_the_users_own_figures_survive_the_translation(self):
        answer = {'kicker': 'Workshop storage', 'headline': 'Mobile tool cabinet', 'subline': 'Seven drawers on casters', 'layout': 'specs', 'free_area': 'none',
                  'product_box': [.2, .1, .8, .9], 'points': [{'title': 'Full-extension drawers', 'detail': '', 'icon': 'drawer', 'parts': []}],
                  'figures': [{'value': '120', 'unit': 'kg', 'label': 'Worktop load', 'icon': 'weight'}],
                  'specs': [{'value': '8–24', 'unit': 'mm', 'label': 'Wrench sizes', 'icon': 'wrench', 'values': ['8', '10', '13', '24']}]}
        hint = '台面承重 120 kg\n扳手：8、10、12、24 mm'
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', return_value=answer) as ask:
            content, source, notes, layout = info_layer.content_for(project(), hint)
        self.assertEqual((source, content['language'], content['headline']), ('ai', 'en', 'Mobile tool cabinet'))
        self.assertEqual([(row['label'], row['value'], row.get('values')) for row in content['specs']],
                         [('Worktop load', '120', None), ('Wrench sizes', '8–24', ['8', '10', '12', '24'])])   # the model's words, the user's sizes
        prompt = ask.call_args.args[1]
        self.assertIn('Write every word in English', prompt); self.assertIn('its label in English', prompt); self.assertIn('at most 28 characters', prompt)
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', return_value={**answer, 'specs': []}):
            unmatched = info_layer.content_for(project(), '扳手：8、10、12、24 mm')[0]
        self.assertEqual(unmatched['specs'][0]['label'], '扳手')                    # nothing to take English words from: the user's own label stays

    def test_without_a_model_the_rules_write_english_and_say_what_they_could_not_translate(self):
        content, notes = info_layer.content_rules(project(dimensions_mm=[690, 380.5, 738]), '台面承重 120 kg')
        self.assertEqual((content['language'], content['kicker']), ('en', 'Workshop storage'))
        self.assertEqual([point['title'] for point in content['points']], ['Drawer storage', 'On casters', 'Lockable', 'Stainless steel worktop'])
        self.assertEqual([(row['label'], row['value']) for row in content['specs']], [('台面承重', '120'), ('Dimensions', '690×380×738')])
        self.assertIn('无法把中文翻译成英文', notes[0]); self.assertEqual(content['headline'], '红色工具柜')
        named, notes = info_layer.content_rules(project(name='Mobile Tool Cabinet'), '')
        self.assertEqual((named['headline'], notes), ('Mobile Tool Cabinet', []))
        self.assertEqual(info_layer.content_rules(project(), '', language='zh')[0]['points'][0]['title'], '抽屉收纳')

    def test_without_a_model_or_when_it_fails_the_rules_answer(self):
        self.assertEqual(info_layer.content_for(project(), language='zh')[1:], ('rules', [], None))
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', side_effect=RuntimeError('down')):
            content, source, notes, layout = info_layer.content_for(project())
        self.assertEqual((source, layout, content['points'][0]['title']), ('rules', None, 'Drawer storage')); self.assertEqual(notes[-1], 'AI 未返回可用结果，已改用名称规则。')
        refused = ValueError('AI 服务的额度已用尽或账户欠费，请充值或联系管理员后重试。（HTTP 402）')
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', side_effect=refused):
            notes = info_layer.content_for(project())[2]
        self.assertIn('额度已用尽', notes[-1]); self.assertIn('HTTP 402', notes[-1])   # the service's own reason reaches the user


class PictureTests(unittest.TestCase):
    def test_the_edge_of_a_flat_sweep_is_even_and_that_of_a_lit_floor_is_not(self):
        self.assertEqual(picture().edge_spread(), 0)
        lit = picture(); lit.luminance = [.55 + .25 * (index // lit.nx) / lit.ny for index in range(lit.nx * lit.ny)]
        self.assertGreater(lit.edge_spread(), .1)

    def test_a_line_points_at_the_largest_visible_patch_of_its_parts(self):
        view = picture(parts={'wheel_a': (.31, .8, .35, .84), 'wheel_b': (.55, .74, .69, .84)})
        anchor, share = view.anchor(['wheel_a', 'wheel_b'])
        self.assertTrue(.55 <= anchor[0] <= .69 and .74 <= anchor[1] <= .84); self.assertGreater(share, 0)
        self.assertEqual(view.anchor(['missing']), (None, 0.0))

    def test_text_is_wrapped_by_width_without_breaking_a_latin_word(self):
        self.assertEqual(info_layer.wrap('Stainless steel worktop 不锈钢台面耐磨', .03, .24), ['Stainless steel', 'worktop 不锈钢', '台面耐磨'])
        self.assertEqual(info_layer.wrap('一行\n两行', .03, 1), ['一行', '两行'])
        self.assertAlmostEqual(info_layer.text_width('台面', .05), .1)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        for module in (store, engine, info_layer):
            item_patch = patch.object(module, 'DATA', self.data); item_patch.start(); self.addCleanup(item_patch.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        record = project(); folder = self.data / 'p' / 'runs' / 'r'; folder.mkdir(parents=True)
        image = Image.new('RGB', (64, 64), 'white')
        for x in range(20, 44):
            for y in range(16, 54): image.putpixel((x, y), (200, 30, 30))
        image.save(folder / 'hero.png')
        record['outputs'] = [{'job_id': 'j1', 'shot_id': 'hero', 'version': 1, 'quality': 'preview', 'width': 64, 'height': 64, 'created_at': 5,
                              'png_url': '/files/p/runs/r/hero.png'}]
        store.put('project', record)

    def test_a_layer_is_saved_beside_the_plan_and_never_makes_a_render_stale(self):
        before = store.get('project', 'p')
        saved = info_layer.save('p', 'hero', {'layer': {'version': 1, 'items': [item()]}, 'expected_revision': 0})
        self.assertEqual((saved['revision'], len(saved['items'])), (1, 1))
        after = store.get('project', 'p')
        self.assertEqual((after['version'], after['plan'], after['history']), (before['version'], before['plan'], before['history']))
        self.assertEqual(info_layer.save('p', 'hero', {'layer': {'version': 1, 'items': []}, 'expected_revision': 1})['revision'], 2)
        with self.assertRaises(info_layer.LayerConflict): info_layer.save('p', 'hero', {'layer': {'version': 1, 'items': []}, 'expected_revision': 1})
        with self.assertRaises(KeyError): info_layer.save('p', 'no-such-shot', {'layer': {'version': 1, 'items': []}})
        with self.assertRaises(ValueError): info_layer.save('p', 'hero', {'layer': {'version': 1, 'items': [item(type='script')]}})
        self.assertEqual(engine.project_view('p')['layers']['hero']['revision'], 2)
        info_layer.remove('p', 'hero'); self.assertNotIn('hero', store.get('project', 'p')['layers'])

    def test_a_proposal_is_laid_out_on_the_saved_scene_and_saves_nothing(self):
        cells = [0 if 20 <= index % 64 < 44 and 16 <= index // 64 < 54 else -1 for index in range(64 * 64)]
        for y in range(20, 26):
            for x in range(22, 42): cells[y * 64 + x] = 1
        scene = {'success': True, 'nx': 64, 'ny': 64, 'cells': cells, 'objects': ['柜体', 'G-07C抽屉(2)_8']}
        with patch.object(info_layer, 'probe', return_value=scene) as probe:
            result = info_layer.suggest('p', 'hero', {'hint': '', 'output': {'job_id': 'j1', 'version': 1, 'quality': 'preview'}})
        self.assertEqual(probe.call_args.args[1]['job_id'], 'j1')
        report = result['report']; layer = result['layer']
        self.assertEqual((report['source'], report['probe'], report['layout']), ('rules', 'scene', layer['layout']))
        self.assertEqual(report['suitable'][0], layer['layout']); self.assertEqual(sorted(report['suitable']), sorted(['split', 'callouts', 'scene', 'cards', 'spotlight', 'specs']))
        drawer = next(point for point in layer['content']['points'] if point['title'] == 'Drawer storage')
        self.assertTrue(22 / 64 <= drawer['anchor'][0] <= 42 / 64 and 20 / 64 <= drawer['anchor'][1] <= 26 / 64)    # where the drawer is in the render
        self.assertTrue(report['icons']); self.assertTrue(all(icon['source'] in ('lucide', 'tabler') and icon['license'] in ('ISC', 'MIT') for icon in report['icons'].values()))
        self.assertIn(layer['content']['points'][0]['glyph'], layer['glyphs'])
        self.assertEqual(layer['basis']['aspect'], '1:1'); self.assertEqual(layer['basis']['azimuth'], store.get('project', 'p')['plan']['shots'][0]['azimuth'])
        self.assertNotIn('theme', layer); self.assertRegex(layer['canvas']['color'], r'^#[0-9a-f]{6}$')
        # The same words as structure notes: the line ends on the drawer, wherever the layout put the picture.
        with patch.object(info_layer, 'probe', return_value=scene):
            notes = info_layer.suggest('p', 'hero', {'layout': 'callouts'})
        note = next(item for item in notes['layer']['items'] if item['type'] == 'callout' and item['title'] == 'Drawer storage')
        x, y = in_render(notes['layer'], note['anchor'])
        self.assertTrue(22 / 64 - .01 <= x <= 42 / 64 + .01 and 20 / 64 - .01 <= y <= 26 / 64 + .01)
        self.assertEqual((notes['report']['layout'], notes['report']['anchored']), ('callouts', 1))
        self.assertNotIn('layers', store.get('project', 'p'))

    def test_without_the_scene_the_picture_itself_is_read_and_the_report_says_so(self):
        with patch.object(info_layer, 'probe', return_value=None):
            result = info_layer.suggest('p', 'hero', {})
        self.assertEqual(result['report']['probe'], 'image'); self.assertTrue(any('没有指向线' in note for note in result['report']['notes']))
        layer = result['layer']; headline = next(entry for entry in layer['items'] if entry['id'] == 'headline')
        left, top = on_canvas(layer, (20 / 64, 16 / 64)); right, bottom = on_canvas(layer, (44 / 64, 54 / 64))      # the red block, as the layout shows it
        end = headline['y'] + len(headline['text'].split('\n')) * headline['size'] * headline['leading']
        self.assertTrue(headline['x'] + headline['w'] <= left + .01 or headline['x'] >= right - .01 or end <= top + .01 or headline['y'] >= bottom - .01)
        self.assertTrue(all(entry.get('anchor') is None for entry in layer['items']))
        self.assertTrue(all(point['anchor'] is None for point in layer['content']['points']))

    def test_another_layout_keeps_the_words_and_asks_no_model(self):
        with patch.object(info_layer, 'probe', return_value=None):
            first = info_layer.suggest('p', 'hero', {'hint': '台面承重 120 kg'})
        content = copy.deepcopy(first['layer']['content']); content['headline'] = '我的标题'
        with patch.object(info_layer, 'probe', return_value=None), patch.object(info_layer, '_ask', side_effect=AssertionError('no model')):
            again = info_layer.recompose('p', 'hero', {'layout': 'spotlight', 'content': content, 'glyphs': first['layer'].get('glyphs', {})})
        layer = again['layer']
        self.assertEqual((layer['layout'], layer['source'], again['report']['layout']), ('spotlight', 'manual', 'spotlight'))
        self.assertEqual(next(item for item in layer['items'] if item['id'] == 'headline')['text'].replace('\n', ''), '我的标题')
        self.assertEqual([item['value'] for item in layer['items'] if item['type'] == 'figure'], ['120'])
        self.assertEqual(layer['content']['headline'], '我的标题')
        self.assertNotIn('layers', store.get('project', 'p'))

    def test_requests_that_cannot_be_served_say_why(self):
        with self.assertRaisesRegex(ValueError, '先生成'): info_layer.suggest('p', 'campaign', {})
        with self.assertRaisesRegex(ValueError, '先生成'): info_layer.suggest('p', 'hero', {'output': {'job_id': 'other', 'version': 1, 'quality': 'preview'}})
        with self.assertRaisesRegex(ValueError, '不能超过'): info_layer.suggest('p', 'hero', {'hint': 'x' * 401})
        with self.assertRaisesRegex(ValueError, '未知的版式'): info_layer.suggest('p', 'hero', {'layout': 'collage'})
        with self.assertRaisesRegex(ValueError, '请选择一个版式'): info_layer.recompose('p', 'hero', {'layout': 'collage'})
        with self.assertRaisesRegex(ValueError, '引用了不存在的图标'): info_layer.recompose('p', 'hero', {'layout': 'split', 'content': {'points': [{'title': 'a', 'glyph': 'g1'}]}})
        with self.assertRaises(KeyError): info_layer.suggest('p', 'nope', {})

    def test_the_web_layer_reads_writes_and_reports_conflicts(self):
        import app as web
        client = web.app.test_client()
        with patch.object(web, 'DATA', self.data):
            self.assertEqual(client.get('/api/projects/p/layers/hero').get_json()['layer'], None)
            body = {'layer': {'version': 1, 'items': [item()]}, 'expected_revision': 0}
            saved = client.put('/api/projects/p/layers/hero', json=body)
            self.assertEqual((saved.status_code, saved.get_json()['revision']), (200, 1))
            self.assertEqual(client.put('/api/projects/p/layers/hero', json=body).status_code, 409)
            self.assertEqual(client.put('/api/projects/p/layers/hero', json={'layer': {'version': 1, 'items': [item(type='x')]}}).status_code, 400)
            self.assertEqual(client.get('/api/projects/p/layers/nope').status_code, 404)
            self.assertEqual(client.get('/api/projects/p').get_json()['layers']['hero']['items'][0]['text'], '标题')
            with patch.object(info_layer, 'probe', return_value=None):
                proposal = client.post('/api/projects/p/layers/hero/suggest', json={'hint': ''})
            self.assertEqual(proposal.status_code, 200); self.assertEqual([entry['id'] for entry in proposal.get_json()['layer']['items']][:2], ['kicker', 'headline'])
            content, glyphs = proposal.get_json()['layer']['content'], proposal.get_json()['layer'].get('glyphs', {})
            with patch.object(info_layer, 'probe', return_value=None):
                switched = client.post('/api/projects/p/layers/hero/compose', json={'layout': 'cards', 'content': content, 'glyphs': glyphs})
                orphan = client.post('/api/projects/p/layers/hero/compose', json={'layout': 'cards', 'content': content})
            self.assertEqual((switched.status_code, switched.get_json()['layer']['layout']), (200, 'cards'))
            if glyphs: self.assertEqual(orphan.status_code, 400)                     # an icon is only referred to with its drawing
            self.assertEqual(client.post('/api/projects/p/layers/hero/compose', json={'layout': 'collage'}).status_code, 400)
            self.assertEqual(client.post('/api/projects/p/layers/hero/suggest', json={'layout': 'collage'}).status_code, 400)
            self.assertEqual(client.delete('/api/projects/p/layers/hero').get_json()['removed'], True)


def cube(name, low, high, material):
    x0, y0, z0 = low; x1, y1, z1 = high
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return name, material, corners, [(1, 2, 3, 4), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1), (5, 8, 7, 6)]


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: scene probe not run')
class SceneProbeRuntimeTests(unittest.TestCase):
    """Real Blender: a rendered synthetic cabinet, probed from its saved scene."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name); cls.data = root / 'data'; cls.data.mkdir()
        model = root / 'model' / 'cabinet.obj'; model.parent.mkdir()
        lines = ['mtllib cabinet.mtl']; offset = 0
        for name, material, corners, faces in (cube('柜体', (-.5, -.3, .1), (.5, .3, 1), '红'), cube('抽屉_1', (-.45, -.32, .7), (.45, -.3, .9), '红'),
                                               cube('脚轮_1', (-.48, -.28, 0), (-.38, -.18, .1), '黑'), cube('脚轮_2', (.38, -.28, 0), (.48, -.18, .1), '黑')):
            lines += ['o ' + name, 'usemtl ' + material] + ['v %s %s %s' % (x, z, -y) for x, y, z in corners]
            lines += ['f ' + ' '.join(str(index + offset) for index in face) for face in faces]; offset += len(corners)
        model.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (model.parent / 'cabinet.mtl').write_text('newmtl 红\nKd 0.7 0.05 0.05\nnewmtl 黑\nKd 0.05 0.05 0.05\n', encoding='utf-8')
        for module in (store, engine, info_layer):
            item_patch = patch.object(module, 'DATA', cls.data); item_patch.start(); cls.addClassCleanup(item_patch.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        plan = planner.default_plan(); plan.update(visual_review=False)
        job = {'id': 'job', 'project_id': 'p', 'status': 'running', 'plan': plan, 'version': 1, 'quality': 'preview'}; store.put('job', job)
        record = {'id': 'p', 'name': '工具柜', 'version': 1, 'status': 'ready', 'metadata': {}, 'plan': plan, 'outputs': [], 'history': [], 'model_path': str(model)}
        store.put('project', record)
        record['analysis'] = engine.execute(job, {'action': 'analyze', 'model_path': str(model), 'plan': plan}, cls.data / 'p' / 'analysis')
        shot = dict(plan['shots'][0], azimuth=0, elevation=10)
        run = cls.data / 'p' / 'runs' / 'hero'
        engine.execute(job, {'action': 'render', 'model_path': str(model), 'plan': plan, 'shot': shot, 'quality': 'preview'}, run)
        png = next(run.glob('*.png')); blend = next(run.glob('*.blend'))
        with Image.open(png) as image: width, height = image.size
        cls.output = {'job_id': 'job', 'shot_id': 'hero', 'version': 1, 'quality': 'preview', 'width': width, 'height': height, 'created_at': 1,
                      'png_url': '/files/' + png.relative_to(cls.data).as_posix(), 'blend_url': '/files/' + blend.relative_to(cls.data).as_posix()}
        record['outputs'] = [cls.output]; store.put('project', record)

    def test_the_probe_tells_product_parts_and_background_apart(self):
        scene = info_layer.probe('p', self.output)
        self.assertIsNotNone(scene); self.assertEqual((scene['nx'], scene['ny']), (96, 96))
        self.assertEqual(sorted(scene['objects']), ['抽屉_1', '柜体', '脚轮_1', '脚轮_2'])
        view = info_layer.Picture(scene)
        self.assertEqual(view.cover((0, 0, .08, .08)), (0.0, 0.0))                # a corner of the sweep is free
        self.assertGreater(view.cover((.45, .45, .55, .55))[0], .99)             # the middle is the product
        drawer, _ = view.anchor(['抽屉_1']); left, _ = view.anchor(['脚轮_1']); right, _ = view.anchor(['脚轮_2'])
        self.assertLess(left[0], .5); self.assertGreater(right[0], .5)            # seen from the front: each caster on its own side
        self.assertGreater(left[1], drawer[1])                                    # and below the drawer
        self.assertTrue(view.rect[0] < drawer[0] < view.rect[2])
        again = info_layer.probe('p', self.output)                                # the second read comes from the cache
        self.assertEqual(again, scene); self.assertEqual(len(list((self.data / 'p' / 'layers' / 'probes').glob('*.json'))), 1)

    def test_a_proposal_on_the_real_scene_points_at_the_parts(self):
        result = info_layer.suggest('p', 'hero', {})
        self.assertEqual((result['report']['probe'], result['report']['source'], result['layer']['layout']), ('scene', 'rules', 'callouts'))
        callouts = {entry['title']: entry for entry in result['layer']['items'] if entry['type'] == 'callout'}
        self.assertEqual(sorted(callouts), ['Drawer storage', 'On casters'])
        view = info_layer.Picture(info_layer.probe('p', self.output))
        for entry in callouts.values():
            self.assertTrue(view.on_product(*in_render(result['layer'], entry['anchor'])))


if __name__ == '__main__':
    unittest.main()

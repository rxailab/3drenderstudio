"""The text and icon layer: data contract, key points, layout and storage.

No network and no model service: model replies are scripted. One class runs
the real Blender probe on a synthetic cabinet and is skipped without Blender.
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
import planner
import studio_store as store

ROOT = Path(__file__).resolve().parents[1]
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


def box(entry, aspect=(1, 1)):
    """The rectangle a callout or text item takes, estimated the way the server lays it out."""
    unit = min(aspect); sy = unit / aspect[1]
    if entry['type'] == 'text':
        lines = len(entry['text'].split('\n')); pill = .72 if entry.get('role') == 'kicker' else 0
        return (entry['x'], entry['y'], entry['x'] + entry['w'], entry['y'] + (lines * info_layer.LINE + pill) * entry['size'] * sy)
    return (entry['x'], entry['y'], entry['x'] + entry['w'], entry['y'] + entry['size'] * 3.0 * sy)


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

    def test_a_model_writes_the_points_when_configured_and_may_only_point_at_parts_it_was_shown(self):
        answer = {'kicker': '车间收纳', 'headline': '车间工具柜', 'emphasis': '工具柜', 'subline': '收纳与移动', 'points': [
            {'title': '全拉出抽屉', 'detail': '', 'icon': 'drawer', 'parts': ['G-07C抽屉(2)_8', '不存在的部件']},
            {'title': '   ', 'detail': '', 'icon': 'check', 'parts': []}]}
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', return_value=answer) as ask:
            content, source, notes = info_layer.copy_for(project(), '突出抽屉', None, ['G-07C抽屉(2)_8'])
        self.assertEqual((source, content['headline'], content['emphasis'], content['kicker']), ('ai', '车间工具柜', '工具柜', '车间收纳'))
        self.assertEqual(content['points'], [{'title': '全拉出抽屉', 'detail': '', 'icon': 'drawer', 'parts': ['G-07C抽屉(2)_8']}])
        facts = ask.call_args.args[2]
        self.assertEqual((facts['hint'], facts['visible_parts']), ('突出抽屉', ['G-07C抽屉(2)_8']))
        self.assertIn('Never give a number', ask.call_args.args[1]); self.assertIn('Do not praise', ask.call_args.args[1])
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', return_value={**answer, 'emphasis': '不在标题里'}):
            self.assertEqual(info_layer.copy_for(project())[0]['emphasis'], '')       # only a word that is really in the headline can be set in colour

    def test_without_a_model_or_when_it_fails_the_rules_answer(self):
        self.assertEqual(info_layer.copy_for(project())[1], 'rules')
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', side_effect=RuntimeError('down')):
            content, source, notes = info_layer.copy_for(project())
        self.assertEqual((source, content['points'][0]['title']), ('rules', '抽屉收纳')); self.assertEqual(notes[-1], 'AI 未返回可用结果，已改用名称规则。')
        refused = ValueError('AI 服务的额度已用尽或账户欠费，请充值或联系管理员后重试。（HTTP 402）')
        with patch.object(info_layer, '_config', return_value={'enabled': True}), patch.object(info_layer, '_ask', side_effect=refused):
            notes = info_layer.copy_for(project())[2]
        self.assertIn('额度已用尽', notes[-1]); self.assertIn('HTTP 402', notes[-1])   # the service's own reason reaches the user


class LayoutTests(unittest.TestCase):
    CONTENT = {'headline': '红色工具柜', 'subline': '抽屉收纳 · 带轮移动', 'points': [
        {'title': '抽屉收纳', 'detail': '物品分层放置', 'icon': 'drawer', 'parts': ['drawer']},
        {'title': '带轮移动', 'detail': '可推到手边使用', 'icon': 'wheel', 'parts': ['wheel']},
        {'title': '带锁', 'detail': '可上锁存放', 'icon': 'lock', 'parts': ['lock']}]}
    PARTS = {'drawer': (.32, .3, .5, .4), 'wheel': (.6, .78, .68, .85), 'lock': (.33, .6, .37, .64)}

    def assert_clean(self, items, aspect=(1, 1)):
        rects = [box(entry, aspect) for entry in items]
        for rect in rects:
            self.assertGreaterEqual(rect[0], .04); self.assertLessEqual(rect[2], .97); self.assertGreaterEqual(rect[1], .04); self.assertLessEqual(rect[3], .97)
        for index, a in enumerate(rects):
            for b in rects[index + 1:]:
                if items[index]['id'] in ('headline', 'subline') and items[rects.index(b)]['id'] in ('headline', 'subline'): continue
                self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3], (a, b))
        info_layer.validate({'version': 1, 'items': items})

    def test_text_goes_where_the_picture_is_free_and_each_callout_beside_its_part(self):
        view = picture(parts=self.PARTS)
        items, dropped, crowded, look = info_layer.arrange(self.CONTENT, view)
        self.assertEqual((dropped, crowded), ([], False)); self.assert_clean(items)
        self.assertEqual(look['theme'], 'bold')                                  # a calm light backdrop: bare type, no cards
        by_id = {entry['id']: entry for entry in items}
        self.assertEqual(view.cover(box(by_id['headline']))[0], 0)               # the headline is off the product
        self.assertEqual((by_id['headline']['role'], by_id['headline']['weight']), ('headline', 700))
        self.assertGreaterEqual(by_id['headline']['size'], .05)                  # and set large
        drawer, wheel, lock = by_id['point1'], by_id['point2'], by_id['point3']
        self.assertLess(drawer['x'] + drawer['w'], .32); self.assertLess(lock['x'] + lock['w'], .32)       # parts on the left: labels on the left
        self.assertGreater(wheel['x'] + wheel['w'] / 2, .7)                      # the part on the right: its label on the right
        self.assertLess(drawer['y'], lock['y'])                                  # same order as the parts, so the lines do not cross
        for entry, name in ((drawer, 'drawer'), (wheel, 'wheel'), (lock, 'lock')):
            rect = self.PARTS[name]
            self.assertTrue(rect[0] <= entry['anchor'][0] <= rect[2] and rect[1] <= entry['anchor'][1] <= rect[3])    # the line ends on the part
            self.assertLess(abs(entry['y'] - entry['anchor'][1]), .2)            # and the label stays near it
        self.assertEqual({entry['color'] for entry in items}, {'#141414'})       # dark type on a light picture
        self.assertTrue(all(entry.get('plate') is None for entry in items))

    def test_a_dark_picture_gets_light_type_and_a_full_frame_gets_smaller_labels_on_plates(self):
        dark, _, _, look = info_layer.arrange(self.CONTENT, picture(parts=self.PARTS, light=.12))
        self.assertEqual({entry['color'] for entry in dark}, {'#ffffff'})
        self.assertEqual((look['theme'], look['accent']), ('card', '#ffc53d'))    # a dark picture: cards, and a warm accent when the product has no colour
        self.assertTrue(all(entry.get('plate') is None for entry in dark if entry['type'] == 'callout'))       # the style draws the cards itself
        roomy, _, _, _ = info_layer.arrange(self.CONTENT, picture(parts=self.PARTS))
        tight_parts = {'drawer': (.2, .3, .5, .4), 'wheel': (.7, .8, .8, .88), 'lock': (.2, .6, .26, .66)}
        tight, _, crowded, _ = info_layer.arrange(self.CONTENT, picture(product=(.14, .12, .86, .9), parts=tight_parts))
        self.assertTrue(crowded); self.assert_clean(tight)
        size = lambda items: next(entry['size'] for entry in items if entry['id'] == 'point1')
        self.assertLess(size(tight), size(roomy))
        self.assertTrue(any(entry.get('plate') for entry in tight if entry['type'] == 'callout'))
        for entry in tight:
            if entry.get('anchor'): self.assertLess(abs(entry['y'] - entry['anchor'][1]), .25)     # short lines, not labels pushed to a far corner

    def test_scenery_is_avoided_and_points_without_a_part_form_a_tidy_group(self):
        view = picture(parts=self.PARTS, scenery=[(.05, .1, .28, .5)])
        items, _, _, _ = info_layer.arrange(self.CONTENT, view)
        drawer = next(entry for entry in items if entry['id'] == 'point1')
        self.assertLess(view.cover(box(drawer))[1], .2)
        loose = {'headline': '哑光按压瓶', 'subline': '', 'points': [{'title': '500 ml 大容量', 'detail': '', 'icon': 'check', 'parts': []},
                                                               {'title': '哑光瓶身', 'detail': '', 'icon': 'check', 'parts': []}]}
        items, dropped, _, _ = info_layer.arrange(loose, picture(product=(.4, .15, .6, .85)))
        self.assert_clean(items); self.assertEqual(dropped, [])
        first, second = [entry for entry in items if entry['type'] == 'callout']
        self.assertTrue(first['anchor'] is None and second['anchor'] is None)
        self.assertTrue(abs(first['y'] - second['y']) < 1e-6 or abs(first['x'] - second['x']) < 1e-6)       # one row or one column

    def test_other_picture_shapes_keep_everything_inside_the_frame(self):
        for aspect in ('3:4', '16:9'):
            with self.subTest(aspect=aspect):
                items, _, _, _ = info_layer.arrange(self.CONTENT, picture(parts=self.PARTS), aspect)
                self.assert_clean(items, tuple(float(part) for part in aspect.split(':')))

    def test_a_picture_read_without_its_scene_gets_no_pointing_lines(self):
        items, _, _, _ = info_layer.arrange(self.CONTENT, picture(parts=self.PARTS, estimated=True))
        self.assertTrue(all(entry.get('anchor') is None for entry in items)); self.assert_clean(items)

    def test_the_accent_is_the_products_own_colour_and_the_tag_and_emphasis_are_laid_out(self):
        red = picture(parts=self.PARTS, color=(214, 40, 34))
        import colorsys
        hue, saturation, value = colorsys.rgb_to_hsv(*(int(red.accent()[index:index + 2], 16) / 255 for index in (1, 3, 5)))
        self.assertTrue((hue < .04 or hue > .96) and saturation > .7 and value > .8)      # a vivid red, whatever the render's exact shade
        self.assertIsNone(picture(parts=self.PARTS, color=(180, 182, 186)).accent())     # brushed steel has no hue to take
        self.assertEqual(info_layer.look_for(picture(parts=self.PARTS, color=(180, 182, 186)))['accent'], '#e8452c')
        content = {**self.CONTENT, 'kicker': '车间收纳', 'emphasis': '工具柜', 'subline': ''}
        items, _, _, look = info_layer.arrange(content, red)
        self.assertEqual(look['accent'], red.accent()); self.assert_clean(items)
        kicker, headline = items[0], items[1]
        self.assertEqual((kicker['role'], kicker['text'], headline['mark']), ('kicker', '车间收纳', '工具柜'))
        self.assertLess(kicker['y'], headline['y']); self.assertEqual(kicker['align'], headline['align'])
        self.assertLess(kicker['size'], headline['size'] * .5)
        self.assertNotIn('mark', info_layer.arrange({**content, 'emphasis': '别的词'}, red)[0][1])

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
        report = result['report']
        self.assertEqual((report['source'], report['probe'], report['anchored']), ('rules', 'scene', 1))
        drawer = next(entry for entry in result['layer']['items'] if entry['type'] == 'callout')
        self.assertEqual(drawer['title'], '抽屉收纳'); self.assertTrue(22 / 64 <= drawer['anchor'][0] <= 42 / 64 and 20 / 64 <= drawer['anchor'][1] <= 26 / 64)
        self.assertEqual(result['layer']['basis']['aspect'], '1:1'); self.assertEqual(result['layer']['basis']['azimuth'], store.get('project', 'p')['plan']['shots'][0]['azimuth'])
        self.assertEqual(result['layer']['theme'], 'bold'); self.assertRegex(result['layer']['accent'], r'^#[0-9a-f]{6}$')     # dressed for this picture: white sweep, red product
        self.assertNotIn('layers', store.get('project', 'p'))

    def test_without_the_scene_the_picture_itself_is_read_and_the_report_says_so(self):
        with patch.object(info_layer, 'probe', return_value=None):
            result = info_layer.suggest('p', 'hero', {})
        self.assertEqual(result['report']['probe'], 'image'); self.assertIn('没有指向线', result['report']['notes'][0])
        headline = next(entry for entry in result['layer']['items'] if entry['id'] == 'headline')
        self.assertTrue(headline['x'] + headline['w'] <= 20 / 64 + .02 or headline['x'] >= 44 / 64 - .02 or headline['y'] + headline['size'] * 1.3 <= 16 / 64 + .02)
        self.assertTrue(all(entry.get('anchor') is None for entry in result['layer']['items']))

    def test_requests_that_cannot_be_served_say_why(self):
        with self.assertRaisesRegex(ValueError, '先生成'): info_layer.suggest('p', 'campaign', {})
        with self.assertRaisesRegex(ValueError, '先生成'): info_layer.suggest('p', 'hero', {'output': {'job_id': 'other', 'version': 1, 'quality': 'preview'}})
        with self.assertRaisesRegex(ValueError, '不能超过'): info_layer.suggest('p', 'hero', {'hint': 'x' * 401})
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
        self.assertEqual((result['report']['probe'], result['report']['source']), ('scene', 'rules'))
        callouts = {entry['title']: entry for entry in result['layer']['items'] if entry['type'] == 'callout'}
        self.assertEqual(sorted(callouts), ['带轮移动', '抽屉收纳'])
        view = info_layer.Picture(info_layer.probe('p', self.output))
        for entry in callouts.values():
            self.assertTrue(view.on_product(*entry['anchor']))


if __name__ == '__main__':
    unittest.main()

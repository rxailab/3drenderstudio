"""Compositions for the text layer: six layouts modelled on twelve brand references.

What is checked is what a designer checks on a proof: everything on the canvas,
no words on top of each other or on the product, the product never cut, notes
pointing at the part they name, colours that can be read, numbers only when the
user gave them, and the words kept when the layout changes. Pictures are
synthetic grids; no Blender, network or model.
"""
import copy
import itertools
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import info_layer
import layer_compose as compose
import layer_icons
from test_info_layer import picture, project

PARTS = {'drawer': (.32, .3, .5, .4), 'wheel': (.6, .78, .68, .85), 'lock': (.33, .6, .37, .64)}
TALL = {'drawer': (.25, .2, .5, .3), 'wheel': (.6, .9, .68, .96), 'lock': (.3, .5, .34, .55)}
WIDE = {'drawer': (.1, .45, .3, .55), 'wheel': (.8, .6, .88, .66), 'lock': (.5, .42, .54, .47)}
CONTENT = {'kicker': '车间收纳', 'headline': '红色移动工具柜', 'subline': '抽屉分层，带轮移动',
           'points': [{'title': '抽屉收纳', 'detail': '物品分层放置', 'icon': 'drawer', 'parts': ['drawer'], 'glyph': 'g1'},
                      {'title': '带轮移动', 'detail': '可推到手边使用', 'icon': 'caster wheel', 'parts': ['wheel'], 'glyph': 'g2'},
                      {'title': '带锁', 'detail': '可上锁存放', 'icon': 'lock', 'parts': ['lock']}],
           'figures': [{'value': '120', 'unit': 'kg', 'label': '台面承重', 'icon': 'weight', 'glyph': 'g3'}],
           'specs': [{'value': '690×380×738', 'unit': 'mm', 'label': '外形尺寸', 'icon': 'ruler'},
                     {'value': '8–24', 'unit': 'mm', 'label': '扳手尺寸', 'icon': 'wrench', 'glyph': 'g4',
                      'values': ['8', '10', '12', '14', '17', '19', '22', '24']}],
           'free_area': 'none'}
CONTENT_EN = {'kicker': 'Workshop storage', 'headline': 'Mobile tool cabinet', 'subline': 'Seven drawers on smooth casters',
              'points': [{'title': 'Drawer storage', 'detail': 'Tools sorted by drawer', 'icon': 'drawer', 'parts': ['drawer'], 'glyph': 'g1'},
                         {'title': 'On casters', 'detail': 'Rolls to where you work', 'icon': 'caster wheel', 'parts': ['wheel'], 'glyph': 'g2'},
                         {'title': 'Lockable', 'detail': 'Keeps tools secure', 'icon': 'lock', 'parts': ['lock']}],
              'figures': [{'value': '120', 'unit': 'kg', 'label': 'Worktop load', 'icon': 'weight', 'glyph': 'g3'}],
              'specs': [{'value': '690×380×738', 'unit': 'mm', 'label': 'Dimensions', 'icon': 'ruler'},
                        {'value': '8–24', 'unit': 'mm', 'label': 'Wrench sizes', 'icon': 'wrench', 'glyph': 'g4',
                         'values': ['8', '10', '12', '14', '17', '19', '22', '24']}],
              'free_area': 'none', 'language': 'en'}
GLYPHS = {'g1': 'lucide:archive', 'g2': 'lucide:circle-dot', 'g3': 'lucide:weight', 'g4': 'lucide:wrench'}
VIEWS = {   # name: (picture, its parts, a generated photo)
    'studio': (lambda: picture(parts=PARTS, color=(214, 40, 34)), PARTS, False),
    'dark': (lambda: picture(parts=PARTS, light=.1), PARTS, False),
    'tall': (lambda: picture(product=(.2, .05, .8, .97), parts=TALL), TALL, False),
    'wide': (lambda: picture(product=(.04, .36, .96, .7), parts=WIDE), WIDE, False),
    'photo': (lambda: picture(parts=PARTS, scenery=[(0, .6, .3, 1), (.75, 0, 1, .5)]), PARTS, True),
    'tall photo': (lambda: picture(product=(.2, .05, .8, .97), parts=TALL, scenery=[(0, 0, .15, 1)]), TALL, True),
    'wide photo': (lambda: picture(product=(.04, .36, .96, .7), parts=WIDE, scenery=[(0, .8, 1, 1)]), WIDE, True),
}
ASPECTS = ('1:1', '3:4', '4:3')


def glyphs():
    return {key: layer_icons.glyph(name) for key, name in GLYPHS.items()}


def extent(frame, item):
    """The rectangle an item's words take, measured the way the server lays them out (static/layer.js draws them alike)."""
    x, y, size = item['x'], item['y'], item['size']
    if item['type'] == 'text':
        return x, y, x + item['w'], y + compose._text_height(frame, item)
    if item['type'] == 'figure':
        label = info_layer.wrap(item['label'], size * compose.FIGURE_LABEL, item['w'] / frame.sx) if item.get('label') else []
        height = size * 1.02 + (size * compose.FIGURE_GAP + len(label) * size * compose.FIGURE_LABEL * 1.4 if label else 0)
        return x, y, x + item['w'], y + height * frame.sy
    if item['type'] == 'callout':
        icon = size * compose.ROW_ICON if item.get('style') == 'row' and item.get('glyph') else 0
        limit = item['w'] / frame.sx - (icon + size * .7 if icon else 0)
        title = info_layer.wrap(item['title'], size, limit)
        detail = info_layer.wrap(item['detail'], size * compose.ROW_DETAIL, limit) if item.get('detail') else []
        height = max(icon, len(title) * size * 1.3 + len(detail) * size * compose.ROW_DETAIL * 1.45)
        return x, y, x + item['w'], y + height * frame.sy
    if item['type'] == 'chips':
        chip = size * 1.5; gap = size * .28; width = item['w'] / frame.sx; rows = 1; used = 0.0
        for value in item['values']:
            each = max(chip, info_layer.text_width(value, size * .62) + size * 1.1)
            if used and used + gap + each > width:
                rows += 1; used = each
            else:
                used += (gap if used else 0) + each
        top = size * .5 * 1.6 if item.get('label') or item.get('unit') else 0
        return x, y, x + item['w'], y + (top + rows * chip + (rows - 1) * gap) * frame.sy
    return None


def frame_of(canvas):
    return (canvas or {}).get('photo') or {'x': 0, 'y': 0, 'w': 1, 'h': 1, 'crop': [0, 0, 1, 1]}


def to_canvas(photo, point):
    crop = photo['crop']
    return (photo['x'] + (point[0] - crop[0]) / (crop[2] - crop[0]) * photo['w'], photo['y'] + (point[1] - crop[1]) / (crop[3] - crop[1]) * photo['h'])


def to_render(photo, point):
    crop = photo['crop']
    return (crop[0] + (point[0] - photo['x']) / photo['w'] * (crop[2] - crop[0]), crop[1] + (point[1] - photo['y']) / photo['h'] * (crop[3] - crop[1]))


def overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def composed(layout, aspect, view, generated=False, content=CONTENT):
    content = copy.deepcopy(content)
    info_layer._point_anchors(content, view)
    canvas, items, report = compose.compose(layout, content, view, aspect, generated)
    return content, canvas, items, report


class ProofTests(unittest.TestCase):
    def test_every_layout_fits_its_canvas_keeps_words_apart_and_off_the_product(self):
        for (name, (make, parts, generated)), layout, aspect, words in itertools.product(VIEWS.items(), compose.LAYOUTS, ASPECTS, (CONTENT, CONTENT_EN)):
            with self.subTest(picture=name, layout=layout, aspect=aspect, language=words.get('language', 'zh')):
                view = make()
                content, canvas, items, report = composed(layout, aspect, view, generated, words)
                layer = info_layer.validate({'version': 1, 'layout': layout, 'canvas': canvas, 'content': content, 'glyphs': glyphs(), 'items': items})
                self.assertEqual(len(layer['items']), len(items))
                # Nothing is lost silently: what a layout does not show is named, so the user can pick another.
                shown = {'.'.join(item['slot'].split('.')[:2]) for item in items if item.get('slot')}
                for group in ('points', 'figures', 'specs'):
                    for index, entry in enumerate(content[group]):
                        name = entry.get('title') or entry.get('label')
                        self.assertNotEqual('%s.%d' % (group, index) in shown, name in report['dropped'], (group, name))
                frame = compose.Frame(aspect); photo = frame_of(canvas)
                words = [(item, extent(frame, item)) for item in items if item['type'] in ('text', 'figure', 'callout', 'chips')]
                self.assertTrue(any(item['id'] == 'headline' for item, _ in words))
                for item, box in words:
                    self.assertTrue(-.002 <= box[0] and -.002 <= box[1] and box[2] <= 1.002 and box[3] <= 1.002, (item['id'], box))
                for (a, box_a), (b, box_b) in itertools.combinations(words, 2):
                    self.assertLess(overlap(box_a, box_b), 1e-5, (a['id'], b['id']))
                corner = to_canvas(photo, view.rect[:2]); far = to_canvas(photo, view.rect[2:])
                product = (max(corner[0], photo['x']), max(corner[1], photo['y']), min(far[0], photo['x'] + photo['w']), min(far[1], photo['y'] + photo['h']))
                for item, box in words:
                    self.assertLess(overlap(box, product) / ((box[2] - box[0]) * (box[3] - box[1])), .02, item['id'])

    def test_the_product_is_never_cut_and_a_photo_is_never_extended(self):
        for (name, (make, _, generated)), layout, aspect in itertools.product(VIEWS.items(), compose.LAYOUTS, ASPECTS):
            with self.subTest(picture=name, layout=layout, aspect=aspect):
                view = make()
                _, canvas, _, _ = composed(layout, aspect, view, generated)
                if not canvas:                                               # the words lie on the full picture
                    continue
                photo = canvas['photo']; crop = photo['crop']
                self.assertTrue(crop[0] <= view.rect[0] + 1e-6 and crop[1] <= view.rect[1] + 1e-6 and crop[2] >= view.rect[2] - 1e-6 and crop[3] >= view.rect[3] - 1e-6)
                self.assertTrue(-1e-6 <= photo['x'] and -1e-6 <= photo['y'] and photo['x'] + photo['w'] <= 1 + 1e-6 and photo['y'] + photo['h'] <= 1 + 1e-6)
                if not photo['feather']:                                     # only a studio sweep may run past the render's edge
                    self.assertTrue(-1e-6 <= crop[0] and -1e-6 <= crop[1] and crop[2] <= 1 + 1e-6 and crop[3] <= 1 + 1e-6, crop)

    def test_a_photo_that_cannot_fill_its_space_becomes_a_print_of_its_own(self):
        view = picture(product=(.2, .05, .8, .97), parts=TALL, scenery=[(0, 0, .15, 1)])
        _, canvas, _, _ = composed('specs', '4:3', view, True)
        photo = canvas['photo']
        self.assertEqual(photo['radius'], .016)
        self.assertLess(photo['w'], .9); self.assertGreater(photo['x'], 0)              # inset on the canvas, not run off one edge
        roomy = picture(product=(.42, .4, .58, .6), parts={}, scenery=[(0, .8, 1, 1)])
        _, canvas, _, _ = composed('split', '1:1', roomy, True)
        self.assertNotIn('radius', canvas['photo']); self.assertAlmostEqual(canvas['photo']['x'] + canvas['photo']['w'], 1)   # full bleed when it can

    def test_notes_point_at_the_parts_they_name_without_crossing(self):
        for (name, (make, parts, generated)), aspect in itertools.product(VIEWS.items(), ASPECTS):
            with self.subTest(picture=name, aspect=aspect):
                view = make()
                content, canvas, items, _ = composed('callouts', aspect, view, generated)
                photo = frame_of(canvas)
                notes = [item for item in items if item['type'] == 'callout']
                self.assertEqual(len(notes), 3)
                self.assertEqual(len({round(item['x'], 6) for item in notes}), 1)       # one aligned column
                for item in notes:
                    part = parts[next(point['parts'][0] for point in content['points'] if point['title'] == item['title'])]
                    x, y = to_render(photo, item['anchor'])
                    self.assertTrue(part[0] - .01 <= x <= part[2] + .01 and part[1] - .01 <= y <= part[3] + .01, (item['title'], x, y, part))
                    self.assertEqual(item['style'], 'line'); self.assertGreater(item['x'], item['anchor'][0])
                ordered = sorted(notes, key=lambda item: item['y'])
                self.assertEqual([item['title'] for item in ordered], [item['title'] for item in sorted(notes, key=lambda item: item['anchor'][1])])

    def test_a_picture_read_without_its_scene_lists_its_points_without_lines(self):
        view = picture(parts=PARTS, estimated=True)
        content, _, items, _ = composed('callouts', '1:1', view)
        self.assertTrue(all(point['anchor'] is None for point in content['points']))
        notes = [item for item in items if item['type'] == 'callout']
        self.assertEqual([item['anchor'] for item in notes], [None, None, None])
        self.assertNotIn('callouts', compose.suitable(content, compose.palette(view), view)[:2])

    def test_the_layout_that_suits_the_words_and_the_picture_comes_first(self):
        studio = picture(parts=PARTS); anchored = copy.deepcopy(CONTENT); info_layer._point_anchors(anchored, studio)
        bare = {**anchored, 'figures': [], 'specs': []}
        self.assertEqual(compose.suitable(bare, compose.palette(studio), studio)[0], 'callouts')
        self.assertEqual(compose.suitable({**bare, 'points': bare['points'][:1]}, compose.palette(studio), studio)[0], 'split')
        self.assertEqual(compose.suitable({**bare, 'figures': CONTENT['figures']}, compose.palette(studio), studio)[0], 'spotlight')
        self.assertEqual(compose.suitable(anchored, compose.palette(studio), studio)[0], 'specs')        # two specifications, one a size range
        for scene, generated in ((picture(parts=PARTS, scenery=[(0, .5, .4, 1)]), False), (studio, True)):
            self.assertEqual(compose.suitable(anchored, compose.palette(scene, generated), scene)[0], 'scene')
        self.assertEqual(sorted(compose.suitable(bare, compose.palette(studio), studio)), sorted(compose.LAYOUTS))

    def test_a_layout_without_room_for_everything_says_what_it_left_out(self):
        many = {**CONTENT, 'figures': [{'value': str(100 + n), 'unit': 'kg', 'label': '承重%d' % n, 'icon': 'weight'} for n in range(4)]}
        _, _, items, report = composed('cards', '1:1', picture(parts=PARTS), content=many)
        shown = {item['value'] for item in items if item['type'] == 'figure'}
        self.assertEqual(shown, {'100', '101', '102'})                                # three small cards
        self.assertEqual(report['dropped'], ['抽屉收纳', '带轮移动', '带锁', '承重3', '外形尺寸', '扳手尺寸'])


class TypeTests(unittest.TestCase):
    def test_a_tag_in_spaced_capitals_is_measured_as_set_and_wraps_before_it_meets_the_headline(self):
        frame = compose.Frame('1:1')
        _, _, items, _ = composed('callouts', '1:1', picture(parts=PARTS), content=CONTENT_EN)
        kicker, headline = (next(item for item in items if item['id'] == name) for name in ('kicker', 'headline'))
        self.assertEqual(kicker['text'].split('\n'), ['WORKSHOP', 'STORAGE'])            # too wide for the column on one line once tracked
        self.assertGreaterEqual(headline['y'], kicker['y'] + compose._text_height(frame, kicker))
        _, _, wide, _ = composed('split', '1:1', picture(parts=PARTS), content=CONTENT_EN)
        self.assertEqual(next(item for item in wide if item['id'] == 'kicker')['text'], 'WORKSHOP STORAGE')


class LookTests(unittest.TestCase):
    def test_a_flat_sweep_is_extended_seamlessly_and_anything_else_keeps_hard_edges(self):
        white = compose.palette(picture(parts=PARTS, color=(214, 40, 34)))
        self.assertTrue(white['studio']); self.assertFalse(white['scene']); self.assertEqual(white['canvas'], '#f2f2f2')
        gradient = picture(parts=PARTS, color=(214, 40, 34))
        gradient.luminance = [.55 + .25 * (index // gradient.nx) / gradient.ny for index in range(gradient.nx * gradient.ny)]
        gradient.colors = [(round(v * 255),) * 3 if cell < 0 else (214, 40, 34) for v, cell in zip(gradient.luminance, gradient.cells)]
        self.assertGreater(gradient.edge_spread(), .045)
        self.assertFalse(compose.palette(gradient)['studio'])                       # a lit floor would show where the render ends
        mid = picture(parts=PARTS, light=.5)
        self.assertFalse(compose.palette(mid)['studio'])                            # mid-grey: neither ink reads well on it
        self.assertFalse(compose.palette(picture(parts=PARTS), generated=True)['studio'])

    def test_words_and_figures_can_be_read_on_the_canvas(self):
        for light, color in ((.95, (214, 40, 34)), (.1, (214, 40, 34)), (.7, (230, 190, 40)), (.95, (180, 182, 186))):
            with self.subTest(light=light, color=color):
                pal = compose.palette(picture(parts=PARTS, light=light, color=color))
                self.assertGreaterEqual(compose.contrast(pal['ink'], pal['canvas']), 7)
                self.assertGreaterEqual(compose.contrast(pal['soft'], pal['canvas']), 4.5)
                self.assertGreaterEqual(compose.contrast(pal['accent'], pal['canvas']), 3)
        red = compose.palette(picture(parts=PARTS, color=(214, 40, 34)))
        import colorsys
        hue, saturation, _ = colorsys.rgb_to_hsv(*(int(red['accent'][index:index + 2], 16) / 255 for index in (1, 3, 5)))
        self.assertTrue((hue < .04 or hue > .96) and saturation > .7)               # the product's own red marks the figures
        steel = compose.palette(picture(parts=PARTS, color=(180, 182, 186)))
        self.assertEqual(steel['accent'], steel['ink'])                              # brushed steel has no hue to take
        self.assertEqual(compose.readable('#6e6e73', '#ffffff', '#1d1d1f', 4.5), '#6e6e73')
        self.assertGreaterEqual(compose.contrast(compose.readable('#6e6e73', '#9fa1a5', '#1d1d1f', 4.5), '#9fa1a5'), 4.5)

    def test_a_headline_breaks_between_words_and_never_leaves_one_character_alone(self):
        for text in ('红色移动工具柜', '不锈钢台面工具柜', '车间工具柜收纳', '72件套工具组合'):
            with self.subTest(text=text):
                size, lines = compose._fit(text, (.066, .06, .054, .048, .044), .3, 2)
                self.assertEqual(''.join(lines), text)
                self.assertTrue(all(len(line) >= 2 for line in lines), lines)
        size, lines = compose._fit('红色工具柜', (.066, .06, .054), .6, 2)
        self.assertEqual((size, lines), (.066, ['红色工具柜']))                           # a short headline stays on one line, large


class SlotTests(unittest.TestCase):
    def test_every_item_that_shows_the_words_says_which_words_it_shows(self):
        for layout in compose.LAYOUTS:
            with self.subTest(layout=layout):
                content, _, items, _ = composed(layout, '1:1', picture(parts=PARTS))
                for item in items:
                    slot = item.get('slot')
                    if item['id'] in ('kicker', 'headline', 'subline'):
                        self.assertEqual(item['text'].replace('\n', ''), content[slot].upper() if slot == 'kicker' else content[slot])
                    elif item['type'] == 'callout':
                        group, index = slot.split('.'); self.assertEqual(content[group][int(index)]['title'], item['title'])
                    elif item['type'] == 'figure':
                        group, index = slot.split('.'); self.assertEqual(content[group][int(index)]['value'], item['value'])
                    elif item['type'] == 'chips':
                        group, index = slot.split('.'); self.assertEqual(content[group][int(index)]['values'], item['values'])
                    if slot:
                        info_layer.validate({'version': 1, 'items': [item], 'glyphs': glyphs()})


class NumberTests(unittest.TestCase):
    def test_a_number_is_shown_only_when_it_was_given(self):
        facts = '台面承重 120 kg，扳手 8 10 12 24 mm，重 1.5 kg'
        for value in ('120', '8–24', '1.5', '1.50', '24.0'):
            with self.subTest(value=value): self.assertTrue(compose.grounded(value, facts))
        for value in ('150', '8–30', '15', '', '大容量'):
            with self.subTest(value=value): self.assertFalse(compose.grounded(value, facts))
        self.assertEqual(compose.numbers_in('8,10,12 与 24.0'), {'8', '10', '12', '24'})

    def test_specifications_are_read_from_the_users_own_lines(self):
        specs, rest = info_layer.specs_from('扳手尺寸：8、10、12、14 mm\n台面承重 120 kg\n抽屉带滑轨\n规格: 8,10,12,13 mm\n外形 690x380x738 mm\n重量 1.5-2 kg')
        self.assertEqual(rest, ['抽屉带滑轨'])
        size, load, listed, outline, weight = specs
        self.assertEqual((size['value'], size['unit'], size['values']), ('8–14', 'mm', ['8', '10', '12', '14']))
        self.assertEqual(size['icon'], '扳手尺寸')                                   # looked up by what it names: a wrench
        self.assertEqual((load['label'], load['value'], load['unit'], load.get('values')), ('台面承重', '120', 'kg', None))
        self.assertEqual(listed['values'], ['8', '10', '12', '13'])                   # a list, not decimals
        self.assertEqual((outline['value'], weight['value']), ('690×380×738', '1.5–2'))
        self.assertIn('wrench', layer_icons.search(size['icon'], 3)[0]['name'])

    def test_the_rules_never_put_a_number_the_user_did_not_give_into_the_words(self):
        for language in ('en', 'zh'):
            content, _ = info_layer.content_rules(project(), '', language=language)
            said = ' '.join([content['kicker'], content['headline'], content['subline']] + [point['title'] + point['detail'] for point in content['points']])
            self.assertNotRegex(said, r'\d')
            self.assertEqual((content['figures'], content['specs']), ([], []))
        sized, _ = info_layer.content_rules(project(dimensions_mm=[690, 380.5, 738]), '台面承重 120 kg', language='zh')
        self.assertEqual([(row['label'], row['value']) for row in sized['specs']], [('台面承重', '120'), ('外形尺寸', '690×380×738')])
        self.assertEqual([row['value'] for row in sized['figures']], ['120'])


if __name__ == '__main__':
    unittest.main()

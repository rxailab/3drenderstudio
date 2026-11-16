"""Components, the brand kit, the page renderer and the listing built from them.

Blender is replaced by a stand-in that paints a plain picture, except in the runtime class
at the end, which runs blender/stage.py on a small model when Blender is installed. No test
reaches a model service.
"""
import io
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import brand_kit
import components
import engine
import info_layer
import listing
import listing_draw as draw
import listing_elements as el
import listing_pages
import studio_store as store
from blender import stage_geometry as geometry
from test_scene_images import project

PARTS = ['G-07C抽屉(2)_8', 'G-不锈钢台面_2']


def grid(nu, nv, solid):
    return [bool(solid(x, y)) for y in range(nv) for x in range(nu)]


def staged(folder, request, size=240):
    """What blender/stage.py would return for a request: a red product in the middle, a pale workpiece across it when asked."""
    folder.mkdir(parents=True, exist_ok=True)
    width, height = request.get('size', [size, size])
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0)); pen = ImageDraw.Draw(image)
    box = [.3, .35, .7, .75]; nx = ny = 48; names = list(PARTS); cells = [-1] * (nx * ny)
    piece = request.get('workpiece')
    if piece:
        names.append('Listing workpiece')
        pen.rectangle((0, round(height * .5), width, round(height * .58)), fill=(235, 235, 230, 255))
        for y in range(int(ny * .5), int(ny * .58) + 1):
            for x in range(nx):
                cells[y * nx + x] = 2
    pen.rectangle((round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height)), fill=(200, 40, 30, 255))
    for y in range(int(box[1] * ny), int(box[3] * ny)):
        for x in range(int(box[0] * nx), int(box[2] * nx)):
            cells[y * nx + x] = 0 if x < nx / 2 else 1
    image.save(folder / 'picture.png')
    result = {'success': True, 'width': width, 'height': height, 'unit_mm': 150.0, 'azimuth': request['camera'].get('azimuth', 35), 'elevation': 18,
              'projection': request['camera'].get('projection', 'perspective'), 'dimensions_mm': [300.0, 120.0, 180.0], 'product_rect': box,
              'parts': [{'name': PARTS[0], 'rect': [.3, .35, .5, .75]}, {'name': PARTS[1], 'rect': [.5, .35, .7, .75]}],
              'probe': {'success': True, 'nx': nx, 'ny': ny, 'cells': cells, 'objects': names},
              'dimension_edges': [{'axis': 'x', 'mm': 300.0, 'p1': [.3, .78], 'p2': [.7, .78], 'apparent': .4},
                                  {'axis': 'y', 'mm': 120.0, 'p1': [.7, .78], 'p2': [.74, .74], 'apparent': .06},
                                  {'axis': 'z', 'mm': 180.0, 'p1': [.74, .75], 'p2': [.74, .35], 'apparent': .4}],
              'warnings': [], 'image': 'picture.png', 'seconds': .1}
    if piece:
        result['workpiece'] = {'kind': piece['kind'], 'material': piece.get('material', 'pvc_white'), 'mode': piece['place']['mode'], 'notes': [], 'length_mm': 900.0,
                               'axis': [1, 0, 0], 'diameter_mm': piece.get('diameter_mm') or 32.0, 'opening_mm': 36.0, 'rect': [0, .5, 1, .58],
                               'anchors': {'diameter': {'mm': piece.get('diameter_mm') or 32.0, 'p1': [.15, .5], 'p2': [.15, .58]}}}
    return {**result, 'folder': folder, 'path': folder / 'picture.png', 'reused': False}


class GeometryTests(unittest.TestCase):
    def test_a_hole_and_a_jaw_are_openings_and_the_outside_of_a_corner_is_not(self):
        ring = grid(40, 40, lambda x, y: 8 <= x < 32 and 8 <= y < 32 and not (14 <= x < 26 and 14 <= y < 26))
        x, y, radius, enclosure = geometry.largest_opening(ring, 40, 40)
        self.assertTrue(18 <= x <= 21 and 18 <= y <= 21, (x, y)); self.assertEqual(enclosure, 1.0); self.assertTrue(5 <= radius <= 6.5, radius)
        jaw = grid(40, 40, lambda x, y: 5 <= x < 35 and 5 <= y < 35 and not (15 <= x < 35 and 14 <= y < 26))       # a C, open to the right
        x, y, radius, enclosure = geometry.largest_opening(jaw, 40, 40)
        self.assertTrue(15 <= x < 30 and 14 <= y < 26, (x, y)); self.assertTrue(.7 <= enclosure < 1, enclosure); self.assertTrue(4.5 <= radius <= 6, radius)
        corner = grid(40, 40, lambda x, y: (5 <= x < 15 and 5 <= y < 35) or (5 <= x < 35 and 5 <= y < 15))           # an L: nothing closes round it
        self.assertIsNone(geometry.largest_opening(corner, 40, 40))
        self.assertIsNone(geometry.largest_opening([False] * 100, 10, 10))
        self.assertIsNone(geometry.largest_opening([True] * 100, 10, 10))

    def test_a_small_gap_does_not_win_over_the_working_opening(self):
        tool = grid(60, 40, lambda x, y: 4 <= x < 56 and 4 <= y < 36 and not (30 <= x < 56 and 12 <= y < 28) and not (10 <= x < 13 and 18 <= y < 21))
        x, y, radius, _ = geometry.largest_opening(tool, 60, 40)
        self.assertTrue(x >= 30 and radius >= 6, (x, y, radius))

    def test_stock_sizes_leave_room_and_lengths_are_printed_plainly(self):
        self.assertEqual(geometry.stock_size(25), 22.0); self.assertEqual(geometry.stock_size(52), 42.0); self.assertEqual(geometry.stock_size(35.2), 32.0)
        self.assertEqual(geometry.stock_size(4), 3.2)                                     # nothing standard fits: a plain fraction
        self.assertEqual(geometry.stock_size(20, geometry.BOARD_THICKNESSES), 18.0)
        self.assertEqual((geometry.dimension_text(225.4), geometry.dimension_text(6.5), geometry.dimension_text(254, 'in')), ('225 mm', '6.5 mm', '10 in'))
        self.assertEqual(geometry.dimension_text(31.75, 'in'), '1.2 in')

    def test_a_models_own_units_are_only_a_guess_at_its_size(self):
        self.assertEqual(geometry.model_size_mm(.215, '.glb'), 215.0)                     # glTF is in metres
        self.assertEqual(geometry.model_size_mm(738, '.obj'), 738.0)                      # an OBJ this large is in millimetres
        self.assertEqual(geometry.model_size_mm(1.2, '.obj'), 1200.0)                     # this small, in metres
        self.assertIsNone(geometry.model_size_mm(0, '.glb'))


class DrawTests(unittest.TestCase):
    HEAD = {'font': 'archivo', 'weight': 900, 'width': 125, 'size': 60, 'case': 'upper'}

    def test_width_follows_the_face_its_width_axis_and_tracking(self):
        wide = draw.text_width('Kit includes', self.HEAD); narrow = draw.text_width('Kit includes', {**self.HEAD, 'width': 62})
        self.assertGreater(wide, narrow * 1.5)
        self.assertGreater(draw.text_width('Kit includes', {**self.HEAD, 'tracking': .1}), wide + 60)
        self.assertEqual(draw.text_width('{Kit} includes', self.HEAD), wide)              # the marks take no room
        self.assertGreater(draw.text_width('kit includes', self.HEAD), draw.text_width('kit includes', {**self.HEAD, 'case': 'none'}))

    def test_text_wraps_at_spaces_keeps_its_accent_and_is_never_cut(self):
        lines = draw.wrap('High {performance} motor', self.HEAD, 700)
        self.assertEqual(lines, [[('HIGH', False)], [('PERFORMANCE', True)], [('MOTOR', False)]])
        for line in lines:
            self.assertLessEqual(draw.line_width(line, self.HEAD), 700)
        shrunk, tight = draw.fit('High performance motor', self.HEAD, 520, 3)       # a word wider than the column: the type shrinks until it fits
        self.assertTrue(shrunk['size'] < 60 and all(draw.line_width(line, shrunk) <= 520.5 for line in tight))
        self.assertEqual(draw.wrap('A\nB', self.HEAD, 500), [[('A', False)], [('B', False)]])
        style, fitted = draw.fit('Two-position side handle', self.HEAD, 300, 2)
        self.assertLess(style['size'], 60)
        self.assertEqual(''.join(part for line in fitted for part, _ in line).replace(' ', ''), 'TWO-POSITIONSIDEHANDLE')
        # Even when it cannot fit in the lines asked for, every word is still there.
        _, crowded = draw.fit('Hardened replaceable cutting wheel assembly', self.HEAD, 200, 1)
        self.assertEqual(sum(len(part.replace(' ', '')) for line in crowded for part, _ in line), len('Hardenedreplaceablecuttingwheelassembly'))
        self.assertGreaterEqual(len(draw.wrap('可拆卸的不锈钢切割轮', {'font': 'inter', 'size': 40}, 130)), 3)       # Chinese breaks anywhere

    def test_a_character_the_display_face_lacks_is_drawn_by_the_text_face(self):
        self.assertEqual(draw._runs('TORQUE', 'orbitron'), [('TORQUE', '')])
        self.assertEqual(draw._runs('IN USE · PIPE', 'orbitron'), [('IN USE ', ''), ('· ', 'plain'), ('PIPE', '')])
        self.assertEqual(draw._runs('20V 电池', 'archivo'), [('20V ', ''), ('电池', 'cjk')])
        self.assertEqual(draw._runs('Ø 25 mm', 'archivo'), [('Ø 25 mm', '')])

    def test_a_dimension_is_a_span_moved_clear_with_witness_lines_and_a_label(self):
        lines, middle, normal = draw.dimension_parts({'p1': [100, 500], 'p2': [500, 500], 'offset': 40})
        self.assertEqual(lines[0]['points'], [(100.0, 540.0), (500.0, 540.0)]); self.assertEqual(len(lines), 3)
        self.assertEqual((middle, normal), ((300.0, 540.0), (0.0, 1.0)))
        _, moved, _ = draw.dimension_parts({'p1': [100, 500], 'p2': [500, 500], 'offset': 40, 'label_at': .25})
        self.assertEqual(moved, (200.0, 540.0))
        self.assertEqual(len(draw.dimension_parts({'p1': [0, 0], 'p2': [0, 100], 'offset': 0})[0]), 1)       # on the object: no witness lines

    def test_every_item_type_paints_and_the_page_has_the_size_asked_for(self):
        with tempfile.TemporaryDirectory() as temp:
            picture = Path(temp) / 'cut.png'
            cut = Image.new('RGBA', (120, 90), (0, 0, 0, 0)); ImageDraw.Draw(cut).ellipse((10, 10, 110, 80), fill=(200, 30, 30, 255)); cut.save(picture)
            import layer_icons
            items = [{'type': 'rect', 'x': 0, 'y': 0, 'w': 1000, 'h': 120, 'fill': '#141414'},
                     {'type': 'rect', 'x': 40, 'y': 160, 'w': 300, 'h': 200, 'fill': '#1b6fd8', 'radius': 20, 'gradient': {'to': '#0b3c86'}, 'shadow': {'dy': 8, 'blur': 10}},
                     {'type': 'poly', 'points': [[400, 160], [500, 160], [540, 260], [400, 260]], 'fill': '#f5b800'},
                     {'type': 'ellipse', 'x': 600, 'y': 160, 'w': 120, 'h': 120, 'stroke': '#dd2222', 'stroke_width': 5, 'dash': [14, 8]},
                     {'type': 'line', 'points': [[60, 420], [200, 420], [200, 480]], 'color': '#f5b800', 'width': 4, 'start': 'square', 'end': 'arrow'},
                     {'type': 'line', 'points': [[260, 420], [460, 480]], 'color': '#111111', 'width': 2, 'dash': [10, 6], 'end': 'ring', 'ground': '#ffffff'},
                     {'type': 'text', 'x': 48, 'y': 44, 'w': 600, 'text': 'Kit {includes} 套装', 'style': {**self.HEAD, 'size': 44, 'color': '#ffffff', 'accent': '#f5b800'}},
                     {'type': 'text', 'x': 520, 'y': 420, 'w': 440, 'text': 'Outlined', 'style': {'font': 'orbitron', 'weight': 800, 'size': 44, 'outline': {'color': '#f5b800', 'width': 3},
                                                                                              'shadow': {'dx': 4, 'dy': 4}, 'gradient': ['#ffd84a', '#e06b00']}},
                     {'type': 'image', 'src': str(picture), 'x': 60, 'y': 560, 'w': 300, 'h': 240, 'shadow': {'kind': 'ground'}},
                     {'type': 'image', 'src': str(picture), 'x': 420, 'y': 560, 'w': 200, 'h': 200, 'fit': 'cover', 'mask': 'circle', 'stroke': '#111111'},
                     {'type': 'icon', 'glyph': layer_icons.glyph('lucide:battery'), 'x': 700, 'y': 560, 'size': 120, 'color': '#111111'},
                     {'type': 'dimension', 'p1': [100, 900], 'p2': [500, 900], 'offset': 30, 'label': '225 mm', 'plate': '#ffffff'},
                     {'type': 'unknown', 'x': 0, 'y': 0}]
            items += [{'type': 'texture', 'kind': kind, 'x': 640 + (index % 3) * 110, 'y': 700 + (index // 3) * 110, 'w': 100, 'h': 100, 'color': '#333333', 'opacity': .6}
                      for index, kind in enumerate(draw.TEXTURES)]
            image = draw.render({'height': 1000, 'background': '#ffffff', 'items': items}, 400)
            self.assertEqual((image.size, image.mode), ((400, 400), 'RGB'))
            self.assertEqual(image.getpixel((380, 10)), (20, 20, 20))                    # the band
            self.assertEqual(image.getpixel((390, 150)), (255, 255, 255))                # untouched ground
            red, green, blue = image.getpixel((84, 270)); self.assertTrue(red > 150 and green < 90, (red, green, blue))       # the cut-out
            self.assertNotEqual(image.getpixel((120, 372)), (255, 255, 255))             # the dimension line
            tall = draw.render({'height': 1250, 'background': '#101010', 'items': []}, 200)
            self.assertEqual((tall.size, tall.getpixel((5, 5))), ((200, 250), (16, 16, 16)))

    def test_colours_know_what_reads_on_them(self):
        self.assertEqual((draw.on('#ffffff'), draw.on('#16171a'), draw.on('#f5b800')), ('#111111', '#ffffff', '#111111'))
        self.assertGreater(draw.contrast('#000000', '#ffffff'), 20); self.assertEqual(draw.mix('#000000', '#ffffff', .5), '#808080')
        self.assertEqual(draw.color('#fa0'), (255, 170, 0, 255))


class BrandTests(unittest.TestCase):
    def test_a_brand_record_is_checked_and_keeps_what_was_not_sent(self):
        record = brand_kit.validate({'preset': 'tech', 'name': ' NORTHFORGE ', 'accent': '#F5B800', 'badges': '20V, PRO；X', 'legal': 'a\nb'})
        self.assertEqual((record['preset'], record['name'], record['accent'], record['badges'], record['legal'], record['unit']),
                         ('tech', 'NORTHFORGE', '#f5b800', ['20V', 'PRO', 'X'], 'a b', 'mm'))
        kept = brand_kit.validate({'unit': 'in'}, record)
        self.assertEqual((kept['preset'], kept['accent'], kept['unit']), ('tech', '#f5b800', 'in'))
        self.assertIsNone(brand_kit.validate({'accent': None}, record)['accent'])
        for bad in ({'preset': 'dewalt'}, {'accent': 'red'}, {'badges': ['a', 'b', 'c', 'd']}, {'unit': 'cm'}, {'logo': 'https://example.com/x.png'}, {'name': 'x' * 41}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                brand_kit.validate(bad)
        self.assertEqual([row['id'] for row in brand_kit.presets()], ['frame', 'flat', 'tech', 'clean', 'promo'])
        for row in brand_kit.presets():
            self.assertTrue(row['label']['zh'] and row['label']['en'] and row['note']['zh'])

    def test_the_presets_name_no_brand(self):
        text = json.dumps(brand_kit.PRESETS, ensure_ascii=False).lower() + Path(brand_kit.__file__).read_text(encoding='utf-8').lower()
        for mark in ('dewalt', 'milwaukee', 'makita', 'bosch', 'greenworks', 'black+decker', 'hoto', 'seesii', 'workpro', 'ryobi'):
            self.assertNotIn(mark, text)

    def test_the_accent_is_the_products_own_colour_unless_the_user_sets_one(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cut.png'
            image = Image.new('RGBA', (80, 80), (0, 0, 0, 0)); pen = ImageDraw.Draw(image)
            pen.rectangle((10, 10, 70, 50), fill=(205, 40, 30, 255)); pen.rectangle((10, 50, 70, 70), fill=(20, 20, 22, 255)); image.save(path)
            found = brand_kit.dominant_color(path)
            red, green, blue, _ = draw.color(found); self.assertTrue(red > 180 and green < 100 and blue < 100, found)
            Image.new('RGBA', (80, 80), (120, 122, 125, 255)).save(path)
            self.assertIsNone(brand_kit.dominant_color(path))                             # a grey product has no colour to lend
            self.assertIsNone(brand_kit.dominant_color(Path(temp) / 'missing.png'))
        self.assertEqual((brand_kit.Brand({}, '#d9463d').accent, brand_kit.Brand({}, '#d9463d').accent_source), ('#d9463d', 'product'))
        self.assertEqual(brand_kit.Brand({'accent': '#0a84ff'}, '#d9463d').accent_source, 'user')
        self.assertEqual((brand_kit.Brand({}).accent, brand_kit.Brand({}).accent_source), (brand_kit.DEFAULT_ACCENT, 'default'))

    def test_ink_and_accent_always_read_on_the_ground_they_stand_on(self):
        for preset in brand_kit.PRESETS:
            for accent in ('#f5b800', '#111111', '#ffffff', '#d9463d', '#0a84ff'):
                with self.subTest(preset=preset, accent=accent):
                    brand = brand_kit.Brand({'preset': preset, 'accent': accent})
                    self.assertGreaterEqual(draw.contrast(brand.ink, brand.ground), 4.0)       # the better of black and white, even on a mid tone
                    self.assertGreaterEqual(draw.contrast(brand.accent_on_ground, brand.ground), 2.6)
                    self.assertGreaterEqual(draw.contrast(brand.accent_on_dark, brand.dark), 2.6)
                    self.assertGreaterEqual(draw.contrast(brand.ink_for(brand.panel), brand.panel), 4.0)

    def test_a_flat_page_in_the_products_own_colour_is_deepened_so_the_product_still_shows(self):
        own = brand_kit.Brand({'preset': 'flat'}, '#d9463d'); chosen = brand_kit.Brand({'preset': 'flat', 'accent': '#d9463d'})
        self.assertEqual(chosen.ground, '#d9463d')
        self.assertLess(draw.luminance(own.ground), draw.luminance('#d9463d') * .5)
        self.assertEqual(brand_kit.Brand({'preset': 'frame'}, '#d9463d').ground, '#ffffff')

    def test_each_preset_sets_type_its_own_way(self):
        styles = {preset: brand_kit.Brand({'preset': preset}).headline(60) for preset in brand_kit.PRESETS}
        self.assertEqual((styles['frame']['width'], styles['frame']['case']), (125, 'upper'))
        self.assertEqual(styles['tech']['font'], 'orbitron')
        self.assertTrue(styles['promo']['italic'] and styles['promo']['width'] < 75 and styles['promo'].get('shadow'))
        self.assertEqual((styles['clean']['case'], styles['flat']['case']), ('none', 'none'))
        self.assertEqual({brand_kit.PRESETS[preset]['leader']['route'] for preset in brand_kit.PRESETS}, {'elbow', 'straight', 'angled'})


class ElementTests(unittest.TestCase):
    def test_every_element_of_the_library_has_a_builder(self):
        for name in el.ELEMENTS:
            self.assertTrue(callable(getattr(el, name, None)), name)
        self.assertGreaterEqual(len(el.ELEMENTS), 20)

    def test_the_frame_leaves_a_content_box_inside_the_page_in_every_preset(self):
        for preset in brand_kit.PRESETS:
            for title in ('Kit includes', None):
                with self.subTest(preset=preset, title=title):
                    brand = brand_kit.Brand({'preset': preset, 'name': 'NORTHFORGE', 'badges': ['20V'], 'legal': '*A legal line.'})
                    items, box = el.frame(brand, 1000, title)
                    self.assertTrue(40 <= box[0] < box[2] <= 960 and 0 < box[1] < box[3] <= 1000 and box[3] - box[1] > 600, box)
                    self.assertTrue(all(item['type'] in draw.TYPES for item in items))
                    words = ' '.join(''.join(part for line in item.get('lines', []) for part, _ in line) + item.get('text', '') for item in items if item['type'] == 'text')
                    self.assertIn('legal line', words.lower())
                    if title:
                        self.assertIn('KIT INCLUDES', words.upper())
        banded = el.frame(brand_kit.Brand({'preset': 'frame', 'badges': ['20V']}), 1000, None)[0]
        self.assertIn('20V', [item.get('text') for item in banded if item['type'] == 'text'])       # no title: the badge stands in the band

    def test_a_header_band_with_nothing_to_say_names_the_product(self):
        bare = brand_kit.Brand({'preset': 'frame', 'name': '', 'badges': []})
        items, _ = el.frame(bare, 1000, None, fallback='Cordless Angle Grinder')
        self.assertIn('CORDLESS ANGLE GRINDER', ' '.join(''.join(part for line in item.get('lines') or [] for part, _ in line) for item in items if item['type'] == 'text').upper())
        badged = brand_kit.Brand({'preset': 'frame', 'badges': ['20V']})
        self.assertNotIn('Cordless', str(el.frame(badged, 1000, None, fallback='Cordless')[0]))      # a badge already fills it

    def test_a_logo_takes_the_place_of_the_name(self):
        brand = brand_kit.Brand({'preset': 'frame', 'name': 'NORTHFORGE'}); brand.logo_path = '/tmp/logo.png'
        items, _ = el.frame(brand, 1000, 'Kit includes')
        self.assertEqual([item['src'] for item in items if item['type'] == 'image'], ['/tmp/logo.png'])
        self.assertNotIn('NORTHFORGE', [item.get('text') for item in items if item['type'] == 'text'])

    def test_a_headline_without_marks_gets_one_accent_phrase(self):
        brand = brand_kit.Brand({'preset': 'frame', 'accent': '#f5b800'})
        (item,), height = el.headline(brand, 'High performance motor', 0, 0, 520, 80)
        self.assertEqual([[accent for _, accent in line] for line in item['lines']], [[False], [True], [False]]); self.assertGreater(height, 100)
        (one,), _ = el.headline(brand, 'Clean cuts', 0, 0, 900, 60)
        self.assertEqual(one['lines'], [[('CLEAN ', False), ('CUTS', True)]])
        (own,), _ = el.headline(brand, '{One-hand} clean cuts', 0, 0, 900, 40)
        self.assertEqual(own['lines'], [[('ONE-HAND', True), (' CLEAN CUTS', False)]])
        (plain,), _ = el.headline(brand, 'Clean cuts', 0, 0, 900, 60, two_tone=False)
        self.assertFalse(any(accent for line in plain['lines'] for _, accent in line))

    def test_a_leader_follows_the_brands_manner_and_never_runs_through_its_label(self):
        frame = brand_kit.Brand({'preset': 'frame'}); clean = brand_kit.Brand({'preset': 'clean'})
        elbow = el.leader(frame, (200, 300), (420, 480))
        self.assertEqual((elbow['points'], elbow['end']), ([[200, 300], [420, 300], [420, 480]], 'square'))
        self.assertEqual(len(el.leader(clean, (200, 300), (420, 480))['points']), 3); self.assertEqual(el.leader(clean, (200, 300), (420, 480))['end'], 'dot')
        beside, _ = el.callout(frame, 'Feed knob', '', (500, 310), 700, 300, 200, 'right')
        self.assertEqual(beside[0]['points'][0][0], 686)                                  # leaves from the label's inner edge
        above, _ = el.callout(frame, 'Feed knob', '', (780, 120), 700, 300, 200, 'right')
        line = above[0]['points']
        self.assertEqual(len(line), 2); self.assertEqual(line[0][0], 780); self.assertLess(line[0][1], 300)       # straight up from the label, not across its words

    def test_small_elements_report_the_room_they_take(self):
        brand = brand_kit.Brand({'preset': 'frame'})
        for kind in ('box', 'pill', 'hex', 'burst'):
            items, width = el.badge(brand, '20V', 0, 0, 60, kind)
            self.assertTrue(items and width >= 60, kind)
        items, height = el.chip_row(brand, ['One', 'Two', 'A much longer third chip'], 0, 0, 260, 40)
        self.assertGreater(height, 40)                                                   # wrapped to a second row
        self.assertTrue(all(item['x'] + 1 < 400 for item in items if item['type'] == 'text'))
        rows, table_height = el.table(brand, [('Voltage', '20 V'), ('Speed', '8,500 rpm')], 0, 0, 400, 50)
        self.assertEqual(table_height, 100)
        stat, _ = el.stat(brand, '8,500', 'rpm', 'No-load speed', 0, 0, 120)
        self.assertLessEqual(draw.text_width('8,500', stat[0]['style']) + draw.text_width(' rpm', stat[1]['style']), 121)       # shrunk to its column
        self.assertEqual(el.shifted({'type': 'line', 'points': [[1, 2]], 'x': 1, 'y': 2}, 10, 20), {'type': 'line', 'points': [[11, 22]], 'x': 11, 'y': 22})


class StoreCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for module in (store, engine, components, listing):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        for target, name, value in ((engine, 'QUEUE', queue.Queue()), (components, 'QUEUE', queue.Queue())):
            mocked = patch.object(target, name, value); mocked.start(); self.addCleanup(mocked.stop)
        for target, name, value in ((engine, 'require_runtime', None), (engine, 'blender_path', '/usr/bin/blender')):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(info_layer, '_config', return_value=None); mocked.start(); self.addCleanup(mocked.stop)       # no model service
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        record = project('Ratchet Pipe Cutter'); record['model_path'] = str(self.data / 'fixture' / 'originals' / 'cutter.glb')
        store.put('project', record)
        self.requests = []

    def stage(self, project_id, request, cancelled=None, on_progress=None):
        self.requests.append(request)
        if on_progress: on_progress(.5)
        return staged(self.data / project_id / 'stage' / ('s%d' % len(self.requests)), request)

    def picture(self, name='battery.png', size=(300, 200), ground=(255, 255, 255)):
        base = self.data / 'upload' / name; base.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new('RGB', size, ground); ImageDraw.Draw(image).rectangle((60, 50, 240, 150), fill=(30, 30, 34)); image.save(base)
        return base

    def add_picture(self, **fields):
        component_id, base = components.reserve('fixture')
        shutil.copy(self.picture(), base / 'battery.png')
        record = components.register('fixture', component_id, fields)
        components.process('fixture', component_id, store.get('project', 'fixture')['components'][-1]['token'])
        return next(row for row in store.get('project', 'fixture')['components'] if row['id'] == record['id'])


class ComponentTests(StoreCase):
    def test_a_picture_on_a_plain_ground_becomes_a_trimmed_cut_out(self):
        row = self.add_picture(name='Battery', sku='BP-205', quantity='2', caption='20V 5.0Ah')
        self.assertEqual((row['status'], row['kind'], row['role'], row['quantity'], row['progress']), ('ready', 'image', 'battery', 2, 100))
        path = components.path_of('fixture', row['cutout']['url'])
        with Image.open(path) as image:
            self.assertEqual(image.mode, 'RGBA'); self.assertTrue(abs(image.width - 185) <= 6 and abs(image.height - 105) <= 6, image.size)
            self.assertEqual(image.getpixel((image.width // 2, image.height // 2))[3], 255); self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertIn('已按纯色背景自动抠图。', row['notes'])

    def test_a_picture_with_its_own_transparency_is_used_as_it_is_and_a_busy_ground_is_said(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'a.png'; target = Path(temp) / 'out.png'
            image = Image.new('RGBA', (100, 100), (0, 0, 0, 0)); ImageDraw.Draw(image).rectangle((20, 30, 80, 70), fill=(200, 0, 0, 255)); image.save(source)
            size, notes = components.cut_out_picture(source, target)
            self.assertEqual(notes, ['使用图片自带的透明背景。']); self.assertTrue(60 <= size[0] <= 66 and 40 <= size[1] <= 46, size)
            busy = Image.new('RGB', (100, 100)); busy.putdata([((x * 7) % 255, (y * 5) % 255, 90) for y in range(100) for x in range(100)]); busy.save(source)
            _, notes = components.cut_out_picture(source, target)
            self.assertIn('背景不是纯色', notes[0])

    def test_a_model_is_analysed_posed_staged_and_trimmed_like_the_product(self):
        import orientation
        component_id, base = components.reserve('fixture'); (base / 'charger.glb').write_bytes(b'glTF')
        record = components.register('fixture', component_id, {'name': 'Fast Charger', 'sku': 'CH-118'})
        self.assertEqual((record['kind'], record['role'], record['status']), ('model', 'charger', 'queued'))
        analysis = {'success': True, 'features': {'dimensions': [.15, .11, .08]}, 'objects': [{'name': 'Base', 'type': 'MESH', 'materials': ['Black']}], 'materials': [{'name': 'Black'}]}
        judged = {'rotation': [180.0, 0.0, 0.0], 'changed': True, 'source': 'names', 'chosen': 'flip', 'reason': '底座在下。'}
        with patch.object(components, 'run_blender', return_value=analysis) as ran, patch.object(orientation, 'decide', return_value=judged), \
                patch.object(components, 'stage', side_effect=self.stage):
            components.process('fixture', component_id, store.get('project', 'fixture')['components'][0]['token'])
        row = store.get('project', 'fixture')['components'][0]
        self.assertEqual((row['status'], row['rotation'], row['pose']['chosen'], row['size_mm'], row['size_source']), ('ready', [180.0, 0.0, 0.0], 'flip', 150.0, 'model'))
        self.assertEqual(ran.call_args.args[1]['action'], 'analyze')
        request = self.requests[0]
        self.assertEqual((request['plan']['rotation'], request['plan']['look'], request['ground'], request['size_mm']), ([180.0, 0.0, 0.0], 'showcase', 'none', 150.0))
        with Image.open(components.path_of('fixture', row['cutout']['url'])) as image:
            self.assertTrue(image.width < components.CUTOUT_PX * .5 and image.height < components.CUTOUT_PX * .5, image.size)       # trimmed to the product, not the whole render
        self.assertTrue(any('估计' in note for note in row['notes']))
        # A turn by hand keeps the pose the user set and renders again; a size they enter is taken as real.
        again = components.update('fixture', component_id, {'turn': 'left', 'size_mm': '160'})
        self.assertEqual((again['status'], again['size_mm'], again['size_source']), ('queued', 160.0, 'user'))
        with patch.object(components, 'run_blender', return_value=analysis), patch.object(orientation, 'decide', side_effect=AssertionError('not judged again')), \
                patch.object(components, 'stage', side_effect=self.stage):
            components.process('fixture', component_id, store.get('project', 'fixture')['components'][0]['token'])
        row = store.get('project', 'fixture')['components'][0]
        self.assertEqual((row['status'], row['size_mm'], row['size_source']), ('ready', 160.0, 'user'))
        self.assertTrue(orientation.same(row['rotation'], orientation.turned([180, 0, 0], 90)))
        self.assertEqual(self.requests[-1]['size_mm'], 160.0)

    def test_a_failure_is_recorded_on_the_component_and_can_be_tried_again(self):
        component_id, base = components.reserve('fixture'); (base / 'case.glb').write_bytes(b'glTF')
        record = components.register('fixture', component_id, {})
        with patch.object(components, 'run_blender', side_effect=ValueError('渲染失败：模型没有可渲染网格')):
            components.process('fixture', component_id, store.get('project', 'fixture')['components'][0]['token'])
        row = store.get('project', 'fixture')['components'][0]
        self.assertEqual((row['status'], row['error']), ('failed', '渲染失败：模型没有可渲染网格'))
        self.assertEqual(components.refresh('fixture', component_id)['status'], 'queued')
        self.assertEqual(components.QUEUE.qsize(), 2)
        # A stale worker (an older token) changes nothing.
        with patch.object(components, 'run_blender', side_effect=AssertionError('stale work must not run')):
            components.process('fixture', component_id, record.get('token', 'old'))
        self.assertEqual(store.get('project', 'fixture')['components'][0]['status'], 'queued')

    def test_what_is_uploaded_is_checked(self):
        component_id, base = components.reserve('fixture'); (base / 'a.glb').write_bytes(b'x'); (base / 'b.obj').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, '每个组件请上传一个'):
            components.register('fixture', component_id, {})
        self.assertFalse((self.data / 'fixture' / 'components' / component_id).exists())       # nothing is left behind
        component_id, base = components.reserve('fixture'); (base / 'notes.txt').write_text('x')
        with self.assertRaisesRegex(ValueError, '请为组件上传'):
            components.register('fixture', component_id, {})
        for bad in ({'role': 'weapon'}, {'quantity': 0}, {'quantity': '1.5'}, {'size_mm': 'big'}, {'size_mm': -4}, {'name': 'x' * 61}, {'included': 'yes'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                components.fields(bad)
        self.assertEqual(components.fields({'name': ' A\tB '})['name'], 'A B')
        self.assertEqual([components.guess_role(name) for name in ('20V battery pack', 'Fast Charger', '工具箱', 'Cutting disc', 'Widget')],
                         ['battery', 'charger', 'case', 'blade', 'other'])
        with patch.object(components, 'MAX_COMPONENTS', 0), self.assertRaisesRegex(ValueError, '最多'):
            components.reserve('fixture')

    def test_editing_removing_and_a_restart(self):
        row = self.add_picture(name='Battery')
        edited = components.update('fixture', row['id'], {'sku': 'BP-205', 'quantity': 2, 'included': False})
        self.assertEqual((edited['sku'], edited['quantity'], edited['included'], edited['status']), ('BP-205', 2, False, 'ready'))
        with self.assertRaisesRegex(ValueError, '图片组件不能调整朝向'):
            components.update('fixture', row['id'], {'turn': 'flip'})
        with self.assertRaises(KeyError):
            components.update('fixture', 'missing', {})
        self.assertNotIn('token', components.public(store.get('project', 'fixture')['components'][0]))
        self.assertNotIn('source', components.public(store.get('project', 'fixture')['components'][0]))
        saved = store.get('project', 'fixture'); saved['components'][0]['status'] = 'processing'; store.put('project', saved)
        components.recover_interrupted()
        self.assertEqual(store.get('project', 'fixture')['components'][0]['status'], 'queued')
        self.assertEqual(components.remove('fixture', row['id'])['removed'], row['id'])
        self.assertEqual(store.get('project', 'fixture')['components'], [])
        self.assertFalse((self.data / 'fixture' / 'components' / row['id']).exists())

    def test_a_stored_address_only_opens_this_projects_own_files(self):
        row = self.add_picture()
        self.assertTrue(components.path_of('fixture', row['cutout']['url']).is_file())
        (self.data / 'other').mkdir(); (self.data / 'other' / 'x.png').write_bytes(b'x')
        for bad in ('/files/other/x.png', '/files/fixture/../other/x.png', 'https://example.com/a.png', None, '/files/fixture/missing.png', row['cutout']['url'][:-4] + '.json'):
            self.assertIsNone(components.path_of('fixture', bad), bad)

    def test_a_staged_picture_is_rendered_once_per_request(self):
        model = Path(store.get('project', 'fixture')['model_path']); model.parent.mkdir(parents=True); model.write_bytes(b'glTF')
        request = components.cutout_request(model, {'rotation': [0, 0, 0]}, 225)
        runs = []
        def blender(script, sent, folder, cancelled=None, on_progress=None, timeout=0, key=None):
            runs.append(sent); made = staged(Path(folder), sent)
            result = {key: value for key, value in made.items() if key not in ('folder', 'path', 'reused')}
            (Path(folder) / 'result.json').write_text(json.dumps(result), encoding='utf-8')       # as the script itself leaves it
            return result
        with patch.object(components, 'run_blender', side_effect=blender):
            first = components.stage('fixture', request); second = components.stage('fixture', request)
            other = components.stage('fixture', {**request, 'size_mm': 300.0})
            model.write_bytes(b'glTF changed'); third = components.stage('fixture', request)
        self.assertEqual((len(runs), first['reused'], second['reused'], other['reused'], third['reused']), (3, False, True, False, False))
        self.assertEqual(first['path'], second['path']); self.assertTrue(first['path'].is_file())
        with self.assertRaisesRegex(ValueError, '模型文件不存在'):
            components.stage('fixture', {**request, 'model_path': str(model.parent / 'gone.glb')})


class RecordTests(StoreCase):
    def test_a_new_record_starts_from_the_project_and_a_use_case_only_where_the_tool_closes_round_its_work(self):
        product, brand, _ = listing.stored(store.get('project', 'fixture'))
        self.assertEqual((product['title'], product['language'], product['features'], product['pages']), ('Ratchet Pipe Cutter', 'en', [], None))
        self.assertEqual((product['usecase']['enabled'], product['usecase']['kind'], product['usecase']['material'], product['usecase']['mode']),
                         (True, 'pipe', 'pvc_white', 'through'))
        self.assertEqual(brand['preset'], 'frame')
        self.assertEqual(listing.usecase_for('20V Cordless Drill'), {'kind': 'board', 'material': 'wood', 'mode': 'front'})
        self.assertFalse(listing.default_product({'name': '20V Cordless Drill'})['usecase']['enabled'])       # suggested, not switched on
        self.assertEqual(listing.usecase_for('管子割刀')['kind'], 'pipe'); self.assertIsNone(listing.usecase_for('Tool cabinet'))

    def test_a_long_tool_stood_on_end_is_shown_lying_level_and_nothing_else_is_turned(self):
        import orientation
        record = store.get('project', 'fixture')
        record.update(name='锂电角磨机'); record['analysis']['features']['dimensions'] = [.92, 1.775, 3.34]; record['plan']['rotation'] = [0, 0, 0]
        rotation, laid = listing.display_rotation(record)
        self.assertTrue(laid)
        width, depth, height = orientation.oriented_dimensions([.92, 1.775, 3.34], rotation)
        self.assertAlmostEqual(width, 3.34, places=3); self.assertLess(height, 2)               # its length across, lying level
        self.assertAlmostEqual(orientation.oriented_dimensions([.92, 1.775, 3.34], rotation)[0], max(width, depth, height), places=3)
        for name, dims in (('Water bottle', [.8, .8, 2.6]),                                    # tall, but not a tool: it stays upright
                           ('Cordless drill', [1.0, .3, 1.25]),                                # a tool, but not much taller than long
                           ('Angle grinder', [3.34, 1.775, .92])):                             # a tool already lying level
            with self.subTest(name=name):
                record.update(name=name); record['analysis']['features']['dimensions'] = dims
                self.assertEqual(listing.display_rotation(record), ([0.0, 0.0, 0.0], False))
        record.update(name='Angle grinder'); record['analysis']['features']['dimensions'] = [.92, 1.775, 3.34]
        requests = listing.picture_requests(record, listing.stored(record)[0], 330, [{'kind': 'main', 'index': 0}, {'kind': 'specs', 'index': 0}])
        self.assertEqual(requests['hero']['plan']['rotation'], listing.display_rotation(record)[0])
        self.assertEqual(requests['side']['plan']['rotation'], requests['hero']['plan']['rotation'])
        self.assertEqual(requests['side']['camera']['azimuth'], 0)                              # the long side, face on
        self.assertEqual(record['plan']['rotation'], [0, 0, 0])                                 # the project's own pose is not changed

    def test_a_line_the_copy_writer_cut_short_ends_on_a_whole_word(self):
        self.assertEqual(listing.whole('Battery powered, for cutting and grinding without a…', 120), 'Battery powered, for cutting and grinding without')
        self.assertEqual(listing.whole('Cuts PVC, PEX and copper pipe, one-hand…', 120), 'Cuts PVC, PEX and copper pipe')
        self.assertEqual(listing.whole('Complete line.', 120), 'Complete line.')
        self.assertEqual(listing.whole('  spaced   out  ', 120), 'spaced out')

    def test_what_is_saved_is_checked_and_parts_must_be_the_models_own(self):
        state = listing.save_product('fixture', {'title': 'Pipe Cutter', 'model': 'PC-22', 'size_mm': '225',
                                                 'features': [{'title': 'Hardened wheel', 'detail': 'Replaceable', 'parts': [PARTS[0], 'Not a part']}, {'title': ' '}],
                                                 'specs': [{'label': 'Capacity', 'value': '6 - 22 mm'}, {'label': '', 'value': 'x'}],
                                                 'figures': [{'value': '22', 'unit': 'mm', 'label': 'Max pipe'}], 'usecase': {'diameter_mm': '22', 'offset_mm': [0, 5, 0]}})
        product = state['product']
        self.assertEqual(product['features'], [{'title': 'Hardened wheel', 'detail': 'Replaceable', 'parts': [PARTS[0]], 'icon': ''}])
        self.assertEqual((product['specs'], product['size_mm'], product['usecase']['diameter_mm'], product['usecase']['offset_mm'], product['usecase']['enabled']),
                         ([{'label': 'Capacity', 'value': '6 - 22 mm'}], 225.0, 22.0, [0.0, 5.0, 0.0], True))
        self.assertEqual(state['size'], {'mm': 225.0, 'known': True})
        kept = listing.save_product('fixture', {'headline': 'Clean cuts'})['product']       # what is not sent stays
        self.assertEqual((kept['model'], kept['headline'], len(kept['features'])), ('PC-22', 'Clean cuts', 1))
        for bad in ({'language': 'fr'}, {'features': [{}] * 9}, {'specs': 'x'}, {'pages': ['poster']}, {'usecase': {'kind': 'rope'}}, {'usecase': {'mode': 'over'}},
                    {'usecase': {'offset_mm': [1, 2]}}, {'size_mm': 0}, {'title': 'x' * 61}, {'figures': [{}] * 4}, {'usecase': {'enabled': 'yes'}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                listing.save_product('fixture', bad)
        self.assertEqual(listing.save_brand('fixture', {'preset': 'tech', 'name': 'NORTHFORGE'})['brand']['preset'], 'tech')
        with self.assertRaises(ValueError):
            listing.save_brand('fixture', {'preset': 'none'})

    def test_the_real_size_comes_from_the_user_and_the_model_only_gives_a_guess(self):
        record = store.get('project', 'fixture'); product, _, _ = listing.stored(record)
        self.assertEqual(listing.product_size(record, product), (73800.0, False))         # 73.8 units read as metres: flagged as not real
        self.assertEqual(listing.product_size(record, {**product, 'size_mm': 225}), (225.0, True))
        record['plan']['dimensions_mm'] = [700, 380, 740]
        self.assertEqual(listing.product_size(record, product), (740.0, True))

    def test_the_set_is_planned_from_what_there_is_to_show(self):
        record = store.get('project', 'fixture'); product, _, _ = listing.stored(record)
        self.assertEqual([row['kind'] for row in listing.planned(record, product, [])], ['main', 'usecase', 'band'])
        product = listing.save_product('fixture', {'size_mm': 225, 'features': [{'title': 'A', 'parts': [PARTS[0]]}, {'title': 'B'}, {'title': 'C'}]})['product']
        record = store.get('project', 'fixture')
        self.assertEqual([(row['kind'], row.get('index')) for row in listing.planned(record, product, [])],
                         [('main', 0), ('callouts', 0), ('usecase', 0), ('feature', 0), ('band', 0), ('specs', 0), ('benefits', 0)])
        with_kit = listing.planned(record, product, [{'name': 'Battery'}])
        self.assertEqual([row['kind'] for row in with_kit], ['main', 'includes', 'callouts', 'usecase', 'feature', 'band', 'specs'])       # seven at most
        chosen = listing.planned(record, {**product, 'pages': ['main', 'includes', 'specs']}, [])
        self.assertEqual([(row['kind'], bool(row.get('skipped'))) for row in chosen], [('main', False), ('includes', True), ('specs', False)])
        self.assertIn('组件', chosen[1]['skipped'])

    def test_a_draft_fills_only_what_is_empty_and_prints_only_the_users_numbers(self):
        state = listing.draft('fixture', {'hint': '切割范围: 6-22 mm\n硬化钢切割轮'})
        product = state['product']
        self.assertEqual(product['drafted']['source'], 'rules')
        self.assertTrue(product['headline'])
        self.assertTrue(any('22' in spec['value'] for spec in product['specs']), product['specs'])
        numbers = ''.join(spec['value'] for spec in product['specs']) + ''.join(figure['value'] for figure in product['figures'])
        self.assertFalse(set(''.join(character for character in numbers if character.isdigit())) - set('622'))
        listing.save_product('fixture', {'headline': 'My own headline', 'features': [{'title': 'Mine'}]})
        again = listing.draft('fixture', {'hint': '切割范围: 6-22 mm'})['product']
        self.assertEqual((again['headline'], [row['title'] for row in again['features']]), ('My own headline', ['Mine']))
        replaced = listing.draft('fixture', {'hint': '', 'replace': True})['product']
        self.assertNotEqual(replaced['headline'], 'My own headline')
        with self.assertRaises(ValueError):
            listing.draft('fixture', {'hint': 'x' * 401})


class BuildTests(StoreCase):
    def setUp(self):
        super().setUp()
        mocked = patch.object(components, 'stage', side_effect=self.stage); mocked.start(); self.addCleanup(mocked.stop)
        listing.save_product('fixture', {'title': 'Ratchet Pipe Cutter', 'model': 'PC-22', 'headline': 'One-hand clean cuts', 'subline': 'Square, burr-free cuts.',
                                         'size_mm': 225, 'features': [{'title': 'Hardened cutting wheel', 'detail': 'Replaceable', 'parts': [PARTS[0]]},
                                                                      {'title': 'Twin guide rollers', 'parts': [PARTS[1]]}, {'title': 'Lifetime sharpness'}],
                                         'specs': [{'label': 'Pipe capacity', 'value': '6 - 22 mm'}], 'figures': [{'value': '22', 'unit': 'mm', 'label': 'Max pipe'}]})
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'badges': ['PRO'], 'legal': '*A legal line.'})

    def words(self, page):
        return ' '.join(' '.join(''.join(part for part, _ in line) for line in item.get('lines') or []) + ' ' + str(item.get('text', '')) + ' ' + str(item.get('label', ''))
                        for item in page['items'] if item['type'] in ('text', 'dimension'))

    def context(self, preset='frame', **product):
        record = store.get('project', 'fixture'); stored_product, brand_record, _ = listing.stored(record)
        stored_product = {**stored_product, **product}
        parts = listing.ready_components(record); size, known = listing.product_size(record, stored_product)
        requests = listing.picture_requests(record, stored_product, size, [{'kind': kind, 'index': 0} for kind in listing_pages.KINDS])
        shots = {}
        for name, request in requests.items():
            result = self.stage('fixture', request)
            shots[name] = listing_pages.Shot(result, info_layer.Picture(result['probe'], result['path']))
        brand = brand_kit.Brand({**brand_record, 'preset': preset}, '#d9463d')
        return listing.context(record, stored_product, brand, parts, shots, size, known)

    def test_every_kind_of_page_composes_and_paints_in_every_preset(self):
        self.add_picture(name='Battery', sku='BP-205', quantity='2')
        for preset in brand_kit.PRESETS:
            ctx = self.context(preset)
            for kind in listing_pages.KINDS:
                with self.subTest(preset=preset, kind=kind):
                    page = listing_pages.compose(kind, ctx)
                    self.assertEqual((page['kind'], page['height']), (kind, 1000))
                    self.assertTrue(page['items'] and all(item['type'] in draw.TYPES for item in page['items']))
                    image = draw.render(page, 200, supersample=1)
                    self.assertEqual(image.size, (200, 200)); self.assertGreater(len(set(image.getdata())), 3)
                    for item in page['items']:
                        if item['type'] == 'text' and 'x' in item:
                            self.assertTrue(-5 <= item['x'] <= 1000 and -5 <= item['y'] <= 1000, item)

    def test_the_main_image_is_white_and_wordless(self):
        self.add_picture(name='Battery', quantity='2'); self.add_picture(name='Carry case')
        page = listing_pages.compose('main', self.context('tech'))
        self.assertEqual(page['background'], '#ffffff')
        self.assertEqual({item['type'] for item in page['items']}, {'image'})
        self.assertEqual(len(page['items']), 4)                                           # the case behind, the product, and the battery twice

    def test_the_kit_page_names_every_included_thing_with_its_number(self):
        self.add_picture(name='Battery', sku='BP-205', quantity='2', caption='20V 5.0Ah'); self.add_picture(name='Charger')
        row = self.add_picture(name='Spare'); components.update('fixture', row['id'], {'included': False})
        words = self.words(listing_pages.compose('includes', self.context()))
        for expected in ('KIT INCLUDES', 'PC-22', '(2) BP-205', '20V 5.0AH', 'CHARGER', 'NORTHFORGE', 'legal line'):
            self.assertIn(expected.upper(), words.upper())
        self.assertNotIn('SPARE', words.upper())
        self.assertEqual((listing_pages._rows(4), listing_pages._rows(5), listing_pages._rows(7), listing_pages._rows(14)), ([2, 2], [2, 3], [3, 4], [4, 4, 4, 4]))

    def test_callouts_point_at_the_part_and_a_point_without_a_part_becomes_a_chip(self):
        page = listing_pages.compose('callouts', self.context())
        leaders = [item for item in page['items'] if item['type'] == 'line' and item.get('end') == 'square']
        self.assertEqual(len(leaders), 2)
        picture = next(item for item in page['items'] if item['type'] == 'image')
        for leader in leaders:
            x, y = leader['points'][-1]
            self.assertTrue(picture['x'] <= x <= picture['x'] + picture['w'] and picture['y'] <= y <= picture['y'] + picture['h'], (x, y))       # on the product
        self.assertIn('LIFETIME SHARPNESS', self.words(page).upper()); self.assertIn('没有对应到模型', page['notes'][0])
        self.assertNotIn('REPLACEABLE', self.words(page).upper())                                   # a framed brand prints titles only
        self.assertIn('Replaceable', self.words(listing_pages.compose('callouts', self.context('clean'))))

    def test_the_use_case_marks_the_workpiece_and_the_products_size_only_when_it_is_real(self):
        ctx = self.context()
        page = listing_pages.compose('usecase', ctx); words = self.words(page)
        self.assertIn('IN USE · PVC PIPE', words.upper()); self.assertIn('Ø 32 mm', words)
        self.assertEqual([item['label'] for item in page['items'] if item['type'] == 'dimension'], ['300 mm', '180 mm'])
        picture = next(item for item in page['items'] if item['type'] == 'image')
        self.assertEqual((picture['fit'], picture['w'], picture['h'], picture['src']), ('cover', 1000, 1000, ctx['shots']['usecase'].path))       # the picture is the page
        request = next(request for request in self.requests if request.get('workpiece'))
        self.assertEqual((request['workpiece']['kind'], request['workpiece']['place']['mode'], request['camera']['auto']), ('pipe', 'through', 'workpiece'))
        self.assertNotIn('diameter_mm', request['workpiece'])                               # left to the opening measured on the model
        unknown = listing_pages.compose('usecase', {**ctx, 'size_known': False})
        self.assertEqual([item for item in unknown['items'] if item['type'] == 'dimension'], [])
        self.assertIn('Ø 32 mm', self.words(unknown)); self.assertIn('真实尺寸', unknown['notes'][0])
        inches = self.context(); inches['brand'].unit = 'in'
        self.assertIn('Ø 1.3 in', self.words(listing_pages.compose('usecase', inches)))
        titled = self.context(usecase={**ctx['usecase'], 'headline': 'Cuts PVC in one turn'})
        self.assertIn('CUTS PVC IN ONE TURN', self.words(listing_pages.compose('usecase', titled)).upper())

    def test_a_dimension_is_left_out_rather_than_drawn_across_the_tool(self):
        ctx = self.context(); shot = ctx['shots']['usecase']
        shot.crop = [0, 0, 1, 1]; shot.origin = (0, 0); shot.scale = 1000 / shot.width
        clear = listing_pages._dimension_fit(shot, (300, 780), (700, 780), (500, 550), '300 mm')
        self.assertEqual(clear, (30, .5))
        self.assertIsNone(listing_pages._dimension_fit(shot, (300, 560), (700, 560), (500, 900), '300 mm'))       # the only way out runs over the product
        self.assertIsNone(listing_pages._dimension_fit(shot, (300, 780), (700, 780), (500, 550), '300 mm', avoid=[(500, 812)]))
        self.assertEqual(shot.shares([320, 400, 480, 480]), (1.0, 0.0)); self.assertEqual(shot.shares([20, 510, 200, 570])[0], 0.0)
        self.assertGreater(shot.shares([20, 510, 200, 570])[1], .8)

    def test_on_the_band_the_headline_stops_short_of_the_key_figures(self):
        figures = [{'value': '8,500', 'unit': 'rpm', 'label': 'No-load speed'}, {'value': '115', 'unit': 'mm', 'label': 'Disc'}]
        for preset in brand_kit.PRESETS:
            with self.subTest(preset=preset):
                page = listing_pages.compose('band', self.context(preset, headline='Cuts steel, stays cool', figures=figures))
                texts = [item for item in page['items'] if item['type'] == 'text']
                headline = texts[0]; reach = headline['x'] + max(draw.line_width(line, headline['style']) for line in headline['lines'])
                first_figure = min(item['x'] for item in texts if any('8,500' in part for line in item.get('lines') or [[(item.get('text', ''), False)]] for part, _ in line))
                self.assertLess(reach, first_figure - 10, (reach, first_figure))
        photo = self.context(); photo['photo'] = photo['shots']['hero'].path
        first = listing_pages.compose('band', photo)['items'][0]
        self.assertEqual((first['type'], first['fit'], first['w']), ('image', 'cover', 1000))       # a photograph, when there is one, fills the top

    def test_a_feature_close_up_shows_the_whole_product_with_a_magnifier_on_the_part(self):
        ctx = self.context()
        detail = ctx['shots']['detail0']
        page = listing_pages.compose('feature', ctx, 0)
        images = [item for item in page['items'] if item['type'] == 'image']
        self.assertEqual(images[0]['src'], ctx['shots']['hero'].path)                           # the whole product
        circle = next(item for item in images if item.get('mask') == 'circle')
        self.assertEqual(circle['src'], detail.path)
        self.assertEqual([round(value, 3) for value in circle['crop']], [round(value, 3) for value in listing_pages._part_crop(detail.result, [PARTS[0]])])
        self.assertTrue(any(item['type'] == 'line' and item.get('end') == 'ring' for item in page['items']))      # pointing at the part
        self.assertIsNone(listing_pages._part_crop(detail.result, ['Not a part']))
        square = listing_pages._part_crop({'parts': [{'name': 'a', 'rect': [.4, .4, .5, .6]}]}, ['a'])
        self.assertAlmostEqual(square[2] - square[0], square[3] - square[1]); self.assertTrue(0 <= square[0] and square[3] <= 1)

    def test_the_specification_page_prints_the_users_rows_and_the_measured_size(self):
        words = self.words(listing_pages.compose('specs', self.context()))
        for expected in ('SPECIFICATIONS', 'PIPE CAPACITY', '6 - 22 MM', 'LENGTH', '300 MM', 'HEIGHT', '180 MM'):
            self.assertIn(expected, words.upper())
        guessed = listing_pages.compose('specs', {**self.context(), 'size_known': False})
        self.assertNotIn('300', self.words(guessed)); self.assertIn('真实尺寸', guessed['notes'][0])
        chinese = self.words(listing_pages.compose('specs', self.context(language='zh')))
        self.assertIn('规格参数', chinese); self.assertIn('长度', chinese)

    def test_building_writes_the_pages_in_order_and_remembers_them(self):
        self.add_picture(name='Battery', sku='BP-205')
        seen = []
        pages, notes = listing.build('fixture', progress=lambda stage, share, detail='': seen.append((stage, round(share, 2))))
        self.assertEqual([page['kind'] for page in pages], ['main', 'includes', 'callouts', 'usecase', 'feature', 'band', 'specs'])
        self.assertEqual([page['file'] for page in pages][:2], ['01-main.png', '02-includes.png'])
        for page in pages:
            with Image.open(components.path_of('fixture', page['url'])) as image:
                self.assertEqual(image.size, (listing.PAGE_PX, listing.PAGE_PX))
        self.assertEqual((seen[0][0], seen[-1][0]), ('pictures', 'compose')); self.assertTrue(any('品牌色取自产品' in note for note in notes))
        state = listing.state('fixture')
        self.assertEqual((len(state['pages']), state['brand']['accent_source'], state['order'][0]), (7, 'product', 'main'))
        red, green, blue, _ = draw.color(state['brand']['accent_used']); self.assertTrue(red > 150 and green < 110, state['brand']['accent_used'])
        data, name = listing.archive('fixture')
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            self.assertEqual(bundle.namelist(), [page['file'] for page in pages])
        self.assertEqual(name, 'fixture-listing.zip')
        first = Path(components.path_of('fixture', pages[0]['url'])).parent
        listing.build('fixture')                                                            # a new build replaces the old files
        self.assertFalse(first.exists()); self.assertEqual(len(list((self.data / 'fixture' / 'listing').glob('build-*'))), 1)

    def test_a_component_still_being_processed_is_left_out_and_said(self):
        component_id, base = components.reserve('fixture'); shutil.copy(self.picture(), base / 'case.png'); components.register('fixture', component_id, {'name': 'Case'})
        pages, notes = listing.build('fixture')
        self.assertNotIn('includes', [page['kind'] for page in pages]); self.assertIn('还在处理', notes[0]); self.assertIn('Case', notes[0])

    def test_a_task_reports_progress_can_be_cancelled_and_only_one_runs(self):
        with patch.object(listing.threading, 'Thread') as thread:
            task = listing.submit('fixture')
            with self.assertRaises(engine.JobConflictError):
                listing.submit('fixture')
        self.assertEqual((task['status'], task['progress']), ('queued', 0))
        listing._run(thread.call_args.kwargs['args'][0])
        done = listing.latest('fixture')
        self.assertEqual((done['status'], done['progress'], done['pages']), ('completed', 100, 7))
        with patch.object(listing.threading, 'Thread') as thread:
            second = listing.submit('fixture')
        self.assertEqual(listing.cancel('fixture', second['id'])['status'], 'cancelled')
        before = store.get('project', 'fixture')['listing']['built_at']
        listing._run(thread.call_args.kwargs['args'][0])
        self.assertEqual((listing.latest('fixture')['status'], store.get('project', 'fixture')['listing']['built_at']), ('cancelled', before))
        with self.assertRaises(KeyError):
            listing.cancel('other', second['id'])
        store.put(listing.KIND, {'id': 'stuck', 'project_id': 'fixture', 'status': 'running', 'created_at': 1})
        listing.recover_interrupted()
        self.assertEqual(store.get(listing.KIND, 'stuck')['status'], 'failed')

    def test_a_failed_build_says_why_and_keeps_the_last_set(self):
        listing.build('fixture'); kept = store.get('project', 'fixture')['listing']['pages']
        with patch.object(components, 'stage', side_effect=ValueError('渲染失败：显存不足')), patch.object(listing.threading, 'Thread') as thread:
            listing.submit('fixture'); listing._run(thread.call_args.kwargs['args'][0])
        failed = listing.latest('fixture')
        self.assertEqual((failed['status'], failed['error']), ('failed', '渲染失败：显存不足'))
        self.assertEqual(store.get('project', 'fixture')['listing']['pages'], kept)

    def test_components_sent_with_a_new_project_are_registered_and_a_bad_one_refuses_the_upload(self):
        import app as application
        for module in (application,):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        client = application.app.test_client()
        glb = (ROOT / 'samples' / 'matte-pump-bottle.glb').read_bytes()
        picture = io.BytesIO(); Image.new('RGB', (120, 80), 'white').save(picture, 'PNG')
        with patch.object(engine, 'submit') as submit:
            made = client.post('/api/projects', content_type='multipart/form-data', data={
                'files': (io.BytesIO(glb), 'grinder.glb'), 'name': 'Grinder kit',
                'component_files': [(io.BytesIO(glb), 'battery.glb'), (io.BytesIO(picture.getvalue()), 'bag.png')],
                'components': json.dumps([{'name': '20V battery', 'role': 'battery', 'quantity': 2}, {'name': 'Tool bag', 'role': 'bag'}])})
        self.assertEqual(made.status_code, 201, made.get_json())
        body = made.get_json(); ident = body['id']
        self.assertEqual(submit.call_args.args[:2], (ident, 'analyze'))
        self.assertEqual([(row['name'], row['role'], row['quantity'], row['kind'], row['status']) for row in body['components']],
                         [('20V battery', 'battery', 2, 'model', 'queued'), ('Tool bag', 'bag', 1, 'image', 'queued')])
        self.assertEqual(body['component_errors'], [])
        stored = store.get('project', ident)
        self.assertEqual(len(components.components(stored)), 2)
        self.assertEqual(len(list((self.data / ident / 'originals').rglob('*.glb'))), 1)             # the project's own model stays alone
        self.assertEqual(components.QUEUE.qsize(), 2)
        # A component that cannot be one refuses the whole upload before anything is made.
        before = {record['id'] for record in store.all_records('project')}
        refused = client.post('/api/projects', content_type='multipart/form-data', data={
            'files': (io.BytesIO(glb), 'grinder.glb'), 'component_files': [(io.BytesIO(b'%PDF'), 'manual.pdf')]})
        self.assertEqual(refused.status_code, 400); self.assertIn('组件文件格式不受支持', refused.get_json()['error'])
        bad_role = client.post('/api/projects', content_type='multipart/form-data', data={
            'files': (io.BytesIO(glb), 'grinder.glb'), 'component_files': [(io.BytesIO(glb), 'x.glb')], 'components': json.dumps([{'role': 'rocket'}])})
        self.assertEqual(bad_role.status_code, 400)
        self.assertEqual({record['id'] for record in store.all_records('project')}, before)

    def test_components_with_models_are_rendered_with_the_product_for_the_main_image(self):
        record = store.get('project', 'fixture')
        parts = [{'name': 'Battery', 'role': 'battery', 'quantity': 2, 'size_mm': 120, 'model_path': '/m/battery.glb', 'rotation': [180, 0, 0],
                  'finishes': [{'name': 'Shell', 'preset': 'matte_plastic'}], 'path': 'x.png'},
                 {'name': 'Bag', 'role': 'bag', 'quantity': 1, 'size_mm': None, 'path': 'bag.png'}]            # a photograph only: not in 3D
        kit = listing.kit_request(record, record['plan'], 225, parts, 35)
        self.assertEqual(kit['companions'], [{'name': 'Battery', 'role': 'battery', 'model_path': '/m/battery.glb', 'size_mm': 120, 'quantity': 2,
                                              'rotation': [180, 0, 0], 'plan': {'material_overrides': [{'name': 'Shell', 'preset': 'matte_plastic'}]}}])
        self.assertEqual((kit['model_path'], kit['size_mm'], kit['camera']['azimuth'], kit['ground']), (record['model_path'], 225.0, 35, 'none'))
        self.assertIsNone(listing.kit_request(record, record['plan'], 225, parts[1:], 35))
        requests = listing.picture_requests(record, listing.stored(record)[0], 225, [{'kind': 'main', 'index': 0}], parts)
        self.assertIn('kit', requests)
        self.assertNotIn('kit', listing.picture_requests(record, listing.stored(record)[0], 225, [{'kind': 'callouts', 'index': 0}], parts))
        # The main image is the kit render itself, cropped to everything in it, with a photograph-only component beside.
        ctx = self.context()
        result = self.stage('fixture', kit); result['kit_rect'] = [.2, .3, .8, .8]
        ctx['shots']['kit'] = listing_pages.Shot(result)
        ctx['components'] = [{**parts[0], 'path': ctx['shots']['hero'].path}, {**parts[1], 'path': ctx['shots']['hero'].path}]
        page = listing_pages.compose('main', ctx)
        images = [item for item in page['items'] if item['type'] == 'image']
        self.assertEqual(images[0]['src'], str(result['path'])); self.assertEqual([round(value, 4) for value in images[0]['crop']], [.185, .285, .815, .815])
        self.assertTrue(images[0].get('reflection')); self.assertEqual(len(images), 2)

    def test_the_web_layer_serves_the_workspace(self):
        import app as application
        mocked = patch.object(application, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)       # the file route reads its own name for the folder
        client = application.app.test_client()
        state = client.get('/api/projects/fixture/listing').get_json()
        self.assertEqual((state['product']['model'], state['brand']['name'], state['pages'], state['task']), ('PC-22', 'NORTHFORGE', [], None))
        self.assertEqual(len(state['presets']), 5); self.assertIn('pipe', state['options']['workpieces']); self.assertGreaterEqual(len(state['elements']), 20)
        self.assertEqual(client.put('/api/projects/fixture/listing/product', json={'model': 'PC-25'}).get_json()['product']['model'], 'PC-25')
        self.assertEqual(client.put('/api/projects/fixture/listing/product', json={'language': 'fr'}).status_code, 400)
        self.assertEqual(client.put('/api/projects/fixture/listing/brand', json={'preset': 'promo'}).get_json()['brand']['preset'], 'promo')
        self.assertEqual(client.post('/api/projects/fixture/listing/draft', json={'hint': '6-22 mm'}).status_code, 200)
        picture = self.picture('charger.png').read_bytes()
        made = client.post('/api/projects/fixture/components', data={'files': (io.BytesIO(picture), 'charger.png'), 'name': 'Fast Charger', 'sku': 'CH-118', 'quantity': '1'},
                           content_type='multipart/form-data')
        self.assertEqual((made.status_code, made.get_json()['role'], made.get_json()['status']), (201, 'charger', 'queued'))
        component_id = made.get_json()['id']
        components.process('fixture', component_id, store.get('project', 'fixture')['components'][0]['token'])
        listed = client.get('/api/projects/fixture/components').get_json()
        self.assertEqual((listed['components'][0]['status'], listed['roles']['charger']['en']), ('ready', 'Charger'))
        self.assertEqual(client.put('/api/projects/fixture/components/%s' % component_id, json={'quantity': 2}).get_json()['quantity'], 2)
        self.assertEqual(client.post('/api/projects/fixture/components', data={}, content_type='multipart/form-data').status_code, 400)
        self.assertEqual(client.post('/api/projects/fixture/components', data={'files': (io.BytesIO(b'x'), '../evil.png')}, content_type='multipart/form-data').status_code, 400)
        logo = io.BytesIO(); Image.new('RGBA', (60, 20), (10, 10, 10, 255)).save(logo, 'PNG'); logo.seek(0)
        with_logo = client.post('/api/projects/fixture/listing/logo', data={'file': (logo, 'logo.png')}, content_type='multipart/form-data').get_json()
        self.assertTrue(with_logo['brand']['logo'].startswith('/files/fixture/listing/brand/logo-'))
        self.assertEqual(client.post('/api/projects/fixture/listing/logo', data={'file': (io.BytesIO(b'x'), 'logo.svg')}, content_type='multipart/form-data').status_code, 400)
        self.assertEqual(client.get('/api/projects/fixture/listing/download').status_code, 400)       # nothing built yet
        with patch.object(listing.threading, 'Thread') as thread:
            self.assertEqual(client.post('/api/projects/fixture/listing/build', json={}).status_code, 202)
            self.assertEqual(client.post('/api/projects/fixture/listing/build', json={}).status_code, 409)
        listing._run(thread.call_args.kwargs['args'][0])
        built = client.get('/api/projects/fixture/listing').get_json()
        self.assertEqual((built['task']['status'], built['pages'][1]['kind']), ('completed', 'includes'))
        self.assertEqual(client.get(built['pages'][0]['url']).status_code, 200)
        bundle = client.get('/api/projects/fixture/listing/download')
        self.assertEqual((bundle.status_code, bundle.mimetype), (200, 'application/zip'))
        self.assertIsNone(client.delete('/api/projects/fixture/listing/logo').get_json()['brand']['logo'])
        self.assertEqual(client.delete('/api/projects/fixture/components/%s' % component_id).get_json()['removed'], component_id)
        self.assertEqual(client.get('/api/projects/missing/listing').status_code, 404)


# A frame shaped like a C, 100 x 100 x 30 mm, open to +X: a spine and two arms 40 mm apart.
def c_frame():
    lines = ['o Frame']; faces = []
    for index, (x0, x1, y0, y1) in enumerate(((0, 40, 0, 100), (40, 100, 70, 100), (40, 100, 0, 30))):
        for z in (0, 30):
            lines += ['v %g %g %g' % (x, y, z) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        base = index * 8
        for quad in ((1, 2, 3, 4), (8, 7, 6, 5), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1)):
            faces.append('f ' + ' '.join(str(base + corner) for corner in quad))
    return '\n'.join(lines + faces) + '\n'


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: staging not run')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.folder = Path(cls.temp.name)
        (cls.folder / 'frame.obj').write_text(c_frame(), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_stage(self, name, **request):
        out = self.folder / name; out.mkdir()
        body = {'action': 'measure', 'model_path': str(self.folder / 'frame.obj'), 'plan': {}, 'size_mm': 100, 'size': [96, 96], 'grid': 32,
                'camera': {'azimuth': 35, 'elevation': 18, 'margin': .15}, 'ground': 'none', 'output_dir': str(out), **request}
        (out / 'request.json').write_text(json.dumps(body), encoding='utf-8')
        ran = subprocess.run([shutil.which('blender'), '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(ROOT / 'blender' / 'stage.py'), '--',
                              str(out / 'request.json')], capture_output=True, text=True, timeout=300)
        self.assertEqual(ran.returncode, 0, ran.stdout[-2000:] + ran.stderr[-2000:])
        return json.loads((out / 'result.json').read_text(encoding='utf-8')), out

    def test_a_pipe_goes_through_the_jaw_and_its_size_is_read_in_the_picture(self):
        result, _ = self.run_stage('through', workpiece={'kind': 'pipe', 'material': 'pvc_white', 'place': {'mode': 'through'}},
                                   camera={'azimuth': 35, 'elevation': 18, 'margin': .15, 'auto': 'workpiece'})
        piece = result['workpiece']
        self.assertEqual((piece['mode'], piece['notes']), ('through', []))
        self.assertTrue(36 <= piece['opening_mm'] <= 44, piece['opening_mm'])              # the arms are 40 mm apart
        self.assertEqual(piece['diameter_mm'], geometry.stock_size(piece['opening_mm']))
        self.assertGreater(abs(piece['axis'][1]), .99)                                     # along the frame's depth
        anchor = piece['anchors']['diameter']
        self.assertEqual(anchor['mm'], piece['diameter_mm'])
        for point in (anchor['p1'], anchor['p2']):
            self.assertTrue(0 < point[0] < 1 and 0 < point[1] < 1, point)                  # read where the picture shows it
        self.assertTrue(piece['rect'][0] < 0 or piece['rect'][2] > 1 or piece['rect'][1] < 0 or piece['rect'][3] > 1)       # it runs out of the frame
        self.assertEqual(result['dimensions_mm'], [100.0, 30.0, 100.0])
        self.assertIn('Listing workpiece', result['probe']['objects'])
        self.assertEqual(len(result['probe']['cells']), result['probe']['nx'] * result['probe']['ny'])
        rect = result['product_rect']; self.assertTrue(0 <= rect[0] < rect[2] <= 1 and 0 <= rect[1] < rect[3] <= 1, rect)
        self.assertEqual([row['axis'] for row in result['dimension_edges']], ['x', 'y', 'z'])
        self.assertNotIn('image', result)                                                  # measured, not rendered

    def test_a_chosen_size_is_kept_and_said_when_it_is_too_wide(self):
        result, _ = self.run_stage('wide', workpiece={'kind': 'pipe', 'diameter_mm': 63, 'place': {'mode': 'through'}})
        self.assertEqual((result['workpiece']['diameter_mm'], result['workpiece']['notes']), (63.0, ['wider_than_opening']))

    def test_stock_under_the_tool_lifts_it_and_a_render_is_a_transparent_picture(self):
        result, out = self.run_stage('under', action='render', samples=4, workpiece={'kind': 'board', 'material': 'wood', 'place': {'mode': 'under'}})
        self.assertEqual((result['workpiece']['mode'], result['workpiece']['thickness_mm']), ('under', 18.0))
        self.assertIn('thickness', result['workpiece']['anchors'])
        with Image.open(out / result['image']) as image:
            self.assertEqual((image.size, image.mode), ((96, 96), 'RGBA'))
            alpha = image.getchannel('A'); self.assertEqual(alpha.getpixel((1, 1)), 0); self.assertGreater(alpha.getextrema()[1], 200)

    def test_components_are_staged_round_the_product_at_their_own_size(self):
        box = '\n'.join(['o Box'] + ['v %g %g %g' % (x, y, z) for z in (0, 40) for x, y in ((0, 0), (60, 0), (60, 30), (0, 30))]
                         + ['f 1 2 3 4', 'f 8 7 6 5', 'f 1 5 6 2', 'f 2 6 7 3', 'f 3 7 8 4', 'f 4 8 5 1']) + '\n'
        (self.folder / 'box.obj').write_text(box, encoding='utf-8')
        result, _ = self.run_stage('kit', companions=[{'name': 'Battery', 'role': 'battery', 'model_path': str(self.folder / 'box.obj'), 'size_mm': 60, 'quantity': 2},
                                                      {'name': 'Case', 'role': 'case', 'model_path': str(self.folder / 'box.obj'), 'size_mm': 150, 'quantity': 1}])
        self.assertEqual([row['name'] for row in result['companions']], ['Battery', 'Battery', 'Case'])
        kit = result['kit_rect']; product = result['product_rect']
        self.assertTrue(kit[0] <= product[0] and kit[2] >= product[2] and kit[3] >= product[3], (kit, product))
        for row in result['companions']:
            self.assertTrue(0 <= row['rect'][0] < row['rect'][2] <= 1 and 0 <= row['rect'][1] < row['rect'][3] <= 1, row)
        width = lambda rect: rect[2] - rect[0]
        self.assertGreater(width(result['companions'][2]['rect']), width(result['companions'][0]['rect']))      # the case is the larger thing
        self.assertEqual(result['dimensions_mm'], [100.0, 30.0, 100.0])                                       # the product itself is measured alone

    def test_a_view_is_chosen_that_shows_the_part_asked_for(self):
        result, _ = self.run_stage('focus', camera={'azimuth': 35, 'elevation': 20, 'margin': .2, 'focus': ['Frame'], 'azimuths': [35, -35, 125]})
        self.assertIn(result['azimuth'], (35.0, -35.0, 125.0)); self.assertEqual(result['probe']['objects'], ['Frame'])
        self.assertEqual(result['projection'], 'perspective')
        flat, _ = self.run_stage('side', camera={'azimuth': 0, 'elevation': 0, 'projection': 'orthographic', 'margin': .1})
        edges = {row['axis']: row for row in flat['dimension_edges']}
        self.assertEqual(flat['projection'], 'orthographic')
        self.assertAlmostEqual(edges['x']['p1'][1], edges['x']['p2'][1], places=3)        # level in a side view
        self.assertAlmostEqual(edges['z']['p1'][0], edges['z']['p2'][0], places=3)        # and upright
        self.assertAlmostEqual(edges['x']['apparent'], .8, delta=.02)


if __name__ == '__main__':
    unittest.main()

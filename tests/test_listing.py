"""Components, the brand kit, the page renderer and the listing built from them.

Blender is replaced by a stand-in that paints a plain picture, except in the runtime class
at the end, which runs blender/stage.py on a small model when Blender is installed. No test
reaches a model service.
"""
import copy
import io
import json
import re
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

import articulation
import brand_kit
import components
import engine
import info_layer
import listing
import listing_draw as draw
import listing_elements as el
import brandkit_render
import brandkit_set
import scene_stylist
import studio_store as store
from blender import stage_geometry as geometry
from test_scene_images import project

PARTS = ['G-07C抽屉(2)_8', 'G-不锈钢台面_2']
# The states the pose agent (articulation.py) found for a tool whose parts move, as articulation.states gives them.
POSES = [{'id': 'closed', 'label_zh': '闭合', 'label_en': 'Closed', 'role': 'rest', 'values': {'hinge': 0.0}},
         {'id': 'open', 'label_zh': '张开', 'label_en': 'Open', 'role': 'open', 'values': {'hinge': 20.0}},
         {'id': 'wide', 'label_zh': '全开', 'label_en': 'Fully open', 'role': 'open', 'values': {'hinge': 35.0}}]


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
        self.assertEqual((record['fonts'], record['badge_style'], record['texture']), ('industrial', 'auto', 'none'))
        look = brand_kit.validate({'fonts': 'elegant', 'badge_style': 'ribbon', 'texture': 'carbon'}, record)
        self.assertEqual((look['fonts'], look['badge_style'], look['texture'], look['preset']), ('elegant', 'ribbon', 'carbon', 'tech'))
        self.assertEqual(brand_kit.validate({'unit': 'in'}, look)['fonts'], 'elegant')
        for bad in ({'fonts': 'comic-sans'}, {'badge_style': 'heart'}, {'texture': 'leopard'}, {'preset': 'dewalt'}, {'accent': 'red'}, {'badges': ['a', 'b', 'c', 'd']}, {'unit': 'cm'}, {'logo': 'https://example.com/x.png'}, {'name': 'x' * 41}):
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
        import scene_imagegen
        import scene_stylist
        for target in ((scene_stylist, '_config'), (scene_imagegen, 'config')):                                         # whatever .env says
            mocked = patch.object(*target, return_value=None); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
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
        requests = listing.picture_requests(record, listing.stored(record)[0], 330, [{'kind': 'T12', 'index': 0}, {'kind': 'T06', 'index': 0}])
        self.assertEqual(requests['hero']['plan']['rotation'], listing.display_rotation(record)[0])
        self.assertEqual(requests['side']['plan']['rotation'], requests['hero']['plan']['rotation'])
        self.assertEqual(requests['side']['camera']['azimuth'], 0)                              # the long side, face on
        self.assertEqual(record['plan']['rotation'], [0, 0, 0])                                 # the project's own pose is not changed

    def test_a_tool_lying_flat_is_shown_from_above_and_anything_else_from_the_hero_view(self):
        record = store.get('project', 'fixture'); record.update(name='Hand saw'); record['plan']['rotation'] = [0, 0, 0]
        kinds = [{'kind': 'T03', 'index': 0}, {'kind': 'T06', 'index': 0}]
        for dims, hero in (([.154, .643, .020], 60.0), ([.381, 2.0, .134], 60.0),           # a saw, a wrench: flat lays
                           ([.448, 2.0, .463], 18.0), ([1.0, .3, 1.25], 18.0)):              # a round screwdriver, a drill
            with self.subTest(dims=dims):
                record['analysis']['features']['dimensions'] = dims
                requests = listing.picture_requests(record, listing.stored(record)[0], 330, kinds)
                self.assertEqual(requests['hero']['camera']['elevation'], hero)
                self.assertEqual(requests['side']['camera']['elevation'], 89 if hero == 60 else 0)     # the outline shot is unchanged
                self.assertEqual(listing._hero_view(record)[1], 18.0)                  # the stored view the rest of the listing keys on

    def test_points_found_on_an_earlier_main_picture_are_not_kept_once_a_flat_tool_is_shown_as_a_flat_lay(self):
        record = store.get('project', 'fixture'); record['plan']['rotation'] = [0, 0, 0]
        record['analysis']['features']['dimensions'] = [1.0, .3, 1.25]                       # standing: the key is as it was
        plan = record['plan']; shot = next(row for row in plan['shots'] if row['id'] == 'hero')
        old = listing.hashlib.sha256(listing.json.dumps([record.get('model_path'), plan.get('rotation'), shot.get('azimuth'), shot.get('elevation')],
                                                        sort_keys=True, default=str).encode()).hexdigest()[:16]
        self.assertEqual(listing._view_key(record), old)
        record['analysis']['features']['dimensions'] = [.154, .643, .020]                    # a saw: its main picture is now from 60 degrees
        self.assertNotEqual(listing._view_key(record), old)

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
        for bad in ({'language': 'fr'}, {'features': [{}] * 9}, {'specs': 'x'}, {'pages': ['poster']}, {'pages': ['main']}, {'aspect': '16:9'}, {'scenes': {'count': 4}},
                    {'scenes': {'people': 'crowd'}}, {'scenes': {'audience': 'nobody'}}, {'scenes': {'hint': 'x' * 301}}, {'usecase': {'kind': 'rope'}}, {'usecase': {'mode': 'over'}},
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
        # No image model here: the only scene is the workpiece render a pipe cutter gets by default, and nothing else is known yet.
        self.assertEqual([row['kind'] for row in listing.planned(record, product, []) if not row.get('skipped')], ['T12'])
        product = listing.save_product('fixture', {'size_mm': 225, 'features': [{'title': 'A', 'parts': [PARTS[0]]}, {'title': 'B'}, {'title': 'C'}]})['product']
        record = store.get('project', 'fixture')
        self.assertEqual([(row['kind'], row.get('index')) for row in listing.planned(record, product, []) if not row.get('skipped')], [('T12', 0), ('T02', 0), ('T04', 0), ('T06', 0)])
        product = listing.save_product('fixture', {'features': [{'title': 'A', 'parts': [PARTS[0]]}, {'title': 'B', 'parts': [PARTS[1]]}]})['product']
        with patch.object(brandkit_set, 'image_model_ready', return_value=True):
            with_kit = listing.planned(record, product, [{'name': 'Battery'}])
        # everything but the poses page, which needs a model whose parts move (test_the_poses_are_staged_...)
        self.assertEqual([row['kind'] for row in with_kit if not row.get('skipped')], [kind for kind in brandkit_set.AUTO if kind != 'T13'])
        self.assertIn('活动部件', next(row['skipped'] for row in with_kit if row['kind'] == 'T13'))
        chosen = listing.planned(record, {**product, 'pages': ['T12', 'T05', 'T09', 'T06']}, [])
        self.assertEqual([(row['kind'], bool(row.get('skipped'))) for row in chosen], [('T12', False), ('T05', True), ('T09', True), ('T06', False)])
        self.assertIn('组件', chosen[1]['skipped']); self.assertIn('证据', chosen[2]['skipped'])
        legacy = store.get('project', 'fixture'); legacy['listing']['product']['pages'] = ['main', 'includes']; store.put('project', legacy)
        self.assertIsNone(listing.stored(legacy)[0]['pages'])                                  # page kinds from before the templates are dropped, not an error

    def test_the_poses_are_staged_from_the_heros_camera_with_the_envelope_of_every_state(self):
        record = store.get('project', 'fixture'); product = listing.stored(record)[0]
        rows = [{'kind': 'T12', 'index': 0}, {'kind': 'T13', 'index': 0}]
        posed = {'closed': None, 'open': {'state': 'open', 'joints': [{'name': 'hinge', 'type': 'hinge', 'value': 20.0}]},
                 'wide': {'state': 'wide', 'joints': [{'name': 'hinge', 'type': 'hinge', 'value': 35.0}]}}
        with patch.object(articulation, 'states', return_value=POSES), \
                patch.object(articulation, 'plan_for', side_effect=lambda project, state: copy.deepcopy(posed[state])):
            requests = listing.picture_requests(record, product, 225, rows)
            planned = listing.planned(record, product, [])
        self.assertEqual([name for name in requests if name.startswith('pose_')], ['pose_closed', 'pose_open', 'pose_wide'])
        for name in ('pose_closed', 'pose_open', 'pose_wide'):
            with self.subTest(name=name):
                # the main picture's camera, framed for every state alike
                self.assertEqual({key: value for key, value in requests[name].items() if key != 'plan'}, {key: value for key, value in requests['hero'].items() if key != 'plan'})
                self.assertEqual(requests[name]['plan']['articulation_envelope'], [{}, posed['open'], posed['wide']])
                self.assertEqual(requests[name]['plan']['rotation'], requests['hero']['plan']['rotation'])
        self.assertNotIn('articulation', requests['pose_closed']['plan'])                                    # the rest pose is the model as it is
        self.assertEqual((requests['pose_open']['plan']['articulation'], requests['pose_wide']['plan']['articulation']), (posed['open'], posed['wide']))
        self.assertNotIn('articulation_envelope', requests['hero']['plan']); self.assertNotIn('articulation', requests['hero']['plan'])
        self.assertNotIn('skipped', next(row for row in planned if row['kind'] == 'T13'))
        # one state, no pose agent record, or no poses page: nothing more is staged
        for states, kinds in ((POSES[:1], rows), ([], rows), (POSES, rows[:1])):
            with self.subTest(states=len(states), kinds=len(kinds)), patch.object(articulation, 'states', return_value=states), \
                    patch.object(articulation, 'plan_for', side_effect=lambda project, state: copy.deepcopy(posed[state])):
                self.assertFalse([name for name in listing.picture_requests(record, product, 225, kinds) if name.startswith('pose_')])
        self.assertIn('活动部件', next(row['skipped'] for row in listing.planned(record, product, []) if row['kind'] == 'T13'))

    def test_points_found_on_the_picture_are_kept_while_the_view_stays_the_same(self):
        listing.save_product('fixture', {'features': [{'title': 'Side cutter', 'parts': []}, {'title': 'Grip', 'parts': [PARTS[1]]}]})
        project = store.get('project', 'fixture')
        listing._update('fixture', lambda record, lst: lst.__setitem__('placed', {'view': listing._view_key(record),
                        'features': {'Side cutter': {'point': [.4, .5], 'parts': [PARTS[0]]}, 'Grip': {'point': [.6, .5], 'parts': []}}}))
        features = listing.stored(store.get('project', 'fixture'))[0]['features']
        self.assertEqual((features[0].get('point'), features[0]['parts']), ([.4, .5], [PARTS[0]]))
        self.assertEqual(features[1]['parts'], [PARTS[1]])                       # a part the user chose stays
        moved = store.get('project', 'fixture'); moved['plan'] = {**moved['plan'], 'rotation': [0, 0, 90]}; store.put('project', moved)
        self.assertIsNone(listing.stored(store.get('project', 'fixture'))[0]['features'][0].get('point'))     # shown another way: found again

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

    def test_a_pasted_description_is_sorted_into_the_record_keeping_only_what_it_says(self):
        import brandkit_review
        text = '棘轮割管器 PC-22\n切割范围 6-22 mm，整机长 215 mm，重 380 g。硬化钢刀轮可更换；双导向滚轮让切口平直。'
        answer = {'title': '棘轮割管器', 'model': 'PC-22', 'longest_side': {'value': '215', 'unit': 'mm', 'quote': '整机长 215 mm'}, 'headline': '6-22 mm 管材一次切平',
                  'subline': '硬化钢刀轮，双导向滚轮', 'notes': ['产地未写'],
                  'features': [{'title': '硬化钢刀轮', 'detail': '可更换', 'parts': [PARTS[0], 'Not a part']},
                               {'title': '双导向滚轮', 'detail': '切口平直，寿命 5000 次', 'parts': []}],          # a number the text never gave
                  'specs': [{'label': '切割范围', 'value': '6-22 mm'}, {'label': '重量', 'value': '380 g'}, {'label': '功率', 'value': '12 W'}],
                  'figures': [{'value': '22', 'unit': 'mm', 'label': '最大管径'}]}
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), \
                patch.object(brandkit_review, '_ask', return_value=answer) as asked:
            kept = listing.extract('fixture', {'text': text})['product']                   # a record with English words keeps English
            self.assertEqual((kept['language'], asked.call_args.args[2]['output_language']), ('en', 'English'))
            listing.save_product('fixture', {'title': '', 'headline': '', 'features': [], 'language': 'en'})
            product = listing.extract('fixture', {'text': text})['product']                 # a blank one takes the text's language
        request = asked.call_args.args[2]
        self.assertEqual((request['text'], request['output_language']), (text, 'Simplified Chinese'))
        self.assertIn(PARTS[0], request['model_parts'])
        self.assertEqual((product['title'], product['model'], product['language'], product['size_mm']), ('棘轮割管器', 'PC-22', 'zh', 215.0))
        self.assertEqual([(row['title'], row['parts']) for row in product['features']], [('硬化钢刀轮', [PARTS[0]])])        # the invented 5000 left out
        self.assertEqual([row['label'] for row in product['specs']], ['切割范围', '重量'])                                     # 12 W was never written
        self.assertEqual(product['figures'], [{'value': '22', 'unit': 'mm', 'label': '最大管径'}])
        self.assertEqual((product['source'], product['drafted']['source']), (text, 'extracted'))
        self.assertIn('双导向滚轮', product['drafted']['notes'][0]); self.assertIn('功率', product['drafted']['notes'][0])
        self.assertIn('产地未写', product['drafted']['notes'])
        self.assertNotIn('size_mm', [] if product['size_mm'] else ['size_mm'])
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), \
                patch.object(brandkit_review, '_ask', return_value={**answer, 'longest_side': {'value': '30', 'unit': 'cm', 'quote': '整机长 215 mm'}, 'features': [], 'specs': [], 'figures': []}):
            again = listing.extract('fixture', {'text': text})['product']
        self.assertEqual((again['size_mm'], [row['title'] for row in again['features']]), (215.0, ['硬化钢刀轮']))    # a size not in the text is not taken; empty lists keep what was there
        with patch.object(brandkit_review, '_configs', return_value=(None, None)):
            with self.assertRaisesRegex(ValueError, '没有配置文本模型'):
                listing.extract('fixture', {'text': text})
        self.assertEqual(listing._numbers('管径 16,20,25 mm，1,200 g，功率１２００W，2.0Ah'), {'16', '20', '25', '1200', '2'})
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), \
                patch.object(brandkit_review, '_ask', return_value={**answer, 'longest_side': {'value': '215', 'unit': 'm', 'quote': '整机长 215 mm'},
                                                                    'features': [{'title': '刀轮', 'detail': '', 'parts': [PARTS[0]] * 60}]}):
            far = listing.extract('fixture', {'text': text})['product']                   # 215 m is out of range, 60 parts are one
        self.assertEqual((far['size_mm'], far['features'][0]['parts']), (215.0, [PARTS[0]]))
        self.assertTrue(any('超出范围' in note for note in far['drafted']['notes']))
        for body in ({'text': '  '}, {'text': 'x' * (listing.MAX_SOURCE + 1)}, {}):
            with self.assertRaises(ValueError):
                listing.extract('fixture', body)
        self.assertEqual(listing.save_product('fixture', {'source': 'kept'})['product']['source'], 'kept')


class BuildTests(StoreCase):
    def setUp(self):
        super().setUp()
        mocked = patch.object(components, 'stage', side_effect=self.stage); mocked.start(); self.addCleanup(mocked.stop)
        self.jobs = []; self.failing = {}; self.scenes = []
        for target, name, value in ((brandkit_render, 'available', True), (brandkit_set, 'image_model_ready', False)):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(brandkit_render, 'render', side_effect=self.fake_render); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(brandkit_render, 'export_svg', side_effect=self.fake_svg); mocked.start(); self.addCleanup(mocked.stop)
        listing.save_product('fixture', {'title': 'Ratchet Pipe Cutter', 'model': 'PC-22', 'headline': 'One-hand clean cuts', 'subline': 'Square, burr-free cuts.',
                                         'size_mm': 225, 'features': [{'title': 'Hardened cutting wheel', 'detail': 'Replaceable', 'parts': [PARTS[0]]},
                                                                      {'title': 'Twin guide rollers', 'parts': [PARTS[1]]}, {'title': 'Lifetime sharpness'}],
                                         'specs': [{'label': 'Pipe capacity', 'value': '6 - 22 mm'}], 'figures': [{'value': '22', 'unit': 'mm', 'label': 'Max pipe'}]})
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'badges': ['PRO'], 'legal': '*A legal line.'})

    def fake_svg(self, template_id, job, out_svg, work=None):
        out_svg = Path(out_svg); out_svg.parent.mkdir(parents=True, exist_ok=True)
        out_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>%s</text></svg>' % template_id)
        (out_svg.parent / 'fonts').mkdir(exist_ok=True); (out_svg.parent / 'fonts' / 'Inter-Variable.ttf').write_bytes(b'font')
        return out_svg

    def fake_render(self, template_id, job, out, work=None):
        """The browser stand-in: a page of the job's size, and the layout it was asked for remembered."""
        self.jobs.append((template_id, job))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (job['canvas']['width'], job['canvas']['height']), job['palette']['bg']).save(out)
        issues = self.failing[template_id].pop(0) if self.failing.get(template_id) else []
        return {'issues': issues, 'notes': [], 'render': {'png': str(out), 'size': [job['canvas']['width'], job['canvas']['height']]}}

    def scene(self, project, proposal, hint='', cancelled=None, progress=None, folder=None, pose='auto'):
        """The image model's stand-in: a scene picture with the product's box in it."""
        self.scenes.append(proposal); folder.mkdir(parents=True, exist_ok=True)
        if progress: progress('图像模型正在生成场景')
        png = folder / (folder.name + '.png'); Image.new('RGB', (2048, 1536), (90, 110, 120)).save(png)
        # scenes are painted at once: each takes the check result of its own place in the set (scene1, scene2, ...)
        number = int(re.match(r'scene(\d+)', folder.name).group(1)) - 1 if re.match(r'scene(\d+)', folder.name) else 0
        checks = getattr(self, 'scene_checks', None)
        status = checks[number] if checks and number < len(checks) else 'passed'
        return None, {'summary': 'A scene', 'model': 'image-model', 'check': {'status': status}}, \
            {'png': png, 'jpg': png, 'width': 2048, 'height': 1536, 'record': {'anchor': {'box': [.4, .3, .6, .8]}}}

    def with_scenes(self):
        proposal = {'segment_id': None, 'scene': 'A workshop bench.', 'people': True, 'framing': 'hands', 'method': 'generate', 'source': 'rules'}
        for target, name, value in ((brandkit_set, 'image_model_ready', True), (scene_stylist, 'propose', proposal), (scene_stylist, '_config', None)):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_stylist, 'generate', side_effect=self.scene); mocked.start(); self.addCleanup(mocked.stop)

    def job(self, template_id):
        return next(job for kind, job in reversed(self.jobs) if kind == template_id)

    def test_building_lays_out_the_templates_in_order_and_remembers_them(self):
        self.add_picture(name='Battery', sku='BP-205', quantity='2')
        seen = []
        pages, notes = listing.build('fixture', progress=lambda stage, share, detail='': seen.append((stage, round(share, 2))))
        # No image model: the poster stands on a solid ground and the workpiece render is the only scene, too few for the usage page.
        self.assertEqual([page['kind'] for page in pages], ['T12', 'T01', 'T03', 'T02', 'T04', 'T06', 'T05'])
        self.assertEqual([page['file'] for page in pages][:2], ['01-T12.png', '02-T01.png'])
        for page in pages:
            with Image.open(components.path_of('fixture', page['url'])) as image:
                self.assertEqual(image.size, (1500, 1500))
            self.assertTrue(page['checks']['passed'])
        self.assertEqual((seen[0][0], seen[-1][0]), ('pictures', 'compose'))
        self.assertTrue(any('图像模型未配置' in note for note in notes)); self.assertTrue(any('品牌色取自产品' in note for note in notes))
        self.assertEqual(self.job('T12')['mode'], 'solid')
        state = listing.state('fixture')
        self.assertEqual((len(state['pages']), state['brand']['accent_source'], state['order'][0], state['kinds']['T01']['zh']), (7, 'product', 'T12', '功能标注'))
        self.assertFalse(state['scene_options']['image_ready'])
        self.assertIn('2 张', next(row['skipped'] for row in state['plan'] if row['kind'] == 'T08'))
        data, name = listing.archive('fixture')
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            names = bundle.namelist()
            # the pages, then what the checks and the review found of each of them
            self.assertEqual([name for name in names if not name.startswith('editable/')], [page['file'] for page in pages] + ['review-status.json', '审核状态.txt'])
            # each page also as an editable SVG (live text, named layers), with the fonts it names
            self.assertEqual([name for name in names if name.endswith('.svg')], ['editable/' + page['file'][:-4] + '.svg' for page in pages])
            self.assertIn('editable/fonts/Inter-Variable.ttf', names)
        self.assertTrue(all(page['svg'] for page in pages))
        self.assertEqual(name, 'fixture-listing-draft.zip')            # every page, reviewed or not: a draft, and named so
        first = Path(components.path_of('fixture', pages[0]['url'])).parent
        listing.build('fixture')                                                            # a new build replaces the old files
        self.assertFalse(first.exists()); self.assertEqual(len(list((self.data / 'fixture' / 'listing').glob('build-*'))), 1)

    def test_the_jobs_carry_the_users_words_and_what_was_measured_on_the_model(self):
        self.add_picture(name='Battery', sku='BP-205', quantity='2')
        listing.save_product('fixture', {'language': 'zh', 'aspect': '4:5', 'headline': '单手操作，切口平整'})
        listing.save_brand('fixture', {'accent': '#1f6feb', 'unit': 'in'})
        listing.build('fixture')
        callouts = self.job('T01')
        self.assertEqual((callouts['lang'], callouts['canvas']), ('zh', {'width': 1200, 'height': 1500}))
        self.assertEqual([row['title'] for row in callouts['features']], ['Hardened cutting wheel', 'Twin guide rollers'])       # a point without a part has nothing to point at
        hero = callouts['assets'][callouts['hero']]
        for row in callouts['features']:
            self.assertTrue(0 <= row['anchor'][0] <= .5 if row['title'].startswith('Hardened') else .5 <= row['anchor'][0] <= 1, row)   # on its own half of the product
        self.assertTrue(Path(hero['path']).is_file()); self.assertTrue(hero['file'].startswith('file://')); self.assertIn('hull', hero)
        with Image.open(hero['path']) as picture:
            self.assertEqual(picture.mode, 'RGB'); self.assertEqual(picture.getpixel((0, 0)), (255, 255, 255))          # trimmed and laid on white
        self.assertEqual(self.job('T12')['headline'], '单手操作，\n切口平整')                                                 # broken at the pause, not mid-phrase
        size = self.job('T06')
        # Only the longest side was given (the cutter stands taller than it is wide, so it is its height): one line, nothing measured on the model, no turned view.
        self.assertEqual([(row['key'], row['unit'], row['value']) for row in size['dimensions']], [('height', 'in', round(225 / 25.4, 1))])
        self.assertIsNone(size.get('end')); self.assertNotIn('depth', size)
        self.assertEqual(self.job('T04')['specs'][0], {'label': 'Max pipe', 'value': '22', 'unit': 'mm'})
        box = self.job('T05')
        self.assertEqual([(row['name'], row['qty']) for row in box['items']], [('Ratchet Pipe Cutter', 1), ('Battery', 2)])
        for kind, job in self.jobs:
            self.assertNotEqual(job['palette']['accent'].lower(), brandkit_render.template_info(kind)['tokens']['palette']['accent'].lower())
            self.assertNotIn('demo', job)                                                    # the user's own product: no demo watermark

    def test_scenes_are_made_by_the_image_model_once_and_laid_into_the_scene_pages(self):
        self.with_scenes()
        pages, _ = listing.build('fixture')
        self.assertIn('T08', [page['kind'] for page in pages])
        self.assertEqual(len(self.scenes), 3)
        self.assertEqual([row['people'] for row in self.scenes][:2], [True, False])                         # at work, then set down where it is used
        poster = self.job('T12')
        self.assertEqual((poster['mode'], poster['scene'], poster['focus']), ('overlay', 'scene1', [.5, .55]))
        self.assertEqual(len(self.job('T08')['items']), 3)
        state = listing.state('fixture')
        self.assertEqual(len(state['scenes']), 3); self.assertTrue(all(row['url'].startswith('/files/fixture/listing/scenes-') for row in state['scenes']))
        listing.build('fixture')
        self.assertEqual(len(self.scenes), 3)                                                # the same settings reuse the pictures
        listing.save_product('fixture', {'scenes': {'hint': 'on a building site', 'count': 2}})
        listing.build('fixture')
        self.assertEqual(len(self.scenes), 5)
        self.assertEqual(len(list((self.data / 'fixture' / 'listing').glob('scenes-*'))), 1)
        listing.save_product('fixture', {'scenes': {'enabled': False}, 'usecase': {'enabled': False}})
        pages, _ = listing.build('fixture')
        self.assertEqual(self.job('T12')['mode'], 'solid'); self.assertNotIn('T08', [page['kind'] for page in pages])

    def test_one_scene_is_made_again_and_only_that_one(self):
        self.with_scenes(); self.scene_checks = ['passed', 'failed', 'passed']
        listing.build('fixture')
        self.assertEqual(len(self.scenes), 3)
        shown = listing.state('fixture')['scenes']
        self.assertEqual([row['check'] for row in shown], ['passed', 'failed', 'passed'])
        self.assertEqual([row['index'] for row in shown], [0, 1, 2])
        self.assertEqual([row['placed'] for row in shown], ['painted'] * 3)
        self.assertFalse(any(row['locked'] for row in shown))          # painted and not put back: never called locked
        kept = [row['url'] for row in shown]
        # the failure memory of the slot goes with it, and a stale page cannot remove another picture
        def remember(project, record):
            record['scenes']['failed'] = [{'scene': '%s%d' % (shown[1]['role'], 1 + sum(1 for row in shown[:1] if row['role'] == shown[1]['role'])),
                                           'role': shown[1]['role'], 'count': 2}]
        listing._update('fixture', remember)
        with self.assertRaises(ValueError):
            listing.forget_scene('fixture', 1, url=kept[0])
        with self.assertRaises(KeyError):
            listing.forget_scene('fixture', 7)
        listing.forget_scene('fixture', 1, url=kept[1])
        record = store.get('project', 'fixture')['listing']['scenes']
        self.assertEqual(([row['url'] for row in record['items']], record['failed']), ([kept[0], kept[2]], []))
        listing.build('fixture')
        self.assertEqual(len(self.scenes), 4)                                                # one picture made, two kept
        after = [row['url'] for row in listing.state('fixture')['scenes']]
        self.assertEqual(len(after), 3); self.assertIn(kept[0], after); self.assertIn(kept[2], after)

    def test_a_scene_is_not_removed_while_a_build_runs_and_the_route_starts_a_build(self):
        self.with_scenes()
        listing.build('fixture')
        import app as application
        client = application.app.test_client()
        self.assertEqual(client.post('/api/projects/fixture/listing/scenes/9/redraw', json={}).status_code, 404)
        with patch.object(listing.threading, 'Thread'):
            first = listing.state('fixture')['scenes'][0]
            made = client.post('/api/projects/fixture/listing/scenes/0/redraw', json={'url': first['url']})
            self.assertEqual(made.status_code, 202, made.get_json()); self.assertEqual(made.get_json()['status'], 'queued')
            self.assertEqual(len(store.get('project', 'fixture')['listing']['scenes']['items']), 2)
            busy = client.post('/api/projects/fixture/listing/scenes/0/redraw', json={})
            self.assertEqual(busy.status_code, 409)
            self.assertEqual(len(store.get('project', 'fixture')['listing']['scenes']['items']), 2)

    def test_a_scene_is_redrawn_only_when_a_new_one_can_be_made_and_goes_back_to_its_own_slot(self):
        self.with_scenes()
        listing.build('fixture')
        kept = [row['url'] for row in listing.state('fixture')['scenes']]
        # no image model: the picture stays where it is, nothing is taken out
        with patch.object(listing.brandkit_set, 'image_model_ready', return_value=False):
            with self.assertRaisesRegex(ValueError, '原图保留'):
                listing.redraw_scene('fixture', 0, kept[0])
        self.assertEqual([row['url'] for row in store.get('project', 'fixture')['listing']['scenes']['items']], kept)
        import app as application
        self.assertEqual(application.app.test_client().post('/api/projects/fixture/listing/scenes/0/redraw', json=[1]).status_code, 400)
        # every kept row knows the slot it was made for; a rebuild puts it back there, so the clicked one is the one made again
        slots = [row.get('slot') for row in store.get('project', 'fixture')['listing']['scenes']['items']]
        self.assertTrue(all(slots), slots)
        first_brief = self.scenes[0]; made = len(self.scenes)
        listing.forget_scene('fixture', 0, url=kept[0])
        listing.build('fixture')
        self.assertEqual(len(self.scenes), made + 1)
        self.assertEqual(self.scenes[-1]['scene'], first_brief['scene'])                 # the first scene's own brief, not another
        self.assertEqual([row['url'] for row in listing.state('fixture')['scenes']][1:], kept[1:])

    def test_the_state_says_what_a_refused_ai_service_left_undone(self):
        def refused(project, record):
            record['notes'] = ['翻译产品文字时文本模型没有回应（AI 服务的额度已用尽或账户欠费，请充值或联系管理员后重试。（HTTP 402））。',
                               '第 2 张场景图没有生成：图像服务的额度已用尽或账户欠费，请充值后重试。（HTTP 402）', '品牌色取自产品本身的颜色 #d9463d。']
            record['pages'] = [{'kind': 'T12', 'notes': ['视觉审核没有完成（AI 服务的额度已用尽或账户欠费（HTTP 402）），这张图未经审核。', 'd3: value pill longer than its line'],
                                'review': {'status': 'not_verified'}, 'checks': {'passed': True, 'issues': []}}]
        listing._update('fixture', refused)
        state = listing.state('fixture')
        self.assertEqual(state['degraded'], {'reason': 'quota', 'affected': ['translation', 'scenes', 'review'], 'words': ['文字翻译', '场景图', '视觉审核']})
        self.assertEqual(state['notes_view']['seller'][0]['level'], 'critical')
        page = state['pages'][0]
        self.assertEqual([row['text'] for row in page['note_view']['tech']], ['d3: value pill longer than its line'])
        self.assertEqual(len(page['note_view']['seller']), 1); self.assertFalse(page['ready'])
        listing._update('fixture', lambda project, record: record.update(notes=['品牌色取自产品本身的颜色 #d9463d。'], pages=[]))
        self.assertIsNone(listing.state('fixture')['degraded'])

    def test_the_copy_and_vision_models_supervise_every_page(self):
        import brandkit_review
        reviewed = {}; checks = []

        def ask(config, system, facts, schema, image_path=None, reference_path=None, clean=None):
            if system == brandkit_review.COPYWRITER:
                return {'pages': [{'template': 'T01', 'eyebrow': 'Parts', 'headline': 'Cuts clean, grips firm'},
                                  {'template': 'T02', 'eyebrow': 'Close', 'headline': 'Cuts 50 mm pipe'},             # a number nobody gave: refused
                                  {'template': 'T04', 'eyebrow': 'Specs', 'headline': 'x' * 80}]}                     # too long: refused
            if system == brandkit_review.SET_REVIEW:
                return {'consistent': True, 'repeats': [[3, 4]], 'weakest': 4, 'notes': '**第 3、4 张**都是部件特写。'}
            if system == brandkit_review.VERIFIER:                  # the second look: the close-up page's complaint does not stand
                checks.append(facts['template'])
                return {'claims': [{'number': row['number'], 'real': facts['template'] != 'T02', 'why': 'seen'} for row in facts['claims']]}
            kind = facts['template']['id']; reviewed[kind] = reviewed.get(kind, 0) + 1
            self.assertEqual(reference_path, str(self.data / 'fixture' / 'listing' / Path(reference_path).parent.parent.name / 'assets' / 'hero.png'))
            self.assertIn('facts', facts); self.assertEqual(facts['criteria_maximum'], brandkit_review.CRITERIA)
            good = {'scores': {'product': 22, 'hierarchy': 18, 'composition': 17, 'task': 13, 'color': 9, 'independence': 8}, 'vetoes': [], 'issues': [], 'summary': 'ok'}
            if kind == 'T01' and reviewed[kind] == 1:              # crowded: drop the second callout... which the minimum of two does not allow
                return {**good, 'scores': {**good['scores'], 'composition': 8}, 'issues': [
                    {'region': 'headline', 'evidence': 'too long', 'action': {'type': 'shorten_text', 'target': 'headline', 'text': 'Grips firm'}},
                    {'region': 'callouts', 'evidence': 'crowded', 'action': {'type': 'drop_item', 'target': 'features', 'index': 1}}]}
            if kind == 'T02':                                       # a false alarm on the close-ups
                self.assertEqual(facts['measured_layout']['canvas_px'], [1500, 1500])
                return {**good, 'scores': {**good['scores'], 'product': 10}, 'issues': [
                    {'region': 'close-up', 'evidence': 'product cut off', 'action': {'type': 'next_layout'}}]}
            if kind == 'T06':                                       # never good enough: two rounds, then reported
                return {**good, 'vetoes': [{'kind': 'text_unreadable', 'evidence': 'table too small'}], 'issues': [
                    {'region': 'table', 'evidence': 'small', 'action': {'type': 'next_layout'}}]}
            return good
        for target, name, value in ((brandkit_review, 'available', (True, True)), (brandkit_review, '_configs', ({'model': 'text'}, {'model': 'vision'}))):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(brandkit_review, '_ask', side_effect=ask); mocked.start(); self.addCleanup(mocked.stop)
        pages, notes = listing.build('fixture')
        by_kind = {page['kind']: page for page in pages}
        self.assertEqual(self.job('T01')['headline'], 'Grips firm'); self.assertEqual(self.job('T01')['eyebrow'], 'Parts')
        self.assertEqual(len(self.job('T01')['features']), 2)                                  # the drop that would go below two was not made
        # A refused line gives way to the product's own name, not a stock phrase.
        # A refused line gives way to the product's own data: the parts the close-ups show, the strongest figure.
        self.assertEqual(self.job('T02')['headline'], 'Hardened cutting wheel,\n[[twin guide rollers]]')
        self.assertTrue(self.job('T04')['headline'].startswith('[['))
        self.assertTrue(any('改用产品资料' in note for note in notes))
        self.assertEqual((by_kind['T01']['review']['status'], by_kind['T01']['review']['first_score'], by_kind['T01']['review']['score']), ('revised', 78, 87))
        self.assertIn('缩短headline', by_kind['T01']['review']['applied'])
        self.assertEqual((by_kind['T12']['review']['status'], by_kind['T12']['review']['rounds']), ('passed', 0))
        self.assertEqual((by_kind['T06']['review']['status'], by_kind['T06']['review']['vetoes']), ('failed', ['text_unreadable']))
        self.assertEqual(reviewed['T06'], 2)                                                   # a revision that scores no better ends the loop
        self.assertTrue(any('没有让评分变好' in note for note in by_kind['T06']['notes']))
        self.assertTrue(any('视觉审核未通过' in note for note in by_kind['T06']['notes']))
        self.assertTrue(any(note.startswith('整套检查') and '第 3、4 张' in note and '*' not in note for note in notes))
        self.assertEqual((by_kind['T02']['review']['status'], by_kind['T02']['review']['refuted'], reviewed['T02']), ('passed', 1, 1))
        self.assertNotIn('T12', checks)                                                        # a page that passes is not checked again
        self.assertFalse(list((self.data / 'fixture' / 'listing').glob('build-*/*.rev*.png')))

    def test_a_set_without_selling_points_makes_none_up(self):
        listing.save_product('fixture', {'title': '06_diagonal_cutting_pliers', 'headline': '', 'subline': '', 'features': []})
        pages, notes = listing.build('fixture')
        kinds = [page['kind'] for page in pages]
        self.assertFalse({'T01', 'T02', 'T03'} & set(kinds), kinds)
        self.assertTrue(any('没有填写卖点' in note for note in notes), notes)
        self.assertNotIn('_', self.job('T12')['headline']); self.assertIn('Diagonal Cutting', self.job('T12')['headline'].replace('\n', ' '))
        plan = listing.state('fixture')['plan']
        self.assertIn('没有填写卖点', next(row['skipped'] for row in plan if row['kind'] == 'T01'))

    def test_a_mesh_named_point_is_left_out_and_a_rejected_line_is_not_drawn(self):
        import brandkit_review
        listing.save_product('fixture', {'features': [{'title': 'Hardened cutting wheel', 'parts': [PARTS[0]]}, {'title': 'Cylinder grip', 'parts': [PARTS[1]]},
                                                      {'title': 'Twin guide rollers', 'parts': [PARTS[1]]}]})
        project = store.get('project', 'fixture')           # an old drafted record: "Cylinder grip" came from the mesh name
        project['listing']['product']['drafted'] = {'source': 'ai', 'at': 0, 'notes': []}; store.put('project', project)

        def ask(config, system, facts, schema, image_path=None, **kwargs):
            if system is brandkit_set.POINT_CHECK:
                self.assertEqual([row['selling_point'] for row in facts['points']], ['Hardened cutting wheel', 'Twin guide rollers'])
                return {'points': [{'number': 1, 'on': True, 'visible': True, 'x': 0, 'y': 0}, {'number': 2, 'on': False, 'visible': False, 'x': 0, 'y': 0}]}
            raise ValueError('not asked in this test')
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), patch.object(brandkit_review, '_ask', side_effect=ask):
            pages, notes = listing.build('fixture')
        kinds = [page['kind'] for page in pages]
        self.assertNotIn('T01', kinds)                                               # one line left of two: no callout page
        self.assertTrue(any('Cylinder grip' in note for note in notes)); self.assertTrue(any('去掉这些引线' in note for note in notes))
        located = store.get('project', 'fixture')['listing']['located']
        self.assertTrue(any(key.startswith('check-') for key in located))
        for kind, job in self.jobs:
            for row in job.get('features') or job.get('details') or []:
                self.assertNotEqual(row.get('title'), 'Cylinder grip')

    def test_a_refused_review_is_not_asked_again_for_the_other_pages(self):
        import brandkit_review
        calls = []

        def ask(config, system, facts, schema, image_path=None, reference_path=None, clean=None):
            calls.append(system); raise ValueError('AI 服务的额度已用尽（HTTP 402）')
        for target, name, value in ((brandkit_review, 'available', (False, True)), (brandkit_review, '_configs', (None, {'model': 'vision'}))):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        pool = listing.concurrent.futures.ThreadPoolExecutor
        with patch.object(brandkit_review, '_ask', side_effect=ask), patch.object(listing.concurrent.futures, 'ThreadPoolExecutor', lambda workers: pool(1)):
            pages, notes = listing.build('fixture')
        self.assertEqual(len(calls), 1); self.assertEqual({page['review']['status'] for page in pages}, {'not_verified'})
        self.assertTrue(any('之后的页面没有再请求审核' in note for note in notes))

    def test_without_a_vision_model_no_page_is_called_reviewed(self):
        pages, notes = listing.build('fixture')
        self.assertEqual({page['review']['status'] for page in pages}, {'not_verified'})
        self.assertTrue(any('没有经过视觉审核' in note for note in notes))

    def test_the_detail_column_shows_points_the_callouts_did_not(self):
        listing.save_product('fixture', {'features': [{'title': 'A', 'parts': [PARTS[0]]}, {'title': 'B', 'parts': [PARTS[1]]},
                                                      {'title': 'C', 'parts': [PARTS[0]]}, {'title': 'D', 'parts': [PARTS[1]]},
                                                      {'title': 'E', 'parts': [PARTS[0]]}, {'title': 'F', 'parts': [PARTS[1]]}]})
        listing.build('fixture')
        callouts = [row['title'] for row in self.job('T01')['features']]; column = [row['title'] for row in self.job('T03')['details']]
        # A, C and E name the same part: one callout per spot (stacked ones would overlap); the column shows the others.
        self.assertEqual(callouts, ['A', 'B']); self.assertEqual(column[:2], ['C', 'D'])
        listing.save_product('fixture', {'features': [{'title': 'A', 'parts': [PARTS[0]]}, {'title': 'B', 'parts': [PARTS[1]]}]})
        listing.build('fixture')
        self.assertEqual([row['title'] for row in self.job('T03')['details']], ['A', 'B'])          # only two points: shown again rather than not at all

    def test_the_stock_lines_are_tried_when_the_copywriters_do_not_fit(self):
        import brandkit_review
        self.failing = {'T01': [[{'check': 'text_max_lines', 'id': 'headline'}]] * 2}          # both arrangements with the model's line
        with patch.object(brandkit_review, 'available', return_value=(True, False)), \
                patch.object(brandkit_review, 'write_copy', return_value=({'T01': {'eyebrow': 'Parts', 'headline': 'A very long line'}}, None)):
            pages, _ = listing.build('fixture')
        tried = [job['headline'] for kind, job in self.jobs if kind == 'T01']
        self.assertEqual(tried[0], 'A very long line'); self.assertEqual(self.job('T01')['headline'], 'Square, burr-free cuts.')     # the seller's own line
        self.assertTrue(next(page for page in pages if page['kind'] == 'T01')['checks']['passed'])

    def test_scenes_whose_product_check_failed_stay_off_the_poster_and_fail_their_pages(self):
        import brandkit_review
        self.with_scenes(); self.scene_checks = ['failed', 'passed', 'failed']
        good = {'scores': {'product': 22, 'hierarchy': 18, 'composition': 17, 'task': 13, 'color': 9, 'independence': 8}, 'vetoes': [], 'issues': [], 'summary': 'ok'}
        for target, name, value in ((brandkit_review, 'available', (False, True)), (brandkit_review, '_configs', (None, {'model': 'vision'})),
                                    (brandkit_review, '_ask', good)):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        pages, _ = listing.build('fixture')
        self.assertEqual((self.job('T12')['scene'], self.job('T04')['scene']), ('scene2', 'scene4'))      # the workpiece render, not a failed scene
        by_kind = {page['kind']: page for page in pages}
        self.assertEqual((by_kind['T12']['review']['status'], by_kind['T04']['review']['status']), ('passed', 'passed'))
        self.assertEqual((by_kind['T08']['review']['status'], by_kind['T08']['review']['vetoes']), ('passed', []))
        self.assertEqual([item['asset'] for item in self.job('T08')['items']], ['scene2', 'scene4'])       # two good scenes carry the page alone
        self.scene_checks = ['passed', 'passed', 'failed']; listing.save_product('fixture', {'scenes': {'hint': 'again'}})
        listing.build('fixture')
        self.assertEqual((self.job('T12')['scene'], self.job('T04')['scene']), ('scene1', 'scene2'))      # two that passed: one each

    def test_a_page_that_fails_its_checks_is_laid_out_again_lighter(self):
        self.failing = {'T12': [[{'check': 'text_max_lines', 'id': 'headline'}]]}
        pages, _ = listing.build('fixture')
        first, second = [job for kind, job in self.jobs if kind == 'T12']
        self.assertEqual((first['mode'], second['mode']), ('solid', 'solid')); self.assertNotIn('compact', first); self.assertTrue(second['compact'])
        self.assertIn('精简版本', pages[0]['notes'][0]); self.assertTrue(pages[0]['checks']['passed'])

    def posed(self):
        """A model whose parts move: the pose agent's states, and their plans for the renderer."""
        plans = {'closed': None, 'open': {'state': 'open', 'joints': [{'name': 'hinge', 'value': 20.0}]},
                 'wide': {'state': 'wide', 'joints': [{'name': 'hinge', 'value': 35.0}]}}
        for name, value in (('states', {'return_value': POSES}), ('plan_for', {'side_effect': lambda project, state: copy.deepcopy(plans[state])})):
            mocked = patch.object(articulation, name, **value); mocked.start(); self.addCleanup(mocked.stop)

    def test_a_product_with_moving_parts_gets_a_poses_page(self):
        self.posed()
        pages, notes = listing.build('fixture')
        self.assertEqual([page['kind'] for page in pages], ['T12', 'T01', 'T03', 'T13', 'T02', 'T04', 'T06'], notes)
        job = self.job('T13')
        self.assertEqual([row['label'] for row in job['poses']], ['Closed', 'Open', 'Fully open'])
        self.assertEqual([row['asset'] for row in job['poses']], ['pose_closed', 'pose_open', 'pose_wide'])
        self.assertEqual((job['eyebrow'], job['headline']), ('Ratchet Pipe Cutter', 'Closed\n[[to fully open]]'))      # from the data, not a stock line
        sizes = {(job['assets'][name]['width'], job['assets'][name]['height']) for name in job['assets']}
        self.assertEqual(len(sizes), 1)                                                     # one window for every pose: one scale
        staged = [request for request in self.requests if 'articulation_envelope' in request['plan']]
        self.assertEqual(len(staged), 3)
        self.assertEqual([request['plan'].get('articulation', {}).get('state') for request in staged], [None, 'open', 'wide'])
        listing.save_product('fixture', {'language': 'zh'})
        pages, notes = listing.build('fixture')
        job = self.job('T13')
        self.assertEqual([row['label'] for row in job['poses']], ['闭合', '张开', '全开'])
        self.assertEqual(job['headline'], '从闭合\n[[到全开]]')
        self.assertIn('T13', [page['kind'] for page in pages])

    def test_the_set_is_built_when_the_pose_agent_fails(self):
        mocked = patch.object(listing, 'pose_agent_ready', return_value=True); mocked.start(); self.addCleanup(mocked.stop)
        with patch.object(articulation, 'analyse', side_effect=RuntimeError('vision service down')) as analyse:
            pages, notes = listing.build('fixture')
        self.assertEqual((analyse.call_args.args[0], analyse.call_args.args[1]['title'], analyse.call_args.args[2]), ('fixture', 'Ratchet Pipe Cutter', 225.0))
        self.assertNotIn('T13', [page['kind'] for page in pages]); self.assertIn('T12', [page['kind'] for page in pages])
        self.assertTrue(any('活动部件识别失败' in note and 'vision service down' in note for note in notes), notes)
        self.assertNotIn('fixture', articulation.RUNNING)
        failed = {'status': 'failed', 'states': [], 'notes': ['姿态检查未通过：钳口转轴不对'], 'key': 'k'}
        with patch.object(articulation, 'analyse', return_value=failed):
            pages, notes = listing.build('fixture')
        self.assertTrue(any('活动部件的姿态没有通过检查' in note and '钳口转轴不对' in note for note in notes), notes)
        self.assertNotIn('T13', [page['kind'] for page in pages])
        # A stored record for another model file is not used when the pose agent cannot look again.
        record = store.get('project', 'fixture'); record['articulation'] = {'status': 'ready', 'key': 'stale', 'states': POSES, 'joints': []}
        store.put('project', record); mocked.stop()
        with patch.object(listing, 'pose_agent_ready', return_value=False):
            pages, _ = listing.build('fixture')
        self.assertNotIn('T13', [page['kind'] for page in pages])

    def test_a_component_still_being_processed_is_left_out_and_said(self):
        component_id, base = components.reserve('fixture'); shutil.copy(self.picture(), base / 'case.png'); components.register('fixture', component_id, {'name': 'Case'})
        pages, notes = listing.build('fixture')
        self.assertNotIn('T05', [page['kind'] for page in pages]); self.assertIn('还在处理', notes[0]); self.assertIn('Case', notes[0])

    def test_a_task_reports_progress_can_be_cancelled_and_only_one_runs(self):
        with patch.object(listing.threading, 'Thread') as thread:
            task = listing.submit('fixture')
            with self.assertRaises(engine.JobConflictError):
                listing.submit('fixture')
        self.assertEqual((task['status'], task['progress']), ('queued', 0))
        listing._run(thread.call_args.kwargs['args'][0])
        done = listing.latest('fixture')
        self.assertEqual((done['status'], done['progress'], done['pages']), ('completed', 100, 6))
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

    def test_the_web_layer_serves_the_workspace(self):
        import app as application
        mocked = patch.object(application, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)       # the file route reads its own name for the folder
        client = application.app.test_client()
        state = client.get('/api/projects/fixture/listing').get_json()
        self.assertEqual((state['product']['model'], state['brand']['name'], state['pages'], state['task']), ('PC-22', 'NORTHFORGE', [], None))
        self.assertEqual(len(state['presets']), 5); self.assertIn('pipe', state['options']['workpieces']); self.assertEqual(state['order'], list(brandkit_set.ORDER))
        self.assertEqual((state['scene_options']['max'], state['product']['scenes']['count'], state['product']['aspect']), (3, 3, '1:1'))
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
        self.assertEqual((built['task']['status'], built['pages'][1]['kind']), ('completed', 'T01'))
        self.assertEqual(client.get(built['pages'][0]['url']).status_code, 200)
        bundle = client.get('/api/projects/fixture/listing/download')
        self.assertEqual((bundle.status_code, bundle.mimetype), (200, 'application/zip'))
        self.assertIsNone(client.delete('/api/projects/fixture/listing/logo').get_json()['brand']['logo'])
        self.assertEqual(client.delete('/api/projects/fixture/components/%s' % component_id).get_json()['removed'], component_id)
        self.assertEqual(client.get('/api/projects/missing/listing').status_code, 404)


    def test_the_pose_agent_is_shown_and_asked_again_through_the_web_layer(self):
        import app as application
        mocked = patch.object(application, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        client = application.app.test_client()
        self.assertEqual(client.get('/api/projects/fixture/articulation').get_json()['status'], 'unknown')
        folder = self.data / 'fixture' / 'articulation' / '0123456789abcdef'; folder.mkdir(parents=True)
        for name in ('parts.png', 'states1.png', 'probe.png'):
            Image.new('RGB', (40, 20), (250, 250, 250)).save(folder / name)
        record = store.get('project', 'fixture')
        record['articulation'] = {'status': 'ready', 'key': '0123456789abcdef', 'product_kind': 'pliers', 'reasoning': 'Two halves turn about the rivet.',
                                  'states': POSES[:2], 'joints': [{'name': 'hinge', 'type': 'hinge', 'max': 20.0, 'moving': [1]}],
                                  'usage': {'zh': ['钳口夹住电线'], 'en': ['Grip the wire in the jaws']}, 'notes': [], 'at': 7,
                                  'parts_sheet': str(folder / 'parts.png'), 'previews': str(folder / 'states1.png')}
        store.put('project', record)
        view = client.get('/api/projects/fixture/articulation').get_json()
        self.assertEqual((view['status'], view['product_kind'], [row['id'] for row in view['states']], view['running']), ('ready', 'pliers', ['closed', 'open'], False))
        self.assertEqual(view['parts_sheet'], '/api/projects/fixture/articulation/files/0123456789abcdef/parts.png')
        self.assertEqual(client.get(view['parts_sheet']).mimetype, 'image/png'); self.assertEqual(client.get(view['previews']).status_code, 200)
        for bad in ('0123456789abcdef/probe.png', '0123456789abcdef/../../originals/x.png', 'zzzz/parts.png', '0123456789abcdef/states9.png'):
            with self.subTest(bad=bad):
                self.assertEqual(client.get('/api/projects/fixture/articulation/files/' + bad).status_code, 404)
        self.assertEqual(client.get('/api/projects/missing/articulation').status_code, 404)
        record['articulation']['parts_sheet'] = str(self.data / 'elsewhere' / 'parts.png'); store.put('project', record)
        self.assertIsNone(client.get('/api/projects/fixture/articulation').get_json()['parts_sheet'])        # only the pose agent's own folder
        # Asking again runs in the background; while it runs the record says so, and a failure is kept as a note.
        import articulation as pose_agent
        with patch('threading.Thread') as thread, patch.object(pose_agent, 'analyse', side_effect=ValueError('渲染失败')) as analyse:
            started = client.post('/api/projects/fixture/articulation', json={})
            self.assertEqual((started.status_code, started.get_json()['running']), (202, True))
            self.assertEqual(client.post('/api/projects/fixture/articulation', json={}).status_code, 202)
            self.assertEqual(thread.call_count, 1)                                              # one run at a time
            thread.call_args.kwargs['target']()
        self.assertEqual((analyse.call_args.args[0], analyse.call_args.args[2], analyse.call_args.kwargs), ('fixture', 225.0, {'force': True}))
        view = client.get('/api/projects/fixture/articulation').get_json()
        self.assertEqual((view['running'], view['status']), (False, 'ready')); self.assertIn('识别失败：渲染失败', view['notes'][-1])


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


class WorkpieceNameTests(unittest.TestCase):
    def test_a_workpiece_is_matched_by_whole_words(self):
        """A service cart is not a vice: it was shown standing on a plank, as a clamp would be shown closed round one."""
        import listing
        for name in ('Service Cart', 'Advice board', 'Seesaw', 'Device holder'):
            self.assertIsNone(listing.usecase_for(name), name)
        self.assertEqual(listing.usecase_for('Bench Vice 6 inch')['kind'], 'board')
        self.assertEqual(listing.usecase_for('F-Clamps, set of 4')['kind'], 'board')
        self.assertEqual(listing.usecase_for('Combination wrenches')['kind'], 'pipe')
        self.assertEqual(listing.usecase_for('台钳 150mm')['kind'], 'board')
        self.assertFalse(listing.default_product({'name': 'Service Cart'})['usecase']['enabled'])


class ResultsViewTests(unittest.TestCase):
    """What the workspace shows of a built set: the notes a seller reads, grouped and ordered, and the scenes' status."""


    def test_the_reviewers_own_check_names_are_put_into_words_even_inside_chinese(self):
        page = listing.page_public({'notes': [], 'review': {'summary': '属于实质差异，构成scene_product_mismatch。', 'vetoes': ['scene_product_mismatch'],
                                                            'issues': [{'region': 'scene2', 'evidence': '构成scene_product_mismatch'}, 'text_overflow']}})
        self.assertEqual(page['review']['summary'], '属于实质差异，构成场景里的产品与模型不符。')
        self.assertEqual(page['review']['issues'], [{'region': '第 2 张场景图', 'evidence': '构成场景里的产品与模型不符'}, '文字超出文本框'])
    def test_layout_log_lines_are_technical_and_the_actionable_ones_read_in_chinese(self):
        self.assertEqual(listing.plain_note('d3: value pill longer than its line'), ('d3: value pill longer than its line', True))
        self.assertEqual(listing.plain_note('headline: stock phrase replaced by the figures (BUILT TOUGH)')[1], False)
        self.assertRegex(listing.plain_note('headline too long for 2 line(s): shorten T04_headline')[0], '主标题太长')
        self.assertEqual(listing.plain_note('item 2 (Battery): no picture, slot left out (supply a cut-out)'),
                         ('第 2 个组件没有图片，没有放进清单：请上传它的模型或图片。', False))
        # the review's and the checks' own names in a Chinese note become words
        text, technical = listing.plain_note('视觉审核未通过（61 分，否决项 scene_product_mismatch、claim_not_in_facts）：场景图的产品核对未通过：scene2')
        self.assertFalse(technical)
        self.assertNotRegex(text, r'[a-z]+_[a-z]+'); self.assertIn('场景里的产品与模型不符', text); self.assertIn('第 2 张场景图', text)
        self.assertEqual(listing.plain_note('版式检查仍有 1 处问题：text_overflow。')[0], '版式检查仍有 1 处问题：文字超出文本框。')

    def test_notes_are_grouped_once_and_the_ones_that_matter_come_first(self):
        none = brandkit_set.NO_FEATURES
        notes = ['场景图沿用上次生成的 3 张（设置没有变化）。', '没有填写卖点，卖点标注图、细节特写图没有生成（不会替你编造卖点）：请写下卖点。',
                 '「功能标注」没有生成：%s。' % none, '视觉审核没有完成（AI 服务的额度已用尽或账户欠费（HTTP 402）），这张图未经审核。',
                 'headline: stock phrase replaced by the figures (X)', 'd3: value pill longer than its line', 'd3: value pill longer than its line',
                 '「尺寸图」排版失败：文字放不下']
        view = listing.note_view(notes, [('功能标注', none), ('主体 + 细节栏', none), ('深色细节特写', none)])
        texts = [row['text'] for row in view['seller']]
        self.assertEqual([row['level'] for row in view['seller']], ['critical', 'error', 'error', 'warn', 'info'])
        held = [text for text in texts if none.rstrip('。') in text]
        self.assertEqual(held, ['「功能标注」、「主体 + 细节栏」、「深色细节特写」暂时无法生成：%s。' % none])     # once, not four times
        self.assertFalse(any(text.startswith('没有填写卖点') for text in texts))
        self.assertEqual(view['tech'], [{'text': 'd3: value pill longer than its line', 'count': 2}])
        self.assertTrue(all(listing.CJK.search(text) for text in texts))

    def test_a_scene_says_how_its_product_got_there_and_whether_it_was_checked(self):
        rows = [{'url': '/a.png', 'check': 'passed', 'placed': 'composite', 'role': 'place'},
                {'url': '/b.png', 'check': 'passed', 'placed': 'painted', 'restored': True, 'role': 'work'},
                {'url': '/c.png', 'check': 'unchecked', 'check_error': True, 'placed': 'painted', 'role': 'work'},
                {'url': '/d.png', 'check': 'failed', 'placed': 'painted', 'check_flags': ['scale_plausible'], 'check_issues': ['too big'], 'role': 'work'},
                {'url': '/e.png', 'check': 'unchecked', 'role': 'work'}]
        shown = [listing.scene_public(row, index) for index, row in enumerate(rows)]
        self.assertEqual([row['check'] for row in shown], ['passed', 'passed', 'pending', 'failed', 'unchecked'])
        self.assertEqual([row['locked'] for row in shown], [True, True, False, False, False])
        self.assertEqual([row['usable'] for row in shown], [True, True, False, False, True])
        self.assertEqual(shown[3]['flags'], ['大小比例不合理'])

    def test_a_page_revised_by_the_review_counts_as_reviewed(self):
        page = {'kind': 'T01', 'checks': {'passed': True, 'issues': []}, 'review': {'status': 'revised', 'score': 87, 'vetoes': ['wrong_pointer']}}
        self.assertTrue(listing.page_status(page)['ready'])
        shown = listing.page_public(page)
        self.assertEqual(shown['review']['veto_words'], ['引线指错位置']); self.assertTrue(shown['ready'])


if __name__ == '__main__':
    unittest.main()

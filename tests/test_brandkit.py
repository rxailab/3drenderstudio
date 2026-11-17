"""The brand-kit templates: the geometry and words handed to them, and real layouts in a headless browser.

The browser tests run when Chrome, Chromium or Edge is installed (``BRANDKIT_CHROME`` points at one
elsewhere); the rest needs nothing but Pillow.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))

import brandkit_render
import brandkit_set
import components
import listing
from test_listing import PARTS, StoreCase


def cut_out(path, size=(600, 400), box=(150, 120, 450, 280), angle=0):
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(40, 60, 200, 255))
    if angle:
        image = image.rotate(angle, expand=False)
    image.save(path)
    return path


class GeometryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_a_render_is_trimmed_to_the_product_and_laid_on_white_with_its_shape_measured(self):
        source = cut_out(self.folder / 'render.png')
        fields, crop = brandkit_set.flatten(source, self.folder / 'hero.png', pad=0)
        self.assertEqual((fields['width'], fields['height']), (301, 161))
        self.assertEqual([round(value, 2) for value in crop], [.25, .3, .75, .7])
        with Image.open(fields['path']) as picture:
            self.assertEqual((picture.mode, picture.getpixel((5, 5))), ('RGB', (40, 60, 200)))
        self.assertEqual(fields['content_bbox'], [0.0, 0.0, 1.0, 1.0])
        self.assertGreaterEqual(len(fields['hull']), 4)
        (x0, y0), (x1, y1) = fields['axis']
        self.assertAlmostEqual(y0, y1, delta=.02); self.assertLess(x0, .1); self.assertGreater(x1, .9)        # the long side runs across
        self.assertTrue(.2 < fields['half_width'] < .3, fields['half_width'])               # half of 161 px across 301 px
        for got in brandkit_set.remap([.5, .5], crop):
            self.assertAlmostEqual(got, .5, delta=.005)
        self.assertEqual(brandkit_set.remap([.25, .3], crop), [0.0, 0.0])

    def test_a_slanted_product_has_a_slanted_axis_and_a_dark_close_up_has_no_white(self):
        source = cut_out(self.folder / 'slant.png', size=(600, 600), box=(100, 270, 500, 330), angle=35)
        fields, _ = brandkit_set.flatten(source, self.folder / 'slant-flat.png')
        self.assertAlmostEqual(abs(fields['axis_deg']), 35, delta=3)
        dark, _ = brandkit_set.flatten(source, self.folder / 'dark.png', ground=(22, 24, 27), dark=True)
        with Image.open(dark['path']) as picture:
            self.assertLess(max(picture.getpixel((0, picture.height - 1))), 60)
        self.assertNotIn('hull', dark)

    def test_words_are_cut_whole_and_chinese_headlines_break_at_a_pause(self):
        self.assertEqual(brandkit_set._clip('Rolling Stack Toolbox', 18), 'Rolling Stack…')
        self.assertEqual(brandkit_set._clip('Stack it,\nroll it away', 40), 'Stack it,\nroll it away')
        self.assertEqual(brandkit_set._clip('叠放式带轮工具箱组合套装', 8), '叠放式带轮工具…')
        self.assertEqual(brandkit_set._headline('三层叠放，一次拉走', 'zh', 40), '三层叠放，\n一次拉走')
        self.assertEqual(brandkit_set._headline('单手切管', 'zh', 40), '单手切管')
        self.assertEqual(brandkit_set._headline('Stack it, roll it away', 'en', 40), 'Stack it, roll it away')

    def test_scene_settings_are_checked(self):
        self.assertEqual(brandkit_set.validate_scenes(None), brandkit_set.default_scenes())
        kept = brandkit_set.validate_scenes({'count': 2}, {'enabled': False, 'hint': ' a  bench '})
        self.assertEqual((kept['count'], kept['enabled'], kept['hint']), (2, False, 'a  bench'))
        for bad in ({'count': 0}, {'count': 2.5}, {'count': True}, {'enabled': 1}, {'people': 'crowd'}, {'audience': 'nobody'}, {'hint': 3}, 'x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                brandkit_set.validate_scenes(bad)

    def test_every_template_has_its_source_and_its_record(self):
        for kind in brandkit_set.KINDS:
            with self.subTest(kind=kind):
                self.assertTrue((brandkit_render.TEMPLATES / (kind + '.html')).is_file())
                info = brandkit_render.template_info(kind)
                self.assertEqual(info['template_id'], kind); self.assertIn('bg', info['tokens']['palette'])
        self.assertTrue(set(brandkit_set.AUTO) <= set(brandkit_set.KINDS))
        self.assertEqual(set(brandkit_set.KINDS) - set(brandkit_set.AUTO), set(brandkit_set.BLOCKED))


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class LayoutTests(StoreCase):
    """A whole set, laid out by the real templates in a headless browser, with Blender and the image model stood in for."""

    def setUp(self):
        super().setUp()
        mocked = patch.object(components, 'stage', side_effect=self.stage); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(brandkit_set, 'image_model_ready', return_value=False); mocked.start(); self.addCleanup(mocked.stop)
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'accent': '#d0612a'})

    def product(self, language):
        zh = language == 'zh'
        listing.save_product('fixture', {
            'title': '棘轮割管器' if zh else 'Ratchet Pipe Cutter', 'language': language, 'size_mm': 225,
            'headline': '单手操作，切口平整' if zh else 'One-hand clean cuts', 'subline': '切口方正，无毛刺。' if zh else 'Square, burr-free cuts.',
            'features': [{'title': '硬化刀轮' if zh else 'Hardened cutting wheel', 'detail': '可更换' if zh else 'Replaceable', 'parts': [PARTS[0]]},
                         {'title': '双导向滚轮' if zh else 'Twin guide rollers', 'parts': [PARTS[1]]}],
            'specs': [{'label': '管径范围' if zh else 'Pipe capacity', 'value': '6 - 22 mm'}], 'figures': [{'value': '22', 'unit': 'mm', 'label': '最大管径' if zh else 'Max pipe'}]})

    def test_every_page_is_laid_out_and_passes_its_checks(self):
        self.add_picture(name='Battery', quantity='2')
        for language, aspect in (('en', '1:1'), ('zh', '4:5')):
            with self.subTest(language=language, aspect=aspect):
                self.product(language); listing.save_product('fixture', {'aspect': aspect})
                pages, notes = listing.build('fixture')
                self.assertEqual([page['kind'] for page in pages], ['T12', 'T01', 'T03', 'T02', 'T04', 'T06', 'T05'], notes)
                for page in pages:
                    self.assertTrue(page['checks']['passed'], (page['kind'], page['checks'], page['notes']))
                    with Image.open(components.path_of('fixture', page['url'])) as image:
                        self.assertEqual(image.size, brandkit_set.CANVAS[aspect])
                        self.assertGreater(len(image.convert('L').getcolors(65536) or []), 20)        # a page, not a blank
        self.assertFalse(list((self.data / 'fixture' / 'listing').glob('build-*/_layout')))           # the browser's working files are gone

    def test_text_on_the_product_is_caught(self):
        out = self.data / 'probe.png'; hero = cut_out(self.data / 'hero-src.png', size=(800, 800), box=(0, 0, 800, 800))
        fields, _ = brandkit_set.flatten(hero, self.data / 'hero.png')
        job = {'template_id': 'T12', 'canvas': {'width': 1500, 'height': 1500}, 'lang': 'en', 'safe_margin': 84,
               'palette': brandkit_render.template_info('T12')['tokens']['palette'], 'mode': 'solid', 'hero': 'hero',
               'headline': 'A headline', 'assets': {'hero': fields}}
        qa = brandkit_render.render('T12', job, out)
        self.assertEqual(qa['render']['size'], [1500, 1500]); self.assertTrue(out.is_file())
        self.assertEqual(brandkit_render.layout_issues(qa), [])
        self.assertIn('pixel_checks', qa)


if __name__ == '__main__':
    unittest.main()

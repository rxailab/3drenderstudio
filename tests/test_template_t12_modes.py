"""T12's studio poster modes 'split' (a brand-colour block beside the light field, the product across the seam) and 'center'
(the product centred, the words under it or set huge above it), laid out by the real template in a headless browser.

Run when Chrome, Chromium or Edge is installed (``BRANDKIT_CHROME`` points at one elsewhere).
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brandkit_render
import listing_assets

CANVAS = {'1:1': (1500, 1500), '4:5': (1200, 1500)}
WORDS = {'en': {'headline': 'Insulated steel bottle', 'support': 'Keeps drinks cold for a full day', 'short': 'Steel Bottle'},
         'zh': {'headline': '保温钢瓶，\n冷饮一整天', 'support': '双层真空，冰饮长时间保冷。', 'short': '保温\n钢瓶'}}


def bottle(path):
    """A tall cut-out with a white highlight inside it (the backdrop key must not open it onto the colour block)."""
    image = Image.new('RGBA', (500, 900), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 220, 410, 860), radius=70, fill=(32, 70, 120, 255))
    draw.rectangle((190, 90, 310, 230), fill=(40, 40, 46, 255)); draw.rounded_rectangle((170, 40, 330, 110), radius=20, fill=(210, 90, 40, 255))
    draw.rectangle((150, 320, 180, 700), fill=(255, 255, 255, 255))
    image.save(path)
    return path


def drill(path):
    """A wide cut-out with a gap under its handle."""
    image = Image.new('RGBA', (1000, 640), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 60, 940, 300), radius=60, fill=(200, 60, 30, 255))
    draw.rectangle((520, 280, 680, 600), fill=(30, 30, 34, 255)); draw.rectangle((420, 520, 780, 620), fill=(60, 60, 66, 255))
    image.save(path)
    return path


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class T12ModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp())
        cls.heroes = {name: listing_assets.flatten(make(cls.folder / (name + '-src.png')), cls.folder / (name + '.png'))[0]
                      for name, make in (('bottle', bottle), ('drill', drill))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, True)

    def job(self, language, aspect, hero='bottle', **extra):
        width, height = CANVAS[aspect]; words = WORDS[language]
        return {'template_id': 'T12', 'variant': 'studio', 'canvas': {'width': width, 'height': height}, 'lang': language,
                'safe_margin': 72, 'min_margin': 60, 'palette': {**brandkit_render.template_info('T12')['tokens']['palette'], 'accent': '#d0612a'},
                'hero': 'hero', 'assets': {'hero': self.heroes[hero]}, 'headline': words['headline'], 'support': words['support'], **extra}

    def render(self, job, name):
        qa = brandkit_render.render('T12', job, self.folder / (name + '.png'), self.folder)
        self.assertEqual(brandkit_render.layout_issues(qa), [], (name, qa['issues'], qa.get('notes')))
        self.assertFalse([row for row in qa['issues'] if row['check'] in ('min_size_at_360', 'upscale', 'font_missing')], (name, qa['issues']))
        with Image.open(self.folder / (name + '.png')) as image:
            self.assertEqual(image.size, tuple(job['canvas'].values()))
        return qa

    @staticmethod
    def rows(qa):
        return {row['id']: row for row in qa['elements']}

    def test_a_split_poster_sets_every_word_on_the_colour_block_with_the_product_across_the_seam(self):
        for language in ('en', 'zh'):
            for aspect in ('1:1', '4:5'):
                for side, hero in (('left', 'bottle'), ('right', 'drill')):
                    with self.subTest(language=language, aspect=aspect, side=side):
                        qa = self.render(self.job(language, aspect, hero, mode='split', split_side=side), 'split-%s-%s-%s' % (language, aspect[0], side))
                        rows = self.rows(qa); block = rows['block']; layout = qa['layout']
                        width = qa['canvas'][0]
                        self.assertEqual((layout['mode'], layout['split']['side']), ('split', side))
                        self.assertAlmostEqual(block['w'], round(width * .46), delta=1)
                        self.assertEqual(block['x'], 0 if side == 'left' else width - block['w'])
                        words = [row for row in qa['elements'] if row['kind'] == 'text' and not row['exempt']]
                        self.assertTrue({'headline', 'support'} <= {row['id'] for row in words})
                        for row in words:
                            self.assertTrue(block['x'] <= row['x'] and row['x'] + row['w'] <= block['x'] + block['w'], row)
                        # the product overlaps both fields
                        x, _, w, _ = layout['hero_bbox']; seam = layout['split']['seam']
                        self.assertTrue(x + .1 * w < seam < x + .9 * w, (x, w, seam))
                        self.assertNotEqual(rows['hero']['blend'], 'multiply')        # keyed, not dyed by the block

    def test_the_split_ratio_is_kept_within_its_range(self):
        qa = self.render(self.job('en', '1:1', mode='split', split_ratio=.9), 'split-ratio')
        self.assertEqual(qa['layout']['split']['ratio'], .55)
        self.assertEqual(self.rows(qa)['block']['w'], 825)

    def test_a_centred_poster_sets_the_words_under_the_product(self):
        for language in ('en', 'zh'):
            for aspect in ('1:1', '4:5'):
                with self.subTest(language=language, aspect=aspect):
                    qa = self.render(self.job(language, aspect, 'drill', mode='center', figures=[{'value': '18', 'unit': 'V', 'label': 'Battery' if language == 'en' else '电池'}]),
                                     'below-%s-%s' % (language, aspect[0]))
                    rows = self.rows(qa); width = qa['canvas'][0]; x, y, w, h = qa['layout']['hero_bbox']
                    self.assertEqual(qa['layout']['center_type'], 'below')
                    self.assertAlmostEqual(x + w / 2, width / 2, delta=2)
                    for name in ('headline', 'support', 'chip1'):
                        self.assertGreater(rows[name]['y'], y + h, name)
                        self.assertAlmostEqual(rows[name]['x'] + rows[name]['w'] / 2, width / 2, delta=4)

    def test_a_short_headline_is_set_huge_behind_the_product_and_still_checked_as_words(self):
        for language in ('en', 'zh'):
            for aspect in ('1:1', '4:5'):
                with self.subTest(language=language, aspect=aspect):
                    qa = self.render(self.job(language, aspect, mode='center', center_type='behind', headline=WORDS[language]['short']),
                                     'behind-%s-%s' % (language, aspect[0]))
                    rows = self.rows(qa); hl = rows['headline']; x, y, w, h = qa['layout']['hero_bbox']
                    self.assertEqual(qa['layout']['center_type'], 'behind')
                    self.assertEqual((hl['kind'], hl['exempt'], hl['tier']), ('text', False, 'headline'))
                    self.assertGreaterEqual(hl['font_size'], 150)
                    self.assertLess(hl['y'], y)                                   # the words above, the product in front of their foot
                    self.assertGreater(rows['support']['y'], y + h)               # the support line small at the bottom
                    self.assertLess(rows['support']['font_size'], 48)
                    # painted before the product: the product stands in front of the words
                    order = [row['id'] for row in qa['elements']]
                    self.assertLess(order.index('headline'), order.index('hero'))

    def test_a_long_headline_cannot_be_set_huge_and_goes_under_the_product(self):
        qa = self.render(self.job('en', '1:1', mode='center', center_type='behind'), 'behind-long')
        self.assertEqual(qa['layout']['center_type'], 'below')
        self.assertTrue(any('one/two-word' in note for note in qa['notes']), qa['notes'])

    def test_a_job_without_the_new_modes_renders_exactly_as_before(self):
        try:
            old = subprocess.run(['git', 'show', '5e4979d:brandkit/templates/T12.html'], cwd=ROOT, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('the template before the new modes is not in this checkout')
        before = self.folder / 'before'; before.mkdir(exist_ok=True); (before / 'T12.html').write_bytes(old)
        for language, aspect, extra in (('en', '1:1', {'mode': 'solid'}), ('zh', '4:5', {'compact': True})):
            job = self.job(language, aspect, figures=[{'value': '750', 'unit': 'ml', 'label': 'Volume' if language == 'en' else '容量'}], **extra)
            now = brandkit_render.render('T12', job, self.folder / 'now.png', self.folder)
            with patch.object(brandkit_render, 'TEMPLATES', before):
                then = brandkit_render.render('T12', job, self.folder / 'then.png', self.folder)
            for qa in (now, then):
                qa.pop('render')
            self.assertEqual(now, then)
            with Image.open(self.folder / 'now.png') as a, Image.open(self.folder / 'then.png') as b:
                self.assertIsNone(ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox())


if __name__ == '__main__':
    unittest.main()

"""The editable SVG export of the brand-kit pages: drawn back in the browser it must look like the rendered page, its
text must be real text, its pictures embedded, every element a named layer, and its fonts shipped beside it.

Runs when Chrome, Chromium or Edge is installed (``BRANDKIT_CHROME`` points at one elsewhere). The jobs come from a
whole set built the way tests/test_brandkit.py builds one (Blender and the image model stood in for), in English and
Chinese. ``scripts/svg_fidelity.py`` runs the same comparison on saved real jobs.
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests')); sys.path.insert(0, str(ROOT / 'scripts'))

import brandkit_render

SVG = '{http://www.w3.org/2000/svg}'
MEAN_LIMIT = 6.0          # mean absolute difference, 0-255
OFF_LIMIT = 0.03          # share of pixels off by more than 40


def svg_text(root):
    """Every <text> element's content, its tspans joined."""
    return [''.join(node.itertext()) for node in root.iter(SVG + 'text')]


def squash(value):
    """Text without the job's marks, case or spacing, to find a headline across its rendered lines."""
    value = re.sub(r'\[\[([^\]]+)\]\]', r'\1', str(value)); value = re.sub(r'\{([^}]+)\}', r'\1', value)
    return re.sub(r'\s+', '', value).upper()


class ExporterUnitTests(unittest.TestCase):
    def test_the_exporter_is_only_added_when_exporting(self):
        job = {'template_id': 'T12', 'canvas': {'width': 600, 'height': 600}, 'lang': 'en', 'assets': {}}
        with patch('brandkit_assets.ensure_fonts', return_value={'present': [], 'missing': []}):
            page = brandkit_render.compose('T12', job)
        self.assertNotIn('svg_export.js', page)
        self.assertIn(brandkit_render.RUNTIME_TAG, page)

    def test_links_left_by_the_page_are_embedded_from_disk(self):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder)
        picture = folder / 'a b.png'; picture.write_bytes(b'\x89PNG\r\n\x1a\nfake')
        svg = brandkit_render._embed_links('<image xlink:href="%s"/><image xlink:href="file:///nowhere/x.png"/>' % picture.as_uri())
        self.assertIn('xlink:href="data:image/png;base64,', svg)
        self.assertIn('file:///nowhere/x.png', svg)          # a missing file stays a link


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: SVG export not run')
class SvgExportTests(unittest.TestCase):
    """A whole set built in each language, every page exported and compared with its PNG."""

    @classmethod
    def setUpClass(cls):
        import test_brandkit
        cls.tb = test_brandkit

    def build(self, language, aspect):
        """The pages of a set and the job each was laid out from (the first version written beside it)."""
        tb = self.tb

        class Builder(tb.StoreCase):
            def runTest(self):
                pass
        case = Builder(); case.setUp(); self.addCleanup(case.doCleanups)
        for target, name, value in ((tb.components, 'stage', case.stage), (tb.brandkit_set, 'image_model_ready', lambda *a, **k: False)):
            mocked = patch.object(target, name, side_effect=value); mocked.start(); self.addCleanup(mocked.stop)
        tb.listing.save_brand('fixture', {'name': 'NORTHFORGE', 'accent': '#d0612a'})
        case.add_picture(name='Battery', quantity='2')
        tb.LayoutTests.product(case, language); tb.listing.save_product('fixture', {'aspect': aspect})
        pages, notes = tb.listing.build('fixture')
        out = []
        for page in pages:
            png = Path(tb.components.path_of('fixture', page['url']))
            versions = json.loads((png.parent / 'jobs' / (page['kind'] + '.json')).read_text(encoding='utf-8'))
            out.append((page['kind'], versions[0]))
        return out, case.data

    def check_set(self, language, aspect):
        import svg_fidelity
        jobs, data = self.build(language, aspect)
        self.assertGreaterEqual(len(jobs), 5)
        for kind, job in jobs:
            with self.subTest(language=language, page=kind):
                out = data / 'svg' / language
                row = svg_fidelity.check(kind, job, out, kind)
                self.assertLess(row['mean'], MEAN_LIMIT, row); self.assertLess(row['off40'], OFF_LIMIT, row)
                svg_path = out / (kind + '.svg'); source = svg_path.read_text(encoding='utf-8')
                root = ET.fromstring(source.encode('utf-8'))            # well-formed XML
                self.assertEqual((root.get('width'), root.get('height')), (str(job['canvas']['width']), str(job['canvas']['height'])))
                # real text: every line of copy on the page, the headline first, is in the <text> content
                texts = squash(''.join(svg_text(root))); self.assertTrue(texts)
                shown = [row for row in row['qa']['elements'] if row.get('kind') == 'text' and str(row.get('text', '')).strip()]
                self.assertTrue(any(row.get('tier') == 'headline' for row in shown), kind)
                for text_row in shown:
                    self.assertIn(squash(text_row['text']), texts, text_row['id'])
                # pictures are embedded, never linked
                images = list(root.iter(SVG + 'image'))
                self.assertTrue(images)
                for image in images:
                    self.assertTrue(image.get('{http://www.w3.org/1999/xlink}href', '').startswith('data:image/'))
                # named layers, the QA ids among them
                names = {node.get('id') for node in root.iter(SVG + 'g') if node.get('data-name')}
                qa_ids = {element['id'] for element in row['qa']['elements']}
                self.assertGreater(len(names & qa_ids), len(qa_ids) // 2, sorted(qa_ids - names))
                # the fonts the SVG declares are shipped beside it
                for name in set(re.findall(r"url\('fonts/([^']+)'\)", source)):
                    self.assertTrue((out / 'fonts' / name).is_file(), name)
                self.assertEqual("font-family:'Noto Sans SC'" in source, language == 'zh')

    def test_an_english_set_exports_like_its_pages(self):
        self.check_set('en', '1:1')

    def test_a_chinese_set_exports_like_its_pages(self):
        self.check_set('zh', '4:5')



@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: SVG export not run')
class KeyedHeroExportTests(unittest.TestCase):
    """T12 'split' keys the product picture's white backdrop out over the colour block (a CSS mask from a picture): the
    SVG must carry that mask, or the backdrop comes back as a white rectangle over the block."""

    def test_a_split_poster_exports_its_keyed_product_without_the_white_backdrop(self):
        import listing_assets
        import svg_fidelity
        from test_template_t12_modes import bottle
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        hero = listing_assets.flatten(bottle(folder / 'bottle-src.png'), folder / 'bottle.png')[0]
        job = {'template_id': 'T12', 'variant': 'studio', 'canvas': {'width': 1500, 'height': 1500}, 'lang': 'en',
               'safe_margin': 72, 'min_margin': 60, 'palette': {**brandkit_render.template_info('T12')['tokens']['palette'], 'accent': '#d0612a'},
               'hero': 'hero', 'assets': {'hero': hero}, 'headline': 'Insulated steel bottle', 'support': 'Keeps drinks cold',
               'mode': 'split', 'split_side': 'left'}
        row = svg_fidelity.check('T12', job, folder, 'split')
        qa = row['qa']; rows = {element['id']: element for element in qa['elements']}
        block = tuple(int(v) for v in re.findall(r'\d+', qa['layout']['block_color']))
        seam = qa['layout']['split']['seam']; image = rows['hero']; cx, cy, cw, ch = qa['layout']['hero_bbox']
        # the picture's backdrop on the block side: inside the picture's box, left of the seam, clear of the product
        spots = [(x, y) for x in range(int(image['x']) + 6, seam - 4, 8) for y in range(int(image['y']) + 6, int(image['y'] + image['h']) - 6, 24)
                 if not (cx - 12 <= x <= cx + cw + 12 and cy - 12 <= y <= cy + ch + 40)]
        self.assertGreater(len(spots), 20)
        source = (folder / 'split.svg').read_text(encoding='utf-8')
        self.assertIn('mask-type="alpha"', source)
        with Image.open(folder / 'split.png') as page, Image.open(folder / 'split.svg.png') as drawn:
            page, drawn = page.convert('RGB'), drawn.convert('RGB')
            for name, picture in (('page', page), ('svg', drawn)):
                off = [(spot, picture.getpixel(spot)) for spot in spots
                       if max(abs(a - b) for a, b in zip(picture.getpixel(spot), block)) > 12]
                self.assertFalse(off, (name, block, off[:5]))
        self.assertLess(row['mean'], MEAN_LIMIT, row); self.assertLess(row['off40'], OFF_LIMIT, row)


if __name__ == '__main__':
    unittest.main()

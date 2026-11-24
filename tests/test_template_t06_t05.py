"""The dimensions page's drafting look (T06 ground_style 'blueprint', title_block) and the kit chart's light header (T05 header).

Each knob is off by default and a job without it renders exactly as before; with it the page is laid out by the real
template in a headless browser and must pass the template's own checks, in English 1:1 and Chinese 4:5.
"""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brandkit_render
import brandkit_set
import listing_jobs

TOOLBOX = [400.0, 194.0, 190.0]            # standing: width, depth, height in mm
SPECS = [{'label': 'Weight', 'value': '6.4 kg'}, {'label': 'Material', 'value': 'Cold-rolled steel'},
         {'label': 'Load', 'value': '30 kg'}, {'label': 'Finish', 'value': 'Powder coat'}]
SPECS_ZH = [{'label': '重量', 'value': '6.4 kg'}, {'label': '材质', 'value': '冷轧钢'}, {'label': '承重', 'value': '30 kg'}]


def cut_out(folder, name, size, shapes, colour=(200, 40, 30, 255)):
    """A render with a transparent ground (rectangles in one colour), flattened as the set builder does."""
    image = Image.new('RGBA', size, (0, 0, 0, 0)); pen = ImageDraw.Draw(image)
    for box in shapes:
        pen.rectangle(box, fill=colour)
    image.save(folder / (name + '.src.png'))
    fields, _ = brandkit_set.flatten(folder / (name + '.src.png'), folder / (name + '.png'), pad=0)
    return fields


def issues(qa):
    return brandkit_render.layout_issues(qa)


def element(qa, name):
    return next((row for row in qa['elements'] if row['id'] == name), None)


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class TemplateKnobTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)

    def render(self, kind, job, name):
        qa = brandkit_render.render(kind, job, self.folder / (name + '.png'), work=self.folder)
        with Image.open(self.folder / (name + '.png')) as image:
            return qa, image.convert('RGB')

    def dimension_job(self, language='en', aspect='1:1', **knobs):
        # a toolbox face on (a C shape: its open side would vanish under a convex hull) and turned a quarter
        front = cut_out(self.folder, 'side', (1500, 760), [(0, 0, 1499, 160), (0, 0, 300, 759), (0, 600, 1499, 759)])
        end = cut_out(self.folder, 'end', (800, 800), [(0, 0, 799, 783)])
        zh = language == 'zh'
        item = {'title': '工具箱' if zh else 'Toolbox', 'size_mm': 400, 'headline': '', 'subline': '', 'features': [], 'figures': [],
                'specs': [{'label': '长度' if zh else 'Length', 'value': '40 cm'}, {'label': '宽度' if zh else 'Width', 'value': '19 cm'},
                          {'label': '高度' if zh else 'Height', 'value': '19 cm'}]}
        rows, _ = brandkit_set.stated_dimensions(item, TOOLBOX, False, None, 'mm')
        ctx = {'product': item, 'language': language, 'aspect': aspect, 'brand': None, 'assets': {'side': front, 'end': end},
               'unit': 'mm', 'dims_mm': TOOLBOX, 'flat': False, 'stated': rows, 'scenes': [], 'parts': [], 'poses': []}
        job = brandkit_set.jobs_for('T06', ctx)[0]
        job['specs'] = [dict(row) for row in (SPECS_ZH if zh else SPECS)]
        return {**job, **knobs}

    def kit_job(self, language='en', aspect='1:1', count=4, **knobs):
        shapes = [[(0, 0, 599, 399)], [(0, 0, 299, 599)], [(0, 100, 599, 299), (250, 0, 349, 399)], [(0, 0, 499, 499)],
                  [(0, 0, 699, 199)], [(0, 0, 399, 599)]]
        assets = {'a%d' % i: cut_out(self.folder, 'item%d' % i, (720, 640), shapes[i], (40 + 30 * i, 60, 200 - 20 * i, 255)) for i in range(count)}
        names = ['扳手', '螺丝刀', '钳子', '收纳盒', '卷尺', '水平尺'] if language == 'zh' else ['Wrench', 'Screwdriver', 'Pliers', 'Storage case', 'Tape', 'Level']
        ctx = {'aspect': aspect, 'language': language, 'brand': None}
        return listing_jobs._job('T05', ctx, headline='包装清单' if language == 'zh' else "What's in the box",
                                 items=[{'asset': 'a%d' % i, 'name': names[i]} for i in range(count)],
                                 note='配件以实物为准。' if language == 'zh' else 'Accessories may vary.', assets=assets, **knobs)

    def test_the_new_knobs_at_their_defaults_change_nothing(self):
        plain, plain_png = self.render('T06', self.dimension_job(), 'plain')
        same, same_png = self.render('T06', self.dimension_job(ground_style='plain', title_block=False), 'same')
        self.assertEqual(list(plain_png.getdata()), list(same_png.getdata()))
        self.assertNotIn('grid', plain['layout']); self.assertIsNone(element(plain, 'title_block'))
        band, band_png = self.render('T05', self.kit_job(), 'band')
        named, named_png = self.render('T05', self.kit_job(header='band'), 'named')
        self.assertEqual(list(band_png.getdata()), list(named_png.getdata()))
        self.assertIsNotNone(element(band, 'band'))

    def test_the_blueprint_grid_lies_under_the_drawing_only_and_off_the_product(self):
        for language, aspect in (('en', '1:1'), ('zh', '4:5')):
            with self.subTest(language=language, aspect=aspect):
                plain, plain_png = self.render('T06', self.dimension_job(language, aspect), 'plain-' + language)
                qa, png = self.render('T06', self.dimension_job(language, aspect, ground_style='blueprint'), 'bp-' + language)
                self.assertFalse(issues(qa), qa['issues'])
                grid = qa['layout']['grid']; width, height = png.size
                headline, disclaimer = element(qa, 'headline'), element(qa, 'disclaimer')
                # the grid starts under the header and stops above the footer: those strips are untouched
                self.assertGreater(grid['y'], headline['y'] + headline['h'])
                self.assertLess(grid['y'] + grid['h'], disclaimer['y'])
                top, bottom = (0, 0, width, int(grid['y']) - 1), (0, int(grid['y'] + grid['h']) + 1, width, height)
                for box in (top, bottom):
                    self.assertEqual(list(plain_png.crop(box).getdata()), list(png.crop(box).getdata()))
                # lines on the sheet beside the product, none across the product's own pixels
                self.assertNotEqual(list(plain_png.crop((int(grid['x']), int(grid['y']), int(grid['x']) + 200, int(grid['y']) + 200)).getdata()),
                                    list(png.crop((int(grid['x']), int(grid['y']), int(grid['x']) + 200, int(grid['y']) + 200)).getdata()))
                x, y, w, h = element(qa, 'hero')['layout']
                solid = (int(x + w * .1), int(y + h * .3), int(x + w * .18), int(y + h * .7))           # the C's back
                self.assertEqual(list(plain_png.crop(solid).getdata()), list(png.crop(solid).getdata()))
                gap = (int(x + w * .45), int(y + h * .4), int(x + w * .75), int(y + h * .6))            # its open middle
                self.assertNotEqual(list(plain_png.crop(gap).getdata()), list(png.crop(gap).getdata()))
                self.assertEqual(qa['layout']['ground_style'], 'blueprint')

    def test_the_blueprint_reads_on_a_dark_ground_too(self):
        job = self.dimension_job(ground_style='blueprint')
        job['palette'] = {**job['palette'], 'bg': '#1F2428', 'text': '#EEF0F2', 'muted': '#A7AFB6'}
        qa, png = self.render('T06', job, 'dark')
        self.assertFalse(issues(qa), qa['issues'])
        grid = qa['layout']['grid']
        corner = png.crop((int(grid['x']), int(grid['y'] + grid['h'] / 2), int(grid['x']) + 120, int(grid['y'] + grid['h'] / 2) + 120))
        self.assertGreater(max(sum(p) for p in corner.getdata()) - min(sum(p) for p in corner.getdata()), 15)   # light lines on the dark

    def test_the_title_block_holds_three_specs_where_it_is_free_or_is_left_out_with_a_note(self):
        for language, aspect in (('en', '1:1'), ('zh', '4:5')):
            with self.subTest(language=language, aspect=aspect):
                qa, _ = self.render('T06', self.dimension_job(language, aspect, ground_style='blueprint', title_block=True), 'tb-' + language)
                self.assertFalse(issues(qa), qa['issues'])
                block = element(qa, 'title_block'); width, height = qa['canvas']
                self.assertIsNotNone(block, qa['notes'])
                self.assertEqual([row['id'] for row in qa['elements'] if row['id'].startswith('tb') and row['id'].endswith('_name')],
                                 ['tb1_name', 'tb2_name', 'tb3_name'])                          # three of the four specs
                self.assertGreater(block['x'] + block['w'], width * .7); self.assertGreater(block['y'], height * .5)
                for row in qa['elements']:
                    if row['id'] in ('hero', 'end_view', 'disclaimer') or row['id'].endswith('_pill'):
                        self.assertTrue(block['x'] + block['w'] <= row['x'] or row['x'] + row['w'] <= block['x'] or
                                        block['y'] + block['h'] <= row['y'] or row['y'] + row['h'] <= block['y'], row['id'])
        # specs the table already lists are not repeated; with nothing left the block is named as left out
        job = self.dimension_job(mode='table', title_block=True)
        job['specs'] = [{'name': 'Weight', 'value': 6.4, 'unit': 'kg'}]
        qa, _ = self.render('T06', job, 'table')
        self.assertIsNone(element(qa, 'title_block')); self.assertTrue(any('title block' in note for note in qa['notes']), qa['notes'])
        # a product filling the page leaves no room
        big = cut_out(self.folder, 'big', (1000, 1000), [(0, 0, 999, 999)])
        job = self.dimension_job(title_block=True)
        job.update(end=None, assets={'side': big}, dimensions=job['dimensions'][:1])
        qa, _ = self.render('T06', job, 'full')
        self.assertFalse(issues(qa), qa['issues'])
        if element(qa, 'title_block') is None:
            self.assertTrue(any('title block' in note for note in qa['notes']), qa['notes'])

    def test_a_light_header_drops_the_dark_band_and_the_rows_grow_into_its_room(self):
        for language, aspect, mode in (('en', '1:1', None), ('zh', '4:5', None), ('en', '1:1', 'knolling')):
            with self.subTest(language=language, aspect=aspect, mode=mode):
                extra = {'mode': mode, 'count': 6} if mode else {}
                band, band_png = self.render('T05', self.kit_job(language, aspect, **extra), 'band')
                light, light_png = self.render('T05', self.kit_job(language, aspect, header='light', **extra), 'light')
                self.assertFalse(issues(light), light['issues'])
                self.assertIsNone(element(light, 'band'))
                self.assertGreater(sum(light_png.getpixel((20, 20))), 600); self.assertLess(sum(band_png.getpixel((20, 20))), 200)
                title, rule = element(light, 'title'), element(light, 'rule')
                self.assertLess(sum(int(v) for v in title['color'][4:-1].split(',')[:3]), 200)            # ink on the page ground
                self.assertGreater(rule['y'], title['y'] + title['h']); self.assertLess(rule['w'], 200)       # a short accent rule under it
                if not mode:
                    self.assertLess(element(light, 'item1')['y'], element(band, 'item1')['y'])               # the rows start higher
                self.assertEqual(light['layout']['header'], 'light')

    def test_a_value_badge_sits_beside_the_light_title(self):
        badge = {'approved': True, 'text': '$129 value'}
        qa, _ = self.render('T05', self.kit_job(header='light', value_claim=badge), 'value')
        self.assertFalse(issues(qa), qa['issues'])
        title, vb, item = element(qa, 'title'), element(qa, 'vb'), element(qa, 'item1')
        self.assertGreater(vb['x'], title['x'] + title['w']); self.assertGreater(item['y'], vb['y'] + vb['h'])


if __name__ == '__main__':
    unittest.main()

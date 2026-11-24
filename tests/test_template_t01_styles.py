"""T01's callout styles (job.callout_style): numbered discs on the parts with a legend column, and one stacked column of
labels with level leaders, on either side (job.label_side), laid out by the real template in a headless browser.

Runs when Chrome, Chromium or Edge is installed (``BRANDKIT_CHROME`` points at one elsewhere). Set ``T01_SHOTS`` to a
folder to keep the pages for a look.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brandkit_render
import listing_assets

CANVAS = {'1:1': (1500, 1500), '4:5': (1200, 1500)}
# a cordless drill drawn on a transparent ground: its parts in source pixels, where the callouts point
SOURCE = (1100, 960)
PARTS = {'chuck': (905, 290), 'trigger': (548, 470), 'switch': (430, 250), 'battery': (450, 760), 'grip': (440, 590)}
FEATURES = {
    'en': [('chuck', 'Keyless chuck', 'Bit changes by hand'), ('trigger', 'Variable trigger', 'Speed follows your finger'),
           ('switch', '2-speed gearbox', 'Low for driving, high for drilling'), ('battery', 'Slide-on battery', 'Fits the whole range')],
    'zh': [('chuck', '免钥匙夹头', '徒手换钻头'), ('trigger', '无级调速扳机', '轻按慢转，深按快转'),
           ('switch', '两档变速', '低档拧螺丝，高档钻孔'), ('battery', '滑入式电池', '全系通用')],
}
HEADLINE = {'en': 'Built to\nwork hard', 'zh': '每个部件\n一目了然'}
EYEBROW = {'en': 'Part overview', 'zh': '部位导览'}


def drill(path):
    image = Image.new('RGBA', SOURCE, (0, 0, 0, 0)); pen = ImageDraw.Draw(image)
    dark, mid, steel = (46, 49, 55, 255), (70, 74, 82, 255), (176, 179, 186, 255)
    pen.rounded_rectangle((160, 190, 780, 400), 60, fill=dark)                        # motor housing
    for x in range(250, 380, 26):
        pen.rectangle((x, 230, x + 12, 360), fill=mid)                                 # vents
    pen.rectangle((405, 225, 455, 275), fill=(200, 70, 40, 255))                       # speed switch
    pen.polygon([(780, 230), (900, 250), (900, 330), (780, 360)], fill=steel)          # chuck
    pen.rectangle((900, 280, 1040, 300), fill=(120, 122, 128, 255))                    # bit
    pen.polygon([(360, 400), (540, 400), (510, 700), (340, 700)], fill=dark)           # grip
    pen.rounded_rectangle((530, 430, 575, 515), 14, fill=(200, 70, 40, 255))           # trigger
    pen.rounded_rectangle((250, 700, 640, 850), 22, fill=(200, 70, 40, 255))           # battery
    pen.rectangle((270, 720, 620, 735), fill=(150, 50, 28, 255))
    image.save(path)
    return path


def hero_asset(folder):
    fields, crop = listing_assets.flatten(drill(folder / 'drill-src.png'), folder / 'drill.png')
    def anchor(name):
        x, y = PARTS[name]
        return [round((x / SOURCE[0] - crop[0]) / (crop[2] - crop[0]), 4), round((y / SOURCE[1] - crop[1]) / (crop[3] - crop[1]), 4)]
    return fields, anchor


def job_for(folder, language='en', aspect='1:1', count=4, frame=None, **knobs):
    fields, anchor = hero_asset(folder)
    width, height = CANVAS[aspect]
    tokens = brandkit_render.template_info('T01')['tokens']
    features = [{'title': title, 'body': body, 'anchor': anchor(part)} for part, title, body in FEATURES[language][:count]]
    job = {'template_id': 'T01', 'variant': 'studio', 'canvas': {'width': width, 'height': height}, 'lang': language,
           'safe_margin': tokens['safe_margin'], 'min_margin': tokens['min_margin'], 'palette': dict(tokens['palette']),
           'mode': 'band', 'hero': 'hero', 'eyebrow': EYEBROW[language], 'headline': HEADLINE[language],
           'features': features, 'assets': {'hero': fields}, **knobs}
    if frame:
        job['frame'] = frame
    return job


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class CalloutStyleTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.folder, True)
        self.shots = Path(os.environ['T01_SHOTS']) if os.environ.get('T01_SHOTS') else None

    def render(self, name, job):
        out = (self.shots or self.folder) / ('T01-%s.png' % name)
        return brandkit_render.render('T01', job, out, work=self.folder)

    def boxes(self, qa, prefix):
        return {row['id']: row for row in qa['elements'] if row['id'].startswith(prefix)}

    def test_numbered_and_column_callouts_lay_out_cleanly_on_either_side_in_both_languages_and_shapes(self):
        for style in ('numbered', 'column'):
            for side in ('right', 'left'):
                for language, aspect in (('en', '1:1'), ('zh', '4:5'), ('en', '4:5'), ('zh', '1:1')):
                    for count in (4, 2):
                        name = '%s-%s-%s-%s-%d' % (style, side, language, aspect.replace(':', 'x'), count)
                        with self.subTest(name):
                            qa = self.render(name, job_for(self.folder, language, aspect, count, callout_style=style, label_side=side))
                            self.assertEqual(brandkit_render.layout_issues(qa), [], (qa['issues'], qa['notes']))
                            self.assertEqual((qa['layout']['callout_style'], qa['layout']['label_side']), (style, side))
                            titles = [row for row in qa['elements'] if row['id'].endswith('_title')]
                            self.assertEqual(len(titles), count)
                            # one column: every title starts at the same x, on label_side of the product
                            self.assertEqual(len({row['x'] for row in titles}), 1, titles)
                            hero = qa['layout']['hero_content_quad']
                            if side == 'right':
                                self.assertGreater(titles[0]['x'], max(p[0] for p in hero))
                            else:
                                self.assertLess(titles[0]['x'] + max(row['w'] for row in titles), min(p[0] for p in hero))
                            # stacked top to bottom with even gaps
                            stack = sorted(titles, key=lambda row: row['y'])
                            if style == 'numbered':
                                self.assertEqual(qa['leaders'], [])
                                marks = self.boxes(qa, 'mk')
                                self.assertEqual(len(marks), count)
                                # read top to bottom, the legend is numbered 1, 2, 3...
                                keys = sorted(self.boxes(qa, 'lbl').items(), key=lambda kv: kv[1]['y'])
                                self.assertEqual([kv[0] for kv in keys if kv[0].endswith('_no')], [row['id'].replace('_title', '_no') for row in stack])
                            else:
                                self.assertEqual(len(qa['leaders']), count)
                                # ordered by the parts' height: label k's leader reaches the k-th highest part. With the
                                # column on the left the chuck (far right, near the top) is lifted over the others instead
                                # of its line crossing the whole drill.
                                ends = {row['label']: row['pts'][-1][1] for row in qa['leaders']}
                                heights = [ends[row['id'].replace('_title', '')] for row in stack]
                                if side == 'right' or count == 2:
                                    self.assertEqual(heights, sorted(heights))
                                for row in qa['leaders']:
                                    self.assertLessEqual(len(row['pts']), 3, row)      # straight or one bend

    def test_the_numbered_legend_reads_in_the_order_of_the_parts_from_the_top(self):
        qa = self.render('order', job_for(self.folder, callout_style='numbered'))
        marks = sorted(self.boxes(qa, 'mk').values(), key=lambda row: row['y'])
        self.assertEqual([row['id'] for row in marks], ['mk3', 'mk1', 'mk2', 'mk4'])     # switch, chuck, trigger, battery

    def test_a_job_without_a_callout_style_lays_out_as_before(self):
        plain = self.render('leaders', job_for(self.folder))
        named = self.render('leaders-named', job_for(self.folder, callout_style='leaders', label_side='left'))
        self.assertNotIn('callout_style', plain['layout'])
        strip = lambda qa: [{k: v for k, v in row.items() if k not in ('text',)} for row in qa['elements']]
        self.assertEqual(strip(plain), strip(named))
        self.assertEqual(plain['leaders'], named['leaders'])
        self.assertFalse(self.boxes(plain, 'mk')); self.assertFalse([row for row in plain['elements'] if row['id'].endswith(('_bar', '_no'))])

    def test_parts_too_close_for_numbered_discs_fail_the_checks_and_say_why(self):
        job = job_for(self.folder, callout_style='numbered', count=2)
        job['features'][1]['anchor'] = [job['features'][0]['anchor'][0] - 0.01, job['features'][0]['anchor'][1]]
        qa = self.render('crowded', job)
        self.assertTrue(any('crowded' in note for note in qa['notes']), qa['notes'])
        self.assertIn('overlap', [row['check'] for row in brandkit_render.layout_issues(qa)])


if __name__ == '__main__':
    unittest.main()

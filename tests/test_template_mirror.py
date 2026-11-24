"""The mirrored layouts of the close-up pages: T03 split with the rail on the right, T02 rail with the copy panel on the right.

Real pages from a built set, laid out again with the knob in a headless browser (skipped without Chrome/Chromium/Edge)."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))

import brandkit_render
import brandkit_set
import components
import listing
from test_listing import PARTS, StoreCase


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class MirrorTests(StoreCase):
    def setUp(self):
        super().setUp()
        mocked = patch.object(components, 'stage', side_effect=self.stage); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(brandkit_set, 'image_model_ready', return_value=False); mocked.start(); self.addCleanup(mocked.stop)
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'accent': '#d0612a'})

    def jobs(self, language, aspect):
        """The T03 job a real build chose and its T02 job with two close-ups (the rail layout), in this language and shape."""
        zh = language == 'zh'
        listing.save_product('fixture', {
            'title': '棘轮割管器' if zh else 'Ratchet Pipe Cutter', 'language': language, 'size_mm': 225, 'aspect': aspect,
            'headline': '单手操作，切口平整' if zh else 'One-hand clean cuts', 'subline': '切口方正，无毛刺。' if zh else 'Square, burr-free cuts.',
            'features': [{'title': '硬化刀轮' if zh else 'Hardened cutting wheel', 'detail': '可更换' if zh else 'Replaceable', 'parts': [PARTS[0]]},
                         {'title': '双导向滚轮' if zh else 'Twin guide rollers', 'detail': '夹紧管身' if zh else 'Hold the pipe square', 'parts': [PARTS[1]]}]})
        pages, notes = listing.build('fixture')
        build = max((self.data / 'fixture' / 'listing').glob('build-*'), key=lambda path: path.stat().st_mtime)
        read = lambda kind: json.loads((build / 'jobs' / (kind + '.json')).read_text())
        return {'T03': read('T03')[-1], 'T02': next(job for job in read('T02') if len(job['features']) == 2)}

    def lay_out(self, kind, job, name, **knobs):
        out = self.data / ('%s-%s.png' % (kind, name))
        qa = brandkit_render.render(kind, {**job, **knobs}, out, work=self.data)
        self.assertFalse(brandkit_render.layout_issues(qa), (kind, name, qa['issues'], qa['notes']))
        return qa, {row['id']: row for row in qa['elements']}, out.read_bytes()

    def test_the_close_up_rail_moves_to_the_right_with_the_hero_and_headline_on_the_left(self):
        for language, aspect in (('en', '1:1'), ('zh', '4:5')):
            with self.subTest(language=language, aspect=aspect):
                job = self.jobs(language, aspect)['T03']
                self.assertEqual(job['mode'], 'split')
                W = job['canvas']['width']
                qa, el, png = self.lay_out('T03', job, language)
                _, _, left = self.lay_out('T03', job, language + '-left', rail_side='left')
                self.assertEqual(png, left)                                  # 'left' is the page drawn without the knob
                rail = el['rail_bg']
                self.assertEqual((rail['x'], qa['layout']['rail']['side']), (0, 'left'))
                self.assertGreater(el['headline']['x'], rail['w'])

                qa, el, _ = self.lay_out('T03', job, language + '-right', rail_side='right')
                rail, head, hero = el['rail_bg'], el['headline'], el['hero']
                self.assertEqual(qa['layout']['rail']['side'], 'right')
                self.assertAlmostEqual(rail['x'] + rail['w'], W, delta=1)          # flush to the right edge
                self.assertLessEqual(head['x'] + head['w'], rail['x'])
                self.assertLess(hero['x'] + hero['w'] / 2, rail['x'])
                for i in range(1, len(job['details']) + 1):
                    title, tile = el['c%d_title' % i], el['detail%d_frame' % i]
                    self.assertGreaterEqual(tile['x'], rail['x'] - 1)
                    self.assertGreaterEqual(title['x'], rail['x'])
                    self.assertLessEqual(title['x'] + title['w'], W - 60)
                self.assertGreaterEqual(el['gap1']['x'], rail['x'] - 1)
                self.assertAlmostEqual(el['rail_seam']['x'] + el['rail_seam']['w'], rail['x'], delta=1)   # the seam on the rail's inner edge

    def test_the_copy_panel_moves_to_the_right_with_the_macro_bleeding_off_the_left(self):
        for language, aspect in (('en', '1:1'), ('zh', '4:5')):
            with self.subTest(language=language, aspect=aspect):
                job = self.jobs(language, aspect)['T02']
                W = job['canvas']['width']
                qa, el, png = self.lay_out('T02', job, language)
                _, _, left = self.lay_out('T02', job, language + '-left', panel_side='left')
                self.assertEqual(png, left)
                self.assertEqual((qa['layout']['layout'], qa['layout']['panel_side']), ('rail', 'left'))
                self.assertGreater(qa['layout']['detail1']['x'], W / 4)
                self.assertLess(el['f1_title']['x'], W / 4)

                qa, el, _ = self.lay_out('T02', job, language + '-right', panel_side='right')
                macro, ring = qa['layout']['detail1'], qa['layout']['detail2']
                self.assertEqual((qa['layout']['layout'], qa['layout']['panel_side'], macro['x']), ('rail', 'right', 0))
                for id in ('f1_title', 'f2_title'):
                    self.assertGreaterEqual(el[id]['x'], macro['x'] + macro['w'])  # copy on the ground right of the macro
                    self.assertLessEqual(el[id]['x'] + el[id]['w'], W - 60)
                self.assertLess(ring['cx'] + ring['r'], macro['x'] + macro['w'])   # the ringed close-up stays inside the picture
                self.assertGreater(ring['cx'], macro['w'] / 2)                    # near the copy side
                leader = next(row for row in qa['leaders'] if row['id'] == 'f2_leader')
                xs = [p[0] for p in leader['pts']]
                self.assertLessEqual(max(xs), el['f2_title']['x'])                 # from the caption bar leftward to the ring
                self.assertGreaterEqual(min(xs), ring['cx'])


if __name__ == '__main__':
    unittest.main()

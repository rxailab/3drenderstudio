"""Pages print only the seller's words, at the right spot, and never cut off (v41).

Diagonal cutting pliers uploaded as 06_diagonal_cutting_pliers.glb with no description came back with
"06_DIAGONAL_C…" cut off at the canvas edge, two invented selling points ("Angled cutting edges",
"Cylinder grip": the mesh "Cylinder") and their lines drawn to the rivet and the jaw/handle joint.
No test reaches a model service: the text and vision models are stood in for.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import brand_kit
import brandkit_render
import brandkit_review
import brandkit_set
import listing_layout
import info_layer
import listing
import studio_store as store
from test_brandkit import cut_out
from test_listing import PARTS, StoreCase


class NameTests(unittest.TestCase):
    def test_a_file_stem_becomes_a_readable_name_and_a_written_name_is_left_alone(self):
        cases = {'06_diagonal_cutting_pliers': 'Diagonal Cutting Pliers', '06_DIAGONAL_CUTTING_PLIERS.glb': 'Diagonal Cutting Pliers',
                 '08_phillips_screwdriver_v2_final': 'Phillips Screwdriver', 'pliers_01': 'Pliers', '12V_Drill': '12V Drill',
                 '3-in-1-wire-stripper': '3 in 1 Wire Stripper', '数显卡尺_01': '数显卡尺', '06 diagonal pliers': 'Diagonal Pliers',
                 'DeWalt DCD777': 'DeWalt DCD777', 'Ratchet Pipe Cutter': 'Ratchet Pipe Cutter', '数显卡尺': '数显卡尺', '': ''}
        for raw, printed in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(brandkit_set.humanised(raw), printed)
        self.assertEqual(listing.default_product({'name': '06_diagonal_cutting_pliers'})['title'], 'Diagonal Cutting Pliers')

    def test_no_page_builder_prints_the_file_stem(self):
        ctx = {'language': 'en', 'aspect': '1:1', 'brand': brand_kit.Brand(brand_kit.validate({}), '#d0612a'), 'copy': {}, 'kinds': {'T03'}, 'scenes': [],
               'product': {'title': '06_diagonal_cutting_pliers', 'headline': '', 'subline': '', 'features': [{'title': 'Sharp jaws'}, {'title': 'Soft grips'}],
                           'specs': [], 'figures': []},
               'assets': {'hero': {'width': 800, 'height': 600}, 'd0': {}, 'd1': {}}, 'details': [(0, 'd0'), (1, 'd1')], 'anchors': [], 'focus': {}}
        for kind in ('T03', 'T12'):
            for job in brandkit_set.jobs_for(kind, ctx):
                self.assertNotIn('_', job['headline']); self.assertIn('Diagonal Cutting', job['headline'].replace('\n', ' '))
        self.assertEqual(brandkit_set._data_eyebrow(ctx, 'Something else'), 'Diagonal Cutting Pliers')


class MeshNameTests(unittest.TestCase):
    product = {'_model_parts': ['Cylinder', 'Handle_L', 'Plane.001', 'Jaw'], 'source': '', 'headline': '', 'subline': '',
               'drafted': {'source': 'ai'}}

    def test_a_mesh_name_is_never_a_selling_point(self):
        for title in ('Cylinder grip', 'Handle_L', 'Cube.001', 'Plane', 'jaw'):
            with self.subTest(title=title):
                self.assertTrue(brandkit_set.mesh_named(title, self.product))
        for title in ('Angled cutting edges', 'Insulated handles', '硬化钢刀轮'):
            with self.subTest(title=title):
                self.assertIsNone(brandkit_set.mesh_named(title, self.product))
        # the seller's own word is theirs: a hydraulic cylinder they wrote about stays
        self.assertIsNone(brandkit_set.mesh_named('Hydraulic cylinder', {**self.product, 'source': 'Hydraulic cylinder lifts 2 t'}))
        # the page builders (strict off: the line may be a translation) still refuse file marks and part names
        self.assertIsNone(brandkit_set.mesh_named('Cylinder grip', self.product, strict=False))
        self.assertTrue(brandkit_set.mesh_named('Handle_L', self.product, strict=False))
        printed, notes = brandkit_set.printable({**self.product, 'features': [{'title': 'Cylinder grip'}, {'title': 'Insulated handles'}]})
        self.assertEqual([row['title'] for row in printed['features']], ['Insulated handles'])
        self.assertIn('Cylinder grip', notes[0])

    def test_a_measurement_is_not_a_file_mark(self):
        for title in ('0.01 mm accuracy', 'Reads to 0.02 mm', '1.25 in jaw opening', '精度 0.01 mm'):
            with self.subTest(title=title):
                self.assertIsNone(brandkit_set.mesh_named(title, self.product))
        self.assertTrue(brandkit_set.mesh_named('Cube.001', self.product))

    def test_points_typed_into_a_record_never_drafted_are_the_sellers_own(self):
        typed = {**self.product, 'drafted': None,
                 'features': [{'title': 'Hydraulic cylinder'}, {'title': 'Jaw'}, {'title': 'Handle_L'}, {'title': '0.01 mm accuracy'}]}
        printed, notes = brandkit_set.printable(typed)
        self.assertEqual([row['title'] for row in printed['features']], ['Hydraulic cylinder', 'Jaw', '0.01 mm accuracy'])
        self.assertEqual(len(notes), 1); self.assertIn('Handle_L', notes[0])
        ctx = {'product': typed}
        self.assertEqual(brandkit_set._printable_rows(ctx, [(0, 'a'), (1, 'b'), (2, 'c')]), [(0, 'a'), (1, 'b')])
        # the same points in a drafted record were made from the file: left out
        drafted, _ = brandkit_set.printable({**typed, 'drafted': {'source': 'ai'}})
        self.assertEqual([row['title'] for row in drafted['features']], ['0.01 mm accuracy'])

    def test_the_callout_close_up_and_detail_builders_skip_a_mesh_named_point(self):
        features = [{'title': 'Handle_L', 'detail': ''}, {'title': 'Sharp jaws', 'detail': ''}, {'title': 'Soft grips', 'detail': ''}]
        ctx = {'language': 'en', 'aspect': '1:1', 'brand': None, 'copy': {}, 'kinds': {'T01', 'T02', 'T03'}, 'scenes': [],
               'product': {**self.product, 'title': 'Pliers', 'features': features, 'specs': [], 'figures': []},
               'assets': {'hero': {'width': 800, 'height': 600}, 'm0': {}, 'm1': {}, 'd0': {}, 'd2': {}},
               'anchors': [(0, [.2, .2]), (1, [.5, .5]), (2, [.7, .7])], 'macros': [(0, 'm0'), (1, 'm1')],
               'details': [(0, 'd0'), (2, 'd2')], 'focus': {}}
        with patch.object(brandkit_render, 'template_info', return_value={'tokens': {'palette': {'bg': '#ffffff', 'accent': '#d0612a'}}}):
            printed = [[row['title'] for row in job.get('features') or job.get('details') or []]
                       for kind in ('T01', 'T02', 'T03') for job in brandkit_set.jobs_for(kind, ctx)]
        self.assertTrue(printed)
        self.assertFalse([titles for titles in printed if 'Handle_L' in titles], printed)

    def test_pages_that_print_selling_points_wait_for_the_sellers_own(self):
        product = {'features': [], 'figures': [], 'specs': [], '_model_parts': PARTS}
        has, why = brandkit_set.requirements(product, [], True, 0)
        self.assertEqual((has['T01'], has['T02'], has['T03'], has['T12']), (False, False, False, True))
        for kind in ('T01', 'T02', 'T03'):
            self.assertIn('没有填写卖点', why[kind])


class DraftTests(StoreCase):
    def compose(self, points):
        """The text model's stand-in for the draft: it writes headline and points whatever it was given."""
        answer = {'kicker': '', 'headline': 'Diagonal cutting pliers', 'subline': 'For cutting wire and cable ends.', 'figures': [], 'specs': [],
                  'layout': 'callouts', 'free_area': 'none', 'product_box': [.1, .1, .9, .9],
                  'points': [{'title': title, 'detail': '', 'icon': '', 'parts': []} for title in points]}
        return [patch.object(info_layer, '_config', return_value={'model': 'text'}), patch.object(info_layer, '_ask', return_value=answer)]

    def test_without_the_users_points_none_is_drafted(self):
        patches = self.compose(['Angled cutting edges', 'Cylinder grip'])
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        product = listing.draft('fixture', {'hint': ''})['product']
        self.assertEqual(product['features'], [])
        self.assertIn('没有写卖点', product['drafted']['notes'][0])
        # the rules (no model) draft nothing from the part names either
        for item in patches:
            item.stop()
        self.assertEqual(listing.draft('fixture', {'hint': '', 'replace': True})['product']['features'], [])

    def test_a_drafted_point_is_kept_only_when_the_user_wrote_it(self):
        for item in self.compose(['Flush-cut jaws', 'Angled cutting edges', 'Cylinder grip']):
            item.start(); self.addCleanup(item.stop)
        product = listing.draft('fixture', {'hint': 'Flush cut jaws for clean wire ends', 'language': 'en'})['product']
        self.assertEqual([row['title'] for row in product['features']], ['Flush-cut jaws'])
        self.assertTrue(any('2 条卖点' in note for note in product['drafted']['notes']), product['drafted']['notes'])
        # A Chinese hint on an English page: the user's own lines, translated at build time, never the model's.
        zh = listing.draft('fixture', {'hint': '刀口平齐\n握把防滑', 'language': 'en', 'replace': True})['product']
        self.assertEqual([row['title'] for row in zh['features']], ['刀口平齐', '握把防滑'])

    def test_extraction_never_keeps_a_mesh_name(self):
        answer = {'title': 'Diagonal Cutting Pliers', 'model': '', 'longest_side': {'value': '', 'unit': '', 'quote': ''}, 'headline': '', 'subline': '',
                  'notes': [], 'specs': [], 'figures': [],
                  'features': [{'title': 'Flush cut', 'detail': '', 'parts': []}, {'title': 'Cylinder grip', 'detail': '', 'parts': []}]}
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), patch.object(brandkit_review, '_ask', return_value=answer):
            product = listing.extract('fixture', {'text': 'Diagonal cutting pliers. Flush cut for clean ends.'})['product']
        self.assertEqual([row['title'] for row in product['features']], ['Flush cut'])


class PointCheckTests(unittest.TestCase):
    """One vision call checks every callout point before a line is drawn to it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)
        nx = ny = 20
        # two meshes: the jaws on the left half, the handles on the right
        cells = [-1] * (nx * ny)
        for y in range(5, 15):
            for x in range(3, 17):
                cells[y * nx + x] = 0 if x < 10 else 1
        self.picture = info_layer.Picture({'success': True, 'nx': nx, 'ny': ny, 'cells': cells, 'objects': ['Jaws', 'Handles']})
        self.hero = self.folder / 'hero.png'; Image.new('RGBA', (400, 400), (200, 90, 30, 255)).save(self.hero)
        self.product = {'title': 'Pliers', '_model_parts': ['Jaws', 'Handles'],
                        'features': [{'title': 'Flush-cut jaws', 'parts': [], 'point': [.4, .5]},          # found by the locator
                                     {'title': 'Soft grips', 'parts': ['Handles']},                        # chosen by the user
                                     {'title': 'Spring return', 'parts': [], 'point': [.6, .4]}]}
        self.original = [{'title': 'Flush-cut jaws', 'parts': []}, {'title': 'Soft grips', 'parts': ['Handles']}, {'title': 'Spring return', 'parts': []}]

    def ask(self, verdicts):
        return [patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})),
                patch.object(brandkit_review, '_ask', return_value={'points': verdicts})]

    def test_a_point_the_check_rejects_is_dropped_and_a_backed_correction_is_taken(self):
        candidates = brandkit_set.candidate_points(self.product, self.picture)
        self.assertEqual([index for index, _ in candidates], [0, 1, 2])
        verdicts = [{'number': 1, 'on': True, 'visible': True, 'x': 0, 'y': 0},
                    {'number': 2, 'on': False, 'visible': True, 'x': .2, 'y': .5},       # moved onto the jaws: not the handles' own part
                    {'number': 3, 'on': False, 'visible': True, 'x': .7, 'y': .5}]       # no part of its own to back the move
        cache = {}
        first, second = self.ask(verdicts)
        with first, second as asked:
            features, notes = brandkit_set.check_points(self.hero, self.picture, self.product, self.original, cache, self.folder)
            again, _ = brandkit_set.check_points(self.hero, self.picture, self.product, self.original, cache, self.folder)
        self.assertEqual(asked.call_count, 1)                                     # kept with the located points, not asked again
        self.assertEqual(features, again)
        facts = asked.call_args.args[2]
        self.assertEqual([row['selling_point'] for row in facts['points']], ['Flush-cut jaws', 'Soft grips', 'Spring return'])
        self.assertEqual(Path(asked.call_args.kwargs['image_path']).name, 'points-check.png')
        self.assertTrue((self.folder / 'points-check.png').is_file())
        self.assertEqual(features[0]['point'], [.4, .5])
        self.assertTrue(features[1]['no_leader']); self.assertEqual(features[1]['parts'], ['Handles'])      # the user's part keeps its close-ups
        self.assertNotIn('point', features[2]); self.assertEqual(features[2]['parts'], [])
        self.assertTrue(any(key.startswith('check-') for key in cache))
        self.assertIn('2 处', notes[0])
        has, _ = brandkit_set.requirements({**self.product, 'features': features, 'specs': [], 'figures': []}, [], True, 0)
        self.assertFalse(has['T01'])                                              # one line left: the callout page falls back
        # a correction that lands on the feature's own part is taken
        first, second = self.ask([{'number': 1, 'on': True, 'visible': True, 'x': 0, 'y': 0},
                                  {'number': 2, 'on': False, 'visible': True, 'x': .7, 'y': .6},
                                  {'number': 3, 'on': True, 'visible': True, 'x': 0, 'y': 0}])
        with first, second:
            moved, notes = brandkit_set.check_points(self.hero, self.picture, self.product, self.original, {}, self.folder)
        self.assertEqual((moved[1]['point'], moved[1].get('no_leader')), ([.7, .6], None))
        self.assertIn('移到', notes[-1])

    def test_without_a_vision_model_or_a_usable_answer_nothing_is_dropped(self):
        with patch.object(brandkit_review, '_configs', return_value=(None, None)):
            features, notes = brandkit_set.check_points(self.hero, self.picture, self.product, self.original, {}, self.folder)
        self.assertEqual((features, notes), (self.product['features'], []))
        cache = {}
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', side_effect=ValueError('timeout')):
            features, notes = brandkit_set.check_points(self.hero, self.picture, self.product, self.original, cache, self.folder)
        self.assertEqual(features, self.product['features']); self.assertIn('未经核对', notes[0]); self.assertEqual(cache, {})


class CutTextRankingTests(unittest.TestCase):
    """A version whose words are cut off is never made, so it never wins over one that holds them (renderer stood in for)."""

    def test_a_version_that_holds_its_words_wins_over_fewer_other_problems(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); folder = Path(temp.name)
        texts = [{'id': 'headline', 'kind': 'text'}, {'id': 'body', 'kind': 'text'}]
        answers = [{'issues': [{'check': 'text_overflow', 'id': 'headline'}], 'elements': texts},          # one hard problem: cut off
                   {'issues': [{'check': 'overlap', 'id': 'body'}, {'check': 'safe_margin', 'id': 'body'}], 'elements': texts}]
        jobs = [{'headline': 'first', '_group': 0}, {'headline': 'second', '_group': 1}]

        def render(kind, job, target, work):
            Image.new('RGB', (10, 10)).save(target)
            return answers[jobs.index(job)]
        with patch.object(listing_layout, 'jobs_for', return_value=jobs), patch.object(brandkit_render, 'render', side_effect=render):
            qa, notes, _ = brandkit_set.lay_out('T12', {'product': {}}, folder / 'page.png', folder / '_layout')
        self.assertIs(qa, answers[1]); self.assertTrue((folder / 'page.png').is_file())
        self.assertTrue(any('版式检查仍有 2 处问题' in note for note in notes), notes)


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class HeadlineFitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)

    def ctx(self, title):
        hero, _ = brandkit_set.flatten(cut_out(self.folder / 'h.png', size=(800, 500), box=(100, 150, 700, 350)), self.folder / 'hero.png')
        details = {}
        for index in range(2):
            details['d%d' % index], _ = brandkit_set.flatten(cut_out(self.folder / ('s%d.png' % index), size=(600, 600), box=(0, 0, 600, 600)),
                                                             self.folder / ('d%d.png' % index), trim=False, dark=True, ground=(38, 41, 45))
        return {'language': 'en', 'aspect': '1:1', 'brand': brand_kit.Brand(brand_kit.validate({}), '#d0612a'), 'copy': {}, 'kinds': {'T03'}, 'anchors': [],
                'product': {'title': title, 'headline': '', 'subline': '', 'features': [{'title': 'Sharp jaws'}, {'title': 'Soft grips'}], 'specs': [], 'figures': []},
                'assets': {'hero': hero, **details}, 'details': [(0, 'd0'), (1, 'd1')], 'focus': {}, 'scenes': []}

    def test_a_long_headline_fits_and_one_that_cannot_is_not_made(self):
        qa, _, _ = brandkit_set.lay_out('T03', self.ctx('06_diagonal_cutting_pliers'), self.folder / 'ok.png', self.folder / '_layout')
        self.assertFalse([row for row in qa['issues'] if row['check'] in ('text_overflow', 'safe_margin')], qa['issues'])
        headline = next(row for row in qa['elements'] if row['id'] == 'headline')
        self.assertEqual(headline['text'].upper().split(), ['DIAGONAL', 'CUTTING', 'PLIERS'])
        # One word wider than the column even at the smallest size: shrunk, then refused rather than cut off at the edge.
        with self.assertRaisesRegex(ValueError, '文字放不下'):
            brandkit_set.lay_out('T03', self.ctx('WWWWWWWWWWWWWWWWWWWWWWWWWW'), self.folder / 'bad.png', self.folder / '_layout')
        self.assertFalse((self.folder / 'bad.png').exists())


if __name__ == '__main__':
    unittest.main()


class BareSceneWordsTests(unittest.TestCase):
    """A product with nothing but a name: its scenes are titled by their place, so the mosaic's headline joins two places, never a task."""

    def test_scenes_are_titled_by_place_only_and_get_no_line(self):
        import brandkit_review, brandkit_set
        from unittest.mock import patch
        rows = [{'path': 'a.png', 'title': 'Workshop Drawer Check', 'body': 'x', 'role': 'use'}]
        product = {'language': 'en', 'title': 'Tool Cabinet', 'features': [], 'headline': '', 'subline': ''}
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', return_value={'title': 'In the workshop', 'body': 'Drawers glide open'}) as asked:
            out, done = brandkit_set.seen_captions(product, rows)
        self.assertIn('never a task', asked.call_args.args[2]['shows'])
        self.assertEqual((out[0]['title'], out[0]['body'], done), ('In the workshop', '', 1))
        self.assertEqual(out[0]['captions']['en']['how'], brandkit_set.PLACE_ONLY)
        told = {**product, 'features': [{'title': 'Soft-close drawers'}]}
        self.assertEqual(brandkit_set.caption_way(told), brandkit_set.CAPTIONED)        # captions written the usual way are kept for it

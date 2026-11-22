"""The dimensions page (T06) prints only the sizes the user stated, each on a real view of the product (v41).

A screwdriver given only as 21 cm long once came out with "16.7 mm" twice: as a second line on the product and as the depth of
a made-up pink box. These tests hold the rules that replaced that: sizes come from the user's facts only, the model's own
measurements just place them on an axis and flag implausible ones, and a size the main view hides is drawn on the same
product turned a quarter (a second render at the same scale), never on a schematic box.
"""
import json
import sys
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
import studio_store as store
from test_listing import StoreCase

SCREWDRIVER = [210.0, 16.7, 16.7]          # [width, depth, height] as the listing poses it, in mm
PLIERS = [200.0, 68.0, 30.0]               # lying on its broad face
TOOLBOX = [400.0, 194.0, 190.0]            # standing


def product(**fields):
    return {'title': 'Phillips Screwdriver', 'specs': [], 'figures': [], 'size_mm': None, **fields}


def stated(item, dims, flat=False, plan=None, unit='mm'):
    return brandkit_set.stated_dimensions(item, dims, flat, plan, unit)


class StatedDimensionTests(unittest.TestCase):
    def test_only_the_length_given_gives_only_the_length(self):
        rows, notes = stated(product(size_mm=210), SCREWDRIVER)
        self.assertEqual([(row['key'], row['view'], row['value'], row['unit']) for row in rows], [('length', 'main', 21.0, 'cm')])
        self.assertEqual(notes, [])
        rows, _ = stated(product(size_mm=210, specs=[{'label': 'Length', 'value': '21 cm'}]), SCREWDRIVER, unit='in')
        self.assertEqual([(row['value'], row['unit']) for row in rows], [(21.0, 'cm')])          # as the user wrote it, not converted
        self.assertFalse(any(abs(row['mm'] - 16.7) < 1 for row in rows))                     # nothing measured on the model

    def test_the_sellers_figure_with_its_other_unit_wins(self):
        rows, _ = stated(product(size_mm=200, specs=[{'label': 'Material', 'value': '200 mm (8 in) CR-V'}]), PLIERS, flat=True)
        self.assertEqual((rows[0]['value'], rows[0]['unit'], rows[0]['alt']), (200.0, 'mm', {'value': 8.0, 'unit': 'in'}))
        rows, _ = stated(product(specs=[{'label': 'Overall length', 'value': '8 in / 200 mm'}]), PLIERS, flat=True)
        self.assertEqual((rows[0]['value'], rows[0]['unit'], rows[0]['alt']), (8.0, 'in', {'value': 200.0, 'unit': 'mm'}))

    def test_each_label_finds_its_axis_and_the_view_that_shows_it(self):
        # Pliers lying flat, seen from above: the length on the top view, the thickness on the tool seen from the front.
        rows, _ = stated(product(size_mm=200, specs=[{'label': 'Length', 'value': '200 mm'}, {'label': 'Thickness', 'value': '30 mm'}]), PLIERS, flat=True)
        self.assertEqual([(row['key'], row['axis'], row['view']) for row in rows], [('length', 0, 'main'), ('thickness', 2, 'end')])
        # A toolbox face on: length and height on the front, the width only from the end.
        rows, _ = stated(product(size_mm=400, specs=[{'label': '长', 'value': '40 cm'}, {'label': '宽', 'value': '19 cm'}, {'label': '高度', 'value': '19cm'}]), TOOLBOX)
        self.assertEqual([(row['key'], row['axis'], row['view']) for row in rows], [('length', 0, 'main'), ('height', 2, 'main'), ('width', 1, 'end')])
        # One spec giving all three, by its letters or by the stock order of a size line.
        for label in ('Dimensions (L x W x H)', 'Overall size', '外形尺寸'):
            with self.subTest(label=label):
                rows, _ = stated(product(specs=[{'label': label, 'value': '40 x 19 x 19 cm'}]), TOOLBOX)
                self.assertEqual(sorted((row['key'], row['mm']) for row in rows), [('height', 190.0), ('length', 400.0), ('width', 190.0)])
        # A round handle's diameter goes on the view already drawn, so no second render is needed.
        rows, _ = stated(product(size_mm=210, specs=[{'label': 'Diameter', 'value': '17 mm'}]), SCREWDRIVER)
        self.assertEqual({row['view'] for row in rows}, {'main'})

    def test_a_part_size_and_a_size_far_off_the_model_are_left_out_with_a_note(self):
        rows, notes = stated(product(size_mm=210, specs=[{'label': 'Blade length', 'value': '75 mm'}, {'label': 'Width', 'value': '60 mm'},
                                                       {'label': 'Screwdriver length', 'value': '21 cm'}, {'label': 'High-carbon steel', 'value': 'S2'}]),
                             SCREWDRIVER)
        self.assertEqual([row['key'] for row in rows], ['length'])
        self.assertTrue(any('Blade length' in note and '部件' in note for note in notes), notes)
        self.assertTrue(any('Width 60 mm' in note and '比例不符' in note for note in notes), notes)
        self.assertFalse(any('High-carbon' in note for note in notes))                        # not a length at all: no note
        _, notes = stated(product(specs=[{'label': 'Length', 'value': '20-25 cm'}]), SCREWDRIVER)
        self.assertTrue(any('对不上' in note for note in notes), notes)

    def test_the_plans_three_real_dimensions_are_printed_largest_to_longest(self):
        rows, _ = stated(product(), [690.0, 380.0, 738.0], plan=[380.5, 738, 690])
        self.assertEqual(sorted((row['key'], row['mm']) for row in rows), [('depth', 380.5), ('height', 738.0), ('width', 690.0)])
        self.assertEqual(next(row['view'] for row in rows if row['key'] == 'depth'), 'end')

    def test_the_longest_side_of_a_standing_product_is_its_height(self):
        # a ladder given only as 1.8 m long: one vertical line, named height, not "Length"
        rows, _ = stated(product(title='Step ladder', size_mm=1800), [500.0, 120.0, 1800.0])
        self.assertEqual([(row['key'], row['axis'], row['view']) for row in rows], [('height', 2, 'main')])
        # lying flat, the longest side stays the length
        rows, _ = stated(product(size_mm=200), PLIERS, flat=True)
        self.assertEqual([row['key'] for row in rows], ['length'])

    def test_the_reviewer_facts_carry_only_the_stated_sizes(self):
        import brandkit_review
        facts = brandkit_review.facts_of({**product(size_mm=210), 'headline': '', 'subline': '', 'features': []}, {'length': 210.0})
        self.assertEqual(facts['stated_sizes_mm'], {'length': 210.0})
        self.assertNotIn('measured_on_3d_model_mm', facts)
        self.assertNotIn('measured on the 3D model', json.dumps(facts))

    def test_the_figure_chips_never_add_a_measured_size(self):
        ctx = {'language': 'en', 'unit': 'mm', 'dims_mm': SCREWDRIVER, 'flat': False,
               'product': product(size_mm=210, figures=[{'value': '6', 'unit': 'mm', 'label': 'Tip'}])}
        chips = brandkit_set._chip_rows(ctx)
        self.assertEqual([(row['label'], row['value'], row['unit']) for row in chips], [('Tip', '6', 'mm'), ('Length', '21', 'cm')])


def side_asset(folder, name, size, box):
    image = Image.new('RGBA', size, (0, 0, 0, 0)); ImageDraw.Draw(image).rectangle(box, fill=(200, 40, 30, 255))
    image.save(folder / (name + '.src.png'))
    fields, _ = brandkit_set.flatten(folder / (name + '.src.png'), folder / (name + '.png'), pad=0)
    return fields


class DimensionJobTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.folder = Path(temp.name)

    def ctx(self, item, dims, flat, assets):
        rows, _ = brandkit_set.stated_dimensions(item, dims, flat, None, 'mm')
        return {'product': {'headline': '', 'subline': '', 'features': [], **item}, 'language': 'en', 'aspect': '1:1', 'brand': None, 'assets': assets,
                'unit': 'mm', 'dims_mm': dims, 'flat': flat, 'stated': rows, 'scenes': [], 'parts': [], 'poses': []}

    def test_the_page_carries_only_stated_sizes_and_no_depth_box(self):
        side = side_asset(self.folder, 'side', (1500, 300), (0, 100, 1500, 220))
        job = brandkit_set.jobs_for('T06', self.ctx(product(size_mm=210), SCREWDRIVER, False, {'side': side}))[0]
        self.assertEqual([(row['key'], row['value']) for row in job['dimensions']], [('length', 21.0)])
        self.assertNotIn('depth', job); self.assertIsNone(job['end']); self.assertEqual(set(job['assets']), {'side'})
        self.assertNotIn('Depth', json.dumps(job))

    def test_a_hidden_size_goes_on_the_turned_product_at_the_same_scale(self):
        top = side_asset(self.folder, 'side', (1500, 600), (0, 0, 1499, 509))          # 200 x 68 mm from above
        front = side_asset(self.folder, 'end', (1000, 200), (0, 0, 999, 149))           # 200 x 30 mm from the front, at another zoom
        item = product(title='Pliers', size_mm=200, specs=[{'label': 'Thickness', 'value': '30 mm'}])
        job = brandkit_set.jobs_for('T06', self.ctx(item, PLIERS, True, {'side': top, 'end': front}))[0]
        self.assertEqual([row['key'] for row in job['dimensions']], ['length'])
        self.assertEqual((job['end']['asset'], job['end']['at'], [row['key'] for row in job['end']['dimensions']]), ('end', 'below', ['thickness']))
        self.assertAlmostEqual(job['end']['scale'], 1.5, places=2)                       # the shared length: 1500 px against 1000 px
        self.assertEqual(job['end']['dimensions'][0]['side'], [1, 0])                     # a vertical line beside the front view
        # Without the turned render the hidden size is not printed anywhere.
        job = brandkit_set.jobs_for('T06', self.ctx(item, PLIERS, True, {'side': top}))[0]
        self.assertIsNone(job['end']); self.assertEqual([row['key'] for row in job['dimensions']], ['length'])
        # Nothing stated that passes the checks: no page.
        bad = product(specs=[{'label': 'Length', 'value': '2 cm'}])
        self.assertEqual(brandkit_set.jobs_for('T06', self.ctx(bad, PLIERS, True, {'side': top})), [])

    @unittest.skipUnless(brandkit_render.available(), 'needs Chrome')
    def test_the_turned_view_is_laid_out_beside_the_product(self):
        front = side_asset(self.folder, 'side', (1500, 760), (0, 0, 1499, 759))         # a toolbox face on, 400 x 190 mm
        end = side_asset(self.folder, 'end', (800, 800), (0, 0, 799, 783))              # from the end, 194 x 190 mm
        item = product(title='Toolbox', size_mm=400, specs=[{'label': 'Length', 'value': '40 cm'}, {'label': 'Width', 'value': '19 cm'},
                                                           {'label': 'Height', 'value': '19 cm'}])
        job = brandkit_set.jobs_for('T06', self.ctx(item, TOOLBOX, False, {'side': front, 'end': end}))[0]
        qa = brandkit_render.render('T06', job, self.folder / 'T06.png', work=self.folder)
        self.assertFalse(brandkit_render.layout_issues(qa), qa['issues'])
        layout = qa.get('layout') or {}
        end_quad, hero_quad = layout['end_view']['quad'], layout['hero_content_quad']
        self.assertGreater(min(q[0] for q in end_quad), max(q[0] for q in hero_quad))                 # right of the product
        self.assertAlmostEqual(max(q[1] for q in end_quad), max(q[1] for q in hero_quad), delta=1)     # on its ground line
        height = lambda quad: max(q[1] for q in quad) - min(q[1] for q in quad)
        self.assertAlmostEqual(height(end_quad), height(hero_quad), delta=2)                          # one scale: the same height
        ids = {row['id'] for row in qa['elements']}
        self.assertIn('end_view', ids); self.assertFalse({'depth_val', 'depth_name'} & ids)


class DimensionPictureTests(StoreCase):
    def project(self, dims):
        record = store.get('project', 'fixture'); record.update(name='Combination pliers')
        record['analysis']['features']['dimensions'] = dims; record['plan']['rotation'] = [0, 0, 0]
        return record

    def test_the_turned_render_is_asked_for_only_when_a_stated_size_needs_it(self):
        rows = [{'kind': 'T06', 'index': 0}]
        record = self.project([2.0, .68, .30])                                            # pliers lying on their face
        only_length = listing.picture_requests(record, {**listing.stored(record)[0], 'size_mm': 200}, 200, rows)
        self.assertEqual(only_length['side']['camera']['elevation'], 89); self.assertNotIn('end', only_length)
        item = {**listing.stored(record)[0], 'size_mm': 200, 'specs': [{'label': 'Thickness', 'value': '30 mm'}]}
        requests = listing.picture_requests(record, item, 200, rows)
        self.assertEqual((requests['end']['camera']['azimuth'], requests['end']['camera']['elevation']), (requests['side']['camera']['azimuth'], 0))
        self.assertEqual(requests['end']['camera']['projection'], 'orthographic')
        record = self.project([4.0, 1.94, 1.90])                                          # a toolbox standing
        item = {**listing.stored(record)[0], 'size_mm': 400, 'specs': [{'label': 'Width', 'value': '19 cm'}]}
        requests = listing.picture_requests(record, item, 400, rows)
        self.assertEqual(requests['side']['camera']['elevation'], 0)
        self.assertEqual(requests['end']['camera']['azimuth'], requests['side']['camera']['azimuth'] + 90)
        self.assertEqual(listing.picture_requests(record, {**item, 'specs': []}, 400, rows).get('end'), None)

    def test_a_build_flattens_the_turned_view_and_reports_what_it_left_out(self):
        with patch.object(components, 'stage', side_effect=self.stage), patch.object(brandkit_set, 'image_model_ready', return_value=False), \
                patch.object(brandkit_render, 'render', side_effect=self.fake_render), patch.object(brandkit_render, 'layout_issues', return_value=[]), \
                patch.object(brandkit_render, 'available', return_value=True), patch.object(brandkit_render, 'export_svg', return_value=None):
            record = store.get('project', 'fixture')
            dims = listing._oriented_mm(record, listing.display_rotation(record)[0], 225)
            (across, up), hidden = brandkit_set.view_axes(dims, listing._seen_from_above(dims))
            listing.save_product('fixture', {'title': 'Pipe Cutter', 'size_mm': 225, 'pages': ['T06'],
                                             'specs': [{'label': 'Depth', 'value': '%g mm' % round(dims[hidden])}, {'label': 'Blade length', 'value': '30 mm'}]})
            _, notes = listing.build('fixture')
        self.assertEqual(len([row for row in self.requests if (row.get('camera') or {}).get('projection') == 'orthographic']), 2)   # face on and turned
        job = self.jobs[0]
        self.assertEqual(job['end']['asset'], 'end'); self.assertIn('end', job['assets'])
        self.assertTrue(Path(job['assets']['end']['path']).is_file())
        self.assertEqual([row['key'] for row in job['end']['dimensions']], ['depth'])
        self.assertTrue(any('Blade length' in note for note in notes), notes)

    def fake_render(self, kind, job, out, work=None):
        self.__dict__.setdefault('jobs', []).append(job)
        Image.new('RGB', (job['canvas']['width'], job['canvas']['height']), 'white').save(out)
        return {'issues': [], 'elements': [], 'notes': [], 'layout': {}, 'fonts': [], 'render': {'png': str(out), 'size': [job['canvas']['width'], job['canvas']['height']]}}


if __name__ == '__main__':
    unittest.main()

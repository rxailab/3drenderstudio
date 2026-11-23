"""Backdrops: a product's own render set into a photograph of an empty place.

No network and no depth model are used: pictures and measurements are fixtures. The last class runs real Blender on
a synthetic place (a grey floor, a wall, a camera 1.3 m up) and a red box, to check that the box lands on the floor
at an asked size and darkens the floor beside it. How a finished picture looks is judged on real builds, not here.
"""
import copy
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import brandkit_set
import planner
import scene_backdrop
import scene_imagegen
import scene_stylist

ROOT = Path(__file__).resolve().parents[1]
BLENDER = shutil.which('blender') or next((line.split('=', 1)[1].strip().strip('"') for line in
                                           (ROOT / '.env').read_text(encoding='utf-8').splitlines() if line.startswith('BLENDER_PATH=')), None) \
    if (ROOT / '.env').is_file() else shutil.which('blender')
GOOD = {'version': scene_backdrop.VERSION, 'size': [2048, 2048], 'focal_px': 1891.0, 'fov_deg': 56.9, 'normal': [0, -.964, -.266], 'height_m': 1.3,
        'tilt_down_deg': 15.4, 'roll_deg': .4, 'support_share': .49, 'depth_step': 8, 'lamps': [], 'near_m': 1.73}         # a garage floor, framed for 0.94 m
BENCH = {**GOOD, 'focal_px': 2614.0, 'fov_deg': 42.8, 'height_m': .54, 'tilt_down_deg': 19.0, 'near_m': 1.01}              # a bench top, framed for 0.39 m
CLOSE = {**BENCH, 'height_m': .3, 'near_m': .45}                                                                          # a close-up of it, for 0.18 m


def project(size_mm=900):
    plan = planner.default_plan(); plan.update(visual_review=False, dimensions_mm=[size_mm * .78, size_mm * .54, size_mm])
    return {'id': 'fixture', 'slug': 'fixture', 'name': 'Tool Cabinet', 'version': 1, 'status': 'ready', 'history': [], 'outputs': [],
            'metadata': {}, 'analysis': {'features': {'dimensions': [.78, .54, 1.0]}}, 'plan': plan, 'model_path': '/nowhere/cabinet.glb'}


def library(test):
    folder = Path(tempfile.mkdtemp()); test.addCleanup(shutil.rmtree, folder, True)
    holder = patch.object(scene_backdrop, 'LIBRARY', folder); holder.start(); test.addCleanup(holder.stop)
    # Whether a place looks new is a vision model's answer: none here unless a test gives one
    looks = patch.object(scene_imagegen, 'place_check', return_value=None); test.looks = looks.start(); test.addCleanup(looks.stop)
    return folder


def stock(folder, spec, geometry=GOOD, look=scene_backdrop.LOOK):
    """A measured backdrop in the library (made under ``look``; None: before places were asked to look new)."""
    place = folder / spec['id']; place.mkdir(parents=True)
    Image.new('RGB', (16, 16), (150, 150, 150)).save(place / 'backdrop.png'); Image.new('L', (16, 16), 255).save(place / 'support.png')
    (place / 'depth.npy').write_bytes(b'x'); (place / 'geometry.json').write_text(json.dumps(geometry))
    (place / 'backdrop.json').write_text(json.dumps({'version': 1, 'id': spec['id'], **({'look': look} if look else {})}))
    return place


class SpecTests(unittest.TestCase):
    def test_a_large_product_stands_on_the_floor_and_a_small_one_on_the_places_bench(self):
        self.assertEqual(scene_backdrop.spec_for('workshop_hobbyist', .9)['id'], 'workshop_hobbyist-floor-large')
        self.assertEqual(scene_backdrop.spec_for('workshop_hobbyist', .16)['id'], 'workshop_hobbyist-surface-small')
        self.assertEqual(scene_backdrop.spec_for('home_starter', .45)['stand'], 'surface')

    def test_outdoor_places_and_sizes_no_picture_is_framed_for_have_no_backdrop(self):
        for segment in ('home_garden', 'pro_landscape', 'mobile_tradesperson', 'nobody', None):
            self.assertIsNone(scene_backdrop.spec_for(segment, .5))
        self.assertIsNone(scene_backdrop.spec_for('workshop_hobbyist', .03))                # too small for any framing
        self.assertIsNone(scene_backdrop.spec_for('workshop_hobbyist', 4))

    def test_the_picture_asked_for_is_the_empty_place_with_room_for_the_product(self):
        for size in (.9, .4, .15):
            text = scene_backdrop.spec_for('workshop_hobbyist', size)['prompt']
            self.assertIn('no people', text); self.assertIn('completely empty in the middle', text); self.assertNotIn('{', text)
        self.assertIn('pegboard', scene_backdrop.spec_for('workshop_hobbyist', .15)['prompt'])
        self.assertNotIn('workbench against the wall', scene_backdrop.spec_for('workshop_hobbyist', .15)['prompt'])   # it is the bench

    def test_a_measured_picture_is_checked_before_it_is_used(self):
        spec = scene_backdrop.spec_for('workshop_hobbyist', .9)
        self.assertIsNone(scene_backdrop.gate(GOOD, spec))
        for change in ({'support_share': .05}, {'roll_deg': 9}, {'tilt_down_deg': 1}, {'height_m': .3}, {'height_m': 3.4}, {'fov_deg': 140}, {'version': 0}):
            self.assertTrue(scene_backdrop.gate({**GOOD, **change}, spec), change)
        self.assertTrue(scene_backdrop.flaw(GOOD, 'surface'))                                # a floor view is no bench view
        self.assertIsNone(scene_backdrop.flaw(BENCH, 'surface'))

    def test_a_picture_is_filed_under_the_size_it_is_framed_for(self):
        """The image model was asked for a close-up of a bench and made a wider view: that is a backdrop for larger things."""
        self.assertAlmostEqual(scene_backdrop.framed_for(GOOD), .94, delta=.01); self.assertAlmostEqual(scene_backdrop.framed_for(BENCH), .39, delta=.01)
        self.assertEqual(scene_backdrop.suits(GOOD, 'floor', 'large'), 'large')
        self.assertEqual(scene_backdrop.suits(BENCH, 'surface', 'small'), 'medium'); self.assertEqual(scene_backdrop.suits(CLOSE, 'surface', 'small'), 'small')
        self.assertIsNone(scene_backdrop.suits({**GOOD, 'near_m': .3}, 'floor', 'large'))    # a floor seen that close is framed for nothing
        self.assertTrue(scene_backdrop.gate(BENCH, scene_backdrop.spec_for('workshop_hobbyist', .16)))
        self.assertIsNone(scene_backdrop.gate(BENCH, scene_backdrop.spec_for('workshop_hobbyist', .4)))
        self.assertEqual(scene_backdrop.framed_for({}), 0)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder = library(self); self.spec = scene_backdrop.spec_for('workshop_hobbyist', .9)
        buffer = self.folder / 'painted.png'; Image.new('RGB', (32, 32), (160, 160, 160)).save(buffer); self.png = buffer.read_bytes()

    def measured(self, geometry):
        def measure(folder, stop=None):
            if geometry is None: return None
            Image.new('L', (32, 32), 255).save(folder / 'support.png'); (folder / 'depth.npy').write_bytes(b'x')
            (folder / 'geometry.json').write_text(json.dumps(geometry)); return geometry
        return measure

    def test_a_backdrop_in_the_library_is_used_without_any_request(self):
        place = stock(self.folder, self.spec)
        with patch.object(scene_backdrop, '_paint') as paint, patch.object(scene_backdrop, 'measure') as measure:
            self.assertEqual(scene_backdrop.ensure(self.spec), (place, self.spec))
        paint.assert_not_called(); measure.assert_not_called()

    def test_a_missing_backdrop_is_painted_and_measured_once_and_kept(self):
        said = []
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png) as paint, \
                patch.object(scene_backdrop, 'measure', side_effect=self.measured(GOOD)):
            first, filed = scene_backdrop.ensure(self.spec, None, said.append); second, _ = scene_backdrop.ensure(self.spec)
        self.assertEqual(first, second); self.assertEqual(paint.call_count, 1); self.assertEqual(filed['id'], self.spec['id'])
        self.assertEqual(paint.call_args.args[0], self.spec['prompt'])
        self.assertEqual(json.loads((first / 'backdrop.json').read_text())['id'], self.spec['id'])
        self.assertTrue(any('只做一次' in line for line in said))
        self.assertEqual([path.name for path in self.folder.iterdir() if path.name.startswith('.')], [])       # no work folder left behind

    def test_a_picture_framed_for_another_size_is_kept_for_that_size(self):
        small = scene_backdrop.spec_for('workshop_hobbyist', .16)
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png) as paint, \
                patch.object(scene_backdrop, 'measure', side_effect=self.measured(BENCH)):
            folder, filed = scene_backdrop.ensure(small)
            self.assertEqual((filed['id'], folder.name), ('workshop_hobbyist-surface-medium',) * 2)        # not thrown away: it is a backdrop
            self.assertIsNone(scene_backdrop.ready(small)); self.assertTrue(scene_backdrop.ready(filed))
            # asked again, the image model frames it the same way again: the place already has that one, and the close-up is given up
            with self.assertRaises(scene_backdrop.Unavailable):
                scene_backdrop.ensure(small)
            with self.assertRaises(scene_backdrop.Unavailable):
                scene_backdrop.ensure(small)
        self.assertEqual(paint.call_count, scene_backdrop.MAX_REJECTED)
        self.assertEqual(json.loads((folder / 'backdrop.json').read_text())['prompt'], small['prompt'])    # how the picture was really made

    def test_a_place_that_looks_used_is_painted_again_and_then_given_up_for_this_time(self):
        self.looks.side_effect = [{'new': False, 'issues': ['the bench top is scored']}, {'new': True, 'issues': []}]
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png) as paint, \
                patch.object(scene_backdrop, 'measure', side_effect=self.measured(GOOD)):
            folder, _ = scene_backdrop.ensure(self.spec)
        self.assertEqual(paint.call_count, 2); self.assertTrue((self.folder / (self.spec['id'] + '.worn-1.png')).is_file())
        made = json.loads((folder / 'backdrop.json').read_text())
        self.assertEqual((made['look'], made['looked']), (scene_backdrop.LOOK, True))
        self.assertIn('brand new and spotless', self.spec['prompt'])
        shutil.rmtree(folder); self.looks.side_effect = None
        self.looks.return_value = {'new': False, 'issues': ['oil on the floor']}
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png) as paint, \
                patch.object(scene_backdrop, 'measure') as measure:
            with self.assertRaises(scene_backdrop.Unavailable):
                scene_backdrop.ensure(self.spec)
        self.assertEqual(paint.call_count, scene_backdrop.PAINTS); measure.assert_not_called()
        # a worn picture is not a measurement failure: it never uses up the place's measurement tries
        self.assertFalse((self.folder / (self.spec['id'] + '.rejected.json')).exists())
        worn = json.loads((self.folder / (self.spec['id'] + '.worn.json')).read_text())
        self.assertEqual((worn['count'], worn['look']), (1, scene_backdrop.LOOK)); self.assertIn('oil on the floor', worn['reason'])
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png) as paint:
            with self.assertRaises(scene_backdrop.Unavailable):
                scene_backdrop.ensure(self.spec)                             # the second worn round
            with self.assertRaises(scene_backdrop.Unavailable) as said:
                scene_backdrop.ensure(self.spec)                             # paused: nothing painted
        self.assertEqual(paint.call_count, scene_backdrop.PAINTS); self.assertIn('陈旧或脏乱', str(said.exception))
        # after the pause the place is painted again, and a picture that looks new is kept
        self.looks.return_value = {'new': True, 'issues': []}
        with patch.object(scene_backdrop, 'WORN_PAUSE', -1), patch.object(scene_backdrop, 'can_measure', return_value=True), \
                patch.object(scene_backdrop, '_paint', return_value=self.png), patch.object(scene_backdrop, 'measure', side_effect=self.measured(GOOD)):
            self.assertTrue(scene_backdrop.ensure(self.spec)[0].is_dir())

    def test_a_backdrop_made_before_places_had_to_look_new_is_looked_at_once(self):
        place = stock(self.folder, self.spec, look=None)
        self.assertIsNone(scene_backdrop.ready(self.spec))
        self.assertFalse(scene_backdrop.revet(self.spec))                    # nobody could look: it stays out
        self.looks.return_value = {'new': True, 'issues': []}
        self.assertTrue(scene_backdrop.revet(self.spec)); self.assertEqual(scene_backdrop.ready(self.spec), place)
        (place / 'backdrop.json').write_text(json.dumps({'version': 1, 'id': self.spec['id']}))       # an old one again, that looks used
        self.looks.return_value = {'new': False, 'issues': ['the bench top is scored']}; self.looks.reset_mock()
        self.assertFalse(scene_backdrop.revet(self.spec)); self.assertFalse(scene_backdrop.revet(self.spec))
        self.assertEqual(self.looks.call_count, 1)                           # marked worn: not asked again
        self.assertEqual(scene_backdrop._made(place)['worn'], ['the bench top is scored'])
        self.assertIsNone(scene_backdrop.ready(self.spec))

    def test_an_old_backdrop_is_looked_at_under_the_place_lock(self):
        stock(self.folder, self.spec, look=None); seen = []
        def check(path):
            seen.append(scene_backdrop._place_lock(self.spec).locked()); return {'new': True, 'issues': []}
        self.looks.side_effect = check
        self.assertTrue(scene_backdrop.revet(self.spec)); self.assertEqual(seen, [True])

    def test_without_the_measuring_environment_nothing_is_painted(self):
        with patch.object(scene_backdrop, 'can_measure', return_value=False), patch.object(scene_backdrop, '_paint') as paint:
            with self.assertRaises(scene_backdrop.Unavailable):
                scene_backdrop.ensure(self.spec)
        paint.assert_not_called()

    def test_a_picture_that_fails_its_measurement_is_set_aside_and_the_place_given_up_after_two(self):
        bad = {**GOOD, 'support_share': .03}
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png) as paint, \
                patch.object(scene_backdrop, 'measure', side_effect=self.measured(bad)):
            for _ in range(3):
                with self.assertRaises(scene_backdrop.Unavailable):
                    scene_backdrop.ensure(self.spec)
        self.assertEqual(paint.call_count, scene_backdrop.MAX_REJECTED)                      # the third time no picture is paid for
        self.assertTrue((self.folder / (self.spec['id'] + '.rejected-1.png')).is_file())
        self.assertIsNone(scene_backdrop.ready(self.spec))

    def test_a_depth_model_that_does_not_finish_rejects_the_picture(self):
        with patch.object(scene_backdrop, 'can_measure', return_value=True), patch.object(scene_backdrop, '_paint', return_value=self.png), \
                patch.object(scene_backdrop, 'measure', side_effect=self.measured(None)):
            with self.assertRaises(scene_backdrop.Unavailable):
                scene_backdrop.ensure(self.spec)
        self.assertEqual(json.loads((self.folder / (self.spec['id'] + '.rejected.json')).read_text())['count'], 1)


class CreateTests(unittest.TestCase):
    def setUp(self):
        self.folder = library(self); self.out = self.folder / 'out' / 'scene2'
        self.place = stock(self.folder, scene_backdrop.spec_for('workshop_hobbyist', .9))

    def rendered(self, result):
        def render(request, folder, stop=None):
            self.request = request; folder.mkdir(parents=True, exist_ok=True)
            if result.get('success'):
                Image.new('RGB', (64, 64), (120, 30, 30)).save(folder / (folder.name + '.png'))
            return result
        return render

    def test_the_product_is_set_into_the_backdrop_and_recorded_as_a_composite(self):
        result = {'success': True, 'box': [.35, .4, .79, .9], 'share': .5, 'distance_m': 2.4, 'yaw_deg': -30.0, 'light': {'ceiling_watts': 40}}
        proposal = {'segment_id': 'workshop_hobbyist', 'people': False, 'articulation': {'state': 'top2_open', 'joints': [{'name': 'drawer1', 'value': 260}], 'label': 'Top 2 Open'}}
        with patch.object(scene_backdrop, 'enabled', return_value=True), patch.object(scene_backdrop, 'render', side_effect=self.rendered(result)):
            picture = scene_backdrop.create(project(), project()['plan'], proposal, 65, 900, self.out)
        record = picture['record']
        self.assertEqual((record['model'], record['attempts'], record['usage']), ('composite', 1, {}))
        self.assertEqual(record['check']['status'], 'passed'); self.assertTrue(all(record['check']['flags'][name] for name in scene_imagegen.FIDELITY_FLAGS))
        self.assertEqual(record['anchor']['box'], result['box']); self.assertEqual(record['backdrop']['id'], 'workshop_hobbyist-floor-large')
        self.assertIs(record['people'], False); self.assertEqual(record['usage_retry'], scene_imagegen.USAGE_RETRY)
        self.assertTrue(picture['png'].is_file() and picture['jpg'].is_file())
        self.assertEqual(self.request['longest_m'], .9); self.assertEqual(self.request['backdrop'], str(self.place))
        self.assertEqual(self.request['azimuth'], scene_backdrop.MAX_TURN)                    # seen from its front quarter, not its side
        self.assertEqual(self.request['plan']['articulation'], {'state': 'top2_open', 'joints': [{'name': 'drawer1', 'value': 260}]})
        self.assertEqual(self.request['stand'], 'floor')

    def test_no_free_spot_no_place_or_no_size_leaves_the_scene_to_the_image_model(self):
        indoor = {'segment_id': 'workshop_hobbyist', 'people': False}
        with patch.object(scene_backdrop, 'enabled', return_value=True), patch.object(scene_backdrop, 'render', side_effect=self.rendered({'success': False, 'reason': 'no_fit'})):
            with self.assertRaisesRegex(scene_backdrop.Unavailable, '空位'):
                scene_backdrop.create(project(), project()['plan'], indoor, 30, 900, self.out)
        with patch.object(scene_backdrop, 'enabled', return_value=True), patch.object(scene_backdrop, 'render') as render:
            for proposal, size in (({'segment_id': 'home_garden', 'people': False}, 900), (indoor, 0), (indoor, 20)):
                with self.assertRaises(scene_backdrop.Unavailable):
                    scene_backdrop.create(project(), project()['plan'], proposal, 30, size, self.out)
            render.assert_not_called()
        with patch.object(scene_backdrop, 'enabled', return_value=False), self.assertRaises(scene_backdrop.Unavailable):
            scene_backdrop.create(project(), project()['plan'], indoor, 30, 900, self.out)

    def test_a_backdrop_the_place_already_has_is_tried_before_a_new_one_is_paid_for(self):
        small = scene_backdrop.spec_for('workshop_hobbyist', .16); medium = scene_backdrop.spec_for('workshop_hobbyist', .4)
        stock(self.folder, small, CLOSE)
        result = {'success': True, 'box': [.3, .3, .7, .7], 'share': .4, 'zoom': 1.0}
        with patch.object(scene_backdrop, 'enabled', return_value=True), patch.object(scene_backdrop, 'render', side_effect=self.rendered(result)), \
                patch.object(scene_backdrop, 'ensure') as ensure:
            picture = scene_backdrop.create(project(), project()['plan'], {'segment_id': 'workshop_hobbyist', 'people': False}, 30, 400, self.out)
        ensure.assert_not_called()
        self.assertEqual(picture['record']['backdrop']['id'], small['id']); self.assertEqual(self.request['stand'], 'surface')
        # no room in it either: the one framed for this size is made
        answers = iter([{'success': False, 'reason': 'no_fit'}, result]); made = stock(self.folder / 'elsewhere', medium, BENCH)
        def render(request, folder, stop=None):
            return self.rendered(next(answers))(request, folder, stop)
        with patch.object(scene_backdrop, 'enabled', return_value=True), patch.object(scene_backdrop, 'render', side_effect=render), \
                patch.object(scene_backdrop, 'ensure', return_value=(made, medium)) as ensure:
            picture = scene_backdrop.create(project(), project()['plan'], {'segment_id': 'workshop_hobbyist', 'people': False}, 30, 400, self.out)
        ensure.assert_called_once(); self.assertEqual(picture['record']['backdrop']['id'], medium['id'])

    def test_it_can_be_switched_off(self):
        with patch.dict(os.environ, {'SCENE_BACKDROPS': '0'}):
            self.assertFalse(scene_backdrop.enabled())


class SceneTests(unittest.TestCase):
    """Where a listing's scenes are made: the place scene goes to the backdrop, everything else to the image model."""

    def painted(self, *args, **kwargs):
        self.asked.append('painted')
        return {'png': Path('x.png'), 'jpg': Path('x.jpg'), 'width': 8, 'height': 8,
                'record': {'model': 'gpt-image-2', 'attempts': 1, 'check': {'status': 'passed'}, 'people': kwargs.get('people')}}

    def composite(self, *args, **kwargs):
        self.asked.append('composite')
        return {'png': Path('x.png'), 'jpg': Path('x.jpg'), 'width': 8, 'height': 8,
                'record': {'model': 'composite', 'attempts': 1, 'check': {'status': 'passed'}, 'people': False, 'backdrop': {'id': 'workshop_hobbyist-floor-large'}}}

    def generate(self, proposal, create=None, item=None):
        self.asked = []
        with patch.object(scene_imagegen, 'create', side_effect=self.painted), \
                patch.object(scene_backdrop, 'create', side_effect=create or self.composite):
            return scene_stylist.generate(item or project(), proposal, folder=Path(tempfile.gettempdir()) / 'never-written')

    def test_a_place_scene_without_people_is_set_into_a_backdrop(self):
        _, record, _ = self.generate({'scene': 'A workshop.', 'segment_id': 'workshop_hobbyist', 'people': False, 'backdrop': True})
        self.assertEqual(self.asked, ['composite'])
        self.assertEqual((record['model'], record['backdrop']['id']), ('composite', 'workshop_hobbyist-floor-large'))
        self.assertNotIn('painted_because', record)

    def test_a_scene_with_people_or_without_the_mark_is_painted(self):
        for proposal in ({'scene': 'x', 'people': True, 'backdrop': True}, {'scene': 'x', 'people': False}, {'scene': 'x', 'backdrop': True}):
            _, record, _ = self.generate(proposal)
            self.assertEqual(self.asked, ['painted'], proposal); self.assertNotIn('backdrop', record)

    def test_when_no_backdrop_can_be_had_the_scene_is_painted_and_the_record_says_why(self):
        def refuse(*args, **kwargs):
            self.asked.append('composite'); raise scene_backdrop.Unavailable('背景图里没有能按合适大小放下产品的空位')
        _, record, _ = self.generate({'scene': 'x', 'segment_id': 'workshop_hobbyist', 'people': False, 'backdrop': True}, refuse)
        self.assertEqual(self.asked, ['composite', 'painted']); self.assertIn('空位', record['painted_because'])

    def test_a_size_guessed_from_the_category_is_not_built_on(self):
        item = project(); item['plan'].pop('dimensions_mm'); item['analysis'] = {}
        _, record, _ = self.generate({'scene': 'x', 'segment_id': 'workshop_hobbyist', 'people': False, 'backdrop': True}, item=item)
        self.assertEqual(self.asked, ['painted']); self.assertIn('尺寸', record['painted_because'])

    def test_the_listing_marks_which_scenes_may_be_set_into_a_backdrop(self):
        import scene_audience
        first = {'scene': 'Hands cut copper wire on a wooden bench.', 'segment_id': 'workshop_hobbyist', 'people': True, 'framing': 'hands'}
        with patch.object(scene_stylist, 'propose', return_value=first), patch.object(scene_stylist, '_source_image', return_value=None), \
                patch.object(scene_audience, 'rank', return_value=['workshop_hobbyist', 'site_team']):
            briefs = brandkit_set.scene_briefs({'name': 'Thing'}, {'hint': '', 'audience': 'auto', 'people': 'exclude'}, 3)
        self.assertEqual([bool(brief['proposal'].get('backdrop')) for brief in briefs], [False, True, True])
        self.assertEqual([brief['proposal'].get('people') for brief in briefs][1:], [False, False])
        with patch.object(scene_stylist, 'propose', return_value=first), patch.object(scene_stylist, '_source_image', return_value=None), \
                patch.object(scene_audience, 'rank', return_value=['workshop_hobbyist', 'site_team']):
            briefs = brandkit_set.scene_briefs({'name': 'Thing'}, {'hint': '', 'audience': 'auto', 'people': 'auto'}, 3)
        self.assertEqual([bool(brief['proposal'].get('backdrop')) for brief in briefs], [False, True, False])      # the third shows people

    def scenes(self, previous, records):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        for row in (previous or {}).get('items', []):
            Image.new('RGB', (8, 8)).save(folder / (row['role'] + '.png')); row['path'] = str(folder / (row['role'] + '.png'))
        made = []
        def generate(item, proposal, *args):
            made.append(proposal['scene']); picture = folder / 'new.png'; Image.new('RGB', (8, 8)).save(picture)
            return None, records[proposal['scene']], {'png': picture, 'record': {'anchor': {'box': [.3, .4, .7, .9]}}}
        briefs = [{'role': role, 'proposal': {'scene': role, 'backdrop': role == 'place', 'people': role != 'place'}} for role in ('use', 'place')]
        settings = {'enabled': True, 'count': 2, 'hint': '', 'audience': 'auto', 'people': 'auto'}
        with patch.object(brandkit_set, 'image_model_ready', return_value=True), patch.object(brandkit_set, 'scene_key', return_value='k'), \
                patch.object(brandkit_set, 'scene_briefs', return_value=briefs), patch.object(scene_backdrop, 'enabled', return_value=True), \
                patch.object(brandkit_set, 'captions', return_value=[{'title': 't', 'body': ''}] * 2), \
                patch.object(brandkit_set, 'seen_captions', side_effect=lambda product, rows: (rows, 0)), \
                patch.object(scene_stylist, 'generate', side_effect=generate):
            rows, _, notes = brandkit_set.make_scenes({'id': 'p'}, {'scenes': settings, 'language': 'en'}, folder, lambda: False, lambda *a: None, previous)
        return rows, notes, made

    def test_the_listing_says_which_scene_is_the_models_own_render(self):
        records = {'use': {'model': 'gpt-image-2', 'check': {'status': 'passed'}, 'people': True},
                   'place': {'model': 'composite', 'check': {'status': 'passed'}, 'people': False, 'attempts': 1}}
        rows, notes, made = self.scenes(None, records)
        self.assertEqual([row['placed'] for row in rows], ['painted', 'composite']); self.assertEqual(rows[1]['box'], [.3, .4, .7, .9])
        self.assertTrue(any('第 2 张' in note and '合成进背景图' in note for note in notes))
        self.assertFalse(any('第 1 张' in note and '合成' in note for note in notes))

    def test_a_place_that_failed_when_painted_gets_its_one_more_try_and_is_then_left_alone(self):
        """Rows stored before this had no record of their extra try: a failed one is made once more (a place now goes into
        a backdrop), and what is stored from then on says so, so that a rebuild does not pay for it again."""
        previous = {'key': 'k', 'items': [{'role': 'use', 'title': 'u', 'body': '', 'check': 'passed'},
                                          {'role': 'place', 'title': 'p', 'body': '', 'check': 'failed'}]}
        records = {'place': {'model': 'composite', 'check': {'status': 'passed'}, 'people': False, 'usage_retry': scene_imagegen.USAGE_RETRY}}
        rows, notes, made = self.scenes(copy.deepcopy(previous), records)
        self.assertEqual(made, ['place']); self.assertEqual([(row['role'], row['check']) for row in rows], [('use', 'passed'), ('place', 'passed')])
        self.assertEqual((rows[1]['placed'], rows[1]['usage_retry']), ('composite', scene_imagegen.USAGE_RETRY))
        previous['items'][1].update(usage_retry=scene_imagegen.USAGE_RETRY, placed='painted')       # painted again and failed again
        rows, notes, made = self.scenes(copy.deepcopy(previous), records)
        self.assertEqual(made, []); self.assertEqual(rows[1]['check'], 'failed')

    def test_what_a_rebuild_needs_to_know_about_a_scene_is_stored_with_the_listing(self):
        import listing
        row = {'path': str(Path(tempfile.gettempdir()) / 'scene1.png'), 'title': 't', 'focus': [.5, .5], 'role': 'use', 'usage_retry': 1,
               'placed': 'painted', 'people': False, 'check_issues': ['x'], 'check_flags': ['rests_stably'], 'check_error': True,
               'recheck': {'reference': 'r.png'}}
        with patch.object(listing.components, 'url', return_value='/files/x'):
            stored = listing._scene_record(row)
        for key in ('usage_retry', 'placed', 'people', 'check_issues', 'check_flags', 'check_error', 'recheck'):
            self.assertEqual(stored[key], row[key], key)


def save_npy(path, rows):
    """A float32 array file, written by hand: the app's own Python has no numpy."""
    header = "{'descr': '<f4', 'fortran_order': False, 'shape': (%d, %d), }" % (len(rows), len(rows[0]))
    header += ' ' * (64 - (10 + len(header) + 1) % 64) + '\n'
    values = [value for row in rows for value in row]
    Path(path).write_bytes(b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin1') + struct.pack('<%df' % len(values), *values))


@unittest.skipUnless(BLENDER and Path(BLENDER).is_file(), 'Blender is not installed')
class BlenderTests(unittest.TestCase):
    """A synthetic place: the camera 1.3 m above a grey floor, looking down 20 degrees, a lighter wall 6 m away."""

    SIZE = 256; FOCAL = 240.0; HEIGHT = 1.3; TILT = 20.0; WALL = 6.0
    MAKE = '''
import bpy, sys
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0.25)); box = bpy.context.object; box.scale = (0.3, 0.2, 0.25)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
red = bpy.data.materials.new('Red'); red.use_nodes = True
red.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.7, 0.03, 0.03, 1)
box.data.materials.append(red)
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format='GLB')
'''

    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp()); cls.model = cls.folder / 'box.glb'
        script = cls.folder / 'make.py'; script.write_text(cls.MAKE)
        subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(script), '--', str(cls.model)], capture_output=True, timeout=300)
        cls.place = cls.backdrop(cls.folder / 'place', free=True)
        cls.full = cls.backdrop(cls.folder / 'full', free=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    @classmethod
    def backdrop(cls, folder, free):
        folder.mkdir(); size = cls.SIZE; tilt = math.radians(cls.TILT); normal = [0.0, -math.cos(tilt), -math.sin(tilt)]

        def depth(u, v):
            down = normal[1] * (v - size / 2) / cls.FOCAL + normal[2]
            return min(-cls.HEIGHT / down, cls.WALL) if down < -1e-6 else cls.WALL
        picture = Image.new('RGB', (size, size)); support = Image.new('L', (size, size))
        for v in range(size):
            floor = depth(0, v) < cls.WALL
            for u in range(size):
                picture.putpixel((u, v), (150, 150, 150) if floor else (200, 200, 200)); support.putpixel((u, v), 255 if floor and free else 0)
        picture.save(folder / 'backdrop.png'); support.save(folder / 'support.png')
        save_npy(folder / 'depth.npy', [[depth(u, v) for u in range(0, size, 8)] for v in range(0, size, 8)])
        (folder / 'geometry.json').write_text(json.dumps({'version': 1, 'size': [size, size], 'focal_px': cls.FOCAL, 'fov_deg': 56.1, 'normal': normal,
                                                         'height_m': cls.HEIGHT, 'tilt_down_deg': cls.TILT, 'roll_deg': 0, 'support_share': .6,
                                                         'depth_step': 8, 'lamps': []}))
        return folder

    def run_blender(self, backdrop, out, longest_m=.6, share_range=(.15, .6), **more):
        plan = planner.default_plan(); plan.update(visual_review=False)
        with patch.dict(os.environ, {'BLENDER_PATH': BLENDER}):
            return scene_backdrop.render({'model_path': str(self.model), 'plan': plan, 'longest_m': longest_m, 'backdrop': str(backdrop), 'azimuth': 30,
                                          'share': .3, 'share_range': list(share_range), 'stand': 'floor', 'samples': 16, 'long': self.SIZE, **more}, out)

    def test_the_box_stands_on_the_floor_at_the_asked_size_and_darkens_it(self):
        out = self.folder / 'scene1'; result = self.run_blender(self.place, out)
        self.assertTrue(result and result.get('success'), (result, (out / 'blender.log').read_text()[-1500:] if (out / 'blender.log').is_file() else ''))
        x0, y0, x1, y1 = result['box']
        self.assertTrue(.03 < x0 < x1 < .97 and .03 < y0 < y1 < .97, result['box'])
        self.assertTrue(.15 <= max(x1 - x0, y1 - y0) <= .62, result['box'])
        self.assertAlmostEqual(result['light']['rendered_plane'], result['light']['photographed_plane'], delta=.03)     # as bright as the photograph
        with Image.open(out / 'scene1.png') as image:
            image = image.convert('RGB'); size = image.size
            middle = image.getpixel((int((x0 + x1) / 2 * size[0]), int((y0 + y1) / 2 * size[1])))
            far = image.getpixel((12, 112)); wall = image.getpixel((12, 30))
            beside = image.getpixel((int((x0 + x1) / 2 * size[0]), min(int(y1 * size[1]) + 3, size[1] - 1)))
        self.assertEqual(size, (self.SIZE, self.SIZE))
        self.assertGreater(middle[0], middle[1] + 40)                                    # the red box is there
        self.assertTrue(all(abs(value - 150) <= 8 for value in far), far)                 # the floor far from it is the photograph's
        self.assertTrue(all(abs(value - 200) <= 3 for value in wall), wall)               # so is the wall
        self.assertLess(beside[1], 150 - 8)                                               # its shadow is on the floor beside it

    def test_a_product_too_small_for_the_frame_is_shown_in_an_enlarged_part_of_the_photograph(self):
        out = self.folder / 'scene3'; result = self.run_blender(self.place, out, longest_m=.3, share_range=(.27, .6))
        self.assertTrue(result and result.get('success'), result)
        self.assertGreater(result['zoom'], 1.2); self.assertLessEqual(result['zoom'], 2.0)
        x0, y0, x1, y1 = result['box']
        self.assertTrue(.03 < x0 < x1 < .97 and .03 < y0 < y1 < .97, result['box'])
        self.assertTrue(.22 <= max(x1 - x0, y1 - y0) <= .62, result['box'])                 # where the search said it would be, and as large
        self.assertAlmostEqual((x0 + x1) / 2, .5, delta=.12)
        with Image.open(out / 'scene3.png') as image:
            image = image.convert('RGB'); middle = image.getpixel((int((x0 + x1) / 2 * image.width), int((y0 + y1) / 2 * image.height)))
            floor = image.getpixel((6, image.height - 6))
        self.assertGreater(middle[0], middle[1] + 40)
        self.assertTrue(all(abs(value - 150) <= 10 for value in floor), floor)             # the enlarged floor is still the photograph's
        # without the enlargement the same product has no place at that size
        self.assertEqual((self.run_blender(self.place, self.folder / 'scene4', longest_m=.3, share_range=(.27, .6), max_zoom=1) or {}).get('reason'), 'no_fit')

    def test_a_place_with_no_free_floor_is_reported_as_no_fit(self):
        result = self.run_blender(self.full, self.folder / 'scene2')
        self.assertEqual((result or {}).get('reason'), 'no_fit')


if __name__ == '__main__':
    unittest.main()

"""The pose agent: what the vision model may say about moving parts, how a state becomes a render plan, and the
geometry it is solved with (when Blender is installed)."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import articulation  # noqa: E402
import brandkit_set  # noqa: E402
import listing_scenes  # noqa: E402

PROBE = {'parts': [{'id': 1, 'object': 'Half A', 'pieces': None}, {'id': 2, 'object': 'Half B', 'pieces': None},
                   {'id': 3, 'object': 'Rivet', 'pieces': None}]}


def answer(**changes):
    value = {'articulated': True, 'product_kind': 'pliers', 'reasoning': 'two halves on a rivet',
             'joints': [{'name': 'jaws', 'type': 'hinge', 'moving': [1, 9], 'counter': [2, 1], 'stretch': [], 'axis': 'z',
                         'pivot': {'kind': 'part', 'part': 3, 'side': ''}, 'away_from': 0, 'max': 400}],
             'states': [{'id': 'closed', 'label_zh': '闭合', 'label_en': 'Closed', 'role': 'rest', 'values': [{'joint': 'jaws', 'value': 0}]},
                        {'id': 'open', 'label_zh': '张开', 'label_en': 'Open', 'role': 'open',
                         'values': [{'joint': 'jaws', 'value': 18}, {'joint': 'ghost', 'value': 5}]}],
             'usage': {'en': ['grip the wire in the jaws'], 'zh': ['钳口夹住导线']}}
    value.update(changes)
    return value


class AnswerTests(unittest.TestCase):
    def test_only_known_parts_and_joints_are_kept_and_ranges_are_bounded(self):
        clean = articulation._clean_answer(answer(), PROBE)
        joint = clean['joints'][0]
        self.assertEqual((joint['moving'], joint['counter']), ([1], [2]))          # unknown part 9 dropped; 1 cannot turn both ways
        self.assertEqual(joint['max'], 180.0)                                       # a hinge turns at most half a circle
        self.assertEqual((joint['pivot']['part'], joint['pivot']['side'], joint['away_from']), (3, None, None))
        self.assertEqual(clean['states'][1]['values'], {'jaws': 18.0})              # a value for a joint that does not exist is dropped
        self.assertTrue(clean['articulated'])

    def test_a_hinge_rests_as_modelled_and_its_open_state_is_open(self):
        swapped = answer(states=[{'id': 'closed', 'label_zh': '闭合', 'label_en': 'Closed', 'role': 'rest', 'values': [{'joint': 'jaws', 'value': 20}]},
                                 {'id': 'open', 'label_zh': '张开', 'label_en': 'Open', 'role': 'open', 'values': [{'joint': 'jaws', 'value': 0}]}])
        clean = articulation._clean_answer(swapped, PROBE)
        self.assertEqual([state['values']['jaws'] for state in clean['states']], [0.0, 20.0])
        self.assertEqual(articulation._tidy({'usage': ['grip it']})['usage'], {'en': ['grip it'], 'zh': []})

    def test_one_state_or_no_joint_is_not_articulated(self):
        self.assertFalse(articulation._clean_answer(answer(states=answer()['states'][:1]), PROBE)['articulated'])
        self.assertFalse(articulation._clean_answer(answer(joints=[]), PROBE)['articulated'])
        self.assertFalse(articulation._clean_answer(answer(articulated=False), PROBE)['articulated'])

    def test_state_values_are_held_inside_the_solved_range(self):
        states = [{'id': 'out', 'values': {'blade': 400.0}}, {'id': 'in', 'values': {'blade': -900.0}}]
        clamped = articulation._clamped(states, [{'name': 'blade', 'max': 250.0, 'min': -58.0}])
        self.assertEqual([row['values']['blade'] for row in clamped], [250.0, -58.0])          # pulled in only as far as it is out
        shut = articulation._clamped([{'id': 'closed', 'values': {'jaws': -20.0}}], [{'name': 'jaws', 'max': 20.0}])
        self.assertEqual(shut[0]['values']['jaws'], 0.0)                                         # never shut past the model


class PlanTests(unittest.TestCase):
    record = {'status': 'ready', 'key': 'k', 'joints': [{'name': 'jaws', 'type': 'hinge', 'pivot': [0, 0, 0], 'axis': [0, 0, 1], 'sign': -1,
                                                         'max': 20, 'moving': [{'object': 'A', 'pieces': None}], 'notes': ['x']}],
              'states': [{'id': 'closed', 'label_en': 'Closed', 'role': 'rest', 'values': {'jaws': 0}},
                         {'id': 'open', 'label_en': 'Open', 'role': 'open', 'values': {'jaws': 18}}],
              'usage': {'en': ['grip the wire in the jaws'], 'zh': ['夹住导线']}}

    def test_a_state_becomes_the_plan_blender_poses_and_the_rest_pose_needs_none(self):
        project = {'id': 'p', 'articulation': self.record}
        plan = articulation.plan_for(project, 'open')
        self.assertEqual((plan['state'], plan['joints'][0]['value'], plan['joints'][0]['sign']), ('open', 18, -1))
        self.assertNotIn('notes', plan['joints'][0])
        self.assertIsNone(articulation.plan_for(project, 'closed'))
        self.assertIsNone(articulation.plan_for({'articulation': {**self.record, 'status': 'failed'}}, 'open'))
        self.assertEqual(articulation.usage(project, 'zh'), ['夹住导线'])

    def test_each_scene_gets_the_state_of_its_moment_and_the_usage_rules(self):
        project = {'id': 'p', 'articulation': self.record}
        with patch('studio_store.get', return_value=project):
            at_work = brandkit_set._with_moving_parts(project, {'role': 'use', 'proposal': {'scene': 'x'}})
            at_rest = brandkit_set._with_moving_parts(project, {'role': 'place', 'proposal': {'scene': 'y'}})
        self.assertEqual((at_work['proposal']['articulation']['state'], at_work['proposal']['articulation']['label']), ('open', 'Open'))
        self.assertNotIn('articulation', at_rest['proposal'])                      # set down, it rests closed
        self.assertEqual(at_rest['proposal']['usage'], ['grip the wire in the jaws'])


BLENDER = shutil.which('blender') or next((line.split('=', 1)[1].strip().strip('"') for line in
                                          (ROOT / '.env').read_text(encoding='utf-8').splitlines() if line.startswith('BLENDER_PATH=')), None) \
    if (ROOT / '.env').is_file() else shutil.which('blender')


@unittest.skipUnless(BLENDER and Path(BLENDER).is_file(), 'Blender is not installed')
class GeometryTests(unittest.TestCase):
    """A pair of bars crossing at a rivet (a toy pair of pliers) and a slider on a short rail, built in Blender."""

    MAKE = '''
import bpy, sys
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
def bar(name, angle, location, scale):
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=(0, 0, angle)); ob = bpy.context.object
    ob.name = name; ob.scale = scale; bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return ob
a = bar('Half A', 0.12, (0, 0, 0.004), (0.1, 0.006, 0.003))
b = bar('Half B', -0.12, (0, 0, -0.004), (0.1, 0.006, 0.003))
for ob in (a, b):
    ob.data.transform(__import__('mathutils').Matrix.Translation((0, 0, 0)))
rail = bar('Rail', 0, (0.3, 0, 0), (0.06, 0.01, 0.004))
slider = bar('Slider', 0, (0.21, 0, 0), (0.03, 0.02, 0.008))
fixed = bar('Stop', 0, (0.17, 0, 0), (0.01, 0.03, 0.006))
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format='GLB')
'''

    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp())
        cls.model = cls.folder / 'toy.glb'
        script = cls.folder / 'make.py'; script.write_text(cls.MAKE)
        subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(script), '--', str(cls.model)], capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def run_script(self, request, name):
        out = self.folder / name; out.mkdir(exist_ok=True)
        (out / 'request.json').write_text(json.dumps({**request, 'model_path': str(self.model), 'output_dir': str(out)}))
        subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(ROOT / 'blender' / 'articulate.py'), '--', str(out / 'request.json')],
                       capture_output=True, timeout=300)
        return json.loads((out / 'result.json').read_text())

    def test_parts_are_numbered_and_drawn_from_two_sides(self):
        self.assertTrue(self.model.is_file())
        probe = self.run_script({'action': 'probe', 'size': 256}, 'probe')
        self.assertTrue(probe['success'], probe.get('error'))
        names = {part['object'] for part in probe['parts']}
        self.assertEqual(names, {'Half A', 'Half B', 'Rail', 'Slider', 'Stop'})
        self.assertEqual([view['name'] for view in probe['views']], ['top', 'three_quarter'])
        self.assertTrue(all((self.folder / 'probe' / view['image']).is_file() for view in probe['views']))

    def test_a_slider_opens_away_from_its_stop_and_a_scissor_pair_opens_without_crossing(self):
        probe = self.run_script({'action': 'probe', 'size': 128}, 'probe2')
        ids = {part['object']: part['id'] for part in probe['parts']}
        joints = [{'name': 'slide', 'type': 'slide', 'moving': [ids['Slider']], 'counter': [], 'stretch': [], 'axis': 'x',
                   'pivot': {'kind': 'auto', 'part': None, 'side': None}, 'away_from': ids['Stop'], 'max': 60},
                  {'name': 'jaws', 'type': 'hinge', 'moving': [ids['Half A']], 'counter': [ids['Half B']], 'stretch': [], 'axis': 'z',
                   'pivot': {'kind': 'crossing', 'part': None, 'side': None}, 'away_from': None, 'max': 20}]
        states = [{'id': 'rest', 'values': {}}, {'id': 'open', 'values': {'slide': 40, 'jaws': 15}}]
        solved = self.run_script({'action': 'solve', 'size': 128, 'joints': joints, 'states': states}, 'solve')
        self.assertTrue(solved['success'], solved.get('error'))
        slide = next(j for j in solved['joints'] if j['name'] == 'slide')
        self.assertEqual(slide['sign'], 1)                                          # toward +x: away from the stop at its -x end
        hinge = next(j for j in solved['joints'] if j['name'] == 'jaws')
        self.assertLess(abs(hinge['pivot'][0]), .02)                                # the bars cross at the middle
        self.assertEqual(len(solved['previews']), 2)
        rest, opened = solved['previews']
        for row in solved['previews']:                                              # each state from both front corners
            self.assertTrue((self.folder / 'solve' / row['image']).is_file() and (self.folder / 'solve' / row['other']).is_file())
        self.assertEqual(rest['measured'], {'crossing': 'none', 'moved_away_mm': None})
        self.assertNotEqual(opened['measured']['crossing'], 'yes')                  # nothing passes through anything
        self.assertGreater(opened['measured']['moved_away_mm'], 0)                  # the slider went away from the parts it rides on


if __name__ == '__main__':
    unittest.main()


class VerdictTests(unittest.TestCase):
    def test_the_pose_as_delivered_is_never_failed(self):
        """A pair of cutters modelled with their handles spread: the check calls the as-modelled 'closed' state wrong."""
        project = {'id': 'p', 'model_path': __file__, 'name': 'cutters'}
        states = [{'id': 'closed', 'label_zh': '闭合', 'label_en': 'Closed', 'role': 'rest', 'values': {'jaws': 0.0}},
                  {'id': 'open', 'label_zh': '张开', 'label_en': 'Open', 'role': 'open', 'values': {'jaws': 15.0}}]
        answer_ = {'articulated': True, 'product_kind': 'cutters', 'reasoning': '', 'usage': {'en': [], 'zh': []},
                   'joints': [{'name': 'jaws', 'type': 'hinge', 'moving': [1], 'counter': [2], 'stretch': [], 'axis': 'z',
                               'pivot': {'kind': 'origin', 'part': 0, 'side': ''}, 'away_from': 0, 'max': 20}],
                   'states': [{'id': s['id'], 'label_zh': s['label_zh'], 'label_en': s['label_en'], 'role': s['role'],
                               'values': [{'joint': 'jaws', 'value': s['values']['jaws']}]} for s in states]}
        verdict = {'ok': False, 'fix': 'closed is not closed', 'states': [{'id': 'closed', 'ok': False, 'issue': '手柄张开'}, {'id': 'open', 'ok': True, 'issue': ''}]}
        part = {'size_mm': [1, 1, 1], 'centroid_mm': [0, 0, 0], 'materials': [], 'pieces': None}
        probe = {'parts': [{**part, 'id': 1, 'object': 'A'}, {**part, 'id': 2, 'object': 'B'}], 'objects': [], 'views': [],
                 'unit_mm': 1000.0, 'bbox_mm': [[0, 0, 0], [1, 1, 1]]}
        solved = {'joints': [{'name': 'jaws', 'type': 'hinge', 'max': 20.0, 'min': 0.0}], 'previews': []}
        asked = iter([answer_, verdict])
        stored = {}
        import brandkit_review, components
        with patch.object(articulation, 'get', return_value=project), patch.object(articulation, 'key_of', return_value='k'), \
                patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'v'})), \
                patch.object(brandkit_review, '_ask', side_effect=lambda *a, **k: next(asked)), \
                patch.object(components, 'run_blender', side_effect=lambda script, request, *a, **k: probe if request['action'] == 'probe' else solved), \
                patch.object(articulation, 'labelled_sheet', return_value='parts.png'), patch.object(articulation, 'state_sheet', return_value='states.png'), \
                patch.object(articulation, '_store', side_effect=lambda pid, record: stored.setdefault('r', record)):
            record = articulation.analyse('p', force=True)
        self.assertEqual(record['status'], 'ready')


class MeasuredTests(unittest.TestCase):
    """A cabinet door opened towards the camera is seen edge-on and was judged to swing into the cabinet. The geometry
    was right; what is measured on it now outweighs what the picture seems to show."""

    def analysed(self, verdict_rows, measured):
        project = {'id': 'p', 'model_path': __file__, 'name': 'cart'}
        answer_ = {'articulated': True, 'product_kind': 'cart', 'reasoning': '', 'usage': {'en': [], 'zh': []},
                   'joints': [{'name': 'door', 'type': 'hinge', 'moving': [2], 'counter': [], 'stretch': [], 'axis': 'z',
                               'pivot': {'kind': 'edge', 'part': 0, 'side': '+x'}, 'away_from': 1, 'max': 110}],
                   'states': [{'id': 'closed', 'label_zh': '关', 'label_en': 'Closed', 'role': 'rest', 'values': [{'joint': 'door', 'value': 0}]},
                              {'id': 'open', 'label_zh': '开', 'label_en': 'Open', 'role': 'open', 'values': [{'joint': 'door', 'value': 110}]}]}
        part = {'size_mm': [1, 1, 1], 'centroid_mm': [0, 0, 0], 'materials': [], 'pieces': None}
        probe = {'parts': [{**part, 'id': 1, 'object': 'Body'}, {**part, 'id': 2, 'object': 'Door'}], 'objects': [], 'views': [],
                 'unit_mm': 1000.0, 'bbox_mm': [[0, 0, 0], [1, 1, 1]]}
        solved = {'joints': [{'name': 'door', 'type': 'hinge', 'max': 110.0, 'min': 0.0}],
                  'previews': [{'state': 'closed', 'image': 'a.png', 'other': 'b.png', 'measured': {'crossing': 'none', 'moved_away_mm': None}},
                               {'state': 'open', 'image': 'c.png', 'other': 'd.png', 'measured': measured}]}
        verdict = {'ok': False, 'fix': 'door', 'states': [{'id': 'closed', 'ok': True, 'issue': '', 'fault': 'none'}, *verdict_rows]}
        asked = []; answers = iter([answer_, copy.deepcopy(verdict), answer_, copy.deepcopy(verdict)])
        import brandkit_review, components
        def ask(config, system, facts, *args, **kwargs):
            asked.append(facts); return next(answers)
        with patch.object(articulation, 'get', return_value=project), patch.object(articulation, 'key_of', return_value='k'), \
                patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'v'})), patch.object(brandkit_review, '_ask', side_effect=ask), \
                patch.object(components, 'run_blender', side_effect=lambda script, request, *a, **k: probe if request['action'] == 'probe' else solved), \
                patch.object(articulation, 'labelled_sheet', return_value='parts.png'), patch.object(articulation, 'state_sheet', return_value='states.png'), \
                patch.object(articulation, '_store', side_effect=lambda pid, record: record):
            return articulation.analyse('p', force=True), asked

    def test_a_door_measured_to_open_outward_is_not_failed_for_seeming_to_swing_inward(self):
        seen = [{'id': 'open', 'ok': False, 'issue': '右门向内旋转并穿入柜体', 'fault': 'wrong_direction'}]
        record, asked = self.analysed(seen, {'crossing': 'at the hinge only', 'moved_away_mm': 323.2})
        self.assertEqual(record['status'], 'ready')
        opened = record['verdict']['states'][1]
        self.assertTrue(opened['ok']); self.assertIn('向内', opened['overruled'])
        self.assertEqual(asked[1]['states'][1]['measured'], {'crossing': 'at the hinge only', 'moved_away_mm': 323.2})      # the check was told
        self.assertEqual(len(asked), 2)                                                                                 # and not asked again

    def test_a_part_that_really_passes_through_another_or_comes_off_is_still_failed(self):
        through = [{'id': 'open', 'ok': False, 'issue': '门穿过柜体', 'fault': 'passes_through'}]
        self.assertEqual(self.analysed(through, {'crossing': 'yes', 'moved_away_mm': 160.7})[0]['status'], 'failed')
        inward = [{'id': 'open', 'ok': False, 'issue': '向内', 'fault': 'wrong_direction'}]
        self.assertEqual(self.analysed(inward, {'crossing': 'none', 'moved_away_mm': -40.0})[0]['status'], 'failed')
        detached = [{'id': 'open', 'ok': False, 'issue': '把手留在原处', 'fault': 'detached'}]
        self.assertEqual(self.analysed(detached, {'crossing': 'none', 'moved_away_mm': 323.2})[0]['status'], 'failed')

    def test_the_check_sees_each_state_from_both_front_corners(self):
        from PIL import Image
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        for name in ('a.png', 'a2.png', 'b.png'):
            Image.new('RGB', (64, 64), 'white').save(folder / name)
        sheet = articulation.state_sheet(folder, [{'state': 'closed', 'image': 'a.png', 'other': 'a2.png'}, {'state': 'open', 'image': 'b.png'}],
                                         [{'id': 'closed', 'label_en': 'Closed'}, {'id': 'open', 'label_en': 'Open'}], folder / 'sheet.png')
        with Image.open(sheet) as image:
            self.assertEqual(image.size, (64 + 16 + 64, 128))          # two views under each other where there are two
        self.assertIn('edge-on', articulation.VERIFIER); self.assertIn('measured', articulation.VERIFIER)
        self.assertIn('fault', articulation._verify_schema()['properties']['states']['items']['required'])


class HintTests(unittest.TestCase):
    def test_a_scene_hint_that_asks_for_a_pose_is_recognised(self):
        for hint in ('车间/车房的工具柜，拉开前两个抽屉', '打开柜门', 'lid open on the bench', 'drawers pulled out'):
            self.assertTrue(articulation.asks_for_pose(hint), hint)
        for hint in ('车间/车房的工具柜', 'on a workbench at dusk', ''):
            self.assertFalse(articulation.asks_for_pose(hint), hint)

    RECORD = {'status': 'ready', 'key': 'k', 'usage': {'en': []},
              'joints': [{'name': 'd1', 'type': 'slide', 'axis': [0, 1, 0], 'sign': -1, 'max': 250, 'moving': [{'object': 'A', 'pieces': None}]}],
              'states': [{'id': 'closed', 'label_en': 'Closed', 'role': 'rest', 'values': {'d1': 0}, 'matches_scene_hint': False},
                         {'id': 'top2', 'label_en': 'Top two open', 'role': 'working', 'values': {'d1': 180}, 'matches_scene_hint': False},
                         {'id': 'all', 'label_en': 'All open', 'role': 'open', 'values': {'d1': 250}, 'matches_scene_hint': False}]}

    def shown(self, record, roles=('use', 'place', 'other')):
        import scene_audience, scene_stylist
        project = {'id': 'p', 'name': 'Cabinet', 'articulation': record}
        first = {'scene': 'x', 'segment_id': 'workshop_hobbyist', 'people': True, 'framing': 'hands'}
        with patch('studio_store.get', return_value=project), patch.object(scene_stylist, 'propose', return_value=first), \
                patch.object(scene_stylist, '_source_image', return_value=None), patch.object(scene_audience, 'rank', return_value=['site_team']):
            briefs = brandkit_set.scene_briefs(project, {'hint': '', 'audience': 'auto', 'people': 'auto'}, len(roles))
        return [(brief['role'], (brief['proposal'].get('articulation') or {}).get('state')) for brief in briefs]

    def test_a_set_of_scenes_shows_the_product_closed_and_open_by_default(self):
        """Drawers, doors, lids: the scenes do not all show one state. At work it is open the working way, at rest it is closed
        (no articulation: the model as delivered), and the third picture shows it fully open."""
        self.assertEqual(self.shown(self.RECORD), [('use', 'top2'), ('place', None), ('other', 'all')])
        two = {**self.RECORD, 'states': self.RECORD['states'][:2]}                     # only closed and one open state
        self.assertEqual(self.shown(two), [('use', 'top2'), ('place', None), ('other', 'top2')])
        self.assertEqual(self.shown({'status': 'none'}), [('use', None), ('place', None), ('other', None)])

    def test_a_pose_asked_for_in_the_hint_is_shown_at_work_and_the_product_is_still_shown_closed(self):
        asked = copy.deepcopy(self.RECORD); asked['states'][2]['matches_scene_hint'] = True
        self.assertEqual(self.shown(asked), [('use', 'all'), ('place', None), ('other', 'all')])
        project = {'id': 'p', 'articulation': asked}
        with patch('studio_store.get', return_value=project):                          # one scene on its own (the single-scene tool)
            brief = brandkit_set._with_moving_parts(project, {'role': 'use', 'proposal': {'scene': 'x'}})
        self.assertEqual(brief['proposal']['articulation']['state'], 'all')

    def test_scenes_of_a_product_with_moving_parts_are_made_again_once(self):
        product = {'title': 'Cabinet'}; settings = {'hint': ''}
        still = {'id': 'p', 'model_path': 'm.glb', 'plan': {}}
        moving = {**still, 'articulation': self.RECORD}
        with patch.object(listing_scenes, 'POSES_SHOWN', listing_scenes.POSES_SHOWN + 1):
            changed = (brandkit_set.scene_key(still, product, settings), brandkit_set.scene_key(moving, product, settings))
        self.assertEqual(changed[0], brandkit_set.scene_key(still, product, settings))          # nothing moves: its scenes are kept
        self.assertNotEqual(changed[1], brandkit_set.scene_key(moving, product, settings))


class PromptTests(unittest.TestCase):
    def test_the_image_model_may_not_open_any_part_itself(self):
        import scene_imagegen
        text = scene_imagegen.prompt('rolling tool cabinet', 900, 'A garage, drawers pulled out.', people=False)
        self.assertIn('never open, pull out, close, fold or move any of them', text)
        self.assertIn('open or closed exactly as in the references', scene_imagegen.FIDELITY_PROMPT)


class StructuralGuardTests(unittest.TestCase):
    def _cabinet_probe(self):
        return {
            'bbox_mm': [[0.0, 0.0, 0.0], [900.0, 355.0, 450.0]],
            'parts': [
                {'id': 3, 'object': 'G-01D右门', 'size_mm': [410.0, 27.0, 416.0], 'centroid_mm': [680.0, 15.0, 225.0], 'materials': ['red']},
                {'id': 9, 'object': 'G-01D左门', 'size_mm': [410.0, 26.0, 416.0], 'centroid_mm': [220.0, 15.0, 225.0], 'materials': ['red']},
                {'id': 12, 'object': 'G-01D盖子', 'size_mm': [900.0, 355.0, 40.0], 'centroid_mm': [450.0, 177.0, 430.0], 'materials': ['red']},
                {'id': 20, 'object': 'G-01D门铝条', 'size_mm': [34.0, 31.0, 414.0], 'centroid_mm': [415.0, 12.0, 225.0], 'materials': ['alu']},
                {'id': 21, 'object': 'G-01D门铝条_2', 'size_mm': [34.0, 31.0, 414.0], 'centroid_mm': [485.0, 12.0, 225.0], 'materials': ['alu']},
                {'id': 26, 'object': 'M4X6自攻螺丝_1', 'size_mm': [11.0, 11.0, 13.0], 'centroid_mm': [40.0, 320.0, 430.0], 'materials': ['steel']},
                {'id': 27, 'object': 'M4X6自攻螺丝_2', 'size_mm': [11.0, 11.0, 13.0], 'centroid_mm': [860.0, 20.0, 430.0], 'materials': ['steel']},
            ],
        }

    def test_bolted_cabinet_top_is_rejected_and_front_doors_are_identified(self):
        probe = self._cabinet_probe()
        info = articulation._structural_analysis(probe)
        self.assertEqual([t['id'] for t in info['fixed_top_panels']], [12])
        by_id = {d['id']: d for d in info['front_doors']}
        self.assertEqual(by_id[9]['hinge_side'], '-x')
        self.assertEqual(by_id[3]['hinge_side'], '+x')
        self.assertEqual(by_id[9]['attached_parts'], [20])
        self.assertEqual(by_id[3]['attached_parts'], [21])
        bad = {'articulated': True, 'joints': [{'name': 'lid', 'type': 'hinge', 'moving': [12]}], 'states': []}
        fault = articulation._structural_fault(bad, probe)
        self.assertIn('part 12', fault)
        self.assertIn('front doors', fault)
        good = {'articulated': True, 'joints': [
            {'name': 'left_door', 'type': 'hinge', 'moving': [9, 20]},
            {'name': 'right_door', 'type': 'hinge', 'moving': [3, 21]},
        ], 'states': []}
        self.assertEqual(articulation._structural_fault(good, probe), '')

    def test_true_unfastened_toolbox_lid_is_not_blocked(self):
        probe = {
            'bbox_mm': [[0.0, 0.0, 0.0], [500.0, 250.0, 220.0]],
            'parts': [
                {'id': 1, 'object': 'toolbox_body', 'size_mm': [500.0, 250.0, 180.0], 'centroid_mm': [250.0, 125.0, 90.0], 'materials': ['red']},
                {'id': 2, 'object': 'toolbox_lid', 'size_mm': [500.0, 250.0, 40.0], 'centroid_mm': [250.0, 125.0, 200.0], 'materials': ['red']},
            ],
        }
        lid_answer = {'articulated': True, 'joints': [{'name': 'lid', 'type': 'hinge', 'moving': [2]}], 'states': []}
        self.assertEqual(articulation._structural_fault(lid_answer, probe), '')


@unittest.skipUnless(BLENDER and Path(BLENDER).is_file(), 'Blender is not installed')
class AttachmentPropagationTests(unittest.TestCase):
    """Hidden back-side stiffeners, brackets and multi-hop plaques welded to a moving door follow it automatically,
    while fixed cabinet rails bolted to the carcass stay behind."""

    MAKE = '''
import bpy, sys
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
def box(name, center, size):
    bpy.ops.mesh.primitive_cube_add(location=center)
    ob = bpy.context.object; ob.name = name
    ob.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return ob
# Cabinet carcass: 0.90 x 0.35 x 0.45 m
box('Floor', (0, 0, -0.22), (0.90, 0.35, 0.02))
box('Roof', (0, 0, 0.22), (0.90, 0.35, 0.02))
box('Back', (0, 0.165, 0), (0.90, 0.02, 0.44))
box('SideLeft', (-0.44, 0, 0), (0.02, 0.35, 0.44))
box('SideRight', (0.44, 0, 0), (0.02, 0.35, 0.44))
# Fixed bottom door-stop rail bolted to the floor (NOT touching doors)
box('BottomSill', (0, -0.145, -0.202), (0.86, 0.015, 0.016))
# Two front doors
box('LeftDoor', (-0.215, -0.175, 0), (0.42, 0.02, 0.41))
box('RightDoor', (0.215, -0.175, 0), (0.42, 0.02, 0.41))
# Back-side astragal/stiffener bar welded flush onto inner edge of LeftDoor (y=-0.165 touches LeftDoor back face)
box('LeftDoorStiffener', (-0.010, -0.162, 0), (0.032, 0.006, 0.40))
# Door-mounted hinge brackets welded to backs of LeftDoor & RightDoor
box('LeftBracket', (-0.40, -0.162, 0.17), (0.02, 0.006, 0.05))
box('RightBracket', (0.40, -0.162, 0.17), (0.02, 0.006, 0.05))
# Multi-hop badge: Backing welded to LeftDoor front, Text welded to Backing
box('LogoBacking', (-0.30, -0.188, 0.12), (0.14, 0.006, 0.04))
box('LogoText', (-0.30, -0.193, 0.12), (0.10, 0.004, 0.02))
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format='GLB')
'''

    def test_backside_stiffeners_brackets_and_multihop_hardware_follow_bare_doors(self):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        model = folder / 'cabinet.glb'
        script = folder / 'make.py'; script.write_text(self.MAKE)
        subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(script), '--', str(model)], capture_output=True, timeout=300, check=True)
        def run_art(req, sub):
            out = folder / sub; out.mkdir(exist_ok=True)
            (out / 'req.json').write_text(json.dumps({**req, 'model_path': str(model), 'output_dir': str(out)}))
            subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(ROOT / 'blender' / 'articulate.py'), '--', str(out / 'req.json')],
                           capture_output=True, timeout=300, check=True)
            return json.loads((out / 'result.json').read_text())
        probe = run_art({'action': 'probe', 'size': 128, 'size_mm': 900}, 'probe')
        ids = {p['object']: p['id'] for p in probe['parts']}
        # Deliberately pass ONLY the bare door panels (simulating vision model missing occluded back-side hardware).
        joints = [
            {'name': 'left', 'type': 'hinge', 'moving': [ids['LeftDoor']], 'counter': [], 'stretch': [], 'axis': 'z',
             'pivot': {'kind': 'edge', 'part': None, 'side': '-x'}, 'away_from': ids['Back'], 'max': 95},
            {'name': 'right', 'type': 'hinge', 'moving': [ids['RightDoor']], 'counter': [], 'stretch': [], 'axis': 'z',
             'pivot': {'kind': 'edge', 'part': None, 'side': '+x'}, 'away_from': ids['Back'], 'max': 95},
        ]
        states = [{'id': 'closed', 'values': {'left': 0, 'right': 0}}, {'id': 'open', 'values': {'left': 95, 'right': 95}}]
        solved = run_art({'action': 'solve', 'size': 128, 'size_mm': 900, 'joints': joints, 'states': states}, 'solve')
        self.assertTrue(solved['success'], solved.get('error'))
        left = {m['object'] for m in next(j for j in solved['joints'] if j['name'] == 'left')['moving']}
        right = {m['object'] for m in next(j for j in solved['joints'] if j['name'] == 'right')['moving']}
        self.assertEqual(left, {'LeftDoor', 'LeftDoorStiffener', 'LeftBracket', 'LogoBacking', 'LogoText'})
        self.assertEqual(right, {'RightDoor', 'RightBracket'})
        for fixed_name in ('Floor', 'Roof', 'Back', 'SideLeft', 'SideRight', 'BottomSill'):
            self.assertNotIn(fixed_name, left)
            self.assertNotIn(fixed_name, right)

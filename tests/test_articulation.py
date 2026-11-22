"""The pose agent: what the vision model may say about moving parts, how a state becomes a render plan, and the
geometry it is solved with (when Blender is installed)."""
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


class HintTests(unittest.TestCase):
    def test_a_scene_hint_that_asks_for_a_pose_is_recognised(self):
        for hint in ('车间/车房的工具柜，拉开前两个抽屉', '打开柜门', 'lid open on the bench', 'drawers pulled out'):
            self.assertTrue(articulation.asks_for_pose(hint), hint)
        for hint in ('车间/车房的工具柜', 'on a workbench at dusk', ''):
            self.assertFalse(articulation.asks_for_pose(hint), hint)

    def test_the_hinted_state_holds_in_every_scene(self):
        record = {'status': 'ready', 'key': 'k', 'usage': {'en': []},
                  'joints': [{'name': 'd1', 'type': 'slide', 'axis': [0, 1, 0], 'sign': -1, 'max': 250, 'moving': [{'object': 'A', 'pieces': None}]}],
                  'states': [{'id': 'closed', 'label_en': 'Closed', 'role': 'rest', 'values': {'d1': 0}, 'matches_scene_hint': False},
                             {'id': 'top2', 'label_en': 'Top two open', 'role': 'open', 'values': {'d1': 180}, 'matches_scene_hint': True},
                             {'id': 'all', 'label_en': 'All open', 'role': 'working', 'values': {'d1': 250}, 'matches_scene_hint': False}]}
        project = {'id': 'p', 'articulation': record}
        with patch('studio_store.get', return_value=project):
            for role in ('use', 'place', 'other'):
                brief = brandkit_set._with_moving_parts(project, {'role': role, 'proposal': {'scene': 'x'}})
                self.assertEqual(brief['proposal']['articulation']['state'], 'top2', role)


class PromptTests(unittest.TestCase):
    def test_the_image_model_may_not_open_any_part_itself(self):
        import scene_imagegen
        text = scene_imagegen.prompt('rolling tool cabinet', 900, 'A garage, drawers pulled out.', people=False)
        self.assertIn('never open, pull out, close, fold or move any of them', text)
        self.assertIn('open or closed exactly as in the references', scene_imagegen.FIDELITY_PROMPT)

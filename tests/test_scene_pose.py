"""The product's pose in a generated scene: candidates, the vision model's choice and every way back to the plan's own pose."""
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orientation
import scene_lock
import scene_pose


def axis_after(rotation, axis):
    """Where one of the model's own axes points once ``rotation`` is applied."""
    m = orientation.matrix(rotation)
    return [round(m[row][axis], 6) for row in range(3)]


class RotationTests(unittest.TestCase):
    def test_about_turns_right_handed_about_any_axis(self):
        self.assertEqual(axis_after(orientation.about((0, 0, 1), 90), 0), [0, 1, 0])
        self.assertEqual(axis_after(orientation.about((1, 0, 0), 90), 2), [0, -1, 0])
        tilted = orientation.about((1, 1, 0), 180)
        self.assertEqual(axis_after(tilted, 0), [0, 1, 0]); self.assertEqual(axis_after(tilted, 2), [0, 0, -1])
        self.assertEqual(orientation.about((0, 0, 0), 45), [0.0, 0.0, 0.0])

    def test_the_ways_up_read_as_the_camera_sees_them(self):
        # Camera straight ahead (azimuth 0, on the -Y side): its right is +X.
        poses = {ident: rotation for ident, _, rotation in scene_pose.first_poses([0, 0, 0], 0)}
        self.assertEqual(axis_after(poses['as_is@0'], 2), [0, 0, 1])
        self.assertEqual(axis_after(poses['upside_down@0'], 2), [0, 0, -1])
        self.assertEqual(axis_after(poses['top_left@0'], 2), [-1, 0, 0])          # rolled in the picture, top to the left
        self.assertEqual(axis_after(poses['top_right@0'], 2), [1, 0, 0])
        self.assertEqual(axis_after(poses['top_to_camera@0'], 2), [0, -1, 0])     # the top faces the camera
        self.assertEqual(axis_after(poses['top_away@0'], 2), [0, 1, 0])
        # Each way up again, turned a quarter about the vertical: twelve different poses.
        self.assertEqual(len(poses), 12)
        rows = list(poses.values())
        self.assertFalse(any(orientation.same(a, b) for index, a in enumerate(rows) for b in rows[index + 1:]))

    def test_the_ways_up_follow_the_camera_and_start_from_the_plans_pose(self):
        base = [180.0, 0.0, 0.0]
        poses = {ident: rotation for ident, _, rotation in scene_pose.first_poses(base, 35)}
        self.assertTrue(orientation.same(poses['as_is@0'], base))
        # Rolled about the line to a camera at 35 degrees: the model's top ends up level, across the picture to the left.
        top = axis_after(poses['top_left@0'], 2); left = [-math.cos(math.radians(35)), -math.sin(math.radians(35)), 0]
        self.assertAlmostEqual(sum(a * b for a, b in zip(top, axis_after(base, 2))), 0, places=6)
        self.assertAlmostEqual(sum(a * b for a, b in zip(axis_after(scene_pose.moved([0, 0, 0], 35, 'toward', 90), 2), left)), 1, places=6)

    def test_adjustments_are_small_changes_of_the_chosen_pose(self):
        chosen = scene_pose.moved([0, 0, 0], 0, 'toward', 90)
        rows = scene_pose.adjusted_poses(chosen, 0)
        self.assertEqual(len(rows), len(scene_pose.ADJUSTMENTS)); self.assertLessEqual(len(rows), 12)
        self.assertEqual(rows[0][0], 'chosen'); self.assertTrue(orientation.same(rows[0][2], chosen))
        lean = dict((ident, rotation) for ident, _, rotation in rows)['lean_left']
        top = axis_after(lean, 2)
        self.assertAlmostEqual(math.degrees(math.acos(max(-1, min(1, sum(a * b for a, b in zip(top, axis_after(chosen, 2))))))), 25, places=4)

    def test_a_product_set_down_is_told_only_when_it_clearly_falls_over(self):
        rows = [{'id': 'as_is@0', 'tip_angle': -20.0, 'dimensions': [1, 1, 3]}, {'id': 'top_left@0', 'tip_angle': .7, 'dimensions': [3, 1, 1]},
                {'id': 'top_away@0', 'tip_angle': -43.6, 'dimensions': [1, 3, 1]}, {'id': 'upside_down@0', 'tip_angle': None}]
        facts = scene_pose.rest_facts(rows)
        self.assertEqual([fact['tips_over'] for fact in facts], [False, False, True, False])          # its own pose is never ruled out
        self.assertEqual([fact['cell'] for fact in facts], ['A', 'B', 'C', 'D'])
        self.assertEqual((facts[0]['stance'], facts[1]['stance']), ('stands tall', 'lies level'))
        self.assertEqual([ident for ident, _, _ in scene_pose.first_poses([0, 0, 0], 20, ['as_is'], (0, 90))], ['as_is@0', 'as_is@90'])


# Measured by blender/orient.py on 06_diagonal_cutting_pliers.glb, the twelve resting candidates of scene_pose (fix4/scenes/rest.jpg):
# (id, height, flat_contact, tip_angle). Lying on either face is 0.245 tall; everything else stands the pliers on points.
PLIERS = (('as_is@0', .245, .0152, 58.77), ('as_is@90', .245, .0152, 58.68), ('upside_down@0', .245, .001, -69.33),
          ('upside_down@90', .245, .001, -69.33), ('top_left@0', 1.592, 0.0, -45.82), ('top_left@90', 1.592, 0.0, -43.96),
          ('top_right@0', 1.592, .0023, -19.74), ('top_right@90', 1.592, .0023, -19.74), ('top_to_camera@0', 1.864, .003, -2.6),
          ('top_to_camera@90', 1.864, .003, -2.83), ('top_away@0', 1.864, 0.0, -28.18), ('top_away@90', 1.864, 0.0, -27.57))


class StableTests(unittest.TestCase):
    def rows(self, table):
        return [{'id': ident, 'dimensions': [1.9, 1.3, height], 'flat_contact': flat, 'tip_angle': tip} for ident, height, flat, tip in table]

    def test_pliers_may_only_lie_on_a_face_never_stand_on_a_jaw_tip_and_a_handle_end(self):
        allowed = scene_pose.stable(self.rows(PLIERS))
        self.assertEqual([ident for (ident, _, _, _), ok in zip(PLIERS, allowed) if ok], ['as_is@0', 'as_is@90', 'upside_down@0', 'upside_down@90'])
        # top_to_camera leans only 2.6 degrees, which the old tips_over rule (-15) let through: the user's picture.
        self.assertFalse(scene_pose.rest_facts(self.rows(PLIERS))[8]['tips_over'])

    def test_a_tall_pose_rests_on_a_flat_base_with_its_weight_inside_and_the_main_pose_always_does(self):
        table = (('as_is@0', 2.0, 0.0, -40.0),                  # the main image's own pose (a drill on its battery): kept
                 ('upside_down@0', 1.0, .0, -30.0),             # lying: settles onto a side
                 ('top_left@0', 1.0 * orientation.STANDING + .01, .36, 24.0),     # tall, on a flat foot, weight well inside: a tape measure
                 ('top_right@0', 3.0, .36, 2.0),                # flat foot but the weight near its edge
                 ('top_away@0', 3.0, .05, 30.0),                # weight inside, but only points on the floor
                 ('top_to_camera@0', None, None, None))         # not measured while others were
        rows = self.rows(table); rows[-1]['dimensions'] = None
        self.assertEqual(scene_pose.stable(rows), [True, True, True, False, False, False])
        unmeasured = [{'id': 'top_left@0'}, {'id': 'top_away@0', 'dimensions': None}]
        self.assertEqual(scene_pose.stable(unmeasured), [True, True])          # nothing measured: nothing is ruled out

    def test_a_main_pose_standing_a_thin_tool_on_its_edge_is_not_trusted(self):
        # Measured on 15_hand_saw.glb: its main image stood on the teeth (0.479 tall) against 0.063 lying flat.
        saw = (('as_is@0', .479, .041, -7.6), ('as_is@90', .479, .041, -7.6),
               ('upside_down@0', .063, .002, -82.6), ('upside_down@90', .063, .002, -82.6))
        self.assertEqual(scene_pose.stable(self.rows(saw)), [False, False, True, True])
        # A drill on its battery (12_cordless_drill_red.glb: 2.0 tall against 0.459) is still its own main pose.
        drill = (('as_is@0', 2.0, .103, -9.3), ('top_left@0', .459, .035, -44.3))
        self.assertEqual(scene_pose.stable(self.rows(drill)), [True, True])


class DecideTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        mocked = patch.object(scene_pose, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        self.project = {'id': 'p1', 'model_path': str(self.data / 'model.glb'),
                        'analysis': {'objects': [{'name': 'disc'}, {'name': 'battery'}, {'name': 'body'}]}}
        self.plan = {'rotation': [180, 0, 0]}
        self.pictured = []; self.answers = []; self.asked = []

    def picture(self, project, plan, poses, folder, cancelled=None, view=None, floor=True, size=orientation.SIZE, samples=16, render=True):
        self.pictured.append({'ids': [ident for ident, _, _ in poses], 'view': view, 'floor': floor, 'render': render})
        folder.mkdir(parents=True, exist_ok=True); rows = []
        for index, (ident, label, rotation) in enumerate(poses):
            path = None
            if render:
                path = folder / ('pose_%d.png' % index); Image.new('RGB', (32, 32), (index * 9, 0, 0)).save(path)
            rows.append({'id': ident, 'label': label, 'rotation': list(map(float, rotation)), 'path': path,
                         'tip_angle': -30.0 if ident.startswith('top_left') else 20.0, 'dimensions': [1, 1, 2]})
        return rows

    def look(self, config, system, facts, schema, image, max_tokens=900):
        self.asked.append(facts)
        reply = self.answers.pop(0)
        letters = facts['cells']
        return {'product': 'angle grinder', 'working_part': 'disc', 'use': 'held level, disc down onto the steel', 'pose': 'held level, disc down',
                'contact': 'the disc, lower left', 'work_side': 'below', 'reason': '角磨机工作时机身水平。',
                'cells': [{'cell': letter, 'working_part_points': 'down', 'fits': letter == reply} for letter in letters], 'choice': reply}

    def decide(self, hold='hands', mode='auto', vision=True):
        with patch.object(orientation, 'picture', side_effect=self.picture), patch.object(orientation, '_look', side_effect=self.look), \
                patch.object(orientation, '_vision', return_value={'model': 'vision-x', 'provider': 'openai'} if vision else None):
            return scene_pose.decide(self.project, self.plan, 30, 20, 'Grinding a weld on a steel plate.', 'angle grinder', hold, mode=mode, hint='weld')

    def test_a_held_product_is_chosen_from_twelve_poses_then_adjusted(self):
        self.answers = ['E', 'C']               # top to the right, then leaning left
        pose = self.decide()
        self.assertEqual([len(row['ids']) for row in self.pictured], [12, 10])
        self.assertEqual([(row['view'], row['floor']) for row in self.pictured], [((30.0, 20.0), False), ((30.0, 20.0), False)])   # held: no floor
        first = scene_pose.first_poses([180.0, 0.0, 0.0], 30)[4][2]
        self.assertTrue(orientation.same(pose['base_rotation'], [180, 0, 0]))
        expected = scene_pose.adjusted_poses(first, 30)[2][2]
        self.assertTrue(orientation.same(pose['rotation'], expected))
        self.assertEqual((pose['source'], pose['changed'], pose['candidate'], pose['work_side'], pose['contact']),
                         ('ai', True, 'lean_left', 'below', 'the disc, lower left'))
        self.assertEqual(pose['description'], 'held level, disc down'); self.assertTrue(pose['consistent'])
        self.assertEqual([facts['people'] for facts in self.asked], ['hands', 'hands'])
        self.assertEqual(self.asked[0]['part_names'], ['disc', 'battery', 'body']); self.assertEqual(self.asked[0]['hint'], 'weld')
        self.assertIn('every way up', self.asked[0]['cells_are']); self.assertIn('small changes', self.asked[1]['cells_are'])
        self.assertEqual(self.asked[1]['first_choice'], 'held level, disc down onto the steel')
        self.assertEqual(len(pose['sheets']), 2); self.assertTrue(all(url.startswith('/files/p1/scene_ai/poses/') for url in pose['sheets']))

    def test_a_product_set_down_is_shown_on_a_floor_with_what_was_measured_and_not_adjusted(self):
        self.answers = ['B']
        pose = self.decide(hold='nobody')
        self.assertEqual(len(self.pictured), 1)                                                     # one step: a tilted product set down would fall
        self.assertEqual((len(self.pictured[0]['ids']), self.pictured[0]['floor'], self.pictured[0]['render']), (12, True, True))
        facts = self.asked[0]
        self.assertEqual(facts['people'], 'nobody'); self.assertIn('set down on a surface', facts['cells_are'])
        self.assertEqual(len(facts['cell_facts']), 12); self.assertIn('tips_over', facts['cell_facts'][0])
        self.assertEqual((pose['source'], pose['changed'], pose['candidate']), ('ai', True, 'as_is@90'))
    def test_a_product_set_down_is_only_offered_the_poses_it_can_rest_in(self):
        measured = dict((ident, (height, flat, tip)) for ident, height, flat, tip in PLIERS)
        def picture(project, plan, poses, folder, cancelled=None, view=None, floor=True, size=orientation.SIZE, samples=16, render=True):
            rows = self.picture(project, plan, poses, folder, cancelled, view, floor, size, samples, render)
            for row in rows:
                height, flat, tip = measured.get(row['id'], (.245, .01, 10.0))
                row.update(dimensions=[1.9, 1.3, height], flat_contact=flat, tip_angle=tip)
            return rows
        self.answers = ['C']
        with patch.object(orientation, 'picture', side_effect=picture), patch.object(orientation, '_look', side_effect=self.look), \
                patch.object(orientation, '_vision', return_value={'model': 'vision-x', 'provider': 'openai'}):
            pose = scene_pose.decide(self.project, self.plan, 30, 20, 'Pliers on a bench.', 'diagonal cutting pliers', 'nobody')
        facts = self.asked[0]
        self.assertEqual(facts['cells'], ['A', 'B', 'C', 'D'])                  # twelve rendered, four shown
        self.assertEqual(len(facts['cell_facts']), 4)
        self.assertIn('poses that would balance it on a tip, an edge, its jaws or a handle end are not shown', facts['cells_are'])
        self.assertEqual(pose['candidate'], 'upside_down@0')
        self.assertEqual(pose['refused'], [ident for ident, _, _, _ in PLIERS[4:]])
        with Image.open(self.data / 'p1' / 'scene_ai' / 'poses' / next((self.data / 'p1' / 'scene_ai' / 'poses').iterdir()).name / 'sheet.png') as sheet:
            self.assertEqual(sheet.size[1], scene_pose.SIZE + 20)               # one row of four cells
        # Held, every way up is still offered: a hand holds it in the air.
        self.answers = ['K', 'A']; self.asked = []
        with patch.object(orientation, 'picture', side_effect=picture), patch.object(orientation, '_look', side_effect=self.look), \
                patch.object(orientation, '_vision', return_value={'model': 'vision-x', 'provider': 'openai'}):
            held = scene_pose.decide(self.project, self.plan, 30, 20, 'Cutting a wire.', 'diagonal cutting pliers', 'hands')
        self.assertEqual(len(self.asked[0]['cells']), 12); self.assertEqual(held['source'], 'ai'); self.assertNotIn('refused', held)

    def test_the_pose_agent_is_told_how_hand_tools_are_held_and_set_down(self):
        for words in ('pliers and cutters are gripped by both handles, the wire deep in the jaws near the pivot',
                      'a hammer is gripped by the lower half of its handle', 'never balanced on a tip, an edge, its jaws or a handle end'):
            self.assertIn(words, scene_pose.POSE_PROMPT)

    def test_the_plans_pose_is_kept_when_asked_or_when_no_model_can_choose(self):
        kept = self.decide(mode='keep')
        self.assertEqual((kept['source'], kept['changed'], kept['rotation'], kept['description']), ('user', False, [180.0, 0.0, 0.0], None))
        unseen = self.decide(vision=False)
        self.assertEqual((unseen['source'], unseen['changed']), ('default', False))
        self.assertEqual(self.pictured, [])                                                         # nothing was rendered for either

    def test_a_failure_anywhere_keeps_the_plans_pose_and_never_fails_the_scene(self):
        def broken(*args, **kwargs):
            raise ValueError('Blender crashed')
        with patch.object(orientation, 'picture', side_effect=broken), patch.object(orientation, '_vision', return_value={'model': 'v'}):
            pose = scene_pose.decide(self.project, self.plan, 30, 20, 'scene', 'grinder', 'hands')
        self.assertEqual((pose['source'], pose['changed'], pose['rotation']), ('default', False, [180.0, 0.0, 0.0]))
        self.assertIn('Blender crashed', pose['error'])
        self.answers = []                                                                            # the vision model gives no answer
        with patch.object(orientation, 'picture', side_effect=self.picture), patch.object(orientation, '_vision', return_value={'model': 'v'}), \
                patch.object(orientation, '_look', side_effect=ValueError('timeout')):
            self.assertEqual(scene_pose.decide(self.project, self.plan, 30, 20, 'scene', 'grinder', 'hands')['source'], 'default')

    def test_a_cancellation_is_not_swallowed(self):
        with patch.object(orientation, 'picture', side_effect=InterruptedError()), patch.object(orientation, '_vision', return_value={'model': 'v'}):
            with self.assertRaises(InterruptedError):
                scene_pose.decide(self.project, self.plan, 30, 20, 'scene', 'grinder', 'hands')

    def test_renders_of_the_same_poses_are_reused(self):
        self.answers = ['A', 'A', 'A', 'A']
        self.decide(); self.decide()
        self.assertEqual(len(self.pictured), 2)                    # the second scene found both sheets already rendered

    def test_the_answer_schema_asks_for_every_cell_before_the_choice(self):
        schema = scene_pose.schema(10)
        order = list(schema['properties'])
        self.assertLess(order.index('cells'), order.index('choice'))
        self.assertEqual(schema['properties']['cells']['minItems'], 10)
        self.assertEqual(schema['properties']['choice']['enum'], list('ABCDEFGHIJ'))
        self.assertEqual(schema['properties']['work_side']['enum'], list(scene_pose.SIDES))


class AnchorRoomTests(unittest.TestCase):
    def test_the_product_moves_away_from_the_side_its_work_lies_on(self):
        cutout = Image.new('RGBA', (200, 100), (0, 0, 0, 0)); cutout.paste((200, 30, 30, 255), (20, 20, 180, 80))
        _, _, plain = scene_lock.anchor(cutout, (1000, 1000), 'hands')
        _, _, below = scene_lock.anchor(cutout, (1000, 1000), 'hands', 'below')
        _, _, left = scene_lock.anchor(cutout, (1000, 1000), 'hands', 'left')
        _, _, unknown = scene_lock.anchor(cutout, (1000, 1000), 'hands', 'around')
        self.assertEqual(below[1], plain[1] - 70); self.assertEqual(below[0], plain[0])
        self.assertEqual(left[0], plain[0] + 70); self.assertEqual(unknown, plain)


if __name__ == '__main__':
    unittest.main()

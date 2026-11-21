"""Which way up a product stands: rotations, the choice of pose, and how it reaches the plan.

Blender and the vision model are replaced except in the runtime class at the
end, which runs blender/orient.py on a real model when Blender is installed.
"""
import copy
import itertools
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import ai_config
import engine
import orientation
import planner
import studio_store as store
from test_scene_images import project

QUARTERS = (0, 90, 180, -90)


def rows(parts=None, folder=None, flat=None):
    """The six candidates as blender/orient.py would report them. ``flat`` gives the flat contact per candidate id."""
    result = []
    for ident, label, rotation in orientation.POSES:
        up = orientation.matrix(rotation)[2]            # which of the file's axes points up in this pose
        placed = []
        for name, position in (parts or []):
            height = round(sum(up[axis] * position[axis] for axis in range(3)), 3)
            middle = (height + 1) / 2                   # a small part: it reaches a little below and above its centre
            placed.append({'name': name, 'height': height, 'low': round(max(0.0, middle - .05), 3), 'high': round(min(1.0, middle + .05), 3)})
        row = {'id': ident, 'label': label, 'rotation': [float(value) for value in rotation], 'dimensions': [1.0, 1.0, 2.0],
               'support_ratio': .2, 'weight_height': .5, 'tip_angle': 10.0, 'flat_contact': (flat or {}).get(ident), 'parts': placed}
        if folder is not None:
            path = folder / ('pose_%s.png' % ident); path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (orientation.SIZE, orientation.SIZE), (200, 200, 210)).save(path)
            row['path'] = path
        result.append(row)
    return result


class RotationTests(unittest.TestCase):
    def test_every_quarter_turn_pose_survives_being_written_as_angles(self):
        for rotation in itertools.product(QUARTERS, repeat=3):
            with self.subTest(rotation=rotation):
                back = orientation.euler(orientation.matrix(rotation))
                self.assertTrue(orientation.same(rotation, back), back)
                self.assertTrue(all(-180 < value <= 180 and value == round(value) for value in back), back)
        self.assertTrue(orientation.same([30, 40, 50], orientation.euler(orientation.matrix([30, 40, 50]))))

    def test_the_matrix_is_the_one_blender_applies(self):
        # XYZ Euler: a turn about X carries the file's +Y to +Z; a turn about Y by -90 carries +X to +Z.
        self.assertEqual([round(value, 6) for value in orientation.matrix([90, 0, 0])[2]], [0, 1, 0])
        self.assertEqual([round(value, 6) for value in orientation.matrix([0, -90, 0])[2]], [1, 0, 0])
        self.assertEqual([round(value, 6) for value in orientation.matrix([180, 0, 0])[2]], [0, 0, -1])
        # The six candidates put six different sides on top.
        tops = {tuple(round(value) for value in orientation.matrix(rotation)[2]) for _, _, rotation in orientation.POSES}
        self.assertEqual(len(tops), 6)

    def test_a_turn_keeps_what_is_up_and_a_flip_reverses_it(self):
        for _, _, rotation in orientation.POSES:
            with self.subTest(rotation=rotation):
                for degrees in (90, -90, 180):
                    turned = orientation.turned(rotation, degrees)
                    self.assertTrue(orientation.same_up(rotation, turned)); self.assertFalse(orientation.same(rotation, turned))
                self.assertTrue(orientation.same(orientation.turned(orientation.turned(rotation, 90), -90), rotation))
                flipped = orientation.flipped(rotation)
                self.assertEqual([round(-value, 6) for value in orientation.matrix(rotation)[2]], [round(value, 6) for value in orientation.matrix(flipped)[2]])
                self.assertTrue(orientation.same(orientation.flipped(flipped), rotation))
        self.assertEqual(orientation.flipped([0, 0, 0]), [180.0, 0.0, 0.0])
        self.assertEqual(orientation.turned([180, 0, 0], 90), [180.0, 0.0, 90.0])

    def test_the_box_of_a_posed_model_follows_the_pose(self):
        self.assertEqual([round(value, 6) for value in orientation.oriented_dimensions([1, 2, 3], [90, 0, 0])], [1, 3, 2])
        self.assertEqual([round(value, 6) for value in orientation.oriented_dimensions([1, 2, 3], [0, 90, 0])], [3, 2, 1])
        self.assertEqual([round(value, 6) for value in orientation.oriented_dimensions([1, 2, 3], [180, 0, 90])], [2, 1, 3])
        self.assertTrue(orientation.valid_rotation([0, -90.0, 180])); self.assertFalse(orientation.valid_rotation([0, 0]))
        self.assertFalse(orientation.valid_rotation([0, 0, float('nan')])); self.assertFalse(orientation.valid_rotation([0, 0, 400]))

    def test_a_model_stood_upright_is_framed_as_it_stands(self):
        # A flat tray stored on its edge: in the file it looks tall, once laid down it is flat.
        analysis = {'features': {'dimensions': [2.0, .2, 2.0]}, 'objects': [], 'materials': [], 'warnings': []}
        as_filed = planner.enrich(planner.default_plan(), analysis)
        self.assertNotIn('auto_framing', as_filed)
        plan = planner.default_plan(); plan['rotation'] = [90, 0, 0]
        self.assertEqual(planner.enrich(plan, analysis)['auto_framing']['shape'], 'flat_long')


class NameTests(unittest.TestCase):
    CABINET = [('Body', (0, 0, 0)), ('Caster_FL', (-.8, -.8, .95)), ('Caster_FR', (.8, -.8, .95)), ('Drawer_1', (0, 0, -.3))]

    def test_wheels_on_top_mean_the_model_is_upside_down(self):
        row, reason = orientation.by_names(rows(self.CABINET))
        self.assertEqual(row['id'], 'flip'); self.assertIn('Caster_FL', reason); self.assertIn('底部', reason)

    def test_parts_that_already_sit_where_they_belong_change_nothing(self):
        upright = [(name, (x, y, -z)) for name, (x, y, z) in self.CABINET]
        self.assertIsNone(orientation.by_names(rows(upright)))

    def test_names_that_say_nothing_about_up_and_down_are_ignored(self):
        self.assertIsNone(orientation.by_names(rows([('Body', (0, 0, 0)), ('Handle', (0, 0, .9)), ('Battery_Cover', (0, 0, -.9))])))
        self.assertIsNone(orientation.by_names(rows([])))
        # A part near the middle is no evidence.
        self.assertIsNone(orientation.by_names(rows([('Base', (0, 0, .1))])))

    def test_whole_words_count_not_fragments(self):
        self.assertEqual(orientation._cue('Laptop_Shell'), 0); self.assertEqual(orientation._cue('TopPanel'), 1)
        self.assertEqual(orientation._cue('database'), 0); self.assertEqual(orientation._cue('Base.001'), -1)
        self.assertEqual(orientation._cue('脚轮_01'), -1); self.assertEqual(orientation._cue('顶盖'), 1)
        self.assertEqual(orientation._cue('top_base'), 0)        # says both, so says nothing


class VisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.rows = rows(folder=self.folder)
        self.sheet = orientation.sheet(self.rows, self.folder / 'sheet.png')
        self.vision = {'provider': 'anthropic', 'base_url': 'https://api.hai.network/anthropic', 'model': 'claude-sonnet-4-6', 'api_key': 'k', 'enabled': True}

    def reply(self, fits=None, **changes):
        chosen = changes.get('upright', 'B')
        value = {'product': 'cordless drill', 'rests_on': 'It stands upright on the flat underside of its battery pack.', 'carried_by': 'flat_underside',
                 'cells': [{'cell': letter, 'on_ground': 'the battery underside' if letter == chosen else 'the chuck', 'fits': letter == chosen if fits is None else letter in fits}
                           for letter in 'ABCDEF'],
                 'upright': chosen, 'also_normal': [], 'confident': True, 'reason': '电池底座在下。'}
        value.update(changes)
        return value

    def ask(self, reply, function=None, **options):
        sent = {}
        def post(config, body):
            sent['body'] = body
            if isinstance(reply, Exception): raise reply
            return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'submit_photography_patch', 'input': reply}]}
        with patch.object(ai_config, 'get_vision_config', return_value=self.vision), patch('ai_client._post', side_effect=post):
            call = function or (lambda: orientation.choose(self.rows, self.sheet, options.get('names', ())))
            return call(), sent.get('body')

    def test_the_sheet_shows_every_candidate_in_a_lettered_cell(self):
        with Image.open(self.sheet) as picture:
            self.assertEqual(picture.size, (3 * 320 + 4 * 10, 2 * 320 + 3 * 10))
            self.assertLess(sum(picture.getpixel((20, 20))), 150)          # the letter's dark tab, top left of cell A
            self.assertGreater(sum(picture.getpixel((200, 200))), 500)     # the pose picture itself

    def test_the_pose_agent_is_shown_the_sheet_and_the_measurements_and_its_confident_answer_is_taken(self):
        (row, source, reason, seen), body = self.ask(self.reply(), names=['Drill_01'])
        self.assertEqual((row['id'], source, reason, seen['product']), ('flip', 'vision', '电池底座在下。', 'cordless drill'))
        self.assertEqual((seen['rests_on'], seen['carried_by'], seen['model']), ('It stands upright on the flat underside of its battery pack.', 'flat_underside', 'claude-sonnet-4-6'))
        self.assertEqual([(cell['id'], cell['fits']) for cell in seen['cells']][:2], [('file', False), ('flip', True)])
        content = body['messages'][-1]['content']
        self.assertEqual([block['type'] for block in content], ['text', 'image', 'text'])
        facts = json.loads(content[-1]['text'])
        self.assertEqual(facts['cells'], list('ABCDEF'))
        self.assertEqual(facts['facts'][0], {'cell': 'A', 'stance': 'stands tall'})        # one anonymous mesh: nothing else was measured
        system = body['system'] if isinstance(body.get('system'), str) else json.dumps(body.get('system'))
        self.assertIn('set down properly', system); self.assertIn('flat underside of the battery pack', system)

    def test_the_answer_is_reasoned_in_order_before_a_cell_is_named(self):
        fields = list(orientation.pose_schema(6)['properties'])
        self.assertEqual(fields, ['product', 'rests_on', 'carried_by', 'cells', 'upright', 'also_normal', 'confident', 'reason'])
        self.assertEqual(orientation.pose_schema(6)['properties']['cells']['minItems'], 6)        # every cell is described, not only the chosen one

    def test_the_files_own_pose_is_left_only_on_a_confident_answer(self):
        (row, source, _, _), _ = self.ask(self.reply(upright='A'))
        self.assertEqual((row['id'], source), ('file', 'vision'))
        (row, source, reason, _), _ = self.ask(self.reply(confident=False))
        self.assertEqual((row['id'], source), ('file', 'file')); self.assertIn('不能确定', reason)
        # The file's pose is one of several normal ones: it stays.
        (row, source, _, _), _ = self.ask(self.reply(upright='E', also_normal=['A', 'F']))
        self.assertEqual((row['id'], source), ('file', 'vision'))

    def test_an_answer_that_contradicts_itself_is_not_trusted(self):
        # The agent names B but said itself that B does not fit.
        (row, source, reason, seen), _ = self.ask(self.reply(fits=['D']))
        self.assertEqual((row['id'], source, seen['confident']), ('file', 'file', False)); self.assertIn('不能确定', reason)

    def test_a_choice_the_measurements_speak_against_changes_nothing(self):
        # A grinder that already rests on its battery: its flat face is down in the file's pose, hardly at all when flipped.
        measured = rows(folder=self.folder / 'measured', flat={'file': .11, 'flip': .05, 'right': .14})
        ask = lambda **changes: self.ask(self.reply(**changes), lambda: orientation.choose(measured, self.sheet))[0]
        row, source, reason, seen = ask()
        self.assertEqual((row['id'], source, seen['doubt']), ('file', 'file', True)); self.assertIn('与测量不符', reason)
        # A cabinet on casters has no flat face down the right way up; that is no contradiction.
        self.assertEqual(ask(carried_by='feet_or_wheels')[0]['id'], 'flip')
        # The other way round the measurements agree, and nothing was measured means nothing is contradicted.
        agree = rows(folder=self.folder / 'agree', flat={'file': .05, 'flip': .4})
        self.assertEqual(self.ask(self.reply(), lambda: orientation.choose(agree, self.sheet))[0][0]['id'], 'flip')
        self.assertFalse(orientation.contradicted(self.rows, self.rows[1], 'flat_underside'))
        # It only ever withholds a change: it never proposes the other pose.
        self.assertTrue(orientation.contradicted(measured, measured[1], 'flat_underside'))
        self.assertFalse(orientation.contradicted(measured, measured[0], 'flat_underside'))

    def test_measurements_are_put_into_words_for_the_agent(self):
        grinder = [('Battery_Pack', (.6, 0, .95)), ('Motor_Housing', (0, 0, -.9)), ('Cutting_Disc', (-.8, 0, .9))]
        named = rows(grinder, flat={'file': .04, 'flip': .11})
        for row in named: row['dimensions'] = [2.0, 1.0, .55] if row['id'] in ('file', 'flip') else [.55, 1.0, 2.0] if row['id'] in ('right', 'left') else [2.0, .55, 1.3]
        facts = orientation.cell_facts(named)
        self.assertEqual(facts[0], {'cell': 'A', 'stance': 'lies level', 'flat_face_on_floor': .04, 'on_floor': ['Motor_Housing'], 'on_top': ['Battery_Pack', 'Cutting_Disc']})
        self.assertEqual((facts[1]['on_floor'], facts[1]['on_top'], facts[1]['flat_face_on_floor']), (['Battery_Pack', 'Cutting_Disc'], ['Motor_Housing'], .11))
        self.assertEqual([fact['stance'] for fact in facts], ['lies level', 'lies level', 'in between', 'in between', 'stands tall', 'stands tall'])
        # One mesh, or hundreds of parts: the lists would say nothing, so they are left out.
        self.assertNotIn('on_floor', orientation.cell_facts(rows([('Mesh_0', (0, 0, 0))]))[0])
        self.assertNotIn('on_floor', orientation.cell_facts(rows([('p%d' % index, (0, 0, -.9)) for index in range(81)]))[0])

    def test_a_stronger_model_can_be_named_for_the_pose_agent_alone(self):
        with patch('ai_config.read_env', return_value={'AI_POSE_MODEL': ' gpt-6.1-sol '}):
            (_, _, _, seen), body = self.ask(self.reply())
            self.assertEqual((seen['model'], body['model']), ('gpt-6.1-sol', 'gpt-6.1-sol'))
            with patch.object(ai_config, 'get_vision_config', return_value=self.vision):
                asked = orientation._vision()
            self.assertEqual((asked['api_key'], asked['base_url']), ('k', self.vision['base_url']))       # the vision model's endpoint and key
        with patch('ai_config.read_env', return_value={}):
            self.assertEqual(self.ask(self.reply())[1]['model'], 'claude-sonnet-4-6')

    def test_without_an_answer_part_names_decide_and_otherwise_nothing_changes(self):
        for reply in (ValueError('AI 请求超时'), self.reply(upright='Z'), {'product': 'x', 'upright': 'B'}, {key: value for key, value in self.reply().items() if key != 'cells'}):
            with self.subTest(reply=reply):
                (row, source, reason, _), _ = self.ask(reply)
                self.assertEqual((row['id'], source), ('file', 'file')); self.assertIn('朝向未自动调整', reason)
        named = rows(NameTests.CABINET, self.folder / 'named')
        (row, source, _, _), _ = self.ask(ValueError('AI 请求超时'), lambda: orientation.choose(named, self.sheet))
        self.assertEqual((row['id'], source), ('flip', 'names'))
        with patch.object(ai_config, 'get_vision_config', return_value={**self.vision, 'enabled': False}), patch('ai_client._post') as post:
            row, source, _, _ = orientation.choose(self.rows, self.sheet)
            self.assertEqual((row['id'], source), ('file', 'file')); post.assert_not_called()

    def test_the_front_is_asked_about_separately(self):
        turns = [{'id': 'turn%d' % degrees, 'label': '', 'rotation': orientation.turned([180, 0, 0], degrees), 'path': self.rows[0]['path']}
                 for degrees in orientation.TURNS]
        sheet = orientation.sheet(turns, self.folder / 'front.png', columns=2)
        with Image.open(sheet) as picture:
            self.assertEqual(picture.size, (2 * 320 + 3 * 10, 2 * 320 + 3 * 10))
        answer = {'rests_properly': True, 'front': 'C', 'confident': True, 'reason': '夹头朝向镜头。'}
        seen, body = self.ask(answer, lambda: orientation.front_by_vision(turns, sheet, 'cordless drill', 'It stands on its battery.'))
        self.assertEqual((seen['row']['id'], seen['confident'], seen['rests']), ('turn180', True, True))
        facts = json.loads(body['messages'][-1]['content'][-1]['text'])
        self.assertEqual((facts['cells'], facts['product'], facts['should_rest']), (list('ABCD'), 'cordless drill', 'It stands on its battery.'))
        refused, _ = self.ask({**answer, 'rests_properly': False, 'reason': '立在电池尾端。'}, lambda: orientation.front_by_vision(turns, sheet))
        self.assertEqual((refused['rests'], refused['reason']), (False, '立在电池尾端。'))
        self.assertIsNone(self.ask(ValueError('x'), lambda: orientation.front_by_vision(turns, sheet))[0])
        self.assertIsNone(self.ask({'front': 'C', 'confident': True, 'reason': 'x'}, lambda: orientation.front_by_vision(turns, sheet))[0])     # not the agreed answer


class Fixture(unittest.TestCase):
    """A stored project, with Blender's candidate pictures and the vision model replaced."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for module in (store, engine, orientation):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        for name, value in [('QUEUE', queue.Queue()), ('RUNNING_JOBS', set()), ('PROCESSES', {})]:
            mocked = patch.object(engine, name, value); mocked.start(); self.addCleanup(mocked.stop)
        for target, name, value in ((engine, 'require_runtime', None), (engine, 'blender_path', '/usr/bin/blender')):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project', project('充电式电钻'))
        self.pictured = []; self.upright = {'row': 'flip', 'confident': True, 'reason': '电池底座在下。', 'product': 'cordless drill', 'also': [],
                                            'rests_on': 'It stands upright on the flat underside of its battery pack.', 'carried_by': 'flat_underside',
                                            'model': 'gpt-6.1-sol', 'cells': [{'id': 'flip', 'on_ground': 'the battery underside', 'fits': True}]}
        self.front = None; self.vision = True; self.second_looks = []
        for name, effect in (('picture', self.picture), ('by_vision', self.by_vision), ('front_by_vision', self.front_by_vision)):
            mocked = patch.object(orientation, name, side_effect=effect); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(orientation, '_vision', side_effect=lambda: {'provider': 'anthropic'} if self.vision else None)
        mocked.start(); self.addCleanup(mocked.stop)

    def picture(self, record, plan, poses, folder, cancelled=None):
        self.pictured.append([ident for ident, _, _ in poses])
        made = []
        for ident, label, rotation in poses:
            path = folder / ('pose_%s.png' % ident); path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (orientation.SIZE, orientation.SIZE), (200, 200, 210)).save(path)
            size = getattr(self, 'size', None)          # the model's own sides: each pose then measures as it would
            made.append({'id': ident, 'label': label, 'rotation': [float(value) for value in rotation], 'path': path,
                         'dimensions': orientation.oriented_dimensions(size, rotation) if size else [1.0, 1.0, 2.0],
                         'support_ratio': .2, 'weight_height': .5, 'tip_angle': 10.0, 'parts': []})
        return made

    def by_vision(self, candidates, image, part_names=()):
        if not self.vision or self.upright is None: return None
        return {**self.upright, 'row': next(row for row in candidates if row['id'] == self.upright['row'])}

    def front_by_vision(self, candidates, image, product=None, rests_on=None):
        self.second_looks.append((product, rests_on))
        if not self.front: return None
        return {'row': next(row for row in candidates if row['id'] == self.front.get('row', 'turn0')), 'confident': self.front.get('confident', True),
                'rests': self.front.get('rests', True), 'reason': self.front.get('reason', '夹头朝向镜头。')}


class DecisionTests(Fixture):
    def decide(self):
        record = store.get('project', 'fixture')
        return orientation.decide(record, record['plan'], self.data / 'fixture' / 'orientation' / 'job')

    def test_an_upside_down_model_is_stood_up_and_its_candidates_are_kept_for_the_user(self):
        record = self.decide()
        self.assertEqual((record['chosen'], record['changed'], record['source'], record['rotation']), ('flip', True, 'vision', [180.0, 0.0, 0.0]))
        self.assertEqual([row['id'] for row in record['candidates']], [ident for ident, _, _ in orientation.POSES])
        self.assertTrue(all(row['image_url'].startswith('/files/fixture/orientation/job/up/pose_') for row in record['candidates']))
        self.assertTrue((self.data / record['sheet_url'][7:]).is_file())
        self.assertEqual(self.pictured, [['file', 'flip', 'forward', 'backward', 'right', 'left'], ['turn0', 'turn90', 'turn180', 'turn270']])
        self.assertNotIn('front', record)                                  # nobody could say which side is the front
        self.assertEqual((record['rests_on'], record['model'], record['product']), (self.upright['rests_on'], 'gpt-6.1-sol', 'cordless drill'))
        self.assertEqual(self.second_looks, [('cordless drill', self.upright['rests_on'])])       # the second look is told what to look for
        shown = orientation.public(record)
        self.assertNotIn('sheet_url', shown); self.assertEqual(shown['candidates'][1]['rotation'], [180.0, 0.0, 0.0])
        self.assertEqual((shown['rests_on'], shown['cells']), (record['rests_on'], record['cells']))

    def test_a_pose_that_does_not_rest_as_described_on_the_second_look_is_not_applied(self):
        self.front = {'rests': False, 'reason': '立在电池尾端，不是电池底面。', 'row': 'turn90'}
        record = self.decide()
        self.assertEqual((record['chosen'], record['changed'], record['source'], record['rotation']), ('file', False, 'file', [0.0, 0.0, 0.0]))
        self.assertEqual(record['rejected']['id'], 'flip'); self.assertIn('立在电池尾端', record['reason']); self.assertNotIn('front', record)
        self.assertTrue((self.data / record['rejected']['sheet_url'][7:]).is_file())
        self.assertEqual(orientation.public(record)['rejected']['id'], 'flip')

    def test_a_righted_model_also_gets_its_front_turned_to_the_camera(self):
        self.front = {'row': 'turn180'}
        record = self.decide()
        self.assertEqual(record['rotation'], orientation.turned([180, 0, 0], 180))
        self.assertTrue(orientation.same_up(record['rotation'], [180, 0, 0])); self.assertEqual(record['front']['turn'], 180)
        self.front = {'row': 'turn90', 'confident': False}
        self.assertEqual(self.decide()['rotation'], [180.0, 0.0, 0.0])       # an unsure answer turns nothing

    def test_a_model_that_is_already_upright_is_not_touched_and_its_front_is_not_asked_about(self):
        self.upright['row'] = 'file'
        record = self.decide()
        self.assertEqual((record['changed'], record['rotation'], record['source']), (False, [0.0, 0.0, 0.0], 'vision'))
        self.assertEqual(len(self.pictured), 1)

    def test_an_upload_never_fails_over_its_orientation(self):
        stored = store.get('project', 'fixture')
        with patch.object(orientation, 'picture', side_effect=ValueError('朝向检查超时，模型保持当前朝向。')):
            record = orientation.first_pose(stored, stored['analysis'], self.data / 'fixture' / 'orientation' / 'job')
        self.assertEqual((record['status'], record['changed'], record['rotation'], record['reason']), ('failed', False, [0, 0, 0], '朝向检查超时，模型保持当前朝向。'))
        with patch.object(orientation, 'picture', side_effect=RuntimeError('boom')):
            self.assertEqual(orientation.first_pose(stored, stored['analysis'], self.data / 'x')['status'], 'failed')
        with patch.object(orientation, 'picture', side_effect=InterruptedError()), self.assertRaises(InterruptedError):
            orientation.first_pose(stored, stored['analysis'], self.data / 'x')


class LyingFlatTests(Fixture):
    """Long and flat tools lie on their largest face, by measurement, without asking the pose agent."""
    SAW = [.154, .643, .020]; WRENCH = [.381, 2.0, .134]; SCREWDRIVER = [.448, 2.0, .463]; CALIPER = [.5, 2.0, .0625]
    HAMMER = [.791, 1.999, .183]; PLIERS = [.68, 2.0, .297]; DRILL = [2.0, 1.56, .459]          # measured from the test models

    def measured(self, size, flat=None):
        result = rows(flat=flat)
        for row in result: row['dimensions'] = orientation.oriented_dimensions(size, row['rotation'])
        return result

    def decide(self, name='手锯', category=''):
        record = store.get('project', 'fixture'); record['name'] = name; record['metadata'] = {'product_category': category}
        return orientation.decide(record, record['plan'], self.data / 'fixture' / 'orientation' / 'job')

    def test_long_or_thin_tools_lie_flat_and_a_drill_stays_free_to_stand(self):
        for size in (self.SAW, self.WRENCH, self.SCREWDRIVER, self.CALIPER):
            with self.subTest(size=size):
                self.assertTrue(orientation.lies_flat(self.measured(size)))
        for size in (self.HAMMER, self.PLIERS, self.DRILL, [1, 1, 2]):
            with self.subTest(size=size):
                self.assertFalse(orientation.lies_flat(self.measured(size)))     # the pose agent still decides these
        # Named as standing, a long or thin product is left to the pose agent: names are often just file names.
        for words in (('12_cordless_drill_red',), ('冲击起子机',), ('Impact Driver',), ('', '台灯'), ('water bottle',)):
            with self.subTest(words=words):
                self.assertFalse(orientation.lies_flat(self.measured(self.WRENCH), words))
        for words in (('Phillips Screwdriver',), ('screw driver',), ('十字螺丝刀',)):
            with self.subTest(words=words):
                self.assertTrue(orientation.lies_flat(self.measured(self.WRENCH), words))
        self.assertFalse(orientation.lies_flat(rows()))                         # unmeasured candidates tell nothing

    def test_the_lowest_pose_is_kept_or_found_and_its_resting_twin_preferred(self):
        flat = self.measured(self.SAW)
        self.assertIs(orientation.lowest(flat), flat[0])
        # A saw stored on its edge: of the two lying poses, the one with more of a flat face on the floor.
        edge = self.measured([.154, .020, .643], flat={'forward': .03, 'backward': .13})
        self.assertEqual(orientation.lowest(edge)['id'], 'backward')
        self.assertTrue(orientation.standing(edge, edge[0])); self.assertFalse(orientation.standing(edge, edge[3]))

    def test_a_flat_tool_is_decided_without_the_pose_agent(self):
        self.size = self.SAW
        record = self.decide()
        self.assertEqual((record['chosen'], record['changed'], record['source'], record['rotation']), ('file', False, 'file', [0.0, 0.0, 0.0]))
        self.assertIn('平放', record['reason']); orientation.by_vision.assert_not_called()
        self.assertEqual(len(self.pictured), 1)                                 # no second look either
        # Stored standing on its edge, it is laid down by measurement, still without asking.
        self.size = [.154, .020, .643]
        record = self.decide()
        self.assertEqual((record['changed'], record['source']), (True, 'geometry'))
        chosen = next(row for row in record['candidates'] if row['id'] == record['chosen'])
        self.assertAlmostEqual(chosen['dimensions'][2], .020)
        orientation.by_vision.assert_not_called(); self.assertEqual(self.second_looks, [])

    def test_a_drill_is_still_stood_up_by_the_pose_agent(self):
        self.size = self.DRILL; self.upright['row'] = 'forward'
        record = self.decide('12_cordless_drill_red')
        self.assertEqual((record['chosen'], record['source']), ('forward', 'vision')); orientation.by_vision.assert_called_once()

    def test_the_pose_agent_cannot_stand_a_flat_tool_on_its_edge(self):
        saw = self.measured(self.SAW)
        on_edge = {**self.upright, 'row': saw[4], 'reason': '锯片竖立。'}
        with patch.object(orientation, 'by_vision', return_value=on_edge):
            row, source, reason, seen = orientation.choose(saw, None, flat=True)
            self.assertEqual((row['id'], source), ('file', 'file')); self.assertIn('平放', reason); self.assertIs(seen, on_edge)
            self.assertEqual(orientation.choose(saw, None)[0]['id'], 'right')       # without the guard it would be taken
        # A pick that lies flat too (the other face up) is still the agent's to make.
        with patch.object(orientation, 'by_vision', return_value={**on_edge, 'row': saw[1]}):
            self.assertEqual(orientation.choose(saw, None, flat=True)[:2], (saw[1], 'vision'))


class ApplyTests(Fixture):
    def test_a_chosen_pose_is_one_undoable_version_and_is_previewed_at_once(self):
        view = orientation.apply('fixture', {'expected_version': 1, 'candidate': 'flip', 'shot_id': 'side'})
        self.assertEqual((view['version'], view['plan']['rotation'], view['history'][-1]['reason']), (2, [180.0, 0.0, 0.0], '改选摆放姿态'))
        jobs = store.all_records('job')
        self.assertEqual([(job['kind'], job['shot_ids'], job['version']) for job in jobs], [('preview', ['side'], 2)])
        engine.cancel(jobs[0]['id'])
        view = orientation.apply('fixture', {'expected_version': 2, 'turn': 90})
        self.assertEqual((view['version'], view['plan']['rotation'], view['history'][-1]['reason']), (3, [180.0, 0.0, 90.0], '转动产品朝向'))
        self.assertEqual([job['shot_ids'] for job in store.all_records('job') if job['status'] == 'queued'], [['hero']])    # no shot named: the main image
        for job in store.all_records('job'): engine.cancel(job['id'])
        view = orientation.apply('fixture', {'expected_version': 3, 'flip': True})
        self.assertTrue(orientation.same(view['plan']['rotation'], [0, 0, -90])); self.assertEqual(view['history'][-1]['reason'], '上下翻转产品')

    def test_what_was_fitted_to_the_old_pose_goes_with_it(self):
        plan = store.get('project', 'fixture')['plan']
        plan['scene_checkpoints'] = {'hero': 'token'}; plan['shots'][0].update(camera_distance=5.0, target_offset=[0, 0, .1], azimuth=20)
        posed = orientation.repose(plan, [180, 0, 0])
        self.assertNotIn('scene_checkpoints', posed); self.assertNotIn('camera_distance', posed['shots'][0]); self.assertNotIn('target_offset', posed['shots'][0])
        self.assertEqual((posed['shots'][0]['azimuth'], posed['rotation']), (20, [180.0, 0.0, 0.0]))
        self.assertIn('scene_checkpoints', plan)                              # the plan it was made from is left alone

    def test_the_pose_already_in_use_changes_nothing(self):
        view = orientation.apply('fixture', {'expected_version': 1, 'candidate': 'file'})
        self.assertEqual((view['version'], store.all_records('job')), (1, []))

    def test_a_change_waits_for_other_work_and_bad_requests_are_refused(self):
        for body in ({}, {'candidate': 'flip', 'flip': True}, {'candidate': 'sideways'}, {'turn': 45}, {'flip': 'yes'}, {'rotation': [0, 0]},
                     {'candidate': 'flip', 'expected_version': 0}, 'flip'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                orientation.apply('fixture', body)
        with self.assertRaises(engine.JobConflictError):
            orientation.apply('fixture', {'expected_version': 7, 'flip': True})
        job = engine.submit('fixture', 'preview', 'preview', ['hero'])
        with self.assertRaisesRegex(engine.JobConflictError, '已有渲染任务'):
            orientation.apply('fixture', {'flip': True})
        engine.cancel(job['id'])
        store.put('scene_task', {'id': 't', 'project_id': 'fixture', 'status': 'running'})
        with self.assertRaisesRegex(engine.JobConflictError, '有任务正在进行'):
            orientation.apply('fixture', {'flip': True})
        self.assertEqual(store.get('project', 'fixture')['version'], 1)

    def test_a_generated_scene_is_never_sent_to_blender_for_the_preview(self):
        record = store.get('project', 'fixture')
        record['plan']['shots'].append({'id': 'scene', 'label': '使用场景', 'kind': 'full', 'style': 'soft', 'projection': 'perspective',
                                        'azimuth': 30, 'elevation': 20, 'focal_length': 55})
        record['plan']['scene_brief'] = {'version': 1, 'shot_id': 'scene', 'segment_id': None, 'reference_id': None, 'source': 'rules',
                                         'method': 'generate', 'reason': '', 'summary': '', 'people': True, 'complex': False}
        store.put('project', record)
        orientation.apply('fixture', {'flip': True, 'shot_id': 'scene'})
        self.assertEqual(store.all_records('job')[0]['shot_ids'], ['hero'])


class TaskTests(Fixture):
    def run_task(self, **body):
        with patch.object(orientation.threading, 'Thread') as thread:
            task = orientation.submit('fixture', {'expected_version': store.get('project', 'fixture')['version'], **body})
        orientation._run(thread.call_args.kwargs['args'][0])
        return store.get(orientation.KIND, task['id'])

    def test_an_existing_project_is_stood_up_on_request(self):
        task = self.run_task(shot_id='campaign')
        self.assertEqual((task['status'], task['stage'], task['changed'], task['applied_version']), ('completed', '已摆正模型', True, 2), task.get('error'))
        saved = store.get('project', 'fixture')
        self.assertEqual((saved['plan']['rotation'], saved['history'][-1]['reason'], saved['orientation']['chosen']), ([180.0, 0.0, 0.0], '自动摆正模型', 'flip'))
        self.assertEqual([job['shot_ids'] for job in store.all_records('job')], [['campaign']])
        view = engine.project_view('fixture')
        self.assertEqual((view['orient_task']['status'], view['orientation']['source']), ('completed', 'vision')); self.assertNotIn('sheet_url', view['orientation'])

    def test_nothing_changes_when_nothing_could_be_told_or_the_right_side_is_already_up(self):
        record = store.get('project', 'fixture'); record['plan']['rotation'] = [180, 0, 90]; store.put('project', record)
        task = self.run_task()                                   # the user already turned it: flipped, facing another way
        self.assertEqual((task['status'], task['changed'], task['stage']), ('completed', False, '朝向无需调整'))
        self.assertEqual((store.get('project', 'fixture')['version'], store.get('project', 'fixture')['plan']['rotation']), (1, [180, 0, 90]))
        self.vision = False
        task = self.run_task()                                   # no vision model, no telling names: the pose stays as the user set it
        self.assertEqual((task['changed'], store.get('project', 'fixture')['plan']['rotation'], store.get('project', 'fixture')['orientation']['source']),
                         (False, [180, 0, 90], 'file'))
        self.assertEqual(store.all_records('job'), [])

    def test_a_check_that_fails_or_is_overtaken_changes_nothing(self):
        with patch.object(orientation, 'picture', side_effect=ValueError('朝向检查失败，模型保持当前朝向。')):
            task = self.run_task()
        self.assertEqual((task['status'], task['error']), ('failed', '朝向检查失败，模型保持当前朝向。'))
        def edited(*arguments, **options):
            record = store.get('project', 'fixture'); record['version'] += 1; store.put('project', record)
            return self.picture(*arguments, **options)
        with patch.object(orientation, 'picture', side_effect=edited):
            task = self.run_task()
        self.assertEqual(task['status'], 'failed'); self.assertIn('已被修改', task['error'])
        self.assertEqual(store.get('project', 'fixture')['plan']['rotation'], [0, 0, 0])

    def test_one_check_at_a_time_and_it_can_be_cancelled(self):
        with patch.object(orientation.threading, 'Thread'):
            task = orientation.submit('fixture', {})
            with self.assertRaises(engine.JobConflictError):
                orientation.submit('fixture', {})
            with self.assertRaises(engine.JobConflictError):
                orientation.submit('fixture', {'expected_version': 3})
        self.assertEqual(orientation.cancel('fixture', task['id'])['status'], 'cancelled')
        orientation._run(task['id'])
        self.assertEqual((store.get(orientation.KIND, task['id'])['status'], self.pictured), ('cancelled', []))
        with self.assertRaises(KeyError):
            orientation.cancel('other', task['id'])
        store.put(orientation.KIND, {'id': 'stuck', 'project_id': 'fixture', 'status': 'running', 'created_at': 1})
        orientation.recover_interrupted()
        self.assertEqual(store.get(orientation.KIND, 'stuck')['status'], 'failed')

    def test_the_web_layer_serves_the_state_the_check_and_the_change(self):
        import app as application
        client = application.app.test_client()
        state = client.get('/api/projects/fixture/orientation').get_json()
        self.assertEqual((state['orientation'], state['task'], state['rotation']), (None, None, [0, 0, 0]))
        with patch.object(orientation.threading, 'Thread') as thread:
            self.assertEqual(client.post('/api/projects/fixture/orientation', json={'expected_version': 1}).status_code, 202)
        orientation._run(thread.call_args.kwargs['args'][0])
        view = client.get('/api/projects/fixture').get_json()
        self.assertEqual((view['version'], view['plan']['rotation'], view['orient_task']['status']), (2, [180.0, 0.0, 0.0], 'completed'))
        for job in store.all_records('job'): engine.cancel(job['id'])
        turned = client.post('/api/projects/fixture/orientation/apply', json={'expected_version': 2, 'turn': -90})
        self.assertEqual((turned.status_code, turned.get_json()['plan']['rotation']), (200, [180.0, 0.0, -90.0]))
        for job in store.all_records('job'): engine.cancel(job['id'])
        self.assertEqual(client.post('/api/projects/fixture/orientation/apply', json={'turn': 30}).status_code, 400)
        self.assertEqual(client.post('/api/projects/fixture/orientation/apply', json={'expected_version': 1, 'flip': True}).status_code, 409)
        with patch.object(application, 'DATA', self.data):
            self.assertEqual(client.get(view['orientation']['candidates'][0]['image_url']).status_code, 200)


class FirstImageTests(Fixture):
    """The analysis job of a new upload: the pose is decided before the first plan is built."""

    def analyse(self, features=None):
        record = store.get('project', 'fixture'); analysis = record.pop('analysis'); record['status'] = 'analyzing'
        record['plan'] = planner.default_plan(); store.put('project', record)
        if features: analysis = {**analysis, 'features': {**analysis.get('features', {}), **features}}
        job = {'id': 'analysis', 'project_id': 'fixture', 'kind': 'analyze', 'quality': 'preview', 'shot_ids': [], 'status': 'queued', 'stage': '', 'progress': 0,
               'plan': copy.deepcopy(record['plan']), 'version': 1, 'created_at': 1, 'completed': [], 'log_tail': [], 'error': None}
        store.put('job', job)
        with patch.object(engine, 'execute', return_value=analysis), \
                patch.object(engine, 'runtime_status', return_value={'capabilities': {'orientation': self.available}}):
            engine._run_job('analysis')
        return store.get('project', 'fixture')

    def setUp(self):
        super().setUp(); self.available = True

    def test_the_first_plan_already_holds_the_upright_pose(self):
        saved = self.analyse({'dimensions': [2.0, .5, 2.0]})
        self.assertEqual((saved['status'], saved['plan']['rotation'], saved['orientation']['chosen'], saved['version']), ('ready', [180.0, 0.0, 0.0], 'flip', 1))
        self.assertTrue(saved['orientation']['candidates'][0]['image_url'].startswith('/files/fixture/orientation/analysis/up/'))

    def test_the_first_framing_follows_the_pose_the_model_was_stood_in(self):
        self.upright['row'] = 'forward'                              # filed lying down, stood up: now tall
        saved = self.analyse({'dimensions': [.6, 2.0, .5]})
        self.assertEqual(saved['plan']['rotation'], [90.0, 0.0, 0.0]); self.assertEqual(saved['plan']['auto_framing']['shape'], 'tall')

    def test_an_upright_upload_and_an_unavailable_check_leave_the_plan_as_it_was(self):
        self.upright['row'] = 'file'
        saved = self.analyse()
        self.assertEqual((saved['plan']['rotation'], saved['orientation']['changed']), ([0, 0, 0], False))
        self.available = False; self.pictured.clear()
        saved = self.analyse()
        self.assertEqual((saved['plan']['rotation'], saved['status'], self.pictured), ([0, 0, 0], 'ready', []))
        self.available = True
        with patch.object(orientation, 'picture', side_effect=ValueError('朝向检查失败，模型保持当前朝向。')):
            saved = self.analyse()
        self.assertEqual((saved['status'], saved['plan']['rotation'], saved['orientation']['status']), ('ready', [0, 0, 0], 'failed'))


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: candidate poses not rendered')
class RuntimeTests(unittest.TestCase):
    """blender/orient.py on a real model: the pump bottle from the samples, which stands upright."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        poses = [{'id': ident, 'rotation': rotation} for ident, _, rotation in orientation.POSES[:3]]
        request = {'model_path': str(ROOT / 'samples' / 'matte-pump-bottle.glb'), 'output_dir': str(cls.folder), 'size': 128, 'samples': 4, 'candidates': poses}
        (cls.folder / 'request.json').write_text(json.dumps(request), encoding='utf-8')
        cls.ran = subprocess.run([shutil.which('blender'), '-b', '--factory-startup', '--disable-autoexec', '-t', '4', '--python-exit-code', '1',
                                  '--python', str(ROOT / 'blender' / 'orient.py'), '--', str(cls.folder / 'request.json')],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        cls.result = json.loads((cls.folder / 'result.json').read_text(encoding='utf-8')) if (cls.folder / 'result.json').is_file() else {}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_pose_is_measured_and_pictured(self):
        self.assertEqual(self.ran.returncode, 0, self.ran.stdout[-2000:])
        self.assertTrue(self.result.get('success'), self.result.get('error'))
        found = {row['id']: row for row in self.result['candidates']}
        self.assertEqual(list(found), ['file', 'flip', 'forward'])
        for row in found.values():
            with Image.open(self.folder / row['image']) as picture:
                self.assertEqual((picture.size, picture.format), ((128, 128), 'PNG'))
                self.assertGreater(len(set(picture.convert('L').getdata())), 20)      # a picture, not a blank frame

    def test_the_measurements_describe_how_the_model_rests(self):
        found = {row['id']: row for row in self.result.get('candidates', [])}
        upright, flipped, lying = found['file'], found['flip'], found['forward']
        # The bottle is taller than wide; lying down its height and depth change places.
        self.assertGreater(upright['dimensions'][2], upright['dimensions'][0])
        self.assertAlmostEqual(lying['dimensions'][1], upright['dimensions'][2], places=3)
        self.assertEqual(upright['dimensions'], flipped['dimensions'])
        # Standing on its base it is steady; on its pump head it is not.
        self.assertGreater(upright['support_ratio'], flipped['support_ratio'])
        self.assertGreater(upright['tip_angle'], flipped['tip_angle'])
        # Each part's height in the pose mirrors when the model is flipped, and so does how near it comes to floor and top.
        for first, second in zip(upright['parts'], flipped['parts']):
            self.assertEqual(first['name'], second['name']); self.assertAlmostEqual(first['height'], -second['height'], places=2)
            self.assertAlmostEqual(first['low'], 1 - second['high'], places=2)
            self.assertTrue(0 <= first['low'] <= first['high'] <= 1.001, first)
        self.assertTrue(any(part['low'] <= .04 for part in upright['parts']))          # something stands on the floor
        # Its flat base lies on the floor; the pump head on top is no flat face to stand on.
        self.assertGreater(upright['flat_contact'], .2); self.assertLess(flipped['flat_contact'], upright['flat_contact'] / 2)
        self.assertLess(lying['flat_contact'], upright['flat_contact'] / 2)


if __name__ == '__main__':
    unittest.main()

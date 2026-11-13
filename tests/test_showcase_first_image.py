"""The first image: look-dev for placeholder materials and the background 2K pass.

Storage is temporary and Blender execution is replaced, so these tests cover
decisions and job lifecycle only. Image quality is judged on real renders, not
here; the one real-Blender test at the end checks that the showcase scene is
built as described.
"""
import copy
import json
import queue
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
import engine
import lookdev
import planner
import studio_store as store


def material(name, color=(.55, .59, .63, 1), source='fallback', **extra):
    return {'name': name, 'source': source, 'color': list(color), 'roughness': .8, 'metallic': 0, 'transmission': 0,
            'textures': [], **extra}


def part(name, *materials):
    return {'name': name, 'type': 'MESH', 'materials': list(materials)}


RED = (.65, .045, .045, 1)
CABINET = {'materials': [material('亮光金属_6'), material('红色塑料_4', RED), material('黑色.1_3', (.05, .06, .07, 1)),
                         material('黑色.1_2', (.05, .06, .07, 1)), material('default'), material('铝_1')],
           'objects': [part('G-不锈钢台面_2', '亮光金属_6'), part('M6X16螺丝_5', '亮光金属_6'), part('2.5寸轮子支架_4', '亮光金属_6'),
                       part('G-07C左边板_2', '红色塑料_4'), part('pasted__G_02M左门_1', '红色塑料_4'),
                       part('2.5寸轮子_4', '黑色.1_3'), part('2.5寸轮子_5', '黑色.1_3'),
                       part('左拉手_1', '黑色.1_2'), part('右拉手_1', 'default'), part('pasted__G_02M右门_1', 'default'),
                       part('钥匙环_3', 'default'), part('G-07C锁条固定片_4', 'default'), part('铝条_7', '铝_1')]}


class LookDevTests(unittest.TestCase):
    def decisions(self, analysis=CABINET, path='cabinet.obj'):
        result = lookdev.propose(analysis, path)
        by_material = {entry['name']: entry for entry in result['material_overrides']}
        by_part = {entry['object_name']: entry for entry in result['object_material_overrides']}
        return result, by_material, by_part

    def test_material_names_decide_the_finish_and_colour_is_kept(self):
        _, materials, _ = self.decisions()
        self.assertEqual(materials['亮光金属_6']['lookdev_finish'], 'bright_metal')
        self.assertEqual((materials['亮光金属_6']['metallic'], materials['亮光金属_6']['preserve_original']), (1, False))
        self.assertEqual(materials['铝_1']['lookdev_finish'], 'brushed_metal')
        red = materials['红色塑料_4']
        self.assertEqual((red['lookdev_finish'], red['metallic'], red['coat']), ('gloss_plastic', 0, .3))
        # The stored colour is the model's own, converted from linear to sRGB.
        self.assertEqual(red['color'], lookdev._hex(RED)); self.assertEqual(red['color'], '#D33C3C')

    def test_a_part_name_outranks_the_material_it_shares(self):
        _, _, parts = self.decisions()
        self.assertEqual(parts['G-不锈钢台面_2']['lookdev_finish'], 'brushed_metal')
        self.assertEqual(parts['G-不锈钢台面_2']['material_name'], '亮光金属_6')
        self.assertNotIn('M6X16螺丝_5', parts)   # already what its material says

    def test_parts_agreeing_on_a_nameless_colour_decide_the_whole_material(self):
        _, materials, parts = self.decisions()
        self.assertEqual(materials['黑色.1_3']['lookdev_finish'], 'rubber')
        self.assertNotIn('2.5寸轮子_4', parts)

    def test_wheel_hardware_is_not_rubber(self):
        self.assertEqual(lookdev._rule('2.5寸轮子支架_4', lookdev.PART_RULES)[0], 'bright_metal')
        self.assertEqual(lookdev._rule('2.5寸轮子_4', lookdev.PART_RULES)[0], 'rubber')
        self.assertEqual(lookdev._rule('caster bracket', lookdev.PART_RULES)[0], 'bright_metal')

    def test_an_unassigned_part_takes_the_look_of_its_twin(self):
        result, _, parts = self.decisions()
        handle = parts['右拉手_1']
        self.assertEqual((handle['color'], handle['material_name']), (lookdev._hex((.05, .06, .07)), 'default'))
        door = parts['pasted__G_02M右门_1']
        self.assertEqual((door['lookdev_finish'], door['color']), ('gloss_plastic', '#D33C3C'))
        twin = next(item for item in result['decisions'] if item.get('object') == '右拉手_1')
        self.assertEqual(twin['twin_of'], '黑色.1_2')

    def test_twins_that_disagree_are_left_alone(self):
        analysis = copy.deepcopy(CABINET)
        analysis['objects'].append(part('中拉手_1', '红色塑料_4')); analysis['objects'].append(part('左拉手_2', '红色塑料_4'))
        _, _, parts = self.decisions(analysis)
        self.assertNotIn('右拉手_1', parts)

    def test_what_names_do_not_explain_gets_a_neutral_sheen_not_a_guess(self):
        _, materials, parts = self.decisions()
        self.assertEqual((materials['default']['lookdev_finish'], materials['default']['metallic']), ('satin', 0))
        self.assertEqual(parts['钥匙环_3']['lookdev_finish'], 'bright_metal')
        self.assertNotIn('G-07C锁条固定片_4', parts)

    def test_authored_materials_are_never_touched(self):
        analysis = {'materials': [material('Body paint', source='model', textures=['base_color']),
                                  material('Chrome', source='model', metallic=1), material('Glass', source='model', transmission=1)],
                    'objects': [part('Shell', 'Body paint'), part('Trim', 'Chrome'), part('Window', 'Glass')]}
        self.assertEqual(lookdev.propose(analysis, 'model.obj')['decisions'], [])
        gltf = {'materials': [material('塑料外壳', source='model')], 'objects': [part('Shell', '塑料外壳')]}
        self.assertEqual(lookdev.propose(gltf, 'model.glb')['decisions'], [])

    def test_a_strongly_coloured_metal_is_painted(self):
        analysis = {'materials': [material('红色金属', RED)], 'objects': [part('Panel', '红色金属')]}
        self.assertEqual(lookdev.propose(analysis, 'a.obj')['material_overrides'][0]['lookdev_finish'], 'gloss_paint')

    def test_ascii_keywords_match_whole_words_only(self):
        self.assertIsNone(lookdev._match('monkey bar', ('key',)))
        self.assertEqual(lookdev._match('Key.1_3', ('key',)), 'key')
        self.assertIsNone(lookdev._match('absolute', ('abs',)))

    def test_apply_writes_a_valid_plan_and_never_overrides_existing_choices(self):
        plan = planner.default_plan(); plan['look'] = 'showcase'
        developed, record = lookdev.apply(copy.deepcopy(plan), CABINET, 'cabinet.obj')
        validated = planner.validate(developed)
        self.assertEqual(validated['lookdev']['summary'], record['summary'])
        self.assertIn('亮面金属', record['summary'])
        chosen = copy.deepcopy(plan); chosen['material_overrides'] = [{'name': '红色塑料_4', 'preset': 'rubber'}]
        self.assertEqual(lookdev.apply(chosen, CABINET, 'cabinet.obj'), (chosen, None))

    def test_plan_validation_guards_the_new_fields(self):
        plan = planner.default_plan()
        for field, value in (('look', 'cinematic'), ('auto_polish', 'yes'), ('lookdev', {'summary': 5})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                planner.validate({**plan, field: value})
        with self.assertRaises(ValueError):
            planner.validate({**plan, 'material_overrides': [{'name': 'A', 'preset': 'painted_metal', 'coat': 1.4}]})
        self.assertEqual(planner.validate({**plan, 'look': 'showcase', 'auto_polish': True})['look'], 'showcase')


class MaterialDecisionTests(unittest.TestCase):
    """An explicit decision about a material outranks what look-dev guessed."""
    def setUp(self):
        plan = planner.default_plan(); plan['look'] = 'showcase'
        self.plan = planner.validate(lookdev.apply(plan, CABINET, 'cabinet.obj')[0])

    def test_whole_material_edit_replaces_the_per_part_guess(self):
        self.assertTrue(any(entry['object_name'] == 'G-不锈钢台面_2' for entry in self.plan['object_material_overrides']))
        result = ai_client.apply_patch(self.plan, {'material_overrides': [{'name': '亮光金属_6', 'roughness': .05}]}, CABINET)
        self.assertFalse(any(entry['material_name'] == '亮光金属_6' for entry in result['object_material_overrides']))
        edited = next(entry for entry in result['material_overrides'] if entry['name'] == '亮光金属_6')
        self.assertEqual((edited['roughness'], edited['source'], edited['color']), (.05, 'ai', '#C4CAD0'))
        self.assertNotIn('lookdev_finish', edited)

    def test_a_new_material_type_drops_the_old_coat(self):
        result = ai_client.apply_patch(self.plan, {'material_overrides': [{'name': '红色塑料_4', 'preset': 'rubber'}]}, CABINET)
        edited = next(entry for entry in result['material_overrides'] if entry['name'] == '红色塑料_4')
        self.assertNotIn('coat', edited); self.assertEqual(edited['preset'], 'rubber')

    def test_the_planner_can_set_a_clear_coat(self):
        schema = ai_client.patch_schema(self.plan, CABINET)
        self.assertIn('coat', schema['properties']['object_material_overrides']['items']['properties'])
        patch_value = {'object_material_overrides': [{'object_name': 'G-07C左边板_2', 'material_name': '红色塑料_4', 'coat': .8}]}
        result = ai_client.apply_patch(self.plan, patch_value, CABINET)
        self.assertEqual(next(entry for entry in result['object_material_overrides'] if entry['object_name'] == 'G-07C左边板_2')['coat'], .8)


class PassFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        for module in (store, engine):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        for name, value in [('QUEUE', queue.Queue()), ('RUNNING_JOBS', set()), ('PROCESSES', {})]:
            mocked = patch.object(engine, name, value); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(engine, 'require_runtime'); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        plan = planner.default_plan(); plan.update(visual_review=False, look='showcase', auto_polish=True)
        store.put('project', {'id': 'fixture', 'slug': 'fixture', 'name': 'Fixture', 'model_path': str(self.data / 'fixture/cabinet.obj'),
                              'version': 1, 'history': [], 'status': 'analyzing', 'analysis': None, 'plan': plan, 'outputs': [],
                              'metadata': {}, 'initial_preview': {'status': 'pending'}})
        self.analysis = {**copy.deepcopy(CABINET), 'features': {}, 'warnings': []}

    def render(self, job, request, out):
        if request['action'] == 'analyze':
            return copy.deepcopy(self.analysis)
        long = {'thumbnail': 256, 'preview': 512, 'showcase': 2048}.get(request['quality'], 4096)
        out.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (long, long), 'white').save(out / (out.name + '.png'))
        (out / 'result.json').write_text('{}', encoding='utf-8')
        self.rendered.append((request['quality'], request['shot']['id']))
        return {'success': True, 'checks': {}, 'render_pipeline_version': 2}

    def drain(self):
        self.rendered = getattr(self, 'rendered', [])
        with patch.object(engine, 'execute', side_effect=self.render), patch.object(engine, 'camera_matches_result', return_value=True):
            while not engine.QUEUE.empty():
                ident = engine.QUEUE.get_nowait()
                try: engine.run_job(ident)
                finally: engine.QUEUE.task_done()

    def run_one(self):
        self.rendered = getattr(self, 'rendered', [])
        with patch.object(engine, 'execute', side_effect=self.render), patch.object(engine, 'camera_matches_result', return_value=True):
            ident = engine.QUEUE.get_nowait()
            try: engine.run_job(ident)
            finally: engine.QUEUE.task_done()

    def jobs(self, origin=None):
        rows = sorted(store.all_records('job'), key=lambda job: job['created_at'])
        return [job for job in rows if origin is None or job.get('origin') == origin]

    def start(self):
        engine.submit('fixture', 'analyze')
        self.run_one(); self.run_one()            # analysis, then the first draft
        self.run_one()                            # other shots before 2K polish


class BackgroundPassTests(PassFixture):
    def test_failed_thumbnail_does_not_prevent_hero_polish(self):
        engine.submit('fixture','analyze');self.run_one();self.run_one()
        thumb=self.jobs(engine.THUMBNAIL_ORIGIN)[0]
        self.rendered=[]
        with patch.object(engine,'execute',side_effect=RuntimeError('thumbnail failed')):
            engine.run_job(thumb['id'])
        self.assertEqual(store.get('job',thumb['id'])['status'],'failed')
        self.assertEqual(self.jobs(engine.POLISH_ORIGIN)[0]['status'],'queued')

    def test_analysis_develops_the_look_before_the_first_image(self):
        engine.submit('fixture', 'analyze'); self.run_one()
        project = store.get('project', 'fixture')
        self.assertIn('亮面金属', project['plan']['lookdev']['summary'])
        self.assertEqual(self.jobs('initial_preview')[0]['plan']['material_overrides'], project['plan']['material_overrides'])

    def test_first_draft_is_followed_by_one_showcase_pass_of_the_same_shot(self):
        self.start()
        polish = self.jobs(engine.POLISH_ORIGIN)
        self.assertEqual(len(polish), 1)
        self.assertEqual((polish[0]['kind'], polish[0]['quality'], polish[0]['shot_ids'], polish[0]['status']), ('render', 'showcase', ['hero'], 'queued'))
        self.assertEqual(store.get('project', 'fixture')['polish']['status'], 'queued')
        self.drain()
        project = store.get('project', 'fixture')
        self.assertEqual(self.rendered[0],('preview','hero'))
        self.assertEqual(self.rendered[-1],('showcase','hero'))
        self.assertEqual({shot for quality,shot in self.rendered if quality=='thumbnail'},
                         {shot['id'] for shot in project['plan']['shots'] if shot['id']!='hero'})
        final = project['outputs'][-1]
        self.assertEqual((final['quality'], final['width'], final['height']), ('showcase', 2048, 2048))
        self.assertEqual(project['polish']['status'], 'completed')
        self.assertEqual(len(self.jobs(engine.POLISH_ORIGIN)), 1)       # finished: never repeated

    def test_a_request_takes_the_renderer_and_the_pass_is_offered_again_afterwards(self):
        self.start()
        first = self.jobs(engine.POLISH_ORIGIN)[0]
        requested = engine.submit('fixture', 'preview', 'preview', ['hero'])
        self.assertNotEqual(requested['id'], first['id'])
        stopped = store.get('job', first['id'])
        self.assertEqual((stopped['status'], stopped['yielded']), ('cancelled', True))
        self.assertEqual(store.get('project', 'fixture')['polish']['status'], 'cancelled')
        self.drain()
        again = self.jobs(engine.POLISH_ORIGIN)
        self.assertEqual([job['status'] for job in again], ['cancelled', 'completed'])
        self.assertEqual(self.rendered.count(('showcase', 'hero')), 1)

    def test_yield_is_what_the_web_layer_calls_before_an_instruction(self):
        self.start()
        self.assertTrue(engine.yield_to_user('fixture'))
        self.assertFalse(engine.yield_to_user('fixture'))
        self.assertFalse(any(job['status'] in ('queued', 'running') for job in self.jobs()))

    def test_a_pass_the_user_cancelled_is_not_started_again(self):
        self.start()
        engine.cancel(self.jobs(engine.POLISH_ORIGIN)[0]['id'])
        engine.submit('fixture', 'preview', 'preview', ['hero']); self.drain()
        self.assertEqual(len(self.jobs(engine.POLISH_ORIGIN)), 1)

    def test_projects_that_did_not_opt_in_get_no_background_pass(self):
        for change in ({'auto_polish': False}, {'look': 'classic'}):
            with self.subTest(change=change):
                project = store.get('project', 'fixture'); project['plan'].update(change); store.put('project', project)
                preview = {'id': 'done-' + str(len(change)), 'project_id': 'fixture', 'kind': 'preview', 'status': 'completed',
                           'shot_ids': ['hero'], 'version': 1, 'created_at': 1}
                store.put('job', preview)
                self.assertIsNone(engine.schedule_polish('fixture', preview['id']))

    def test_showcase_quality_is_a_render_quality_with_its_own_size_contract(self):
        with self.assertRaises(ValueError): engine.submit('fixture', 'preview', 'showcase')
        self.start(); self.drain()
        job = self.jobs(engine.POLISH_ORIGIN)[0]
        folder = self.data / 'wrong'; folder.mkdir(); Image.new('RGB', (512, 512)).save(folder / 'wrong.png')
        with self.assertRaisesRegex(RuntimeError, '2048'):
            engine.output_record(job, job['plan']['shots'][0], folder, {})


class RouteTests(PassFixture):
    """The web layer: requests take the renderer, and older projects can opt in."""
    def setUp(self):
        super().setUp()
        import app as application
        self.client = application.app.test_client()

    def running_pass(self):
        self.start()
        job = self.jobs(engine.POLISH_ORIGIN)[0]
        self.assertEqual(job['status'], 'queued')
        return job

    def test_changing_the_plan_takes_the_renderer_from_the_background_pass(self):
        job = self.running_pass()
        response = self.client.post('/api/projects/fixture/plan', json={'exposure': .3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((store.get('job', job['id'])['status'], store.get('job', job['id'])['yielded']), ('cancelled', True))
        self.assertEqual(response.get_json()['version'], 2)

    def test_reading_and_unrelated_writes_leave_the_pass_alone(self):
        job = self.running_pass()
        self.assertEqual(self.client.get('/api/projects/fixture').status_code, 200)
        self.client.put('/api/projects/fixture/ecommerce', json={'platform': 'general'})
        self.assertEqual(store.get('job', job['id'])['status'], 'queued')

    def test_a_new_render_request_is_accepted_while_the_pass_is_queued(self):
        job = self.running_pass()
        response = self.client.post('/api/projects/fixture/jobs', json={'kind': 'preview', 'shot_ids': ['campaign'], 'expected_version': 1})
        self.assertEqual(response.status_code, 202)
        self.assertNotEqual(response.get_json()['id'], job['id'])
        self.assertEqual(store.get('job', job['id'])['status'], 'cancelled')

    def test_showcase_quality_can_be_requested_as_a_render_but_not_as_a_preview(self):
        self.start(); engine.yield_to_user('fixture')
        accepted = self.client.post('/api/projects/fixture/jobs', json={'kind': 'render', 'quality': 'showcase', 'shot_ids': ['hero']})
        self.assertEqual((accepted.status_code, accepted.get_json()['quality']), (202, 'showcase'))
        engine.cancel(accepted.get_json()['id'])
        refused = self.client.post('/api/projects/fixture/jobs', json={'kind': 'preview', 'quality': 'showcase', 'shot_ids': ['hero']})
        self.assertEqual(refused.status_code, 400)

    def test_switching_an_older_project_to_the_showcase_look_develops_its_materials_once(self):
        project = store.get('project', 'fixture')
        plan = planner.default_plan(); plan['visual_review'] = False
        project.update(plan=plan, analysis=copy.deepcopy(self.analysis), status='ready'); store.put('project', project)
        saved = self.client.post('/api/projects/fixture/plan', json={'look': 'showcase'}).get_json()['plan']
        self.assertIn('亮面金属', saved['lookdev']['summary'])
        self.assertTrue(saved['material_overrides'])
        again = self.client.post('/api/projects/fixture/plan', json={'look': 'classic'}).get_json()['plan']
        self.assertEqual(again['material_overrides'], saved['material_overrides'])     # choices are never silently removed
        chosen = store.get('project', 'fixture'); chosen['plan'] = {**plan, 'material_overrides': [{'name': '铝_1', 'preset': 'rubber'}]}
        store.put('project', chosen)
        kept = self.client.post('/api/projects/fixture/plan', json={'look': 'showcase'}).get_json()['plan']
        self.assertEqual([entry['name'] for entry in kept['material_overrides']], ['铝_1'])
        self.assertNotIn('lookdev', kept)


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: showcase scene not built')
class ShowcaseSceneRuntimeTests(unittest.TestCase):
    """Real Blender: the showcase look builds the set this module describes."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name); data = root / 'data'; data.mkdir()
        obj = root / 'model' / 'box.obj'; obj.parent.mkdir()
        obj.write_text('mtllib box.mtl\no 红色喷漆外壳\nusemtl 红色塑料\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0 0 1\nv 1 0 1\nv 1 1 1\nv 0 1 1\n'
                       'f 1 2 3 4\nf 5 8 7 6\nf 1 5 6 2\nf 2 6 7 3\nf 3 7 8 4\nf 5 1 4 8\n', encoding='utf-8')
        (obj.parent / 'box.mtl').write_text('newmtl 红色塑料\nKd 0.65 0.045 0.045\n', encoding='utf-8')
        patches = [patch.object(module, 'DATA', data) for module in (store, engine)]
        for item in patches: item.start(); cls.addClassCleanup(item.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        plan = planner.default_plan(); plan.update(visual_review=False, look='showcase')
        job = {'id': 'job', 'project_id': 'p', 'status': 'running', 'plan': plan, 'version': 1, 'quality': 'preview'}
        store.put('job', job); store.put('project', {'id': 'p', 'version': 1, 'plan': plan, 'outputs': [], 'analysis': None, 'model_path': str(obj)})
        analysis = engine.execute(job, {'action': 'analyze', 'model_path': str(obj), 'plan': plan}, data / 'p' / 'analysis')
        project = store.get('project', 'p'); project['analysis'] = analysis; store.put('project', project)
        plan = planner.validate(lookdev.apply(planner.enrich(plan, analysis), analysis, str(obj))[0])
        cls.plan = plan; cls.results = {}
        for shot_id in ('hero', 'campaign'):
            shot = next(shot for shot in plan['shots'] if shot['id'] == shot_id)
            out = data / 'p' / 'runs' / shot_id
            cls.results[shot_id] = engine.execute(job, {'action': 'render', 'model_path': str(obj), 'plan': plan, 'shot': shot, 'quality': 'preview'}, out)
            cls.results[shot_id]['png'] = next(out.glob('*.png'))

    def test_placeholder_material_was_developed_from_its_name(self):
        self.assertEqual(self.plan['material_overrides'][0]['lookdev_finish'], 'gloss_plastic')

    def test_white_set_keeps_the_named_lamps_and_a_pure_white_frame_edge(self):
        studio = self.results['hero']['studio']
        names = [lamp['name'] for lamp in studio['lights']]
        for name in ('Key softbox', 'Fill softbox', 'Edge softbox', 'Edge strip left', 'Top reflection', 'Back reflection card', 'Front sweep card'):
            self.assertIn(name, names)
        self.assertNotIn('Backdrop wash', names)
        key = next(lamp for lamp in studio['lights'] if lamp['name'] == 'Key softbox')
        fill = next(lamp for lamp in studio['lights'] if lamp['name'] == 'Fill softbox')
        self.assertGreater(key['energy'], 5 * fill['energy'])
        self.assertTrue(key['casts_shadow']); self.assertFalse(fill['casts_shadow'])
        with Image.open(self.results['hero']['png']) as image:
            self.assertEqual(image.convert('RGB').getpixel((2, 2)), (255, 255, 255))
        operations = json.dumps(self.results['hero'].get('operations', []), ensure_ascii=False)
        self.assertIn('edge_highlight', operations)

    def test_the_product_is_lit_with_contrast_and_keeps_its_colour(self):
        with Image.open(self.results['hero']['png']) as image:
            pixels = list(image.convert('RGB').resize((64, 64)).getdata())
        red = [pixel for pixel in pixels if pixel[0] > pixel[1] + 40 and pixel[0] > pixel[2] + 40]
        self.assertGreater(len(red), 300)
        levels = sorted(pixel[0] for pixel in red)
        self.assertGreater(levels[int(len(levels) * .9)] - levels[int(len(levels) * .1)], 50)   # a lit side and a shaded side
        self.assertLess(sum(1 for pixel in red if pixel[0] >= 250 and pixel[1] > 150) / len(red), .05)   # not washed out

    def test_studio_set_washes_the_backdrop_evenly(self):
        names = [lamp['name'] for lamp in self.results['campaign']['studio']['lights']]
        self.assertIn('Backdrop wash', names)
        with Image.open(self.results['campaign']['png']) as image:
            rgb = image.convert('L')
            top = [rgb.getpixel((x, 6)) for x in range(8, 504, 16)]
        self.assertLess(max(top) - min(top), 40)      # no dark band across the far floor


if __name__ == '__main__':
    unittest.main()

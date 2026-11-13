"""Usage scenes: prop sourcing, the scene-set contract, the stylist agents and set dressing.

No network is used: the catalogue is a small fixture and downloads are
replaced. The last class runs real Blender on a synthetic cabinet and
synthetic props to check where things end up; how a finished scene looks is
judged on real renders, not here.
"""
import copy
import hashlib
import json
import queue
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import engine
import planner
import scene_props
import scene_set
import scene_stylist
import studio_store as store


def asset(name, categories, tags, size, triangles=3000, kind=2):
    return {'name': name, 'categories': categories, 'tags': tags, 'dimensions': size, 'polycount': triangles, 'type': kind}


MODELS = {
    'adjustable_wrench': asset('Adjustable Wrench', ['props', 'tools', 'industrial'], ['hand tool', 'garage'], [68, 30, 254]),
    'combination_wrench': asset('Combination Wrench', ['tools', 'industrial'], ['garage', 'workshop'], [56, 337, 21]),
    'pipe_wrench': asset('Pipe Wrench', ['tools'], ['wrench', 'plumbing'], [90, 40, 405]),
    'pliers': asset('Pliers', ['tools', 'props'], ['combination pliers', 'hand tool'], [55, 18, 180]),
    'screwdriver': asset('Screwdriver', ['tools', 'props'], ['flathead', 'hand tool'], [27, 27, 209]),
    'measuring_tape_01': asset('Measuring Tape 01', ['tools', 'props'], ['tape', 'measure'], [40, 169, 73]),
    'lubricant_spray': asset('Lubricant Spray', ['props', 'containers', 'tools'], ['spray', 'can', 'oil'], [65, 65, 165]),
    'ratchet_wrench': asset('Ratchet Wrench', ['props', 'tools'], ['wrench', 'mechanic'], [44, 43, 246]),
    'cross_pein_hammer': asset('Cross Pein Hammer', ['props', 'tools'], ['hammer', 'blacksmith'], [99, 24, 300]),
    'ornate_war_hammer': asset('Ornate War Hammer', ['props', 'weapons'], ['hammer', 'medieval'], [200, 80, 711]),
    'metal_toolbox': asset('Metal Toolbox', ['containers', 'tools'], ['garage', 'storage'], [400, 200, 220]),
    'potted_plant_01': asset('Potted Plant 01', ['plants', 'potted plants'], ['plant', 'pot'], [589, 637, 1351], 96000),
    'potted_plant_04': asset('Potted Plant 04', ['plants', 'potted plants'], ['plant', 'succulent'], [220, 230, 268]),
    'ceramic_vase_02': asset('Ceramic Vase 02', ['decorative', 'vases'], ['vase', 'ceramic'], [200, 200, 309]),
    'wooden_bowl_02': asset('Wooden Bowl 02', ['dishes', 'props'], ['bowl', 'wood'], [131, 120, 60]),
    'huge_tree': asset('Plant Tree', ['nature', 'trees'], ['plant'], [4000, 4000, 9000], 900000),
    'food_apple_01': asset('Food Apple 01', ['food'], ['apple', 'fruit'], [80, 80, 98]),
}
TEXTURES = {
    'scuffed_cement': asset('Scuffed Cement', ['floor', 'concrete', 'indoor'], ['worn'], [2000, 2000], kind=1),
    'concrete_wall_008': asset('Concrete Wall 008', ['wall', 'concrete'], ['panels'], [2710, 2710], kind=1),
    'marble_01': asset('Marble 01', ['floor', 'indoor', 'clean', 'wall'], ['marble'], [2000, 2000], kind=1),
}
ANALYSIS = {'objects': [{'name': 'G-07C抽屉(2)_8', 'type': 'MESH', 'materials': ['红色']}, {'name': 'G-不锈钢台面_2', 'type': 'MESH', 'materials': ['金属']}],
            'materials': [{'name': '红色'}, {'name': '金属'}], 'features': {'dimensions': [69.3, 37.8, 73.8]},
            'dimensions': {'value': [69.3, 37.8, 73.8], 'unit': 'unknown'}}
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def project(name='红色工具柜', **metadata):
    plan = planner.default_plan(); plan.update(visual_review=False, look='showcase')
    return {'id': 'fixture', 'slug': 'fixture', 'name': name, 'version': 1, 'status': 'ready', 'history': [], 'outputs': [],
            'metadata': metadata, 'analysis': copy.deepcopy(ANALYSIS), 'plan': plan, 'model_path': '/nowhere/cabinet.obj'}


class CatalogueSearchTests(unittest.TestCase):
    def ids(self, concept, **options):
        return [row['asset_id'] for row in scene_props.search(concept, assets=MODELS, **options)]

    def test_the_named_thing_comes_before_things_that_merely_share_a_word(self):
        self.assertEqual(self.ids('adjustable wrench')[0], 'adjustable_wrench')
        self.assertEqual(self.ids('combination wrench')[0], 'combination_wrench')
        self.assertEqual(self.ids('pliers'), ['pliers'])
        self.assertEqual(self.ids('potted plants')[:2], ['potted_plant_01', 'potted_plant_04'])      # plural is not a different word

    def test_size_limits_and_excluded_categories_filter_what_is_offered(self):
        self.assertIn('ornate_war_hammer', self.ids('hammer'))
        self.assertEqual(self.ids('hammer', exclude=('weapons',)), ['cross_pein_hammer'])
        self.assertEqual(self.ids('potted plant', largest_mm=400), ['potted_plant_04'])
        self.assertEqual(self.ids('potted plant', smallest_mm=1000), ['potted_plant_01'])
        self.assertNotIn('huge_tree', self.ids('plant'))                                             # too heavy to be a prop

    def test_nothing_is_returned_for_what_the_catalogue_does_not_have(self):
        self.assertEqual(self.ids('sunglasses'), [])
        self.assertEqual(self.ids(''), [])

    def test_every_result_says_where_it_comes_from(self):
        row = scene_props.search('pliers', assets=MODELS)[0]
        self.assertEqual((row['provider'], row['license'], row['source_url']), ('Poly Haven', 'CC0 1.0', 'https://polyhaven.com/a/pliers'))
        self.assertEqual(row['dimensions_mm'], [55.0, 18.0, 180.0])


class DownloadSafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        mocked = patch.object(scene_props, 'DATA', self.root); mocked.start(); self.addCleanup(mocked.stop)
        scene_props._MEMORY.clear(); self.addCleanup(scene_props._MEMORY.clear)
        self.files = {}

    def listing(self, gltf=None, extra=None):
        document = json.dumps(gltf or {'buffers': [{'uri': 'pliers.bin'}], 'images': [{'uri': 'textures/pliers_diff_1k.jpg'}]}).encode()
        payloads = {'https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/pliers/pliers_1k.gltf': document,
                    'https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/pliers/pliers.bin': b'binary-mesh',
                    'https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/pliers/pliers_diff_1k.jpg': b'jpeg-bytes'}
        self.files.update(payloads)
        item = lambda url: {'url': url, 'size': len(payloads[url]), 'md5': hashlib.md5(payloads[url]).hexdigest()}
        urls = list(payloads)
        include = {'pliers.bin': item(urls[1]), 'textures/pliers_diff_1k.jpg': item(urls[2]), **(extra or {})}
        return {'gltf': {'1k': {'gltf': {**item(urls[0]), 'include': include}}}}

    def fetch(self, listing):
        def reply(url, limit, files=False, cancelled=None):
            scene_props._check(url, files)
            if url.endswith('/assets?t=models'): return json.dumps(MODELS).encode()
            if url.endswith('/files/pliers'): return json.dumps(listing).encode()
            return self.files[url]
        return patch.object(scene_props, '_fetch', side_effect=reply)

    def test_only_the_two_poly_haven_hosts_and_asset_paths_are_ever_contacted(self):
        scene_props._check('https://api.polyhaven.com/assets?t=models')
        scene_props._check('https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/pliers/pliers_1k.gltf', files=True)
        for url, files in (('http://api.polyhaven.com/assets', False), ('https://api.polyhaven.com.evil.test/assets', False),
                           ('https://user@api.polyhaven.com/assets', False), ('https://dl.polyhaven.org/other/path.gltf', True),
                           ('https://example.test/file/ph-assets/Models/a.gltf', True), ('https://api.polyhaven.com/files/x', True)):
            with self.subTest(url=url), self.assertRaises(scene_props.PropError):
                scene_props._check(url, files)

    def test_a_model_is_stored_with_its_files_and_its_source_record(self):
        with self.fetch(self.listing()):
            record = scene_props.fetch_model('pliers', self.root / 'cache')
        folder = self.root / 'cache' / 'pliers'
        self.assertEqual(Path(record['file']), folder / 'pliers.gltf')
        self.assertEqual((folder / 'pliers.bin').read_bytes(), b'binary-mesh')
        self.assertTrue((folder / 'textures' / 'pliers_diff_1k.jpg').is_file())
        source = json.loads((folder / 'source.json').read_text(encoding='utf-8'))
        self.assertEqual((source['license'], source['source_url'], source['license_url']),
                         ('CC0 1.0', 'https://polyhaven.com/a/pliers', 'https://polyhaven.com/license'))

    def test_a_file_that_does_not_match_its_listed_checksum_is_refused(self):
        listing = self.listing()
        self.files['https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/pliers/pliers.bin'] = b'tampered---'
        with self.fetch(listing), self.assertRaisesRegex(scene_props.PropError, '校验失败'):
            scene_props.fetch_model('pliers', self.root / 'cache')
        self.assertFalse((self.root / 'cache' / 'pliers' / 'pliers.bin').exists())

    def test_a_model_may_only_refer_to_the_files_downloaded_with_it(self):
        for uri in ('https://evil.test/payload.bin', '../../outside.bin', 'other.bin'):
            with self.subTest(uri=uri), self.fetch(self.listing({'buffers': [{'uri': uri}]})), self.assertRaisesRegex(scene_props.PropError, '未随附'):
                scene_props.fetch_model('pliers', self.root / ('cache-' + str(len(uri))))

    def test_unsafe_listed_file_names_are_refused(self):
        payload = {'url': 'https://dl.polyhaven.org/file/ph-assets/Models/x', 'size': 1, 'md5': '0' * 32}
        for name in ('../escape.bin', '/abs/file.bin', 'script.py', 'a\\b.bin'):
            with self.subTest(name=name), self.fetch(self.listing(extra={name: payload})), self.assertRaises(scene_props.PropError):
                scene_props.fetch_model('pliers', self.root / 'unsafe')

    def test_an_unknown_asset_is_never_requested(self):
        with self.fetch(self.listing()) as fetch, self.assertRaises(scene_props.PropError):
            scene_props.fetch_model('not_in_catalogue', self.root / 'cache')
        self.assertFalse(any('/files/' in call.args[0] for call in fetch.call_args_list))

    def test_the_catalogue_falls_back_to_its_last_copy_when_offline(self):
        with self.fetch(self.listing()):
            self.assertEqual(len(scene_props.catalogue('models')), len(MODELS))
        scene_props._MEMORY.clear()
        stale = self.root / 'props_cache' / 'catalogue-models.json'
        import os; os.utime(stale, (1, 1))
        with patch.object(scene_props, '_fetch', side_effect=scene_props.PropError('offline')):
            self.assertEqual(len(scene_props.catalogue('models')), len(MODELS))


class SceneSetContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = planner.default_plan()
        self.plan['shots'].append({'id': 'scene', 'label': '使用场景', 'kind': 'full', 'projection': 'perspective', 'azimuth': 35,
                                   'elevation': 30, 'focal_length': 55, 'style': 'soft', 'camera_distance': 6.5, 'target_offset': [0, 0, 0]})
        self.set = {'version': 1, 'shot_id': 'scene', 'setting': 'workshop', 'source': 'rules', 'units_per_meter': 2.71,
                    'product': {'type': '工具柜', 'max_dimension_mm': 738, 'size_basis': 'model_units'},
                    'props': [{'id': 'p1', 'asset_id': 'pliers', 'name': 'Pliers', 'file': '/data/p/props/models/pliers/pliers.gltf',
                               'zone': 'on_top', 'pose': 'flat', 'matrix': list(IDENTITY), 'license': 'CC0 1.0',
                               'source_url': 'https://polyhaven.com/a/pliers'}],
                    'open': {'objects': ['抽屉'], 'translation': [0, -.58, 0]},
                    'surface': {'asset_id': 'scuffed_cement', 'textures': {'base_color': '/data/p/props/surfaces/a.jpg'}, 'tile_m': 2, 'color': [.5, .5, .5]},
                    'wall': {'color': [.9, .9, .88], 'distance_m': .5}, 'basis': scene_set.basis(self.plan), 'seed': 7, 'skipped': []}

    def test_a_complete_scene_set_is_part_of_a_valid_plan(self):
        plan = planner.validate({**self.plan, 'scene_set': self.set})
        self.assertEqual(plan['scene_set']['props'][0]['asset_id'], 'pliers')
        self.assertEqual(scene_set.files(self.set), ['/data/p/props/models/pliers/pliers.gltf', '/data/p/props/surfaces/a.jpg'])

    def test_anything_that_is_not_plain_local_data_is_rejected(self):
        def changed(path, value):
            broken = copy.deepcopy(self.set); target = broken
            for key in path[:-1]: target = target[key]
            target[path[-1]] = value
            return broken
        cases = [(['shot_id'], 'missing'), (['setting'], 'moon'), (['source'], 'someone'), (['units_per_meter'], float('nan')),
                 (['props', 0, 'file'], 'https://evil.test/a.gltf'), (['props', 0, 'file'], '/data/p/props/a.blend'),
                 (['props', 0, 'zone'], 'floating'), (['props', 0, 'matrix'], [1, 2, 3]), (['props', 0, 'id'], 'x'),
                 (['props', 0, 'asset_id'], '../etc'), (['open', 'translation'], [0, 0]), (['open', 'objects'], []),
                 (['surface', 'textures'], {'emission': '/a.jpg'}), (['surface', 'textures', 'base_color'], 'data:image/png;base64,AA'),
                 (['wall', 'color'], [2, 0, 0]), (['basis'], {}), (['version'], 2), (['props'], [copy.deepcopy(self.set['props'][0])] * 13)]
        for path, value in cases:
            with self.subTest(path=path), self.assertRaises(ValueError):
                planner.validate({**self.plan, 'scene_set': changed(path, value)})
        with self.assertRaises(ValueError):
            planner.validate({**self.plan, 'scene_set': {**self.set, 'run': 'import os'}})

    def test_the_basis_changes_when_the_model_is_turned_or_resized(self):
        base = scene_set.basis(self.plan)
        self.assertNotEqual(scene_set.basis({**self.plan, 'rotation': [0, 0, 90]}), base)
        self.assertNotEqual(scene_set.basis({**self.plan, 'dimensions_mm': [700, 400, 740]}), base)
        self.assertEqual(scene_set.basis({**self.plan, 'exposure': .4}), base)


class DirectorAndScoutTests(unittest.TestCase):
    def test_recipes_are_chosen_from_what_the_project_says_it_is(self):
        self.assertEqual(scene_stylist.recipe_for(project('红色工具柜'))['id'], 'tool_storage')
        self.assertEqual(scene_stylist.recipe_for(project('哑光塑料按压瓶'))['id'], 'personal_care')
        self.assertEqual(scene_stylist.recipe_for(project('厚壁透明玻璃杯'))['id'], 'tableware')
        self.assertEqual(scene_stylist.recipe_for(project('帆布皮革手提包'))['id'], 'bag')
        self.assertEqual(scene_stylist.recipe_for(project('Model 7', product_category='tool cabinet'))['id'], 'tool_storage')
        nameless = project('零件 A'); nameless['analysis']['objects'] = [{'name': 'Body', 'type': 'MESH'}]
        self.assertEqual(scene_stylist.recipe_for(nameless)['id'], 'generic')
        self.assertEqual(scene_stylist.recipe_for(nameless, hint='放在厨房里的水壶')['id'], 'tableware')

    def test_part_names_alone_can_identify_a_product(self):
        unnamed = project('08_G07C_G02M_combo')
        self.assertEqual(scene_stylist.recipe_for(unnamed)['id'], 'furniture')       # drawers and a top, nothing says "tool"
        wheeled = copy.deepcopy(unnamed); wheeled['analysis']['objects'].append({'name': '2.5寸轮子_4', 'type': 'MESH'})
        self.assertEqual(scene_stylist.recipe_for(wheeled)['id'], 'tool_storage')    # drawers on casters: a workshop trolley
        unnamed['analysis']['objects'].append({'name': '工具挂板_1', 'type': 'MESH'})
        self.assertEqual(scene_stylist.recipe_for(unnamed)['id'], 'tool_storage')

    def test_real_size_prefers_the_user_then_plausible_units_then_the_category(self):
        cabinet = project()
        self.assertEqual(scene_stylist.real_size(cabinet, (300, 1800)), (738.0, 'model_units'))      # 73.8 units read as centimetres
        cabinet['plan']['dimensions_mm'] = [690, 380, 760]
        self.assertEqual(scene_stylist.real_size(cabinet, (300, 1800)), (760.0, 'user'))
        odd = project(); odd['analysis']['dimensions']['value'] = [.002, .001, .003]; odd['analysis']['features'] = {}
        size, basis = scene_stylist.real_size(odd, (300, 1800))
        self.assertEqual(basis, 'category'); self.assertTrue(300 < size < 1800)
        self.assertEqual(scene_stylist.real_size(project(), (300, 1800), estimate=820), (820.0, 'ai'))

    def brief(self, *props, setting='workshop'):
        return {'setting': setting, 'props': [((concept,), zone, pose) for concept, zone, pose in props]}

    def test_the_scout_sizes_props_to_the_product(self):
        brief = self.brief(('potted plant', 'behind', 'upright'), ('hammer', 'beside', 'flat'))
        with patch.object(scene_stylist, '_config', return_value=None):
            small, skipped_small = scene_stylist.scout(brief, 150, MODELS)
            large, _ = scene_stylist.scout(brief, 800, MODELS)
        self.assertEqual([row['asset_id'] for row in small], ['potted_plant_04'])        # a 1.35 m plant beside a 15 cm bottle is not offered
        self.assertEqual(skipped_small, [{'concept': 'hammer', 'reason': 'not_in_catalogue'}])
        self.assertEqual([row['asset_id'] for row in large], ['potted_plant_01', 'cross_pein_hammer'])
        self.assertNotIn('ornate_war_hammer', [row['asset_id'] for row in large])       # weapons are never props

    def test_the_same_model_is_not_used_twice(self):
        brief = self.brief(('adjustable wrench', 'on_top', 'flat'), ('adjustable wrench', 'inside', 'flat'))
        with patch.object(scene_stylist, '_config', return_value=None):
            choices, skipped = scene_stylist.scout(brief, 740, MODELS)
        self.assertEqual(len(choices), 1); self.assertEqual(skipped[0]['reason'], 'duplicate')

    def test_a_model_scout_may_choose_among_candidates_or_decline_but_not_invent(self):
        brief = self.brief(('wrench', 'on_top', 'flat'), ('pliers', 'on_top', 'flat'))
        answers = [{'choices': [{'index': 0, 'asset_id': 'ratchet_wrench'}]}, {'choices': [{'index': 0, 'asset_id': ''}]},
                   {'choices': [{'index': 0, 'asset_id': 'huge_tree'}]}]
        expected = [['ratchet_wrench', 'pliers'], ['pliers'], None]
        for answer, wanted in zip(answers, expected):
            with self.subTest(answer=answer), patch.object(scene_stylist, '_config', return_value={'enabled': True}), \
                    patch.object(scene_stylist, '_ask', return_value=answer) as ask:
                choices, _ = scene_stylist.scout(brief, 740, MODELS)
            picked = [row['asset_id'] for row in choices]
            if wanted is None:
                self.assertEqual(len(picked), 2); self.assertNotIn('huge_tree', picked)
            else:
                self.assertEqual(picked, wanted)
            self.assertEqual(len(ask.call_args.args[2]['wishes']), 1)                    # only the wish that had a real choice was asked

    def test_the_director_uses_the_model_when_there_is_one_and_rules_when_it_fails(self):
        answer = {'product_type': '工具柜', 'setting': 'workshop', 'max_dimension_mm': 800, 'open_compartment': False, 'summary': '车间里的工具柜。',
                  'props': [{'concept': 'pliers', 'zone': 'inside', 'pose': 'flat'}, {'concept': 'metal toolbox', 'zone': 'beside', 'pose': 'upright'}]}
        with patch.object(scene_stylist, '_config', return_value={'enabled': True}), patch.object(scene_stylist, '_ask', return_value=answer) as ask:
            brief, source = scene_stylist.direct(project(), '', None, MODELS)
        self.assertEqual((source, brief['estimate_mm'], brief['open']), ('ai', 800, ()))
        self.assertEqual(brief['props'][0], (('pliers',), 'on_top', 'flat'))             # nothing goes "inside" a product that is not opened
        facts = ask.call_args.args[2]
        self.assertIn('Pliers', facts['available_models']); self.assertNotIn('Ornate War Hammer', facts['available_models'])
        with patch.object(scene_stylist, '_config', return_value={'enabled': True}), patch.object(scene_stylist, '_ask', side_effect=ValueError('timeout')):
            brief, source = scene_stylist.direct(project(), '', None, MODELS)
        self.assertEqual((source, brief['recipe']), ('rules', 'tool_storage'))


class StylistFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for module in (store, engine, scene_props, scene_stylist):
            mocked = patch.object(module, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        for name, value in [('QUEUE', queue.Queue()), ('RUNNING_JOBS', set()), ('PROCESSES', {})]:
            mocked = patch.object(engine, name, value); mocked.start(); self.addCleanup(mocked.stop)
        for target, name, value in ((engine, 'require_runtime', None), (engine, 'blender_path', '/usr/bin/blender'),
                                    (scene_stylist, '_config', None)):
            mocked = patch.object(target, name, return_value=value); mocked.start(); self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        store.put('project', project())
        scene_props._MEMORY.clear(); self.addCleanup(scene_props._MEMORY.clear)
        mocked = patch.object(scene_props, 'catalogue', side_effect=lambda kind='models', refresh=False: MODELS if kind == 'models' else TEXTURES)
        mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_props, 'fetch_model', side_effect=self.fetch_model); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(scene_props, 'fetch_surface', side_effect=self.fetch_surface); mocked.start(); self.addCleanup(mocked.stop)
        self.layout = patch.object(scene_stylist, '_layout', side_effect=self.solve).start(); self.addCleanup(patch.stopall)
        self.unplaced = set(); self.failed_downloads = set()

    def fetch_model(self, asset_id, folder, resolution='1k', cancelled=None):
        if asset_id in self.failed_downloads: raise scene_props.PropError('无法连接 Poly Haven，请检查网络后重试')
        target = Path(folder) / asset_id; target.mkdir(parents=True, exist_ok=True)
        (target / (asset_id + '.gltf')).write_text('{}', encoding='utf-8')
        return {**scene_props.describe(asset_id, MODELS[asset_id]), 'file': str(target / (asset_id + '.gltf'))}

    def fetch_surface(self, asset_id, folder, resolution='2k', cancelled=None):
        target = Path(folder) / asset_id; target.mkdir(parents=True, exist_ok=True)
        path = target / (asset_id + '_base_color_2k.jpg'); path.write_bytes(b'jpeg')
        return {'asset_id': asset_id, 'name': TEXTURES[asset_id]['name'], 'textures': {'base_color': str(path)}, 'tile_m': 2.0,
                'source_url': scene_props.PAGE + asset_id, 'license': scene_props.LICENSE}

    def solve(self, project_record, plan, shot, props, units, seed, keywords, folder, elevation_open, wall_distance_m=.6):
        self.request = {'props': copy.deepcopy(props), 'units': units, 'keywords': keywords, 'shot': copy.deepcopy(shot)}
        rows = [{'id': prop['id'], 'placed': prop['asset_id'] not in self.unplaced, 'zone_used': prop['zone'], 'reason': 'no_room',
                 'matrix': list(IDENTITY), 'size_mm': [10, 10, 10]} for prop in props]
        return {'success': True, 'props': rows, 'open': {'objects': ['G-07C抽屉(2)_8'], 'translation': [0, -.58, 0]},
                'camera': {'camera_distance': 6.4, 'target_offset': [.02, 0, .01], 'elevation': 36.0}}

    def run_task(self, **body):
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            task = scene_stylist.submit('fixture', {'expected_version': 1, **body})
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        return store.get(scene_stylist.KIND, task['id'])

    def test_a_tool_cabinet_gets_a_workshop_with_tools_in_one_undoable_version(self):
        task = self.run_task(seed=5)
        self.assertEqual((task['status'], task['applied_version']), ('completed', 2))
        saved = store.get('project', 'fixture'); plan = saved['plan']; scene = plan['scene_set']
        self.assertEqual((saved['version'], saved['history'][-1]['reason'], saved['history'][-1]['version']), (2, '生成使用场景', 1))
        self.assertNotIn('scene_set', saved['history'][-1]['plan'])
        shot = plan['shots'][-1]
        self.assertEqual((shot['id'], shot['projection'], shot['camera_distance'], shot['elevation']), ('scene', 'perspective', 6.4, 36.0))
        self.assertEqual((scene['setting'], scene['source'], scene['product']['size_basis'], scene['seed']), ('workshop', 'rules', 'model_units', 5))
        self.assertAlmostEqual(scene['units_per_meter'], 2000 / 738)
        zones = {prop['asset_id']: prop['zone'] for prop in scene['props']}
        self.assertEqual(zones['adjustable_wrench'], 'on_top'); self.assertEqual(zones['combination_wrench'], 'inside')
        self.assertEqual(zones['metal_toolbox'], 'beside')
        self.assertEqual([prop['id'] for prop in scene['props']], ['p%d' % (n + 1) for n in range(len(scene['props']))])
        for path in scene_set.files(scene):
            self.assertTrue(Path(path).is_file()); self.assertIn(self.data / 'fixture' / 'props', Path(path).parents)
        self.assertEqual((scene['surface']['asset_id'], scene['wall']['asset_id']), ('scuffed_cement', 'concrete_wall_008'))
        self.assertEqual(self.request['keywords'], ('抽屉', 'drawer'))
        preview = store.get('job', task['job_id'])
        self.assertEqual((preview['kind'], preview['shot_ids'], preview['version']), ('preview', ['scene'], 2))

    def test_what_could_not_be_found_fetched_or_placed_is_recorded_not_hidden(self):
        self.unplaced = {'metal_toolbox'}; self.failed_downloads = {'pliers'}
        task = self.run_task()
        scene = store.get('project', 'fixture')['plan']['scene_set']
        reasons = {row.get('asset_id') or row['concept']: row['reason'] for row in scene['skipped']}
        self.assertEqual((reasons['metal_toolbox'], reasons['pliers']), ('no_room', 'download_failed'))
        self.assertNotIn('metal_toolbox', [prop['asset_id'] for prop in scene['props']])
        self.assertEqual(task['result']['placed'], len(scene['props']))

    def test_generating_again_replaces_the_scene_and_removing_it_restores_the_plan(self):
        self.run_task(seed=1)
        with self.assertRaises(engine.JobConflictError):          # its own preview is still rendering
            scene_stylist.submit('fixture', {'expected_version': 2, 'seed': 2})
        for job in store.all_records('job'):
            if job['status'] in ('queued', 'running'): engine.cancel(job['id'])
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            scene_stylist.submit('fixture', {'expected_version': 2, 'seed': 2})
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        saved = store.get('project', 'fixture')
        self.assertEqual(saved['version'], 3)
        self.assertEqual([shot['id'] for shot in saved['plan']['shots']].count('scene'), 1)
        self.assertEqual(saved['plan']['scene_set']['seed'], 2)
        for job in store.all_records('job'):
            if job['status'] in ('queued', 'running'): engine.cancel(job['id'])
        removed = scene_stylist.remove('fixture', 3)
        self.assertEqual(removed['version'], 4)
        self.assertNotIn('scene_set', removed['plan']); self.assertNotIn('scene', [shot['id'] for shot in removed['plan']['shots']])
        self.assertEqual(removed['history'][-1]['reason'], '移除使用场景')

    def test_a_plan_changed_meanwhile_is_never_overwritten(self):
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            task = scene_stylist.submit('fixture', {'expected_version': 1})
        changed = store.get('project', 'fixture'); changed['version'] = 2; changed['plan']['exposure'] = .5; store.put('project', changed)
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        task = store.get(scene_stylist.KIND, task['id'])
        self.assertEqual(task['status'], 'failed'); self.assertIn('已被修改', task['error'])
        self.assertNotIn('scene_set', store.get('project', 'fixture')['plan'])

    def test_requests_are_validated_and_one_scene_is_built_at_a_time(self):
        for body in ({'hint': 'x' * 400}, {'expected_version': 0}, {'seed': 'lucky'}, {'expected_version': 9}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                scene_stylist.submit('fixture', body)
        with patch.object(scene_stylist.threading, 'Thread'):
            task = scene_stylist.submit('fixture', {'expected_version': 1})
            with self.assertRaises(engine.JobConflictError):
                scene_stylist.submit('fixture', {'expected_version': 1})
            self.assertEqual(scene_stylist.cancel('fixture', task['id'])['status'], 'cancelled')
            scene_stylist.submit('fixture', {'expected_version': 1})
        self.assertEqual(scene_stylist.latest('fixture')['status'], 'queued')

    def test_nothing_usable_fails_cleanly_and_changes_nothing(self):
        self.failed_downloads = set(MODELS)
        task = self.run_task()
        self.assertEqual(task['status'], 'failed'); self.assertIn('下载失败', task['error'])
        self.assertEqual(store.get('project', 'fixture')['version'], 1)

    def test_the_web_layer_exposes_the_task_and_keeps_scene_files_inside_the_project(self):
        import app as application
        client = application.app.test_client()
        with patch.object(scene_stylist.threading, 'Thread') as thread:
            response = client.post('/api/projects/fixture/scene', json={'expected_version': 1})
        self.assertEqual(response.status_code, 202)
        scene_stylist._run(thread.call_args.kwargs['args'][0])
        view = client.get('/api/projects/fixture').get_json()
        self.assertEqual((view['scene_task']['status'], view['version']), ('completed', 2))
        self.assertEqual(client.get('/api/projects/fixture/scene').get_json()['scene_set']['setting'], 'workshop')
        outside = copy.deepcopy(view['plan']); outside['scene_set']['props'][0]['file'] = str(self.data / 'elsewhere' / 'a.gltf')
        with self.assertRaisesRegex(ValueError, '下载到当前项目'):
            application.validate_project_plan('fixture', outside)
        for job in store.all_records('job'):
            if job['status'] in ('queued', 'running'): engine.cancel(job['id'])
        removed = client.post('/api/projects/fixture/scene/remove', json={'expected_version': 2}).get_json()
        self.assertNotIn('scene_set', removed['plan'])


def box(name, low, high, material, open_top=False):
    x0, y0, z0 = low; x1, y1, z1 = high
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(1, 2, 3, 4), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1)] + ([] if open_top else [(5, 8, 7, 6)])
    return name, material, corners, faces


def write_obj(path, boxes):
    lines = ['mtllib cabinet.mtl']; offset = 0
    for name, material, corners, faces in boxes:
        lines.append('o ' + name); lines.append('usemtl ' + material)
        lines += ['v %s %s %s' % (x, z, -y) for x, y, z in corners]          # OBJ is Y-up; the app imports it Z-up
        lines += ['f ' + ' '.join(str(index + offset) for index in face) for face in faces]
        offset += len(corners)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (path.parent / 'cabinet.mtl').write_text('newmtl 红色塑料\nKd 0.6 0.05 0.05\nnewmtl 铝\nKd 0.6 0.6 0.6\n', encoding='utf-8')


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: set dressing not run')
class SetDressingRuntimeTests(unittest.TestCase):
    """Real Blender: a synthetic cabinet with one drawer, and three synthetic props."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name); cls.data = root / 'data'; cls.data.mkdir()
        model = root / 'model' / 'cabinet.obj'; model.parent.mkdir()
        # A body 1.0 wide, 0.6 deep, 1.0 high with a recess for an open-topped drawer behind its front face (y = -0.3).
        write_obj(model, [box('柜体_下', (-.5, -.3, 0), (.5, .3, .7), '红色塑料'), box('柜体_上', (-.5, -.3, .9), (.5, .3, 1), '铝'),
                          box('柜体_后', (-.5, .2, .7), (.5, .3, .9), '红色塑料'),
                          box('抽屉_1', (-.45, -.3, .72), (.45, .18, .88), '红色塑料', open_top=True),
                          box('抽屉底板_1', (-.44, -.29, .72), (.44, .17, .735), '红色塑料')])
        props = root / 'props'; props.mkdir()
        script = root / 'make_props.py'
        script.write_text('''
import bpy, sys
out = sys.argv[sys.argv.index('--') + 1]
for name, size in (('tool', (.03, .03, .2)), ('can', (.06, .06, .16)), ('crate', (.3, .2, .2))):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0)); bpy.context.object.scale = size
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.export_scene.gltf(filepath=out + '/' + name + '.glb', export_format='GLB')
''', encoding='utf-8')
        ran = subprocess.run([shutil.which('blender'), '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(script), '--', str(props)],
                             capture_output=True, text=True, timeout=180)
        if ran.returncode: raise AssertionError(ran.stdout[-3000:] + ran.stderr[-2000:])
        patches = [patch.object(module, 'DATA', cls.data) for module in (store, engine, scene_stylist)]
        for item in patches: item.start(); cls.addClassCleanup(item.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        plan = planner.default_plan(); plan.update(visual_review=False, look='showcase')
        cls.job = {'id': 'job', 'project_id': 'p', 'status': 'running', 'plan': plan, 'version': 1, 'quality': 'preview'}
        store.put('job', cls.job)
        cls.project = {'id': 'p', 'version': 1, 'plan': plan, 'outputs': [], 'analysis': None, 'model_path': str(model)}
        store.put('project', cls.project)
        analysis = engine.execute(cls.job, {'action': 'analyze', 'model_path': str(model), 'plan': plan}, cls.data / 'p' / 'analysis')
        cls.project['analysis'] = analysis; store.put('project', cls.project)
        cls.shot = {'id': 'scene', 'label': '使用场景', 'kind': 'full', 'style': 'soft', 'projection': 'perspective', 'azimuth': 30,
                    'elevation': 24, 'focal_length': 55}
        cls.props = [{'id': 'p1', 'asset_id': 'tool', 'file': str(props / 'tool.glb'), 'zone': 'inside', 'pose': 'auto'},
                     {'id': 'p2', 'asset_id': 'can', 'file': str(props / 'can.glb'), 'zone': 'on_top', 'pose': 'upright'},
                     {'id': 'p3', 'asset_id': 'crate', 'file': str(props / 'crate.glb'), 'zone': 'beside', 'pose': 'upright'},
                     {'id': 'p4', 'asset_id': 'missing', 'file': str(props / 'missing.glb'), 'zone': 'beside', 'pose': 'auto'}]
        cls.units = 2.0                      # the 1 m cabinet is two work units high
        cls.layout = scene_stylist._layout(cls.project, plan, cls.shot, cls.props, cls.units, 3, ('抽屉', 'drawer'), cls.data / 'p' / 'layout', 36)
        cls.rows = {row['id']: row for row in cls.layout['props']}

    def test_the_drawer_is_found_by_name_and_slides_out_of_the_face_towards_the_camera(self):
        opening = self.layout['open']
        self.assertEqual(sorted(opening['objects']), ['抽屉_1', '抽屉底板_1'])
        x, y, z = opening['translation']
        self.assertAlmostEqual(x, 0, places=5); self.assertAlmostEqual(z, 0, places=5); self.assertLess(y, -.5)
        self.assertGreater(opening['cavity_depth'], .2)

    def test_each_prop_rests_where_its_zone_says_at_real_size(self):
        tool, can, crate = self.rows['p1'], self.rows['p2'], self.rows['p3']
        self.assertEqual((tool['zone_used'], can['zone_used'], crate['zone_used']), ('inside', 'on_top', 'beside'))
        self.assertEqual([round(v) for v in can['size_mm']], [60, 60, 160])
        (low, high) = can['box']
        self.assertAlmostEqual(low[2], 2.0, delta=.01)                                  # standing on the top
        self.assertAlmostEqual(high[2] - low[2], .16 * self.units, delta=.01)
        (low, high) = tool['box']
        self.assertLess(high[2] - low[2], .08)                                          # a long thin tool lies down
        self.assertTrue(1.44 < low[2] < 1.52); self.assertLess(high[1], -.6)            # on the drawer floor, in front of the body
        (low, high) = crate['box']
        self.assertAlmostEqual(low[2], 0, delta=.01)
        self.assertTrue(low[0] > 1.0 or high[0] < -1.0 or high[1] < -.6 or low[1] > .6)  # beside the body, not inside it

    def test_a_prop_that_cannot_be_imported_is_reported_and_the_rest_still_stand(self):
        self.assertEqual((self.rows['p4']['placed'], self.rows['p4']['reason']), (False, 'import_failed'))
        self.assertEqual(sum(row['placed'] for row in self.layout['props']), 3)

    def test_the_camera_looks_into_the_drawer_and_keeps_the_product_the_subject(self):
        camera = self.layout['camera']
        self.assertEqual(camera['elevation'], 36.0)
        self.assertIn('camera_distance', camera); self.assertEqual(len(camera['target_offset']), 3)

    def test_a_solid_block_called_a_drawer_is_left_shut_and_its_tool_goes_on_top(self):
        root = Path(self.temp.name); model = root / 'solid' / 'cabinet.obj'; model.parent.mkdir()
        write_obj(model, [box('柜体_下', (-.5, -.3, 0), (.5, .3, .7), '红色塑料'), box('柜体_上', (-.5, -.3, .9), (.5, .3, 1), '铝'),
                          box('柜体_后', (-.5, .2, .7), (.5, .3, .9), '红色塑料'), box('抽屉_1', (-.45, -.3, .72), (.45, .18, .88), '红色塑料')])
        project = {**self.project, 'model_path': str(model)}
        layout = scene_stylist._layout(project, project['plan'], self.shot, self.props[:1], self.units, 3, ('抽屉', 'drawer'),
                                       self.data / 'p' / 'layout-solid', 36)
        self.assertIsNone(layout['open'])                                               # nothing to look into: nothing is pulled out
        row = layout['props'][0]
        self.assertEqual((row['placed'], row['zone_used']), (True, 'on_top'))
        self.assertEqual(layout['camera']['elevation'], 24)                             # and the camera is not raised for it
        self.assertAlmostEqual(layout['product_box'][0][1], -.6, delta=.01)             # the front face is where it was
        self.assertLess(self.layout['product_box'][0][1], -.9)                          # unlike the real drawer, which stands out

    def scene_plan(self, **changes):
        plan = copy.deepcopy(self.project['plan'])
        shot = {**self.shot, **{key: value for key, value in self.layout['camera'].items()}}
        plan['shots'].append(shot)
        plan['scene_set'] = {'version': 1, 'shot_id': 'scene', 'setting': 'workshop', 'source': 'rules', 'units_per_meter': self.units,
                             'props': [{**{key: prop[key] for key in ('id', 'asset_id', 'file', 'pose')}, 'zone': self.rows[prop['id']]['zone_used'],
                                        'matrix': self.rows[prop['id']]['matrix']} for prop in self.props if self.rows[prop['id']]['placed']],
                             'open': self.layout['open'], 'surface': {'color': [.5, .5, .48]}, 'wall': {'color': [.6, .6, .58], 'distance_m': .4},
                             'basis': scene_set.basis(plan), 'seed': 3}
        plan.update(changes)
        return planner.validate(plan), shot

    def test_a_render_rebuilds_the_layout_and_props_never_count_as_product(self):
        plan, shot = self.scene_plan()
        out = self.data / 'p' / 'runs' / 'scene'
        result = engine.execute(self.job, {'action': 'render', 'model_path': self.project['model_path'], 'plan': plan, 'shot': shot,
                                           'quality': 'preview'}, out)
        operation = next(item for item in result['operations'] if item.get('operation') == 'scene_set')
        self.assertEqual((operation['props'], operation['opened']), (['tool', 'can', 'crate'], 2))
        self.assertIn('room_light', result['studio']['scene_set'])
        with Image.open(next(out.glob('*.png'))) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertLess(image.convert('L').getpixel((6, 6)), 250)                    # a room, not a white sweep
        inspect = out / 'inspect.py'
        inspect.write_text('''
import bpy, json, sys
scene = bpy.context.scene
registered = json.loads(scene['studio_product_objects'])
print('REPORT' + json.dumps({'registered': registered, 'props': sorted(o.name for o in scene.objects if o.name.startswith('Studio prop ') and o.type == 'MESH'),
      'wall': 'Studio scene wall' in scene.objects}, ensure_ascii=False))
''', encoding='utf-8')
        ran = subprocess.run([shutil.which('blender'), '-b', str(next(out.glob('*.blend'))), '--python', str(inspect)], capture_output=True, text=True, timeout=180)
        report = json.loads(next(line for line in ran.stdout.splitlines() if line.startswith('REPORT'))[6:])
        self.assertEqual(len(report['props']), 3); self.assertTrue(report['wall'])
        self.assertFalse(any(name.startswith('Studio prop') for name in report['registered']))
        self.assertIn('抽屉_1', report['registered'])

    def test_other_shots_are_untouched_and_a_turned_model_drops_the_stale_layout(self):
        plan, shot = self.scene_plan()
        hero = plan['shots'][0]
        result = engine.execute(self.job, {'action': 'render', 'model_path': self.project['model_path'], 'plan': plan, 'shot': hero,
                                           'quality': 'preview'}, self.data / 'p' / 'runs' / 'hero')
        self.assertFalse(any(item.get('operation') == 'scene_set' for item in result['operations']))
        turned, shot = self.scene_plan()
        turned['rotation'] = [0, 0, 90]
        result = engine.execute(self.job, {'action': 'render', 'model_path': self.project['model_path'], 'plan': turned, 'shot': shot,
                                           'quality': 'preview'}, self.data / 'p' / 'runs' / 'turned')
        self.assertFalse(any(item.get('operation') == 'scene_set' for item in result['operations']))
        self.assertTrue(any('重新生成使用场景' in warning for warning in result['warnings']))


if __name__ == '__main__':
    unittest.main()

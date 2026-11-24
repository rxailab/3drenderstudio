"""The listing workspace's settings: how the product is installed (and why it was read so), brands shared between
projects, what the next build will roughly cost and what a project has spent. No model service is called."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import ai_client
import ai_transport
import brandkit_review
import brandkit_set
import listing
import mounting
import studio_store as store
from test_listing import StoreCase
from test_mounting import cabinet
from test_scene_images import project


class MountingReasonTests(StoreCase):
    def test_the_reason_names_the_words_it_was_read_from_and_always_agrees_with_of(self):
        cases = [cabinet(name='Wall-Mounted Tool Cabinet'), cabinet(name='壁挂工具柜'), cabinet(metadata={'product_category': 'wall cabinet'}),
                 cabinet(orientation={'product': 'wall-mounted storage cabinet'}), cabinet(analysis={'objects': [{'name': 'Keyhole_slot.001'}]}),
                 cabinet(name='Freestanding cabinet, no wall mounting needed'), cabinet(name='落地工具柜'), cabinet(name='Service Cart'),
                 cabinet(name='Wall-Mounted Tool Cabinet', metadata={'mounting': 'free'}), cabinet(name='Storage box', metadata={'mounting': 'wall'})]
        for case in cases:
            with self.subTest(name=case['name'], metadata=case['metadata']):
                self.assertEqual(mounting.reason(case)[0], mounting.of(case))
        self.assertEqual(mounting.reason(cabinet(name='Wall-Mounted Tool Cabinet')), ('wall', '判断为壁挂：名称含 Wall-Mounted'))
        self.assertEqual(mounting.reason(cabinet(metadata={'product_category': 'wall cabinet'}))[1], '判断为壁挂：类目含 wall cabinet')
        self.assertIn('零件名含 Keyhole_slot', mounting.reason(cabinet(analysis={'objects': [{'name': 'Keyhole_slot.001'}]}))[1])
        self.assertIn('视觉模型', mounting.reason(cabinet(orientation={'product': 'wall-mounted storage cabinet'}))[1])
        self.assertEqual(mounting.reason(cabinet(name='落地工具柜')), (None, '判断为落地·台面：名称含 落地'))
        self.assertEqual(mounting.reason(cabinet(name='Service Cart'))[1], '判断为落地·台面：名称、类目和零件名里没有壁挂的说法')
        self.assertEqual(mounting.reason(cabinet(), hint='wall-mounted'), ('wall', '判断为壁挂：场景说明含 wall-mounted'))
        self.assertEqual(mounting.reason(cabinet(metadata={'mounting': 'free'})), (None, '你设为落地·台面'))

    def test_the_seller_overrides_the_detection_and_a_change_makes_the_scenes_again(self):
        record = store.get('project', 'fixture'); record['name'] = 'Wall-Mounted Tool Cabinet'; store.put('project', record)
        view = listing.state('fixture')['mounting']
        self.assertEqual((view['setting'], view['detected'], view['used']), ('', 'wall', 'wall'))
        self.assertEqual(view['reason'], '判断为壁挂：名称含 Wall-Mounted')
        product, _, _ = listing.stored(store.get('project', 'fixture'))
        before = brandkit_set.scene_key(store.get('project', 'fixture'), product, product['scenes'])
        view = listing.save_mounting('fixture', 'free')['mounting']
        self.assertEqual(store.get('project', 'fixture')['metadata']['mounting'], 'free')
        self.assertEqual((view['setting'], view['detected'], view['used']), ('free', 'wall', 'free'))      # what auto would say stays shown
        self.assertNotEqual(brandkit_set.scene_key(store.get('project', 'fixture'), product, product['scenes']), before)
        listing.save_mounting('fixture', '')
        self.assertNotIn('mounting', store.get('project', 'fixture')['metadata'])
        self.assertEqual(brandkit_set.scene_key(store.get('project', 'fixture'), product, product['scenes']), before)
        with self.assertRaises(ValueError):
            listing.save_mounting('fixture', 'ceiling')

    def test_the_web_layer_saves_the_mounting(self):
        import app as application
        client = application.app.test_client()
        answer = client.put('/api/projects/fixture/listing/mounting', json={'mounting': 'wall'})
        self.assertEqual((answer.status_code, answer.get_json()['mounting']['setting']), (200, 'wall'))
        self.assertEqual(client.put('/api/projects/fixture/listing/mounting', json={'mounting': 'roof'}).status_code, 400)
        self.assertEqual(client.put('/api/projects/missing/listing/mounting', json={'mounting': ''}).status_code, 404)


class SharedBrandTests(StoreCase):
    def logo(self, colour=(200, 30, 30, 255)):
        from io import BytesIO
        data = BytesIO(); Image.new('RGBA', (60, 20), colour).save(data, 'PNG'); data.seek(0)
        return data

    def second(self, ident='second', name='Second Product'):
        record = project(name); record['id'] = ident; record['model_path'] = str(self.data / ident / 'originals' / 'x.glb')
        record.pop('listing', None); store.put('project', record)
        return ident

    def test_saving_a_brand_keeps_it_as_the_last_one_and_a_new_project_starts_with_it_and_its_own_logo_copy(self):
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'preset': 'tech', 'badges': ['PRO']})
        listing.save_logo('fixture', self.logo(), 'logo.png')
        shared = json.loads((self.data / 'brand_shared.json').read_text(encoding='utf-8'))
        self.assertEqual((shared['last']['record']['name'], shared['last']['project_id']), ('NORTHFORGE', 'fixture'))
        self.assertTrue((self.data / 'brand_logos' / shared['last']['logo_file']).is_file())
        self.assertFalse(list(self.data.glob('*.tmp')), 'written atomically')
        ident = self.second()
        self.assertTrue(listing.adopt_shared_brand(ident))
        brand = listing.state(ident)['brand']
        self.assertEqual((brand['name'], brand['preset'], brand['badges']), ('NORTHFORGE', 'tech', ['PRO']))
        self.assertTrue(brand['logo'].startswith('/files/%s/listing/brand/logo-' % ident), brand['logo'])
        self.assertIsNotNone(listing.components.path_of(ident, brand['logo']))       # served from the project's own folder
        # a project that already has a brand keeps it
        listing.save_brand(ident, {'name': 'OTHER'}, remember=False)
        listing.adopt_shared_brand(ident)
        self.assertEqual(listing.state(ident)['brand']['name'], 'OTHER')

    def test_a_project_without_a_saved_brand_cannot_become_the_default(self):
        second = self.second()
        with self.assertRaisesRegex(ValueError, '还没有保存过品牌'):
            listing.set_default_brand(second)

    def test_the_default_brand_wins_over_the_last_one_until_it_is_cleared(self):
        listing.save_brand('fixture', {'name': 'DEFAULTCO'})
        listing.set_default_brand('fixture')
        second = self.second(); listing.save_brand(second, {'name': 'LATER'})
        third = self.second('third', 'Third')
        listing.adopt_shared_brand(third)
        self.assertEqual(listing.state(third)['brand']['name'], 'DEFAULTCO')
        sources = listing.set_default_brand('fixture', clear=True)
        self.assertIsNone(sources['default']); self.assertEqual(sources['last']['name'], 'LATER')
        fourth = self.second('fourth', 'Fourth'); listing.adopt_shared_brand(fourth)
        self.assertEqual(listing.state(fourth)['brand']['name'], 'LATER')

    def test_a_brand_is_copied_from_another_project_with_its_logo_copied_in(self):
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'accent': '#0a84ff'})
        listing.save_logo('fixture', self.logo((10, 120, 200, 255)), 'logo.png')
        ident = self.second()
        sources = listing.brand_sources(ident)
        self.assertEqual([row['id'] for row in sources['projects']], ['fixture'])
        self.assertEqual((sources['projects'][0]['name'], sources['projects'][0]['logo']), ('NORTHFORGE', True))
        brand = listing.copy_brand(ident, 'fixture')['brand']
        self.assertEqual((brand['name'], brand['accent']), ('NORTHFORGE', '#0a84ff'))
        path = listing.components.path_of(ident, brand['logo'])
        self.assertIsNotNone(path); self.assertIn(str(self.data / ident), str(path))
        # removing the source's logo file does not break the copy
        listing.components.path_of('fixture', listing.state('fixture')['brand']['logo']).unlink()
        self.assertTrue(path.is_file())
        for bad in (ident, '../fixture', 'missing', None):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                listing.copy_brand(ident, bad)

    def test_the_web_layer_lists_copies_and_sets_the_default(self):
        import app as application
        mocked = patch.object(application, 'DATA', self.data); mocked.start(); self.addCleanup(mocked.stop)
        client = application.app.test_client()
        client.put('/api/projects/fixture/listing/brand', json={'name': 'NORTHFORGE'})
        ident = self.second()
        listed = client.get('/api/projects/%s/listing/brands' % ident).get_json()
        self.assertEqual((listed['projects'][0]['id'], listed['last']['name'], listed['default']), ('fixture', 'NORTHFORGE', None))
        copied = client.post('/api/projects/%s/listing/brand/copy' % ident, json={'source': 'fixture'})
        self.assertEqual((copied.status_code, copied.get_json()['brand']['name']), (200, 'NORTHFORGE'))
        self.assertEqual(client.post('/api/projects/%s/listing/brand/copy' % ident, json={'source': 'default'}).status_code, 400)
        made = client.post('/api/projects/fixture/listing/brand/default', json={}).get_json()
        self.assertEqual(made['default']['name'], 'NORTHFORGE')
        self.assertIsNone(client.post('/api/projects/fixture/listing/brand/default', json={'clear': True}).get_json()['default'])
        self.assertEqual(client.get('/api/projects/missing/listing/brands').status_code, 404)


class CostTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.log = self.data / 'usage'; self.log.mkdir()
        mocked = patch.object(ai_transport, 'USAGE_DIR', str(self.log)); mocked.start(); self.addCleanup(mocked.stop)
        listing._SPEND.clear()

    def test_the_estimate_counts_new_scene_pictures_and_reviewed_pages_at_list_price(self):
        with patch.object(brandkit_set, 'image_model_ready', return_value=True), patch.object(brandkit_review, 'available', return_value=(True, True)):
            state = listing.state('fixture')
        planned = [row for row in state['plan'] if not row.get('skipped')]
        guess = state['estimate']
        scenes = guess['new_scenes']
        self.assertEqual(guess['review_pages'], len(planned))
        low = round(scenes * 0.075 * 1.5 + len(planned) * 0.04, 2); high = round(scenes * 0.075 * 1.5 + len(planned) * 0.08, 2)
        self.assertEqual(guess['usd'], [low, high])
        if {'T12', 'T04', 'T08'} & {row['kind'] for row in planned}:
            self.assertEqual(scenes, 3)
            # scenes kept from the last build with the same settings are not paid for again
            record = store.get('project', 'fixture'); product, _, _ = listing.stored(record)
            picture = self.data / 'scene.png'; Image.new('RGB', (8, 8)).save(picture)
            with patch.object(brandkit_set, 'image_model_ready', return_value=True):
                key = brandkit_set.scene_key(record, product, product['scenes'])
            record['listing'] = {**record.get('listing', {}), 'scenes': {'key': key, 'items': [{'path': str(picture), 'check': 'passed'}] * 2}}
            store.put('project', record)
            with patch.object(brandkit_set, 'image_model_ready', return_value=True), patch.object(brandkit_review, 'available', return_value=(True, False)):
                guess = listing.state('fixture')['estimate']
            self.assertEqual((guess['new_scenes'], guess['kept_scenes'], guess['review_pages']), (1, 2, 0))
            self.assertEqual(guess['usd'], [0.11, 0.11])
        with patch.object(brandkit_set, 'image_model_ready', return_value=False), patch.object(brandkit_review, 'available', return_value=(False, False)):
            self.assertEqual(listing.state('fixture')['estimate']['usd'], [0, 0])

    def test_usage_lines_carry_the_project_of_a_build_and_only_those_are_summed(self):
        ai_client.log_usage({'model': 'claude-sonnet-4-6', 'caller': 'old', 'usage': {'input_tokens': 1000000, 'output_tokens': 0}})   # no project
        with ai_client.for_project('fixture'):
            ai_client.log_usage({'model': 'gpt-image-2', 'caller': 'scene_imagegen.edit', 'usage': {'input_tokens': 2000, 'output_tokens': 2500}})
            ai_client.log_usage({'model': 'claude-sonnet-4-6', 'caller': 'brandkit_review.review', 'cached': True, 'usage': {}})
            ai_client.log_usage({'model': 'mystery-model', 'caller': 'x', 'usage': {'input_tokens': 10}})
        ai_client.in_project('other', ai_client.log_usage, {'model': 'claude-sonnet-4-6', 'caller': 'y', 'usage': {'input_tokens': 1000, 'output_tokens': 100}})
        rows = [json.loads(line) for line in (self.log / ai_client.USAGE_LOG).read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row.get('project') for row in rows], [None, 'fixture', 'fixture', 'fixture', 'other'])
        spent = listing.spend('fixture')
        self.assertEqual((spent['calls'], spent['unpriced']), (3, 1))
        self.assertAlmostEqual(spent['usd'], (2000 * 8 + 2500 * 30) / 1e6, places=3)
        # read on from where it stopped: a new line is added to the total
        with ai_client.for_project('fixture'):
            ai_client.log_usage({'model': 'claude-sonnet-4-6', 'caller': 'z', 'usage': {'input_tokens': 1000, 'output_tokens': 1000}})
        self.assertEqual(listing.spend('fixture')['calls'], 4)
        self.assertEqual(listing.state('fixture')['spend']['calls'], 4)
        self.assertEqual(listing.spend('nobody'), {'usd': 0.0, 'calls': 0, 'unpriced': 0, 'since': None})

    def test_a_build_and_its_review_threads_tag_their_requests_with_the_project(self):
        seen = []
        with patch.object(listing, '_run', side_effect=lambda task_id: seen.append(ai_client.PROJECT.get())):
            record = store.get('project', 'fixture'); record['status'] = 'ready'; store.put('project', record)
            listing.submit('fixture')
            for _ in range(50):
                if seen: break
                time.sleep(.02)
        self.assertEqual(seen, ['fixture'])
        # scene jobs start their thread the same way (scene_stylist.submit): the target carries the project, the arguments stay
        self.assertEqual(ai_client.tagged('p2', lambda value: (value, ai_client.PROJECT.get()))('task'), ('task', 'p2'))
        self.assertEqual(ai_client.PROJECT.get(), '')


if __name__ == '__main__':
    import unittest
    unittest.main()

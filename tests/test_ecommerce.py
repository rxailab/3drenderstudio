"""E-commerce presets, checks, compositing and export.

Images here are synthetic fixtures with known geometry, so every assertion is
about measured pixels of the written result. The product matte is the only
stand-in: real mattes are rendered by Blender (see the runtime test at the end).
"""
import io
import json
import queue
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import ecommerce
import ecommerce_platforms as presets
import engine
import planner
import project_lifecycle
import studio_store as store

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = (30, 120, 130)


def fixture(size=(400, 400), box=(150, 100, 250, 320), backdrop=(255, 255, 255), shadow=True):
    """A flat-coloured product on a studio floor with a shadow running off-frame."""
    beauty = Image.new('RGB', size, backdrop)
    draw = ImageDraw.Draw(beauty)
    if shadow:
        draw.polygon([(0, box[3] - 30), (box[0], box[3] - 12), (box[0] + 20, box[3] + 14), (0, box[3] + 50)], fill=(206, 206, 206))
    draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=PRODUCT)
    matte = Image.new('L', size, 0)
    ImageDraw.Draw(matte).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
    return beauty, matte


class PlatformCatalogTests(unittest.TestCase):
    def test_every_preset_is_internally_consistent(self):
        identifiers = [item['id'] for item in presets.PLATFORMS]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn(presets.DEFAULT_PLATFORM, identifiers)
        for item in presets.PLATFORMS:
            with self.subTest(platform=item['id']):
                self.assertIn(item['aspect'], item['aspects'])
                self.assertTrue(set(item['aspects']) <= {'1:1', '3:4', '4:3'})
                self.assertLessEqual(item['min_px'], item['target_px'])
                self.assertLessEqual(item['target_px'], item['max_px'] or 10 ** 9)
                self.assertLessEqual(item['target_px'], 4096, 'the largest native render is 4096 px')
                self.assertIn(item['white'], ('required', 'recommended', 'optional'))
                self.assertTrue(.5 < item['fill_target'] < 1)
                if item.get('fill_min'):
                    self.assertGreater(item['fill_target'], item['fill_min'])
                self.assertTrue(item['notes'])
                self.assertTrue(.03 <= presets.recommended_margin(item) <= .4)
                slot = item.get('white_slot')
                if slot:
                    self.assertGreaterEqual(slot['px'], 600)
                    self.assertTrue(.5 < slot['fill_target'] < 1)

    def test_amazon_rules_match_the_published_requirements(self):
        amazon = presets.platform('amazon')
        self.assertEqual(amazon['white'], 'required')
        self.assertEqual(amazon['fill_min'], .85)
        self.assertGreaterEqual(amazon['min_px'], 1000)

    def test_unknown_platform_is_rejected_and_copies_are_isolated(self):
        with self.assertRaises(ValueError):
            presets.platform('not-a-shop')
        presets.platform('amazon')['notes'].clear()
        self.assertTrue(presets.platform('amazon')['notes'])

    def test_plan_recommendation_only_lists_real_differences(self):
        plan = planner.default_plan()
        self.assertEqual(presets.plan_recommendation(presets.platform('amazon'), plan), {'margin': .06})
        plan.update(aspect='4:3', margin=.06)
        self.assertEqual(presets.plan_recommendation(presets.platform('amazon'), plan), {'aspect': '1:1'})
        plan.update(aspect='1:1')
        self.assertEqual(presets.plan_recommendation(presets.platform('amazon'), plan), {})
        square = planner.default_plan()
        self.assertNotIn('aspect', presets.plan_recommendation(presets.platform('xiaohongshu'), square), 'an accepted ratio is kept')
        self.assertEqual(presets.plan_recommendation(presets.platform('xiaohongshu'), square, new_project=True)['aspect'], '3:4')
        planner.validate({**planner.default_plan(), **presets.plan_recommendation(presets.platform('xiaohongshu'), planner.default_plan())})


class MeasurementTests(unittest.TestCase):
    def test_border_whiteness_distinguishes_pure_near_and_shadowed(self):
        self.assertEqual(ecommerce.border_whiteness(Image.new('RGB', (200, 200), (255, 255, 255)))['level'], 'pure')
        self.assertEqual(ecommerce.border_whiteness(Image.new('RGB', (200, 200), (254, 254, 254)))['level'], 'near')
        shadowed, _ = fixture()
        measured = ecommerce.border_whiteness(shadowed)
        self.assertEqual(measured['level'], 'not_white')
        self.assertEqual(measured['darkest'], 206)

    def test_a_coloured_backdrop_is_not_white_even_when_bright(self):
        self.assertEqual(ecommerce.border_whiteness(Image.new('RGB', (100, 100), (255, 255, 180)))['level'], 'not_white')


class ComposeTests(unittest.TestCase):
    def test_white_main_image_is_pure_white_centred_and_filled_to_target(self):
        beauty, matte = fixture()
        image, info = ecommerce.compose(beauty, matte, '1:1', 2000, 'white', shadow=True, fill_target=.88)
        self.assertEqual(info['framing'], 'reframed')
        self.assertEqual(image.size[0], image.size[1])
        self.assertLessEqual(max(image.size), 250, 'a 220 px product at 88% must not be enlarged')
        self.assertAlmostEqual(info['fill'], .88, delta=.01)
        self.assertAlmostEqual(info['center'][0], .5, delta=.01)
        self.assertAlmostEqual(info['center'][1], .5, delta=.01)
        self.assertEqual(ecommerce.border_whiteness(image)['level'], 'pure')
        self.assertEqual(image.getpixel((image.width // 2, image.height // 2)), PRODUCT)

    def test_soft_shadow_stays_near_the_product_and_can_be_removed(self):
        beauty, matte = fixture()
        with_shadow, info = ecommerce.compose(beauty, matte, '1:1', 2000, 'white', shadow=True, fill_target=.8)
        self.assertTrue(info['shadow'])
        box = ecommerce.product_box(with_shadow.convert('L').point(lambda value: 255 if value < 200 else 0))
        beside = with_shadow.getpixel((box[0] - 6, box[3] - 12))
        self.assertLess(beside[0], 250, 'the grounding shadow beside the product base is kept')
        self.assertEqual(with_shadow.getpixel((2, with_shadow.height // 2)), (255, 255, 255))
        without, info = ecommerce.compose(beauty, matte, '1:1', 2000, 'white', shadow=False, fill_target=.8)
        self.assertFalse(info['shadow'])
        colours = {pixel for pixel in without.getdata()}
        self.assertEqual(colours, {(255, 255, 255), PRODUCT})

    def test_transparent_cutout_keeps_product_colour_and_clears_the_backdrop(self):
        beauty, matte = fixture()
        image, info = ecommerce.compose(beauty, matte, '1:1', 4096, 'transparent', shadow=False, fill_target=.85)
        self.assertEqual(image.mode, 'RGBA')
        self.assertEqual(image.getpixel((1, 1))[3], 0)
        self.assertEqual(image.getpixel((image.width // 2, image.height // 2)), PRODUCT + (255,))
        shadowed, _ = ecommerce.compose(beauty, matte, '1:1', 4096, 'transparent', shadow=True, fill_target=.85)
        alphas = shadowed.getchannel('A').histogram()
        self.assertTrue(sum(alphas[1:255]), 'the shadow is carried as partial transparency')
        self.assertEqual(shadowed.getpixel((1, 1))[3], 0)

    def test_coloured_studio_backdrop_leaves_no_halo_on_white(self):
        beauty, matte = fixture(backdrop=(120, 128, 140), shadow=False)
        soft = matte.resize((200, 200), Image.Resampling.BILINEAR).resize(matte.size, Image.Resampling.BILINEAR)
        blended = Image.composite(Image.new('RGB', beauty.size, PRODUCT), Image.new('RGB', beauty.size, (120, 128, 140)), soft)
        image, info = ecommerce.compose(blended, soft, '1:1', 2000, 'white', shadow=True, fill_target=.85, white_source=False)
        self.assertFalse(info['shadow'], 'a shadow cannot be separated from a non-white backdrop')
        self.assertEqual(ecommerce.border_whiteness(image)['level'], 'pure')
        for pixel in image.getdata():
            # Every pixel is a mix of product colour and white, never the grey backdrop.
            self.assertLessEqual(abs((pixel[0] - PRODUCT[0]) * (255 - PRODUCT[1]) - (pixel[1] - PRODUCT[1]) * (255 - PRODUCT[0])), 9000)

    def test_portrait_canvas_from_square_render_and_no_enlargement(self):
        beauty, matte = fixture()
        image, info = ecommerce.compose(beauty, matte, '3:4', 2000, 'white', fill_target=.8)
        self.assertEqual(info['framing'], 'reframed')
        self.assertAlmostEqual(image.width / image.height, .75, delta=.01)
        self.assertAlmostEqual(info['fill'], .8, delta=.012)
        small, info = ecommerce.compose(beauty, matte, '1:1', 120, 'white', fill_target=.8)
        self.assertEqual(small.size, (120, 120))

    def test_detail_shot_touching_the_frame_is_padded_not_reframed(self):
        beauty, matte = fixture(size=(400, 300), box=(0, 40, 400, 300), shadow=False)
        image, info = ecommerce.compose(beauty, matte, '1:1', 2000, 'white', fill_target=.85, reframe=True)
        self.assertEqual(info['framing'], 'padded')
        self.assertEqual(image.size, (400, 400))
        self.assertEqual(image.getpixel((200, 2)), (255, 255, 255))
        self.assertEqual(image.getpixel((200, 300)), PRODUCT)

    def test_original_backdrop_is_cropped_around_the_product_or_left_alone(self):
        beauty, matte = fixture(backdrop=(120, 128, 140), shadow=False)
        image, info = ecommerce.compose(beauty, matte, '3:4', 2000, 'original')
        self.assertEqual(info['framing'], 'cropped')
        self.assertEqual(image.size, (300, 400))
        self.assertEqual(image.getpixel((2, 2)), (120, 128, 140))
        wide, wide_matte = fixture(size=(400, 400), box=(20, 150, 380, 250), backdrop=(120, 128, 140), shadow=False)
        image, info = ecommerce.compose(wide, wide_matte, '3:4', 2000, 'original')
        self.assertEqual(info['framing'], 'kept')
        self.assertIsNone(info['aspect'])
        self.assertIn('保持原画幅', info['warning'])
        self.assertEqual(image.size, (400, 400))

    def test_without_a_matte_nothing_is_cut(self):
        beauty, _ = fixture()
        image, info = ecommerce.compose(beauty, None, '1:1', 2000, 'white')
        self.assertEqual(info['background'], 'original')
        self.assertEqual(list(image.getdata()), list(beauty.getdata()))


class EncodeTests(unittest.TestCase):
    def test_quality_drops_before_pixels_to_meet_the_size_limit(self):
        noise = Image.effect_noise((600, 600), 40).convert('RGB')
        free, quality, _ = ecommerce.encode_jpeg(noise, None)
        self.assertEqual(quality, 95)
        limit = int(len(free) * .6)
        limited, lower, image = ecommerce.encode_jpeg(noise, limit, min_px=600)
        self.assertLessEqual(len(limited), limit)
        self.assertLess(lower, 95)
        self.assertEqual(image.size, (600, 600))
        with Image.open(io.BytesIO(limited)) as opened:
            self.assertEqual((opened.format, opened.size), ('JPEG', (600, 600)))

    def test_pixels_shrink_only_when_no_quality_fits(self):
        noise = Image.effect_noise((600, 600), 120).convert('RGB')
        data, quality, image = ecommerce.encode_jpeg(noise, 20 * 1024, min_px=100)
        self.assertLessEqual(len(data), 20 * 1024)
        self.assertLess(image.width, 600)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for module in (store, ecommerce, engine):
            context = patch.object(module, 'DATA', self.root)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(ecommerce, 'QUEUE', queue.Queue())
        context.start()
        self.addCleanup(context.stop)
        ecommerce._MEASUREMENTS.clear()
        with store.connection() as database:
            database.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.mattes = {}
        context = patch.object(ecommerce, 'product_matte', side_effect=lambda task, project, output, cancelled=None: self.mattes[output['png_url']].copy())
        self.matte_calls = context.start()
        self.addCleanup(context.stop)
        context = patch.object(ecommerce, 'matte_available', return_value=True)
        self.available = context.start()
        self.addCleanup(context.stop)
        self.plan = planner.default_plan()
        store.put('project', {'id': 'p', 'name': '测试瓶', 'slug': '测试瓶', 'version': 3, 'plan': self.plan, 'outputs': [], 'history': []})

    def add_output(self, shot_id='hero', size=(512, 512), version=3, quality='preview', style='white', bounds=(.375, .2, .625, .75)):
        box = (round(bounds[0] * size[0]), round((1 - bounds[3]) * size[1]), round(bounds[2] * size[0]), round((1 - bounds[1]) * size[1]))
        beauty, matte = fixture(size, box, (255, 255, 255) if style == 'white' else (120, 128, 140), shadow=style == 'white')
        folder = self.root / 'p' / 'runs' / ('%s-%s-%s' % (shot_id, version, quality))
        folder.mkdir(parents=True)
        beauty.save(folder / 'image.png')
        (folder / 'scene.blend').write_bytes(b'BLENDER fixture: mattes are stubbed in this test')
        url = '/files/p/runs/%s/image.png' % folder.name
        self.mattes[url] = matte
        shot = next(item for item in self.plan['shots'] if item['id'] == shot_id)
        effective = {**self.plan, 'shots': [dict(item, **({'style': style} if item['id'] == shot_id else {})) for item in self.plan['shots']]}
        output = {'shot_id': shot_id, 'shot_label': shot['label'], 'quality': quality, 'version': version, 'job_id': folder.name,
                  'png_url': url, 'blend_url': '/files/p/runs/%s/scene.blend' % folder.name, 'width': size[0], 'height': size[1],
                  'created_at': len(self.mattes), 'effective_plan': effective,
                  'checks': {'method': 'projected product vertices in the rendered camera', 'projected_bounds': list(bounds)}}
        project = store.get('project', 'p')
        project['outputs'].append(output)
        store.put('project', project)
        return output

    def run_export(self, **body):
        task = ecommerce.submit('p', body)
        ecommerce.execute(task['id'])
        return ecommerce.public(store.get(ecommerce.KIND, task['id']))


class OverviewTests(StoreCase):
    def items(self, view, shot_id='hero'):
        shot = next(item for item in view['shots'] if item['shot_id'] == shot_id)
        return {item['id']: item for item in shot['items']}, shot

    def test_preview_only_main_image_needs_a_4k_render(self):
        self.add_output()
        view = ecommerce.overview(store.get('project', 'p'), 'amazon')
        items, shot = self.items(view)
        self.assertEqual(view['step'], 'render')
        self.assertEqual((items['resolution']['status'], items['resolution']['action']), ('action', 'render'))
        self.assertEqual(items['background']['status'], 'auto')
        self.assertEqual(items['fill']['status'], 'auto')
        self.assertIn('55%', items['fill']['detail'])
        self.assertEqual(shot['status'], 'action')
        self.assertEqual(view['counts'], {'shots': 6, 'rendered': 1, 'exportable': 0})
        self.assertEqual(self.items(view, 'side')[1]['status'], 'missing')

    def test_4k_render_is_ready_and_reports_the_deliverable_size(self):
        self.add_output(size=(4096, 4096), quality='standard', bounds=(.3, .1, .7, .9))
        view = ecommerce.overview(store.get('project', 'p'), 'amazon')
        items, shot = self.items(view)
        self.assertEqual(view['step'], 'export')
        self.assertEqual(items['resolution']['status'], 'pass')
        self.assertEqual(shot['export']['size'], [2000, 2000])
        self.assertEqual(shot['status'], 'ready')
        self.assertEqual(view['counts']['exportable'], 1)

    def test_newest_plan_version_wins_over_an_older_larger_render(self):
        self.add_output(size=(4096, 4096), quality='standard', version=2)
        self.add_output(size=(512, 512), quality='preview', version=3)
        project = store.get('project', 'p')
        self.assertEqual(ecommerce.best_output(project, 'hero')['quality'], 'preview')
        project['version'] = 4
        items, _ = self.items(ecommerce.overview(project, 'general'))
        self.assertEqual(items['version']['status'], 'warn')

    def test_thumbnail_never_replaces_real_render_for_export(self):
        self.add_output(size=(4096,4096),quality='standard',version=2)
        self.add_output(size=(256,256),quality='thumbnail',version=3)
        self.assertEqual(ecommerce.best_output(store.get('project','p'),'hero')['quality'],'standard')

    def test_keeping_the_render_as_is_is_flagged_where_white_is_mandatory(self):
        self.add_output(size=(4096, 4096), quality='standard')
        project = store.get('project', 'p')
        items, shot = self.items(ecommerce.overview(project, 'amazon', {'background': 'original'}))
        self.assertEqual(items['background']['status'], 'action')
        self.assertEqual(items['fill']['status'], 'action')
        self.assertFalse(shot['export']['cut'])
        items, _ = self.items(ecommerce.overview(project, 'ebay', {'background': 'original'}))
        self.assertNotIn('background', items)

    def test_other_ratio_is_handled_by_the_export(self):
        self.add_output(size=(3072, 4096), quality='standard')
        items, shot = self.items(ecommerce.overview(store.get('project', 'p'), 'amazon'))
        self.assertEqual(items['aspect']['status'], 'auto')
        self.assertEqual(shot['export']['aspect'], '1:1')
        items, shot = self.items(ecommerce.overview(store.get('project', 'p'), 'xiaohongshu'))
        self.assertEqual(items['aspect']['status'], 'pass')
        self.assertEqual(shot['export']['aspect'], '3:4')

    def test_settings_are_validated_and_remembered(self):
        with self.assertRaises(ValueError):
            ecommerce.save_settings('p', {'platform': 'nope'})
        with self.assertRaises(ValueError):
            ecommerce.save_settings('p', {'background': 'blue'})
        with self.assertRaises(ValueError):
            ecommerce.save_settings('p', {'shadow': 'yes'})
        saved = ecommerce.save_settings('p', {'platform': 'jd', 'background': 'all_white', 'shadow': False, 'transparent': True})
        self.assertEqual(ecommerce.settings(store.get('project', 'p')), saved)
        self.assertEqual(ecommerce.settings({'ecommerce': {'platform': 'removed-shop'}})['platform'], presets.DEFAULT_PLATFORM)


class ExportTests(StoreCase):
    def test_amazon_package_is_verified_on_the_written_files(self):
        self.add_output(size=(2400, 2400), quality='standard', bounds=(.3, .1, .7, .9))
        self.add_output('campaign', size=(2400, 2400), quality='standard', style='soft')
        task = self.run_export(platform='amazon', transparent=True)
        self.assertEqual(task['status'], 'completed', task['error'])
        names = [item['name'] for item in task['files']]
        self.assertEqual(names[0], '01_hero_main_2000x2000.jpg')
        main = task['files'][0]
        self.assertTrue(main['pure_white'])
        self.assertAlmostEqual(main['fill'], .88, delta=.01)
        self.assertEqual(main['issues'], [])
        self.assertLessEqual(main['bytes'], presets.platform('amazon')['max_bytes'])
        angle = next(item for item in task['files'] if item['shot_id'] == 'campaign' and item['role'] == 'angle')
        self.assertEqual(angle['background'], 'original')
        self.assertEqual(sum(item['role'] == 'transparent' for item in task['files']), 2)
        archive = ecommerce._project_file('p', task['download_url'], ('.zip',))
        with zipfile.ZipFile(archive) as bundle:
            listed = set(bundle.namelist())
            self.assertTrue({'manifest.json', 'README.txt', *names} <= listed)
            manifest = json.loads(bundle.read('manifest.json'))
            self.assertEqual(manifest['platform']['id'], 'amazon')
            self.assertEqual([item['name'] for item in manifest['files']], names)
            with Image.open(io.BytesIO(bundle.read(names[0]))) as written:
                self.assertEqual((written.format, written.size), ('JPEG', (2000, 2000)))
                self.assertEqual(ecommerce.border_whiteness(written)['level'], 'pure')
            self.assertIn('纯白底已校验', bundle.read('README.txt').decode('utf-8'))

    def test_preview_sized_export_is_delivered_but_flagged(self):
        self.add_output()
        task = self.run_export(platform='amazon')
        self.assertEqual(task['status'], 'completed')
        self.assertIn('低于平台最低 1000 px', task['files'][0]['issues'][0])
        self.assertIn('需要留意', task['stage'])

    def test_taobao_adds_the_dedicated_white_background_image(self):
        self.add_output(size=(2400, 2400), quality='standard', bounds=(.3, .1, .7, .9))
        task = self.run_export(platform='taobao')
        slot = next(item for item in task['files'] if item['role'] == 'white_slot')
        self.assertEqual((slot['width'], slot['height']), (800, 800))
        self.assertLessEqual(slot['bytes'], 300 * 1024)
        self.assertTrue(slot['pure_white'])
        self.assertFalse(slot['shadow'], 'the marketplace white-background slot forbids shadows')
        self.assertAlmostEqual(slot['fill'], .9, delta=.01)
        self.assertEqual((task['files'][0]['width'], task['files'][0]['height']), (1440, 1440))

    def test_all_white_cuts_studio_shots_and_original_never_calls_the_matte(self):
        self.add_output(size=(1600, 1600), quality='standard')
        self.add_output('campaign', size=(1600, 1600), quality='standard', style='soft')
        task = self.run_export(platform='general', background='all_white')
        self.assertTrue(all(item['pure_white'] for item in task['files']))
        self.assertFalse(next(item for item in task['files'] if item['shot_id'] == 'campaign')['shadow'])
        self.matte_calls.reset_mock()
        task = self.run_export(platform='ebay', background='original')
        self.assertEqual(task['status'], 'completed')
        self.matte_calls.assert_not_called()
        self.assertEqual({item['background'] for item in task['files']}, {'original'})

    def test_identical_request_reuses_the_finished_package(self):
        self.add_output(size=(1600, 1600), quality='standard')
        first = self.run_export(platform='jd')
        again = ecommerce.submit('p', {'platform': 'jd'})
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(again['status'], 'completed')
        other = ecommerce.submit('p', {'platform': 'jd', 'shadow': False})
        self.assertNotEqual(other['id'], first['id'])

    def test_requests_without_images_or_with_bad_shots_are_refused(self):
        with self.assertRaises(ValueError):
            ecommerce.submit('p', {'platform': 'amazon'})
        self.add_output()
        with self.assertRaises(ValueError):
            ecommerce.submit('p', {'platform': 'amazon', 'shot_ids': ['nope']})
        with self.assertRaises(ValueError):
            ecommerce.submit('p', {'platform': 'nope'})
        self.available.return_value = False
        with self.assertRaises(ValueError):
            ecommerce.submit('p', {'platform': 'amazon'})
        self.assertEqual(ecommerce.submit('p', {'platform': 'amazon', 'background': 'original'})['status'], 'queued')

    def test_matte_failure_fails_the_task_with_the_reason(self):
        self.add_output(size=(1600, 1600), quality='standard')
        self.matte_calls.side_effect = RuntimeError('未能生成产品蒙版：fixture')
        task = self.run_export(platform='amazon')
        self.assertEqual(task['status'], 'failed')
        self.assertIn('产品蒙版', task['error'])
        self.assertIsNone(task['download_url'])

    def test_cancel_and_restart_never_leave_a_running_task(self):
        self.add_output()
        task = ecommerce.submit('p', {'platform': 'general'})
        self.assertEqual(ecommerce.cancel(task['id'])['status'], 'cancelled')
        ecommerce.execute(task['id'])
        self.assertEqual(store.get(ecommerce.KIND, task['id'])['status'], 'cancelled')
        queued = ecommerce.submit('p', {'platform': 'general', 'shadow': False})
        with patch('ecommerce.threading.Thread'):
            ecommerce.start()
        self.assertEqual(store.get(ecommerce.KIND, queued['id'])['status'], 'interrupted')

    def test_project_view_lists_exports_and_deletion_waits_for_them(self):
        self.add_output()
        task = ecommerce.submit('p', {'platform': 'general'})
        view = engine.project_view('p')
        self.assertEqual(view['ecommerce_exports'][0]['id'], task['id'])
        self.assertEqual(view['ecommerce']['platform'], 'general')
        self.assertNotIn('sources', view['ecommerce_exports'][0])
        with self.assertRaises(project_lifecycle.ProjectBusyError):
            project_lifecycle._ensure_idle('p')
        ecommerce.cancel(task['id'])
        project_lifecycle._ensure_idle('p')

    def test_old_packages_are_pruned(self):
        self.add_output()
        identifiers = []
        for index in range(ecommerce.KEEP_EXPORTS + 3):
            task = ecommerce.submit('p', {'platform': 'general', 'shot_ids': ['hero'], 'shadow': index % 2 == 0,
                                          'transparent': index % 3 == 0, 'background': ecommerce.BACKGROUNDS[index % 3]})
            store.put(ecommerce.KIND, {**store.get(ecommerce.KIND, task['id']), 'status': 'failed', 'created_at': index})
            identifiers.append(task['id'])
        remaining = {task['id'] for task in store.all_records(ecommerce.KIND)}
        self.assertLessEqual(len(remaining), ecommerce.KEEP_EXPORTS + 1)
        self.assertIn(identifiers[-1], remaining)


class RouteTests(StoreCase):
    def setUp(self):
        super().setUp()
        import app as application
        self.application = application
        self.client = application.app.test_client()

    def test_catalog_and_overview_routes(self):
        catalog = self.client.get('/api/ecommerce/platforms').get_json()
        self.assertEqual(catalog['default'], 'general')
        self.assertIn('amazon', [item['id'] for item in catalog['platforms']])
        self.add_output()
        view = self.client.get('/api/projects/p/ecommerce?platform=amazon').get_json()
        self.assertEqual(view['platform']['id'], 'amazon')
        self.assertEqual(self.client.get('/api/projects/p/ecommerce?platform=zzz').status_code, 400)
        saved = self.client.put('/api/projects/p/ecommerce', json={'platform': 'jd', 'transparent': True}).get_json()
        self.assertEqual((saved['settings']['platform'], saved['settings']['transparent']), ('jd', True))
        self.assertEqual(self.client.get('/api/projects/p/ecommerce').get_json()['platform']['id'], 'jd')

    def test_adapt_changes_ratio_and_margin_as_one_undoable_version(self):
        project = store.get('project', 'p')
        project['plan']['aspect'] = '4:3'
        store.put('project', project)
        response = self.client.post('/api/projects/p/ecommerce/adapt', json={'platform': 'amazon', 'expected_version': 3})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body['plan']['aspect'], body['plan']['margin'], body['version']), ('1:1', .06, 4))
        self.assertIn('Amazon', body['history'][-1]['reason'])
        self.assertEqual(body['ecommerce']['platform'], 'amazon')
        again = self.client.post('/api/projects/p/ecommerce/adapt', json={'platform': 'amazon'}).get_json()
        self.assertEqual(again['version'], 4, 'nothing left to change, no new version')
        self.assertEqual(self.client.post('/api/projects/p/ecommerce/adapt', json={'platform': 'jd', 'expected_version': 1}).status_code, 409)

    def test_adapt_waits_for_running_renders(self):
        store.put('job', {'id': 'j', 'project_id': 'p', 'status': 'running'})
        self.assertEqual(self.client.post('/api/projects/p/ecommerce/adapt', json={'platform': 'amazon'}).status_code, 409)

    def test_export_routes_create_read_and_cancel(self):
        self.add_output()
        created = self.client.post('/api/projects/p/ecommerce/exports', json={'platform': 'general'})
        self.assertEqual(created.status_code, 202)
        ident = created.get_json()['id']
        self.assertEqual(self.client.get('/api/ecommerce/exports/' + ident).get_json()['status'], 'queued')
        self.assertEqual(self.client.post('/api/ecommerce/exports/%s/cancel' % ident).get_json()['status'], 'cancelled')
        self.assertEqual(self.client.get('/api/ecommerce/exports/missing').status_code, 404)

    def test_upload_with_a_platform_frames_the_first_preview_for_it(self):
        base = self.root / 'new' / 'originals'
        base.mkdir(parents=True)
        shutil.copy2(ROOT / 'samples' / 'matte-pump-bottle.glb', base / 'bottle.glb')
        with patch.object(engine, 'submit'), patch.object(engine, 'project_view', side_effect=lambda ident: store.get('project', ident)):
            created = self.application.create_project(base, '瓶', {}, 'xiaohongshu')
        self.assertEqual((created['plan']['aspect'], created['plan']['margin']), ('3:4', .1))
        self.assertEqual(created['ecommerce'], {'platform': 'xiaohongshu'})
        with patch.object(engine, 'require_runtime'):
            refused = self.client.post('/api/projects', data={'platform': 'nope', 'files': (io.BytesIO(b'x'), 'a.glb')})
        self.assertEqual(refused.status_code, 400)
        self.assertFalse([path for path in self.root.iterdir() if path.name not in ('p', 'new', 'studio.sqlite', 'studio.sqlite-wal', 'studio.sqlite-shm')])


class SmartFramingTests(unittest.TestCase):
    def enrich(self, dimensions, plan=None):
        return planner.enrich(plan or planner.default_plan(), {'features': {'dimensions': dimensions}, 'objects': [{'name': 'Body'}],
                                                                'materials': [], 'warnings': []})

    def test_flat_and_tall_products_get_their_own_first_angles(self):
        flat = self.enrich([.4, .3, .05])
        self.assertEqual(flat['auto_framing']['shape'], 'flat')
        self.assertEqual({shot['id']: shot['elevation'] for shot in flat['shots'] if shot['kind'] == 'full'},
                         {'hero': 40, 'campaign': 36, 'side': 20, 'back': 34})
        tall = self.enrich([.195, .196, .67])
        self.assertEqual(tall['shots'][0]['elevation'], 12)
        self.assertEqual([shot['elevation'] for shot in tall['shots'] if shot['kind'] == 'detail'], [25, 35])

    def test_ordinary_or_unmeasured_shapes_keep_the_default_plan(self):
        for dimensions in ([.3, .3, .3], [0, 1, 1], ['a', 1, 1], None):
            with self.subTest(dimensions=dimensions):
                plan = self.enrich(dimensions)
                self.assertNotIn('auto_framing', plan)
                self.assertEqual([shot['elevation'] for shot in plan['shots']], [shot['elevation'] for shot in planner.default_plan()['shots']])

    def test_a_plan_the_user_already_changed_is_left_alone(self):
        plan = planner.default_plan()
        plan['shots'][0]['elevation'] = 33
        self.assertEqual(self.enrich([.4, .3, .05], plan)['shots'][0]['elevation'], 33)


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: real render, framing check and product matte not run')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.data = Path(cls.temp.name)
        for module in (store, ecommerce, engine):
            context = patch.object(module, 'DATA', cls.data)
            context.start()
            cls.addClassCleanup(context.stop)
        with store.connection() as database:
            database.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        model = cls.data / 'p' / 'originals' / 'bottle.glb'
        model.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'samples' / 'matte-pump-bottle.glb', model)
        cls.plan = planner.default_plan()
        cls.plan.update(visual_review=False, margin=.1)
        job = {'id': 'job', 'project_id': 'p', 'status': 'running', 'plan': cls.plan, 'version': 1, 'quality': 'preview'}
        store.put('job', job)
        store.put('project', {'id': 'p', 'version': 1, 'plan': cls.plan, 'outputs': [], 'analysis': None})
        analysis = engine.execute(job, {'action': 'analyze', 'model_path': str(model), 'plan': cls.plan}, cls.data / 'p' / 'analysis')
        project = store.get('project', 'p')
        project['analysis'] = analysis
        store.put('project', project)
        cls.out = cls.data / 'p' / 'runs' / 'job' / 'hero'
        cls.result = engine.execute(job, {'action': 'render', 'model_path': str(model), 'plan': cls.plan, 'shot': cls.plan['shots'][0],
                                          'quality': 'preview'}, cls.out)
        cls.output = engine.output_record(job, cls.plan['shots'][0], cls.out, cls.result)
        project['outputs'] = [cls.output]
        store.put('project', project)

    def test_orthographic_shot_is_rendered_once_and_measured_on_real_vertices(self):
        checks = self.result['checks']
        self.assertEqual(len(checks['attempts']), 1)
        self.assertEqual(checks['corrections'], 0)
        self.assertEqual(checks['automatic_check_status'], 'passed')
        self.assertIn('vertices', checks['method'])
        self.assertAlmostEqual(checks['occupancy'], .8, delta=.005)
        self.assertFalse(any('自动取景' in note for note in checks['manual_review']))
        renders = [line for line in (self.out / 'blender.log').read_text(encoding='utf-8').splitlines() if '镜头已就绪，正在渲染' in line]
        self.assertEqual(len(renders), 1, 'the same picture must not be rendered again')

    def test_matte_matches_the_render_and_yields_a_compliant_main_image(self):
        matte = ecommerce.product_matte('task', 'p', self.output)
        self.assertEqual(matte.size, (self.output['width'], self.output['height']))
        box = ecommerce.product_box(matte)
        fill = max((box[2] - box[0]) / matte.width, (box[3] - box[1]) / matte.height)
        self.assertAlmostEqual(fill, self.result['checks']['occupancy'], delta=.012)
        cached = list((self.data / 'p' / 'ecommerce' / 'mattes').glob('*.png'))
        self.assertEqual(len(cached), 1)
        with patch.object(ecommerce.subprocess, 'Popen', side_effect=AssertionError('the cached matte must be reused')):
            self.assertEqual(ecommerce.product_matte('task', 'p', self.output).size, matte.size)
        with Image.open(self.out / next(self.out.glob('*.png')).name) as opened:
            beauty = opened.convert('RGB')
        self.assertNotEqual(ecommerce.border_whiteness(beauty)['level'], 'pure', 'the raw render carries its shadow to the frame edge')
        image, info = ecommerce.compose(beauty, matte, '1:1', 2000, 'white', shadow=True, fill_target=.88)
        self.assertEqual(ecommerce.border_whiteness(image)['level'], 'pure')
        self.assertAlmostEqual(info['fill'], .88, delta=.012)
        view = ecommerce.overview(store.get('project', 'p'), 'amazon')
        fill_item = next(item for item in view['shots'][0]['items'] if item['id'] == 'fill')
        self.assertIn('80%', fill_item['detail'])


if __name__ == '__main__':
    unittest.main()

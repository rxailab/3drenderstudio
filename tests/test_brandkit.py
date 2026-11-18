"""The brand-kit templates: the geometry and words handed to them, and real layouts in a headless browser.

The browser tests run when Chrome, Chromium or Edge is installed (``BRANDKIT_CHROME`` points at one
elsewhere); the rest needs nothing but Pillow.
"""
import shutil
import sys
import tempfile
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
from test_listing import PARTS, StoreCase


def cut_out(path, size=(600, 400), box=(150, 120, 450, 280), angle=0):
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(40, 60, 200, 255))
    if angle:
        image = image.rotate(angle, expand=False)
    image.save(path)
    return path


class GeometryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_a_render_is_trimmed_to_the_product_and_laid_on_white_with_its_shape_measured(self):
        source = cut_out(self.folder / 'render.png')
        fields, crop = brandkit_set.flatten(source, self.folder / 'hero.png', pad=0)
        self.assertEqual((fields['width'], fields['height']), (301, 161))
        self.assertEqual([round(value, 2) for value in crop], [.25, .3, .75, .7])
        with Image.open(fields['path']) as picture:
            self.assertEqual((picture.mode, picture.getpixel((5, 5))), ('RGB', (40, 60, 200)))
        self.assertEqual(fields['content_bbox'], [0.0, 0.0, 1.0, 1.0])
        self.assertGreaterEqual(len(fields['hull']), 4)
        (x0, y0), (x1, y1) = fields['axis']
        self.assertAlmostEqual(y0, y1, delta=.02); self.assertLess(x0, .1); self.assertGreater(x1, .9)        # the long side runs across
        self.assertTrue(.2 < fields['half_width'] < .3, fields['half_width'])               # half of 161 px across 301 px
        for got in brandkit_set.remap([.5, .5], crop):
            self.assertAlmostEqual(got, .5, delta=.005)
        self.assertEqual(brandkit_set.remap([.25, .3], crop), [0.0, 0.0])

    def test_a_slanted_product_has_a_slanted_axis_and_a_dark_close_up_has_no_white(self):
        source = cut_out(self.folder / 'slant.png', size=(600, 600), box=(100, 270, 500, 330), angle=35)
        fields, _ = brandkit_set.flatten(source, self.folder / 'slant-flat.png')
        self.assertAlmostEqual(abs(fields['axis_deg']), 35, delta=3)
        dark, _ = brandkit_set.flatten(source, self.folder / 'dark.png', ground=(22, 24, 27), dark=True)
        with Image.open(dark['path']) as picture:
            self.assertLess(max(picture.getpixel((0, picture.height - 1))), 60)
        self.assertNotIn('hull', dark)

    def test_words_are_cut_whole_and_chinese_headlines_break_at_a_pause(self):
        self.assertEqual(brandkit_set._clip('Rolling Stack Toolbox', 18), 'Rolling Stack…')
        self.assertEqual(brandkit_set._clip('Stack it,\nroll it away', 40), 'Stack it,\nroll it away')
        self.assertEqual(brandkit_set._clip('叠放式带轮工具箱组合套装', 8), '叠放式带轮工具…')
        self.assertEqual(brandkit_set._headline('三层叠放，一次拉走', 'zh', 40), '三层叠放，\n一次拉走')
        self.assertEqual(brandkit_set._headline('单手切管', 'zh', 40), '单手切管')
        self.assertEqual(brandkit_set._headline('Stack it, roll it away', 'en', 40), 'Stack it, roll it away')

    def test_scene_settings_are_checked(self):
        self.assertEqual(brandkit_set.validate_scenes(None), brandkit_set.default_scenes())
        kept = brandkit_set.validate_scenes({'count': 2}, {'enabled': False, 'hint': ' a  bench '})
        self.assertEqual((kept['count'], kept['enabled'], kept['hint']), (2, False, 'a  bench'))
        for bad in ({'count': 0}, {'count': 2.5}, {'count': True}, {'enabled': 1}, {'people': 'crowd'}, {'audience': 'nobody'}, {'hint': 3}, 'x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                brandkit_set.validate_scenes(bad)

    def test_every_template_has_its_source_and_its_record(self):
        for kind in brandkit_set.KINDS:
            with self.subTest(kind=kind):
                self.assertTrue((brandkit_render.TEMPLATES / (kind + '.html')).is_file())
                info = brandkit_render.template_info(kind)
                self.assertEqual(info['template_id'], kind); self.assertIn('bg', info['tokens']['palette'])
        self.assertTrue(set(brandkit_set.AUTO) <= set(brandkit_set.KINDS))
        self.assertEqual(set(brandkit_set.KINDS) - set(brandkit_set.AUTO), set(brandkit_set.BLOCKED))


class ReviseTests(unittest.TestCase):
    """The art director's fixes, applied only where they are allowed and keep the facts."""
    FACTS = {'title': 'Pipe Cutter', 'specs': [{'label': 'Capacity', 'value': '6 - 22 mm'}]}

    def verdict(self, *actions):
        return {'issues': [{'region': 'r', 'evidence': 'e', 'action': action} for action in actions], 'vetoes': [], 'score': 60}

    def test_text_is_only_ever_shortened_and_never_gains_a_number(self):
        import brandkit_review as review
        job = {'headline': 'Cuts pipe from 6 to 22 mm cleanly', 'support': 'A line', 'features': [{'title': 'Hardened wheel', 'body': 'Replaceable'}, {'title': 'Rollers'}]}
        new, applied = review.revise('T01', job, self.verdict({'type': 'shorten_text', 'target': 'headline', 'text': 'Cuts 6–22 mm pipe'}), self.FACTS)
        self.assertEqual(new['headline'], 'Cuts 6–22 mm pipe'); self.assertEqual(job['headline'], 'Cuts pipe from 6 to 22 mm cleanly')
        for action in ({'type': 'shorten_text', 'target': 'headline', 'text': 'Cuts 50 mm pipe'},          # a new number
                       {'type': 'shorten_text', 'target': 'headline', 'text': 'Cuts pipe from 6 to 22 mm cleanly, every time'},
                       {'type': 'remove_text', 'target': 'headline'}, {'type': 'shorten_text', 'target': 'price', 'text': 'x'},
                       {'type': 'drop_item', 'target': 'features', 'index': 0}):                            # T01 keeps two callouts
            with self.subTest(action=action):
                self.assertEqual(review.revise('T01', job, self.verdict(action), self.FACTS), (None, []))
        new, applied = review.revise('T01', job, self.verdict({'type': 'remove_text', 'target': 'features.0.body'},
                                                              {'type': 'remove_text', 'target': 'support'}), self.FACTS)
        self.assertIsNone(new['features'][0]['body']); self.assertIsNone(new['support']); self.assertEqual(len(applied), 2)

    def test_items_scenes_and_layouts(self):
        import brandkit_review as review
        job = {'items': [{'asset': 'scene1', 'focus': [.5, .5]}, {'asset': 'scene2', 'focus': [.5, .5]}, {'asset': 'scene3', 'focus': [.5, .5]}]}
        new, _ = review.revise('T08', job, self.verdict({'type': 'drop_item', 'target': 'items', 'index': 2},
                                                        {'type': 'refocus_scene', 'target': 'scene1', 'focus': [.3, .7]}), self.FACTS)
        self.assertEqual([item['asset'] for item in new['items']], ['scene1', 'scene2']); self.assertEqual(new['items'][0]['focus'], [.3, .7])
        self.assertEqual(review.revise('T08', job, self.verdict({'type': 'refocus_scene', 'target': 'scene1', 'focus': [2, .7]}), self.FACTS), (None, []))
        poster = {'scene': 'scene1', 'focus': [.5, .5], 'assets': {'scene1': {'file': 'a'}}}
        new, _ = review.revise('T12', poster, self.verdict({'type': 'swap_scene'}), self.FACTS, [('scene1', {'file': 'a'}, [.5, .5]), ('scene2', {'file': 'b'}, [.4, .6])])
        self.assertEqual((new['scene'], new['focus'], list(new['assets'])), ('scene2', [.4, .6], ['scene2']))
        self.assertEqual(review.revise('T12', poster, self.verdict({'type': 'next_layout'}), self.FACTS), ('next_layout', ['换一种排法']))

    def test_the_copywriters_lines_are_checked_before_use(self):
        import brandkit_review as review
        product = {'language': 'zh', 'title': '托特包', 'model': '', 'headline': '', 'subline': '', 'size_mm': 476,
                   'features': [{'title': '皮革提手', 'detail': ''}], 'specs': [], 'figures': [{'value': '476', 'unit': 'mm', 'label': '高度'}]}
        answer = {'pages': [{'template': 'T01', 'eyebrow': '部位导览', 'headline': '卷边提手\n铆钉加固'},
                            {'template': 'T04', 'eyebrow': '参数', 'headline': '高度476mm\n容量顺手'},          # a number, and two lines on a one-line page
                            {'template': 'T02', 'eyebrow': '细节', 'headline': '细节看得见'},                  # the eyebrow repeated in the headline
                            {'template': 'T08', 'eyebrow': '场景', 'headline': '一\n二\n三'}]}                # three lines
        with patch.object(review, '_configs', return_value=({'model': 'text'}, None)), patch.object(review, '_ask', return_value=answer):
            written, note = review.write_copy(product, ['T01', 'T02', 'T04', 'T08'])
        self.assertEqual(written, {'T01': {'eyebrow': '部位导览', 'headline': '卷边提手\n铆钉加固'}})
        self.assertIn('3 条', note)

    def test_a_wrong_written_line_goes_back_to_the_default_and_unknown_fixes_are_read_as_none(self):
        import brandkit_review as review
        job = {'headline': '卷边皮革握手', 'lang': 'zh', '_lines': 'model'}
        typo = {'issues': [], 'vetoes': [{'kind': 'text_broken', 'evidence': '「握手」应为「提手」'}], 'score': 77}
        self.assertEqual(review.revise('T02', job, typo, self.FACTS), ('default_text', ['改用模板默认标题']))
        self.assertEqual(review.revise('T02', {**job, '_lines': 'stock'}, typo, self.FACTS), (None, []))          # nothing to go back to
        asked = self.verdict({'type': 'default_text'})
        self.assertEqual(review.revise('T02', job, asked, self.FACTS)[0], 'default_text')
        answer = review._cleaned({'issues': [{'region': 'r', 'evidence': 'e', 'action': {'type': 'move_callout'}}],
                                  'vetoes': [{'kind': 'ugly', 'evidence': 'x'}, {'kind': 'text_broken', 'evidence': 'y'}]})
        self.assertEqual((answer['issues'][0]['action'], [row['kind'] for row in answer['vetoes']]), ({'type': 'none'}, ['text_broken']))
        new, _ = review.revise('T12', {'headline': '帆布托特，皮革收边', 'lang': 'zh'},
                               self.verdict({'type': 'shorten_text', 'target': 'headline', 'text': '帆布托特\n，皮革边'}), self.FACTS)
        self.assertEqual(new['headline'], '帆布托特，\n皮革边')                                                # re-broken at the pause

    def test_the_reviewer_is_told_frames_not_the_pictures_behind_them(self):
        qa = {'canvas': [1500, 1500], 'elements': [
            {'id': 'detail2_frame', 'kind': 'shape', 'x': 84, 'y': 868, 'w': 600, 'h': 548},
            {'id': 'detail2', 'kind': 'detail_image', 'x': 84, 'y': 868, 'w': 660, 'h': 660, 'inframe': True, 'crop': 'explicit_detail_crop'},
            {'id': 'hero', 'kind': 'product_image', 'x': 700, 'y': 100, 'w': 700, 'h': 700, 'crop': 'contain'}], 'issues': []}
        pictures = {row['id']: row for row in brandkit_set._measured(qa)['pictures']}
        self.assertEqual((pictures['detail2']['box_px'], pictures['detail2']['fully_inside_page']), ([84, 868, 600, 548], True))
        self.assertEqual(pictures['hero']['shows'], 'the whole product, uncropped')
        self.assertEqual(brandkit_set._fit_line('格纹包身斜角朝前，皮革边条与铜铆钉', 15), '格纹包身斜角朝前。')
        self.assertEqual(brandkit_set._fit_line('一句没有标点的很长很长的说明文字', 8), '')

    def test_a_headline_that_repeats_a_selling_point_is_refused_and_only_failing_pages_are_checked_again(self):
        import brandkit_review as review
        product = {'language': 'zh', 'title': '托特包', 'model': '', 'headline': '', 'subline': '', 'size_mm': 476,
                   'features': [{'title': '卷边皮革提手', 'detail': ''}], 'specs': [], 'figures': []}
        answer = {'pages': [{'template': 'T02', 'eyebrow': '细节', 'headline': '卷边皮革提手\n铆钉加固根部'},
                            {'template': 'T04', 'eyebrow': '参数', 'headline': '卷边皮革提手'}]}          # only the callout pages print the titles
        with patch.object(review, '_configs', return_value=({'model': 'text'}, None)), patch.object(review, '_ask', return_value=answer):
            written, note = review.write_copy(product, ['T02', 'T04'])
        self.assertEqual(list(written), ['T04']); self.assertIn('照抄卖点标题', note)
        good = {'scores': {'product': 22, 'hierarchy': 18, 'composition': 17, 'task': 13, 'color': 9, 'independence': 8},
                'vetoes': [], 'issues': [{'region': 'a', 'evidence': 'cropped'}, {'region': 'b', 'evidence': 'small'}], 'summary': 'ok', 'score': 87}
        with patch.object(review, 'review', return_value=good), patch.object(review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(review, '_ask', return_value={'claims': [{'number': 1, 'real': False, 'why': ''}, {'number': 2, 'real': True, 'why': ''}]}):
            verdict = review.checked('T03', {}, 'x.png', product)
        self.assertEqual(([row['evidence'] for row in verdict['issues']], verdict.get('refuted', 0)), (['cropped', 'small'], 0))   # not asked again
        with patch.object(review, 'review', return_value={**good, 'score': 70}), patch.object(review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(review, '_ask', return_value={'claims': [{'number': 1, 'real': False, 'why': ''}, {'number': 2, 'real': True, 'why': ''}]}):
            verdict = review.checked('T03', {}, 'x.png', product)
        self.assertEqual(([row['evidence'] for row in verdict['issues']], verdict['refuted']), (['small'], 1))

    def test_a_line_across_the_product_to_the_far_rivet_is_caught_and_the_near_one_is_offered(self):
        import tempfile, info_layer
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        image = Image.new('RGB', (100, 100), 'white'); image.paste((120, 80, 40), (10, 10, 90, 90)); image.save(folder / 'hero.png')
        job = {'features': [{'anchor': [.85, .5], 'other_patch': True}, {'anchor': [.5, .5]}], 'assets': {'hero': {'path': str(folder / 'hero.png')}}}
        qa = {'issues': [], 'elements': [{'id': 'hero', 'kind': 'product_image', 'asset': 'hero', 'layout': [500, 500, 1000, 1000]}],
              'leaders': [{'id': 'ldr1', 'pts': [[100, 1000], [1350, 1000]]},          # from a left column to the far rivet
                          {'id': 'ldr2', 'pts': [[100, 1000], [1000, 1000]]}]}         # one large part: its line may cross it
        brandkit_render.pixel_checks(qa, job)
        self.assertEqual([(row['check'], row['id']) for row in qa['issues']], [('leader_over_product', 'ldr1')])
        nx = ny = 10; cells = [-1] * 100
        for x, y in ((1, 5), (2, 5), (1, 4), (7, 5), (8, 5), (8, 4), (3, 1)):
            cells[y * nx + x] = 0
        picture = info_layer.Picture.__new__(info_layer.Picture); picture.nx, picture.ny, picture.cells, picture.objects = nx, ny, cells, ['Rivet']
        self.assertEqual(len(picture.other_anchors(['Rivet'])), 1)                      # the second rivet, not the stray cell

    def test_a_close_up_frame_that_cuts_through_a_badge_is_caught_and_moved(self):
        nx = ny = 20; cells = [0] * 400                              # the body fills the render...
        for y in range(12, 15):
            for x in range(8, 12):
                cells[y * nx + x] = 1                                # ...with a small badge on it
        result = {'probe': {'nx': nx, 'ny': ny, 'cells': cells, 'objects': ['Canvas body', 'Label_MORA']}}
        parts = brandkit_set.part_map(result, (0, 0, 1, 1))
        self.assertEqual([row['name'] for row in parts], ['Label_MORA'])          # the body is too large to count
        def qa(frame_h, feather=None):
            frame = {'id': 'detail1_frame', 'kind': 'shape', 'x': 0, 'y': 0, 'w': 1000, 'h': frame_h}
            if feather:
                frame['feather'] = feather
            return {'issues': [], 'elements': [frame, {'id': 'detail1', 'kind': 'detail_image', 'asset': 'macro0', 'x': 0, 'y': 0, 'w': 1000, 'h': 1000}]}
        job = {'assets': {'macro0': {'_parts': parts}}}
        for frame_h, feather, cut in ((680, None, True), (900, None, False), (900, {'bottom': 400}, True), (500, None, False)):
            record = qa(frame_h, feather); brandkit_render.frame_checks(record, job)
            self.assertEqual(bool(record['issues']), cut, (frame_h, feather))
        self.assertNotIn('_parts', brandkit_render.compose('T02', {**job, 'canvas': {'width': 10, 'height': 10}}))
        jobs = brandkit_set._recropped([{'features': [{'detail': 'macro0', 'focus': [.5, .5]}], 'assets': job['assets']}, {'features': []}])
        self.assertEqual([row['features'][0]['focus'] for row in jobs[:5]], [[.5, .5], [.5, .3], [.5, .7], [.3, .5], [.7, .5]])
        self.assertEqual(len(jobs), 6)                                              # nothing to move without a part map

    def test_a_close_up_window_is_moved_off_a_badge_it_would_slice(self):
        nx = ny = 40; cells = [-1] * 1600
        for y in range(4, 16):
            for x in range(10, 20):
                cells[y * nx + x] = 0                                 # the handle the close-up is about
        for y in range(20, 26):
            for x in range(12, 18):
                cells[y * nx + x] = 1                                 # a badge just under it
        result = {'width': 1000, 'height': 1000, 'probe': {'nx': nx, 'ny': ny, 'cells': cells, 'objects': ['Handle', 'Label_MORA']}}
        region = brandkit_set.part_region(result, ['Handle'], least=.1, margin=.5)
        with patch.object(brandkit_set, '_uncut', side_effect=lambda window, *rest: window):
            plain = brandkit_set.part_region(result, ['Handle'], least=.1, margin=.5)
        self.assertEqual(brandkit_set._cuts(plain, brandkit_set.part_map(result, (0, 0, 1, 1))), ['Label_MORA'])     # as it was: through the badge
        parts = brandkit_set.part_map(result, (0, 0, 1, 1))
        self.assertEqual(brandkit_set._cuts(region, parts), [])
        self.assertTrue(region[0] <= .25 and region[1] <= .1 and region[2] >= .5 and region[3] >= .4, region)   # the handle still whole

    def test_scenes_are_captioned_from_the_pictures_once(self):
        import brandkit_review
        rows = [{'path': 'a.png', 'title': '办公随行', 'body': '伴随职场日常'}, {'path': 'b.png', 'title': '日常外出', 'body': ''}]
        answers = iter([{'title': '窗边木桌', 'body': '托特包立在桌上，旁有绿植'}, {'title': '窗边木桌', 'body': '重复的标题'}])
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', side_effect=lambda *a, **k: next(answers)) as asked:
            out, done = brandkit_set.seen_captions({'language': 'zh', 'title': '托特包'}, rows)
        self.assertEqual(asked.call_args_list[0].kwargs['image_path'], 'a.png')
        self.assertEqual(([row['title'] for row in out], out[0]['body'], out[0].get('caption'), done), (['窗边木桌', '日常外出'], '托特包立在桌上，旁有绿植', 'seen', 1))

    def test_chinese_captions_are_set_one_clause_per_line(self):
        self.assertEqual(brandkit_set._clause_lines('托特包置于窗边，随手可取', 'zh', 8), '托特包置于窗边，\n随手可取')
        self.assertEqual(brandkit_set._clause_lines('简洁背景下托特包伴随职场日常', 'zh', 8), '')            # it would wrap mid-word
        self.assertEqual(brandkit_set._clause_lines('By the window, at hand', 'en', 8), 'By the window, at hand')

    def test_the_copywriter_and_the_reviewer_are_skipped_without_models(self):
        import brandkit_review as review
        with patch.object(review, '_configs', return_value=(None, None)):
            self.assertEqual(review.write_copy({'language': 'en'}, ['T01']), ({}, None))
            self.assertIsNone(review.review('T01', {}, 'x.png', {}))
            self.assertFalse(review.passed(None))
        self.assertTrue(set(brandkit_set.AUTO) <= set(review.briefs()))


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class LayoutTests(StoreCase):
    """A whole set, laid out by the real templates in a headless browser, with Blender and the image model stood in for."""

    def setUp(self):
        super().setUp()
        mocked = patch.object(components, 'stage', side_effect=self.stage); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(brandkit_set, 'image_model_ready', return_value=False); mocked.start(); self.addCleanup(mocked.stop)
        listing.save_brand('fixture', {'name': 'NORTHFORGE', 'accent': '#d0612a'})

    def product(self, language):
        zh = language == 'zh'
        listing.save_product('fixture', {
            'title': '棘轮割管器' if zh else 'Ratchet Pipe Cutter', 'language': language, 'size_mm': 225,
            'headline': '单手操作，切口平整' if zh else 'One-hand clean cuts', 'subline': '切口方正，无毛刺。' if zh else 'Square, burr-free cuts.',
            'features': [{'title': '硬化刀轮' if zh else 'Hardened cutting wheel', 'detail': '可更换' if zh else 'Replaceable', 'parts': [PARTS[0]]},
                         {'title': '双导向滚轮' if zh else 'Twin guide rollers', 'parts': [PARTS[1]]}],
            'specs': [{'label': '管径范围' if zh else 'Pipe capacity', 'value': '6 - 22 mm'}], 'figures': [{'value': '22', 'unit': 'mm', 'label': '最大管径' if zh else 'Max pipe'}]})

    def test_every_page_is_laid_out_and_passes_its_checks(self):
        self.add_picture(name='Battery', quantity='2')
        for language, aspect in (('en', '1:1'), ('zh', '4:5')):
            with self.subTest(language=language, aspect=aspect):
                self.product(language); listing.save_product('fixture', {'aspect': aspect})
                pages, notes = listing.build('fixture')
                self.assertEqual([page['kind'] for page in pages], ['T12', 'T01', 'T03', 'T02', 'T04', 'T06', 'T05'], notes)
                for page in pages:
                    self.assertTrue(page['checks']['passed'], (page['kind'], page['checks'], page['notes']))
                    with Image.open(components.path_of('fixture', page['url'])) as image:
                        self.assertEqual(image.size, brandkit_set.CANVAS[aspect])
                        self.assertGreater(len(image.convert('L').getcolors(65536) or []), 20)        # a page, not a blank
        self.assertFalse(list((self.data / 'fixture' / 'listing').glob('build-*/_layout')))           # the browser's working files are gone

    def test_text_on_the_product_is_caught(self):
        out = self.data / 'probe.png'; hero = cut_out(self.data / 'hero-src.png', size=(800, 800), box=(0, 0, 800, 800))
        fields, _ = brandkit_set.flatten(hero, self.data / 'hero.png')
        job = {'template_id': 'T12', 'canvas': {'width': 1500, 'height': 1500}, 'lang': 'en', 'safe_margin': 84,
               'palette': brandkit_render.template_info('T12')['tokens']['palette'], 'mode': 'solid', 'hero': 'hero',
               'headline': 'A headline', 'assets': {'hero': fields}}
        qa = brandkit_render.render('T12', job, out)
        self.assertEqual(qa['render']['size'], [1500, 1500]); self.assertTrue(out.is_file())
        self.assertEqual(brandkit_render.layout_issues(qa), [])
        self.assertIn('pixel_checks', qa)


if __name__ == '__main__':
    unittest.main()

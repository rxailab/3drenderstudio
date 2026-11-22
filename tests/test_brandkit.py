"""The brand-kit templates: the geometry and words handed to them, and real layouts in a headless browser.

The browser tests run when Chrome, Chromium or Edge is installed (``BRANDKIT_CHROME`` points at one
elsewhere); the rest needs nothing but Pillow.
"""
import shutil
import sys
import tempfile
import json
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
        # A listing never prints "…": the leading segment that fits, else whole words.
        self.assertEqual(brandkit_set._clip('Rolling Stack Toolbox', 18), 'Rolling Stack')
        self.assertEqual(brandkit_set._clip('Stack it,\nroll it away', 40), 'Stack it,\nroll it away')
        self.assertEqual(brandkit_set._clip('叠放式带轮工具箱组合套装', 8), '叠放式带轮工具箱')
        self.assertEqual(brandkit_set._clip('Digital Caliper 0–150 mm / 0–6 in Stainless Steel', 24), 'Digital Caliper 0–150 mm')
        self.assertEqual(brandkit_set._clip('2 × 20V 4.0 Ah Li-Ion Batteries', 22), '2 × 20V 4.0 Ah Li-Ion')
        self.assertNotIn('…', brandkit_set._clip('Fast cutting and grinding of metal, tile and stone — includes 2 batteries', 60))
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
                            {'template': 'T04', 'eyebrow': '参数', 'headline': '高度520mm\n容量顺手'},          # a number the facts do not give
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
        page = brandkit_render.compose('T02', {**job, 'canvas': {'width': 10, 'height': 10}})
        self.assertNotIn('_parts', page[page.index('window.JOB'):page.index('</script>', page.index('window.JOB'))])
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

    def test_a_scene_tile_that_cuts_the_product_off_fails_and_its_box_is_read_off_the_reference(self):
        import tempfile
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        reference = Image.new('RGB', (100, 100), 'white'); reference.paste((90, 60, 30), (20, 40, 80, 70)); reference.save(folder / 'reference.png')
        self.assertEqual(brandkit_set.scene_box({'path': str(folder / 'scene1.png')}, slack=0), [.2, .4, .8, .7])
        self.assertEqual(brandkit_set.scene_box({'path': 'x.png', 'box': [.2, .2, .6, .4]}), [.14, .17, .66, .43])     # some air round it
        job = {'assets': {'scene1': {'_product': [.2, .4, .8, .7]}}}
        def qa(frame_w):          # a 1000 px picture shown in a frame of this width, centred on the product
            left = 500 - frame_w / 2
            return {'issues': [], 'elements': [{'id': 'tile1_frame', 'kind': 'shape', 'x': left, 'y': 0, 'w': frame_w, 'h': 1000},
                                               {'id': 'tile1', 'kind': 'detail_image', 'asset': 'scene1', 'x': 0, 'y': 0, 'w': 1000, 'h': 1000}]}
        for frame_w, cut in ((420, True), (700, False)):
            record = qa(frame_w); brandkit_render.frame_checks(record, job)
            self.assertEqual([row['check'] for row in record['issues']], ['scene_product_cropped'] if cut else [], frame_w)

    def test_an_english_set_prints_the_records_chinese_words_in_english_keeping_their_numbers(self):
        import brandkit_review
        product = {'language': 'en', 'title': '棘轮割管器', 'headline': 'Clean cuts', 'subline': '', 'usecase': {'headline': ''},
                   'features': [{'title': '硬化刀轮', 'detail': '切割 6-22 mm 管材', 'parts': ['Wheel']}, {'title': '四颗铆钉', 'detail': ''}],
                   'specs': [{'label': '重量', 'value': '380 g'}], 'figures': []}
        scenes = [{'title': '工地现场', 'body': '', 'path': 'a.png'}]; parts = [{'name': '电池', 'caption': ''}]
        answer = {'lines': [{'source': '棘轮割管器', 'text': 'Ratchet Pipe Cutter'}, {'source': '硬化刀轮', 'text': 'Hardened wheel'},
                            {'source': '切割 6-22 mm 管材', 'text': 'Cuts 6-25 mm pipe'},               # a number changed: refused
                            {'source': '重量', 'text': 'Weight'}, {'source': '工地现场', 'text': 'On site'}, {'source': '电池', 'text': 'Battery'},
                            {'source': '四颗铆钉', 'text': '4 rivets'}]}                                  # 四 written as a figure is fine
        cache = {}
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), patch.object(brandkit_review, '_ask', return_value=answer) as asked:
            done, shown, kit, notes = brandkit_set.localise(product, scenes, parts, cache)
            again = brandkit_set.localise(product, scenes, parts, cache)
        self.assertEqual((done['title'], done['headline'], done['features'][0]['title'], done['specs'][0]['label']), ('Ratchet Pipe Cutter', 'Clean cuts', 'Hardened wheel', 'Weight'))
        self.assertEqual((shown[0]['title'], kit[0]['name'], shown[0]['path'], done['features'][1]['title']), ('On site', 'Battery', 'a.png', '4 rivets'))
        self.assertEqual(brandkit_set._arabic('四颗铆钉，十二件，二十'), '4颗铆钉，12件，20')
        self.assertEqual(done['features'][0]['detail'], ''); self.assertIn('没能译成英文', notes[0])     # never printed in Chinese
        self.assertEqual(product['title'], '棘轮割管器')                                          # the record itself is not changed
        self.assertEqual(asked.call_count, 4)                     # the refused line once more within each build
        self.assertEqual(asked.call_args.args[2]['lines'], [{'source': '切割 6-22 mm 管材', 'max_chars': 40}])
        self.assertEqual(brandkit_set.localise({**product, 'language': 'zh'}, scenes, parts, {})[0]['title'], '棘轮割管器')
        with patch.object(brandkit_review, '_configs', return_value=(None, None)):
            plain = brandkit_set.localise(product, scenes, parts, {})
            self.assertIn('没有配置文本模型', plain[3][0]); self.assertEqual(plain[0]['title'], '')

    def test_scenes_are_captioned_from_the_pictures_once(self):
        import brandkit_review
        rows = [{'path': 'a.png', 'title': '办公随行', 'body': '伴随职场日常'}, {'path': 'b.png', 'title': '日常外出', 'body': ''}]
        answers = iter([{'title': '窗边木桌', 'body': '托特包立在桌上，旁有绿植'}, {'title': '窗边木桌', 'body': '重复的标题'}])
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', side_effect=lambda *a, **k: next(answers)) as asked:
            out, done = brandkit_set.seen_captions({'language': 'zh', 'title': '托特包'}, rows)
        self.assertEqual(asked.call_args_list[0].kwargs['image_path'], 'a.png')
        self.assertEqual(([row['title'] for row in out], out[0]['body'], out[0].get('caption'), done), (['窗边木桌', '日常外出'], '托特包立在桌上，旁有绿植', brandkit_set.CAPTIONED, 1))

    def test_a_scene_title_naming_a_place_already_shown_is_refused(self):
        import brandkit_review
        rows = [{'path': 'a.png', 'title': 'x', 'body': '', 'role': 'use'}, {'path': 'b.png', 'title': 'y', 'body': '', 'role': 'place'},
                {'path': 'c.png', 'title': 'On a house call', 'body': 'Kept', 'role': 'other'}]
        answers = iter([{'title': 'Stripping wire', 'body': 'Grips the copper firmly'}, {'title': 'In the workshop', 'body': 'Rests on the bench'},
                        {'title': 'At the workbench', 'body': 'Cuts copper cleanly'}])
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', side_effect=lambda *a, **k: next(answers)) as asked:
            out, done = brandkit_set.seen_captions({'language': 'en', 'title': 'Pliers'}, rows)
        self.assertEqual([row['title'] for row in out], ['Stripping wire', 'In the workshop', 'On a house call'])
        self.assertEqual(done, 2)
        self.assertIn('task', asked.call_args_list[0].args[2]['shows']); self.assertIn('place', asked.call_args_list[1].args[2]['shows'])
        self.assertTrue(brandkit_set._same_place('在工坊里', '工作台上'))
        self.assertFalse(brandkit_set._same_place('Cutting wire', 'Stripping wire'))

    def test_the_third_scene_has_its_own_setting_when_no_other_buyer_is_named(self):
        import scene_audience, scene_stylist
        first = {'scene': 'Hands cut copper wire on a wooden bench.', 'segment_id': 'workshop_hobbyist', 'people': True, 'framing': 'hands',
                 'summary': 'workshop plan', 'reference_id': 'R1', 'reason': 'why'}
        settings = {'hint': '', 'audience': 'auto', 'people': 'auto'}
        with patch.object(scene_stylist, 'propose', return_value=first), patch.object(scene_stylist, '_source_image', return_value=None), \
                patch.object(scene_audience, 'rank', return_value=[]):
            briefs = brandkit_set.scene_briefs({'name': 'Thing'}, settings, 3)
        third = briefs[2]['proposal']
        self.assertEqual(third['segment_id'], scene_audience.NEIGHBOUR['workshop_hobbyist'])
        self.assertNotIn(first['scene'], third['scene']); self.assertIsNone(third['reference_id'])
        self.assertEqual(scene_audience.rank('Combination Pliers', 'Combination Pliers')[:1], ['mobile_tradesperson'])
        self.assertNotIn('mobile_tradesperson', scene_audience.rank('wireless earbuds', 'wireless earbuds'))

    def test_close_ups_of_located_points_are_cut_from_the_heros_own_camera(self):
        """The points are found on the hero: the zoom it is cropped from must be the same view (a flat tool's size page
        is drawn from above, which once turned the zoom top-down too)."""
        project = {'model_path': 'm.glb', 'plan': {'rotation': [0, 0, 0], 'shots': [{'id': 'hero', 'azimuth': 35, 'elevation': 24}]},
                   'analysis': {'features': {'dimensions': [200, 60, 15]}}}
        product = {'features': [{'title': 'Jaws', 'point': [.27, .42], 'parts': ['Jaw']}], '_model_parts': [], 'usecase': {'enabled': False}}
        rows = [{'kind': kind} for kind in ('T01', 'T02', 'T06')]
        with patch.object(listing, 'display_rotation', return_value=([0, 0, 0], False)):
            requests = listing.picture_requests(project, product, 200, rows)
        self.assertEqual(requests['side']['camera']['elevation'], 89)                       # the flat tool's size page, from above
        self.assertEqual(requests['zoom']['camera'], requests['hero']['camera'])
        self.assertEqual(requests['zoom']['size'], [3600, 3600])

    def test_a_product_with_nothing_but_a_name_gets_no_made_up_uses(self):
        import brandkit_review
        bare = {'title': 'Diagonal Cutting Pliers', 'model': '', 'headline': '', 'subline': '', 'features': [], 'specs': [], 'figures': [],
                'size_mm': 160}
        self.assertIn('no uses, tasks, benefits', brandkit_review.facts_of(bare)['scope'])
        told = {**bare, 'features': [{'title': 'Induction-hardened edges', 'detail': ''}]}
        self.assertNotIn('scope', brandkit_review.facts_of(told))
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), patch.object(brandkit_review, '_ask') as asked:
            copy, note = brandkit_review.write_copy({**bare, 'language': 'en'}, ['T12', 'T04'])
        self.assertEqual((copy, asked.call_count), ({}, 0))                  # no copywriter: the pages use the product data
        self.assertIn('没有让文案模型发挥', note)

    def test_a_set_cut_short_last_time_makes_only_the_missing_scene(self):
        import scene_stylist
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        kept = []
        for role in ('use', 'place'):
            path = folder / (role + '.png'); Image.new('RGB', (8, 8)).save(path)
            kept.append({'path': str(path), 'role': role, 'title': role, 'body': '', 'focus': [.5, .5], 'box': [.3, .3, .7, .7], 'check': 'passed'})
        briefs = [{'role': role, 'proposal': {'scene': role}} for role in ('use', 'place', 'other')]
        made = []
        def generate(project, proposal, *args):
            made.append(proposal['scene']); picture = folder / 'new.png'; Image.new('RGB', (8, 8)).save(picture)
            return None, {'check': {'status': 'passed'}}, {'png': picture, 'record': {}}
        settings = {'enabled': True, 'count': 3, 'hint': '', 'audience': 'auto', 'people': 'auto'}
        with patch.object(brandkit_set, 'image_model_ready', return_value=True), patch.object(brandkit_set, 'scene_key', return_value='k'), \
                patch.object(brandkit_set, 'scene_briefs', return_value=briefs), \
                patch.object(brandkit_set, 'captions', return_value=[{'title': 't', 'body': ''}] * 3), \
                patch.object(brandkit_set, 'seen_captions', side_effect=lambda product, rows: (rows, 0)), \
                patch.object(scene_stylist, 'generate', side_effect=generate):
            rows, key, notes = brandkit_set.make_scenes({'id': 'p'}, {'scenes': settings, 'language': 'en'}, folder, lambda: False,
                                                        lambda *a: None, {'key': 'k', 'items': kept})
        self.assertEqual(made, ['other'])                                      # only the scene that was never made
        self.assertEqual([row['role'] for row in rows], ['use', 'place', 'other'])
        self.assertIn('补生成缺少的 1 张', notes[0])

    def test_a_scene_that_failed_before_the_extra_try_is_made_once_more(self):
        import scene_stylist
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        rows = []
        for role, check in (('use', 'failed'), ('place', 'passed'), ('other', 'failed')):
            path = folder / (role + '.png'); Image.new('RGB', (8, 8)).save(path)
            rows.append({'path': str(path), 'role': role, 'title': role, 'body': '', 'focus': [.5, .5], 'box': [.3, .3, .7, .7], 'check': check})
        rows[2]['usage_retry'] = 1                        # already had its extra try: kept as it is
        made = []
        def generate(project, proposal, *args):
            made.append(proposal['scene']); picture = folder / 'new.png'; Image.new('RGB', (8, 8)).save(picture)
            return None, {'check': {'status': 'passed'}, 'usage_retry': 1}, {'png': picture, 'record': {}}
        settings = {'enabled': True, 'count': 3, 'hint': '', 'audience': 'auto', 'people': 'auto'}
        with patch.object(brandkit_set, 'image_model_ready', return_value=True), patch.object(brandkit_set, 'scene_key', return_value='k'), \
                patch.object(brandkit_set, 'scene_briefs', return_value=[{'role': r, 'proposal': {'scene': r}} for r in ('use', 'place', 'other')]), \
                patch.object(brandkit_set, 'captions', return_value=[{'title': 't', 'body': ''}] * 3), \
                patch.object(brandkit_set, 'seen_captions', side_effect=lambda product, rows: (rows, 0)), \
                patch.object(scene_stylist, 'generate', side_effect=generate):
            out, _, _ = brandkit_set.make_scenes({'id': 'p'}, {'scenes': settings, 'language': 'en'}, folder, lambda: False,
                                                 lambda *a: None, {'key': 'k', 'items': rows})
        self.assertEqual(made, ['use'])
        self.assertEqual([(row['role'], row['check']) for row in out], [('use', 'passed'), ('place', 'passed'), ('other', 'failed')])

    def test_pages_with_no_copy_never_fall_back_to_stock_or_broken_lines(self):
        product = {'title': 'Rolling Tool Cabinet', 'features': [], 'figures': [], 'specs': [], 'headline': '', 'subline': ''}
        ctx = {'product': product, 'language': 'en', 'scenes': [{'title': 'In use', 'role': 'use'}, {'title': 'At the workbench', 'role': 'place'},
                                                                 {'title': 'On the job site', 'role': 'other'}]}
        self.assertEqual(brandkit_set._data_line('T04', ctx), 'Rolling Tool Cabinet')
        self.assertEqual(brandkit_set._data_line('T08', ctx), 'Rolling Tool Cabinet')        # not "From use to the workbench"
        ctx['scenes'][0]['title'] = 'In the garage'
        self.assertEqual(brandkit_set._data_line('T08', ctx), 'Rolling Tool Cabinet')        # nor a story of its use: nothing was said of it
        ctx['product'] = {**product, 'features': [{'title': 'Soft-close drawers', 'detail': ''}]}
        self.assertEqual(brandkit_set._data_line('T08', ctx), 'From the garage\n[[to the workbench]]')

    def test_a_long_tool_across_a_scene_keeps_room_at_the_sides(self):
        # a caliper held across 80 % of the picture: the air added round it must not reach the picture's edges
        self.assertEqual(brandkit_set.scene_box({'box': [.1, .42, .9, .64]}), [.04, .387, .96, .673])
        self.assertEqual(brandkit_set.scene_box({'box': [.3, .4, .5, .6]}), [.27, .37, .53, .63])        # small ones keep 15 %

    def test_a_scene_mismatch_is_not_a_veto_on_a_page_without_a_scene(self):
        import brandkit_review
        answer = {'scores': {key: value for key, value in brandkit_review.CRITERIA.items()}, 'issues': [], 'summary': '',
                  'vetoes': [{'kind': 'scene_product_mismatch', 'evidence': 'handle all red'}]}
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), patch.object(brandkit_review, '_ask', return_value=answer):
            closeup = brandkit_review.review('T02', {'canvas': {}, 'assets': {'macro0': {}}}, 'p.png', {'language': 'en'}, facts={'title': 'x'})
            scene = brandkit_review.review('T08', {'canvas': {}, 'assets': {'scene1': {}}}, 'p.png', {'language': 'en'}, facts={'title': 'x'})
        self.assertEqual(closeup['vetoes'], [])
        self.assertEqual([row['kind'] for row in scene['vetoes']], ['scene_product_mismatch'])

    def test_each_language_keeps_its_own_scene_captions(self):
        import brandkit_review
        rows = [{'path': 'a.png', 'title': '办公随行', 'body': ''}]
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', return_value={'title': 'At the desk', 'body': 'Stands upright, ready to grab'}):
            english, _ = brandkit_set.seen_captions({'language': 'en', 'title': 'Tote'}, rows)
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), \
                patch.object(brandkit_review, '_ask', return_value={'title': '书桌旁', 'body': '稳稳立住，随手可拿'}) as asked:
            both, _ = brandkit_set.seen_captions({'language': 'zh', 'title': '托特包'}, english)
            again, _ = brandkit_set.seen_captions({'language': 'zh', 'title': '托特包'}, both)
        self.assertEqual(asked.call_count, 1)                                     # Chinese written once, then reused
        self.assertEqual(sorted(both[0]['captions']), ['en', 'zh'])
        self.assertEqual((both[0]['captions']['en']['title'], both[0]['title']), ('At the desk', '书桌旁'))

    def test_selling_points_are_found_on_the_picture_when_part_names_do_not_place_them(self):
        import brandkit_review, info_layer
        nx = ny = 10; cells = [-1] * 100
        for y in range(3, 7):
            for x in range(1, 9):
                cells[y * nx + x] = 0 if x < 5 else 1                         # two meshes with meaningless names
        picture = info_layer.Picture.__new__(info_layer.Picture); picture.nx, picture.ny, picture.cells, picture.objects = nx, ny, cells, ['Circle.001', 'Circle.004']
        product = {'title': 'Pliers', 'features': [{'title': 'Serrated jaws', 'parts': []}, {'title': 'Comfort grip', 'parts': []},
                                                   {'title': 'CR-V steel', 'parts': []}, {'title': 'Side cutter', 'parts': []}],
                   '_model_parts': ['Circle.001', 'Circle.004'], 'figures': [], 'specs': [], 'scenes': brandkit_set.default_scenes()}
        answer = {'points': [{'number': 1, 'visible': True, 'x': .25, 'y': .45}, {'number': 2, 'visible': True, 'x': .75, 'y': .5},
                             {'number': 3, 'visible': False, 'x': 0, 'y': 0}, {'number': 4, 'visible': True, 'x': .5, 'y': .95}]}   # 4: off the product
        cache = {}
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), patch.object(brandkit_review, '_ask', return_value=answer) as asked:
            features, notes = brandkit_set.locate('hero.png', picture, product, cache)
            brandkit_set.locate('hero.png', picture, product, cache)
        self.assertEqual(asked.call_count, 1)                                      # kept with the project, not asked again
        self.assertEqual([row.get('parts') for row in features], [['Circle.001'], ['Circle.004'], [], []])
        self.assertEqual((features[0]['point'], 'point' in features[3]), ([.25, .45], False))
        self.assertIn('2 条', notes[0])
        located = {**product, 'features': features}
        self.assertEqual((brandkit_set.anchored(located), brandkit_set.pointed(located)), ([0, 1], []))
        # A model in one piece: the point is the callout's anchor, but there is no part to show close up.
        whole = {**product, '_model_parts': ['Digital Caliper']}
        picture.objects = ['Digital Caliper']; picture.cells = [0 if cell >= 0 else -1 for cell in cells]
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'vision'})), patch.object(brandkit_review, '_ask', return_value=answer):
            single, _ = brandkit_set.locate('hero.png', picture, whole, {})
        single_product = {**whole, 'features': single}
        self.assertEqual((brandkit_set.anchored(single_product), brandkit_set.pointed(single_product)), ([], [0, 1]))
        has, why = brandkit_set.requirements(single_product, [], True, 0)
        self.assertEqual((has['T01'], has['T03'], has['T02']), (True, False, False)); self.assertIn('整体', why['T03'])
        # Every part named at once is the whole product, not a part of it.
        self.assertEqual(brandkit_set.anchored({'features': [{'title': 'Steel', 'parts': ['Digital Caliper']}], '_model_parts': ['Digital Caliper']}), [])

    def test_a_tool_lying_flat_is_measured_length_width_and_thickness(self):
        rows = brandkit_set.dimensions_mm({'dims_mm': [235.0, 80.0, 10.0], 'flat': True})
        self.assertEqual(rows, [('length', 235.0), ('width', 80.0), ('thickness', 10.0)])
        self.assertEqual(brandkit_set.dimensions_mm({'dims_mm': [405.0, 180.0, 476.0]}), [('width', 405.0), ('height', 476.0), ('depth', 180.0)])
        self.assertTrue(listing._flat([235, 80, 10])); self.assertFalse(listing._flat([405, 180, 476]))

    def test_a_body_line_is_never_cut_mid_sentence_and_joined_words_stay_together(self):
        self.assertEqual(brandkit_set._body('Precision-ground jaws, smooth-sliding beam with a thumb roller', 40), 'Precision-ground jaws.')
        self.assertIsNone(brandkit_set._body('One button switches between millimetres and inches', 40))     # no whole clause fits: left out
        self.assertEqual(brandkit_set._body('Cuts soft wire', 40), 'Cuts soft wire')
        self.assertEqual(brandkit_set._keep_together('Forged CR-V steel'), 'Forged CR-\u2060V steel')
        self.assertEqual(brandkit_set._keep_together('Two - tone'), 'Two - tone')

    def test_a_line_longer_than_its_space_is_rewritten_shorter_in_its_own_language(self):
        import brandkit_review
        product = {'language': 'en', 'title': 'Digital Caliper', 'headline': '', 'subline': '', 'usecase': {'headline': ''},
                   'features': [{'title': 'Inch/MM Conversion', 'detail': 'One button switches between millimetres and inches'}], 'specs': [], 'figures': []}
        answer = {'lines': [{'source': 'One button switches between millimetres and inches', 'text': 'One-button mm/inch switch'}]}
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), patch.object(brandkit_review, '_ask', return_value=answer) as asked:
            done, _, _, notes = brandkit_set.localise(product, [], [], {})
        self.assertEqual(done['features'][0]['detail'], 'One-button mm/inch switch'); self.assertEqual(notes, [])
        self.assertEqual(asked.call_args.args[2]['lines'], [{'source': 'One button switches between millimetres and inches', 'max_chars': 40}])
        zh = {**product, 'language': 'zh', 'title': '数显卡尺', 'features': [{'title': '大屏显示', 'detail': '五位数字读数'}]}
        with patch.object(brandkit_review, '_configs', return_value=({'model': 'text'}, None)), patch.object(brandkit_review, '_ask') as untouched:
            same = brandkit_set.localise(zh, [], [], {})[0]
        self.assertEqual((same['title'], same['features'][0]['detail'], untouched.call_count), ('数显卡尺', '五位数字读数', 0))

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

    def test_a_brand_look_from_the_library_is_carried_through_the_set(self):
        """A font pairing, badges and a texture from the brand panel reach the pages, and the pages still pass."""
        self.product('en')
        listing.save_brand('fixture', {'fonts': 'poster', 'badges': ['2 Year Warranty', 'PRO'], 'badge_style': 'burst', 'texture': 'diagonal'})
        pages, notes = listing.build('fixture')
        for page in pages:
            self.assertTrue(page['checks']['passed'], (page['kind'], page['checks'], page['notes']))
        build = max((self.data / 'fixture' / 'listing').glob('build-*'), key=lambda path: path.stat().st_mtime)
        job = json.loads((build / 'jobs' / 'T01.json').read_text())[-1]
        self.assertEqual(job['fonts']['pairing'], 'poster'); self.assertEqual(job['texture'], 'diagonal')
        self.assertEqual([row['text'] for row in job['badges']], ['2 Year Warranty'])        # one badge on the main image
        qa = brandkit_render.render('T01', job, self.data / 'again.png', work=self.data)
        ids = {row['id'] for row in qa['elements']}
        self.assertIn('texture', ids)
        self.assertTrue({'brand_badge1', 'brand_badge1_t'} <= ids or any('badge' in note for note in qa['notes']), qa['notes'])
        self.assertFalse([row for row in qa['issues'] if row['check'] in ('overlap', 'font_missing', 'safe_margin', 'out_of_canvas')])
        self.assertIn('Anton', {row['family'] for row in qa['fonts']})

    def test_the_poses_page_is_laid_out_and_passes_its_checks(self):
        """T13: two or three states of a product cut with one window, side by side or stacked, in either language."""
        import brand_kit
        work = self.data / 'poses'; work.mkdir()
        shapes = {'long': ((1800, 1800), [(300, 820, 1500, 900), (300, 820, 1500, 900), (300, 820, 1500, 900)],
                           [(300, 760, 420, 820), (520, 760, 640, 820), (1300, 760, 1420, 820)]),        # a beam, its jaw sliding along it
                  'compact': ((1800, 1800), [(600, 600, 1100, 1100)] * 3, [(1100, 980, 1150, 1000), (1100, 980, 1400, 1080), (1100, 980, 1600, 1150)])}
        labels = {'en': ['Closed', 'Measuring', 'Fully open'], 'zh': ['闭合', '测量中', '全开']}
        for shape, (size, bodies, moving) in shapes.items():
            sources = []
            for index, (body, part) in enumerate(zip(bodies, moving)):
                path = work / ('%s-%d.png' % (shape, index))
                image = Image.new('RGBA', size, (0, 0, 0, 0)); pen = ImageDraw.Draw(image)
                pen.rectangle(body, fill=(60, 64, 70, 255)); pen.rectangle(part, fill=(200, 90, 40, 255)); image.save(path)
                sources.append(path)
            assets = brandkit_set.pose_assets(sources, work / shape)
            self.assertEqual(len({(row['width'], row['height']) for row in assets}), 1)               # one window for every pose
            for language, aspect, count in (('en', '1:1', 3), ('zh', '4:5', 3), ('en', '4:5', 2), ('zh', '1:1', 2)):
                with self.subTest(shape=shape, language=language, aspect=aspect, count=count):
                    use = [0, 1, 2][:count] if count == 3 else [0, 2]
                    ctx = {'language': language, 'aspect': aspect, 'brand': brand_kit.Brand(brand_kit.validate({}), '#d0612a'), 'copy': {},
                           'product': {'title': '数显卡尺' if language == 'zh' else 'Digital Caliper', 'headline': '', 'subline': '', 'features': [],
                                       'specs': [], 'figures': []},
                           'assets': {'pose%d' % index: assets[index] for index in use},
                           'poses': [{'asset': 'pose%d' % index, 'label': labels[language][index]} for index in use]}
                    jobs = brandkit_set.jobs_for('T13', ctx)
                    self.assertEqual(jobs[0]['mode'], 'auto'); self.assertEqual(len(jobs[0]['poses']), count)
                    out = self.data / ('T13-%s-%s-%s-%d.png' % (shape, language, aspect.replace(':', 'x'), count))
                    qa, notes, review = brandkit_set.lay_out('T13', ctx, out, self.data / '_layout')
                    self.assertEqual(brandkit_render.layout_issues(qa), [], (qa['issues'], notes))
                    self.assertFalse([row for row in qa['issues'] if row['check'] in ('min_size_at_360', 'upscale', 'font_missing')], qa['issues'])
                    ids = {row['id'] for row in qa['elements']}
                    self.assertTrue({'headline', 'eyebrow'} | {'p%d_img' % (index + 1) for index in range(count)} | {'p%d_label' % (index + 1) for index in range(count)} <= ids, ids)
                    self.assertEqual(review['status'], 'not_verified')
                    printed = [row['text'] for row in qa['elements'] if row['kind'] == 'text']
                    self.assertTrue(set(labels[language][index] for index in use) <= set(printed), printed)
                    # every pose at one scale, and a long product is stacked
                    scales = {row['scale'] for row in qa['elements'] if row['kind'] == 'product_image'}
                    self.assertEqual(len(scales), 1, scales)
                    if shape == 'long':
                        self.assertEqual(qa['layout']['mode'], 'rows')
                    with Image.open(out) as image:
                        self.assertEqual(image.size, brandkit_set.CANVAS[aspect])

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


@unittest.skipUnless(brandkit_render.available(), 'No Chrome/Chromium/Edge: template layout not run')
class SceneMosaicTests(unittest.TestCase):
    """Scenes whose product is held across the whole picture (a caliper in a hands close-up)."""

    def test_a_tall_tile_that_would_leave_bare_ground_gives_way_to_one_row_per_scene(self):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        assets = {}
        for index in range(3):
            picture = Image.new('RGB', (1024, 1024), (120 + 30 * index, 90, 60))
            ImageDraw.Draw(picture).rectangle([60, 430, 960, 620], fill=(235, 235, 235))
            path = folder / ('scene%d.png' % (index + 1)); picture.save(path)
            assets['scene%d' % (index + 1)] = {**brandkit_render.asset(path), '_product': [.04, .39, .96, .66]}
        items = [{'asset': name, 'title': title, 'body': None, 'focus': [.5, .5], 'box': [.04, .39, .96, .66]}
                 for name, title in zip(assets, ('Measuring a dowel', 'At the bench', 'In the plant room'))]
        base = {'template_id': 'T08', 'variant': 'studio', 'canvas': {'width': 1500, 'height': 1500}, 'lang': 'en', 'safe_margin': 72,
                'min_margin': 60, 'palette': brandkit_render.template_info('T08')['tokens']['palette'], 'semantics': 'uses',
                'eyebrow': 'Four modes', 'headline': 'One tool for every job', 'items': items, 'assets': assets}
        mosaic = brandkit_render.render('T08', {**base, 'mode': 'stagger'}, folder / 'a.png', work=folder)
        rows = brandkit_render.render('T08', {**base, 'mode': 'stagger', 'stagger_style': 'rows'}, folder / 'b.png', work=folder)
        self.assertIn('empty_area', [row['check'] for row in brandkit_render.layout_issues(mosaic)])
        self.assertEqual(brandkit_render.layout_issues(rows), [])

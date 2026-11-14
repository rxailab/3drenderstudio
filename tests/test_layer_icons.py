"""The icon agent: icons found in open sets, online, or drawn by a model, stored as plain path data.

No network and no model service: online answers and model replies are scripted.
What is checked: a glyph can carry nothing but path data, the vendored sets are
the ones recorded with their licences, searching in Chinese or English finds the
literal picture (a wrench for a wrench size), only freely licensed sets are taken
online, and a drawing is validated and checked before it is used.
"""
import gzip
import hashlib
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import layer_icons as icons

ONLINE_ALLOWED = icons.online_allowed          # the real setting, before the tests stub it
GOOD = {'source': 'drawn', 'name': '扳手', 'license': 'own', 'ops': [['s', 'M4 20 L14 10'], ['f', 'M15 5a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z']], 'stroke': 2, 'grid': 24}


def offline():
    """No model and no network for the duration of a test."""
    return [patch.object(icons, '_config', return_value=None), patch.object(icons, 'online_allowed', return_value=False)]


class Offline(unittest.TestCase):
    def setUp(self):
        for mocked in offline():
            mocked.start(); self.addCleanup(mocked.stop)


class GlyphContractTests(unittest.TestCase):
    def test_a_glyph_is_path_data_with_where_it_came_from(self):
        value = icons.validate_glyph({**GOOD, 'ops': [['s', 'M4  20\n L14 10']], 'check': {'status': 'passed', 'note': '清楚'}, 'concept': '扳手'})
        self.assertEqual(value['ops'], [['s', 'M4 20 L14 10']])                    # whitespace is normalised
        self.assertEqual((value['source'], value['license'], value['check']), ('drawn', 'own', {'status': 'passed', 'note': '清楚'}))
        json.dumps(value)

    def test_nothing_but_path_commands_and_numbers_gets_in(self):
        bad = [{**GOOD, 'ops': [['s', 'M0 0"/><script>alert(1)</script>']]}, {**GOOD, 'ops': [['s', 'url(#x)']]}, {**GOOD, 'ops': [['s', 'L0 0']]},
               {**GOOD, 'ops': [['x', 'M0 0']]}, {**GOOD, 'ops': []}, {**GOOD, 'ops': [['s', 'M0 0']] * 33}, {**GOOD, 'ops': [['s', 'M0 0 ' + '1 ' * 2100]]},
               {**GOOD, 'href': 'https://example.com'}, {**GOOD, 'stroke': 100}, {**GOOD, 'grid': 2}, {**GOOD, 'name': 'a\x00b'},
               {**GOOD, 'check': {'status': 'great'}}, 'M0 0']
        for value in bad:
            with self.subTest(value=str(value)[:60]), self.assertRaises(ValueError):
                icons.validate_glyph(value)

    def test_svg_shapes_become_paths_and_anything_else_is_refused(self):
        ops = icons.convert_body('<g fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><rect x="2" y="4" width="20" height="16" rx="2"/>'
                                 '<line x1="2" y1="12" x2="22" y2="12"/><polyline points="4 4 8 8 12 4"/><polygon points="1,1 5,1 3,4"/></g><path fill-rule="evenodd" d="M0 0h4v4H0z"/>')
        self.assertEqual([mode for mode, _ in ops], ['s', 's', 's', 's', 's', 'e'])
        self.assertTrue(ops[0][1].startswith('M9 12a3 3 0 1 0 6 0')); self.assertTrue(ops[3][1].startswith('M4 4L8 8')); self.assertTrue(ops[4][1].endswith('z'))
        for body in ('<text x="1">A</text>', '<image href="x.png"/>', '<path d="M0 0" transform="rotate(4)"/>', '<path d="M0 0" style="fill:red"/>',
                     '<path d="M0 0" fill="#ff0000"/>', '<use href="#a"/>', '<!DOCTYPE x><path d="M0 0"/>', '<path d="M0 0&amp;"/>', '<path d="javascript:x"/>', '<g>'):
            with self.subTest(body=body):
                self.assertIsNone(icons.convert_body(body))


class CatalogueTests(Offline):
    def test_the_vendored_sets_are_the_recorded_ones_with_their_licences(self):
        folder = ROOT / 'static' / 'vendor' / 'icons'
        record = json.loads((folder / 'VENDORED.json').read_text(encoding='utf-8'))
        self.assertEqual([row['license']['spdx'] for row in record['sets']], ['ISC', 'MIT'])
        for row in record['sets']:
            path = folder / row['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['sha256'])
            data = json.loads(gzip.decompress(path.read_bytes()))
            self.assertEqual((len(data['icons']), data['license']['spdx']), (row['icons'], row['license']['spdx']))
            for name, ops in list(data['icons'].items())[:200]:
                icons.validate_glyph({'ops': ops})                                  # every icon is plain path data
        for name in ('LICENSE-lucide.txt', 'LICENSE-tabler.txt', 'LICENSES.txt'):
            self.assertGreater((folder / name).stat().st_size, 200)
        self.assertIn('ISC', (folder / 'LICENSE-lucide.txt').read_text(encoding='utf-8'))

    def test_the_photoshop_writer_is_the_recorded_one_with_its_licences(self):
        folder = ROOT / 'static' / 'vendor' / 'ag-psd'
        record = json.loads((folder / 'VENDORED.json').read_text(encoding='utf-8'))
        writer = record['packages'][0]
        self.assertEqual((writer['package'], writer['license']), ('ag-psd', 'MIT'))
        self.assertEqual(hashlib.sha256((folder / writer['file']).read_bytes()).hexdigest(), writer['sha256'])
        self.assertEqual({row['package'] for row in record['packages'][1:]}, {'pako', 'base64-js'})      # bundled inside it
        for name in ('ag-psd', 'pako', 'base64-js'):
            self.assertIn('Permission is hereby granted', (folder / ('LICENSE-%s.txt' % name)).read_text(encoding='utf-8'))

    def test_a_word_finds_the_literal_picture(self):
        for query, wanted in (('扳手', 'wrench'), ('扳手尺寸', 'wrench'), ('尺寸', 'ruler'), ('承重', 'weight'), ('锁', 'lock'), ('padlock', 'lock'),
                              ('weights', 'weight'), ('drawers', 'archive'), ('电池续航', 'battery'), ('wrench', 'wrench')):
            with self.subTest(query=query):
                rows = icons.search(query, 5)
                self.assertTrue(rows, query); self.assertIn(wanted, rows[0]['name'])
                self.assertTrue(all(row['license'] in ('ISC', 'MIT') for row in rows))
        self.assertEqual(icons.search('', 5), []); self.assertEqual(icons.search('？？', 5), [])

    def test_a_name_that_shows_something_else_is_never_offered(self):
        for query in ('nut', '螺母', 'socket', 'bolt'):
            with self.subTest(query=query):
                self.assertNotIn(('lucide', 'nut'), {(row['prefix'], row['name']) for row in icons.search(query, 40)})     # Lucide's nut is a hazelnut
        for query in ('台面', 'worktop', 'table', 'workbench'):
            with self.subTest(query=query):
                found = {(row['prefix'], row['name']) for row in icons.search(query, 40)}
                self.assertEqual(icons.search(query, 1)[0]['name'], 'desk')
                self.assertFalse(found & {('lucide', 'table'), ('lucide', 'table-2'), ('tabler', 'table')}, 'a spreadsheet is not a worktop')

    def test_a_stored_glyph_is_looked_up_by_set_and_name(self):
        wrench = icons.glyph('lucide:wrench')
        self.assertEqual((wrench['source'], wrench['name'], wrench['license'], wrench['stroke'], wrench['grid']), ('lucide', 'wrench', 'ISC', 2, 24))
        self.assertIsNone(icons.glyph('lucide:no-such-icon')); self.assertIsNone(icons.glyph('evil:wrench')); self.assertIsNone(icons.glyph('wrench'))


class OnlineTests(Offline):
    SEARCH = {'icons': ['mdi:wrench', 'mdi:wrench-outline', 'ph:wrench', 'gpl:wrench', 'mdi:../../etc', 'iconoir:ruler'],
              'collections': {'mdi': {'license': {'spdx': 'Apache-2.0'}}, 'ph': {'license': {'spdx': 'MIT'}}, 'gpl': {'license': {'spdx': 'GPL-3.0'}},
                              'iconoir': {'license': {'spdx': 'CC-BY-4.0'}}}}
    ICONS = {'mdi': {'icons': {'wrench': {'body': '<path fill="currentColor" d="M22.7 19l-9.1-9.1"/>'},
                               'wrench-outline': {'body': '<path fill="none" stroke="currentColor" stroke-width="1.5" d="M2 2L20 20"/>'}}, 'height': 24},
             'ph': {'icons': {'wrench': {'body': '<image href="https://example.com/x.png"/>'}}, 'height': 256}}

    def answer(self, path, params):
        if path == '/search':
            return self.SEARCH
        prefix = path.strip('/').split('.')[0]
        self.assertIn(prefix, ('mdi', 'ph'), 'only freely licensed sets are fetched')
        return self.ICONS[prefix]

    def test_only_freely_licensed_sets_are_taken_and_only_their_plain_shapes(self):
        with patch.object(icons, '_get_json', side_effect=self.answer):
            rows = icons.search_online('wrench', 10)
        self.assertEqual([(row['id'], row['license'], row['source']) for row in rows],
                         [('mdi:wrench-outline', 'Apache-2.0', 'iconify:mdi'), ('mdi:wrench', 'Apache-2.0', 'iconify:mdi')])   # line icons first
        self.assertEqual((rows[0]['stroke'], rows[0]['ops']), (1.5, [['s', 'M2 2L20 20']]))
        for row in rows:
            icons.validate_glyph({key: row[key] for key in ('source', 'name', 'license', 'ops', 'stroke', 'grid')})

    def test_no_network_means_no_online_icons_and_no_error(self):
        with patch.object(icons, '_get_json', side_effect=urllib.error.URLError('offline')):
            self.assertEqual(icons.search_online('wrench'), [])
        with patch.object(icons, '_get_json', side_effect=ValueError('too large')):
            self.assertEqual(icons.search_online('wrench'), [])

    def test_the_online_search_is_on_unless_switched_off(self):
        import os
        environment = {key: value for key, value in os.environ.items() if key != 'AI_ICON_ONLINE'}
        for value, allowed in (('0', False), ('off', False), ('false', False), ('1', True), (None, True)):
            with self.subTest(value=value), patch('ai_config.read_env', return_value={} if value is None else {'AI_ICON_ONLINE': value}), \
                    patch.dict('os.environ', environment, clear=True):
                self.assertIs(ONLINE_ALLOWED(), allowed)


class AgentTests(Offline):
    def test_without_a_model_each_point_takes_the_best_local_match_and_shares_a_drawing_when_it_repeats(self):
        glyphs, chosen, notes = icons.resolve([('抽屉收纳', 'drawer'), ('扳手尺寸', 'wrench'), ('多个抽屉', 'drawer'), ('？', '')])
        self.assertEqual(chosen[0], chosen[2]); self.assertIsNone(chosen[3]); self.assertEqual(len(glyphs), 2)
        self.assertEqual(glyphs[chosen[1]]['name'], 'wrench'); self.assertEqual(notes, [])
        for value in glyphs.values():
            self.assertEqual(icons.validate_glyph(value), value)

    def test_a_wrong_keyword_still_offers_the_right_picture_and_the_same_words_share_one_icon(self):
        shown = []
        def pick(points, candidates):
            shown.extend([row['name'] for row in rows[:8]] for rows in candidates)
            return [next(index for index, row in enumerate(rows[:8]) if row['name'] == 'weight') for rows in candidates]
        with patch.object(icons, 'pick', side_effect=pick):
            glyphs, chosen, _ = icons.resolve([('台面承重', 'ruler'), ('台面承重', 'weight scale')])     # a figure and the specification it repeats
        self.assertEqual(len(shown), 1, 'the same words are looked up once')
        self.assertIn('ruler', shown[0]); self.assertIn('weight', shown[0])        # the model's keyword and the point's own words, side by side
        self.assertEqual(chosen[0], chosen[1]); self.assertEqual(glyphs[chosen[0]]['name'], 'weight')

    def test_without_a_model_to_look_the_points_own_words_decide(self):
        glyphs, chosen, _ = icons.resolve([('台面承重', 'ruler'), ('奇特部件', 'wrench')])
        self.assertEqual([glyphs[key]['name'] for key in chosen], ['weight', 'wrench'])      # known words win; unknown ones leave it to the keywords

    def test_which_word_names_the_picture(self):
        for query, wanted in (('台面承重', 'weight'), ('扳手尺寸', 'wrench'), ('7层抽屉', 'archive'), ('抽屉数量', 'archive'), ('不锈钢台面', 'desk'),
                              ('外形尺寸', 'ruler'), ('电池续航', 'battery'), ('扭矩', 'gauge'), ('Top load', 'weight'), ('Wrench sizes', 'wrench'),
                              ('Locking door', 'lock'), ('On casters', 'wheel'), ('Stainless steel worktop', 'desk'), ('Battery runtime', 'battery')):
            with self.subTest(query=query):
                self.assertIn(wanted, icons.search(query, 1)[0]['name'])

    def test_a_point_nothing_fits_is_drawn_from_its_own_words_and_left_out_when_the_drawing_fails(self):
        asked = []
        def draw(concept, attempts=2):
            asked.append(concept)
            return {**GOOD, 'name': concept, 'concept': concept, 'check': {'status': 'failed' if concept == '奇怪的部件' else 'passed', 'note': ''}}
        with patch.object(icons, 'draw', side_effect=draw):
            glyphs, chosen, notes = icons.resolve([('奇怪的部件', 'zzqx'), ('另一个部件', 'qqzx')])
        self.assertEqual(asked, ['奇怪的部件', '另一个部件'], 'drawn from what the point says')
        self.assertIsNone(chosen[0]); self.assertIsNotNone(chosen[1])
        self.assertIn('未通过核对，已留空', notes[0]); self.assertEqual(notes[1], '「另一个部件」的图标由 AI 绘制。')
        with patch.object(icons, 'draw', side_effect=draw):
            self.assertEqual(icons.resolve([('奇怪的部件', 'zzqx')], allow_draw=False)[1], [None])

    def test_the_model_picks_by_looking_at_the_candidates(self):
        asked = []
        def ask(config, system, facts, schema, image_path=None, max_tokens=2500):
            asked.append((system, facts, Path(image_path).exists()))
            return {'choices': [{'point': 1, 'candidate': 2}, {'point': 2, 'candidate': 0}]}
        with patch.object(icons, '_config', return_value={'model': 'vision'}), patch.object(icons, '_ask', side_effect=ask):
            picked = icons.pick(['抽屉收纳', '扳手'], [icons.search('drawer', 4), icons.search('wrench', 4)])
        self.assertEqual(picked, [1, None])                                        # the second candidate, and none for a point none of them shows
        system, facts, sheet = asked[0]
        self.assertIn('a wrench for a wrench size', system); self.assertTrue(sheet)
        self.assertEqual([len(point['candidates']) for point in facts['points']], [4, 4])

    def test_a_drawing_is_validated_checked_and_redrawn_once_when_it_fails(self):
        drawings = iter([{'plan': 'x', 'paths': [{'mode': 'stroke', 'd': 'not a path'}], 'note': 'x'},
                         {'plan': 'x', 'paths': [{'mode': 'stroke', 'd': 'M6 18 L18 6'}, {'mode': 'stroke', 'd': 'M15 4a3 3 0 1 0 5 5'}], 'note': 'a wrench'}])
        checks = []
        def ask(config, system, facts, schema, image_path=None, max_tokens=2500):
            if system == icons.DRAW_PROMPT:
                checks.append(facts.get('fix')); return next(drawings)
            return {'recognisable': True, 'clean': True, 'issues': [], 'note': '能认出是扳手。'}
        with patch.object(icons, '_config', side_effect=lambda image: {'model': 'vision' if image else 'text'}), patch.object(icons, '_ask', side_effect=ask):
            value = icons.draw('扳手')
        self.assertEqual((value['source'], value['license'], value['model'], value['check']['status']), ('drawn', 'own', 'text', 'passed'))
        self.assertEqual(value['ops'], [['s', 'M6 18 L18 6'], ['s', 'M15 4a3 3 0 1 0 5 5']])
        self.assertIsNone(checks[0]); self.assertIn('not valid SVG path syntax', checks[1][0])          # the second attempt is told what was wrong

    def test_a_malformed_answer_is_drawn_again_and_a_drawing_model_can_be_named(self):
        answers = iter([ValueError('AI 缺少必要字段（$：note、paths、plan），当前方案未修改。'),
                        {'plan': 'x', 'paths': [{'mode': 'stroke', 'd': 'M4 4 L20 20'}], 'note': 'x'}])
        models = []
        def ask(config, system, facts, schema, image_path=None, max_tokens=2500):
            if system == icons.DRAW_PROMPT:
                models.append(config['model']); answer = next(answers)
                if isinstance(answer, Exception): raise answer
                return answer
            return {'recognisable': True, 'clean': True, 'issues': [], 'note': '能认出。'}
        with patch.object(icons, '_config', return_value={'model': 'writer'}), patch.object(icons, '_ask', side_effect=ask), \
                patch.object(icons, 'drawing_model', return_value='painter'):
            value = icons.draw('扳手')
        self.assertEqual(models, ['painter', 'painter']); self.assertEqual((value['model'], value['check']['status']), ('painter', 'passed'))
        refused = ValueError('AI 服务的额度已用尽或账户欠费，请充值或联系管理员后重试。（HTTP 402）')
        with patch.object(icons, '_config', return_value={'model': 'writer'}), patch.object(icons, '_ask', side_effect=refused) as asked, \
                self.assertRaisesRegex(ValueError, 'HTTP 402'):
            icons.draw('扳手')
        self.assertEqual(asked.call_count, 1, 'a refusal is not asked twice')
        with patch('ai_config.read_env', return_value={'AI_ICON_MODEL': ' gpt-6.1-sol '}):
            self.assertEqual(icons.drawing_model(), 'gpt-6.1-sol')
        with patch('ai_config.read_env', return_value={}), patch.dict('os.environ', {}, clear=True):
            self.assertEqual(icons.drawing_model(), '')

    def test_a_drawing_that_never_passes_its_check_is_still_offered_and_marked(self):
        def ask(config, system, facts, schema, image_path=None, max_tokens=2500):
            if system == icons.DRAW_PROMPT:
                return {'plan': 'x', 'paths': [{'mode': 'stroke', 'd': 'M4 4 L20 20'}], 'note': 'x'}
            return {'recognisable': False, 'clean': True, 'issues': ['looks like a stick'], 'note': '看不出是扳手。'}
        with patch.object(icons, '_config', return_value={'model': 'm'}), patch.object(icons, '_ask', side_effect=ask) as asked:
            value = icons.draw('扳手')
        self.assertEqual(value['check'], {'status': 'failed', 'note': '看不出是扳手。'}); self.assertEqual(asked.call_count, 4)
        with self.assertRaisesRegex(ValueError, '未配置 AI'):
            icons.draw('扳手')
        with patch.object(icons, '_config', return_value={'model': 'm'}), self.assertRaisesRegex(ValueError, '请写出'):
            icons.draw('   ')

    def test_a_glyph_is_drawn_into_pixels_with_holes_where_its_shapes_overlap(self):
        line = icons.rasterise({'ops': [['s', 'M2 12 L22 12']], 'stroke': 2, 'grid': 24}, 48)
        self.assertLess(line.getpixel((24, 24))[0], 80); self.assertEqual(line.getpixel((24, 4)), (255, 255, 255))
        ring = icons.rasterise({'ops': [['e', 'M2 2h20v20H2z M8 8h8v8H8z']], 'stroke': 2, 'grid': 24}, 48)
        self.assertLess(ring.getpixel((8, 8))[0], 80); self.assertEqual(ring.getpixel((24, 24)), (255, 255, 255))      # even-odd: the middle is a hole


class RouteTests(Offline):
    def setUp(self):
        super().setUp()
        import app as web
        self.client = web.app.test_client()

    def test_icons_are_searched_and_online_results_come_only_when_asked_and_allowed(self):
        found = self.client.get('/api/icons?q=%E6%89%B3%E6%89%8B')                  # 扳手
        body = found.get_json()
        self.assertEqual(found.status_code, 200); self.assertIn('wrench', body['icons'][0]['name'])
        self.assertEqual((body['online'], body['online_allowed']), ([], False))
        self.assertEqual(set(body['icons'][0]), {'id', 'source', 'name', 'license', 'ops', 'stroke', 'grid'})
        with patch.object(icons, 'online_allowed', return_value=True), patch.object(icons, 'search_online', return_value=[]) as online:
            self.client.get('/api/icons?q=wrench'); self.assertFalse(online.called)
            self.client.get('/api/icons?q=wrench&online=1'); self.assertTrue(online.called)
        for query in ('', '%20', 'x' * 61):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/icons?q=' + query).status_code, 400)

    def test_a_drawing_is_asked_for_by_name_and_refused_without_a_model(self):
        refused = self.client.post('/api/icons/draw', json={'concept': '扳手'})
        self.assertEqual(refused.status_code, 400); self.assertIn('未配置 AI', refused.get_json()['error'])
        for body in ({}, {'concept': ''}, {'concept': 'x' * 81}, {'concept': 3}, []):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/icons/draw', json=body).status_code, 400)
        with patch.object(icons, 'draw', return_value=GOOD):
            self.assertEqual(self.client.post('/api/icons/draw', json={'concept': '扳手'}).get_json()['glyph'], GOOD)


if __name__ == '__main__':
    unittest.main()

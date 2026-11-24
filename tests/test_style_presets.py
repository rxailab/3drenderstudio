"""The style presets as compositions: each preset's layout recipe (brand_kit.PRESETS[id]['layout']) puts its own versions of
a page first and keeps the builder's behind them, names only what the templates draw, and reads on its own ground. No browser."""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brand_kit
import brandkit_assets
import listing_draw as draw
import listing_jobs
import listing_kinds
from listing_theme import _palette

TEMPLATES = ROOT / 'brandkit' / 'templates'
# The knobs the parallel template work adds (the shared contract): not in this branch's template source yet.
CONTRACT = {'T12': {'mode': {'split', 'center'}, 'split_side': {'left', 'right'}, 'split_ratio': None, 'center_type': {'below', 'behind'}},
            'T01': {'callout_style': {'leaders', 'numbered', 'column'}, 'label_side': {'left', 'right'}},
            'T03': {'rail_side': {'left', 'right'}}, 'T02': {'panel_side': {'left', 'right'}},
            'T06': {'ground_style': {'plain', 'blueprint'}, 'title_block': None}, 'T05': {'header': {'band', 'light'}}}
NEW = ('split', 'blueprint', 'workshop', 'editorial', 'catalogue')


def source(kind):
    return (TEMPLATES / (kind + '.html')).read_text(encoding='utf-8')


class RecipeTests(unittest.TestCase):
    jobs = [{'mode': 'overlay', 'headline': 'A'}, {'mode': 'solid', 'headline': 'A'}, {'mode': 'solid', 'headline': 'A', 'compact': True}]

    def test_preferred_versions_come_first_in_entry_order_and_the_builders_follow(self):
        recipe = {'T12': {'prefer': [{'from': 'solid', 'set': {'mode': 'split'}}, {'from': '*', 'set': {'two_tone': False}}]}}
        out = listing_jobs.preset_jobs('T12', self.jobs, recipe)
        self.assertEqual([(row['mode'], row.get('compact', False), row.get('two_tone', True)) for row in out],
                         [('split', False, True), ('split', True, True),
                          ('overlay', False, False), ('solid', False, False), ('solid', True, False),
                          ('overlay', False, True), ('solid', False, True), ('solid', True, True)])
        self.assertEqual(out[-3:], self.jobs)                                     # the builder's own list, untouched, as the fallback

    def test_all_knobs_go_on_every_version_and_a_preferred_set_wins_over_them(self):
        recipe = {'T01': {'prefer': [{'from': '*', 'set': {'frame': 'band'}}], 'all': {'frame': 'plain', 'label_side': 'left'}}}
        out = listing_jobs.preset_jobs('T01', [{'mode': 'band'}], recipe)
        self.assertEqual(out, [{'mode': 'band', 'frame': 'band', 'label_side': 'left'}, {'mode': 'band', 'frame': 'plain', 'label_side': 'left'}])

    def test_a_version_made_twice_is_kept_once(self):
        recipe = {'T08': {'prefer': [{'from': 'columns', 'set': {}}]}}             # a reorder: the columns copy equals the builder's own
        jobs = [{'mode': 'stagger'}, {'mode': 'columns'}]
        self.assertEqual(listing_jobs.preset_jobs('T08', jobs, recipe), [{'mode': 'columns'}, {'mode': 'stagger'}])
        knolled = listing_jobs.preset_jobs('T05', [{'items': 1}, {'items': 1, 'mode': 'knolling'}], {'T05': {'prefer': [{'from': '*', 'set': {'mode': 'knolling'}}]}})
        self.assertEqual(knolled, [{'items': 1, 'mode': 'knolling'}, {'items': 1}])

    def test_a_version_without_a_mode_is_matched_by_the_mode_its_template_draws(self):
        out = listing_jobs.preset_jobs('T04', [{'specs': []}], {'T04': {'prefer': [{'from': 'overlay', 'set': {'mode': 'glass'}}]}})
        self.assertEqual(out[0]['mode'], 'glass')

    def test_the_default_preset_has_no_recipe_and_its_jobs_are_as_before(self):
        self.assertNotIn('layout', brand_kit.PRESETS['frame']); self.assertNotIn('fonts', brand_kit.PRESETS['frame'])
        self.assertIs(listing_jobs.preset_jobs('T12', self.jobs, None), self.jobs)
        style = brand_kit.page_style(brand_kit.Brand({}))
        self.assertEqual(style, {'preset': 'frame', 'ground': 'light', 'headline': None, 'label': None, 'card': None, 'chip': None,
                                 'leader': None, 'frame': {'header': 'none', 'footer': 'none'}})
        self.assertEqual(brand_kit.fonts_of(brand_kit.Brand({}), 'en'), brandkit_assets.DEFAULT_PAIRING)

    def test_jobs_for_applies_the_recipe_before_the_crop_variants_and_frame_changes_nothing(self):
        part = {'detail': 'd1', 'focus': [.5, .5]}
        built = [{'template_id': 'T03', 'mode': 'split', 'headline': 'H', 'details': [part], 'assets': {'d1': {'_parts': [[0, 0, 1, 1]]}}}]
        ctx = {'language': 'en', 'copy': {}}
        with patch.object(listing_jobs, '_jobs_for', side_effect=lambda kind, context: [dict(row) for row in built]):
            plain = listing_jobs.jobs_for('T03', ctx)
            framed = listing_jobs.jobs_for('T03', {**ctx, 'brand': brand_kit.Brand({'preset': 'frame'})})
            clean = listing_jobs.jobs_for('T03', {**ctx, 'brand': brand_kit.Brand({'preset': 'clean'})})
        self.assertEqual(json.dumps(framed, sort_keys=True), json.dumps(plain, sort_keys=True))
        self.assertEqual(len(plain), 1 + len(listing_jobs.CROP_SHIFTS))
        # each version keeps its crop variants right behind it, in a group of its own: the ring first, then the split
        self.assertEqual([(row['mode'], row['_group']) for row in clean], [('ring', 0)] * 5 + [('split', 1)] * 5)
        self.assertEqual(len({json.dumps(row['details'], sort_keys=True) for row in clean[:5]}), 5)


class PresetTests(unittest.TestCase):
    def test_there_are_ten_presets_each_named_and_noted_by_its_composition(self):
        rows = brand_kit.presets()
        self.assertEqual([row['id'] for row in rows], ['frame', 'flat', 'tech', 'clean', 'promo', *NEW])
        for row in rows:
            self.assertTrue(row['label']['zh'] and row['label']['en'], row)
            self.assertGreater(len(row['note']['zh']), 12, row); self.assertGreater(len(row['note']['en']), 40, row)
        self.assertEqual(len({row['note']['zh'] for row in rows}), 10)
        for key, value in brand_kit.PRESETS.items():
            if key != 'frame':
                self.assertTrue(value.get('layout'), key)                    # every look but the default lays its pages out its own way

    def test_every_recipe_names_only_modes_and_knobs_the_templates_draw(self):
        for preset, value in brand_kit.PRESETS.items():
            for kind, recipe in (value.get('layout') or {}).items():
                self.assertIn(kind, listing_kinds.AUTO, (preset, kind))
                text = source(kind); contract = CONTRACT.get(kind, {})
                self.assertLessEqual(set(recipe), {'prefer', 'all'}, (preset, kind))
                knobs = dict(recipe.get('all') or {})
                for entry in recipe.get('prefer') or []:
                    self.assertTrue(entry['from'] == '*' or "'%s'" % entry['from'] in text, (preset, kind, entry))
                    knobs.update(entry.get('set') or {})
                    for key, setting in (entry.get('set') or {}).items():
                        self.check(preset, kind, key, setting, text, contract)
                for key, setting in (recipe.get('all') or {}).items():
                    self.check(preset, kind, key, setting, text, contract)

    def check(self, preset, kind, key, setting, text, contract):
        where = (preset, kind, key, setting)
        self.assertTrue(key in contract or re.search(r'job\.%s\b' % re.escape(key), text), where)
        if isinstance(setting, str):
            self.assertTrue("'%s'" % setting in text or setting in (contract.get(key) or ()), where)
        if key == 'split_ratio':
            self.assertTrue(.38 <= setting <= .55, where)

    def test_the_recipes_use_the_contracts_new_compositions(self):
        sets = [(kind, entry.get('set') or {}) for value in brand_kit.PRESETS.values() for kind, recipe in (value.get('layout') or {}).items()
                for entry in recipe.get('prefer') or []]
        modes = {(kind, row.get('mode')) for kind, row in sets}
        self.assertTrue({('T12', 'split'), ('T12', 'center'), ('T03', 'ring'), ('T08', 'bento'), ('T04', 'glass'), ('T04', 'band')} <= modes, modes)
        styles = {row.get('callout_style') for kind, row in sets if kind == 'T01'}
        self.assertEqual(styles - {None}, {'numbered', 'column'})
        self.assertTrue(brand_kit.PRESETS['blueprint']['layout']['T06']['all']['title_block'])

    def test_the_presets_pair_their_own_type_while_the_brand_keeps_the_default(self):
        editorial = brand_kit.Brand({'preset': 'editorial'})
        self.assertEqual(brand_kit.fonts_of(editorial, 'en'), 'elegant')
        self.assertEqual(brand_kit.fonts_of(editorial, 'zh'), brandkit_assets.DEFAULT_PAIRING)     # its Chinese face is a download
        self.assertEqual(brand_kit.fonts_of(brand_kit.Brand({'preset': 'blueprint'}), 'zh'), 'impact')
        self.assertEqual(brand_kit.fonts_of(brand_kit.Brand({'preset': 'editorial', 'fonts': 'tech'}), 'en'), 'tech')
        for value in brand_kit.PRESETS.values():
            self.assertTrue(value.get('fonts') is None or value['fonts'] in brandkit_assets.PAIRINGS)

    def test_every_preset_reads_on_its_own_ground_on_every_page(self):
        for preset in brand_kit.PRESETS:
            for accent in ('#d0612a', '#1f5fd1', '#f5b800', '#111111', '#ffffff'):
                brand = brand_kit.Brand({'preset': preset, 'accent': accent})
                for kind in listing_kinds.AUTO:
                    with self.subTest(preset=preset, accent=accent, kind=kind):
                        palette = _palette(kind, brand); ink = palette.get('text') or palette.get('ink')
                        # headline-sized type on a brand-colour ground needs 3:1, everything else 4.5:1
                        self.assertGreaterEqual(draw.contrast(ink, palette['bg']), 3 if brand.tokens['ground'] == 'accent' else 4.5)
                        self.assertGreaterEqual(draw.contrast(palette['accent'], palette['bg']), 2.6)
                        self.assertGreaterEqual(draw.contrast(palette['accent-ink'], palette['accent']), 4.5)
        self.assertEqual(_palette('T01', brand_kit.Brand({'preset': 'blueprint'}))['bg'], brand_kit.GROUNDS['blueprint'])
        self.assertEqual(_palette('T01', brand_kit.Brand({'preset': 'editorial'}))['bg'], brand_kit.GROUNDS['paper'])

    def test_the_blueprint_grid_is_a_texture_the_runtime_draws_and_the_brand_panel_offers(self):
        runtime = (ROOT / 'brandkit' / 'engine' / 'runtime.js').read_text(encoding='utf-8')
        self.assertIn("'drafting'", runtime); self.assertIn('style.texture', runtime)
        self.assertIn('drafting', brand_kit.TEXTURES)
        self.assertEqual(brand_kit.validate({'texture': 'drafting'})['texture'], 'drafting')


if __name__ == '__main__':
    unittest.main()

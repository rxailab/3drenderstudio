"""The thumbnails the listing set picker puts on its buttons (static/listing-thumbs/, made by scripts/make_listing_thumbs.py).

The UI names them by preset and by page kind, so a preset or a buildable kind without its picture would show a broken
button: the files are checked against brand_kit.PRESETS and listing_kinds.AUTO, not against a list kept here."""
import importlib.util
import unittest
from pathlib import Path

from PIL import Image

import brand_kit
import listing_kinds

ROOT = Path(__file__).resolve().parents[1]
THUMBS = ROOT / 'static' / 'listing-thumbs'
CONTRACT_PAGES = ('T12', 'T01', 'T03', 'T13', 'T02', 'T04', 'T08', 'T06', 'T05')
MAX_BYTES = 80 * 1024


def _script():
    spec = importlib.util.spec_from_file_location('make_listing_thumbs', ROOT / 'scripts' / 'make_listing_thumbs.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ListingThumbs(unittest.TestCase):
    def names(self):
        return ['style-%s.jpg' % key for key in brand_kit.PRESETS] + ['page-%s.jpg' % kind for kind in listing_kinds.AUTO]

    def test_the_contract_covers_every_preset_and_every_page_the_set_can_build(self):
        self.assertEqual(tuple(listing_kinds.AUTO), CONTRACT_PAGES)
        self.assertEqual(set(brand_kit.PRESETS), {'frame', 'flat', 'tech', 'clean', 'promo'})

    def test_every_thumbnail_is_a_small_square_jpeg(self):
        for name in self.names():
            path = THUMBS / name
            self.assertTrue(path.is_file(), name)
            self.assertLess(path.stat().st_size, MAX_BYTES, name)
            with Image.open(path) as image:
                self.assertEqual((image.format, image.size), ('JPEG', (480, 480)), name)

    def test_the_style_pages_differ_only_in_the_preset(self):
        script = _script()
        job = {'template_id': 'T01', 'palette': {'accent': '#b43232'}, 'headline': 'Same words'}
        styled = {key: script.styled(job, key, '#1f5fd1') for key in brand_kit.PRESETS}
        self.assertEqual({row['style']['preset'] for row in styled.values()}, set(brand_kit.PRESETS))
        self.assertTrue(all(row['headline'] == 'Same words' for row in styled.values()))
        self.assertNotEqual(styled['flat']['palette']['bg'], styled['clean']['palette']['bg'])        # the ground follows the preset
        self.assertNotEqual(styled['tech']['palette']['bg'], styled['clean']['palette']['bg'])


if __name__ == '__main__':
    unittest.main()

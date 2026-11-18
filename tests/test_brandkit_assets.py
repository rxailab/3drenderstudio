"""Fonts and icons for the brand-kit templates: fetched when missing, checked before they are kept."""
import io
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brandkit_assets  # noqa: E402

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 4h16"/></svg>'


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.folder, True)
        for name, value in (('VENDOR', self.folder), ('FONTS', self.folder / 'fonts'), ('ICONS', self.folder / 'icons')):
            mocked = patch.object(brandkit_assets, name, value); mocked.start(); self.addCleanup(mocked.stop)
        brandkit_assets._checked.clear()

    def test_an_icon_is_fetched_once_checked_and_a_missing_name_is_remembered(self):
        asked = []
        def get(url, tries=3):
            asked.append(url)
            if url.endswith('/nope.svg'):
                raise urllib.error.HTTPError(url, 404, 'missing', {}, io.BytesIO())
            if url.endswith('/evil.svg'):
                return b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
            if url.endswith('/linked.svg'):
                return b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>'
            return SVG
        with patch.object(brandkit_assets, '_get', side_effect=get):
            path = brandkit_assets.ensure_icon('battery-4')
            self.assertEqual(path.read_bytes(), SVG); self.assertEqual(oct(path.stat().st_mode & 0o777), '0o644')
            brandkit_assets.ensure_icon('battery-4')                                       # kept: not asked again
            self.assertIsNone(brandkit_assets.ensure_icon('nope')); self.assertIsNone(brandkit_assets.ensure_icon('nope'))
            self.assertIsNone(brandkit_assets.ensure_icon('evil')); self.assertIsNone(brandkit_assets.ensure_icon('linked'))
            self.assertIsNone(brandkit_assets.ensure_icon('../../etc/passwd'))             # not an icon name: nothing fetched
        self.assertEqual(sum(url.endswith('/battery-4.svg') for url in asked), 1)
        self.assertEqual(sum(url.endswith('/nope.svg') for url in asked), 1)
        self.assertIn('<path', brandkit_assets.icon_markup('battery-4'))

    def test_only_real_fonts_are_kept_and_the_css_names_what_is_there(self):
        font = next((Path(p) for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',) if Path(p).is_file()), None)
        if font is None:
            self.skipTest('no system TrueType font to stand in')
        good = font.read_bytes()
        def get(url, tries=3):
            if 'archivo' in url and url.endswith('.ttf'):
                return good
            if url.endswith('.ttf'):
                return b'<html>not a font</html>'
            return b'licence text'
        with patch.object(brandkit_assets, '_get', side_effect=get):
            report = brandkit_assets.ensure_fonts('en')
        self.assertEqual(report['present'], ['Archivo-Variable.ttf'])
        self.assertIn('Inter-Variable.ttf', report['missing']); self.assertNotIn('NotoSansSC-Variable.ttf', report['missing'] + report['present'])
        css = (self.folder / 'fonts.css').read_text()
        self.assertIn("font-family:'Archivo'", css); self.assertNotIn('Inter', css)
        with patch.object(brandkit_assets, '_get', side_effect=urllib.error.URLError('offline')):
            self.assertIn('Inter-Variable.ttf', brandkit_assets.ensure_fonts('en')['missing'])     # offline: reported, not raised


if __name__ == '__main__':
    unittest.main()

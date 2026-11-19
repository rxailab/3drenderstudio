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
        for name, value in (('VENDOR', self.folder), ('FONTS', self.folder / 'fonts'), ('ICONS', self.folder / 'icons'), ('BUNDLED', self.folder / 'none')):
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
        self.assertEqual(sum(url.endswith('/nope.svg') for url in asked), len(brandkit_assets.ICON_MIRRORS))   # every mirror once, then remembered
        self.assertIn('<path', brandkit_assets.icon_markup('battery-4'))

    def test_only_real_fonts_are_kept_and_the_css_names_what_is_there(self):
        font = next((Path(p) for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',) if Path(p).is_file()), None)
        if font is None:
            self.skipTest('no system TrueType font to stand in')
        good = font.read_bytes()
        def get(url, tries=3):
            if url.startswith('https://cdn.jsdelivr.net/gh/'):
                raise urllib.error.URLError('blocked')                        # the first mirror is unreachable: the next one answers
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
        self.assertIn("url('fonts/Archivo-Variable.ttf')", css)
        with patch.object(brandkit_assets, '_get', side_effect=urllib.error.URLError('offline')):
            self.assertIn('Inter-Variable.ttf', brandkit_assets.ensure_fonts('en')['missing'])     # offline: reported, not raised


if __name__ == '__main__':
    unittest.main()


class LibraryTests(unittest.TestCase):
    """The font library: every pairing names real families, and a job carries what the page needs."""

    def test_pairings_name_known_families_with_licences(self):
        files = [face['file'] for family in brandkit_assets.FAMILIES.values() for face in family['files']]
        self.assertEqual(len(files), len(set(files)))
        self.assertIn(brandkit_assets.DEFAULT_PAIRING, brandkit_assets.PAIRINGS)
        for row in brandkit_assets.PAIRINGS.values():
            for role in brandkit_assets.ROLES:
                self.assertIn(row[role], brandkit_assets.FAMILIES, (row['id'], role))
            self.assertEqual(brandkit_assets.FAMILIES[row['cjk']]['script'], 'cjk')
            self.assertEqual(brandkit_assets.FAMILIES[row['display']]['script'], 'latin')
            self.assertTrue(row['label']['zh'] and row['label']['en'])
        for family in brandkit_assets.FAMILIES.values():
            self.assertTrue(family['licence'].endswith('/OFL.txt'))
            for face in family['files']:
                self.assertRegex(face['css'], r'font-weight:\d+')

    def test_the_default_pairing_keeps_the_original_faces(self):
        self.assertEqual({face[0] for face in brandkit_assets.FACES}, {'Archivo', 'Barlow Condensed', 'Inter', 'Noto Sans SC'})
        self.assertEqual([face[4] for face in brandkit_assets.FACES if face[0] == 'Noto Sans SC'], [('zh',)])

    def test_job_fonts_give_stacks_with_fallbacks_and_faces_to_check(self):
        en = brandkit_assets.job_fonts('poster', 'en')
        self.assertEqual(en['pairing'], 'poster')
        self.assertTrue(en['stacks']['display'].startswith('"Anton",')); self.assertIn('"Archivo"', en['stacks']['display'])
        self.assertNotIn('ZCOOL QingKe HuangYou', [row[0] for row in en['probe']])        # an English page does not wait for the Chinese face
        zh = brandkit_assets.job_fonts('poster', 'zh')
        self.assertIn('ZCOOL QingKe HuangYou', [row[0] for row in zh['probe']])
        self.assertTrue(zh['stacks']['cjk'].startswith('"Inter","ZCOOL QingKe HuangYou"'))   # Latin letters in the text face
        self.assertTrue(brandkit_assets.job_fonts('industrial', 'zh')['stacks']['cjk'].startswith('"Noto Sans SC"'))
        self.assertEqual(brandkit_assets.job_fonts('nonsense')['pairing'], brandkit_assets.DEFAULT_PAIRING)

    def test_a_pairing_is_fetched_by_its_own_files_and_declared_for_the_page(self):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        for name, value in (('VENDOR', folder), ('FONTS', folder / 'fonts'), ('BUNDLED', folder / 'none')):
            mocked = patch.object(brandkit_assets, name, value); mocked.start(); self.addCleanup(mocked.stop)
        brandkit_assets._checked.clear(); self.addCleanup(brandkit_assets._checked.clear)
        font = (ROOT / 'brandkit' / 'fonts' / 'Inter-Variable.ttf').read_bytes()
        asked = []
        def get(url, tries=3):
            asked.append(url)
            return font if url.endswith('.ttf') else b'licence'
        with patch.object(brandkit_assets, '_get', side_effect=get):
            report = brandkit_assets.ensure_fonts('en', pairing='impact')
        self.assertEqual(report['missing'], [])
        self.assertTrue(any('oswald/Oswald' in url for url in asked)); self.assertFalse(any('notosanssc' in url for url in asked))
        self.assertTrue((folder / 'fonts' / 'Oswald-Variable.ttf').is_file()); self.assertTrue((folder / 'fonts' / 'Oswald-OFL.txt').is_file())
        css = brandkit_assets.font_face_css('en', 'impact', url=lambda path: 'x/' + path.name)
        self.assertIn("font-family:'Oswald';src:url('x/Oswald-Variable.ttf')", css)
        self.assertIn('Oswald', (folder / 'fonts.css').read_text())

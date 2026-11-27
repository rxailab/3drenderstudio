"""The interactive catalog and the offline renderer must resolve the same preset."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import keyshot_catalog as library


class KeyShotCatalogTests(unittest.TestCase):
    def test_packaged_inventory_and_urls(self):
        result = library.catalog()
        rows = result['materials']
        self.assertEqual(result['total'], 953)
        self.assertEqual(len(result['categories']), 25)
        self.assertEqual(len({row['id'] for row in rows}), result['total'])
        for row in rows:
            ident = row['id']
            self.assertIsNotNone(re.fullmatch(r'ks_[0-9a-f]{20}', ident))
            self.assertEqual(row['thumbnail_url'], f'/api/material-library/{ident}/thumbnail')
            thumbnail = library.thumbnail_path(ident)
            self.assertEqual(bool(row.get('thumbnail_is_swatch')), thumbnail is None)
            spec = library.render_spec(ident)
            public = row['pbr']
            self.assertEqual({k: v for k, v in spec.items() if k != 'textures'},
                             {k: v for k, v in public.items() if k != 'textures'})
            self.assertEqual(spec['textures'].keys(), public['textures'].keys())
            for channel, filename in spec['textures'].items():
                viewport = library.texture_path(ident, channel)
                full = Path(filename)
                self.assertTrue(viewport.is_file() and full.is_file())
                self.assertEqual(viewport.relative_to(library.ROOT).parts[1:], full.relative_to(library.ROOT).parts[1:])
                self.assertEqual(public['textures'][channel], f'/api/material-library/{ident}/textures/{channel}')

    def test_only_manifest_ids_and_channels_select_files(self):
        for invalid in ('../app.py', '__proto__', {}, None):
            with self.assertRaises(ValueError):
                library.thumbnail_path(invalid)
        row = library.catalog()['materials'][0]
        with self.assertRaises(ValueError):
            library.texture_path(row['id'], '../../catalog.json')

    def test_render_overrides_cannot_modify_catalog(self):
        row = next(item for item in library.catalog()['materials'] if item['name'] == 'Walnut')
        spec = library.render_spec(row['id'])
        channels = set(spec['textures'])
        spec['color'][0] = -42
        spec['textures'].clear()
        self.assertNotEqual(library.render_spec(row['id'])['color'][0], -42)
        self.assertEqual(channels, set(library.render_spec(row['id'])['textures']))

    def test_catalog_only_install_and_partial_image_install(self):
        from PIL import Image
        from app import app

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(library.ROOT/'catalog.json',root/'catalog.json')
            with patch.object(library,'ROOT',root), app.test_client() as client:
                rows = client.get('/api/material-library').get_json()['materials']
                self.assertEqual(len(rows),953)
                self.assertTrue(all(not row['pbr']['textures'] and row['thumbnail_is_swatch'] for row in rows))
                oak = next(row for row in rows if row['name']=='Dark Stained Oak')
                self.assertEqual(library.render_spec(oak['id'])['procedural'],oak['pbr']['procedural'])
                walnut = next(row for row in rows if row['name']=='Walnut')
                ident = walnut['id']
                self.assertTrue(walnut['missing_textures'])
                url = f'/api/material-library/{ident}/textures/base_color'
                self.assertEqual(client.get(url).status_code,404)
                swatch = client.get(walnut['thumbnail_url'])
                self.assertEqual(swatch.mimetype,'image/png')
                self.assertEqual(swatch.data[:8],b'\x89PNG\r\n\x1a\n')
                # A partial installation serves the same available file to both renderers.
                texture = root/'viewport-textures'/'Wood'/'walnut.jpg'
                texture.parent.mkdir(parents=True)
                Image.new('RGB',(2,2),'sienna').save(texture)
                self.assertEqual(library.texture_path(ident,'base_color'),texture)
                self.assertEqual(library.render_spec(ident)['textures']['base_color'],str(texture))
                self.assertEqual(client.get(url).status_code,200)

    def test_translucent_color_variants_keep_their_authored_diffuse(self):
        rows = {r['name']: r['pbr'] for r in library.catalog()['materials']}
        red = rows['Soft Shiny Red']['color']
        blue = rows['Soft Shiny Blue']['color']
        self.assertGreater(red[0], 5 * red[2])
        self.assertGreater(blue[2], 5 * blue[0])

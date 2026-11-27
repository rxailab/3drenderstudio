"""Exercise the built-in photographic studio on a checkout without KeyShot resources."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import studio_backdrops


ROOT = Path(__file__).resolve().parents[1]
BLENDER = os.environ.get('BLENDER_EXE') or shutil.which('blender')


class BundledStudioTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        shutil.copytree(studio_backdrops.DIRECTORY / 'builtin', self.directory / 'builtin')
        shutil.copyfile(studio_backdrops.DIRECTORY / 'lights.json', self.directory / 'lights.json')

    def test_default_catalog_and_local_upgrade_share_the_same_http_route(self):
        from app import app
        with patch.object(studio_backdrops, 'DIRECTORY', self.directory), app.test_client() as client:
            def fetch():
                with client.get('/static/studio-backdrops/catalog.json') as response:
                    self.assertEqual(response.status_code, 200)
                    return response.get_json()

            bundled = fetch()
            self.assertEqual(studio_backdrops.default_backdrop(), 'silver-studio')
            self.assertEqual(len(bundled['presets']), 12)
            self.assertIn('white', studio_backdrops.catalog())
            for preset in bundled['presets']:
                self.assertIsNone(preset.get('environmentMap'))
                for field in ('image', 'thumbnail', 'radianceMap'):
                    if preset.get(field):
                        self.assertTrue(preset[field].startswith('/static/studio-backdrops/builtin/'))
                        relative = preset[field].split('/static/studio-backdrops/', 1)[1]
                        self.assertTrue((self.directory / relative).is_file())
            installed = dict(bundled, presets=[*bundled['presets'], {'id': 'local-photo'}])
            (self.directory / 'catalog.json').write_text(json.dumps(installed), encoding='utf8')
            self.assertEqual(fetch(), installed)
            self.assertEqual(studio_backdrops.require_id('local-photo'), 'local-photo')

    @unittest.skipUnless(BLENDER, 'Set BLENDER_EXE to exercise Cycles')
    def test_cycles_renders_white_and_silver_without_local_resources(self):
        probe = self.directory / 'render_builtin.py'
        probe.write_text('''
import sys
from pathlib import Path
repo, location = sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0, repo)
import bpy
import numpy as np
import studio_backdrops
from blender import viewport_studio
studio_backdrops.DIRECTORY = viewport_studio.LIBRARY = Path(location)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 1
scene.render.resolution_percentage = 100
scene.render.resolution_x = scene.render.resolution_y = 64
cube = scene.objects['Cube']
points = np.asarray([list(p) for p in cube.bound_box])
for ident in ('white', 'silver-studio'):
    viewport_studio.apply(scene, [cube], points, {'exposure':0}, ident)
    assert scene[viewport_studio.OWNER] == ident
    scene.render.filepath = str(Path(location) / (ident+'.png'))
    bpy.ops.render.render(write_still=True)
''', encoding='utf8')
        completed = subprocess.run([BLENDER, '-b', '--factory-startup', '--disable-autoexec', '-t', '2',
                                    '--python-exit-code', '1', '--python', str(probe), '--',
                                    str(ROOT), str(self.directory)], capture_output=True, timeout=120)
        self.assertEqual(completed.returncode, 0, (completed.stdout + completed.stderr)[-2500:])
        from PIL import Image
        colors = []
        for name in ('white', 'silver-studio'):
            with Image.open(self.directory / (name+'.png')) as frame:
                self.assertEqual(frame.size, (64, 64))
                colors.append(frame.convert('RGB').getpixel((1, 1)))
        self.assertTrue(min(colors[0]) >= 250, colors)
        self.assertTrue(80 < min(colors[1]) < 245, colors)

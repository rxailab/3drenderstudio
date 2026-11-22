"""A stainless worktop on white: it must stay visible.

Real Blender, the worker as the app runs it. A red box with a polished steel slab on top is rendered as the white main
image from a low camera (where the slab mirrors the card behind the set) and from a high one (where it mirrors the lamp
overhead). Both lamps used to be several times brighter than white in a mirror: the whole top rendered as pure white and
could not be told from the background.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import planner

ROOT = Path(__file__).resolve().parents[1]
BLENDER = shutil.which('blender') or next((line.split('=', 1)[1].strip().strip('"') for line in
                                           (ROOT / '.env').read_text(encoding='utf-8').splitlines() if line.startswith('BLENDER_PATH=')), None) \
    if (ROOT / '.env').is_file() else shutil.which('blender')
MAKE = '''
import bpy, sys
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
def box(name, size, at, colour, metallic, roughness):
    bpy.ops.mesh.primitive_cube_add(location=at); ob = bpy.context.object; ob.name = name; ob.scale = [s / 2 for s in size]
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    m = bpy.data.materials.new(name); m.use_nodes = True; b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*colour, 1); b.inputs['Metallic'].default_value = metallic; b.inputs['Roughness'].default_value = roughness
    ob.data.materials.append(m)
box('Body', (0.8, 0.45, 0.8), (0, 0, 0.4), (0.55, 0.03, 0.03), 0, 0.35)
box('Steel top', (0.9, 0.52, 0.03), (0, 0, 0.815), (0.78, 0.78, 0.78), 1, 0.22)
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format='GLB')
'''
SAFE = 1.05        # worker.MIRROR_SAFE: watts per square metre


@unittest.skipUnless(BLENDER and Path(BLENDER).is_file(), 'Blender is not installed')
class SteelTopOnWhiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp()); cls.model = cls.folder / 'cart.glb'
        script = cls.folder / 'make.py'; script.write_text(MAKE)
        subprocess.run([BLENDER, '-b', '--factory-startup', '--python', str(script), '--', str(cls.model)], capture_output=True, timeout=300)
        cls.results = {}
        for style, elevation in (('white', 18), ('white', 75), ('soft', 18)):
            cls.results[style, elevation] = cls.render(style, elevation)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    @classmethod
    def render(cls, style, elevation):
        plan = planner.default_plan(); plan.update(look='showcase', visual_review=True)        # the worker then samples the product's surface
        shot = {'id': 'hero', 'label': 'hero', 'kind': 'full', 'style': style, 'projection': 'perspective', 'azimuth': 35, 'elevation': elevation, 'focal_length': 70}
        out = cls.folder / ('%s%d' % (style, elevation)); out.mkdir()
        (out / 'request.json').write_text(json.dumps({'action': 'render', 'model_path': str(cls.model), 'plan': plan, 'shot': shot, 'quality': 'preview',
                                                      'output_dir': str(out)}), encoding='utf-8')
        ran = subprocess.run([BLENDER, '-b', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python',
                              str(ROOT / 'blender' / 'worker.py'), '--', str(out / 'request.json')], capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if ran.returncode:
            raise AssertionError(ran.stdout[-2500:] + ran.stderr[-1500:])
        result = json.loads((out / 'result.json').read_text(encoding='utf-8')); result['png'] = out / (out.name + '.png')
        return result

    def steel(self, result):
        """The rendered colours of the samples that fell on the steel top."""
        with Image.open(result['png']) as image:
            image = image.convert('RGB'); width, height = image.size
            return [image.getpixel((min(width - 1, int(row['x'] * width)), min(height - 1, int(row['y'] * height))))
                    for row in result['product_samples'] if 'Steel' in str(row.get('object_name'))]

    def test_the_steel_top_is_bright_but_not_white_from_a_low_and_from_a_high_camera(self):
        for elevation in (18, 75):
            with self.subTest(elevation=elevation):
                pixels = self.steel(self.results['white', elevation])
                self.assertGreater(len(pixels), 5)
                white = sum(min(pixel) >= 250 for pixel in pixels) / len(pixels)
                self.assertLess(white, .34, pixels[:12])                   # was 1.0: every sample pure white
                mean = sum(sum(pixel) / 3 for pixel in pixels) / len(pixels)
                self.assertGreater(mean, 120)                              # still reads as bright metal, not as grey plastic

    def test_what_a_mirror_sees_of_the_card_and_the_top_lamp_is_held_below_white(self):
        lights = {lamp['name']: lamp for lamp in self.results['white', 18]['studio']['lights']}
        card = lights['Back reflection card']; mirror = lights['Top reflection mirror']; top = lights['Top reflection']
        self.assertLessEqual(card['energy'] / (card['size'] * card['size_y']), SAFE + 1e-6)
        self.assertLessEqual(mirror['energy'] / (mirror['size'] * mirror['size_y']), SAFE + 1e-6)
        self.assertTrue(mirror['mirror_only']); self.assertTrue(top['diffuse_only'])
        self.assertGreater(top['energy'], 2 * mirror['energy'])               # the light the top lamp gives is what it was
        self.assertFalse(mirror['casts_shadow'])

    def test_the_grey_set_keeps_its_lamps_as_they_were(self):
        """Its view transform rolls highlights off instead of clipping them: steel there is meant to mirror a bright card."""
        lights = {lamp['name']: lamp for lamp in self.results['soft', 18]['studio']['lights']}
        self.assertNotIn('Top reflection mirror', lights)
        card = lights['Back reflection card']
        self.assertGreater(card['energy'] / (card['size'] * card['size_y']), 3 * SAFE)


if __name__ == '__main__':
    unittest.main()

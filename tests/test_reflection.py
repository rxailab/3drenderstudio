"""The floor reflection: finished white and grey renders (in Blender's Python, which has numpy) and cut-outs on listing pages."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import listing_draw as draw
import listing_elements as el

PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[sys.argv.index('--') + 1])
import numpy
from blender import reflection
rgba = numpy.zeros((100, 80, 4), dtype=numpy.float32)
rgba[20:60, 20:60, :3] = (.8, .1, .1); rgba[20:60, 20:60, 3] = 1          # a red block standing on row 59
rgba[60:64, 15:65, 3] = .4                                                # its shadow, in the alpha only
white, report = reflection.onto_white(rgba)
grey, _ = reflection.onto_white(rgba, kind='soft')
print('RESULT' + json.dumps({'report': report, 'background': white[5, 5].tolist(), 'shadow': white[61, 17].tolist(),
    'product': white[40, 40].tolist(), 'below': white[62, 40].tolist(), 'far': white[95, 40].tolist(), 'beside': white[62, 70].tolist(),
    'grey_top': grey[0, 70].tolist(), 'grey_bottom': grey[99, 70].tolist(), 'empty': reflection.onto_white(numpy.zeros((10, 10, 4), dtype=numpy.float32))[1]}))
'''

MIRROR = r'''
import json, sys
sys.path.insert(0, sys.argv[sys.argv.index('--') + 1]); out = sys.argv[sys.argv.index('--') + 2]
import bpy, numpy
from blender import reflection
rgba = numpy.zeros((100, 80, 4), dtype=numpy.float32)
rgba[20:60, 20:60, :3] = (.8, .1, .1); rgba[20:60, 20:60, 3] = 1
rgba[60:64, 15:65, 3] = .6                                                # a dark contact shadow
mirror = numpy.zeros_like(rgba); mirror[60:100, 20:60, :3] = (.8, .1, .1); mirror[60:100, 20:60, 3] = 1
mirror[30:40, 20:60, :3] = (0, 0, 1); mirror[30:40, 20:60, 3] = 1          # behind the product: must not show
white, report = reflection.with_mirror(rgba, mirror)
# A real scene: a cube on the floor, seen from above; the pass renders its flipped copy and leaves the scene as it was.
scene = bpy.context.scene
for ob in list(scene.objects): bpy.data.objects.remove(ob, do_unlink=True)
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, .5)); cube = bpy.context.object
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -.002)); bpy.context.object.name = 'Studio floor'
bpy.ops.object.light_add(type='SUN', location=(0, 0, 5)); bpy.context.object.data.energy = 3
bpy.ops.object.camera_add(location=(0, -5, 2.2), rotation=(1.2, 0, 0)); scene.camera = bpy.context.object
scene.render.engine = 'CYCLES'; scene.cycles.samples = 4; scene.render.resolution_x = scene.render.resolution_y = 64
scene.render.image_settings.file_format = 'PNG'
before = sorted(ob.name for ob in scene.objects)
ok = reflection.mirror_pass(scene, out, 0.0, 4)
image = bpy.data.images.load(out); px = numpy.empty(64 * 64 * 4, dtype=numpy.float32); image.pixels.foreach_get(px)
alpha = px.reshape(64, 64, 4)[::-1][..., 3]
rows = numpy.nonzero(alpha.max(axis=1) > .5)[0]
print('RESULT' + json.dumps({'report': report, 'under': white[62, 40].tolist(), 'far': white[98, 40].tolist(), 'hidden': white[35, 40].tolist(),
    'ok': ok, 'restored': sorted(ob.name for ob in scene.objects) == before, 'visible': all(not ob.hide_render for ob in scene.objects),
    'film': scene.render.film_transparent, 'mirror_rows': [int(rows.min()), int(rows.max())] if rows.size else None}))
'''


def _blender(script_text, *extra):
    with tempfile.TemporaryDirectory() as temp:
        script = Path(temp) / 'probe.py'; script.write_text(script_text, encoding='utf-8')
        ran = subprocess.run([shutil.which('blender'), '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(script), '--', str(ROOT),
                              *[str(Path(temp) / name) for name in extra]], capture_output=True, text=True, timeout=300)
    assert ran.returncode == 0, ran.stdout[-1500:] + ran.stderr[-1500:]
    return json.loads(next(line for line in ran.stdout.splitlines() if line.startswith('RESULT'))[6:])


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: the render finish runs in its Python')
class FinishTests(unittest.TestCase):
    def test_a_rendered_reflection_shows_under_the_product_through_its_shadow_and_fades(self):
        found = _blender(MIRROR, 'mirror.png')
        self.assertEqual(found['report']['method'], 'mirror_pass')
        under = found['under']; self.assertGreater(under[0], under[1] + .05)         # red, right under it, though the shadow is there
        self.assertEqual([round(value, 3) for value in found['far']], [1.0, 1.0, 1.0])   # faded out further down
        self.assertEqual([round(value, 2) for value in found['hidden']], [.8, .1, .1])    # the product hides what is behind it
        self.assertTrue(found['ok'] and found['restored'] and found['visible']); self.assertFalse(found['film'])
        self.assertIsNotNone(found['mirror_rows']); self.assertGreater(found['mirror_rows'][1], 40)   # the flipped cube, low in the frame

    def test_the_product_is_mirrored_below_its_lowest_edge_and_fades(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / 'probe.py'; script.write_text(PROBE, encoding='utf-8')
            ran = subprocess.run([shutil.which('blender'), '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(script), '--', str(ROOT)],
                                 capture_output=True, text=True, timeout=300)
        self.assertEqual(ran.returncode, 0, ran.stdout[-1500:] + ran.stderr[-1500:])
        found = json.loads(next(line for line in ran.stdout.splitlines() if line.startswith('RESULT'))[6:])
        self.assertEqual(found['background'], [1.0, 1.0, 1.0])                      # white stays white
        self.assertEqual([round(value, 2) for value in found['product']], [.8, .1, .1])
        self.assertLess(found['shadow'][0], .7)                                      # the shadow is kept
        below = found['below']; self.assertGreater(below[0], below[1] + .1)          # red reflected right under the product
        self.assertEqual([round(value, 3) for value in found['far']], [1.0, 1.0, 1.0])    # and gone further down
        self.assertEqual(found['beside'][0], found['beside'][1])                     # nothing reflected beside it
        self.assertEqual((found['report']['reflected_columns'], found['report']['ground']), (40, 'white'))
        self.assertGreater(found['report']['reach_px'], 10)
        self.assertLess(found['grey_top'][0], found['grey_bottom'][0])               # the grey studio gradient, darker at the top
        self.assertEqual(found['empty']['reflected_columns'], 0)


class PageTests(unittest.TestCase):
    def test_a_cut_out_on_a_page_is_reflected_below_where_it_stands(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cut.png'
            cut = Image.new('RGBA', (200, 120), (0, 0, 0, 0)); ImageDraw.Draw(cut).rectangle((20, 10, 180, 110), fill=(30, 30, 200, 255)); cut.save(path)
            plain = el.cutout(path, 200, 200, 600, 360, shadow=False, reflection=False)
            mirrored = el.cutout(path, 200, 200, 600, 360, shadow=False)
            self.assertEqual(mirrored['reflection'], {'opacity': .3, 'length': .4})
            without = draw.render({'height': 1000, 'items': [plain]}, 500); with_it = draw.render({'height': 1000, 'items': [mirrored]}, 500)
        self.assertEqual(without.getpixel((250, 300)), (255, 255, 255))
        red, green, blue = with_it.getpixel((250, 268))
        self.assertTrue(blue > red + 20, (red, green, blue))                         # blue, faded, right under it
        self.assertEqual(with_it.getpixel((250, 360)), (255, 255, 255))              # faded out further down
        self.assertEqual(with_it.getpixel((250, 200)), without.getpixel((250, 200)))  # the product itself is unchanged


if __name__ == '__main__':
    unittest.main()

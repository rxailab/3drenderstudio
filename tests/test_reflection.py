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


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: the render finish runs in its Python')
class FinishTests(unittest.TestCase):
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

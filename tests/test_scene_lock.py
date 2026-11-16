"""The anchored product of a generated scene: placing it, and measuring whether the model kept it. No model is called."""
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import scene_lock

SIZE = (640, 640)


def cutout():
    """A product with detail: a red C-frame, a dark handle, a grey knob on a rod, small rollers. On transparent film."""
    image = Image.new('RGBA', (500, 400), (0, 0, 0, 0)); pen = ImageDraw.Draw(image)
    pen.rectangle((40, 170, 260, 215), fill=(25, 25, 28, 255))                      # handle
    pen.rectangle((260, 80, 300, 330), fill=(200, 35, 30, 255))                     # spine
    pen.rectangle((260, 80, 440, 125), fill=(200, 35, 30, 255))                     # upper arm
    pen.rectangle((260, 285, 440, 330), fill=(200, 35, 30, 255))                    # lower arm
    pen.rectangle((360, 20, 380, 80), fill=(170, 172, 176, 255))                    # rod
    pen.ellipse((335, 0, 405, 40), fill=(30, 30, 32, 255))                          # knob
    for x in (320, 370):
        pen.ellipse((x, 125, x + 34, 159), fill=(190, 192, 196, 255))               # rollers
    pen.ellipse((380, 250, 430, 300), fill=(205, 207, 210, 255))                    # wheel
    for x in range(60, 250, 24):
        pen.line((x, 172, x, 213), fill=(60, 60, 64, 255), width=3)                 # grip ribs
    return image


def scene(layer, change=None, shift=(0, 0), scale=1.0):
    """What a model that kept the product would return: the product on a busy, differently lit ground, optionally moved or altered."""
    ground = Image.new('RGB', layer.size)
    ground.putdata([((x * 3 + y) % 120 + 70, (y * 2) % 90 + 90, (x + y * 5) % 80 + 60) for y in range(layer.size[1]) for x in range(layer.size[0])])
    ground = ground.filter(ImageFilter.GaussianBlur(3))
    product = layer
    if scale != 1.0 or shift != (0, 0):
        bounds = layer.getchannel('A').getbbox(); centre = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        product = scene_lock._transformed(layer, scale, shift[0], shift[1], centre, Image.Resampling.BICUBIC)
    # The scene's light: a little darker and warmer than the studio's.
    lit = product.convert('RGB').point(lambda value: round(value * .85 + 8))
    ground.paste(lit, mask=product.getchannel('A'))
    if change:
        change(ImageDraw.Draw(ground), layer.getchannel('A').getbbox())
    return ground


class AnchorTests(unittest.TestCase):
    def test_the_product_is_placed_by_framing_inside_the_canvas(self):
        for framing, share in (('hands', .56), ('person', .36), (None, .5)):
            canvas, layer, box = scene_lock.anchor(cutout(), SIZE, framing)
            self.assertEqual((canvas.size, canvas.mode, layer.size, layer.mode), (SIZE, 'RGB', SIZE, 'RGBA'))
            self.assertAlmostEqual(max(box[2] - box[0], box[3] - box[1]) / 640, share, delta=.01)
            self.assertTrue(0 <= box[0] < box[2] <= 640 and 0 <= box[1] < box[3] <= 640, box)
            self.assertEqual(canvas.getpixel((2, 2)), scene_lock.GROUND)
        wide = scene_lock.anchor(cutout(), (800, 600))[2]
        self.assertTrue(0 <= wide[0] and wide[2] <= 800 and wide[3] <= 600)
        with self.assertRaises(ValueError):
            scene_lock.anchor(Image.new('RGBA', (50, 50), (0, 0, 0, 0)), SIZE)

    def test_the_mask_keeps_the_product_and_frees_everything_else(self):
        _, layer, box = scene_lock.anchor(cutout(), SIZE, 'hands')
        mask = scene_lock.mask(layer)
        self.assertEqual((mask.size, mask.mode), (SIZE, 'RGBA'))
        self.assertEqual(mask.getpixel((3, 3))[3], 0)                                 # transparent: to be painted
        inside = next((x, y) for y in range(box[1], box[3]) for x in range(box[0], box[2]) if layer.getpixel((x, y))[3] == 255)
        self.assertEqual(mask.getpixel(inside)[3], 255)                               # opaque: to be kept


class InspectTests(unittest.TestCase):
    def setUp(self):
        _, self.layer, self.box = scene_lock.anchor(cutout(), SIZE, 'hands')

    def test_a_kept_product_is_found_even_in_other_light_and_a_little_off(self):
        for shift, scale in (((0, 0), 1.0), ((9, -6), 1.0), ((0, 0), .96)):
            with self.subTest(shift=shift, scale=scale):
                report = scene_lock.inspect(self.layer, scene(self.layer, shift=shift, scale=scale))
                self.assertEqual(report['status'], 'kept', report)
                self.assertGreater(report['score'], scene_lock.MIN_SCORE)
                self.assertAlmostEqual(report['scale'], scale, delta=.025)
                self.assertAlmostEqual(report['shift'][0] * 640, shift[0], delta=6); self.assertAlmostEqual(report['shift'][1] * 640, shift[1], delta=6)
                self.assertLess(report['changed'], scene_lock.MAX_CHANGED)

    def test_a_product_drawn_somewhere_else_is_moved(self):
        far = scene(self.layer, shift=(150, 110))
        self.assertEqual(scene_lock.inspect(self.layer, far)['status'], 'moved')
        blank = Image.new('RGB', SIZE, (120, 130, 140))
        report = scene_lock.inspect(self.layer, blank)
        self.assertEqual((report['status'], report['covered'], report['regions']), ('moved', None, []))

    def test_a_redrawn_part_is_found_and_named(self):
        def simplify(pen, bounds):                                                    # the right half of the frame drawn as a plain block
            w = bounds[2] - bounds[0]; h = bounds[3] - bounds[1]
            pen.rectangle((bounds[0] + w * .55, bounds[1] + h * .25, bounds[0] + w * .97, bounds[1] + h * .85), fill=(185, 35, 30))
        report = scene_lock.inspect(self.layer, scene(self.layer, change=simplify))
        self.assertEqual(report['status'], 'changed', report)
        self.assertGreaterEqual(len(report['regions']), scene_lock.MIN_CHANGED)
        box = [value / 640 for value in self.box]
        self.assertTrue(any('right' in place for place in scene_lock.describe(report, box)), scene_lock.describe(report, box))

    def test_a_hand_in_front_is_covering_not_changing_and_too_much_of_it_hides_the_product(self):
        def hand(pen, bounds):                                                        # skin across the handle, reaching in from the edge
            pen.rectangle((0, bounds[1] + 90, bounds[0] + 150, bounds[1] + 160), fill=(222, 170, 140))
        report = scene_lock.inspect(self.layer, scene(self.layer, change=hand))
        self.assertEqual(report['status'], 'kept', report); self.assertGreater(report['covered'], .03)
        def arm(pen, bounds):                                                         # an arm across most of it; the knob and the arm ends still show
            pen.rectangle((0, bounds[1] + 50, bounds[0] + (bounds[2] - bounds[0]) * .8, bounds[3] + 20), fill=(222, 170, 140))
        # Mostly covered, the product is no longer shown: whether that reads as hidden or as not found, the picture fails.
        self.assertIn(scene_lock.inspect(self.layer, scene(self.layer, change=arm))['status'], ('hidden', 'moved'))

    def test_a_picture_of_another_size_is_compared_at_the_canvas_size(self):
        bigger = scene(self.layer).resize((1280, 1280), Image.Resampling.LANCZOS)
        self.assertEqual(scene_lock.inspect(self.layer, bigger)['status'], 'kept')

    def test_the_side_by_side_shows_the_render_beside_the_same_part_of_the_picture(self):
        picture = scene(self.layer)
        sheet = scene_lock.side_by_side(self.layer, picture, scene_lock.inspect(self.layer, picture), height=200)
        self.assertEqual(sheet.height, 200); self.assertGreater(sheet.width, 400)
        self.assertEqual(sheet.getpixel((2, 2)), (255, 255, 255))                     # the render on white, on the left


if __name__ == '__main__':
    unittest.main()

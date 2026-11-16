"""A running job says how far it is: sample counts from the render scripts, steps from analysis and background tasks."""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / 'tests'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))

import orientation
import scene_stylist

# What engine.execute and hero_optimizer look for in a render script's output.
PATTERN = r'(?:Sample|Rendered)\s+(\d+)\s*/\s*(\d+)'


class StepTests(unittest.TestCase):
    def test_the_line_the_scripts_print_is_the_one_the_engine_reads(self):
        for name in ('engine.py', 'hero_optimizer.py'):
            with self.subTest(file=name):
                self.assertIn(PATTERN, (ROOT / name).read_text(encoding='utf-8'))
        found = re.search(PATTERN, 'PROGRESS: Sample 12/64')
        self.assertEqual((found[1], found[2]), ('12', '64'))
        source = (ROOT / 'blender' / 'render_progress.py').read_text(encoding='utf-8')
        self.assertIn("'PROGRESS: Sample %s/%s'", source)
        # Every script that renders a picture the user waits for reports its samples.
        for name in ('worker.py', 'hero_scene.py'):
            with self.subTest(script=name):
                script = (ROOT / 'blender' / name).read_text(encoding='utf-8')
                self.assertLess(script.index('report_render_progress()'), script.index('bpy.ops.render.render(write_still=True)'))

    def test_a_scene_task_knows_how_far_each_stage_is(self):
        generated = ['识别产品与使用场景', '推荐目标人群与场景', '渲染产品参考角度 1 / 3', '渲染产品参考角度 2 / 3', '渲染产品参考角度 3 / 3',
                     '图像模型正在生成场景', '图像模型正在生成（已返回草图）', '核对产品与画面中的人物']
        built = ['识别产品与使用场景', '从 Poly Haven 搜集道具模型', '准备地面与背景', '布置场景']
        for stages in (generated, built):
            reached = [scene_stylist.stage_progress(stage) for stage in stages]
            self.assertEqual(reached, sorted(reached), stages); self.assertTrue(all(0 < value < 100 for value in reached))
        self.assertEqual(scene_stylist.stage_progress('核对产品是否与参考图一致'), scene_stylist.stage_progress('核对产品与画面中的人物'))
        self.assertIsNotNone(scene_stylist.stage_progress('画面里没有人物，正在重新生成'))
        self.assertIsNone(scene_stylist.stage_progress('等待渲染队列')); self.assertIsNone(scene_stylist.stage_progress(None))
        self.assertIn('progress', scene_stylist.public({'id': 't', 'progress': 44, 'private': 1})); self.assertNotIn('private', scene_stylist.public({'id': 't', 'private': 1}))

    def test_a_pose_check_knows_how_far_each_stage_is(self):
        reached = [orientation.progress_of(stage) for stage, _ in orientation.STAGES]
        self.assertEqual(reached, sorted(reached)); self.assertEqual(reached[0], 0)
        self.assertEqual([orientation.progress_of(stage, 40, 95) for stage, _ in orientation.STAGES], [40, 62, 78])       # inside an analysis job
        self.assertIsNone(orientation.progress_of('别的阶段'))
        self.assertIn('progress', orientation.task_view({'id': 't', 'progress': 70}))


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable: render progress not observed')
class RuntimeTests(unittest.TestCase):
    def test_a_render_reports_its_samples_whatever_blender_prints_itself(self):
        script = '''
import sys
sys.path.insert(0, %r)
import bpy
from blender.render_progress import report
report(); report()                      # asking twice still reports once
scene = bpy.context.scene; scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'
scene.cycles.samples = 48; scene.cycles.use_adaptive_sampling = False; scene.cycles.use_denoising = False
scene.render.resolution_x = scene.render.resolution_y = 160; scene.render.filepath = sys.argv[-1]
bpy.ops.render.render(write_still=True)
print('HANDLERS', sum(1 for handler in bpy.app.handlers.render_stats if handler.__name__ == 'studio_render_progress'))
''' % str(ROOT)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'progress.py'; path.write_text(script, encoding='utf-8')
            ran = subprocess.run([shutil.which('blender'), '-b', '--factory-startup', '--python-exit-code', '1', '--python', str(path), '--',
                                  str(Path(temp) / 'out.png')], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
            self.assertEqual(ran.returncode, 0, ran.stdout[-1500:] + ran.stderr[-500:])
            self.assertTrue((Path(temp) / 'out.png').is_file())
        lines = [line for line in ran.stdout.splitlines() if line.startswith('PROGRESS: ')]
        counts = [tuple(int(value) for value in re.search(PATTERN, line).groups()) for line in lines]
        self.assertTrue(counts, ran.stdout[-800:])
        self.assertEqual(counts[-1], (48, 48))                                   # the last sample is always reported
        self.assertEqual([done for done, _ in counts], sorted({done for done, _ in counts}))      # it only moves forward, no line twice
        self.assertIn('HANDLERS 1', ran.stdout)


if __name__ == '__main__':
    unittest.main()

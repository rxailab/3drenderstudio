"""The global Blender gate: heavy renders one at a time, light work beside them, every child stopped at exit or by id.

A tiny Python script stands in for Blender: it writes when it started and ended, so overlap is measured, not assumed.
"""
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import blender_runner

FAKE = '''import sys, time
out, hold = sys.argv[1], float(sys.argv[2])
open(out + '.start', 'w').write(repr(time.time()))
time.sleep(hold)
open(out + '.end', 'w').write(repr(time.time()))
'''


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name); self.script = self.folder / 'fake_blender.py'; self.script.write_text(FAKE)
        # Fresh lanes per test: one heavy, two light, as the defaults are.
        lanes = patch.dict(blender_runner.SLOTS, {blender_runner.HEAVY: threading.BoundedSemaphore(1),
                                                  blender_runner.LIGHT: threading.BoundedSemaphore(2)})
        lanes.start(); self.addCleanup(lanes.stop)
        self.addCleanup(blender_runner.shutdown, 1)

    def fake(self, name, hold=.6):
        return [sys.executable, str(self.script), str(self.folder / name), str(hold)]

    def span(self, name):
        return (float((self.folder / (name + '.start')).read_text()), float((self.folder / (name + '.end')).read_text()))

    def launch(self, name, lane, hold=.6):
        with blender_runner.launched(self.fake(name, hold), ident=name, lane=lane, stdin=subprocess.DEVNULL) as process:
            return process.wait(timeout=20)

    def parallel(self, *jobs):
        threads = [threading.Thread(target=self.launch, args=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    def test_two_heavy_launches_never_overlap(self):
        self.parallel(('a', blender_runner.HEAVY), ('b', blender_runner.HEAVY))
        first, second = sorted([self.span('a'), self.span('b')])
        self.assertLessEqual(first[1], second[0], 'the second render started before the first one ended')

    def test_light_work_runs_beside_a_heavy_render(self):
        self.parallel(('heavy', blender_runner.HEAVY, 1.5), ('light', blender_runner.LIGHT, .3))
        heavy, light = self.span('heavy'), self.span('light')
        self.assertLess(light[0], heavy[1], 'a light job waited behind the render')

    def test_waiting_is_cancellable_and_reported(self):
        stop = threading.Event(); waited = []
        holder = threading.Thread(target=self.launch, args=('holder', blender_runner.HEAVY, 1.5)); holder.start()
        time.sleep(.4)
        threading.Timer(.3, stop.set).start()
        with self.assertRaises(InterruptedError):
            with blender_runner.launched(self.fake('never'), lane=blender_runner.HEAVY, cancelled=stop.is_set,
                                         on_wait=lambda: waited.append(True)):
                self.fail('a cancelled wait must not start the process')
        holder.join(timeout=20)
        self.assertEqual(waited, [True]); self.assertFalse((self.folder / 'never.start').exists())

    def test_lane_is_released_after_a_failed_start(self):
        with self.assertRaises(OSError):
            with blender_runner.launched([str(self.folder / 'missing-blender')], lane=blender_runner.HEAVY):
                pass
        self.assertTrue(blender_runner.SLOTS[blender_runner.HEAVY].acquire(blocking=False))
        blender_runner.SLOTS[blender_runner.HEAVY].release()

    def test_leaving_the_block_stops_the_child_and_unregisters_it(self):
        with blender_runner.launched(self.fake('left', 30), ident='left', lane=blender_runner.LIGHT) as process:
            self.assertEqual(blender_runner.running('left'), [process])
        self.assertIsNotNone(process.poll()); self.assertEqual(blender_runner.running('left'), [])

    def test_shutdown_and_terminate_kill_registered_children(self):
        started = []
        def hold(name):
            with blender_runner.launched(self.fake(name, 30), ident=name, lane=None) as process:
                started.append(process); process.wait(timeout=30)
        threads = [threading.Thread(target=hold, args=(name,)) for name in ('one', 'two')]
        for thread in threads:
            thread.start()
        deadline = time.time() + 10
        while len(blender_runner.running()) < 2 and time.time() < deadline:
            time.sleep(.05)
        self.assertEqual(blender_runner.terminate('one'), 1)
        threads[0].join(timeout=10); self.assertFalse(threads[0].is_alive())
        self.assertIsNone(next(p for p in started if p.args[2].endswith('two')).poll(), 'terminate(id) stopped another job')
        blender_runner.shutdown(grace=5)
        threads[1].join(timeout=10); self.assertFalse(threads[1].is_alive())
        self.assertTrue(all(process.poll() is not None for process in started))
        self.assertEqual(blender_runner.running(), [])

    def test_run_mirrors_subprocess_run_and_its_timeout(self):
        done = blender_runner.run(self.fake('quick', 0), timeout=20, stdout=subprocess.PIPE)
        self.assertEqual(done.returncode, 0)
        with self.assertRaises(subprocess.TimeoutExpired):
            blender_runner.run(self.fake('slow', 30), timeout=.5)
        self.assertEqual(blender_runner.running(), [])

    def test_timeout_counts_from_launch_not_from_the_wait(self):
        holder = threading.Thread(target=self.launch, args=('first', blender_runner.HEAVY, 1)); holder.start()
        time.sleep(.3)
        done = blender_runner.run(self.fake('second', .3), timeout=.9, lane=blender_runner.HEAVY)
        holder.join(timeout=20)
        self.assertEqual(done.returncode, 0)

    def test_usual_command_line(self):
        self.assertEqual(blender_runner.command('blender', 'w.py', 'r.json', threads=8),
                         ['blender', '-b', '--factory-startup', '--disable-autoexec', '-t', '8', '--python-exit-code', '1',
                          '--python', 'w.py', '--', 'r.json'])
        self.assertNotIn('-t', blender_runner.command('blender', 'w.py', 'r.json'))


if __name__ == '__main__':
    unittest.main()

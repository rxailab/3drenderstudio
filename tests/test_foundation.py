import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app_log
import studio_store as store
import tasks


class DataRootTests(unittest.TestCase):
    def test_the_tests_never_use_the_real_data_folder(self):
        self.assertEqual(Path(os.environ['STUDIO_DATA']).resolve(), store.DATA)
        self.assertNotEqual(store.DATA, store.ROOT / 'data')

    def test_the_folder_and_table_are_made_on_the_first_connection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder) / 'later'):
            self.assertFalse(store.DATA.exists())
            store.put('thing', {'id': 'a'})
            self.assertEqual(store.get('thing', 'a')['id'], 'a')
            (store.DATA / 'studio.sqlite').unlink(); [path.unlink() for path in store.DATA.glob('studio.sqlite-*')]
            self.assertEqual(store.all_records('thing'), [])           # a removed database is made again

    def test_the_environment_variable_chooses_the_folder(self):
        with patch.dict(os.environ, {'STUDIO_DATA': '/somewhere/else'}):
            self.assertEqual(store.data_root(), Path('/somewhere/else').resolve())
        with patch.dict(os.environ, {'STUDIO_DATA': ''}):
            self.assertEqual(store.data_root(), store.ROOT / 'data')


class TaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        mocked = patch.object(store, 'DATA', Path(self.tmp.name)); mocked.start(); self.addCleanup(mocked.stop)

    def test_an_escaping_error_is_logged_and_a_stuck_task_fails_with_a_short_message(self):
        store.put('demo_task', {'id': 't', 'status': 'running', 'stage': 'working'})
        seen = []

        def broken(task_id):
            raise RuntimeError('secret details')
        with self.assertLogs('studio.tasks', logging.ERROR) as logged:
            tasks.run('t', broken, kind='demo_task', message='失败', fields={'stage': '未完成'}, stamp='finished_at', after=seen.append)
        task = store.get('demo_task', 't')
        self.assertEqual((task['status'], task['error'], task['stage']), ('failed', '失败', '未完成'))
        self.assertIn('finished_at', task); self.assertEqual(seen[0]['id'], 't')
        self.assertIn('RuntimeError', logged.output[0])            # the traceback is kept

    def test_a_task_that_ended_on_its_own_is_left_alone(self):
        for status in ('completed', 'cancelled', 'failed'):
            store.put('demo_task', {'id': status, 'status': status, 'error': ''})
            with self.assertLogs('studio.tasks', logging.ERROR):
                tasks.run(status, lambda task_id: 1 / 0, kind='demo_task')
            self.assertEqual(store.get('demo_task', status)['status'], status)
        store.put('demo_task', {'id': 'ok', 'status': 'running'})
        self.assertEqual(tasks.run('ok', lambda task_id: 'done', kind='demo_task'), 'done')
        self.assertEqual(store.get('demo_task', 'ok')['status'], 'running')     # a normal return changes nothing

    def test_threads_are_daemons_and_carry_the_task_id_first(self):
        done = threading.Event()
        worker = tasks.start('x', lambda task_id: done.set(), 'demo')
        self.assertTrue(worker.daemon); self.assertTrue(done.wait(5)); worker.join(5)
        with patch.object(threading, 'Thread') as thread:
            tasks.start('y', print, 'demo')
        self.assertEqual(thread.call_args.kwargs['args'][0], 'y')


class LogTests(unittest.TestCase):
    def test_secrets_are_scrubbed(self):
        with patch.dict(os.environ, {'AI_API_KEY': 'k-1234567890abcdef'}):
            text = app_log.scrub('key k-1234567890abcdef Authorization: Bearer abc.def api_key=zzz sk-abcdefghijklmnopqrstu')
        for secret in ('k-1234567890abcdef', 'abc.def', 'zzz', 'sk-abcdefghijklmnopqrstu'):
            self.assertNotIn(secret, text)

    def test_the_log_rotates_under_the_data_folder(self):
        import logging.handlers
        root = logging.getLogger(); before = list(root.handlers); level = root.level
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(app_log, '_CONFIGURED', []):
                path = app_log.configure(folder)
                self.assertEqual(path, Path(folder) / 'logs' / 'studio.log')
                self.assertEqual(app_log.configure(folder), path)            # once
                added = [handler for handler in root.handlers if handler not in before]
                rotating = [handler for handler in added if isinstance(handler, logging.handlers.RotatingFileHandler)]
                self.assertEqual((len(added), rotating[0].maxBytes, rotating[0].backupCount), (2, app_log.LOG_BYTES, app_log.BACKUPS))
                with patch.dict(os.environ, {'AI_API_KEY': 'k-1234567890abcdef'}):
                    app_log.get('test').error('called with k-1234567890abcdef')
                rotating[0].flush()
                self.assertIn('called with ***', path.read_text(encoding='utf-8'))
                for handler in added: handler.close()
        finally:
            root.handlers[:] = before; root.setLevel(level)


if __name__ == '__main__':
    unittest.main()

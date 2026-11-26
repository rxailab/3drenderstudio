"""Project deletion isolation and real request/worker lifetime boundaries.

Deleting from the interface is forced (DELETE /api/projects/<id>): whatever the project is doing is cancelled, its
processes are stopped and it is removed. lifecycle.delete_project() without ``force`` still refuses a busy project, and
that is what shows here that requests and workers reserve the project they work on.

Run through qa/run_v3_tests.py so the uploaded .env and external networking stay
blocked. Every project, file, database and worker in these tests is temporary.
"""
import copy
import io
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app as web
import engine
import instruction_service
import material_agent
import project_lifecycle as lifecycle
import scene_exports
import studio_store as store
from planner import default_plan


class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        for module in (store, web, engine, scene_exports, material_agent, instruction_service):
            self.replace(module, 'DATA', self.data)
        self.replace(lifecycle, 'OPERATIONS', {})
        self.replace(instruction_service, '_INFLIGHT', {})
        for module, field in ((engine, 'RUNNING_JOBS'), (scene_exports, 'RUNNING_EXPORTS'), (material_agent, 'RUNNING_TASKS')):
            self.replace(module, field, set(), create=True)
        for module in (engine, scene_exports):
            self.replace(module, 'PROCESSES', {})
            self.replace(module, 'QUEUE', queue.Queue())
        with store.connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        # a forced delete waits this long for requests still inside the project before it removes it anyway
        self.replace(lifecycle.abort_project_operations, '__defaults__', (.2,))
        self.projects = {ident: self.make_project(ident) for ident in ('alpha', 'beta')}
        self.client = web.app.test_client()

    def replace(self, target, name, value, **kwargs):
        mocked = patch.object(target, name, value, **kwargs)
        mocked.start()
        self.addCleanup(mocked.stop)

    def make_project(self, ident, folder=True):
        project = {'id': ident, 'name': ident, 'slug': ident, 'status': 'ready', 'version': 1,
                   'analysis': {'objects': []}, 'plan': default_plan(), 'history': [], 'outputs': [],
                   'review_proposals': [{'id': 'proposal-' + ident}],
                   'instruction_receipts': [{'id': 'receipt-' + ident}],
                   'model_path': str(self.data / ident / 'originals/model.obj')}
        store.put('project', project)
        if folder:
            original = self.data / ident / 'originals/model.obj'
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(b'v 0 0 0\n')
            output = self.data / ident / 'preview.png'
            output.write_bytes(b'temporary image fixture')
        return copy.deepcopy(project)

    def record(self, kind, ident, project='alpha', status='completed'):
        item = {'id': ident, 'project_id': project, 'status': status, 'kind': 'preview'}
        store.put(kind, item)
        return item

    def remove_record(self, kind, ident):
        with store.connection() as connection:
            connection.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, ident))

    def assert_project_present(self, ident='alpha'):
        self.assertEqual(store.get('project', ident)['id'], ident)
        self.assertTrue((self.data / ident / 'originals/model.obj').is_file())

    def delete(self, ident='alpha'):
        """The interface's delete: forced."""
        return self.client.delete('/api/projects/' + ident)

    def assert_busy(self, ident='alpha'):
        """An unforced delete is refused and leaves the project as it was."""
        with self.assertRaises(lifecycle.ProjectBusyError):
            lifecycle.delete_project(ident)
        self.assert_project_present(ident)

    def assert_deleted(self, ident='alpha'):
        with self.assertRaises(KeyError):
            store.get('project', ident)
        self.assertFalse((self.data / ident).exists())
        self.assertEqual([item for kind in ('job', 'export', 'material_task') for item in store.all_records(kind)
                          if item.get('project_id') == ident], [])

    def test_project_list_summarizes_only_active_jobs_for_home_cards(self):
        self.record('job', 'done', 'alpha', 'completed')
        self.record('job', 'waiting', 'alpha', 'queued')
        store.put('job', {'id': 'rendering', 'project_id': 'alpha', 'status': 'running', 'kind': 'render',
                          'quality': 'standard', 'progress': 40, 'log_tail': ['private log'], 'plan': {'private': True}})
        listing = {item['id']: item for item in self.client.get('/api/projects').get_json()}
        self.assertEqual(listing['alpha']['active_job'], {'id': 'rendering', 'kind': 'render', 'quality': 'standard',
                                                          'status': 'running', 'progress': 40, 'origin': None})
        self.assertIsNone(listing['beta']['active_job'])

    def test_delete_removes_only_exact_project_records_and_files(self):
        for kind in ('job', 'export', 'material_task'):
            self.record(kind, kind + '-alpha')
            self.record(kind, kind + '-beta', project='beta')
            self.record(kind, kind + '-alpha-other', project='alpha-other')
        # Resource pointers must never determine which directories are removed.
        project = store.get('project', 'alpha')
        project['model_path'] = str(self.data / 'beta/originals/model.obj')
        project['outputs'] = [{'png_url': '/files/beta/preview.png'}]
        store.put('project', project)
        config = self.root / 'configuration-marker.txt'
        config.write_text('keep local configuration')
        response = self.delete()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'deleted': True, 'id': 'alpha', 'cleanup_pending': False})
        with self.assertRaises(KeyError):
            store.get('project', 'alpha')
        self.assertFalse((self.data / 'alpha').exists())
        self.assert_project_present('beta')
        self.assertEqual(config.read_text(), 'keep local configuration')
        self.assertTrue((self.data / 'studio.sqlite').is_file())
        for kind in ('job', 'export', 'material_task'):
            self.assertEqual({item['project_id'] for item in store.all_records(kind)}, {'beta', 'alpha-other'})
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])
        self.assertEqual(self.client.get('/api/projects/alpha').status_code, 404)

    def test_missing_project_directory_can_remove_orphan_records(self):
        self.make_project('orphan', folder=False)
        self.record('job', 'orphan-job', project='orphan')
        response = self.delete('orphan')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['cleanup_pending'])
        with self.assertRaises(KeyError):
            store.get('project', 'orphan')
        self.assertEqual(store.all_records('job'), [])
        self.assert_project_present()

    def test_a_running_listing_build_keeps_the_project_from_being_deleted(self):
        # the build goes on calling paid AI services until it ends: deleting under it would pay for a set nobody gets
        store.put('listing_task', {'id': 'build-1', 'project_id': 'alpha', 'status': 'running'})
        with self.assertRaises(lifecycle.ProjectBusyError):
            lifecycle._ensure_idle('alpha')
        store.put('listing_task', {'id': 'build-1', 'project_id': 'alpha', 'status': 'completed'})
        lifecycle._ensure_idle('alpha')

    def test_missing_and_repeated_delete_are_404_without_other_changes(self):
        self.assertEqual(self.delete('missing').status_code, 404)
        self.assertEqual(self.delete().status_code, 200)
        self.assertEqual(self.delete().status_code, 404)
        self.assert_project_present('beta')

    def test_arbitrary_path_identifiers_are_rejected_before_file_removal(self):
        for ident in ('..', '.', '../beta', 'alpha/beta', '/outside', r'C:\outside', 'studio.sqlite', 'x' * 129):
            with self.subTest(ident=ident):
                self.make_project(ident, folder=False)
                with self.assertRaises(ValueError):
                    lifecycle.delete_project(ident)
                self.assertEqual(store.get('project', ident)['id'], ident)
                self.assert_project_present('beta')
        self.assertTrue((self.data / 'studio.sqlite').is_file())

    def test_project_directory_symlink_is_rejected(self):
        self.make_project('linked', folder=False)
        (self.data / 'linked').symlink_to(self.data / 'beta', target_is_directory=True)
        with self.assertRaises(ValueError):
            lifecycle.delete_project('linked')
        self.assertTrue((self.data / 'linked').is_symlink())
        self.assertEqual(store.get('project', 'linked')['id'], 'linked')
        self.assert_project_present('beta')

    def test_trash_parent_symlink_is_rejected_and_project_is_unchanged(self):
        outside = self.root / 'outside'
        outside.mkdir()
        marker = outside / 'marker.txt'
        marker.write_text('untouched')
        (self.data / lifecycle.TRASH_NAME).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            lifecycle.delete_project('alpha')
        self.assert_project_present()
        self.assertEqual(marker.read_text(), 'untouched')
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    ACTIVE = {'job': ('queued', 'running'), 'export': ('queued', 'running'), 'material_task': ('queued', 'running', 'rendering')}

    def test_each_active_task_status_blocks_an_unforced_delete(self):
        for kind, statuses in self.ACTIVE.items():
            for status in statuses:
                with self.subTest(kind=kind, status=status):
                    self.record(kind, 'active', status=status)
                    self.assert_busy()
                    self.assertEqual(store.get(kind, 'active')['status'], status)
                    self.remove_record(kind, 'active')

    def test_the_interfaces_delete_cancels_whatever_is_active_and_removes_the_project(self):
        for kind, statuses in self.ACTIVE.items():
            for status in statuses:
                with self.subTest(kind=kind, status=status):
                    self.make_project('alpha')
                    self.record(kind, 'active', status=status)
                    self.record(kind, 'other', project='beta', status=status)
                    response = self.delete()
                    self.assertEqual(response.status_code, 200, response.get_json())
                    self.assertEqual(response.get_json(), {'deleted': True, 'id': 'alpha', 'cleanup_pending': False})
                    self.assert_deleted()
                    self.assertEqual(store.get(kind, 'other')['status'], status)          # another project's work goes on
                    self.remove_record(kind, 'other')
        self.assert_project_present('beta')

    def test_a_forced_delete_stops_the_projects_running_processes(self):
        for module, kind in ((engine, 'job'), (scene_exports, 'export')):
            with self.subTest(kind=kind):
                self.make_project('alpha')
                self.record(kind, 'rendering', status='running'); self.record(kind, 'elsewhere', project='beta', status='running')
                mine = Mock(); mine.poll.side_effect = lambda mine=mine: 0 if mine.terminate.called else None
                other = Mock(); other.poll.return_value = None
                module.PROCESSES.update(rendering=mine, elsewhere=other)
                try:
                    self.assertEqual(self.delete().status_code, 200)
                    mine.terminate.assert_called(); other.terminate.assert_not_called(); other.kill.assert_not_called()
                    self.assert_deleted()
                finally:
                    module.PROCESSES.clear(); self.remove_record(kind, 'elsewhere')

    def test_unrelated_active_tasks_do_not_block_deletion(self):
        for kind in ('job', 'export', 'material_task'):
            self.record(kind, kind + '-active', project='beta', status='running')
        self.assertEqual(self.delete().status_code, 200)
        self.assert_project_present('beta')

    def test_cancelled_tasks_remain_protected_from_an_unforced_delete_until_worker_and_process_exit(self):
        entries = [(engine, 'RUNNING_JOBS', 'job'), (scene_exports, 'RUNNING_EXPORTS', 'export'),
                   (material_agent, 'RUNNING_TASKS', 'material_task'),
                   (engine, 'PROCESSES', 'job'), (scene_exports, 'PROCESSES', 'export')]
        for module, field, kind in entries:
            with self.subTest(kind=kind, tracking=field):
                self.record(kind, 'finishing', status='cancelled')
                tracking = getattr(module, field)
                if isinstance(tracking, set):
                    tracking.add('finishing')
                else:
                    tracking['finishing'] = Mock()
                try:
                    self.assert_busy()
                finally:
                    tracking.clear()
                    self.remove_record(kind, 'finishing')
        self.assertEqual(self.delete().status_code, 200)

    def test_request_operation_count_and_inflight_instruction_block_an_unforced_delete(self):
        lifecycle.begin_operation('alpha')
        lifecycle.begin_operation('alpha')
        self.assertEqual(lifecycle.OPERATIONS['alpha'], 2)
        self.assert_busy()
        lifecycle.end_operation('alpha')
        self.assert_busy()
        lifecycle.end_operation('alpha')
        self.assertNotIn('alpha', lifecycle.OPERATIONS)
        instruction_service._INFLIGHT['alpha'] = {'token': 'temporary'}
        self.assert_busy()
        instruction_service._INFLIGHT.clear()
        self.assertEqual(lifecycle.delete_project('alpha')['deleted'], True)

    def test_the_interfaces_delete_does_not_wait_for_requests_or_an_instruction_in_flight(self):
        lifecycle.begin_operation('alpha')
        instruction_service._INFLIGHT['alpha'] = {'token': 'temporary'}
        self.assertEqual(self.delete().status_code, 200)
        self.assert_deleted()
        self.assertNotIn('alpha', instruction_service._INFLIGHT)
        lifecycle.end_operation('alpha')                 # the request it did not wait for ends later, harmlessly
        self.assertEqual(lifecycle.OPERATIONS, {})

    def test_worker_entrypoints_safely_ignore_deleted_queue_ids(self):
        self.assertEqual(self.delete().status_code, 200)
        engine.run_job('deleted-job')
        scene_exports.execute('deleted-export')
        material_agent._run('deleted-material-task')
        self.assertEqual(engine.RUNNING_JOBS, set())
        self.assertEqual(scene_exports.RUNNING_EXPORTS, set())
        self.assertEqual(material_agent.RUNNING_TASKS, set())
        self.assertFalse((self.data / 'alpha').exists())

    def test_queue_workers_continue_after_deleted_id(self):
        for module, kind, implementation in ((engine, 'job', '_run_job'), (scene_exports, 'export', '_execute')):
            with self.subTest(kind=kind):
                self.record(kind, 'next', project='beta', status='queued')
                consumed = []
                fake_queue = Mock()
                fake_queue.get.side_effect = ['deleted-id', 'next', StopIteration]
                with patch.object(module, 'QUEUE', fake_queue), patch.object(module, implementation, side_effect=lambda ident: consumed.append(ident)):
                    with self.assertRaises(StopIteration):
                        module.worker()
                self.assertEqual(consumed, ['next'])
                self.assertEqual(fake_queue.task_done.call_count, 2)
                self.remove_record(kind, 'next')

    def test_worker_tracking_covers_cancelled_callback_until_finally(self):
        entries = [(engine, 'job', 'run_job', '_run_job', 'RUNNING_JOBS'),
                   (scene_exports, 'export', 'execute', '_execute', 'RUNNING_EXPORTS'),
                   (material_agent, 'material_task', '_run', '_run_task', 'RUNNING_TASKS')]
        for module, kind, entry, implementation, field in entries:
            with self.subTest(kind=kind):
                self.record(kind, 'finishing', status='queued')
                def finish(ident):
                    item = store.get(kind, ident)
                    item['status'] = 'cancelled'
                    store.put(kind, item)
                    self.assertIn(ident, getattr(module, field))
                    self.assert_busy()
                with patch.object(module, implementation, side_effect=finish):
                    getattr(module, entry)('finishing')
                self.assertEqual(getattr(module, field), set())
                self.remove_record(kind, 'finishing')
        self.assertEqual(self.delete().status_code, 200)

    def test_worker_tracking_is_released_after_unexpected_error(self):
        entries = [(engine, 'job', 'run_job', '_run_job', 'RUNNING_JOBS'),
                   (scene_exports, 'export', 'execute', '_execute', 'RUNNING_EXPORTS'),
                   (material_agent, 'material_task', '_run', '_run_task', 'RUNNING_TASKS')]
        for module, kind, entry, implementation, field in entries:
            with self.subTest(kind=kind):
                self.record(kind, 'failure', status='queued')
                with patch.object(module, implementation, side_effect=RuntimeError('fixture failure')):
                    with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                        getattr(module, entry)('failure')
                self.assertEqual(getattr(module, field), set())
                self.remove_record(kind, 'failure')

    def test_rename_failure_preserves_database_and_original_files_when_not_forced(self):
        self.record('job', 'old-job')
        with patch.object(Path, 'rename', side_effect=PermissionError('file is busy')):
            with self.assertRaises(PermissionError):
                lifecycle.delete_project('alpha')
        self.assert_project_present()
        self.assertEqual(store.get('job', 'old-job')['project_id'], 'alpha')
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    def test_a_forced_delete_whose_folder_cannot_be_moved_still_removes_the_project(self):
        self.record('job', 'old-job')
        with patch.object(Path, 'rename', side_effect=PermissionError('file is busy')), patch.object(lifecycle.time, 'sleep'):
            response = self.delete()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'deleted': True, 'id': 'alpha', 'cleanup_pending': False})    # removed in place instead
        self.assert_deleted()
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])
        self.assert_project_present('beta')

    def test_database_failure_rolls_back_records_and_restores_renamed_folder(self):
        self.record('job', 'old-job')
        original_connection = store.connection
        class FailingConnection:
            def __init__(self):
                self.real = original_connection()
            def __enter__(self):
                self.real.__enter__()
                return self
            def __exit__(self, *args):
                return self.real.__exit__(*args)
            def execute(self, sql, params=()):
                if sql == 'DELETE FROM records WHERE kind=? AND id=?' and params == ('project', 'alpha'):
                    raise sqlite3.OperationalError('fixture transaction failure')
                return self.real.execute(sql, params)
            def __getattr__(self, name):
                return getattr(self.real, name)
        with patch.object(store, 'connection', side_effect=FailingConnection):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'fixture transaction failure'):
                lifecycle.delete_project('alpha')
        self.assert_project_present()
        self.assertEqual(store.get('job', 'old-job')['project_id'], 'alpha')
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])
        trash = self.data / lifecycle.TRASH_NAME
        self.assertEqual(list(trash.iterdir()), [])

    def test_cleanup_failure_is_persisted_and_startup_retry_finishes(self):
        with patch.object(lifecycle.shutil, 'rmtree', side_effect=PermissionError('fixture file busy')):
            response = self.delete()
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result['deleted'])
        self.assertTrue(result['cleanup_pending'])
        self.assertIn('下次启动', result['warning'])
        with self.assertRaises(KeyError):
            store.get('project', 'alpha')
        pending = store.all_records(lifecycle.CLEANUP_KIND)
        self.assertEqual(len(pending), 1)
        staged = self.data / lifecycle.TRASH_NAME / pending[0]['folder_name']
        self.assertTrue((staged / 'originals/model.obj').is_file())
        self.assertFalse((self.data / 'alpha').exists())
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': pending[0]['id'], 'cleaned': True}])
        self.assertFalse(staged.exists())
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])
        self.assert_project_present('beta')

    def test_staged_deleted_files_and_symlink_aliases_are_not_downloadable(self):
        with patch.object(lifecycle.shutil, 'rmtree', side_effect=PermissionError('fixture file busy')):
            self.assertEqual(self.delete().status_code, 200)
        pending = store.all_records(lifecycle.CLEANUP_KIND)[0]
        relative = lifecycle.TRASH_NAME + '/' + pending['folder_name'] + '/preview.png'
        self.assertEqual(self.client.get('/files/' + relative).status_code, 404)
        (self.data / 'beta/alias.png').symlink_to(self.data / relative)
        self.assertEqual(self.client.get('/files/beta/alias.png').status_code, 404)
        response = self.client.get('/files/beta/preview.png')
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_cleanup_rejects_tampered_directory_record(self):
        store.put(lifecycle.CLEANUP_KIND, {'id': 'bad', 'folder_name': '../beta', 'status': 'pending'})
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': 'bad', 'cleaned': False}])
        self.assert_project_present('beta')
        self.assertEqual(store.get(lifecycle.CLEANUP_KIND, 'bad')['last_error'], 'ValueError')

    def prepared_journal(self, project='alpha', status='prepared'):
        ident = 'a' * 32
        item = {'id': ident, 'project_id': project, 'folder_name': project + '-' + ident,
                'status': status, 'created_at': 1, 'updated_at': 1}
        store.put(lifecycle.CLEANUP_KIND, item)
        staged = self.data / lifecycle.TRASH_NAME / item['folder_name']
        staged.parent.mkdir(exist_ok=True)
        return item, staged

    def test_preparation_is_committed_and_readable_before_directory_move(self):
        original_rename = Path.rename
        observed = []
        def verify_before_move(path, destination):
            journals = store.all_records(lifecycle.CLEANUP_KIND)
            self.assertEqual(len(journals), 1)
            self.assertEqual(journals[0]['status'], 'prepared')
            self.assertEqual(journals[0]['project_id'], 'alpha')
            self.assert_project_present()
            observed.append(journals[0]['id'])
            return original_rename(path, destination)
        with patch.object(Path, 'rename', autospec=True, side_effect=verify_before_move):
            self.assertTrue(lifecycle.delete_project('alpha')['deleted'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    def test_failed_preparation_write_never_moves_or_deletes_project(self):
        original_put = store.put
        def fail_preparation(kind, item):
            if kind == lifecycle.CLEANUP_KIND:
                raise sqlite3.OperationalError('fixture preparation cannot commit')
            return original_put(kind, item)
        with patch.object(store, 'put', side_effect=fail_preparation), patch.object(Path, 'rename') as rename:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'preparation cannot commit'):
                lifecycle.delete_project('alpha')
        rename.assert_not_called()
        self.assert_project_present()
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    def test_startup_clears_preparation_if_directory_was_never_moved(self):
        item, staged = self.prepared_journal()
        self.assertFalse(staged.exists())
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': item['id'], 'cleaned': True}])
        self.assert_project_present()
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    def test_crash_after_move_before_database_commit_restores_project_on_startup(self):
        self.record('job', 'old-job')
        original_connection = store.connection
        class SimulatedProcessExit(BaseException):
            pass
        class ExitAfterMoveConnection:
            def __init__(self):
                self.real = original_connection()
            def __enter__(self):
                self.real.__enter__()
                return self
            def __exit__(self, *args):
                return self.real.__exit__(*args)
            def execute(self, sql, params=()):
                if sql == 'SELECT kind,id,payload FROM records':
                    raise SimulatedProcessExit()
                return self.real.execute(sql, params)
            def __getattr__(self, name):
                return getattr(self.real, name)
        # BaseException bypasses ordinary rollback recovery; connection.close
        # simulates SQLite rolling back the uncommitted transaction on exit.
        with patch.object(store, 'connection', side_effect=ExitAfterMoveConnection):
            with self.assertRaises(SimulatedProcessExit):
                lifecycle.delete_project('alpha')
        self.assertEqual(store.get('project', 'alpha')['id'], 'alpha')
        self.assertEqual(store.get('job', 'old-job')['project_id'], 'alpha')
        self.assertFalse((self.data / 'alpha').exists())
        journal = store.all_records(lifecycle.CLEANUP_KIND)[0]
        self.assertEqual(journal['status'], 'prepared')
        staged = self.data / lifecycle.TRASH_NAME / journal['folder_name']
        self.assertTrue(staged.is_dir())
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': journal['id'], 'cleaned': True}])
        self.assert_project_present()
        self.assertFalse(staged.exists())
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    def test_crash_after_database_commit_retries_file_cleanup_on_startup(self):
        class SimulatedProcessExit(BaseException):
            pass
        with patch.object(lifecycle, '_cleanup', side_effect=SimulatedProcessExit):
            with self.assertRaises(SimulatedProcessExit):
                lifecycle.delete_project('alpha')
        with self.assertRaises(KeyError):
            store.get('project', 'alpha')
        journal = store.all_records(lifecycle.CLEANUP_KIND)[0]
        self.assertEqual(journal['status'], 'pending')
        staged = self.data / lifecycle.TRASH_NAME / journal['folder_name']
        self.assertTrue(staged.is_dir())
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': journal['id'], 'cleaned': True}])
        self.assertFalse(staged.exists())
        self.assertFalse((self.data / 'alpha').exists())
        self.assert_project_present('beta')

    def test_two_possible_originals_preserve_both_directories_and_journal(self):
        item, staged = self.prepared_journal()
        staged.mkdir()
        marker = staged / 'other-copy.txt'
        marker.write_text('preserve both possible originals')
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': item['id'], 'cleaned': False}])
        self.assert_project_present()
        self.assertEqual(marker.read_text(), 'preserve both possible originals')
        saved = store.get(lifecycle.CLEANUP_KIND, item['id'])
        self.assertEqual(saved['status'], 'prepared')
        self.assertEqual(saved['last_error'], 'ValueError')

    def test_restore_failure_keeps_prepared_journal_for_another_startup(self):
        item, staged = self.prepared_journal()
        (self.data / 'alpha').rename(staged)
        with patch.object(Path, 'rename', side_effect=PermissionError('fixture restore blocked')):
            self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': item['id'], 'cleaned': False}])
        self.assertTrue((staged / 'originals/model.obj').is_file())
        self.assertEqual(store.get(lifecycle.CLEANUP_KIND, item['id'])['status'], 'prepared')
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': item['id'], 'cleaned': True}])
        self.assert_project_present()
        self.assertEqual(store.all_records(lifecycle.CLEANUP_KIND), [])

    def test_stale_pending_journal_restores_a_live_project_instead_of_erasing_it(self):
        item, staged = self.prepared_journal(status='pending')
        (self.data / 'alpha').rename(staged)
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': item['id'], 'cleaned': True}])
        self.assert_project_present()
        self.assert_project_present('beta')
        self.assertFalse(staged.exists())

    def test_journal_cannot_retarget_another_projects_staged_directory(self):
        item, staged = self.prepared_journal(project='beta')
        (self.data / 'beta').rename(staged)
        item['project_id'] = 'missing-alpha'
        store.put(lifecycle.CLEANUP_KIND, item)
        self.assertEqual(lifecycle.cleanup_deleted_projects(), [{'id': item['id'], 'cleaned': False}])
        self.assertTrue((staged / 'originals/model.obj').is_file())
        self.assertEqual(store.get('project', 'beta')['id'], 'beta')
        self.assert_project_present()

    def threaded_request(self, endpoint, action, patch_target, patch_name, return_value, force=False, gone=404):
        entered, release = threading.Event(), threading.Event()
        responses, exceptions = [], []
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('fixture request was not released')
            return return_value
        def request():
            try:
                responses.append(action(web.app.test_client()))
            except BaseException as exc:
                exceptions.append(exc)
        with patch.object(patch_target, patch_name, side_effect=blocked):
            thread = threading.Thread(target=request)
            thread.start()
            try:
                self.assertTrue(entered.wait(5), endpoint + ' did not enter the blocked operation')
                self.assertEqual(lifecycle.OPERATIONS.get('alpha'), 1)
                self.assert_busy()
                if force:
                    self.assertEqual(self.delete().status_code, 200)
                    self.assert_deleted()
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(exceptions, [])
        self.assertEqual(len(responses), 1)
        self.assertNotIn('alpha', lifecycle.OPERATIONS)
        if force:
            # the request the delete did not wait for ends as ``gone`` says: never a server error
            self.assertEqual(responses[0].status_code, gone, responses[0].get_json())
            self.assert_deleted()                         # and it does not bring the project back
            self.assert_project_present('beta')
        else:
            self.assertEqual(responses[0].status_code, 200, responses[0].get_json())
            self.assertEqual(self.delete().status_code, 200)

    def test_material_upload_request_blocks_an_unforced_delete_until_file_processing_finishes(self):
        def upload(client):
            return client.post('/api/projects/alpha/materials/import', data={
                'name': 'Paint', 'source': 'temporary fixture', 'license': 'CC0',
                'roughness': (io.BytesIO(b'temporary fixture image'), 'roughness.png')})
        self.threaded_request('material import', upload, engine, 'verify_image', (8, 8))

    def test_pick_request_blocks_an_unforced_delete_until_subprocess_boundary_returns(self):
        def pick(client):
            return client.post('/api/projects/alpha/feedback/pick', json={'points': []})
        self.threaded_request('surface pick', pick, engine, 'pick_output', {'snapshot': {}, 'hits': []})

    def test_a_request_whose_project_is_deleted_under_it_ends_as_not_found(self):
        def upload(client):
            return client.post('/api/projects/alpha/materials/import', data={
                'name': 'Paint', 'source': 'temporary fixture', 'license': 'CC0',
                'roughness': (io.BytesIO(b'temporary fixture image'), 'roughness.png')})
        self.threaded_request('material import', upload, engine, 'verify_image', (8, 8), force=True)

    def test_a_pick_whose_project_is_deleted_under_it_ends_cleanly(self):
        def pick(client):
            return client.post('/api/projects/alpha/feedback/pick', json={'points': []})
        # it only reads: what it had already found is still its answer, and nothing is written back
        self.threaded_request('surface pick', pick, engine, 'pick_output', {'snapshot': {}, 'hits': []}, force=True, gone=200)

    def test_request_reservation_is_released_after_validation_error(self):
        def fail(*_args):
            self.assertEqual(lifecycle.OPERATIONS.get('alpha'), 1)
            raise ValueError('fixture invalid pick')
        with patch.object(engine, 'pick_output', side_effect=fail):
            response = self.client.post('/api/projects/alpha/feedback/pick', json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(lifecycle.OPERATIONS, {})
        self.assertEqual(self.delete().status_code, 200)

    def test_job_and_export_write_routes_reserve_their_owner_project(self):
        for module, kind, endpoint, function in ((engine, 'job', '/api/jobs/old/cancel', 'cancel'),
                                                  (scene_exports, 'export', '/api/exports/old/cancel', 'stop')):
            with self.subTest(kind=kind):
                item = self.record(kind, 'old', status='failed')
                def cancel(_ident):
                    self.assertEqual(lifecycle.OPERATIONS.get('alpha'), 1)
                    self.assert_busy()
                    return item
                with patch.object(module, function, side_effect=cancel):
                    self.assertEqual(self.client.post(endpoint).status_code, 200)
                self.assertNotIn('alpha', lifecycle.OPERATIONS)
                self.remove_record(kind, 'old')
        self.assertEqual(self.delete().status_code, 200)

    def test_plain_project_poll_does_not_reserve_or_block_deletion(self):
        snapshot = copy.deepcopy(self.projects['alpha'])
        def poll(_ident):
            self.assertNotIn('alpha', lifecycle.OPERATIONS)
            self.assertEqual(web.app.test_client().delete('/api/projects/alpha').status_code, 200)
            return snapshot
        with patch.object(engine, 'project_view', side_effect=poll):
            response = self.client.get('/api/projects/alpha')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(lifecycle.OPERATIONS, {})
        self.assert_project_present('beta')


if __name__ == '__main__':
    unittest.main()

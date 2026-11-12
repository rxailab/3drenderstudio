"""Project removal coordinated with requests and background worker lifetimes."""
import json
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

import studio_store as store

OPERATIONS = {}
CLEANUP_KIND = 'project_deletion'
TRASH_NAME = '.deleted-projects'


class ProjectBusyError(ValueError):
    pass


def begin_operation(project_id):
    """Reserve a live project without holding LOCK across IO or inference."""
    with store.LOCK:
        store.get('project', project_id)
        OPERATIONS[project_id] = OPERATIONS.get(project_id, 0) + 1


def end_operation(project_id):
    with store.LOCK:
        count = OPERATIONS.get(project_id, 0)
        if count <= 1:
            OPERATIONS.pop(project_id, None)
        else:
            OPERATIONS[project_id] = count - 1


def _is_link(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _project_folder(project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project_id):
        raise ValueError('项目标识无效，未删除任何文件。')
    root = store.DATA.resolve()
    folder = store.DATA / project_id
    if _is_link(folder) or folder.resolve().parent != root or folder.resolve() == root:
        raise ValueError('项目目录不在允许的独立位置，未删除任何文件。')
    if folder.exists() and not folder.is_dir():
        raise ValueError('项目目录无效，未删除任何文件。')
    return folder


def _trash_folder(folder_name, create=False):
    if not isinstance(folder_name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', folder_name):
        raise ValueError('待清理目录标识无效。')
    parent = store.DATA / TRASH_NAME
    if _is_link(parent) or parent.resolve().parent != store.DATA.resolve():
        raise ValueError('待清理目录位置无效。')
    if create:
        parent.mkdir(exist_ok=True)
    folder = parent / folder_name
    if _is_link(folder) or folder.resolve().parent != parent.resolve():
        raise ValueError('待清理项目目录位置无效。')
    return folder


def _ensure_idle(project_id):
    import engine
    import instruction_service
    import material_agent
    import scene_exports
    import ecommerce

    if OPERATIONS.get(project_id) or project_id in instruction_service._INFLIGHT:
        raise ProjectBusyError('项目还有正在处理的操作，请等待处理完成后再删除。')
    checks = [
        ('job', {'queued', 'running'}, getattr(engine, 'RUNNING_JOBS', set()), engine.PROCESSES,
         '项目正在分析或渲染，请先取消任务或等待完成后再删除。'),
        ('export', {'queued', 'running'}, scene_exports.RUNNING_EXPORTS, scene_exports.PROCESSES,
         '项目正在导出工程，请先取消导出或等待完成后再删除。'),
        ('material_task', material_agent.ACTIVE, material_agent.RUNNING_TASKS, {},
         '项目正在调整材质，请先取消任务或等待完成后再删除。'),
        (ecommerce.KIND, {'queued', 'running'}, ecommerce.RUNNING, ecommerce.PROCESSES,
         '项目正在导出电商套图，请先取消导出或等待完成后再删除。'),
    ]
    for kind, active, running, processes, message in checks:
        for record in store.all_records(kind):
            if record.get('project_id') != project_id:
                continue
            if record.get('status') in active or record['id'] in running or record['id'] in processes:
                raise ProjectBusyError(message)


def _journal_paths(item):
    """A journal can only address its own generated project staging directory."""
    project_id, ident = item.get('project_id'), item.get('id')
    original = _project_folder(project_id)
    if (not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident)
            or item.get('folder_name') != project_id + '-' + ident
            or item.get('status') not in ('prepared', 'pending')):
        raise ValueError('待清理记录与项目目录不一致。')
    return original, _trash_folder(item['folder_name'])


def _cleanup(item):
    """Restore an uncommitted move, or clean files only after project removal.

    A committed journal precedes every rename. The presence of the live project
    is authoritative, even for a stale record labelled pending. Never remove
    either copy if a crash or external change left two possible originals.
    """
    try:
        with store.LOCK:
            original, staged = _journal_paths(item)
            try:
                project = store.get('project', item['project_id'])
            except KeyError:
                project = None
            if project is not None:
                if project.get('id') != item['project_id']:
                    raise ValueError('项目记录标识不一致，暂不恢复或清理。')
                if original.exists() and staged.exists():
                    raise ValueError('原项目与待恢复目录同时存在，已保留两份数据。')
                if staged.exists():
                    staged.rename(original)
                elif not original.exists():
                    raise ValueError('项目文件暂不可定位，已保留恢复记录。')
            elif staged.exists():
                shutil.rmtree(staged)
            with store.connection() as connection:
                connection.execute('DELETE FROM records WHERE kind=? AND id=?', (CLEANUP_KIND, item['id']))
        return True
    except (OSError, ValueError, sqlite3.Error) as exc:
        item.update(last_error=type(exc).__name__, updated_at=time.time())
        try:
            store.put(CLEANUP_KIND, item)
        except sqlite3.Error:
            # The prior committed journal remains available when the database
            # itself cannot record a fresh error message.
            pass
        return False


def cleanup_deleted_projects():
    """Restore interrupted removals and retry cleanup; never erase live files."""
    results = []
    for item in store.all_records(CLEANUP_KIND):
        results.append({'id': item['id'], 'cleaned': _cleanup(item)})
    return results


def delete_project(project_id):
    cleanup = None
    with store.LOCK:
        project = store.get('project', project_id)
        if project.get('id') != project_id:
            raise ValueError('项目记录标识不一致，未删除。')
        _ensure_idle(project_id)
        folder = _project_folder(project_id)
        staged = None
        connection = None
        try:
            # The name is generated here; stored resource paths never drive removal.
            if folder.exists():
                ident = uuid.uuid4().hex
                cleanup = {'id': ident, 'project_id': project_id,
                           'folder_name': project_id + '-' + ident,
                           'status': 'prepared', 'created_at': time.time(), 'updated_at': time.time()}
                staged = _trash_folder(cleanup['folder_name'], create=True)
                # This transaction must commit BEFORE the directory is moved;
                # SQLite rollback after a process exit cannot erase recovery data.
                store.put(CLEANUP_KIND, cleanup)
            connection = store.connection()
            connection.execute('BEGIN IMMEDIATE')
            if staged is not None:
                folder.rename(staged)
            records = connection.execute('SELECT kind,id,payload FROM records').fetchall()
            related = [(kind, ident) for kind, ident, payload in records
                       if kind in {'job', 'export', 'material_task', 'ecommerce_export'}
                       and json.loads(payload).get('project_id') == project_id]
            connection.executemany('DELETE FROM records WHERE kind=? AND id=?', related)
            connection.execute('DELETE FROM records WHERE kind=? AND id=?', ('project', project_id))
            if cleanup:
                cleanup.update(status='pending', updated_at=time.time())
                connection.execute('UPDATE records SET payload=? WHERE kind=? AND id=?',
                                   (json.dumps(cleanup, ensure_ascii=False), CLEANUP_KIND, cleanup['id']))
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
            if cleanup:
                # Reload the committed state rather than a pending in-memory
                # value from a transaction that just rolled back.
                try:
                    saved = store.get(CLEANUP_KIND, cleanup['id'])
                except KeyError:
                    saved = None
                if saved is not None:
                    _cleanup(saved)
            raise
        finally:
            if connection is not None:
                connection.close()
    cleaned = True if cleanup is None else _cleanup(cleanup)
    result = {'deleted': True, 'id': project_id, 'cleanup_pending': not cleaned}
    if not cleaned:
        result['warning'] = '项目已从列表移除，但部分文件仍被占用；应用下次启动会继续清理。'
    return result

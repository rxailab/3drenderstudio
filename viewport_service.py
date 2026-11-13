"""Version-bound, read-only preparation of local interactive viewport assets."""
import atexit
import copy
import hashlib
import json
import re
import struct
import subprocess
import threading
import time
from pathlib import Path

import engine
import planner
import project_lifecycle
from studio_store import ROOT, DATA, LOCK, get, atomic_json

SCHEMA='wuying-viewport-v1'
TASKS={}
PROCESSES={}
_WORKERS=threading.BoundedSemaphore(2)
_TASK_LOCK=threading.RLock()
MAX_GLB_BYTES=256*1024*1024
TIMEOUT=180
MAX_ACTIVE=8
IMAGE_EXTENSIONS={'.png','.jpg','.jpeg','.webp','.tga','.tif','.tiff','.bmp','.exr','.hdr','.mtl'}


class ViewportError(ValueError):
    def __init__(self,message,status_code=400):
        super().__init__(message);self.status_code=status_code


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _sha(path):
    before=path.stat();digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ViewportError('场景资源正在变化，请稍后重新打开实时预览。',409)
    return digest.hexdigest()


def _path(project_id,value,suffixes,url=False):
    root=(DATA/project_id).resolve()
    if url:
        if not isinstance(value,str) or not value.startswith('/files/'):
            raise ViewportError('实时预览缺少有效场景来源。')
        raw=DATA/value[7:]
    else:
        if not isinstance(value,str) or not value:raise ViewportError('实时预览缺少模型来源。')
        raw=Path(value)
    path=raw.resolve()
    if raw.is_symlink() or root not in path.parents or path.suffix.lower() not in suffixes or not path.is_file():
        raise ViewportError('实时预览来源不存在或不属于当前项目。')
    return path


def _product_names(project):
    return list(dict.fromkeys(row['name'] for row in (project.get('analysis') or {}).get('objects',[])
        if isinstance(row,dict) and row.get('type','MESH') in ('MESH','CURVE','FONT','SURFACE','META')
        and isinstance(row.get('name'),str) and row['name']))[:10000]


def _resource_state(project):
    """Include local texture bytes in cache identity, excluding reference images."""
    paths=set();folder=DATA/project['id']/'originals'
    if folder.is_dir() and not folder.is_symlink():
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                paths.add(_path(project['id'],str(path),IMAGE_EXTENSIONS))
                if len(paths)>2000:raise ViewportError('模型资源数量超出实时预览范围。')
    plan=project['plan']
    for row in plan.get('material_overrides',[])+plan.get('object_material_overrides',[]):
        for value in row.get('textures',{}).values():paths.add(_path(project['id'],value,IMAGE_EXTENSIONS))
    return [{'path':path.relative_to(DATA/project['id']).as_posix(),'sha256':_sha(path)} for path in sorted(paths)]


def _source(project,shot_id):
    outputs=[row for row in project.get('outputs',[]) if row.get('shot_id')==shot_id and row.get('quality')!='thumbnail' and row.get('blend_url')]
    outputs.sort(key=lambda row:(row.get('version',0),row.get('created_at',0)),reverse=True)
    for output in outputs:
        if output.get('version')!=project['version']:continue
        path=_path(project['id'],output['blend_url'],{'.blend'},True);sha=_sha(path)
        if output.get('blend_sha256') and output['blend_sha256']!=sha:
            raise ViewportError('已保存场景发生变化，请重新生成当前预览。',409)
        return {'path':str(path),'sha256':sha,'kind':'saved_scene','version':output['version'],
                'output_id':output.get('job_id'),'baseline_plan':copy.deepcopy(output.get('effective_plan',project['plan'])),
                'product_object_names':_product_names(project)}
    token=project['plan'].get('scene_checkpoints',{}).get(shot_id)
    if token:
        import hero_optimizer
        path,metadata=hero_optimizer._checkpoint(project['id'],token)
        path=_path(project['id'],str(path),{'.blend'})
        return {'path':str(path),'sha256':_sha(path),'kind':'scene_checkpoint','version':project['version'],
                'checkpoint':token,'baseline_plan':copy.deepcopy(metadata['baseline_plan']),
                'product_object_names':metadata['product_object_names']}
    for output in outputs:
        if not isinstance(output.get('effective_plan'),dict):continue
        path=_path(project['id'],output['blend_url'],{'.blend'},True);sha=_sha(path)
        if output.get('blend_sha256') and output['blend_sha256']!=sha:continue
        return {'path':str(path),'sha256':sha,'kind':'derived_scene','version':output['version'],
                'output_id':output.get('job_id'),'baseline_plan':copy.deepcopy(output['effective_plan']),
                'product_object_names':_product_names(project)}
    path=_path(project['id'],project.get('model_path'),{'.blend','.glb','.obj'})
    return {'path':str(path),'sha256':_sha(path),'kind':'original_model','version':1,
            'product_object_names':_product_names(project)}


def _signature(project,shot_id):
    source=_source(project,shot_id)
    script=ROOT/'blender/export_viewport.py'
    if not script.is_file():raise ViewportError('实时预览组件尚未就绪，请完整安装本次升级。',503)
    revision_paths=[script,ROOT/'blender/worker.py',ROOT/'blender/hero_scene.py',ROOT/'blender/camera_projection.py',
                    ROOT/'blender/light_direction.py',ROOT/'camera_controls.py',ROOT/'hero_state.py']
    revision=_digest({str(path.relative_to(ROOT)):_sha(path) for path in revision_paths if path.is_file()})
    guard={'project_id':project['id'],'version':project['version'],'shot_id':shot_id,
           'plan':_digest(project['plan']),'analysis':_digest(project.get('analysis')),
           'source':source,'resources':_resource_state(project),'export_revision':revision,'schema':SCHEMA}
    return source,guard,_digest(guard)


def _glb(path):
    if path.is_symlink() or not path.is_file() or not 20<=path.stat().st_size<=MAX_GLB_BYTES:return False
    with path.open('rb') as stream:
        header=stream.read(20)
        magic,version,length,chunk_size,kind=struct.unpack('<4sIIII',header)
        if magic!=b'glTF' or version!=2 or length!=path.stat().st_size or kind!=0x4E4F534A or chunk_size>8*1024*1024:return False
        data=json.loads(stream.read(chunk_size).decode('utf-8').rstrip('\0 \t\r\n'))
    if not data.get('meshes') or not data.get('scenes'):return False
    for row in data.get('buffers',[])+data.get('images',[]):
        if 'uri' in row and not str(row['uri']).startswith('data:'):return False
    return True


def _cached(folder,fingerprint):
    try:
        path=folder/'manifest.json';asset=folder/'scene.glb'
        if path.is_symlink() or not path.is_file() or path.stat().st_size>4*1024*1024:return None
        manifest=json.loads(path.read_text(encoding='utf-8'))
        if (manifest.get('schema')!=SCHEMA or manifest.get('fingerprint')!=fingerprint
                or not _glb(asset) or manifest.get('asset_sha256')!=_sha(asset)):return None
        project_id=folder.relative_to(DATA).parts[0]
        for row in manifest.get('resources',[]):
            resource=_path(project_id,str(DATA/project_id/row['path']),IMAGE_EXTENSIONS|{'.bin'})
            if _sha(resource)!=row['sha256']:return None
        return manifest
    except (ValueError,OSError,TypeError,KeyError,struct.error):return None


def _public(item):
    keys=('id','status','project_id','version','shot_id','source_version','source_kind','source_sha256',
          'asset','error','warnings','created_at','updated_at')
    return {key:copy.deepcopy(item.get(key)) for key in keys}


def _url(path):return '/files/'+path.relative_to(DATA).as_posix()


def _asset(folder,manifest):
    # Texture-only refreshes can reuse geometry identity. A content hash also
    # prevents browser disk caching from displaying the previous GLB bytes.
    suffix='?v='+manifest['asset_sha256'][:16]
    return {'url':_url(folder/'scene.glb')+suffix,'metadata_url':_url(folder/'manifest.json')+suffix}


def capabilities():
    available=bool(engine.blender_path() and (ROOT/'blender/export_viewport.py').is_file())
    return {'available':available,'format':'glb','projection':'orthographic','interactive':True}


def submit(project_id,body):
    if not isinstance(body,dict) or set(body)-{'shot_id','expected_version'}:
        raise ViewportError('实时预览请求格式无效。')
    shot_id=body.get('shot_id');version=body.get('expected_version')
    if not isinstance(shot_id,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,32}',shot_id) or type(version) is not int:
        raise ViewportError('请选择有效的镜头与方案版本。')
    with LOCK:
        project=get('project',project_id)
        if project['version']!=version:raise ViewportError('方案已更新，请刷新实时预览。',409)
        project['plan']=planner.validate(project['plan'])
        if shot_id not in {shot['id'] for shot in project['plan']['shots']}:raise ViewportError('镜头不属于当前项目。')
    if not engine.blender_path():raise ViewportError('未找到 Blender，暂时无法准备实时预览。',503)
    source,guard,fingerprint=_signature(project,shot_id)
    ident=fingerprint[:32];folder=DATA/project_id/'viewport'/fingerprint
    if folder.is_symlink() or (DATA/project_id).resolve() not in folder.resolve().parents:
        raise ViewportError('实时预览缓存目录无效。')
    with _TASK_LOCK:
        prior=TASKS.get(ident)
        if prior and prior['status'] in ('queued','running'):return _public(prior)
        cached=_cached(folder,fingerprint)
        if not cached and sum(row['status'] in ('queued','running') for row in TASKS.values())>=MAX_ACTIVE:
            raise ViewportError('实时模型准备任务较多，请稍后重试。',429)
        now=time.time()
        item={'id':ident,'status':'ready' if cached else 'queued','project_id':project_id,'version':version,
              'shot_id':shot_id,'source_version':source['version'],'source_kind':source['kind'],'source_sha256':source['sha256'],
              'asset':(_asset(folder,cached) if cached else None),
              'error':None,'warnings':cached.get('warnings',[]) if cached else [],'created_at':now,'updated_at':now,
              '_folder':str(folder),'_guard':guard,'_fingerprint':fingerprint,'_project':project,'_source':source}
        if cached:_current(item)
        TASKS[ident]=item
        if len(TASKS)>128:
            for key,value in list(TASKS.items()):
                if len(TASKS)<=128:break
                if key!=ident and value['status'] not in ('queued','running'):TASKS.pop(key,None)
        if not cached:
            project_lifecycle.begin_operation(project_id)
            try:threading.Thread(target=_execute,args=(ident,),name='viewport-'+ident[:8],daemon=True).start()
            except Exception:
                TASKS.pop(ident,None);project_lifecycle.end_operation(project_id);raise
        return _public(item)


def read(project_id,ident):
    if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident):raise KeyError(ident)
    project=get('project',project_id)
    with _TASK_LOCK:
        item=TASKS.get(ident)
        if not item or item['project_id']!=project_id:raise KeyError(ident)
        if project['version']!=item['version'] or _digest(planner.validate(project['plan']))!=item['_guard']['plan']:
            return {**_public(item),'status':'stale','asset':None,'error':'方案已更新，正在等待当前版本的实时预览。'}
        result=_public(item)
    if result['status']=='ready':
        try:
            _current(item)
            if not _cached(Path(item['_folder']),item['_fingerprint']):
                raise ViewportError('实时预览资源已变化。',409)
        except (KeyError,ValueError,OSError):
            _update(ident,status='stale',asset=None,error='场景资源已变化，请重新打开实时预览。')
            return {**result,'status':'stale','asset':None,'error':'场景资源已变化，请重新打开实时预览。'}
    return result


def _current(item):
    project=get('project',item['project_id'])
    project['plan']=planner.validate(project['plan'])
    if project['version']!=item['version'] or _digest(project['plan'])!=item['_guard']['plan']:
        raise ViewportError('方案已变化，本次实时预览未采用。',409)
    source,guard,fingerprint=_signature(project,item['shot_id'])
    if fingerprint!=item['_fingerprint']:raise ViewportError('场景资源已变化，本次实时预览未采用。',409)
    return project


def _update(ident,**values):
    with _TASK_LOCK:
        item=TASKS.get(ident)
        if item:item.update(values,updated_at=time.time())


def _execute(ident):
    item=TASKS[ident];acquired=False
    try:
        acquired=_WORKERS.acquire(timeout=TIMEOUT)
        if not acquired:raise TimeoutError()
        _current(item);_update(ident,status='running')
        folder=Path(item['_folder']);folder.mkdir(parents=True,exist_ok=True)
        source=item['_source'];project=item['_project']
        request={'schema':SCHEMA,'request_id':ident,'source_path':source['path'],'source_sha256':source['sha256'],
                 'source_kind':source['kind'],'project_root':str((DATA/item['project_id']).resolve()),
                 'output_dir':str(folder.resolve()),'plan':copy.deepcopy(project['plan']),
                 'baseline_plan':source.get('baseline_plan'),'shot_id':item['shot_id'],
                 'product_object_names':source['product_object_names']}
        atomic_json(folder/'request.json',request)
        command=[engine.blender_path(),'-b','--factory-startup','--disable-autoexec','-t','2','--python-exit-code','1',
                 '--python',str(ROOT/'blender/export_viewport.py'),'--',str(folder/'request.json')]
        with (folder/'export.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=str(ROOT),
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            PROCESSES[ident]=process
            try:code=process.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                raise TimeoutError() from None
            finally:PROCESSES.pop(ident,None)
        result_path=folder/'result.json'
        if not result_path.is_file():raise RuntimeError('实时模型准备未完成；请先生成当前镜头预览后重试。')
        result=json.loads(result_path.read_text(encoding='utf-8'))
        if code or result.get('success') is not True:
            raise RuntimeError(result.get('error') or '实时模型准备未完成；请先生成当前镜头预览后重试。')
        if result.get('success') is not True or result.get('request_id')!=ident or result.get('source_sha256')!=source['sha256']:
            raise RuntimeError('实时预览来源未通过核验，请重新准备。')
        manifest=result.get('manifest')
        if not isinstance(manifest,dict) or manifest.get('schema')!=SCHEMA or not _glb(folder/'scene.glb'):
            raise RuntimeError('实时模型文件未通过校验。')
        _current(item)
        manifest.update(fingerprint=item['_fingerprint'],asset_sha256=_sha(folder/'scene.glb'),
                        source={'project_id':item['project_id'],'shot_id':item['shot_id'],'version':item['version'],
                                'source_version':item['source_version'],'kind':item['source_kind'],'sha256':source['sha256']})
        atomic_json(folder/'manifest.json',manifest)
        _update(ident,status='ready',warnings=manifest.get('warnings',[]),asset=_asset(folder,manifest))
    except (KeyError,ViewportError) as exc:
        _update(ident,status='stale',asset=None,error='方案或场景已变化，请重新打开实时预览。')
    except TimeoutError:
        _update(ident,status='failed',asset=None,error='准备实时模型超时，可先查看已保存成图后重试。')
    except Exception as exc:
        _update(ident,status='failed',asset=None,error=str(exc)[:400] if isinstance(exc,RuntimeError) else '实时模型准备失败，请先生成当前预览后重试。')
    finally:
        if acquired:_WORKERS.release()
        project_lifecycle.end_operation(item['project_id'])


def shutdown():
    for process in list(PROCESSES.values()):
        if process.poll() is None:process.terminate()

atexit.register(shutdown)

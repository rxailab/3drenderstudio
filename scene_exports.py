"""Snapshot-based native scene exports; no renderer success is inferred."""
import atexit
import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from studio_store import ROOT,DATA,LOCK,get,put,all_records,atomic_json

QUEUE=queue.Queue()
PROCESSES={}
RUNNING_EXPORTS=set()
C4D_STATE={'status':'unverified','message':'尚未验证 Cinema 4D 命令行授权'}

def c4d_path():
    candidates=[os.getenv('C4DPY_PATH'),shutil.which('c4dpy'),r'C:\Program Files\Maxon Cinema 4D R24\c4dpy.exe',r'C:\Program Files\Maxon Cinema 4D R22\c4dpy.exe']
    candidates += [str(p) for p in Path('C:/Program Files').glob('Maxon Cinema 4D*/c4dpy.exe')]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()),None)

def capabilities():
    from engine import blender_path,runtime_status
    exe=c4d_path()
    runtime=runtime_status()
    return {'blender_available':bool(blender_path()),'c4d_executable_found':bool(exe),
            'export_available':runtime['capabilities']['export_blend'],'c4d_export_available':runtime['capabilities']['export_c4d'],
            'c4d_status':C4D_STATE['status'] if exe else 'unavailable',
            'c4d_message':C4D_STATE['message'] if exe else '未找到 c4dpy；可配置 C4DPY_PATH，或下载转换包在 Cinema 4D 中保存原生工程。'}

def file_url(path):return '/files/'+Path(path).relative_to(DATA).as_posix()

def artifact_exists(url,project_id,suffix):
    if not isinstance(url,str) or not url.startswith('/files/'):return False
    path=(DATA/url[7:]).resolve()
    return (DATA/project_id).resolve() in path.parents and path.suffix.lower()==suffix and path.is_file()

def submit(project_id,body):
    fmt=body.get('format')
    if fmt not in ['blend','c4d']:raise ValueError('场景格式必须为 blend 或 c4d')
    with LOCK:
        p=get('project',project_id)
        matches=[o for o in p.get('outputs',[]) if o.get('job_id')==body.get('output_id') and o.get('shot_id')==body.get('shot_id') and o.get('quality')==body.get('quality') and o.get('version')==body.get('version')]
        if len(matches)!=1:raise ValueError('请选择一个确实已生成的镜头版本')
        o=matches[0]
        source_urls={'source_png_url':o.get('png_url'),'source_blend_url':o.get('blend_url')}
        if any(key in body and body[key]!=value for key,value in source_urls.items()):
            raise ValueError('所选镜头来源已变化，请刷新后重新生成场景文件')
        source=(DATA/o.get('blend_url','')[7:]).resolve()
        if (DATA/project_id).resolve() not in source.parents or source.suffix.lower()!='.blend' or not source.is_file():raise ValueError('所选镜头没有可用的 Blender 工程，请重新生成该镜头')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if ('source_sha256' in body and body['source_sha256']!=digest) or (o.get('blend_sha256') and o['blend_sha256']!=digest):
            raise ValueError('所选场景文件已变化，请重新生成该镜头后下载')
        scripts=[ROOT/'blender/export_scene.py']+([ROOT/'c4d/convert_scene.py'] if fmt=='c4d' else [])
        revision=hashlib.sha256(b''.join(f.read_bytes() for f in scripts if f.exists())).hexdigest()
        fingerprint=':'.join([project_id,fmt,str(o['job_id']),str(o['shot_id']),str(o['quality']),str(o['version']),digest,revision])
        for e in all_records('export'):
            if e.get('fingerprint')==fingerprint:
                reusable=e['status'] in ['queued','running'] or (
                    e['status']=='completed' and artifact_exists(e.get('download_url'),project_id,'.zip')
                    and artifact_exists(e.get('native_url'),project_id,'.'+fmt))
                if reusable:
                    # Upgrade legacy records only after the exact source identity
                    # and bytes have matched the original export fingerprint.
                    if any(e.get(key)!=value for key,value in source_urls.items()):
                        e.update(source_urls);put('export',e)
                    return e
        from engine import require_runtime
        require_runtime('export_'+fmt)
        item=dict(id=uuid.uuid4().hex,project_id=project_id,format=fmt,status='queued',stage='等待导出场景',error=None,warnings=[],
                  source_blend=str(source),source_sha256=digest,**source_urls,output_id=o['job_id'],shot_id=o['shot_id'],shot_label=o['shot_label'],quality=o['quality'],version=o['version'],
                  fingerprint=fingerprint,created_at=time.time(),download_url=None,native_url=None,conversion_package_url=None,log_tail=[])
        put('export',item);QUEUE.put(item['id']);return item

def update(ident,**fields):
    with LOCK:
        e=get('export',ident)
        if e['status']=='cancelled':return e
        e.update(fields);put('export',e);return e

def stop(ident):
    with LOCK:
        e=get('export',ident)
        if e['status'] in ['queued','running']:
            e.update(status='cancelled',stage='已取消场景导出');put('export',e)
            proc=PROCESSES.get(ident)
            if proc and proc.poll() is None:proc.terminate()
        return e

def run_process(item,command,log_path,timeout):
    started=time.monotonic()
    with log_path.open('w',encoding='utf-8') as log:
        proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        PROCESSES[item['id']]=proc
        try:
            while proc.poll() is None:
                if get('export',item['id'])['status']=='cancelled':proc.terminate();raise InterruptedError()
                if time.monotonic()-started>timeout:
                    proc.terminate();raise TimeoutError('执行超过 %s 秒；请检查软件授权与日志'%timeout)
                # c4dpy license prompts otherwise block a background task indefinitely.
                if 'c4d' in log_path.name:
                    text=log_path.read_text(encoding='utf-8',errors='replace')[-16000:]
                    if any(s in text for s in ['No License Model defined','Please select the license','Error running authentication']):
                        proc.terminate();C4D_STATE.update(status='license_required',message='Cinema 4D 命令行未配置可用授权，请在本机完成 c4dpy 授权后重试。')
                        raise RuntimeError(C4D_STATE['message'])
                time.sleep(.25)
            return proc.returncode
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            PROCESSES.pop(item['id'],None)
            log.flush()
            lines=log_path.read_text(encoding='utf-8',errors='replace').splitlines()
            update(item['id'],log_tail=lines[-20:])

def archive(folder,path,exclude=()):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for f in folder.rglob('*'):
            if f.is_file() and f.name not in exclude and f.suffix not in ['.zip','.blend1']:
                z.write(f,f.relative_to(folder))
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:raise RuntimeError('工程 ZIP 校验失败')

def read_result(path,code):
    if not path.is_file():raise RuntimeError('执行器没有生成结果文件（退出码 %s）'%code)
    result=json.loads(path.read_text(encoding='utf-8-sig'))
    if not result.get('success'):raise RuntimeError(result.get('error','场景导出失败'))
    if code:raise RuntimeError('执行器异常退出：%s'%code)
    return result

def execute(ident):
    with LOCK:
        try: item=get('export',ident)
        except KeyError: return
        if item['status']!='queued': return
        RUNNING_EXPORTS.add(ident)
    try:
        _execute(ident)
    finally:
        with LOCK: RUNNING_EXPORTS.discard(ident)

def _execute(ident):
    from engine import blender_path,require_runtime
    item=get('export',ident)
    if item['status']!='queued':return
    base=DATA/item['project_id']/'exports'/ident;base.mkdir(parents=True,exist_ok=True)
    package=base/'project';package.mkdir()
    update(ident,status='running',stage='读取镜头的已保存场景',started_at=time.time())
    try:
        require_runtime('export_'+item['format'])
        source=Path(item['source_blend'])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item['source_sha256']:raise RuntimeError('源工程在提交后发生变化，请重新提交导出')
        request_file=base/'request.json';atomic_json(request_file,{'blend_path':str(source),'output_dir':str(package)})
        exe=blender_path()
        if not exe:raise RuntimeError('未找到 Blender')
        command=[exe,'-b','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'blender/export_scene.py'),'--',str(request_file)]
        update(ident,stage='打包 Blender 场景、纹理并校验重开')
        code=run_process(item,command,base/'blender-export.log',600)
        result=read_result(package/'result.json',code)
        warnings=result.get('warnings',[])
        atomic_json(package/'export-source.json',{k:item[k] for k in ['project_id','shot_id','shot_label','quality','version','source_sha256']})
        native=package/'scene.blend'
        if item['format']=='c4d':
            shutil.copy2(ROOT/'c4d/convert_scene.py',package/'convert_scene.py')
            (package/'C4D-使用说明.txt').write_text('在 Cinema 4D 的脚本管理器中打开本目录 convert_scene.py 并执行。脚本读取旁边的 scene.json，并保存真正的 scene.c4d；保留 tex 目录。\n几何为当前姿态的可编辑静态快照；Blender 原工程保留绑定和动画。Cycles 节点/程序纹理/曝光与 C4D 渲染器并不等价，转换后的材质和灯光需要人工复核。不要将 FBX 改名为 C4D。\n',encoding='utf-8')
            fallback=base/'Cinema4D-conversion-kit.zip';archive(package,fallback)
            update(ident,conversion_package_url=file_url(fallback),package_url=file_url(fallback),warnings=warnings)
            exe=c4d_path()
            if not exe:raise RuntimeError('未找到 c4dpy，尚未生成原生 .c4d。可下载转换包，在已授权 Cinema 4D 脚本管理器中执行。')
            update(ident,stage='Cinema 4D 转换并验证原生工程')
            native=package/'scene.c4d';result_path=package/'c4d-result.json'
            command=[exe,str(ROOT/'c4d/convert_scene.py'),str(package/'scene.json'),str(native),str(result_path)]
            timeout=max(30,min(900,int(os.getenv('C4D_EXPORT_TIMEOUT','120'))))
            code=run_process(item,command,base/'c4d-export.log',timeout)
            result=read_result(result_path,code)
            if not native.is_file() or native.stat().st_size<100:raise RuntimeError('Cinema 4D 未输出有效的原生工程')
            warnings+=result.get('warnings',[])
            C4D_STATE.update(status='ready',message='已通过 Cinema 4D 原生保存与重新打开检查')
        (package/'README.txt').write_text('物影 Studio 场景工程\n镜头：%s / v%s / %s\n原生文件：%s\n请保留同目录的 tex 纹理资源。scene.blend 为原始完整 Blender 场景。scene.json/FBX 为交换快照，不代替 C4D 原生工程。\n\n转换注意事项：\n%s\n'%(item['shot_label'],item['version'],item['quality'],native.name,'\n'.join(warnings)),encoding='utf-8')
        bundle=base/(('Blender' if item['format']=='blend' else 'Cinema4D')+'-scene-v'+str(item['version'])+'.zip')
        archive(package,bundle)
        update(ident,status='completed',stage='场景工程导出完成',download_url=file_url(bundle),native_url=file_url(native),warnings=warnings,counts=result.get('counts',result.get('verified')),finished_at=time.time())
    except InterruptedError:pass
    except Exception as exc:
        update(ident,status='failed',stage='场景导出未完成',error=str(exc),finished_at=time.time())

def worker():
    while True:
        ident=QUEUE.get()
        try:execute(ident)
        except Exception as exc:
            try:update(ident,status='failed',error=str(exc),stage='场景导出异常')
            except KeyError:pass
        finally:QUEUE.task_done()

def shutdown():
    for proc in list(PROCESSES.values()):
        if proc.poll() is None:
            try:proc.terminate()
            except OSError:pass
atexit.register(shutdown)

def start():
    for e in all_records('export'):
        if e['status'] in ['queued','running']:e.update(status='interrupted',stage='应用重启，请重新导出',error='上次场景导出被中断；原始渲染和工程保留。');put('export',e)
    threading.Thread(target=worker,daemon=True,name='scene-export-worker').start()

"""Camera hooks around the installed complete worker; never an importer substitute.

Render and save callbacks measure the actual camera. Reopening the saved scene
proves the interactive view and final native file use the same projection.
"""
import copy
import json
from pathlib import Path
import re
import runpy
import sys

import bpy
from bpy.app.handlers import persistent

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from camera_controls import CONTRACT_VERSION,file_digest,shot_request,validate_report
from blender.camera_projection import apply_camera,products_for_request,same_snapshot,snapshot


def _write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def run(request_path):
    request_path=Path(request_path).resolve();request=json.loads(request_path.read_text(encoding='utf-8'))
    out=Path(request['output_dir']).resolve();result_path=out/'result.json'
    contract=request.get('camera_projection_contract',{})
    if (contract.get('version')!=CONTRACT_VERSION or not isinstance(contract.get('request_id'),str)
            or not re.fullmatch(r'[0-9a-f]{32}',contract['request_id'])
            or contract.get('shot')!=shot_request(request.get('shot',{}))
            or contract.get('margin')!=request.get('plan',{}).get('margin',.12)
            or contract.get('aspect')!=request.get('plan',{}).get('aspect','1:1')):
        raise RuntimeError('镜头投影执行契约无效。')
    if not Path(__file__).with_name('worker.py').is_file():
        raise RuntimeError('缺少原有 blender/worker.py，相机适配器不能代替完整渲染组件。')
    worker=Path(__file__).with_name('render_entry.py' if request.get('light_direction_contract') else 'worker.py')
    state={'errors':[],'apply_error':None,'pending':None,'completed':[],'saved':set()}

    def apply(scene):
        products=products_for_request(scene,request)
        aspect=contract['aspect']
        if aspect not in ('1:1','4:3','3:4'):raise ValueError('画幅不受支持')
        x,y=map(int,aspect.split(':'));long=max(scene.render.resolution_x,scene.render.resolution_y)
        scene.render.resolution_x=round(long*x/max(x,y));scene.render.resolution_y=round(long*y/max(x,y))
        return apply_camera(scene,request['shot'],products,contract['margin'])

    @persistent
    def render_pre(scene,*_):
        try:
            actual=apply(scene);path=Path(bpy.path.abspath(scene.render.filepath)).resolve()
            state['pending']={'actual':actual,'paths':{str(path),str(path.with_suffix('.png')),str(Path(scene.render.frame_path()).resolve())}}
            print('EVENT:'+json.dumps({'stage':'正交镜头已就绪，正在渲染' if actual['type']=='ORTHO' else '透视镜头已就绪，正在渲染'},ensure_ascii=False),flush=True)
        except Exception as exc:
            state['errors'].append(str(exc));state['apply_error']=str(exc);state['pending']=None;scene.camera=None

    @persistent
    def render_post(scene,*_):
        pending=state['pending']
        if not pending:return
        try:
            if not same_snapshot(pending['actual'],snapshot(scene)):
                raise RuntimeError('渲染期间实际镜头发生改变，无法核验出图。')
            state['completed'].append(copy.deepcopy(pending))
        except Exception as exc:state['errors'].append(str(exc))
        finally:state['pending']=None

    @persistent
    def save_pre(*_):
        try:apply(bpy.context.scene)
        except Exception:pass  # Preliminary saves are not accepted as evidence.

    @persistent
    def save_post(*_):
        path=Path(bpy.data.filepath).resolve()
        if path.parent==out and path.suffix.lower()=='.blend':state['saved'].add(str(path))

    handlers=((bpy.app.handlers.render_pre,render_pre),(bpy.app.handlers.render_post,render_post),
              (bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.save_post,save_post))
    for collection,handler in handlers:collection.append(handler)
    try:
        original_argv=sys.argv[:];sys.argv=[str(worker),*original_argv[1:]]
        try:
            try:runpy.run_path(str(worker),run_name='__main__')
            except SystemExit as exc:
                if exc.code not in (None,0):raise RuntimeError('原渲染组件退出：'+str(exc.code)) from exc
        except Exception as exc:
            if state['apply_error']:raise RuntimeError(state['apply_error']) from exc
            raise
        finally:sys.argv=original_argv
    finally:
        for collection,handler in handlers:
            if handler in collection:collection.remove(handler)
    if state['errors']:raise RuntimeError(state['errors'][0])
    if not result_path.is_file():raise RuntimeError('原渲染组件没有返回结果。')
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('error') or result.get('success') is False:raise RuntimeError(str(result.get('error','原渲染失败。')))
    png=next(out.glob('*.png'),None);blend=next(out.glob('*.blend'),None)
    if png is None or blend is None:raise RuntimeError('镜头核验需要实际 PNG 和保存的 Blender 场景。')
    completed=[entry for entry in state['completed'] if str(png.resolve()) in entry['paths']]
    if not completed:raise RuntimeError('原渲染组件未执行可核验的镜头钩子。')
    if str(blend.resolve()) not in state['saved']:raise RuntimeError('镜头缺少本次保存的完整 Blender 场景。')
    actual=completed[-1]['actual']
    bpy.ops.wm.open_mainfile(filepath=str(blend.resolve()),load_ui=False,use_scripts=False)
    if not same_snapshot(actual,snapshot(bpy.context.scene)):
        raise RuntimeError('保存的场景与实际出图的镜头不一致。')
    proof={'contract':contract,'actual':actual,'render_completed':True,'saved_scene_verified':True,
           'png_path':str(png.resolve()),'blend_path':str(blend.resolve()),
           'png_sha256':file_digest(png),'blend_sha256':file_digest(blend)}
    validate_report(proof,contract,out)
    result['camera_projection_verification']=proof
    result.setdefault('studio',{})['camera']=copy.deepcopy(actual)
    _write(result_path,result)


if __name__=='__main__':
    path=Path(sys.argv[sys.argv.index('--')+1])
    try:run(path)
    except Exception as exc:
        try:
            request=json.loads(path.read_text(encoding='utf-8'))
            _write(Path(request['output_dir'])/'result.json',{'success':False,'error':'镜头未完成执行验证：'+str(exc)})
        except Exception:pass
        raise

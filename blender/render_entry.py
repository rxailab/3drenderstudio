"""Apply requested light directions around an existing, complete worker.

This entry point is used only for direction requests. It never substitutes for
the application's original importer/renderer, and rejects unverified output.
"""
import copy
import json
from pathlib import Path
import re
import runpy
import sys

import bpy
from bpy.app.handlers import persistent

sys.path.insert(0,str(Path(__file__).resolve().parent))
from light_direction import (CONTRACT_VERSION,apply_scene,file_digest,inspect_scene,
                             requested_angles,snapshot_matches,validate_report)


def _write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def run(request_path):
    request_path=Path(request_path).resolve();request=json.loads(request_path.read_text(encoding='utf-8'))
    out=Path(request['output_dir']).resolve();result_path=out/'result.json'
    contract=request.get('light_direction_contract',{})
    if (contract.get('version')!=CONTRACT_VERSION or not isinstance(contract.get('request_id'),str)
            or not re.fullmatch(r'[0-9a-f]{32}',contract['request_id']) or not isinstance(contract.get('requested'),dict)
            or not contract['requested'] or requested_angles(contract['requested'])!=contract['requested']):
        raise RuntimeError('灯光方向执行契约无效。')
    worker=Path(__file__).resolve().with_name('worker.py')
    if not worker.is_file():raise RuntimeError('缺少原有 blender/worker.py，灯光方向适配器不能代替完整渲染组件。')
    state={'errors':[],'completed':[],'saved':set(),'pending':None,'originals':{}}

    def apply(scene):
        report=apply_scene(scene,request)
        for row in report['lights']:
            signature=(scene.as_pointer(),scene.objects[row['object_name']].as_pointer(),row['role'],
                       json.dumps([report['target']['point'],report['camera_frame']['matrix_world']]))
            state['originals'].setdefault(signature,copy.deepcopy(row['before']))
            row['before']=copy.deepcopy(state['originals'][signature])
        return report

    @persistent
    def render_pre(scene,*_):
        try:
            report=apply(scene)
            path=Path(bpy.path.abspath(scene.render.filepath)).resolve()
            candidates={str(path),str(path.with_suffix('.png')),str(Path(scene.render.frame_path()).resolve())}
            state['pending']={'report':report,'paths':candidates}
            print('EVENT:'+json.dumps({'stage':'已设置实际灯光角度，正在渲染'},ensure_ascii=False),flush=True)
        except Exception as exc:
            state['errors'].append(str(exc));state['pending']=None
            # Blender swallows Python handler exceptions. Removing the camera
            # makes the operator fail instead of rendering an unmodified image.
            scene.camera=None
            print('EVENT:'+json.dumps({'stage':'灯光方向未执行：'+str(exc)},ensure_ascii=False),flush=True)

    @persistent
    def render_post(scene,*_):
        pending=state['pending']
        if not pending:return
        try:
            actual=inspect_scene(scene,request)
            if not snapshot_matches(actual,pending['report']):
                raise RuntimeError('渲染期间实际灯光或相机被改动，灯光方向无法验证。')
            state['completed'].append(copy.deepcopy(pending))
        except Exception as exc:state['errors'].append(str(exc))
        finally:state['pending']=None

    @persistent
    def save_pre(*_):
        # Some complete workers save a preliminary scene before building lights.
        # Such a save is not evidence. The final saved scene is re-opened below.
        try:apply(bpy.context.scene)
        except Exception:pass

    @persistent
    def save_post(*_):
        path=Path(bpy.data.filepath).resolve()
        if path.parent==out and path.suffix.lower()=='.blend':state['saved'].add(str(path))

    handlers=((bpy.app.handlers.render_pre,render_pre),(bpy.app.handlers.render_post,render_post),
              (bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.save_post,save_post))
    for collection,handler in handlers:collection.append(handler)
    try:
        original_argv=sys.argv[:]
        sys.argv=[str(worker),*original_argv[1:]]
        try:
            try:runpy.run_path(str(worker),run_name='__main__')
            except SystemExit as exc:
                if exc.code not in (None,0):raise RuntimeError('原渲染组件退出：'+str(exc.code)) from exc
        except Exception as exc:
            if state['errors']:raise RuntimeError(state['errors'][0]) from exc
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
    if png is None or blend is None:raise RuntimeError('灯光方向验证需要实际 PNG 和保存的 Blender 场景。')
    completed=[entry for entry in state['completed'] if str(png.resolve()) in entry['paths']]
    if not completed:raise RuntimeError('原渲染组件未执行可核验的灯光方向钩子，无法确认出图使用了指定角度。')
    report=copy.deepcopy(completed[-1]['report'])
    if any(not row['after']['visible'] for row in report['lights']):
        raise RuntimeError('所调整灯光在实际出图时不可见，灯光方向不能记为已执行。')
    if str(blend.resolve()) not in state['saved']:
        raise RuntimeError('没有本次保存的完整场景快照，无法核验灯光方向。')
    bpy.ops.wm.open_mainfile(filepath=str(blend.resolve()),load_ui=False,use_scripts=False)
    snapshot=inspect_scene(bpy.context.scene,request)
    if not snapshot_matches(snapshot,report):
        raise RuntimeError('保存的场景与实际出图的灯光角度不一致，未收录结果。')
    report.update(version=CONTRACT_VERSION,request_id=contract['request_id'],requested=contract['requested'],
                  render_completed=True,saved_scene_verified=True,png_path=str(png.resolve()),blend_path=str(blend.resolve()),
                  png_sha256=file_digest(png),blend_sha256=file_digest(blend))
    validate_report(report,contract,out)
    result['light_direction_verification']=report
    result.setdefault('studio',{})['light_directions']=[{
        'role':row['role'],'object_name':row['object_name'],
        **{key:row['after'][key] for key in ('azimuth','elevation','position')},
        'target':report['target']['point'],'camera':report['camera_frame']['camera'],
    } for row in report['lights']]
    _write(result_path,result)


if __name__=='__main__':
    path=Path(sys.argv[sys.argv.index('--')+1])
    try:run(path)
    except Exception as exc:
        try:
            request=json.loads(path.read_text(encoding='utf-8'));result=Path(request['output_dir'])/'result.json'
            _write(result,{'success':False,'error':'灯光方向未完成执行验证：'+str(exc)})
        except Exception:pass
        raise

"""Read actual studio-light roles/directions without rendering or saving."""
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0,str(Path(__file__).resolve().parent))
from light_direction import CONTRACT_VERSION,inspect_scene


def inspect(request):
    source=Path(request['blend_path']).resolve()
    if source.suffix.lower()!='.blend' or not source.is_file():raise RuntimeError('当前预览的场景快照不存在。')
    bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
    result={'directions':[],'available_roles':[],'unavailable_roles':{}}
    for role in ('key','fill','rim'):
        scoped={**request,'light_direction_contract':{'version':CONTRACT_VERSION,
                'product_object_names':request.get('product_object_names',[]),
                'requested':{role+'_azimuth':0}}}
        try:
            snapshot=inspect_scene(bpy.context.scene,scoped);row=snapshot['lights'][0]
            if not row['state']['visible']:raise RuntimeError('该角色的光源在当前场景中不可见。')
            result['directions'].append({'role':role,'object_name':row['object_name'],
                **{key:row['state'][key] for key in ('azimuth','elevation','position')},
                **{key:row['state']['light_data'][key] for key in ('type','energy','size','size_y','shape')
                   if key in row['state']['light_data']},
                'target':snapshot['target']['point'],'camera':snapshot['camera_frame']['camera']})
            result['available_roles'].append(role)
        except (RuntimeError,ValueError,KeyError) as exc:
            result['unavailable_roles'][role]=str(exc)
    return result


if __name__=='__main__':
    request=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8'))
    try:result={'success':True,**inspect(request)}
    except Exception as exc:result={'success':False,'error':str(exc)}
    Path(request['result_path']).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    if not result['success']:raise RuntimeError(result['error'])

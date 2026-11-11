"""Bounded, camera-relative studio-light direction controls.

The module deliberately imports bpy only inside scene functions so the service
can validate the same request/report contract without importing Blender.
"""
import hashlib
import json
import math
import re
from pathlib import Path


CONTRACT_VERSION = 1
ANGLE_FIELDS = {role+'_'+axis: (-180,180) if axis=='azimuth' else (0,85)
                for role in ('key','fill','rim') for axis in ('azimuth','elevation')}


def requested_angles(lighting):
    result={key:lighting[key] for key in ANGLE_FIELDS if key in lighting}
    for key,value in result.items():
        lo,hi=ANGLE_FIELDS[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError('灯光角度参数无效：'+key)
    return result


def file_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _close(a,b,tolerance=1e-4):
    if isinstance(a,(list,tuple)) and isinstance(b,(list,tuple)):
        return len(a)==len(b) and all(_close(x,y,tolerance) for x,y in zip(a,b))
    return type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b) and abs(a-b)<=tolerance


def _angle_close(a,b):
    return type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b) and abs((a-b+180)%360-180)<=1e-3


def validate_report(report,contract,out):
    """Require proof from this invocation and the exact published artifacts."""
    out=Path(out).resolve()
    if (not isinstance(report,dict) or report.get('version')!=CONTRACT_VERSION
            or contract.get('version')!=CONTRACT_VERSION
            or not isinstance(report.get('request_id'),str)
            or not re.fullmatch(r'[0-9a-f]{32}',report['request_id'])
            or report.get('request_id')!=contract.get('request_id')
            or report.get('requested')!=contract.get('requested')
            or report.get('saved_scene_verified') is not True
            or report.get('render_completed') is not True):
        raise RuntimeError('灯光方向没有获得本次真实渲染和场景快照的执行验证，未收录结果。')
    rows=report.get('lights')
    if not isinstance(rows,list):raise RuntimeError('灯光方向执行记录缺失。')
    by_role={row.get('role'):row for row in rows if isinstance(row,dict)}
    expected_roles={key.split('_')[0] for key in contract['requested']}
    if len(by_role)!=len(rows) or set(by_role)!=expected_roles:raise RuntimeError('灯光角色的执行记录不完整。')
    for key,value in contract['requested'].items():
        role,axis=key.split('_');row=by_role[role]
        if not _angle_close(row.get('after',{}).get(axis),value):
            raise RuntimeError('灯光角度未按本次要求执行：'+key)
        before,after=row.get('before',{}),row.get('after',{})
        if (not _close(before.get('distance'),after.get('distance'))
                or before.get('light_data')!=after.get('light_data')):
            raise RuntimeError('灯光方向调整意外改变了灯光距离或光源属性。')
        other='elevation' if axis=='azimuth' else 'azimuth'
        if role+'_'+other not in contract['requested'] and not _angle_close(before.get(other),after.get(other)):
            raise RuntimeError('灯光方向调整改变了未要求的另一轴。')
    for field,suffix in (('png_path','.png'),('blend_path','.blend')):
        path=Path(report.get(field,'')).resolve()
        published=next(out.glob('*'+suffix),None)
        if (path.parent!=out or path.suffix.lower()!=suffix or not path.is_file()
                or published is None or published.resolve()!=path
                or file_digest(path)!=report.get(field.removesuffix('_path')+'_sha256')):
            raise RuntimeError('灯光方向记录与实际输出文件不一致，未收录结果。')
    return report


def _point(value):
    return [float(v) for v in value]


def _evaluated_center(objects,graph):
    from mathutils import Vector
    points=[]
    for obj in objects:
        evaluated=obj.evaluated_get(graph)
        if evaluated.type=='MESH' and len(evaluated.data.vertices):
            points.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
        else:
            points.append(evaluated.matrix_world.translation.copy())
    if not points:raise RuntimeError('无法确认产品的真实中心，灯光方向尚未执行。')
    return Vector([(min(v[axis] for v in points)+max(v[axis] for v in points))/2 for axis in range(3)])


def _target(scene,camera,request,graph):
    """Use exact camera/product evidence; never include an unlabelled floor."""
    from mathutils import Vector
    shot=request.get('shot',{});context=request['light_direction_contract']
    target_name=shot.get('target_object')
    if target_name:
        obj=scene.objects.get(target_name)
        if not obj:raise RuntimeError('当前镜头的明确目标不存在，灯光方向尚未执行。')
        return _evaluated_center([obj],graph),{'source':'shot.target_object','objects':[obj.name]}
    explicit=scene.get('wuying_product_target')
    if explicit is not None:
        if isinstance(explicit,str):
            obj=scene.objects.get(explicit)
            if not obj:raise RuntimeError('场景标记的产品目标不存在。')
            return _evaluated_center([obj],graph),{'source':'scene.wuying_product_target','objects':[obj.name]}
        try:point=list(explicit)
        except TypeError:point=[]
        if len(point)==3 and all(type(v) in (int,float) and math.isfinite(v) for v in point):
            return Vector(point),{'source':'scene.wuying_product_target','objects':[]}
        raise RuntimeError('场景产品目标标记无效。')
    targets={constraint.target for constraint in camera.constraints
             if not constraint.mute and constraint.type in ('TRACK_TO','DAMPED_TRACK','LOCKED_TRACK')
             and constraint.target is not None}
    if len(targets)==1:
        obj=next(iter(targets))
        if any(getattr(constraint,'subtarget','') for constraint in camera.constraints
               if not constraint.mute and constraint.type in ('TRACK_TO','DAMPED_TRACK','LOCKED_TRACK')):
            raise RuntimeError('相机使用骨骼子目标，当前灯光方向无法独立核验其中心。')
        return obj.evaluated_get(graph).matrix_world.translation.copy(),{'source':'camera.track_target','objects':[obj.name]}
    if len(targets)>1:raise RuntimeError('相机存在多个不一致的目标，无法确定灯光方向中心。')
    focus=camera.data.dof.focus_object if camera.data.dof.use_dof else None
    if focus:return focus.evaluated_get(graph).matrix_world.translation.copy(),{'source':'camera.focus_object','objects':[focus.name]}
    names=context.get('product_object_names',[])
    objects=[scene.objects.get(name) for name in names]
    if not names or any(obj is None for obj in objects):
        raise RuntimeError('缺少明确的产品对象或相机目标，无法安全设置灯光角度；需要完整渲染组件中的目标标识。')
    objects=[obj for obj in objects if obj.type=='MESH']
    if not objects:raise RuntimeError('已确认的产品对象中没有可测量表面。')
    return _evaluated_center(objects,graph),{'source':'analysis.product_objects','objects':[obj.name for obj in objects]}


def _frame(camera,target):
    from mathutils import Vector
    up=Vector((0,0,1));front=camera.matrix_world.translation-target;front.z=0
    if front.length<1e-6:
        right=camera.matrix_world.to_quaternion() @ Vector((1,0,0));right.z=0
        if right.length<1e-6:raise RuntimeError('相机方向无法建立稳定的水平灯光坐标。')
        right.normalize();front=right.cross(up)
    else:
        front.normalize();right=up.cross(front).normalized()
    return front,right,up


def _role(obj):
    tagged=[]
    for key in ('wuying_light_role','studio_role'):
        if key in obj:
            value=obj[key]
            tagged.append(value)
    if tagged:
        if not any(value in ('key','fill','rim') for value in tagged):return None
        if len(set(tagged))!=1:raise RuntimeError('光源角色标记冲突：'+obj.name)
        return tagged[0]
    name=re.sub(r'\.\d{3}$','',obj.name.casefold()).strip()
    aliases={role:{role,role+' light',role+'_light',role+'light','studio_'+role,'studio '+role,
                   'studio '+role+' light','wuying_'+role,'wuying '+role} for role in ('key','fill','rim')}
    aliases['key'].update(('主光','主光灯'));aliases['fill'].update(('补光','补光灯'));aliases['rim'].update(('轮廓光','轮廓灯'))
    # Exact lamp names verified in the supplied saved studio scenes. Keep this
    # explicit: arbitrary objects containing "key" are not role evidence.
    aliases['key'].update(('key softbox','hero | main softbox'))
    aliases['fill'].update(('fill softbox','hero | front fill'))
    aliases['rim'].update(('edge softbox','rim softbox','hero | upper edge'))
    return next((role for role,names in aliases.items() if name in names),None)


def _light_data(obj):
    light=obj.data
    result={'type':light.type,'energy':float(light.energy),'color':_point(light.color)}
    for key in ('size','size_y','shape','shadow_soft_size','spot_size','spot_blend'):
        if hasattr(light,key):result[key]=getattr(light,key)
    return result


def _measure(obj,target,frame,graph):
    import bpy
    front,right,up=frame;matrix=obj.evaluated_get(graph).matrix_world
    position=matrix.translation;offset=position-target;distance=offset.length
    if distance<1e-6:raise RuntimeError('光源与产品中心重合，无法设置方向：'+obj.name)
    return {'azimuth':math.degrees(math.atan2(offset.dot(right),offset.dot(front))),
            'elevation':math.degrees(math.atan2(offset.dot(up),math.hypot(offset.dot(front),offset.dot(right)))),
            'distance':float(distance),'position':_point(position),'matrix_world':[_point(row) for row in matrix],
            'light_data':_light_data(obj),'visible':_render_visible(obj,bpy.context.scene,bpy.context.view_layer)}


def _render_visible(obj,scene,view_layer):
    if obj.hide_render or obj.name not in view_layer.objects:return False
    def linked_visible(collection,hidden=False):
        hidden=hidden or collection.hide_render
        if not hidden and obj.name in collection.objects:return True
        return any(linked_visible(child,hidden) for child in collection.children)
    return linked_visible(scene.collection)


def inspect_scene(scene,request):
    import bpy
    if scene!=bpy.context.scene:raise RuntimeError('当前渲染场景与执行上下文不一致，无法验证灯光方向。')
    camera=scene.camera
    if camera is None or camera.type!='CAMERA':raise RuntimeError('当前场景没有明确的渲染相机。')
    bpy.context.view_layer.update();graph=bpy.context.evaluated_depsgraph_get()
    evaluated_camera=camera.evaluated_get(graph)
    target,provenance=_target(scene,camera,request,graph);frame=_frame(evaluated_camera,target)
    requested=request['light_direction_contract']['requested'];wanted={key.split('_')[0] for key in requested}
    candidates={role:[] for role in wanted}
    product=set(request['light_direction_contract'].get('product_object_names',[]))
    for obj in scene.objects:
        if obj.type!='LIGHT' or obj.name in product:continue
        role=_role(obj)
        if role in candidates:candidates[role].append(obj)
    for role,values in candidates.items():
        if len(values)!=1:raise RuntimeError('无法唯一确认 '+role+' 光源；请保留明确的 Key/Fill/Rim 名称或 studio_role 标记，灯光角度未执行。')
    lights=[{'role':role,'object_name':values[0].name,'data_name':values[0].data.name,
             'state':_measure(values[0],target,frame,graph)} for role,values in sorted(candidates.items())]
    return {'target':{'point':_point(target),**provenance},
            'camera_frame':{'camera':camera.name,'position':_point(evaluated_camera.matrix_world.translation),
                            'front':_point(frame[0]),'right':_point(frame[1]),'up':_point(frame[2]),
                            'matrix_world':[_point(row) for row in evaluated_camera.matrix_world]},'lights':lights}


def apply_scene(scene,request):
    import bpy
    from mathutils import Matrix,Vector
    before=inspect_scene(scene,request);target=Vector(before['target']['point'])
    frame=before['camera_frame'];front,right,up=(Vector(frame[key]) for key in ('front','right','up'))
    requested=request['light_direction_contract']['requested']
    for row in before['lights']:
        role=row['role'];state=row['state'];obj=scene.objects[row['object_name']]
        azimuth=math.radians(requested.get(role+'_azimuth',state['azimuth']))
        elevation=math.radians(requested.get(role+'_elevation',state['elevation']))
        position=target+state['distance']*((front*math.cos(azimuth)+right*math.sin(azimuth))*math.cos(elevation)+up*math.sin(elevation))
        orientation=(target-position).to_track_quat('-Z','Y')
        obj.matrix_world=Matrix.LocRotScale(position,orientation,obj.matrix_world.to_scale())
    bpy.context.view_layer.update();after=inspect_scene(scene,request)
    result={'target':after['target'],'camera_frame':after['camera_frame'],'lights':[]}
    for old,new in zip(before['lights'],after['lights']):
        if old['object_name']!=new['object_name']:raise RuntimeError('执行期间光源对象已变化。')
        for axis in ('azimuth','elevation'):
            wanted=requested.get(old['role']+'_'+axis,old['state'][axis])
            if not _angle_close(new['state'][axis],wanted):raise RuntimeError('光源约束或驱动阻止了角度执行：'+old['object_name'])
        if not _close(old['state']['distance'],new['state']['distance']) or old['state']['light_data']!=new['state']['light_data']:
            raise RuntimeError('光源方向调整未能保留距离或光源属性。')
        result['lights'].append({'role':old['role'],'object_name':old['object_name'],'data_name':old['data_name'],
                                 'before':old['state'],'after':new['state']})
    return result


def snapshot_matches(snapshot,render):
    if not _close(snapshot['target']['point'],render['target']['point']):return False
    if not _close(snapshot['camera_frame']['matrix_world'],render['camera_frame']['matrix_world']):return False
    observed={row['role']:row for row in snapshot['lights']}
    for row in render['lights']:
        actual=observed.get(row['role'])
        if not actual or actual['object_name']!=row['object_name']:return False
        expected=row['after'];state=actual['state']
        if (not _close(state['matrix_world'],expected['matrix_world']) or state['light_data']!=expected['light_data']
                or state['visible']!=expected['visible']):return False
    return True

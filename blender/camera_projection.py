"""Actual product camera fitting shared by the worker and native snapshots."""
import json
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from camera_controls import projection,validate_shot


def world_points(products):
    import bpy
    import numpy as np
    graph=bpy.context.evaluated_depsgraph_get();parts=[]
    for obj in products:
        evaluated=obj.evaluated_get(graph);mesh=evaluated.to_mesh()
        try:
            if not mesh or not mesh.vertices:continue
            values=np.empty(len(mesh.vertices)*3,dtype=np.float64);mesh.vertices.foreach_get('co',values)
            homogeneous=np.column_stack((values.reshape((-1,3)),np.ones(len(mesh.vertices))))
            parts.append((np.asarray(evaluated.matrix_world,dtype=np.float64)@homogeneous.T).T[:,:3])
        finally:evaluated.to_mesh_clear()
    if not parts:raise ValueError('没有可测量的产品网格，无法设置正交镜头')
    return np.concatenate(parts)


def products_for_request(scene,request):
    names=request.get('camera_projection_contract',{}).get('product_object_names',[])
    if not names:
        names=scene.get('studio_product_objects',[])
        if isinstance(names,str):
            try:names=json.loads(names)
            except ValueError:names=[]
    if not names and scene.camera and scene.camera.data.dof.use_dof:
        focus=scene.camera.data.dof.focus_object
        if focus and focus.type=='MESH':names=[focus.name]
    if not isinstance(names,list) or not names or any(not isinstance(name,str) for name in names):
        raise ValueError('缺少明确的产品对象登记，无法核验正交镜头')
    objects=[scene.objects.get(name) for name in names]
    if any(obj is None or obj.type!='MESH' for obj in objects):
        raise ValueError('产品对象登记与当前场景不一致')
    scene['studio_product_objects']=json.dumps(names,ensure_ascii=False)
    return objects


def snapshot(scene):
    camera=scene.camera
    if camera is None or camera.type!='CAMERA':raise ValueError('当前场景缺少实际相机')
    data=camera.data
    return {'name':camera.name,'type':data.type,'sensor_fit':data.sensor_fit,
            'sensor_width':float(data.sensor_width),'sensor_height':float(data.sensor_height),
            'ortho_scale':float(data.ortho_scale),'focal_length':float(data.lens),
            'shift_x':float(data.shift_x),'shift_y':float(data.shift_y),
            'clip_start':float(data.clip_start),'clip_end':float(data.clip_end),
            'matrix_world':[[float(value) for value in row] for row in camera.matrix_world]}


def same_snapshot(before,after):
    if set(before)!=set(after):return False
    def same(a,b):
        if type(a) in (int,float) and type(b) in (int,float):
            return math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=2e-6,abs_tol=2e-6)
        if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
        return a==b
    return all(same(before[key],after[key]) for key in before)


def apply_camera(scene,shot,products,margin=.12,points=None):
    """Apply a complete view. Explicit scale and pan never trigger auto-fit."""
    import bpy
    import numpy as np
    from mathutils import Vector,Matrix
    validate_shot(shot)
    points=world_points(products) if points is None else points
    low=points.min(axis=0);high=points.max(axis=0);center=Vector((low+high)/2)
    diagonal=max(float(np.linalg.norm(high-low)),1e-6)
    framing=points;target=center.copy()
    if shot.get('kind')=='detail' and shot.get('target_object'):
        selected=[obj for obj in products if obj.name==shot['target_object']]
        if not selected:raise ValueError('细节镜头目标不属于当前产品')
        framing=world_points(selected);target=Vector((framing.min(axis=0)+framing.max(axis=0))/2)
    camera=scene.camera
    if camera is None or camera.type!='CAMERA':raise ValueError('当前场景缺少实际相机')
    if camera.constraints or camera.parent or camera.animation_data:
        # A derived rendering camera leaves the imported rig and product intact.
        source=camera;camera=bpy.data.objects.new('Wuying view camera',source.data.copy())
        scene.collection.objects.link(camera);camera.matrix_world=source.matrix_world.copy();scene.camera=camera
        camera['wuying_source_camera']=source.name
    data=camera.data
    desired='ORTHO' if projection(shot)=='orthographic' else 'PERSP'
    data.type=desired;data.sensor_fit='HORIZONTAL';data.sensor_width=36;data.shift_x=0;data.shift_y=0
    data.dof.use_dof=False
    old_direction=camera.matrix_world.to_quaternion() @ Vector((0,0,1))
    az=math.radians(shot.get('azimuth',math.degrees(math.atan2(old_direction.x,-old_direction.y))))
    el=math.radians(shot.get('elevation',math.degrees(math.atan2(old_direction.z,math.hypot(old_direction.x,old_direction.y)))))
    direction=Vector((math.sin(az)*math.cos(el),-math.cos(az)*math.cos(el),math.sin(el)))
    rotation=(-direction).to_track_quat('-Z','Y')
    right=rotation @ Vector((1,0,0));up=rotation @ Vector((0,1,0))
    x=framing@np.asarray(right);y=framing@np.asarray(up)
    aspect=(scene.render.resolution_x*scene.render.pixel_aspect_x)/(scene.render.resolution_y*scene.render.pixel_aspect_y)
    occupancy=max(.05,min(.98,1-2*margin))
    if 'target_offset' in shot:target=center+Vector(shot['target_offset'])*diagonal
    elif (desired=='ORTHO' and 'ortho_scale' in shot) or (desired=='PERSP' and 'camera_distance' in shot):
        # A manual zoom changes scale around the current view target. It does
        # not silently undo an earlier pan when only the zoom field changes.
        previous=camera.get('wuying_target_offset')
        if previous is not None and len(previous)==3:target=center+Vector(previous)*diagonal
    elif desired=='ORTHO':
        target+=right*((float(x.min()+x.max())/2)-target.dot(right))+up*((float(y.min()+y.max())/2)-target.dot(up))
    distance=max((camera.matrix_world.translation-target).length,diagonal*2)
    data.lens=shot.get('focal_length',data.lens)
    if desired=='ORTHO':
        data.ortho_scale=shot.get('ortho_scale',max(float(x.max()-x.min()),float(y.max()-y.min())*aspect)/occupancy)
    else:
        # Fit perspective conservatively using each vertex's actual depth.
        tan_x=data.sensor_width/(2*data.lens);tan_y=tan_x/aspect
        delta=framing-np.asarray(target);depth=delta@np.asarray(direction)
        distance=shot.get('camera_distance',max(float(np.max(depth+np.abs(delta@np.asarray(right))/(tan_x*occupancy))),
                     float(np.max(depth+np.abs(delta@np.asarray(up))/(tan_y*occupancy))),diagonal*.6))
    camera.matrix_world=Matrix.LocRotScale(target+direction*distance,rotation,Vector((1,1,1)))
    data.clip_start=max(diagonal*.00001,.000001);data.clip_end=max(distance+diagonal*20,data.clip_start*100)
    camera['wuying_target_offset']=[float(v) for v in (target-center)/diagonal]
    camera['wuying_camera_distance']=float(distance)
    scene['wuying_product_target']=[float(v) for v in center]
    bpy.context.view_layer.update()
    return snapshot(scene)

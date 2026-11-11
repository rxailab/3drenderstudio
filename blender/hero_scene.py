"""Inspect and photograph an existing scene without rebuilding its product.

The input is validated data, never generated Python. Run under Blender with
--factory-startup --disable-autoexec --python hero_scene.py -- request.json.
Source files are read-only; only the requested output directory is written.
"""
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hero_state import SCENE_STATE_VERSION,compare_scene_states,difference_summary,state_digest

SCHEMA_VERSION=1
SURFACE_INPUTS={
    'roughness':'Roughness','metallic':'Metallic','specular':'Specular IOR Level',
    'coat_weight':'Coat Weight','coat_roughness':'Coat Roughness','anisotropic':'Anisotropic',
    'transmission':'Transmission Weight',
}
GROUND_NAMES={'Studio floor','StudioFloor','Studio Floor','WuyingHero Ground'}
ROLE_ALIASES={
    'key':{'key','keylight','key light','key softbox','hero | main softbox','主光'},
    'fill':{'fill','filllight','fill light','fill softbox','hero | front fill','补光'},
    'rim':{'rim','rimlight','rim light','edge softbox','hero | upper edge','轮廓光'},
}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def file_digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()

def number(value,lo,hi,name):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError(name+' 超出可执行范围')
    return float(value)

def vector(values):return [round(float(v),7) for v in values]
def matrix(value):return [vector(row) for row in value]

def _visible(obj,scene):
    import bpy
    if obj.hide_render or obj.name not in bpy.context.view_layer.objects:return False
    def visible(collection,hidden=False):
        hidden=hidden or collection.hide_render
        return (not hidden and obj.name in collection.objects) or any(visible(child,hidden) for child in collection.children)
    return visible(scene.collection)

def _products(scene,provided):
    registered=scene.get('studio_product_objects')
    if isinstance(registered,str):
        try:registered=json.loads(registered)
        except ValueError:raise ValueError('场景的产品对象登记无效')
    names=registered if registered else provided
    if not isinstance(names,(list,tuple)) or not names or len(names)>10000:
        raise ValueError('缺少明确的产品对象登记，不能把背景或地面当作产品自动修改')
    if any(not isinstance(name,str) or not name or name not in scene.objects for name in names) or len(set(names))!=len(names):
        raise ValueError('产品对象登记包含缺失或重复对象')
    objects=[scene.objects[name] for name in sorted(names)]
    if any(obj.type not in ('MESH','CURVE','FONT','SURFACE','META') or obj.name in GROUND_NAMES or obj.get('wuying_ground') is True for obj in objects):
        raise ValueError('产品对象登记中包含不能核验的对象或地面')
    if not any(obj.type=='MESH' and _visible(obj,scene) for obj in objects):
        raise ValueError('当前产品没有可渲染的网格')
    return objects

def _world_vertices(objects,visible_only=True):
    import bpy
    import numpy as np
    scene=bpy.context.scene;graph=bpy.context.evaluated_depsgraph_get();parts=[]
    for obj in objects:
        if visible_only and not _visible(obj,scene):continue
        evaluated=obj.evaluated_get(graph)
        mesh=evaluated.to_mesh()
        try:
            if not mesh or not len(mesh.vertices):continue
            co=np.empty(len(mesh.vertices)*3,dtype=np.float64);mesh.vertices.foreach_get('co',co)
            co=co.reshape((-1,3));co=np.column_stack((co,np.ones(len(co))))
            parts.append((np.asarray(evaluated.matrix_world,dtype=np.float64)@co.T).T[:,:3])
        finally:evaluated.to_mesh_clear()
    if not parts:raise ValueError('产品没有可测量的表面')
    return np.concatenate(parts)

def _bounds(points):
    lo=points.min(axis=0);hi=points.max(axis=0)
    return {'min':vector(lo),'max':vector(hi),'center':vector((lo+hi)/2),
            'dimensions':vector(hi-lo),'max_span':float((hi-lo).max())}

def _projection(scene,points):
    import bpy
    import numpy as np
    camera=scene.camera
    projection=camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=scene.render.resolution_x,
        y=scene.render.resolution_y,scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)
    clip=np.asarray(projection@camera.matrix_world.inverted(),dtype=np.float64)@np.column_stack((points,np.ones(len(points)))).T
    valid=clip[3]>1e-7
    if not valid.all():
        return {'kind':'complete_product','clipped':True,'occupancy':None,'bbox':None,'behind_camera':int((~valid).sum())}
    xy=(clip[:2]/clip[3]+1)/2
    lo=xy.min(axis=1);hi=xy.max(axis=1)
    return {'kind':'complete_product','clipped':bool((lo<0).any() or (hi>1).any()),
            'occupancy':float((hi-lo).max()),'occupied_width':float(hi[0]-lo[0]),'occupied_height':float(hi[1]-lo[1]),
            'bbox':[float(lo[0]),float(lo[1]),float(hi[0]),float(hi[1])],'method':'evaluated-product-vertices-v1'}

def _primitive(value):
    if value is None or type(value) in (bool,int,str):return value
    if isinstance(value,float):return round(value,7)
    if hasattr(value,'name'):return {'name':value.name,'type':type(value).__name__}
    try:return [_primitive(v) for v in value]
    except TypeError:return str(type(value).__name__)

def _nodes(tree):
    if not tree:return None
    result=[]
    for node in sorted(tree.nodes,key=lambda n:n.name):
        row={'name':node.name,'type':node.bl_idname,'mute':node.mute,
             'inputs':[(socket.name,_primitive(socket.default_value)) for socket in node.inputs if hasattr(socket,'default_value')]}
        if hasattr(node,'operation'):row['operation']=node.operation
        if hasattr(node,'color_ramp'):row['ramp']=[(round(e.position,7),vector(e.color)) for e in node.color_ramp.elements]
        if getattr(node,'image',None):row['image']={'name':node.image.name,'filepath':node.image.filepath}
        result.append(row)
    return {'nodes':result,'links':sorted((link.from_node.name,link.from_socket.identifier,link.to_node.name,link.to_socket.identifier) for link in tree.links)}

def _principled(material):
    if not material or not material.node_tree:return None
    nodes=[node for node in material.node_tree.nodes if node.type=='BSDF_PRINCIPLED']
    return nodes[0] if len(nodes)==1 else None

def _material_groups(objects):
    grouped={}
    for obj in objects:
        for index,slot in enumerate(obj.material_slots):
            material=slot.material
            if material is not None:grouped.setdefault(material,[]).append((obj.name,index))
    ordered=sorted(grouped.items(),key=lambda item:sorted(item[1]))
    return [('m'+str(index+1),material,sorted(bindings)) for index,(material,bindings) in enumerate(ordered)]

def _protected(material,bindings):
    node=_principled(material)
    if not node:return True,'复杂或无可确认的 Principled 表面'
    names=' '.join([material.name]+[name for name,_ in bindings])
    if re.search(r'logo|label|decal|brand|标识|商标|标签|铭牌|文字',names,re.I):return True,'保留品牌标识与文字'
    tree=material.node_tree
    if any(n.type in ('TEX_IMAGE','GROUP') for n in tree.nodes):return True,'保留贴图与节点组'
    base=node.inputs.get('Base Color')
    if base is None or base.is_linked:return True,'保留颜色节点连接'
    outputs=[n for n in tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    if len(outputs)!=1:return True,'表面输出不唯一'
    links=list(outputs[0].inputs['Surface'].links)
    if len(links)!=1 or links[0].from_node!=node:return True,'保留复杂表面网络'
    return False,''

def _fingerprints(objects):
    import bpy
    import numpy as np
    geometry=hashlib.sha256();transforms=[];colors=[];resources=[];labels=[]
    graph=bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        geometry.update(obj.name.encode());geometry.update(obj.type.encode())
        geometry.update(digest({'hide_render':obj.hide_render,'visible_camera':getattr(obj,'visible_camera',True),
                                'render_visible':_visible(obj,bpy.context.scene)}).encode())
        evaluated=obj.evaluated_get(graph);mesh=evaluated.to_mesh()
        try:
            coordinates=np.empty(len(mesh.vertices)*3,dtype=np.float32);mesh.vertices.foreach_get('co',coordinates)
            loops=np.empty(len(mesh.loops),dtype=np.int32);mesh.loops.foreach_get('vertex_index',loops)
            sizes=np.empty(len(mesh.polygons),dtype=np.int32);mesh.polygons.foreach_get('loop_total',sizes)
            assignments=np.empty(len(mesh.polygons),dtype=np.int32);mesh.polygons.foreach_get('material_index',assignments)
            geometry.update(coordinates.tobytes());geometry.update(loops.tobytes());geometry.update(sizes.tobytes());geometry.update(assignments.tobytes())
            for layer in mesh.uv_layers:
                uv=np.empty(len(layer.data)*2,dtype=np.float32);layer.data.foreach_get('uv',uv)
                geometry.update(layer.name.encode());geometry.update(uv.tobytes())
        finally:evaluated.to_mesh_clear()
        transforms.append((obj.name,matrix(obj.matrix_world),obj.parent.name if obj.parent else None))
        if obj.type=='FONT':labels.append((obj.name,obj.data.body))
        for index,slot in enumerate(obj.material_slots):
            mat=slot.material;binding=[obj.name,index]
            if not mat:
                colors.append([binding,None]);resources.append([binding,None]);continue
            color_nodes=[];resource_nodes=[]
            if mat.node_tree:
                for node in sorted(mat.node_tree.nodes,key=lambda n:n.name):
                    values=[(socket.name,vector(socket.default_value)) for socket in node.inputs
                            if hasattr(socket,'default_value') and socket.type=='RGBA']
                    ramp=[(round(e.position,7),vector(e.color)) for e in node.color_ramp.elements] if hasattr(node,'color_ramp') else None
                    if values or ramp:color_nodes.append([node.name,node.bl_idname,values,ramp])
                    image=getattr(node,'image',None)
                    if image:
                        packed=getattr(image,'packed_file',None)
                        packed_hash=hashlib.sha256(bytes(packed.data)).hexdigest() if packed else None
                        resource_nodes.append([node.name,image.name,image.filepath,packed_hash,list(image.size)])
                    if node.type=='GROUP':resource_nodes.append([node.name,'GROUP',_nodes(node.node_tree)])
                structure=_nodes(mat.node_tree)
                for node in structure['nodes']:
                    if node['type']=='ShaderNodeBsdfPrincipled':
                        adjustable={'Base Color',*SURFACE_INPUTS.values()}
                        node['inputs']=[item for item in node['inputs'] if item[0] not in adjustable]
                resource_nodes.append(['shader_structure',structure])
            else:color_nodes=[['diffuse',vector(mat.diffuse_color)]]
            colors.append([binding,color_nodes])
            resources.append([binding,resource_nodes])
            if _protected(mat,[(obj.name,index)])[0] and re.search(r'logo|label|brand|标识|商标|文字',obj.name+' '+mat.name,re.I):
                labels.append([binding,_nodes(mat.node_tree),vector(mat.diffuse_color)])
    color_hash=digest(colors)
    return {'geometry_sha256':geometry.hexdigest(),'transforms_sha256':digest(transforms),
            'color_sha256':color_hash,'colors_sha256':color_hash,'resources_sha256':digest(resources),
            'labels_sha256':digest(labels)}

def _role(obj):
    role=obj.get('wuying_light_role') or obj.get('studio_role')
    if role in ('key','fill','rim'):return role
    name=re.sub(r'\.\d{3}$','',obj.name.casefold()).strip()
    return next((role for role,names in ROLE_ALIASES.items() if name in names),None)

def _frame(camera,center):
    from mathutils import Vector
    target=Vector(center);up=Vector((0,0,1));front=camera.matrix_world.translation-target;front.z=0
    if front.length<1e-7:raise ValueError('当前相机无法建立稳定的摄影水平坐标')
    front.normalize();right=up.cross(front).normalized()
    return front,right,up

def _lights(scene,bounds):
    from mathutils import Vector
    target=Vector(bounds['center']);front,right,up=_frame(scene.camera,target);rows=[]
    for obj in sorted(scene.objects,key=lambda o:o.name):
        if obj.type!='LIGHT':continue
        offset=obj.matrix_world.translation-target;horizontal=math.hypot(offset.dot(front),offset.dot(right))
        rows.append({'name':obj.name,'object_name':obj.name,'role':_role(obj),'type':obj.data.type,
            'visible':_visible(obj,scene),'energy':float(obj.data.energy),'color':vector(obj.data.color),
            'azimuth':math.degrees(math.atan2(offset.dot(right),offset.dot(front))),
            'elevation':math.degrees(math.atan2(offset.z,horizontal)),'distance':float(offset.length),
            'size':float(obj.data.size) if hasattr(obj.data,'size') else None,
            'size_y':float(obj.data.size_y) if hasattr(obj.data,'size_y') else None,
            'shape':getattr(obj.data,'shape',None),'diffuse_factor':getattr(obj.data,'diffuse_factor',None),
            'position':vector(obj.matrix_world.translation),'matrix_world':matrix(obj.matrix_world),
            'target':vector(target),'camera':scene.camera.name})
    return rows

def _ground(scene,products):
    matches=[obj for obj in scene.objects if obj.type=='MESH' and obj not in products
             and (obj.name in GROUND_NAMES or obj.get('wuying_ground') is True or obj.get('studio_role')=='ground')]
    if len(matches)!=1:return None,{'name':None,'editable':False,'reason':'没有唯一登记的地面'}
    obj=matches[0]
    if obj.parent or obj.constraints or len(obj.modifiers) or not _visible(obj,scene):
        return None,{'name':obj.name,'editable':False,'reason':'地面存在约束、父级、修改器或隐藏状态'}
    from mathutils import Vector
    corners=[obj.matrix_world@Vector(v) for v in obj.bound_box]
    extent=max(v.z for v in corners)-min(v.z for v in corners)
    if extent>1e-5:return None,{'name':obj.name,'editable':False,'reason':'登记的地面不是可核验水平平面'}
    return obj,{'name':obj.name,'editable':True,'z':float(obj.matrix_world.translation.z)}

def _environment_node(world):
    if not world or not world.node_tree:return None
    tree=world.node_tree
    marked=[node for node in tree.nodes if node.type=='BACKGROUND' and node.get('wuying_illumination') is True]
    if len(marked)==1:return marked[0]
    backgrounds=[node for node in tree.nodes if node.type=='BACKGROUND']
    if len(backgrounds)==1 and not backgrounds[0].inputs['Strength'].is_linked:return backgrounds[0]
    for mix in tree.nodes:
        if mix.type!='MIX_SHADER':continue
        factor=list(mix.inputs[0].links)
        if len(factor)==1 and factor[0].from_node.type=='LIGHT_PATH' and factor[0].from_socket.name=='Is Camera Ray':
            links=list(mix.inputs[1].links)
            if len(links)==1 and links[0].from_node.type=='BACKGROUND':return links[0].from_node
    return None

def _physical_state(scene,result,ground):
    """Measured render controls, with projection-derived quantities separated."""
    camera=scene.camera;data=camera.data
    actual_camera={key:result['camera'][key] for key in ('name','type','focal_length','shift_x','shift_y','ortho_scale','matrix_world','constraints')}
    actual_camera.update(sensor_fit=data.sensor_fit,sensor_width=float(data.sensor_width),sensor_height=float(data.sensor_height),
                         clip_start=float(data.clip_start),clip_end=float(data.clip_end),dof_enabled=bool(data.dof.use_dof),
                         focus_distance=float(data.dof.focus_distance),aperture_fstop=float(data.dof.aperture_fstop),
                         aperture_blades=int(data.dof.aperture_blades),aperture_ratio=float(data.dof.aperture_ratio),
                         aperture_rotation=float(data.dof.aperture_rotation),
                         focus_object={'name':data.dof.focus_object.name,'matrix_world':matrix(data.dof.focus_object.matrix_world)} if data.dof.focus_object else None)
    actual_lights=[]
    for measured in result['lights']:
        obj=scene.objects[measured['name']];lamp=obj.data
        row={key:measured[key] for key in ('name','role','type','visible','energy','color','size','size_y','shape','diffuse_factor','matrix_world')}
        for key in ('specular_factor','volume_factor','spread','angle','shadow_soft_size','normalize','use_shadow'):
            if hasattr(lamp,key):row[key]=_primitive(getattr(lamp,key))
        row['use_nodes']=bool(getattr(lamp,'use_nodes',False));row['nodes']=_nodes(lamp.node_tree)
        actual_lights.append(row)
    ground_obj=scene.objects.get(ground.get('name','')) if ground.get('name') else None
    actual_ground=None
    if ground_obj:
        actual_ground={'name':ground_obj.name,'matrix_world':matrix(ground_obj.matrix_world),
                       'visible':_visible(ground_obj,scene),'shadow_catcher':bool(getattr(ground_obj,'is_shadow_catcher',False)),
                       'geometry_sha256':_fingerprints([ground_obj])['geometry_sha256']}
    divisor=math.gcd(scene.render.resolution_x,scene.render.resolution_y)
    canvas=[scene.render.resolution_x//divisor,scene.render.resolution_y//divisor,scene.render.pixel_aspect_x,scene.render.pixel_aspect_y]
    return {'version':SCENE_STATE_VERSION,'product_scale':result['bounds']['max_span'],
            'camera':actual_camera,'canvas_aspect':canvas,'lights':actual_lights,
            'world_nodes':_nodes(scene.world.node_tree if scene.world else None),
            'ground':actual_ground,'ground_nodes':[_nodes(slot.material.node_tree) if slot.material else None for slot in ground_obj.material_slots] if ground_obj else None,
            'surfaces':result['materials'],
            'product':{key:result[key] for key in ('geometry_sha256','transforms_sha256','color_sha256','resources_sha256','labels_sha256')},
            'exposure':result['exposure'],'view_transform':scene.view_settings.view_transform,'look':scene.view_settings.look,
            'gamma':scene.view_settings.gamma,'unit_scale':scene.unit_settings.scale_length,
            'film_transparent':bool(scene.render.film_transparent)}

def catalog(scene,products,points=None):
    import bpy
    from mathutils import Vector
    if scene.camera is None or scene.camera.data.type not in ('PERSP','ORTHO'):
        raise ValueError('需要可核验的透视或正交产品相机')
    bpy.context.view_layer.update()
    points=_world_vertices(products) if points is None else points
    bounds=_bounds(points);offset=scene.camera.matrix_world.translation-Vector(bounds['center'])
    framing=_projection(scene,points);materials=[]
    for ident,material,bindings in _material_groups(products):
        node=_principled(material);protected,reason=_protected(material,bindings)
        row={'id':ident,'name':material.name,'object_names':sorted({name for name,_ in bindings}),
             'bindings':[{'object_name':name,'slot_index':index} for name,index in bindings],
             'protected':protected,'protected_reason':reason,'editable':[]}
        for key,label in SURFACE_INPUTS.items():
            socket=node.inputs.get(label) if node else None
            row[key]=float(socket.default_value) if socket else None
            if not protected and socket and not socket.is_linked:row['editable'].append(key)
        row['base_color']=vector(node.inputs['Base Color'].default_value) if node else vector(material.diffuse_color)
        row['color_editable']=bool(node and not protected and not node.inputs['Base Color'].is_linked)
        materials.append(row)
    lights=_lights(scene,bounds);lighting={}
    for role in ('key','fill','rim'):
        matches=[row for row in lights if row['role']==role and row['visible']]
        if len(matches)==1:lighting[role]=matches[0]
    environment=_environment_node(scene.world)
    lighting['environment']=float(environment.inputs['Strength'].default_value) if environment and not environment.inputs['Strength'].is_linked else None
    _,ground=_ground(scene,products)
    camera={'name':scene.camera.name,'type':scene.camera.data.type,
            'azimuth':math.degrees(math.atan2(offset.x,-offset.y)),
            'elevation':math.degrees(math.atan2(offset.z,math.hypot(offset.x,offset.y))),
            'focal_length':float(scene.camera.data.lens),'distance':float(offset.length),
            'shift_x':float(scene.camera.data.shift_x),'shift_y':float(scene.camera.data.shift_y),
            'ortho_scale':float(scene.camera.data.ortho_scale),'occupancy':framing.get('occupancy'),'bbox':framing.get('bbox'),
            'matrix_world':matrix(scene.camera.matrix_world),'constraints':bool(scene.camera.constraints)}
    result={'version':SCHEMA_VERSION,'product_object_names':[obj.name for obj in products],
            'bounds':bounds,**_fingerprints(products),'camera':camera,'framing':framing,'materials':materials,
            'ground':ground,'lights':lights,'lighting':lighting,'exposure':float(scene.view_settings.exposure),
            'light_directions':[{key:row[key] for key in ('role','object_name','camera','azimuth','elevation','position','target')}
                                for role,row in lighting.items() if role in ('key','fill','rim')],
            'world':{'name':scene.world.name if scene.world else None,'environment':lighting['environment']},
            'render':{'width':scene.render.resolution_x,'height':scene.render.resolution_y,'engine':scene.render.engine}}
    state=_physical_state(scene,result,ground)
    result['scene_state']=state;result['scene_state_sha256']=state_digest(state)
    result['actual_state_sha256']=result['scene_state_sha256']
    return result

def _aim(obj,target):
    from mathutils import Vector,Matrix
    position=obj.matrix_world.translation.copy()
    rotation=(Vector(target)-position).to_track_quat('-Z','Y')
    obj.matrix_world=Matrix.LocRotScale(position,rotation,obj.matrix_world.to_scale())

def _set_camera(scene,bounds,azimuth,elevation,lens,distance=None):
    import bpy
    from mathutils import Vector,Matrix
    camera=scene.camera
    if camera.constraints or camera.animation_data or camera.parent:
        raise ValueError('当前相机存在约束、动画或父级，不能直接改动其摄影参数')
    center=Vector(bounds['center']);distance=distance or (camera.matrix_world.translation-center).length
    az=math.radians(azimuth);el=math.radians(elevation)
    camera.matrix_world=Matrix.Translation(center+distance*Vector((math.sin(az)*math.cos(el),-math.cos(az)*math.cos(el),math.sin(el))))
    camera.data.lens=lens;_aim(camera,center);bpy.context.view_layer.update()

def _fit_camera(scene,bounds,points,occupancy):
    import bpy
    from mathutils import Vector
    camera=scene.camera;center=Vector(bounds['center']);direction=(camera.matrix_world.translation-center).normalized()
    camera.data.shift_x=0;camera.data.shift_y=0
    if camera.data.type=='ORTHO':
        for _ in range(5):
            current=_projection(scene,points)
            if not current.get('occupancy'):raise ValueError('无法测量正交镜头中的完整产品')
            camera.data.ortho_scale*=current['occupancy']/occupancy
            bpy.context.view_layer.update()
    else:
        lower=max(bounds['max_span']*.6,1e-3);upper=max((camera.location-center).length,bounds['max_span']*2)
        for _ in range(12):
            camera.location=center+direction*upper;bpy.context.view_layer.update();frame=_projection(scene,points)
            if frame.get('occupancy') and frame['occupancy']<=occupancy:break
            upper*=1.6
        else:raise ValueError('无法在有效摄影距离内容纳完整产品')
        for _ in range(24):
            middle=(lower+upper)/2;camera.location=center+direction*middle;bpy.context.view_layer.update();frame=_projection(scene,points)
            if not frame.get('occupancy') or frame['occupancy']>occupancy:lower=middle
            else:upper=middle
        camera.location=center+direction*upper;bpy.context.view_layer.update()
    # Solve optical shift from measured projection, including sensor-fit/aspect.
    for attribute,axis in (('shift_x',0),('shift_y',1)):
        baseline=_projection(scene,points)['bbox'];current=(baseline[axis]+baseline[axis+2])/2
        previous=getattr(camera.data,attribute);setattr(camera.data,attribute,previous+.01)
        bpy.context.view_layer.update();changed=_projection(scene,points)['bbox']
        slope=((changed[axis]+changed[axis+2])/2-current)/.01
        if abs(slope)<1e-8:raise ValueError('相机光学偏移没有可核验的响应')
        setattr(camera.data,attribute,previous+(.5-current)/slope);bpy.context.view_layer.update()
    final=_projection(scene,points)
    if final['clipped'] or abs(final['occupancy']-occupancy)>.005:
        raise ValueError('自动构图未能保证完整产品和目标留白')

def _camera_recipe(scene,bounds,points,recipe,operations):
    if not recipe:return
    current=catalog(scene,_products(scene,[]),points)['camera']
    az=current['azimuth']+recipe.get('azimuth_delta',0)
    el=max(-75,min(85,current['elevation']+recipe.get('elevation_delta',0)))
    lens=recipe.get('focal_length',current['focal_length'])
    if any(key in recipe for key in ('azimuth_delta','elevation_delta','focal_length')):
        _set_camera(scene,bounds,az,el,lens,current['distance'])
    if 'occupancy' in recipe:_fit_camera(scene,bounds,points,recipe['occupancy'])
    operations.append({'kind':'camera','parameters':recipe})

def _copy_surface(material,bindings,scene,products):
    # Keep linked material users outside the registered product untouched.
    allowed=set(bindings)
    outsiders=[obj for obj in scene.objects
               if any(slot.material==material and (obj.name,index) not in allowed for index,slot in enumerate(obj.material_slots))]
    if outsiders:
        original=material;material=original.copy()
        material['wuying_source_material']=original.get('wuying_source_material',original.name)
        for name,index in bindings:scene.objects[name].material_slots[index].material=material
    return material

def _apply_surfaces(scene,products,rows,operations):
    lookup={ident:(material,bindings) for ident,material,bindings in _material_groups(products)}
    for row in rows:
        if row['id'] not in lookup:raise ValueError('表面目标已不在当前场景目录中')
        material,bindings=lookup[row['id']]
        if _protected(material,bindings)[0]:raise ValueError('不能自动修改受保护的文字、纹理或复杂表面')
        material=_copy_surface(material,bindings,scene,products);node=_principled(material);changes={}
        for key,value in row.items():
            if key in ('id','reason'):continue
            socket=node.inputs.get(SURFACE_INPUTS[key])
            if socket is None or socket.is_linked:raise ValueError('表面通道存在节点连接，不能覆盖：'+key)
            previous=float(socket.default_value);socket.default_value=value
            if abs(float(socket.default_value)-value)>1e-6:raise ValueError('表面参数未按要求设置')
            changes[key]={'before':previous,'after':float(socket.default_value)}
        operations.append({'kind':'surface','id':row['id'],'bindings':[{'object_name':name,'slot_index':index} for name,index in bindings],
                           'changes':changes,'reason':row.get('reason','')})

def _new_light(scene,role,position,target,energy,width,height,color=(1,1,1),diffuse=1):
    import bpy
    from mathutils import Matrix,Vector
    owned=[obj for obj in scene.objects if obj.type=='LIGHT' and obj.get('wuying_hero_owned') is True and obj.get('wuying_hero_role')==role]
    if len(owned)>1:raise ValueError('现有摄影灯角色重复')
    if owned:obj=owned[0]
    else:
        data=bpy.data.lights.new('WuyingHero '+role,'AREA');obj=bpy.data.objects.new('WuyingHero '+role,data);scene.collection.objects.link(obj)
    obj['wuying_hero_owned']=True;obj['wuying_hero_role']=role
    if role in ('key','fill','rim'):obj['wuying_light_role']=role
    obj.hide_render=False;obj.hide_viewport=False
    obj.data.type='AREA';obj.data.shape='RECTANGLE';obj.data.energy=energy;obj.data.size=width;obj.data.size_y=height
    obj.data.color=color
    if hasattr(obj.data,'normalize'):obj.data.normalize=True
    if hasattr(obj.data,'diffuse_factor'):obj.data.diffuse_factor=diffuse
    if hasattr(obj.data,'specular_factor'):obj.data.specular_factor=1
    obj.matrix_world=Matrix.Translation(Vector(position));_aim(obj,target)
    return obj

def _light_position(scene,bounds,azimuth,elevation,distance):
    from mathutils import Vector
    front,right,up=_frame(scene.camera,bounds['center']);az=math.radians(azimuth);el=math.radians(elevation)
    return Vector(bounds['center'])+distance*((front*math.cos(az)+right*math.sin(az))*math.cos(el)+up*math.sin(el))

def _lighting_recipe(scene,bounds,recipe,operations):
    if not recipe:return
    measured=_lights(scene,bounds);known={}
    for role in ('key','fill','rim'):
        matches=[row for row in measured if row['role']==role and row['visible']]
        if len(matches)==1:known[role]=matches[0]
    span=bounds['max_span'];anchor=known.get('key',{}).get('energy') or 70*span*span
    active_roles=[role for role in ('key','fill','rim') if recipe.get(role)]
    full_rig=set(active_roles)=={'key','fill','rim'}
    for obj in scene.objects:
        if obj.type!='LIGHT' or not _visible(obj,scene):continue
        if full_rig or _role(obj) in active_roles:
            obj.hide_render=True
            # Hidden legacy role tags must not create duplicate active controls.
            if obj.get('wuying_hero_owned') is not True:
                for tag in ('wuying_light_role','studio_role'):
                    if obj.get(tag) in ('key','fill','rim'):
                        obj['wuying_previous_'+tag]=obj[tag];del obj[tag]
                obj['wuying_light_role']='archived'
    role_anchors={}
    for role in active_roles:
        values=recipe[role];old=known.get(role,{})
        fallback={'key':(-45,40,1.35),'fill':(25,30,.75),'rim':(135,55,.5)}[role]
        az=values.get('azimuth',old.get('azimuth',fallback[0]));el=values.get('elevation',old.get('elevation',fallback[1]))
        distance=old.get('distance',span*3)
        role_anchor=old.get('energy') or anchor
        role_anchors[role]={'energy':role_anchor,'source':'measured_role' if old.get('energy') else 'key_or_scale_fallback'}
        energy=role_anchor*values['power'] if 'power' in values else old.get('energy',anchor*fallback[2])
        width=span*values['width'] if 'width' in values else old.get('size') or span*1.5
        height=span*values['height'] if 'height' in values else old.get('size_y') or width
        _new_light(scene,role,_light_position(scene,bounds,az,el,distance),bounds['center'],energy,width,height)
    reflection=recipe.get('reflection',{})
    if reflection.get('enabled') is False:
        for obj in scene.objects:
            if obj.get('wuying_hero_owned') is True and obj.get('wuying_hero_role') in ('reflection','front_reflection'):obj.hide_render=True
    elif reflection.get('enabled') is True:
        power=reflection.get('power',.03);width=span*reflection.get('width',.9);height=span*reflection.get('height',.4)
        target=list(bounds['center']);target[2]=bounds['min'][2]+bounds['dimensions'][2]*.94
        _new_light(scene,'reflection',_light_position(scene,bounds,-155,25,span*1.8),target,anchor*power,width,height,diffuse=.12)
        _new_light(scene,'front_reflection',_light_position(scene,bounds,-60,8,span*2.4),bounds['center'],anchor*power,span*.55,span*1.5,diffuse=.15)
    elif reflection:
        raise ValueError('反射光调整需要明确 enabled 开关')
    if active_roles or reflection:operations.append({'kind':'lighting','parameters':recipe,'anchor_energy':anchor,'role_anchors':role_anchors})

def _world_recipe(scene,environment=None,clean_white=False):
    import bpy
    if clean_white:
        old=_environment_node(scene.world)
        strength=environment if environment is not None else float(old.inputs['Strength'].default_value) if old else .14
        world=bpy.data.worlds.new('WuyingHero environment');world.use_nodes=True;scene.world=world
        nodes=world.node_tree.nodes;nodes.clear();links=world.node_tree.links
        illum=nodes.new('ShaderNodeBackground');illum.name='Hero environment';illum['wuying_illumination']=True
        illum.inputs['Color'].default_value=(1,1,1,1);illum.inputs['Strength'].default_value=strength
        camera=nodes.new('ShaderNodeBackground');camera.name='Hero camera white';camera.inputs['Color'].default_value=(1,1,1,1);camera.inputs['Strength'].default_value=1
        path=nodes.new('ShaderNodeLightPath');mix=nodes.new('ShaderNodeMixShader');output=nodes.new('ShaderNodeOutputWorld')
        links.new(path.outputs['Is Camera Ray'],mix.inputs[0]);links.new(illum.outputs[0],mix.inputs[1]);links.new(camera.outputs[0],mix.inputs[2]);links.new(mix.outputs[0],output.inputs['Surface'])
        scene.render.film_transparent=False
    elif environment is not None:
        node=_environment_node(scene.world)
        if node is None or node.inputs['Strength'].is_linked:raise ValueError('环境照明没有独立可修改的强度通道')
        world=scene.world.copy();scene.world=world;_environment_node(world).inputs['Strength'].default_value=environment

def _background_recipe(scene,products,bounds,recipe,operations,warnings):
    import bpy
    if not recipe:return
    if recipe.get('clean_white') is True:_world_recipe(scene,clean_white=True)
    contact=any(key in recipe for key in ('contact_strength','contact_distance'))
    if contact:
        obj,info=_ground(scene,products)
        if obj is None:warnings.append(info['reason']+'，已保留原地面')
        else:
            strength=recipe.get('contact_strength',obj.get('wuying_contact_strength',.28))
            distance=recipe.get('contact_distance',obj.get('wuying_contact_distance',.12))
            material=bpy.data.materials.new('WuyingHero white contact');material.use_nodes=True
            nodes=material.node_tree.nodes;nodes.clear();links=material.node_tree.links
            physical=nodes.new('ShaderNodeBsdfPrincipled');physical.inputs['Base Color'].default_value=(.9,.9,.9,1);physical.inputs['Roughness'].default_value=.88
            spec=physical.inputs.get('Specular IOR Level')
            if spec:spec.default_value=.12
            ao=nodes.new('ShaderNodeAmbientOcclusion');ao.name='Local contact';ao.inputs['Distance'].default_value=distance*bounds['max_span']
            ramp=nodes.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].color=(1-strength,1-strength,1-strength,1);ramp.color_ramp.elements[1].color=(1,1,1,1)
            emission=nodes.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=1
            path=nodes.new('ShaderNodeLightPath');mix=nodes.new('ShaderNodeMixShader');output=nodes.new('ShaderNodeOutputMaterial')
            links.new(ao.outputs['AO'],ramp.inputs['Fac']);links.new(ramp.outputs['Color'],emission.inputs['Color'])
            links.new(path.outputs['Is Camera Ray'],mix.inputs[0]);links.new(physical.outputs[0],mix.inputs[1]);links.new(emission.outputs[0],mix.inputs[2]);links.new(mix.outputs[0],output.inputs['Surface'])
            obj.data=obj.data.copy();obj.data.materials.clear();obj.data.materials.append(material)
            obj.is_shadow_catcher=False;obj.location.z=bounds['min'][2]-bounds['max_span']*.0005
            obj['wuying_ground']=True;obj['wuying_contact_strength']=strength;obj['wuying_contact_distance']=distance
            operations.append({'kind':'contact_shadow','ground':obj.name,'parameters':recipe,'method':'camera-ray-AO-white-sweep'})
    if recipe.get('clean_white') is True:operations.append({'kind':'white_background','method':'camera-ray-world'})

def _apply_recipe(scene,products,points,raw,operations,warnings):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from hero_recipe import normalize_recipe
    before=catalog(scene,products,points);recipe,skipped=normalize_recipe(raw,before)
    warnings.extend(skipped);bounds=before['bounds']
    _camera_recipe(scene,bounds,points,recipe['camera'],operations)
    _lighting_recipe(scene,bounds,recipe['lighting'],operations)
    _background_recipe(scene,products,bounds,recipe['background'],operations,warnings)
    if 'environment' in recipe['lighting']:_world_recipe(scene,recipe['lighting']['environment'])
    _apply_surfaces(scene,products,recipe['surfaces'],operations)
    scene['wuying_product_target']=bounds['center']
    scene['studio_hero_recipe']=json.dumps(recipe,ensure_ascii=False,sort_keys=True)
    return recipe

def _unlinked_color(value):
    if isinstance(value,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',value):
        rgb=[int(value[i:i+2],16)/255 for i in (1,3,5)]
        return [channel/12.92 if channel<=.04045 else ((channel+.055)/1.055)**2.4 for channel in rgb]
    if isinstance(value,list) and len(value) in (3,4):return [number(channel,0,1,'颜色') for channel in value[:3]]
    raise ValueError('已有场景的颜色调整需要 #RRGGBB 或三个有限通道')

def _material_alias(name):return re.sub(r'(?:\.\d{3})+$','',name or '')

def _changed_materials(scene,products,plan,baseline,operations,authorized):
    valid_names={obj.name for obj in products}
    for field in ('material_overrides','object_material_overrides'):
        local=field=='object_material_overrides';key_fields=('object_name','material_name') if local else ('name',)
        previous={tuple(row.get(key) for key in key_fields):row for row in baseline.get(field,[])}
        current={tuple(row.get(key) for key in key_fields):row for row in plan.get(field,[])}
        if set(previous)-set(current):raise ValueError('不能从摄影快照恢复已移除的旧材质覆盖，请保留当前材质或重新生成基础场景')
        for index,row in enumerate(plan.get(field,[])):
            old=previous.get(tuple(row.get(key) for key in key_fields),{})
            changes={key:value for key,value in row.items() if key not in (*key_fields,'source') and old.get(key)!=value}
            if not old and row.get('preserve_original') is True:
                # A first scalar override carries plan bookkeeping added by
                # apply_patch(). Its fallback preset does not replace the
                # existing shader when preserve_original is true. Existing
                # overrides switching preservation/preset still fail below.
                changes.pop('preserve_original',None)
                changes.pop('preset',None)
            if not changes:continue
            if set(changes)-{'roughness','metallic','transmission','color'}:
                raise ValueError('当前摄影快照不支持更换材质预设、纹理或恢复原材质；可直接调整颜色、粗糙度、金属度与透射')
            requested_name=row['material_name'] if local else row['name']
            candidates=[]
            if local:
                if row['object_name'] not in valid_names:raise ValueError('材质覆盖引用了产品登记外的部位')
                obj=scene.objects[row['object_name']]
                exact=[(obj.name,i,slot.material) for i,slot in enumerate(obj.material_slots) if slot.material and slot.material.name==requested_name]
                compatible=[(obj.name,i,slot.material) for i,slot in enumerate(obj.material_slots) if slot.material
                    and (slot.material.get('wuying_source_material')==requested_name or _material_alias(slot.material.name)==_material_alias(requested_name))]
                candidates=exact or compatible
                if len(candidates)!=1:raise ValueError('无法唯一确认当前部位的材质槽：'+row['object_name'])
            else:
                candidates=[(obj.name,i,slot.material) for obj in products for i,slot in enumerate(obj.material_slots)
                    if slot.material and (slot.material.name==requested_name or slot.material.get('wuying_source_material')==requested_name)]
                if not candidates:raise ValueError('全局材质名称没有匹配到当前场景')
            groups={}
            for name,slot,material in candidates:groups.setdefault(material,[]).append((name,slot))
            for material,bindings in groups.items():
                if _protected(material,bindings)[0]:raise ValueError('保留品牌、贴图或复杂材质，不能覆盖该表面')
                material=_copy_surface(material,bindings,scene,products);node=_principled(material)
                for key,value in changes.items():
                    socket=node.inputs.get('Base Color' if key=='color' else SURFACE_INPUTS[key])
                    if socket is None or socket.is_linked:raise ValueError('材质通道已连接节点，不能直接覆盖：'+key)
                    if key=='color':
                        before=vector(socket.default_value);after=[*_unlinked_color(value),before[3]]
                        socket.default_value=after;material.diffuse_color=after
                        authorized.append({'path':field+'['+str(index)+'].color','requested_color':copy.deepcopy(value),
                            'bindings':[{'object_name':name,'slot_index':slot} for name,slot in bindings],
                            'before':before,'after':vector(socket.default_value)})
                    else:
                        number(value,0,1,key);socket.default_value=value
                        if abs(socket.default_value-value)>1e-6:raise ValueError('材质参数未按要求设置')
                operations.append({'kind':'existing_surface_delta','bindings':[{'object_name':name,'slot_index':slot} for name,slot in bindings],
                                   'parameters':changes})

def _effective_plan(plan,shot_id):
    """Mirror engine.effective_plan without importing app/config in Blender."""
    result=copy.deepcopy(plan)
    adjustments=result.get('shot_adjustments',{})
    if not isinstance(adjustments,dict):raise ValueError('镜头独立调整无效')
    adjustment=adjustments.get(shot_id,{})
    if not isinstance(adjustment,dict) or set(adjustment)-{'exposure_delta','softness_multiplier','lighting'}:
        raise ValueError('镜头独立调整包含不能执行的参数')
    exposure=number(result.get('exposure',0),-4,4,'基础曝光')
    delta=number(adjustment.get('exposure_delta',0),-8,8,'镜头曝光调整')
    softness=number(result.get('light_softness',1),.25,4,'基础柔和度')
    multiplier=number(adjustment.get('softness_multiplier',1),.0625,16,'镜头柔和倍率')
    result['exposure']=max(-4,min(4,exposure+delta))
    result['light_softness']=max(.25,min(4,softness*multiplier))
    if 'lighting' in result or 'lighting' in adjustment:
        if not isinstance(result.get('lighting',{}),dict) or not isinstance(adjustment.get('lighting',{}),dict):
            raise ValueError('镜头灯光参数无效')
        result['lighting']={**result.get('lighting',{}),**adjustment.get('lighting',{})}
    result['shot_adjustments']={}
    return result

def _checkpoint(scene,products,points,plan,baseline,shot_id,operations,authorized):
    if not isinstance(plan,dict) or not isinstance(baseline,dict):raise ValueError('摄影快照缺少可比较的方案基准')
    plan=_effective_plan(plan,shot_id);baseline=_effective_plan(baseline,shot_id)
    for key in ('rotation','pose','repair','dimensions_mm','texture_scale','texture_rotation','style'):
        if plan.get(key)!=baseline.get(key):raise ValueError('当前摄影快照不能执行 '+key+' 变化，请保留产品结构、纹理与摄影风格')
    old_shot=next((row for row in baseline.get('shots',[]) if row.get('id')==shot_id),None)
    new_shot=next((row for row in plan.get('shots',[]) if row.get('id')==shot_id),None)
    if not old_shot or not new_shot:raise ValueError('摄影快照没有当前镜头的方案基准')
    if any(old_shot.get(key)!=new_shot.get(key) for key in ('kind','target_object','style')):
        raise ValueError('摄影快照不能替换镜头目标或切换细节镜头类型')
    measured=catalog(scene,products,points);bounds=measured['bounds'];camera=measured['camera']
    camera_changed=any(old_shot.get(key)!=new_shot.get(key) for key in ('azimuth','elevation','focal_length'))
    if camera_changed:
        _set_camera(scene,bounds,
            number(new_shot.get('azimuth'),-360,360,'相机方位角'),number(new_shot.get('elevation'),-75,85,'相机俯仰角'),
            number(new_shot.get('focal_length'),20,200,'焦距'),camera['distance'])
        operations.append({'kind':'checkpoint_camera','parameters':{key:new_shot[key] for key in ('azimuth','elevation','focal_length')}})
    if plan.get('aspect')!=baseline.get('aspect'):
        if plan.get('aspect') not in ('1:1','4:3','3:4'):raise ValueError('画幅不受支持')
        x,y=map(int,plan['aspect'].split(':'));long=max(scene.render.resolution_x,scene.render.resolution_y)
        scene.render.resolution_x=round(long*x/max(x,y));scene.render.resolution_y=round(long*y/max(x,y))
    if plan.get('margin')!=baseline.get('margin') or plan.get('aspect')!=baseline.get('aspect'):
        margin=number(plan.get('margin'),.03,.4,'留白');_fit_camera(scene,bounds,points,1-2*margin)
        operations.append({'kind':'checkpoint_framing','margin':margin,'aspect':plan.get('aspect')})
    if plan.get('exposure',0)!=baseline.get('exposure',0):
        scene.view_settings.exposure=number(plan.get('exposure',0),-4,4,'曝光')
        operations.append({'kind':'checkpoint_exposure','value':scene.view_settings.exposure})
    old_lights=baseline.get('lighting',{});new_lights=plan.get('lighting',{})
    allowed={'key_power','fill_power','rim_power','environment'}|{role+'_'+axis for role in ('key','fill','rim') for axis in ('azimuth','elevation')}
    if set(new_lights)-allowed:raise ValueError('摄影快照包含未支持的灯光字段')
    actual=_lights(scene,bounds)
    for role in ('key','fill','rim'):
        keys=(role+'_power',role+'_azimuth',role+'_elevation')
        changed=[key for key in keys if old_lights.get(key,1 if key.endswith('_power') else None)!=new_lights.get(key,1 if key.endswith('_power') else None)]
        if not changed:continue
        rows=[row for row in actual if row['role']==role and row['visible']]
        if len(rows)!=1:raise ValueError('无法唯一定位当前 '+role+' 光源')
        row=rows[0];obj=scene.objects[row['name']]
        if obj.parent or obj.constraints or obj.animation_data:
            raise ValueError('当前光源存在父级、约束或动画，不能直接修改其摄影参数')
        if role+'_power' in changed:
            before=number(old_lights.get(role+'_power',1),.00001,3,'旧光强');after=number(new_lights.get(role+'_power',1),.1,3,'新光强')
            obj.data=obj.data.copy();obj.data.energy*=after/before
        if any(not key.endswith('_power') for key in changed):
            az=number(new_lights[role+'_azimuth'],-180,180,'灯光方位角') if role+'_azimuth' in changed else row['azimuth']
            el=number(new_lights[role+'_elevation'],0,85,'灯光俯仰角') if role+'_elevation' in changed else row['elevation']
            from mathutils import Matrix
            obj.matrix_world=Matrix.Translation(_light_position(scene,bounds,az,el,row['distance']))
            _aim(obj,bounds['center'])
        operations.append({'kind':'checkpoint_light','role':role,'fields':changed})
    if old_lights.get('environment',1)!=new_lights.get('environment',1):
        old=number(old_lights.get('environment',1),.00001,3,'旧环境倍率');new=number(new_lights.get('environment',1),0,3,'新环境倍率')
        node=_environment_node(scene.world)
        if not node:raise ValueError('没有可独立控制的环境照明')
        _world_recipe(scene,float(node.inputs['Strength'].default_value)*new/old)
        operations.append({'kind':'checkpoint_environment','ratio':new/old})
    if plan.get('light_softness',1)!=baseline.get('light_softness',1):
        old=number(baseline.get('light_softness',1),.25,4,'旧柔和度');new=number(plan.get('light_softness',1),.25,4,'柔和度')
        for row in _lights(scene,bounds):
            if row['visible'] and row['type']=='AREA':
                obj=scene.objects[row['name']];obj.data=obj.data.copy();obj.data.size*=new/old
                if obj.data.shape=='RECTANGLE':obj.data.size_y*=new/old
        operations.append({'kind':'checkpoint_softness','ratio':new/old})
    _changed_materials(scene,products,plan,baseline,operations,authorized)

def _pixel_metrics(scene,products,png,framing):
    """Ray-test image samples against the registered product, excluding floor.

    This is a sparse measured product check, not a realism or material-identity
    score. Metallic, transmissive and near-white pigment hits are not used as
    opaque clipping evidence.
    """
    import bpy
    import numpy as np
    from mathutils import Vector
    image=bpy.data.images.load(str(png),check_existing=False)
    width,height=image.size;pixels=np.empty(width*height*4,dtype=np.float32);image.pixels.foreach_get(pixels);pixels=pixels.reshape((height,width,4))
    graph=bpy.context.evaluated_depsgraph_get();camera=scene.camera
    projection=camera.calc_matrix_camera(graph,x=width,y=height,scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)
    inverse=np.linalg.inv(np.asarray(projection@camera.matrix_world.inverted(),dtype=np.float64))
    names={obj.name for obj in products};count=0;clipped=0;values=[];background=[];hits=0
    for yi in range(32):
        for xi in range(32):
            x=min(width-1,int((xi+.5)*width/32));y=min(height-1,int((yi+.5)*height/32))
            ndcx=2*(x+.5)/width-1;ndcy=2*(y+.5)/height-1
            near=inverse@np.array([ndcx,ndcy,-1,1]);far=inverse@np.array([ndcx,ndcy,1,1]);near=near[:3]/near[3];far=far[:3]/far[3]
            origin=camera.matrix_world.translation.copy() if camera.data.type=='PERSP' else Vector(near)
            direction=(Vector(far)-origin).normalized()
            hit,_,_,polygon,obj,_=scene.ray_cast(graph,origin,direction,distance=1e7)
            sample=pixels[y,x,:3]
            if not hit or obj.name not in names or not _visible(obj,scene):background.append(sample.tolist());continue
            hits+=1;values.append(sample.tolist())
            evaluated=obj.evaluated_get(graph)
            material_index=evaluated.data.polygons[polygon].material_index if 0<=polygon<len(evaluated.data.polygons) else 0
            material=obj.material_slots[material_index].material if material_index<len(obj.material_slots) else None
            node=_principled(material)
            if not node:continue
            alpha=node.inputs.get('Alpha');transmission=node.inputs.get('Transmission Weight');metal=node.inputs.get('Metallic');base=node.inputs.get('Base Color')
            if any(socket is None or socket.is_linked for socket in (alpha,transmission,metal,base)):continue
            if alpha.default_value<.99 or transmission.default_value>.05 or metal.default_value>.8 or max(base.default_value[:3])>.95:continue
            count+=1;clipped+=int(float(max(sample))>=.995)
    bpy.data.images.remove(image)
    product={'method':'raycast-product-nonwhite-opaque-v1','sample_grid':32,'product_samples':hits,
             'eligible_opaque_samples':count,'opaque_clipped_fraction':clipped/count if count else None,'luminance_mean':None}
    if values:product['luminance_mean']=float((np.asarray(values)@np.array([.2126,.7152,.0722])).mean())
    background_values=np.asarray(background)
    return {'framing':framing,'product':{**product,'framing':framing},
            'background':{'method':'raycast-nonproduct-samples-v1','samples':len(background),
                          'near_white_fraction':float((background_values.min(axis=1)>=.98).mean()) if len(background) else None}}

def _proof(before,after,authorized):
    flags={name:before[field]==after[field] for name,field in (
        ('geometry_preserved','geometry_sha256'),('transforms_preserved','transforms_sha256'),
        ('color_preserved','color_sha256'),('resources_preserved','resources_sha256'),('labels_preserved','labels_sha256'))}
    if not all(value for name,value in flags.items() if name!='color_preserved') or (not flags['color_preserved'] and not authorized):
        raise RuntimeError('摄影处理意外改变了产品结构、位置、颜色、文字或纹理，结果未采用')
    return {**flags,'before':{key:value for key,value in before.items() if key.endswith('_sha256')},
            'after':{key:value for key,value in after.items() if key.endswith('_sha256')},'authorized_color_changes':authorized}

def execute(request):
    import bpy
    source=Path(request.get('source_blend','')).resolve();out=Path(request.get('output_dir','')).resolve()
    if not source.is_file() or source.suffix.lower()!='.blend':raise ValueError('缺少已保存的原生 Blender 场景')
    if not isinstance(request.get('source_sha256'),str) or file_digest(source)!=request['source_sha256']:raise ValueError('源场景已变化，请刷新后重新开始')
    if source==out/'scene.blend':raise ValueError('输出不能覆盖源场景')
    ident=request.get('request_id')
    if not isinstance(ident,str) or not ident or len(ident)>128:raise ValueError('缺少本次执行标识')
    action=request.get('action')
    if action not in ('inspect','render'):raise ValueError('未知摄影场景动作')
    out.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
    scene=bpy.context.scene;products=_products(scene,request.get('product_object_names',[]))
    # Preserve the exact validated registry in the derived scene for later jobs.
    scene['studio_product_objects']=json.dumps([obj.name for obj in products],ensure_ascii=False)
    points=_world_vertices(products);before=catalog(scene,products,points)
    if action=='inspect':
        if file_digest(source)!=request['source_sha256']:raise ValueError('源场景在检查时发生变化')
        return {'success':True,'request_id':ident,'source_sha256':request['source_sha256'],'catalog':before}
    resolution=request.get('resolution',768);samples=request.get('samples',32)
    if type(resolution) is not int or not 32<=resolution<=4096:raise ValueError('出图尺寸必须为32至4096像素')
    if type(samples) is not int or not 1<=samples<=512:raise ValueError('采样数必须为1至512')
    if type(request.get('force_cpu',False)) is not bool:raise ValueError('CPU选择必须为布尔值')
    aspect=request.get('target_aspect')
    if aspect is not None:
        if aspect not in ('1:1','4:3','3:4'):raise ValueError('目标画幅无效')
        x,y=map(int,aspect.split(':'))
    else:x,y=scene.render.resolution_x,scene.render.resolution_y
    scene.render.resolution_x=round(resolution*x/max(x,y));scene.render.resolution_y=round(resolution*y/max(x,y));scene.render.resolution_percentage=100
    scene.render.engine='CYCLES';scene.cycles.samples=samples
    if request.get('force_cpu'):scene.cycles.device='CPU'
    scene.cycles.use_denoising=samples>=8
    if samples>=8:
        scene.cycles.denoiser='OPENIMAGEDENOISE';scene.cycles.use_adaptive_sampling=True;scene.cycles.adaptive_threshold=.03 if resolution<=1024 else .012
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='8'
    operations=[];warnings=[];authorized=[]
    recipe=request.get('recipe',{})
    if recipe is None:recipe={}
    if not isinstance(recipe,dict):raise ValueError('摄影方案必须为对象')
    if recipe and ('plan' in request or 'baseline_plan' in request):raise ValueError('不能同时重建摄影方案和继续修改快照')
    if recipe:_apply_recipe(scene,products,points,recipe,operations,warnings)
    elif 'plan' in request or 'baseline_plan' in request:
        _checkpoint(scene,products,points,request.get('plan'),request.get('baseline_plan'),request.get('shot_id'),operations,authorized)
    bpy.context.view_layer.update();after=catalog(scene,products,points)
    verification=_proof(before,after,authorized)
    png=out/'image.png';blend=out/'scene.blend';scene.render.filepath=str(png)
    scene['studio_hero_actual_state']=json.dumps({'version':SCHEMA_VERSION,'request_id':ident,'camera':after['camera'],'lighting':after['lighting'],'exposure':after['exposure']},ensure_ascii=False)
    scene['studio_hero_source_sha256']=request['source_sha256']
    print('HERO_STAGE rendering',flush=True)
    response=bpy.ops.render.render(write_still=True)
    if 'FINISHED' not in response or not png.is_file():raise RuntimeError('没有完成本次真实渲染')
    rendered=catalog(scene,products,points)
    _proof(before,rendered,authorized)
    render_comparison=compare_scene_states(after,rendered)
    if not render_comparison['equivalent']:raise RuntimeError('渲染期间场景状态发生变化：'+difference_summary(render_comparison))
    bpy.ops.wm.save_as_mainfile(filepath=str(blend),check_existing=False)
    if not blend.is_file():raise RuntimeError('没有保存可编辑场景')
    metrics=_pixel_metrics(scene,products,png,rendered['framing'])
    print('HERO_STAGE verifying_saved_scene',flush=True)
    bpy.ops.wm.open_mainfile(filepath=str(blend),load_ui=False,use_scripts=False)
    saved_products=_products(bpy.context.scene,request.get('product_object_names',[]));saved=catalog(bpy.context.scene,saved_products)
    _proof(before,saved,authorized)
    saved_comparison=compare_scene_states(rendered,saved)
    if not saved_comparison['equivalent']:raise RuntimeError('保存场景与实际渲染状态不一致：'+difference_summary(saved_comparison))
    if file_digest(source)!=request['source_sha256']:raise RuntimeError('源场景在执行期间发生变化，结果未采用')
    proof={**verification,'request_id':ident,'source_sha256':request['source_sha256'],'render_completed':True,'saved_scene_verified':True,
           'saved_actual_state_sha256':saved['actual_state_sha256'],'rendered_actual_state_sha256':rendered['actual_state_sha256'],
           'scene_state_protocol':saved_comparison['protocol'],'saved_scene_comparison':saved_comparison,
           'request_match':True,'request_plan_sha256':digest(request.get('plan',{})),
           'request_baseline_plan_sha256':digest(request.get('baseline_plan',{})),
           'png_sha256':file_digest(png),'blend_sha256':file_digest(blend)}
    return {'success':True,'request_id':ident,'source_sha256':request['source_sha256'],'outputs':{'png':'image.png','blend':'scene.blend'},
            'proof':proof,'catalog':saved,'metrics':metrics,'checks':saved['framing'],'operations':operations,'warnings':warnings,
            'device':bpy.context.scene.cycles.device,'render_pipeline_version':3}

def main():
    request_path=Path(sys.argv[sys.argv.index('--')+1]);request=json.loads(request_path.read_text(encoding='utf-8-sig'))
    output=Path(request['output_dir']).resolve();output.mkdir(parents=True,exist_ok=True)
    try:
        result=execute(request)
    except Exception as exc:
        result={'success':False,'request_id':request.get('request_id'),'error':str(exc),'error_type':type(exc).__name__}
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()

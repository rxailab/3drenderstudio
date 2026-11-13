"""Export a read-only, self-contained product viewport from a real local scene.

This script never renders, saves the input scene, or executes generated code.
The service invokes Blender with --factory-startup --disable-autoexec.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blender import hero_scene
from blender.camera_projection import apply_camera

SCHEMA='wuying-viewport-v1'
GEOMETRY={'MESH','CURVE','FONT','SURFACE','META'}


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def numbers(values):return [float(value) for value in values]
def yup(values):return [float(values[0]),float(values[2]),-float(values[1])]


def confined(path,root,must_exist=True):
    original=Path(path);resolved=original.resolve()
    if original.is_symlink() or root not in resolved.parents or (must_exist and not resolved.is_file()):
        raise ValueError('实时预览的场景或资源不在当前项目中。')
    return resolved


def glb_resources(source,root):
    """Check any external resources before Blender's importer opens them."""
    with source.open('rb') as stream:
        header=stream.read(20)
        if len(header)!=20:raise ValueError('GLB 文件不完整。')
        magic,version,length,size,kind=struct.unpack('<4sIIII',header)
        if magic!=b'glTF' or version!=2 or length!=source.stat().st_size or kind!=0x4E4F534A or size>8*1024*1024:
            raise ValueError('需要有效的 glTF 2.0 GLB 模型。')
        value=json.loads(stream.read(size).decode('utf-8').rstrip('\0 \t\r\n'))
    resources=[]
    for row in value.get('buffers',[])+value.get('images',[]):
        uri=row.get('uri')
        if not uri or uri.startswith('data:'):continue
        parts=urlsplit(uri)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:raise ValueError('模型资源必须为当前项目内的本地文件。')
        path=confined(source.parent/unquote(uri),root)
        resources.append({'path':path.relative_to(root).as_posix(),'sha256':sha(path)})
    return resources


def open_source(request,root):
    import bpy
    source=confined(request['source_path'],root)
    if source.suffix.lower() not in ('.blend','.glb','.obj'):raise ValueError('实时预览支持 Blender、GLB 与 OBJ 模型。')
    if sha(source)!=request['source_sha256']:raise ValueError('模型已变化，请重新准备实时预览。')
    resources=[]
    if source.suffix.lower()=='.blend':
        bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
        scene=bpy.context.scene
        products=hero_scene._products(scene,request.get('product_object_names',[]))
    else:
        bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
        before=set(bpy.data.objects)
        if source.suffix.lower()=='.glb':
            resources=glb_resources(source,root)
            bpy.ops.import_scene.gltf(filepath=str(source))
        else:
            # Uploaded OBJ/MTL resources are checked at import; the export below
            # additionally refuses every referenced image outside this project.
            bpy.ops.wm.obj_import(filepath=str(source))
        scene=bpy.context.scene
        products=[obj for obj in bpy.data.objects if obj not in before and obj.type in GEOMETRY
                  and obj.name in scene.objects and not obj.hide_render]
        if not products:raise ValueError('上传模型没有可显示的网格。')
        scene['studio_product_objects']=json.dumps([obj.name for obj in products])
    # Match render modifier visibility while retaining the input on disk.
    for obj in products:
        for modifier in obj.modifiers:modifier.show_viewport=modifier.show_render
    bpy.context.view_layer.update()
    return source,scene,products,resources


def original_plan(scene,products,points,plan,shot_id,operations,authorized,*,fresh_upload=False,root=None,warnings=None):
    """Support the normal first-open path without waiting for an offline image."""
    import bpy
    effective=hero_scene._effective_plan(plan,shot_id)
    if (any(effective.get('rotation',[0,0,0])) or effective.get('pose') or effective.get('dimensions_mm')
            or any(effective.get('repair',{}).values()) or effective.get('texture_scale',1)!=1
            or effective.get('texture_rotation',0)!=0):
        raise ValueError('当前摆放、尺寸或纹理调整需要先生成一次场景；完成后可继续实时拖动。')
    if scene.camera is None or scene.camera.type!='CAMERA':
        camera=bpy.data.cameras.new('Wuying viewport camera')
        scene.camera=bpy.data.objects.new('Wuying viewport camera',camera)
        scene.collection.objects.link(scene.camera)
    x,y=map(int,effective.get('aspect','1:1').split(':'))
    scene.render.resolution_x=round(1000*x/max(x,y));scene.render.resolution_y=round(1000*y/max(x,y))
    scene.render.resolution_percentage=100;scene.render.pixel_aspect_x=1;scene.render.pixel_aspect_y=1
    shot=next(row for row in effective['shots'] if row['id']==shot_id)
    apply_camera(scene,shot,products,effective.get('margin',.12),points)
    operations.append({'kind':'original_camera','projection':shot.get('projection','orthographic')})
    if fresh_upload:
        # Fresh OBJ/GLB assets have no photographic checkpoint. Apply the
        # same full material configuration as the render worker; checkpoint
        # delta restrictions do not apply to an imported working copy.
        # Validate all source images before the worker can load textures.
        material_resources(products,root,warnings if warnings is not None else [])
        for override in effective.get('material_overrides',[])+effective.get('object_material_overrides',[]):
            for path in override.get('textures',{}).values():
                confined(path,root)
        from blender.worker import apply_material_overrides
        applied,notes=apply_material_overrides([obj for obj in products if obj.type=='MESH'],effective)
        operations.extend(applied)
        if warnings is not None:warnings.extend(notes)
    else:
        hero_scene._changed_materials(scene,products,effective,{'material_overrides':[],'object_material_overrides':[]},operations,authorized)
    scene.view_settings.exposure=effective.get('exposure',0)
    # Plain GLB/OBJ uploads have no studio. Create a documented preview rig,
    # separate from any subsequently saved production render.
    if not any(obj.type=='LIGHT' and hero_scene._visible(obj,scene) for obj in scene.objects):
        import numpy as np
        from mathutils import Vector
        low=points.min(axis=0);high=points.max(axis=0);center=Vector((low+high)/2)
        diagonal=max(float(np.linalg.norm(high-low)),1e-5)
        lighting=effective.get('lighting',{})
        for role,position,power,size in [('key',(-1,-1.2,1.8),70,1.3),('fill',(1,-.6,1),35,1.1),('rim',(.5,1,1.4),45,1)]:
            data=bpy.data.lights.new(role.title()+'Light','AREA')
            data.energy=power*diagonal*diagonal*lighting.get(role+'_power',1)
            data.shape='DISK';data.size=diagonal*size*effective.get('light_softness',1)
            obj=bpy.data.objects.new(data.name,data);scene.collection.objects.link(obj)
            obj.location=center+Vector(position)*diagonal;hero_scene._aim(obj,center)
            if role+'_azimuth' in lighting or role+'_elevation' in lighting:
                az=lighting.get(role+'_azimuth',-35 if role=='key' else 40 if role=='fill' else 145)
                el=lighting.get(role+'_elevation',45)
                obj.location=hero_scene._light_position(scene,hero_scene._bounds(points),az,el,diagonal*2)
                hero_scene._aim(obj,center)
        if scene.world is None:scene.world=bpy.data.worlds.new('Wuying viewport world')
        scene.world.use_nodes=True
        node=scene.world.node_tree.nodes.get('Background')
        if node:
            node.inputs['Color'].default_value=(1,1,1,1)
            node.inputs['Strength'].default_value=.45*lighting.get('environment',1)
        operations.append({'kind':'preview_studio'})
    else:
        # Apply explicit light intent to native original scenes relative to the
        # unmodified source. The default multiplier leaves its rig intact.
        baseline=copy.deepcopy(effective)
        baseline['lighting']={'key_power':1,'fill_power':1,'rim_power':1,'environment':1}
        baseline['light_softness']=1;baseline['exposure']=effective.get('exposure',0)
        hero_scene._checkpoint(scene,products,points,effective,baseline,shot_id,operations,authorized)
    bpy.context.view_layer.update()


def material_resources(products,root,warnings):
    import bpy
    materials={slot.material for obj in products for slot in obj.material_slots if slot.material}
    images=set();unsupported=False
    supported={'ShaderNodeBsdfPrincipled','ShaderNodeOutputMaterial','ShaderNodeTexImage','ShaderNodeUVMap',
               'ShaderNodeTexCoord','ShaderNodeMapping','ShaderNodeNormalMap','ShaderNodeSeparateColor',
               'ShaderNodeSeparateRGB','ShaderNodeRGB','ShaderNodeValue','ShaderNodeReroute'}
    visited=set()
    def nodes(tree):
        nonlocal unsupported
        if not tree or tree.as_pointer() in visited:return
        visited.add(tree.as_pointer())
        for node in tree.nodes:
            if getattr(node,'image',None):images.add(node.image)
            if node.bl_idname not in supported:unsupported=True
            if getattr(node,'node_tree',None):nodes(node.node_tree)
    for material in materials:nodes(material.node_tree)
    resources=[]
    for image in images:
        if image.source in ('MOVIE','SEQUENCE','TILED'):
            raise ValueError('实时预览暂不支持视频、序列或 UDIM 贴图，请先保存标准 PBR 场景。')
        if image.packed_file or getattr(image,'packed_files',None):continue
        if image.source=='GENERATED':continue
        if not image.filepath:raise ValueError('模型贴图缺少可读取的图像。')
        path=confined(bpy.path.abspath(image.filepath,library=image.library),root)
        resources.append({'path':path.relative_to(root).as_posix(),'sha256':sha(path)})
    if unsupported:warnings.append('部分程序纹理或复杂材质在实时画面中以 PBR 近似显示，正式渲染保留原始节点。')
    return resources


def metadata(scene,products,points,warnings,operations):
    import numpy as np
    from mathutils import Vector
    low=points.min(axis=0);high=points.max(axis=0);center=(low+high)/2
    diagonal=float(np.linalg.norm(high-low))
    if not math.isfinite(diagonal) or diagonal<=1e-10:raise ValueError('模型尺寸无效，无法建立实时视图。')
    transformed=points[:,[0,2,1]].copy();transformed[:,2]*=-1
    ylow=transformed.min(axis=0);yhigh=transformed.max(axis=0)
    camera=scene.camera;data=camera.data
    if data.type not in ('ORTHO','PERSP'):raise ValueError('实时预览需要正交或透视产品相机。')
    offset=numbers(camera.get('wuying_target_offset',[0,0,0]));target=Vector(center)+Vector(offset)*diagonal
    backward=camera.matrix_world.to_quaternion()@Vector((0,0,1))
    width=scene.render.resolution_x*scene.render.pixel_aspect_x
    height=scene.render.resolution_y*scene.render.pixel_aspect_y;aspect=width/height
    lights=[]
    for obj in scene.objects:
        if obj.type!='LIGHT' or not hero_scene._visible(obj,scene):continue
        light=obj.data;direction=obj.matrix_world.to_quaternion()@Vector((0,0,-1))
        row={'name':obj.name,'type':light.type,'position':yup(obj.matrix_world.translation),
             'direction':yup(direction),'color':numbers(light.color),'energy':float(light.energy)}
        for name in ('size','size_y','shadow_soft_size','spot_size','spot_blend'):
            if hasattr(light,name):row[name]=float(getattr(light,name))
        if hasattr(light,'shape'):row['shape']=light.shape
        lights.append(row)
    world={'color':[1,1,1],'strength':0,'has_environment_texture':False}
    if scene.world:
        world['color']=numbers(scene.world.color)
        if scene.world.use_nodes and scene.world.node_tree:
            nodes=scene.world.node_tree.nodes
            world['has_environment_texture']=any(node.bl_idname=='ShaderNodeTexEnvironment' for node in nodes)
            background=hero_scene._environment_node(scene.world)
            if background:
                world['color']=numbers(background.inputs['Color'].default_value[:3])
                world['strength']=float(background.inputs['Strength'].default_value)
                world['linked_color']=bool(background.inputs['Color'].is_linked)
    return {'schema':SCHEMA,'coordinate_system':'gltf-y-up','background':'#ffffff',
            'bounds':{'min':numbers(ylow),'max':numbers(yhigh),'center':numbers((ylow+yhigh)/2),'diagonal':diagonal},
            'bounds_blender':{'min':numbers(low),'max':numbers(high),'center':numbers(center),'diagonal':diagonal},
            'camera':{'projection':'orthographic' if data.type=='ORTHO' else 'perspective',
                      'position':yup(camera.matrix_world.translation),'target':yup(target),'up':[0,1,0],
                      'azimuth':math.degrees(math.atan2(backward.x,-backward.y)),
                      'elevation':math.degrees(math.atan2(backward.z,math.hypot(backward.x,backward.y))),
                      'focal_length':float(data.lens),'ortho_scale':float(data.ortho_scale),
                      'camera_distance':float((camera.matrix_world.translation-target).length),
                      'sensor_fit':data.sensor_fit,'sensor_width':float(data.sensor_width),
                      'fov_y':math.degrees(2*math.atan(data.sensor_width/(2*data.lens*aspect))),
                      'target_offset':offset,'near':float(data.clip_start),'far':float(data.clip_end)},
            'render':{'width':scene.render.resolution_x,'height':scene.render.resolution_y,'aspect':aspect},
            'lights':lights,'world':world,'exposure':float(scene.view_settings.exposure),
            'product_object_names':[obj.name for obj in products],
            'material_fidelity':'supported-pbr','lighting_fidelity':'webgl-approximation',
            'warnings':warnings,'applied_operations':operations}


def export_glb(scene,products,target):
    import bpy
    graph=bpy.context.evaluated_depsgraph_get();export_scene=bpy.data.scenes.new('Wuying isolated viewport')
    product_names={obj.name for obj in products}
    if any(instance.is_instance and instance.parent and instance.parent.original.name in product_names
           for instance in graph.object_instances):
        raise ValueError('该模型包含尚未实化的几何实例，请先将实例转换为网格再打开实时视图。')
    count=0;triangles=0
    for obj in products:
        if not hero_scene._visible(obj,scene):continue
        if not getattr(obj,'visible_camera',True):continue
        evaluated=obj.evaluated_get(graph)
        mesh=bpy.data.meshes.new_from_object(evaluated,preserve_all_data_layers=True,depsgraph=graph)
        if not mesh or not mesh.vertices:
            if mesh:bpy.data.meshes.remove(mesh)
            continue
        triangles+=sum(max(0,len(poly.vertices)-2) for poly in mesh.polygons)
        if triangles>4000000:raise ValueError('模型超过 400 万面，建议先简化后打开实时视图。')
        clone=bpy.data.objects.new(obj.name+' · viewport',mesh);clone.matrix_world=evaluated.matrix_world.copy()
        # Object-linked material overrides are not stored in the mesh data.
        # Replacing slots in place preserves each face's material index.
        # Clearing all slots would collapse multi-material meshes to slot zero.
        for index,slot in enumerate(evaluated.material_slots):
            if index<len(mesh.materials):mesh.materials[index]=slot.material
            else:mesh.materials.append(slot.material)
        export_scene.collection.objects.link(clone);count+=1
    if not count:raise ValueError('当前产品没有可导出的可见表面。')
    # glTF can traverse every scene by default, including a selected ground
    # object in the imported file. Explicitly constrain the active scene.
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.window.scene=export_scene
    for obj in export_scene.objects:obj.select_set(True)
    bpy.context.view_layer.objects.active=next(iter(export_scene.objects))
    parameters={'filepath':str(target),'export_format':'GLB','use_selection':True,'use_active_scene':True,'export_yup':True,
                'export_animations':False,'export_cameras':False,'export_lights':False,'export_apply':True,
                'export_extras':False,'export_materials':'EXPORT','export_texcoords':True,'export_normals':True}
    supported={prop.identifier for prop in bpy.ops.export_scene.gltf.get_rna_type().properties}
    required={'filepath','export_format','use_selection','use_active_scene','export_yup','export_animations','export_cameras','export_lights'}
    if not required<=supported:raise ValueError('当前 Blender 的 GLB 导出器不支持独立产品预览，请更新 Blender。')
    result=bpy.ops.export_scene.gltf(**{key:value for key,value in parameters.items() if key in supported})
    if 'FINISHED' not in result or not target.is_file():raise ValueError('GLB 实时模型未生成。')
    with target.open('rb') as stream:
        _,_,_,size,_=struct.unpack('<4sIIII',stream.read(20))
        gltf=json.loads(stream.read(size).decode('utf-8').rstrip('\0 \t\r\n'))
    nodes=[node for node in gltf.get('nodes',[]) if 'mesh' in node]
    if len(nodes)!=count or {node.get('name') for node in nodes}!={obj.name for obj in export_scene.objects}:
        raise ValueError('导出的实时模型包含产品范围外的对象，未收录此结果。')
    return {'objects':count,'triangles':triangles,'bytes':target.stat().st_size}


def run(path):
    request=json.loads(path.read_text(encoding='utf-8'))
    if request.get('schema')!=SCHEMA or not re.fullmatch(r'[a-f0-9]{32}',str(request.get('request_id',''))):
        raise ValueError('实时预览请求无效。')
    root=Path(request['project_root']).resolve()
    out=confined(request['output_dir'],root,False)
    if not out.is_dir() or path.parent.resolve()!=out:raise ValueError('实时预览输出目录无效。')
    source,scene,products,resources=open_source(request,root)
    points=hero_scene._world_vertices(products);operations=[];authorized=[]
    warnings=['实时光照、阴影与反射采用交互式近似，正式成图以 Blender 渲染为准。']
    if request.get('baseline_plan') is not None:
        hero_scene._checkpoint(scene,products,points,request['plan'],request['baseline_plan'],request['shot_id'],operations,authorized)
    else:
        original_plan(scene,products,points,request['plan'],request['shot_id'],operations,authorized,
                      fresh_upload=source.suffix.lower() in ('.obj','.glb'),root=root,warnings=warnings)
        warnings.append('当前实时画面从上传的模型准备，可在正式出图前旋转查看。')
    resources+=material_resources(products,root,warnings)
    manifest=metadata(scene,products,points,warnings,operations)
    manifest['geometry']=export_glb(scene,products,out/'scene.glb')
    if sha(source)!=request['source_sha256']:raise ValueError('导出过程中模型发生变化，请重试。')
    for row in resources:
        if sha(confined(root/row['path'],root))!=row['sha256']:raise ValueError('导出过程中贴图发生变化，请重试。')
    manifest['resources']=list({row['path']:row for row in resources}.values())
    write(out/'result.json',{'success':True,'request_id':request['request_id'],
                           'source_sha256':request['source_sha256'],'manifest':manifest})


if __name__=='__main__':
    path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    try:run(path)
    except Exception as exc:
        try:
            request=json.loads(path.read_text(encoding='utf-8'))
            root=Path(request['project_root']).resolve();out=confined(request['output_dir'],root,False)
            if path.parent==out:write(out/'result.json',{'success':False,'request_id':request.get('request_id'),'error':str(exc)[:500]})
        except Exception:pass
        raise

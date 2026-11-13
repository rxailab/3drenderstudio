"""Headless Blender executor. Invoked only with validated data, never AI code."""
import sys, os, json, math, traceback, time, re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from asset_provenance import fallback_material_names
import bpy
import bmesh
from mathutils import Vector, Euler
from bpy_extras.object_utils import world_to_camera_view


def event(stage, **kw):
    print('EVENT:' + json.dumps(dict(stage=stage, **kw), ensure_ascii=False), flush=True)


def atomic(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def bounds(objects):
    dg = bpy.context.evaluated_depsgraph_get()
    points = [ob.evaluated_get(dg).matrix_world @ Vector(v)
              for ob in objects if ob.type == 'MESH' for v in ob.evaluated_get(dg).bound_box]
    if not points:
        raise ValueError('模型没有可渲染网格')
    lo = Vector([min(v[i] for v in points) for i in range(3)])
    hi = Vector([max(v[i] for v in points) for i in range(3)])
    return lo, hi, points


def load_asset(path):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    p = Path(path).resolve()
    if p.suffix.lower() == '.obj':
        bpy.ops.wm.obj_import(filepath=str(p), forward_axis='NEGATIVE_Z', up_axis='Y')
    elif p.suffix.lower() == '.glb':
        bpy.ops.import_scene.gltf(filepath=str(p), import_pack_images=True)
    else:
        raise ValueError('首版仅支持 OBJ 和 GLB')
    objects = list(bpy.context.scene.objects)
    meshes = [o for o in objects if o.type == 'MESH' and len(o.data.polygons)]
    if not meshes:
        raise ValueError('导入的模型为空，或没有可渲染的面')
    lo, hi, _ = bounds(meshes)
    if max(hi - lo) < 1e-9:
        raise ValueError('模型尺寸为零')
    for ob in objects:
        if ob.type in ('LIGHT', 'CAMERA'):
            ob.hide_render = True
    return objects, meshes


def bsdf_of(mat):
    if mat and mat.use_nodes:
        return next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    return None


def input_value(node, name, fallback):
    if node and name in node.inputs:
        val = node.inputs[name].default_value
        return list(val) if hasattr(val, '__len__') else val
    return fallback


def analyze(objects, meshes, path):
    lo, hi, _ = bounds(meshes)
    dims = list(hi - lo)
    positive = sorted([max(d, 1e-8) for d in dims])
    warnings = []
    rows = []
    for ob in objects:
        row = dict(name=ob.name, type=ob.type, parent=ob.parent.name if ob.parent else None,
                   dimensions=list(ob.dimensions), location=list(ob.location), rotation=list(ob.rotation_euler),
                   animated=bool(ob.animation_data), constraints=[c.type for c in ob.constraints])
        if ob.type == 'MESH':
            bm = bmesh.new(); bm.from_mesh(ob.data)
            raw_non_manifold=sum(not e.is_manifold for e in bm.edges)
            # glTF legally duplicates vertices at UV/normal seams. Diagnose geometry
            # on a temporary welded copy; never weld the product or lose its UVs.
            eps=max(max(ob.dimensions),1e-6)*1e-6
            bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=eps)
            row.update(vertices=len(ob.data.vertices), faces=len(ob.data.polygons),
                       uv_layers=[uv.name for uv in ob.data.uv_layers],
                       materials=[s.material.name if s.material else None for s in ob.material_slots],
                       non_manifold_edges=sum(not e.is_manifold for e in bm.edges),raw_non_manifold_edges=raw_non_manifold,
                       degenerate_faces=sum(f.calc_area() < 1e-12 for f in bm.faces))
            bm.free()
            if not row['materials'] or any(name is None for name in row['materials']):
                row['original_materials']=list(row['materials'])
                row['materials']=[name or 'Default material' for name in row['materials']] or ['Default material']
                row['default_material_assigned']=True
            if not row['uv_layers']: warnings.append(ob.name + '：没有 UV；程序材质使用物体坐标映射，接缝与拉伸需目视确认')
            if row['non_manifold_edges']: warnings.append(ob.name + '：存在开放或非流形边；可能是薄片/中空结构，不自动封闭')
            if row['degenerate_faces']: warnings.append(ob.name + '：存在退化面，可在建模软件中检查')
        rows.append(row)
    mats = []
    fallback_names=fallback_material_names(path)
    materials = list({m.name: m for ob in meshes for m in ob.data.materials if m}.values())
    for mat in materials:
        node = bsdf_of(mat)
        metal = input_value(node, 'Metallic', 0)
        transmission = input_value(node, 'Transmission Weight', 0)
        rough = input_value(node, 'Roughness', .5)
        emission = input_value(node, 'Emission Strength', 0)
        preset = 'glass' if transmission > .3 else ('polished_metal' if metal > .5 and rough < .2 else 'brushed_metal' if metal > .5 else 'matte_plastic')
        textures = [dict(name=n.image.name, path=bpy.path.abspath(n.image.filepath), color_space=n.image.colorspace_settings.name,
                         packed=bool(n.image.packed_file), missing=not bool(n.image.packed_file) and not Path(bpy.path.abspath(n.image.filepath)).is_file())
                    for n in (mat.node_tree.nodes if mat.use_nodes else []) if n.type == 'TEX_IMAGE' and n.image]
        is_fallback=mat.name in fallback_names
        mats.append(dict(name=mat.name, source='fallback' if is_fallback else 'model', confidence=0.0 if is_fallback else 1.0, suggested_preset=None if is_fallback else preset,
                         suggestion_source='rules', suggestion_confidence=0.0 if is_fallback else .55, color=input_value(node, 'Base Color', list(mat.diffuse_color)),
                         roughness=rough, metallic=metal, transmission=transmission, emission=emission, textures=textures))
        warnings += [mat.name + '：缺失贴图 ' + t['name'] for t in textures if t['missing']]
    if (not mats or any(row.get('default_material_assigned') for row in rows)) and not any(m['name']=='Default material' for m in mats):
        mats.append(dict(name='Default material', source='default', confidence=0.0, suggested_preset='matte_plastic',
                         suggestion_source='rules', suggestion_confidence=.1, color=[.55,.58,.62,1], roughness=.4, metallic=0, transmission=0, textures=[]))
        warnings.append('部分或全部网格未提供材质，使用可编辑的中性哑光默认材质')
    if fallback_names: warnings.append('模型引用的 MTL 标明为替代材质，颜色与材质参数不代表原始产品；请以实际材质信息为准')
    known = Path(path).suffix.lower() == '.glb'
    if not known: warnings.append('OBJ 没有可靠单位信息；真实尺寸未知，工作场景统一缩放用于拍摄')
    warnings.append('朝向、主展示面、法线方向与真实支撑面需通过预览确认；原始资产不做修改')
    return dict(success=True, features=dict(dimensions=dims, aspect_ratio=positive[-1]/positive[0],
                elongated=positive[-1]/positive[-2] > 3, flat=positive[0]/positive[-1] < .12,
                multipart=len(meshes)>1, mesh_count=len(meshes), symmetry='unknown',
                surfaces=list(set('transparent' if m.get('transmission',0)>.3 else 'metal' if m.get('metallic',0)>.5 else 'dielectric' for m in mats)),
                has_animation=any(o.animation_data for o in objects), has_armature=any(o.type=='ARMATURE' for o in objects)),
                bounds=dict(min=list(lo),max=list(hi)), dimensions=dict(value=dims,unit='m' if known else 'unknown',source='glTF specification' if known else 'unknown',reliability='declared' if known else 'unknown'),
                objects=rows,materials=mats,warnings=list(dict.fromkeys(warnings)),
                animations=[dict(name=a.name,frame_range=list(a.frame_range)) for a in bpy.data.actions],
                blender_version=bpy.app.version_string)


def device(force_cpu=False):
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    prefs = bpy.context.preferences.addons['cycles'].preferences
    found = []
    if not force_cpu:
        for api in ('OPTIX','CUDA','HIP','ONEAPI','METAL'):
            try:
                prefs.compute_device_type = api
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == api]
                if gpus:
                    for d in prefs.devices: d.use = d.type == api
                    scene.cycles.device = 'GPU'
                    found = [d.name for d in gpus]
                    result = dict(status='ready',type='GPU',backend=api,name=', '.join(found),blender_version=bpy.app.version_string)
                    event('使用 GPU 渲染',device=result)
                    return result
            except Exception: pass
    scene.cycles.device = 'CPU'
    result = dict(status='ready',type='CPU',backend='CPU',name='CPU',reason='用户指定或 GPU 不可用',blender_version=bpy.app.version_string)
    event('使用 CPU 渲染',device=result)
    return result


PRESETS = {
 'matte_plastic':(.4,0,0,(.38,.43,.5)), 'glossy_plastic':(.16,0,0,(.38,.43,.5)),
 'painted_metal':(.28,.65,0,(.2,.3,.4)), 'brushed_metal':(.28,1,0,(.65,.68,.72)),
 'polished_metal':(.09,1,0,(.7,.72,.75)), 'glass':(.04,0,1,(.94,.98,1)),
 'rubber':(.72,0,0,(.035,.035,.04)), 'fabric':(.82,0,0,(.32,.39,.48)),
 'leather':(.5,0,0,(.22,.085,.035)), 'wood':(.38,0,0,(.36,.17,.06)),
 'ceramic':(.18,0,0,(.85,.83,.78))}


def rgb(color):
    if isinstance(color,str):
        vals=[int(color[i:i+2],16)/255 for i in (1,3,5)]
        return [v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in vals]+[1]
    return list(color[:3])+[1]


def configure_material(mat, override, plan):
    preset = override.get('preset','matte_plastic')
    rough,metal,trans,col = PRESETS[preset]
    mat.use_nodes=True
    tree=mat.node_tree; nodes=tree.nodes; links=tree.links
    bs=bsdf_of(mat)
    if override.get('object_name'):
        # Edit the shader that actually drives the active output, not a stale
        # disconnected Principled node left elsewhere in the material graph.
        output=next((node for node in nodes if node.type=='OUTPUT_MATERIAL' and node.is_active_output),None)
        surface=output.inputs.get('Surface') if output else None
        active=surface.links[0].from_node if surface and surface.is_linked else None
        bs=active if active and active.type=='BSDF_PRINCIPLED' else None
        if not bs and override.get('preserve_original',False):
            raise ValueError('该部件使用复杂材质节点，请明确选择内置材质预设后再修改')
    if not bs:
        bs=nodes.new('ShaderNodeBsdfPrincipled')
        out=next((n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output),None) or nodes.new('ShaderNodeOutputMaterial')
        links.new(bs.outputs['BSDF'],out.inputs['Surface'])
    # Retain image-fed base color, normals and alpha unless replacement was explicit.
    preserve=override.get('preserve_original',False)
    keep_local_color=bool(override.get('object_name')) and 'color' not in override
    if 'color' in override or (not preserve and not keep_local_color and not bs.inputs['Base Color'].is_linked):
        if 'color' in override:
            for link in list(bs.inputs['Base Color'].links): links.remove(link)
        bs.inputs['Base Color'].default_value=rgb(override.get('color',col))
    for key,socket,value in [('roughness','Roughness',rough),('metallic','Metallic',metal),('transmission','Transmission Weight',trans)]:
        if preserve and key not in override: continue
        if socket in bs.inputs:
            if key in override or (not preserve and 'preset' in override):
                for link in list(bs.inputs[socket].links): links.remove(link)
            if not bs.inputs[socket].is_linked: bs.inputs[socket].default_value=override.get(key,value)
    if not preserve: bs.inputs['IOR'].default_value=1.46 if preset=='glass' else 1.5
    # A clear coat is what separates sprayed or moulded gloss from flat diffuse colour.
    coat=override.get('coat')
    if coat is None and plan.get('look')=='showcase' and 'preset' in override and not preserve:
        coat={'painted_metal':.6,'glossy_plastic':.3,'ceramic':.3}.get(preset)
    if coat is not None:
        for socket,value in (('Coat Weight',coat),('Coat Roughness',.06),('Coat IOR',1.5)):
            if socket in bs.inputs and not bs.inputs[socket].is_linked: bs.inputs[socket].default_value=value
    if not preserve and 'Anisotropic IOR Level' in bs.inputs: bs.inputs['Anisotropic IOR Level'].default_value=.65 if preset=='brushed_metal' else 0
    texcoord=nodes.new('ShaderNodeTexCoord'); mapping=nodes.new('ShaderNodeMapping')
    mapping.inputs['Scale'].default_value=(plan.get('texture_scale',1),)*3
    mapping.inputs['Rotation'].default_value[2]=math.radians(plan.get('texture_rotation',0))
    links.new(texcoord.outputs['Object'],mapping.inputs['Vector'])
    if not preserve and preset in ('brushed_metal','fabric','leather','wood') and not bs.inputs['Normal'].is_linked:
        noise=nodes.new('ShaderNodeTexNoise'); bump=nodes.new('ShaderNodeBump')
        links.new(mapping.outputs['Vector'],noise.inputs['Vector'])
        noise.inputs['Scale'].default_value={'brushed_metal':180,'fabric':150,'leather':90,'wood':4}[preset]
        noise.inputs['Detail'].default_value=2
        if preset=='brushed_metal': mapping.inputs['Scale'].default_value=tuple(v*plan.get('texture_scale',1) for v in (1,70,1))
        if preset=='wood': mapping.inputs['Scale'].default_value=tuple(v*plan.get('texture_scale',1) for v in (3,3,.2))
        bump.inputs['Strength'].default_value=.13 if preset=='brushed_metal' else .22
        bump.inputs['Distance'].default_value=.0015
        links.new(noise.outputs['Fac'],bump.inputs['Height']); links.new(bump.outputs['Normal'],bs.inputs['Normal'])
        if preset=='wood' and not keep_local_color and not bs.inputs['Base Color'].is_linked:
            ramp=nodes.new('ShaderNodeValToRGB')
            ramp.color_ramp.elements[0].color=(.08,.025,.008,1);ramp.color_ramp.elements[1].color=rgb(override.get('color',col))
            links.new(noise.outputs['Fac'],ramp.inputs[0]);links.new(ramp.outputs['Color'],bs.inputs['Base Color'])
    resource_maps=[];resource_warnings=[]
    users=[ob for ob in bpy.context.scene.objects if ob.type=='MESH' and mat in list(ob.data.materials)]
    has_uv=bool(users) and all(bool(ob.data.uv_layers) for ob in users)
    scale=override.get('texture_scale',plan.get('texture_scale',1))
    rotation=override.get('texture_rotation',plan.get('texture_rotation',0))
    external_mapping=None
    for key,path in override.get('textures',{}).items():
        if key not in ('base_color','roughness','metallic','normal','transmission'): continue
        # Tangent-space normal maps require real UV tangents. A box-projected
        # normal map fed into a tangent node is incorrect, so retain the old
        # normal input and explicitly report the un-applied downloaded map.
        if key=='normal' and not has_uv:
            warning=mat.name+'：缺少 UV，外部切线法线贴图未应用；保留原法线。其他贴图使用盒式投影，请检查接缝与纹理尺度'
            resource_warnings.append(warning)
            resource_maps.append(dict(channel=key,path=str(path),applied=False,reason='missing_uv'))
            continue
        color_space='sRGB' if key=='base_color' else 'Non-Color'
        image=bpy.data.images.load(str(path),check_existing=True)
        # A file can already drive a label or another channel. Do not mutate
        # that image datablock's color interpretation for unselected surfaces.
        if image.users and image.colorspace_settings.name!=color_space: image=image.copy()
        image.colorspace_settings.name=color_space
        node=nodes.new('ShaderNodeTexImage');node.image=image
        node.name='External '+key
        # Resource maps follow the same user scale/rotation. Missing UV uses box projection.
        if external_mapping is None:
            external_mapping=nodes.new('ShaderNodeMapping');external_mapping.name='External resource mapping'
            external_mapping.inputs['Scale'].default_value=(scale,)*3
            external_mapping.inputs['Rotation'].default_value[2]=math.radians(rotation)
            links.new(texcoord.outputs['UV' if has_uv else 'Generated'],external_mapping.inputs['Vector'])
        links.new(external_mapping.outputs['Vector'],node.inputs['Vector'])
        if not has_uv: node.projection='BOX';node.projection_blend=.2
        if key=='normal':
            normal_color=node.outputs['Color']
            if rotation:
                # Rotating texture coordinates rotates the pattern in the
                # opposite direction. Rotate tangent normals by that same
                # inverse angle so highlights follow the new weave/grain.
                decode=nodes.new('ShaderNodeVectorMath');decode.operation='SUBTRACT';decode.inputs[1].default_value=(.5,.5,.5)
                rotate=nodes.new('ShaderNodeVectorRotate');rotate.rotation_type='AXIS_ANGLE'
                rotate.name='External normal direction';rotate.inputs['Axis'].default_value=(0,0,1)
                rotate.inputs['Angle'].default_value=-math.radians(rotation)
                encode=nodes.new('ShaderNodeVectorMath');encode.operation='ADD';encode.inputs[1].default_value=(.5,.5,.5)
                links.new(normal_color,decode.inputs[0]);links.new(decode.outputs['Vector'],rotate.inputs['Vector'])
                links.new(rotate.outputs['Vector'],encode.inputs[0]);normal_color=encode.outputs['Vector']
            normal=nodes.new('ShaderNodeNormalMap');links.new(normal_color,normal.inputs['Color']);links.new(normal.outputs['Normal'],bs.inputs['Normal'])
        else:
            links.new(node.outputs['Color'],bs.inputs[{'base_color':'Base Color','roughness':'Roughness','metallic':'Metallic','transmission':'Transmission Weight'}[key]])
        resource_maps.append(dict(channel=key,path=str(path),applied=True,color_space=color_space,
                                  mapping='UV' if has_uv else 'Generated box',scale=scale,rotation_degrees=rotation))
    if resource_maps and not has_uv and not resource_warnings:
        resource_warnings.append(mat.name+'：缺少 UV，外部贴图使用盒式投影，请检查接缝与纹理尺度')
    provenance=dict(source='user',preset=preset,resource_source=override.get('source'),license=override.get('license'))
    if override.get('textures'):
        provenance.update(resource=override.get('resource'),maps=resource_maps,warnings=resource_warnings,
                          preserves_original_color='color' not in override and 'base_color' not in override['textures'])
    mat['provenance']=json.dumps(provenance,ensure_ascii=False)
    return dict(material=mat.name,maps=resource_maps,warnings=resource_warnings)


def apply_material_overrides(meshes,plan):
    """Apply the same isolated material edits to any freshly imported model."""
    operations=[];warnings=[]
    for ob in meshes:
        if not ob.data.materials or any(mat is None for mat in ob.data.materials):
            mat=bpy.data.materials.get('Default material') or bpy.data.materials.new('Default material')
            configure_material(mat,{'preset':'matte_plastic'},plan)
            if not ob.data.materials: ob.data.materials.append(mat)
            else:
                for index in range(len(ob.data.materials)):
                    if ob.data.materials[index] is None: ob.data.materials[index]=mat
    for override in plan.get('material_overrides',[]):
        name=override.get('name','*')
        mats={m.name:m for ob in meshes for m in ob.data.materials if m and (name in ('*','all') or m.name==name)}
        resources=[]
        for mat in mats.values():
            configured=configure_material(mat,override,plan)
            if override.get('textures'): resources.append(configured);warnings.extend(configured['warnings'])
        operation=dict(operation='material_override',parameters=override,applied_to=list(mats))
        if resources: operation['resources']=resources
        operations.append(operation)
    copied_objects=set()
    for override in plan.get('object_material_overrides',[]):
        ob=next((o for o in meshes if o.name==override['object_name']),None)
        if ob is None: raise ValueError('局部材质的部件不存在：'+override['object_name'])
        if ob.name not in copied_objects:
            ob.data=ob.data.copy();copied_objects.add(ob.name)
        applied=[];resources=[]
        requested=override['material_name']
        slots=[slot for slot in ob.material_slots if slot.material]
        exact=[slot for slot in slots if slot.material.get('_studio_source_material',slot.material.name)==requested]
        if exact:
            selected=exact
        else:
            # A rendered checkpoint can expose a Blender-generated duplicate
            # name (e.g. Paint.008), while a different shot starts from the
            # uploaded asset. Resolve it only after this object has already
            # established the original slot and only when no other slot has
            # a matching base name. Never widen to a shared material user.
            base=lambda value:re.sub(r'(?:\.\d{3})+$','',value)
            compatible=[slot for slot in slots if base(slot.material.get('_studio_source_material',slot.material.name))==base(requested)]
            selected=(compatible if len(compatible)==1 and
                      compatible[0].material.get('_studio_source_material') is not None and
                      base(requested)!=requested else [])
        for slot in selected:
            original=slot.material
            mat=original.copy();mat['_studio_source_material']=original.get('_studio_source_material',original.name)
            slot.material=mat;configured=configure_material(mat,override,plan);applied.append(mat.name)
            if override.get('textures'): resources.append(configured);warnings.extend(configured['warnings'])
        if not applied: raise ValueError('局部材质槽不存在：'+override['material_name'])
        operation=dict(operation='object_material_override',object=ob.name,parameters=override,applied_to=applied)
        if resources: operation['resources']=resources
        operations.append(operation)
    return operations,warnings


def prepare(objects,meshes,plan):
    scene=bpy.context.scene
    operations=[];warnings=[]
    # Missing external resources must never become magenta or break project packing.
    # Work on this imported scene only; uploaded originals remain untouched.
    for image in list(bpy.data.images):
        if image.source!='FILE' or image.packed_file: continue
        image_path=bpy.path.abspath(image.filepath)
        if image_path and not Path(image_path).is_file():
            warnings.append('缺失贴图 '+image.name+'；已断开该贴图并回退材质参数，请补充资源后重试')
            operations.append(dict(operation='missing_texture_fallback',image=image.name,path=image_path))
            for mat in bpy.data.materials:
                if mat.use_nodes:
                    for node in list(mat.node_tree.nodes):
                        if node.type=='TEX_IMAGE' and node.image==image: mat.node_tree.nodes.remove(node)
            bpy.data.images.remove(image)
    if plan.get('pose',{}).get('frame') is not None:
        scene.frame_set(int(plan['pose']['frame']));operations.append(dict(operation='animation_frame',frame=scene.frame_current))
    pose=plan.get('pose',{});ob=bpy.data.objects.get(pose.get('object',''))
    if pose.get('object') and (ob is None or ob not in objects):
        raise ValueError('姿态目标部件不存在：'+str(pose['object']))
    if ('rotation' in pose or 'translation' in pose) and not pose.get('object'):
        raise ValueError('请先指定需要移动的部件')
    if ob and ob in objects:
        if 'rotation' in pose: ob.rotation_euler.rotate(Euler([math.radians(v) for v in pose['rotation']]))
        if 'translation' in pose: ob.location+=Vector(pose['translation'])
        operations.append(dict(operation='user_pose',parameters=pose))
        warnings.append('用户定义部件运动：轴心、附件归属及穿插需目视确认')
    root=bpy.data.objects.new('Studio asset transform',None);scene.collection.objects.link(root)
    for ob in objects:
        if ob.parent is None:
            matrix=ob.matrix_world.copy();ob.parent=root;ob.matrix_world=matrix
    root.rotation_euler=Euler([math.radians(v) for v in plan.get('rotation',[0,0,0])])
    bpy.context.view_layer.update()
    lo,hi,_=bounds(meshes);scale=2/max(hi-lo)
    root.scale=(scale,)*3
    bpy.context.view_layer.update();lo,hi,_=bounds(meshes)
    root.location=-Vector(((lo.x+hi.x)/2,(lo.y+hi.y)/2,lo.z))
    bpy.context.view_layer.update()
    operations.append(dict(operation='working_transform',rotation_degrees=plan.get('rotation',[0,0,0]),uniform_scale=scale,translation=list(root.location)))
    dims=plan.get('dimensions_mm',plan.get('dimension_mm'))
    if dims:
        actual=[float(v) for v in dims]
        work=list(hi-lo);physical_scale=max(actual)/2000
        root['physical_meters_per_work_unit']=physical_scale
        predicted=[d*physical_scale*1000 for d in work]
        mismatch=max(abs(a-b)/max(a,1e-6) for a,b in zip(actual,predicted))
        operations.append(dict(operation='user_dimensions',dimensions_mm=actual,uniform_physical_scale=physical_scale,predicted_dimensions_mm=predicted,relative_mismatch=mismatch))
        if mismatch>.15: warnings.append('补充尺寸与模型比例不一致；按最长边统一缩放，不拉伸产品形状')
        # Procedural surface scale is proportional to real size, independent of studio normalization.
        plan=dict(plan,texture_scale=plan.get('texture_scale',1)*max(actual)/500)
    for ob in meshes:
        if plan.get('repair',{}).get('normals'):
            bm=bmesh.new();bm.from_mesh(ob.data);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(ob.data);bm.free()
            operations.append(dict(operation='recalculate_normals',object=ob.name))
        if plan.get('repair',{}).get('smooth'):
            for poly in ob.data.polygons: poly.use_smooth=True
            operations.append(dict(operation='smooth_shading',object=ob.name))
    material_operations,material_warnings=apply_material_overrides(meshes,plan)
    operations.extend(material_operations);warnings.extend(material_warnings)
    return operations,warnings


def aim(ob,target):
    ob.rotation_euler=(Vector(target)-ob.location).to_track_quat('-Z','Y').to_euler()


def area(name,loc,power,size,color,target=(0,0,.8)):
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;data.color=color
    ob=bpy.data.objects.new(name,data);bpy.context.scene.collection.objects.link(ob);ob.location=loc;aim(ob,target)
    return dict(name=name,position=list(loc),energy=power,size=size,color=list(color))


def metal_reflection_light(meshes, soft, white, key_multiplier):
    """Aim one small softbox into the reflected camera direction of a visible metal face."""
    import heapq
    scene=bpy.context.scene;cam=scene.camera
    if cam is None: return None
    candidates=[];serial=0
    for ob in meshes:
        matids={i for i,slot in enumerate(ob.material_slots) if slot.material and input_value(bsdf_of(slot.material),'Metallic',0)>.5}
        if not matids: continue
        matrix=ob.matrix_world;normal_matrix=matrix.to_3x3().inverted_safe().transposed()
        determinant=abs(matrix.to_3x3().determinant())
        for face in ob.data.polygons:
            if face.material_index not in matids or face.area<=0: continue
            normal=normal_matrix@face.normal
            if normal.length<1e-8: continue
            scaled_area=face.area*determinant*normal.length
            normal.normalize();center=matrix@face.center;view=cam.location-center
            if view.length<.001: continue
            facing=normal.dot(view.normalized())
            if facing<.15: continue
            reflection=2*facing*normal-view.normalized()
            if reflection.z<.1: continue
            score=scaled_area*facing
            if score<1e-6: continue
            serial+=1;item=(score,serial,ob,center,reflection)
            if len(candidates)<32: heapq.heappush(candidates,item)
            elif score>candidates[0][0]: heapq.heapreplace(candidates,item)
    deps=bpy.context.evaluated_depsgraph_get();lo,hi,_=bounds(meshes);span=max(hi-lo)
    for _,__,ob,center,reflection in sorted(candidates,key=lambda c:c[0],reverse=True):
        toward=center-cam.location;distance=toward.length
        hit,hit_location,_,_,hit_ob,_=scene.ray_cast(deps,cam.location,toward.normalized(),distance=distance+.005)
        if not hit or (hit_ob.original!=ob and hit_ob.name!=ob.name): continue
        if (hit_location-center).length>max(.002,span*.002): continue
        offset=max(1.2,min(4.0,span*1.3))
        location=center+reflection.normalized()*offset
        entry=area('Metal reflection softbox',location,(.5 if white else 1)*key_multiplier,
                   max(.45,min(2.4,span*.65))*soft,(1,1,1),center)
        light=bpy.data.lights[entry['name']];light.shape='RECTANGLE';light.size_y=max(.2,min(.8,span*.22))*soft
        bpy.data.objects[entry['name']].visible_camera=False
        entry.update(energy=light.energy,shape=light.shape,size=light.size,size_y=light.size_y,
                     target_object=ob.name,target=list(center),method='visible metal face reflected camera direction')
        return entry
    return None


def soften_edges(meshes):
    """Give hard CAD edges a hairline highlight without changing any geometry.

    A Cycles bevel node rounds the shading normal at render time. Materials that
    already drive their normal (bump, normal map) keep it.
    """
    lo,hi,_=bounds(meshes);radius=max(hi-lo)*.0022;count=0
    for mat in {m.name:m for ob in meshes for m in ob.data.materials if m}.values():
        bs=bsdf_of(mat)
        if not bs or 'Normal' not in bs.inputs or bs.inputs['Normal'].is_linked: continue
        bevel=mat.node_tree.nodes.new('ShaderNodeBevel');bevel.name='Studio edge highlight'
        bevel.samples=4;bevel.inputs['Radius'].default_value=radius
        mat.node_tree.links.new(bevel.outputs['Normal'],bs.inputs['Normal']);count+=1
    return dict(operation='edge_highlight',radius=radius,materials=count)


def showcase_rig(scene,shot,soft,white,dark,meshes=()):
    """A product-photography set: contrast from a key, shape from rear strips.

    Every lamp is placed relative to the camera, so each shot keeps the same
    modelling whatever its viewing angle. Key, fill and edge keep the names the
    light-direction controls look for. The product is normalised to two units,
    so distances are fixed.
    """
    azimuth=math.radians(shot.get('azimuth',35));target=Vector((0,0,.85))
    def place(relative,elevation,distance):
        a=azimuth+math.radians(relative);e=math.radians(elevation)
        return target+Vector((math.sin(a)*math.cos(e),-math.cos(a)*math.cos(e),math.sin(e)))*distance
    def lamp(name,relative,elevation,distance,power,width,height,color,shadow=True):
        entry=area(name,place(relative,elevation,distance),power,width,color,target)
        light=bpy.data.lights[entry['name']];light.shape='RECTANGLE';light.size=width;light.size_y=height
        light.use_shadow=shadow;light.spread=math.radians(150)
        bpy.data.objects[entry['name']].visible_camera=False
        entry.update(shape='RECTANGLE',size=width,size_y=height,relative_azimuth=relative,elevation=elevation,casts_shadow=shadow)
        return entry
    # The key faces the camera-left side, the fill only opens the other side:
    # roughly two stops apart, which is what gives a box its volume.
    level=.12 if white else .11 if not dark else .1
    lights=[lamp('Key softbox',-46,42,4.2,980*level,3.2*soft,2.4*soft,(1,.965,.93)),
            lamp('Fill softbox',50,16,7,125*level,4.4*soft,3.4*soft,(.93,.96,1),shadow=False),
            lamp('Edge softbox',140,32,6,1200*level,.75*soft,4*soft,(1,1,1),shadow=False),
            lamp('Edge strip left',-130,24,6,1000*level,.6*soft,4*soft,(1,1,1),shadow=False),
            lamp('Top reflection',0,82,5.4,420*level,4.2*soft,4.2*soft,(1,1,1))]
    # Horizontal metal seen from the front mirrors what stands low behind the set.
    # A wide card there is what makes a steel top read as steel.
    card=lamp('Back reflection card',180,14,7.5,900*level,7*soft,3*soft,(1,1,1),shadow=False)
    bpy.data.lights[card['name']].diffuse_factor=.15
    # A vertical glossy panel mirrors the low area on the far side of its normal.
    # A reflection-only card there, placed for the box face nearest the camera,
    # sweeps a soft sheen over the upper part of the panel and leaves the rest deep.
    degrees=shot.get('azimuth',35);facing=round(degrees/90)*90
    sweep=lamp('Front sweep card',2*(facing-degrees),0,3,90*level,3.6*soft,1.1*soft,(1,1,1),shadow=False)
    holder=bpy.data.objects[sweep['name']];holder.location.z=.45;aim(holder,(0,0,.7));holder.data.diffuse_factor=0
    sweep['position']=list(holder.location)
    lights+=[card,sweep]
    if meshes and 'light_linking' in bpy.types.Object.bl_rna.properties:
        # Shaping lamps light the product only; the floor shadow comes from key and top.
        receivers=bpy.data.collections.new('Studio product receivers')
        for ob in meshes: receivers.objects.link(ob)
        for entry in lights:
            if not entry['casts_shadow']: bpy.data.objects[entry['name']].light_linking.receiver_collection=receivers
        floor=bpy.data.objects.get('Studio floor')
        if floor and not white and not dark:
            # Softboxes fall off within a few units and would leave a dark band where
            # a low camera sees far floor. An even wash on the backdrop alone keeps it
            # seamless; the product still blocks it, which deepens the contact shadow.
            data=bpy.data.lights.new('Backdrop wash','SUN');data.energy=1.1;data.angle=math.radians(40)
            wash=bpy.data.objects.new('Backdrop wash',data);scene.collection.objects.link(wash)
            wash.location=(0,0,9);wash.rotation_euler=(0,0,0)
            backdrop=bpy.data.collections.new('Studio backdrop receivers');backdrop.objects.link(floor)
            wash.light_linking.receiver_collection=backdrop
            lights.append(dict(name='Backdrop wash',position=[0,0,9],energy=1.1,size=0,color=[1,1,1],casts_shadow=True,shape='SUN'))
    return lights


def studio(plan,shot,meshes):
    scene=bpy.context.scene;style=shot.get('style',plan.get('style','soft'))
    dark=style=='dark';white=style=='white';soft=plan.get('light_softness',1)
    scene.world=bpy.data.worlds.new('Studio environment');scene.world.use_nodes=True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.8,.85,1,1) if not dark else (.35,.42,.55,1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value=.22 if not dark else .07
    bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.002))
    floor=bpy.context.object;floor.name='Studio floor'
    mat=bpy.data.materials.new('Studio floor material');mat.use_nodes=True
    bs=bsdf_of(mat);bs.inputs['Base Color'].default_value=(.92,.92,.92,1) if white else (.57,.61,.67,1) if not dark else (.018,.025,.04,1)
    bs.inputs['Roughness'].default_value=.7;floor.data.materials.append(mat)
    if white:
        # Camera sees neutral white; other rays retain a moderate physical environment.
        # Cycles shadow catcher carries the real contact shadow onto this background.
        floor.is_shadow_catcher=True
        nodes=scene.world.node_tree.nodes;links=scene.world.node_tree.links
        light_path=nodes.new('ShaderNodeLightPath');background=nodes.new('ShaderNodeBackground')
        background.inputs['Color'].default_value=(1,1,1,1);background.inputs['Strength'].default_value=1
        mix=nodes.new('ShaderNodeMixShader')
        links.new(light_path.outputs['Is Camera Ray'],mix.inputs[0]);links.new(nodes['Background'].outputs[0],mix.inputs[1]);links.new(background.outputs[0],mix.inputs[2])
        links.new(mix.outputs[0],nodes['World Output'].inputs['Surface'])
    showcase=plan.get('look')=='showcase'
    if showcase:
        lights=showcase_rig(scene,shot,soft,white,dark,meshes)
        # A dim neutral surround: enough for open shadows, never a flat wash.
        scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.9,.93,1,1) if not dark else (.3,.36,.48,1)
        scene.world.node_tree.nodes['Background'].inputs[1].default_value=.08 if not dark else .04
        # A white sweep throws a lot of bounce back into the product and veils its
        # colour. The catcher only reports shadow, so a darker floor costs nothing.
        if white: bs.inputs['Base Color'].default_value=(.45,.45,.45,1)
    else:
        lights=[area('Key softbox',(-3,-4,5),650,3*soft,(1,.94,.87)),
                area('Fill softbox',(4,-2,3),380,3*soft,(.86,.93,1)),
                area('Edge softbox',(1.2,3,4),850,2*soft,(.9,.95,1)),
                area('Top reflection',(-.5,0,6),300,2.5*soft,(1,1,1))]
        if white:
            for entry in lights:
                entry['energy']*=.24
                bpy.data.lights[entry['name']].energy=entry['energy']
    materials=list({m.name:m for ob in meshes for m in ob.data.materials if m}.values())
    surfaces=[]
    for material in materials:
        bsdf=bsdf_of(material)
        color=input_value(bsdf,'Base Color',[.5,.5,.5,1])
        surfaces.append(dict(name=material.name,metallic=input_value(bsdf,'Metallic',0),transmission=input_value(bsdf,'Transmission Weight',0),
                             luminance=sum(color[:3])/3,emission=input_value(bsdf,'Emission Strength',0)))
    rationale=[]
    if any(s['transmission']>.3 for s in surfaces):
        edge=bpy.data.lights['Edge softbox'];edge.shape='RECTANGLE';edge.size=.65*soft;edge.size_y=3.5*soft
        edge.energy*=1.15
        if showcase and not dark:
            # Glass shows what surrounds it. The dim surround that deepens an
            # opaque product would turn clear glass smoky, so keep it bright.
            if white: bs.inputs['Base Color'].default_value=(.92,.92,.92,1)
            scene.world.node_tree.nodes['Background'].inputs[1].default_value=.22
        rationale.append('检测到透射材质：使用窄长轮廓软箱和暗反光板表现边缘，保留原模型厚度')
        for side in (-1,1):
            bpy.ops.mesh.primitive_plane_add(size=1,location=(side*2.6,.8,1.8),rotation=(0,math.pi/2,0))
            flag=bpy.context.object;flag.name='Glass negative fill '+str(side);flag.scale=(4,2,1)
            flag.visible_camera=False
            flag.visible_shadow=False
            flagmat=bpy.data.materials.new(flag.name);flagmat.use_nodes=True;bsdf_of(flagmat).inputs['Base Color'].default_value=(.003,.003,.003,1)
            flag.data.materials.append(flagmat)
    if showcase:
        rationale.append('展示布光：主光与补光拉开明暗，两侧后方的条形灯勾出轮廓高光，顶光压出接地阴影')
    elif any(s['metallic']>.5 for s in surfaces):
        key=bpy.data.lights['Key softbox'];key.shape='RECTANGLE';key.size=4*soft;key.size_y=2.5*soft
        rationale.append('检测到金属：主光使用宽矩形反射形状，保持侧面明暗分离')
    if any(s['luminance']<.07 for s in surfaces):
        bpy.data.lights['Edge softbox'].energy*=1.25
        rationale.append('包含深色表面：增强轮廓光，帮助识别暗部边缘')
    if any(s['emission']>1 for s in surfaces):
        bpy.data.lights['Fill softbox'].energy*=.5
        rationale.append('检测到发光材质：降低补光以保留发光区域与主体差异')
    if not rationale: rationale.append('以漫反射表面为主：大面积主光与补光保留柔和反光')
    # Apply user/approved per-shot controls after material-aware adaptation.
    # The white camera-only background remains white; environment scales only
    # the physical Background node used by illumination and reflection rays.
    lighting={'key_power':1,'fill_power':1,'rim_power':1,'environment':1,**plan.get('lighting',{})}
    for setting,name in [('key_power','Key softbox'),('fill_power','Fill softbox'),('rim_power','Edge softbox')]:
        bpy.data.lights[name].energy*=lighting[setting]
    if not showcase and any(s['metallic']>.5 for s in surfaces):
        reflection=metal_reflection_light(meshes,soft,white,lighting['key_power'])
        if reflection:
            lights.append(reflection)
            rationale.append('根据相机与可见金属面的反射方向放置反射软箱：'+reflection['target_object'])
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value*=lighting['environment']
    if any(value!=1 for value in lighting.values()):
        rationale.append('已应用确认的主光、补光、轮廓光与物理环境倍率；白底相机背景保持不变')
    for entry in lights:
        light=bpy.data.lights[entry['name']]
        if light.type!='AREA': entry.update(energy=light.energy);continue
        entry.update(energy=light.energy,shape=light.shape,size=light.size,size_y=light.size_y)
    scene.view_settings.view_transform='Standard' if white else 'AgX'
    try: scene.view_settings.look=('Medium High Contrast' if showcase else 'None') if white else 'AgX - Medium High Contrast'
    except TypeError: pass
    scene.view_settings.exposure=plan.get('exposure',0);scene.view_settings.gamma=1
    return dict(style=style,lights=lights,material_features=surfaces,rationale=rationale,lighting_multipliers=lighting,environment_strength=float(scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value),color_management=dict(view_transform=scene.view_settings.view_transform,look=scene.view_settings.look,exposure=scene.view_settings.exposure,display='sRGB'))


def camera(plan,shot,meshes):
    scene=bpy.context.scene
    data=bpy.data.cameras.new('Product camera');cam=bpy.data.objects.new('Product camera',data);scene.collection.objects.link(cam);scene.camera=cam
    data.lens=shot.get('focal_length',70);data.sensor_width=36;data.clip_start=.01;data.clip_end=1000
    lo,hi,allpoints=bounds(meshes)
    detail=shot.get('kind')=='detail';target_ob=bpy.data.objects.get(shot.get('target_object',''))
    target_meshes=[target_ob] if detail and target_ob in meshes else meshes
    tlo,thi,points=bounds(target_meshes)
    target=(tlo+thi)/2
    region='object bounds'
    if detail and (len(target_meshes)==len(meshes) or max(thi-tlo)>max(hi-lo)*.75):
        # A deterministic upper-front region is an explicit, recorded detail target.
        span=(thi-tlo)*.28
        target=Vector((target.x,tlo.y+(thi.y-tlo.y)*.18,tlo.z+(thi.z-tlo.z)*.68))
        points=[target+Vector((x*span.x,y*span.y,z*span.z)) for x in (-1,1) for y in (-1,1) for z in (-1,1)]
        region='upper-front geometric region; verify visible surface'
    az=math.radians(shot.get('azimuth',35));el=math.radians(shot.get('elevation',20))
    direction=Vector((math.sin(az)*math.cos(el),-math.cos(az)*math.cos(el),math.sin(el)))
    margin=plan.get('margin',.12);distance=max(hi-lo)*2
    def project(d):
        cam.location=target+direction*d;aim(cam,target);bpy.context.view_layer.update()
        return [world_to_camera_view(scene,cam,p) for p in points]
    # Binary solve for the nearest distance that fits the current aspect ratio.
    low=.01;high=distance*10
    for _ in range(50):
        middle=(low+high)/2;coords=project(middle)
        valid=all(c.z>0 and margin<=c.x<=1-margin and margin<=c.y<=1-margin for c in coords)
        if valid: high=middle
        else: low=middle
    coords=project(high*1.005)
    data.dof.use_dof=shot.get('id')=='campaign' and plan.get('dof',False)
    data.dof.focus_distance=(cam.location-target).length;data.dof.aperture_fstop=8
    rectangle=[min(p.x for p in coords),min(p.y for p in coords),max(p.x for p in coords),max(p.y for p in coords)]
    checks=dict(method='projected evaluated bounding boxes',kind='detail_target' if detail else 'complete_product',
                clipped=any(p.z<=0 or p.x<0 or p.x>1 or p.y<0 or p.y>1 for p in coords),
                projected_bounds=rectangle,occupancy=max(rectangle[2]-rectangle[0],rectangle[3]-rectangle[1]),
                margin=margin,corrections=0,detail_target=dict(object=target_ob.name if target_ob else None,center=list(target),region=region) if detail else None,
                projection_test_points=[list(p) for p in points],
                manual_review=['几何检查不证明曝光、噪点、反射和材质辨识度正确；请结合后续预览检查报告判断']+(['细节目标的遮挡与清晰度需要目视判断'] if detail else []),
                ground_contact=min((o.matrix_world@Vector(v)).z for o in meshes for v in o.bound_box))
    return dict(position=list(cam.location),rotation=list(cam.rotation_euler),focal_length=data.lens,target=list(target),dof=data.dof.use_dof),checks


def effective_render_plan(plan,shot_id):
    """Resolve the same per-shot settings used by engine.effective_plan."""
    plan=dict(plan)
    adjustment=plan.get('shot_adjustments',{}).get(shot_id,{})
    plan['exposure']=max(-4,min(4,plan.get('exposure',0)+adjustment.get('exposure_delta',0)))
    plan['light_softness']=max(.25,min(4,plan.get('light_softness',1)*adjustment.get('softness_multiplier',1)))
    if 'lighting' in plan or 'lighting' in adjustment:
        plan['lighting']={**plan.get('lighting',{}),**adjustment.get('lighting',{})}
    plan['shot_adjustments']={}
    return plan


def render(req,objects,meshes):
    plan=req.get('plan',{});shot=req.get('shot',{'id':'hero','kind':'full'});out=Path(req['output_dir']);scene=bpy.context.scene
    # The same per-shot corrections feed preview and final rendering.
    scene['studio_source_plan']=json.dumps(plan,ensure_ascii=False)
    plan=effective_render_plan(plan,shot['id'])
    scene['studio_product_objects']=json.dumps([ob.name for ob in objects])
    quality=req.get('quality','preview');long={'thumbnail':256,'preview':512,'showcase':2048}.get(quality,4096)
    aspect=plan.get('aspect','1:1');a,b=[int(x) for x in aspect.split(':')]
    scene.render.resolution_x=round(long*a/max(a,b));scene.render.resolution_y=round(long*b/max(a,b));scene.render.resolution_percentage=100
    operations,warnings=prepare(objects,meshes,plan)
    if plan.get('look')=='showcase': operations.append(soften_edges(meshes))
    cam,checks=camera(plan,shot,meshes)
    settings=studio(plan,shot,meshes)
    dev=device(req.get('force_cpu',False))
    scene.cycles.samples={'thumbnail':8,'preview':24,'showcase':160,'standard':128,'fine':1024}[quality]
    scene.cycles.use_adaptive_sampling=True;scene.cycles.adaptive_threshold={'thumbnail':.1,'preview':.07,'showcase':.015,'standard':.015,'fine':.005}[quality]
    scene.cycles.use_denoising=True;scene.cycles.max_bounces=12;scene.cycles.transmission_bounces=12
    scene.cycles.time_limit=0 if quality!='fine' else float(plan.get('time_budget',1200))
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB';scene.render.image_settings.color_depth='8'
    scene.render.film_transparent=False
    stem=out.name;png=out/(stem+'.png');jpg=out/(stem+'.jpg');blend=out/(stem+'.blend')
    scene.render.filepath=str(png)
    scene['studio_plan']=json.dumps(plan,ensure_ascii=False);scene['studio_shot']=json.dumps(shot,ensure_ascii=False)
    scene['studio_operations']=json.dumps(operations,ensure_ascii=False)
    event('正在打包可复现工程')
    bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    event('Cycles 正在渲染',device=dev,shot=shot.get('id'))
    started=time.time();bpy.ops.render.render(write_still=True)
    # Re-check the actual evaluated scene after rendering. Corrections are bounded;
    # the analytic fit normally requires zero retries and never reports invented work.
    attempts=[]
    # The camera adapter re-applies its own verified view before every render, so
    # moving this camera back cannot change the picture: measure the real product
    # vertices in that view once and never repeat an identical render.
    adapter=bool(req.get('camera_projection_contract'))
    limit=0 if adapter else int(plan.get('max_corrections',2))
    margin=plan.get('margin',.12);manual_view=False
    for attempt in range(limit+1):
        if adapter:
            from blender.camera_projection import frame_bounds,world_points
            framed=[ob for ob in meshes if shot.get('kind')=='detail' and ob.name==shot.get('target_object')] or meshes
            rect,behind=frame_bounds(scene,world_points(framed))
            clipped=behind or rect[0]<0 or rect[1]<0 or rect[2]>1 or rect[3]>1
            safe=not behind and margin-.005<=rect[0] and rect[2]<=1-margin+.005 and margin-.005<=rect[1] and rect[3]<=1-margin+.005
            manual_view=any(key in shot for key in ('ortho_scale','camera_distance','target_offset'))
            checks.update(method='projected product vertices in the rendered camera',
                          occupancy=max(rect[2]-rect[0],rect[3]-rect[1]))
        else:
            if shot.get('kind')=='detail': test_points=[Vector(p) for p in checks['projection_test_points']]
            else: _,_,test_points=bounds(meshes)
            coords=[world_to_camera_view(scene,scene.camera,p) for p in test_points]
            rect=[min(p.x for p in coords),min(p.y for p in coords),max(p.x for p in coords),max(p.y for p in coords)]
            clipped=any(p.z<=0 or p.x<0 or p.x>1 or p.y<0 or p.y>1 for p in coords)
            safe=all(p.z>0 and margin-.005<=p.x<=1-margin+.005 and margin-.005<=p.y<=1-margin+.005 for p in coords)
        attempts.append(dict(attempt=attempt,clipped=clipped,projected_bounds=rect,safe_margin=safe))
        checks.update(clipped=clipped,projected_bounds=rect,corrections=attempt)
        if safe or attempt>=limit: break
        target=Vector(cam['target']);scene.camera.location=target+(scene.camera.location-target)*1.18
        bpy.context.view_layer.update();cam['position']=list(scene.camera.location)
        event('取景检查未通过，自动增加拍摄距离',correction=attempt+1)
        bpy.ops.render.render(write_still=True)
    checks['attempts']=attempts
    checks['automatic_check_status']='passed' if safe else 'manual_review'
    if not safe:
        checks['manual_review'].append(
            '当前镜头使用手动缩放或平移，产品超出留白范围；复位视角可恢复自动取景' if adapter and manual_view
            else '自动取景后产品仍超出留白范围，请调整留白或镜头角度' if adapter
            else '已达到自动取景修正上限，请人工调整相机与留白')
    if checks['corrections']:
        scene['studio_camera_correction']=json.dumps(attempts)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    scene.render.image_settings.file_format='JPEG';scene.render.image_settings.quality=95
    bpy.data.images['Render Result'].save_render(filepath=str(jpg),scene=scene)
    scene.render.image_settings.file_format='PNG'
    event('正在校验导出图片')
    for file in (png,jpg):
        image=bpy.data.images.load(str(file),check_existing=False)
        if tuple(image.size)!=(scene.render.resolution_x,scene.render.resolution_y): raise RuntimeError('导出尺寸验证失败')
        bpy.data.images.remove(image)
    warnings+=checks['manual_review']
    samples=[];visible_materials={}
    if quality=='preview' and plan.get('visual_review',True):
        event('采样产品表面，排除背景曝光')
        sys.path.insert(0,str(Path(__file__).parent))
        from pick_surface import surface_hits
        points=[{'x':(x+.5)/48,'y':(y+.5)/48} for y in range(48) for x in range(48)]
        hits=surface_hits(points,False)
        samples=[{'x':h['x'],'y':h['y'],'object_name':h['object_name'],
                  'material_name':h['material_name'],'material_render_name':h.get('material_render_name',h['material_name'])} for h in hits]
        for hit in hits:
            pair=(hit['object_name'],hit['material_name'])
            if pair not in visible_materials:
                visible_materials[pair]={'object_name':pair[0],'material_name':pair[1],
                    'material_info':hit['material_info']}
    return dict(success=True,render_pipeline_version=2,device=dev,files=dict(png=png.name,jpg=jpg.name,blend=blend.name),
                width=scene.render.resolution_x,height=scene.render.resolution_y,checks=checks,warnings=warnings,
                operations=operations,camera=cam,studio=settings,quality=quality,render_seconds=round(time.time()-started,3),
                product_samples=samples,visible_materials=list(visible_materials.values()),effective_settings={'exposure':plan['exposure'],'light_softness':plan['light_softness'],'lighting':settings['lighting_multipliers']},
                render_settings=dict(engine='CYCLES',samples=scene.cycles.samples,noise_threshold=scene.cycles.adaptive_threshold,
                                     time_limit=scene.cycles.time_limit,denoising=True),blender_version=bpy.app.version_string)


def main():
    req=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8'))
    out=Path(req['output_dir']);out.mkdir(parents=True,exist_ok=True)
    try:
        if req['action']=='probe': result=dict(success=True,device=device(req.get('force_cpu',False)))
        else:
            event('正在导入模型');objects,meshes=load_asset(req['model_path'])
            if req['action']=='analyze':
                event('正在检查结构与资源');result=analyze(objects,meshes,req['model_path'])
            elif req['action']=='render': result=render(req,objects,meshes)
            else: raise ValueError('未知操作')
        atomic(out/'result.json',result);event('执行完成')
    except Exception as exc:
        traceback.print_exc();atomic(out/'result.json',dict(success=False,error=str(exc),traceback=traceback.format_exc()));raise


if __name__=='__main__': main()

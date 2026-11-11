"""Read-only camera ray picking against the exact saved render scene."""
import json
import sys
from pathlib import Path
import bpy
from mathutils import Vector


def srgb_hex(linear):
    """Blender node colors are scene-linear; HTML colors are display sRGB."""
    values=[]
    for value in linear[:3]:
        value=max(0.0,min(1.0,float(value)))
        encoded=12.92*value if value<=.0031308 else 1.055*(value**(1/2.4))-.055
        values.append(max(0,min(255,round(encoded*255))))
    return '#'+''.join('%02X'%value for value in values)


def upstream_nodes(socket):
    pending=[link.from_node for link in socket.links]
    seen=set()
    while pending:
        node=pending.pop()
        if node.as_pointer() in seen: continue
        seen.add(node.as_pointer());yield node
        for input_socket in node.inputs:
            pending.extend(link.from_node for link in input_socket.links)


def material_info(material):
    """Describe the saved shader. Linked defaults are never claimed as texel values."""
    channels={'color':'Base Color','roughness':'Roughness','metallic':'Metallic',
              'transmission':'Transmission Weight','normal':'Normal','ior':'IOR'}
    result={'name':None,'render_name':None,'color':None,'roughness':None,'metallic':None,
            'transmission':None,'ior':None,'linked':{key:False for key in channels},'textures':[],
            'source':'rendered_scene','value_basis':'unavailable','editable':False,'supported':False,'warnings':[]}
    if material is None:
        result['warnings'].append('该表面没有材质槽，无法读取材质参数。');return result
    result.update(name=material.get('_studio_source_material',material.name),render_name=material.name)
    output=next((node for node in material.node_tree.nodes if node.type=='OUTPUT_MATERIAL' and node.is_active_output),None) if material.use_nodes else None
    active_nodes=list(upstream_nodes(output.inputs['Surface'])) if output else []
    principled=[node for node in active_nodes if node.type=='BSDF_PRINCIPLED']
    if len(principled)!=1:
        result['color']=srgb_hex(material.diffuse_color)
        result['value_basis']='viewport_display_color_only'
        result['warnings'].append('当前材质不是单一可读取的 Principled 着色器；颜色仅为视口显示色，其余参数未知。')
        return result
    shader=principled[0]
    result['value_basis']='principled_input_defaults'
    direct=bool(output and output.inputs['Surface'].links and output.inputs['Surface'].links[0].from_node==shader)
    result['editable']=result['supported']=direct
    if not direct:
        result['warnings'].append('着色器经过混合或其他节点；以下为其中 Principled 的输入值，不代表最终混合结果。')
    if not result['editable']:
        result['warnings'].append('该节点结构不支持直接修改数值；请选择内置材质预设后再编辑。')
    seen_textures=set()
    for key,name in channels.items():
        socket=shader.inputs.get(name)
        if socket is None: continue
        result['linked'][key]=bool(socket.is_linked)
        if key=='color': result[key]=srgb_hex(socket.default_value)
        elif key!='normal': result[key]=round(float(socket.default_value),6)
        for node in upstream_nodes(socket):
            if node.type=='TEX_IMAGE' and node.image:
                identity=(key,node.image.name)
                if identity not in seen_textures:
                    seen_textures.add(identity)
                    result['textures'].append({'name':node.image.name,'channel':key,'colorspace':node.image.colorspace_settings.name})
    if any(result['linked'].values()):
        result['warnings'].append('已连接节点或贴图的通道显示的是备用输入值，不是点击像素的颜色/粗糙度；主动修改该通道会替换其输入连接。')
    return result


def product_names(scene):
    saved = scene.get('studio_product_objects')
    if saved:
        return set(json.loads(saved))
    root = scene.objects.get('Studio asset transform')
    if root:
        return {o.name for o in root.children_recursive}
    return {o.name for o in scene.objects if o.type == 'MESH' and not o.name.startswith(('Studio floor', 'Glass negative fill'))}


def surface_hits(points, include_misses=True):
    scene = bpy.context.scene
    camera = scene.camera
    if not camera or camera.data.type not in {'PERSP', 'ORTHO'}:
        raise ValueError('当前工程没有支持的透视或正交相机')
    depsgraph = bpy.context.evaluated_depsgraph_get()
    frame = camera.data.view_frame(scene=scene)
    left, right = min(v.x for v in frame), max(v.x for v in frame)
    bottom, top = min(v.y for v in frame), max(v.y for v in frame)
    rotation = camera.matrix_world.to_3x3()
    names = product_names(scene)
    hits = []
    material_cache={}
    for point in points:
        local = Vector((left + (right-left)*point['x'], top - (top-bottom)*point['y'], frame[0].z))
        if camera.data.type == 'ORTHO':
            origin = camera.matrix_world @ Vector((local.x, local.y, 0))
            direction = rotation @ Vector((0, 0, -1))
        else:
            origin = camera.matrix_world.translation.copy()
            direction = rotation @ local.normalized()
        direction.normalize()
        result = dict(id=point.get('id'), x=point['x'], y=point['y'], hit=False, message='此位置没有命中产品表面')
        for _ in range(64):
            hit, location, normal, face, obj, matrix = scene.ray_cast(depsgraph, origin, direction, distance=camera.data.clip_end)
            if not hit:
                break
            original = obj.original
            if original.hide_render or not getattr(original, 'visible_camera', True):
                origin = location + direction*.00001
                continue
            if original.name not in names:
                break
            evaluated = original.evaluated_get(depsgraph)
            polygons = getattr(evaluated.data, 'polygons', [])
            material = None
            index = None
            if 0 <= face < len(polygons):
                index = polygons[face].material_index
                if index < len(evaluated.material_slots):
                    material = evaluated.material_slots[index].material
            cache_key=material.as_pointer() if material else None
            if cache_key not in material_cache: material_cache[cache_key]=material_info(material)
            result.update(hit=True, object_name=original.name, material_name=material.get('_studio_source_material',material.name) if material else None,
                          material_render_name=material.name if material else None,
                          material_info=material_cache[cache_key],slot_index=index,
                          face_index=face, position=list(location), message='首个产品表面交点；透明表面后的部件需换角度选取')
            break
        if result['hit'] or include_misses:
            hits.append(result)
    return hits


def main():
    req = json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8'))
    try:
        bpy.ops.wm.open_mainfile(filepath=req['blend_path'], load_ui=False, use_scripts=False)
        result = {'success': True, 'hits': surface_hits(req['points'])}
    except Exception as exc:
        result = {'success': False, 'error': str(exc)}
    Path(req['result_path']).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    if not result['success']:
        raise RuntimeError(result['error'])


if __name__ == '__main__':
    main()

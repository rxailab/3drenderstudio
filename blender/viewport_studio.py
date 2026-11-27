"""Recreate the interactive product stage in Cycles from the same catalog and HDR pixels."""
import json
import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector


LIBRARY = Path(__file__).resolve().parents[1] / 'static' / 'studio-backdrops'
OWNER = 'wuying_viewport_studio'
HIDDEN = 'wuying_viewport_hidden'


def _asset(url):
    prefix = '/static/studio-backdrops/'
    if not url.startswith(prefix):
        raise ValueError('视口资源地址无效')
    path = (LIBRARY / url[len(prefix):]).resolve()
    if LIBRARY not in path.parents or not path.is_file():
        raise FileNotFoundError('所选摄影棚缺少本地资源：' + str(path))
    return path


def _rgb(value):
    s = np.asarray([int(value[i:i+2],16)/255 for i in (1,3,5)])
    return tuple(np.where(s <= .04045,s/12.92,((s+.055)/1.055)**2.4))


def _zup(v):
    return Vector((v[0],-v[2],v[1]))


def restore(scene):
    """Recover the source stage before editing or re-exporting a saved photograph."""
    if not scene.get(OWNER):
        return
    for obj in list(scene.objects):
        if obj.get(OWNER):
            bpy.data.objects.remove(obj,do_unlink=True)
        elif obj.get(HIDDEN):
            obj.hide_render = False
            del obj[HIDDEN]
        elif obj.get('wuying_clear_shadow_ray'):
            obj.visible_shadow=True
            del obj['wuying_clear_shadow_ray']
    scene.world = scene.get('wuying_viewport_source_world')
    if hasattr(scene,'compositing_node_group'):
        scene.compositing_node_group = scene.get('wuying_viewport_source_compositor')
    elif 'wuying_viewport_legacy_composite' in scene:
        from blender.viewport_studio_composite import restore_legacy
        restore_legacy(scene)
    saved = json.loads(scene['wuying_viewport_source_settings'])
    scene.view_settings.view_transform = saved['transform']
    scene.view_settings.look = saved['look']
    scene.view_settings.exposure = saved['exposure']
    scene.view_settings.gamma = saved['gamma']
    scene.render.film_transparent = saved['transparent']
    scene.render.use_compositing = saved['compositing']
    original_base = json.loads(scene['wuying_viewport_base'])
    for key in (OWNER,'wuying_viewport_source_settings','wuying_viewport_source_world',
                'wuying_viewport_source_compositor','wuying_viewport_base'):
        if key in scene:
            del scene[key]
    return original_base


def _fallback_base(scene,products,points,plan,center,diagonal):
    from blender.export_viewport import metadata
    base = metadata(scene,products,points,[],[])
    if not base['lights']:
        # Same fresh-model rig as export_viewport.original_plan, in glTF axes.
        setup = [('key',(-1,1.65,1.15),59,1.42,1.78),
                 ('fill',(1,.85,.65),20,1.45,1.1),
                 ('rim',(.65,1.3,-1.05),23,.48,1.48)]
        lighting = plan.get('lighting',{})
        base['exposure'] = plan.get('exposure',base['exposure'])
        base['world'] = {'strength':.45*lighting.get('environment',1),'color':[1,1,1]}
        for role,pos,power,w,h in setup:
            p = center + _zup(pos)*diagonal
            if role+'_azimuth' in lighting or role+'_elevation' in lighting:
                from blender import hero_scene
                p = hero_scene._light_position(scene,hero_scene._bounds(points),
                    lighting.get(role+'_azimuth',{'key':-35,'fill':40,'rim':145}[role]),
                    lighting.get(role+'_elevation',45),diagonal*2)
            softness = diagonal*plan.get('light_softness',1)
            base['lights'].append(dict(name=role.title()+'Light',role=role,type='AREA',shape='RECTANGLE',
                position=[p.x,p.z,-p.y],target=base['bounds']['center'],width_world=w*softness,
                height_world=h*softness,color=[1,1,1],energy=power*diagonal**2*lighting.get(role+'_power',1)))
    return {k:base[k] for k in ('bounds','world','lights','exposure')}


def _lights(scene,preset,base,center,diagonal,*,reference=False):
    layouts = json.loads((LIBRARY/'lights.json').read_text(encoding='utf8'))
    layout = layouts.get(preset['id'])
    if preset['id']=='welcome':
        return []
    params = preset['lighting']
    yaw = Matrix.Rotation(math.radians(params.get('yaw',0)),3,'Z')
    scale = diagonal if layout else diagonal/base['bounds']['diagonal']
    origin = Vector((0,0,0)) if layout else _zup(base['bounds']['center'])
    def position(v):
        return center + (yaw @ (_zup(v)-origin))*scale
    result = []
    for i,row in enumerate(layout or base['lights']):
        name = row['name']
        role = row.get('role') or next((r for r,names in (
            ('key',('key','主灯')),('fill',('fill','辅灯')),('rim',('rim','back','轮廓')))
            if any(n in name.lower() for n in names)),('key','fill','rim')[i%3])
        kind = row['type']
        lamp = bpy.data.lights.new(name,kind)
        obj = bpy.data.objects.new(name,lamp)
        scene.collection.objects.link(obj)
        obj[OWNER] = True
        obj['wuying_light_role'] = role
        location = position(row['position'])
        direction = (yaw @ _zup(row['direction'])) if 'direction' in row else (
            position(row['target'])-location if 'target' in row else center-location)
        obj.location = location
        if 'axis_x' in row and 'axis_y' in row:
            x,y = (yaw @ _zup(row[k]) for k in ('axis_x','axis_y'))
            obj.rotation_euler = Matrix((x,y,x.cross(y))).transposed().to_euler()
        else:
            obj.rotation_euler = direction.to_track_quat('-Z','Y').to_euler()
        lamp.color = row.get('color',[1,1,1])
        gain = 1 if layout else params.get(role,1)
        lamp.energy = row['energy'] * (1 if kind=='SUN' else scale**2) * gain
        if kind=='AREA':
            lamp.shape = row.get('shape','RECTANGLE')
            lamp.size = row.get('width_world',row.get('size',1))*scale
            if lamp.shape in ('RECTANGLE','ELLIPSE'):
                lamp.size_y = row.get('height_world',row.get('size_y',1))*scale
        if kind=='SPOT':
            lamp.spot_size=row.get('spot_size',math.pi/2)
            penumbra=row.get('spot_blend',0)
            lamp.spot_blend=penumbra
            # Three distributes the recorded flux over the cone's solid
            # angle. Cycles spots start from an isotropic point emitter
            # (watts / 4π) and mask it with the cone instead.
            half_angle=max(.001,min(math.pi/2,lamp.spot_size/2))
            if not reference:
                cone_cos=math.cos(half_angle)
                lamp.energy*=2/(1-cone_cos)
                # Three's penumbra is a fraction of cone angle. Cycles
                # parameterizes the same smoothstep in cosine space.
                lamp.spot_blend=(math.cos(half_angle*(1-penumbra))-cone_cos)/(1-cone_cos)
        if kind in ('SPOT','POINT'):
            lamp.shadow_soft_size=row.get('shadow_soft_size',0)*scale
        result.append(dict(name=obj.name,type=kind,role=role,energy=lamp.energy))
    return result


def _world(scene,preset,base):
    path = _asset(preset['radianceMap'])
    pixels = np.fromfile(path,dtype='<f2').reshape(128,256,3)
    rgba = np.ones((128,256,4),dtype='float32')
    rgba[:,:,:3] = pixels
    if preset['id']=='white':
        rgba[:,:,:3] *= base['world'].get('color',[1,1,1])
    image = bpy.data.images.new('Studio radiance · '+preset['name'],256,128,float_buffer=True)
    image.colorspace_settings.name='Non-Color'
    image.pixels.foreach_set(rgba.ravel())
    image.file_format='OPEN_EXR'
    image.pack()
    world = bpy.data.worlds.new('Viewport studio · '+preset['name'])
    world.use_nodes=True
    scene.world=world
    tree=world.node_tree
    light=tree.nodes['Background']
    light['wuying_illumination']=True
    strength=base['world'].get('strength',1)
    light.inputs['Strength'].default_value = (max(0,strength) if preset['id']=='white' else
        min(8,.46+.74*max(0,strength*preset['lighting'].get('ambient',1))))
    env=tree.nodes.new('ShaderNodeTexEnvironment')
    env.image=image
    # Blender's equirectangular UV is .5-atan2(Y,X)/tau, .5+asin(Z)/pi.
    # Converting glTF (X,Y,Z) to Blender (X,-Z,Y) gives the preview's UV.
    tree.links.new(env.outputs['Color'],light.inputs['Color'])
    return float(light.inputs['Strength'].default_value)


def _plane(scene,name,verts):
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata(verts,[],[(0,1,2,3)])
    mesh.update()
    obj=bpy.data.objects.new(name,mesh)
    scene.collection.objects.link(obj)
    obj[OWNER]=True
    return obj


def _ground(scene,preset,center,diagonal,min_z):
    if preset.get('groundShadow')==0:
        return
    d=diagonal*10
    ground=_plane(scene,'Studio floor',[(-d,-d,0),(d,-d,0),(d,d,0),(-d,d,0)])
    ground.location=(center.x,center.y,min_z-diagonal*.0005)
    ground['wuying_ground']=True
    ground.is_shadow_catcher=True
    ground.visible_transmission=not bool(preset.get('image'))
    mat=bpy.data.materials.new('Studio ground bounce')
    mat.use_nodes=True
    diffuse=mat.node_tree.nodes.get('Principled BSDF')
    color=_rgb(preset['floorColor']) if preset['id']!='white' else (.78,.78,.78)
    diffuse.inputs['Base Color'].default_value=(*color,1)
    diffuse.inputs['Roughness'].default_value=1
    ground.data.materials.append(mat)


def _clear_dielectric(mat):
    """The viewport's direct-light estimator transmits an uncolored shadow ray unchanged."""
    if not mat or not mat.node_tree:
        return False
    output=next((n for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output),None)
    if not output or not output.inputs['Surface'].links:
        return False
    surface=output.inputs['Surface'].links[0].from_node
    if surface.type!='BSDF_PRINCIPLED':
        return False
    inputs=surface.inputs
    transmission=inputs.get('Transmission Weight') or inputs.get('Transmission')
    values=[transmission,inputs['Base Color'],inputs['Metallic'],inputs['Roughness']]
    if any(s is None or s.is_linked for s in values):
        return False
    if (transmission.default_value<.999 or min(inputs['Base Color'].default_value[:3])<.999
            or inputs['Metallic'].default_value>.001 or inputs['Roughness'].default_value>.12):
        return False
    volume=output.inputs['Volume'].links
    if volume:
        absorption=volume[0].from_node
        if absorption.type!='VOLUME_ABSORPTION':
            return False
        density=absorption.inputs['Density']
        return not density.is_linked and density.default_value==0
    return True


def _rear_plate(scene,image,center,diagonal):
    """A secondary transmission ray intersects the same projected set as the viewport."""
    camera=scene.camera
    inv=camera.matrix_world.inverted()
    distance=-(inv @ center).z+.92*diagonal
    frame=camera.data.view_frame(scene=scene)
    corners=[]
    for point in frame:
        factor=distance/-point.z if camera.data.type=='PERSP' else 1
        corners.append(Vector((point.x*factor,point.y*factor,-distance)))
    x0,x1=min(v.x for v in corners),max(v.x for v in corners)
    y0,y1=min(v.y for v in corners),max(v.y for v in corners)
    frame_uv=[(-.16,-.16),(1.16,-.16),(1.16,1.16),(-.16,1.16)]
    obj=_plane(scene,'Studio transmission backplate',[(x0+u*(x1-x0),y0+v*(y1-y0),-distance) for u,v in frame_uv])
    obj.parent=camera
    for name in ('camera','diffuse','glossy','shadow','volume_scatter'):
        setattr(obj,'visible_'+name,False)
    obj.visible_transmission=True
    aspect=(x1-x0)/(y1-y0)
    source_aspect=image.size[0]/image.size[1]
    w,h=min(1,aspect/source_aspect),min(1,source_aspect/aspect)
    uv=obj.data.uv_layers.new()
    for loop,point in zip(uv.data,frame_uv):
        loop.uv=point
    mat=bpy.data.materials.new('Studio plate · transmission')
    mat.use_nodes=True
    tree=mat.node_tree
    coords=tree.nodes.new('ShaderNodeTexCoord').outputs['UV']
    crop=tree.nodes.new('ShaderNodeVectorMath')
    crop.operation='MULTIPLY_ADD'
    tree.links.new(coords,crop.inputs[0])
    crop.inputs[1].default_value=(w,h,1)
    crop.inputs[2].default_value=((1-w)/2,(1-h)/2,0)
    tex=tree.nodes.new('ShaderNodeTexImage')
    tex.image=image
    tex.extension='EXTEND'
    tree.links.new(crop.outputs[0],tex.inputs['Vector'])
    emit=tree.nodes.new('ShaderNodeEmission')
    tree.links.new(tex.outputs['Color'],emit.inputs['Color'])
    # Match the tracer's transition from the plate into the independently
    # illuminated environment on rays refracted outside the photographed crop.
    offset=tree.nodes.new('ShaderNodeVectorMath')
    offset.operation='SUBTRACT'
    tree.links.new(coords,offset.inputs[0])
    offset.inputs[1].default_value=(.5,.5,0)
    absolute=tree.nodes.new('ShaderNodeVectorMath')
    absolute.operation='ABSOLUTE'
    tree.links.new(offset.outputs[0],absolute.inputs[0])
    split=tree.nodes.new('ShaderNodeSeparateXYZ')
    tree.links.new(absolute.outputs[0],split.inputs[0])
    edge=tree.nodes.new('ShaderNodeMath')
    edge.operation='MAXIMUM'
    tree.links.new(split.outputs['X'],edge.inputs[0])
    tree.links.new(split.outputs['Y'],edge.inputs[1])
    falloff=tree.nodes.new('ShaderNodeMapRange')
    falloff.interpolation_type='SMOOTHSTEP'
    falloff.inputs['From Min'].default_value=.5
    falloff.inputs['From Max'].default_value=.66
    tree.links.new(edge.outputs[0],falloff.inputs['Value'])
    mix=tree.nodes.new('ShaderNodeMixShader')
    tree.links.new(falloff.outputs[0],mix.inputs[0])
    tree.links.new(emit.outputs[0],mix.inputs[1])
    tree.links.new(tree.nodes.new('ShaderNodeBsdfTransparent').outputs[0],mix.inputs[2])
    tree.links.new(mix.outputs[0],tree.nodes['Material Output'].inputs['Surface'])
    # The image is visible through glass; it is not an extra mesh emitter.
    if hasattr(mat,'cycles'):
        mat.cycles.emission_sampling='NONE'
    obj.data.materials.append(mat)


def apply(scene,products,points,plan,preset_id,base=None):
    from blender.viewport_studio_composite import make_compositor
    from studio_backdrops import catalog
    preset=catalog().get(preset_id)
    if not preset:
        raise ValueError('找不到所选的主视图摄影棚')
    old_base=restore(scene)
    low,high=points.min(axis=0),points.max(axis=0)
    center=Vector((low+high)/2)
    diagonal=float(np.linalg.norm(high-low))
    base=base or old_base or _fallback_base(scene,products,points,plan,center,diagonal)
    # A fresh upload has no native lamps. Keep an editable copy of its
    # reference rig in the .blend so later viewport exports can recover it.
    if not any(obj.type=='LIGHT' and not obj.hide_render for obj in scene.objects):
        source={'id':'white','lighting':{}}
        for entry in _lights(scene,source,base,center,diagonal,reference=True):
            del scene.objects[entry['name']][OWNER]
        source_world=bpy.data.worlds.new('Viewport reference world')
        source_world.use_nodes=True
        background=source_world.node_tree.nodes['Background']
        background.inputs[0].default_value=(*base['world'].get('color',[1,1,1]),1)
        background.inputs[1].default_value=base['world'].get('strength',1)
        scene.world=source_world
    scene[OWNER]=preset_id
    scene['wuying_viewport_base']=json.dumps(base,ensure_ascii=False)
    if scene.world:
        scene['wuying_viewport_source_world']=scene.world
    if getattr(scene,'compositing_node_group',None):
        scene['wuying_viewport_source_compositor']=scene.compositing_node_group
    scene['wuying_viewport_source_settings']=json.dumps(dict(transform=scene.view_settings.view_transform,
        look=scene.view_settings.look,exposure=scene.view_settings.exposure,gamma=scene.view_settings.gamma,
        transparent=scene.render.film_transparent,compositing=scene.render.use_compositing))
    for obj in scene.objects:
        if (obj.type in ('MESH','CURVE','FONT','SURFACE','META','LIGHT') and obj not in products and not obj.hide_render):
            obj[HIDDEN]=True
            obj.hide_render=True
    # three-gpu-pathtracer attenuates direct-light samples through glass.
    # For a fully clear, nonabsorbing dielectric the shadow transmittance is
    # unity. Only these shadow rays bypass that object; optical intersections
    # on camera, glossy and refraction paths keep their original BSDF and IOR.
    for obj in products:
        if obj.visible_shadow and obj.material_slots and all(_clear_dielectric(slot.material) for slot in obj.material_slots):
            obj['wuying_clear_shadow_ray']=True
            obj.visible_shadow=False
    sources=_lights(scene,preset,base,center,diagonal)
    strength=_world(scene,preset,base)
    plate=None
    if preset.get('image'):
        plate=bpy.data.images.load(str(_asset(preset['image'])),check_existing=True)
        if not plate.packed_file:
            plate.pack()
        _rear_plate(scene,plate,center,diagonal)
    _ground(scene,preset,center,diagonal,float(low[2]))
    # Both render entry points pass the effective plan for this shot. A base
    # read from a manifest or restored .blend describes an earlier exposure.
    exposure=plan.get('exposure',base.get('exposure',0))
    make_compositor(scene,plate,exposure)
    bpy.context.view_layer.update()
    return dict(style='viewport',viewport_backdrop=preset_id,name=preset['name'],lights=sources,
                environment_strength=strength,ground_shadow=preset.get('groundShadow',1),
                lighting_multipliers=plan.get('lighting',{}),exposure=exposure)

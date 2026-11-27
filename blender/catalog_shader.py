"""Build a catalog replacement on an isolated material using the shared PBR description."""
import hashlib
import json
import math
from pathlib import Path

import bpy


def _image(path, color_space):
    # Always pack these allowlisted library images. A saved project or viewport
    # export should not need the developer's copy of the catalog on disk.
    path = Path(path).resolve()
    key = hashlib.sha256((str(path) + color_space).encode()).hexdigest()
    for image in bpy.data.images:
        if image.get('wuying_catalog_image') == key:
            return image
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = color_space
    image.pack()
    image.filepath = '//wuying-library/' + path.name
    image['wuying_catalog_image'] = key
    return image


def _weave(detail):
    weave = detail['weave']
    key = hashlib.sha256(json.dumps(weave,sort_keys=True).encode()).hexdigest()[:20]
    name = 'Wuying weave ' + key
    existing = bpy.data.images.get(name)
    if existing:
        return existing
    cols, rows = weave['pattern_warps'], weave['pattern_wefts']
    mask = weave['pattern_mask']
    # Sample the recorded over/under pattern with a rounded thread cross
    # section. These heights drive shading, without generating ply geometry.
    step = 12
    image = bpy.data.images.new(name, width=cols*step, height=rows*step, alpha=False)
    image.colorspace_settings.name = 'Non-Color'
    pixels = []
    for y in range(rows*step):
        for x in range(cols*step):
            warp = mask[(y//step)*cols+x//step]
            fraction = ((x if warp else y)%step+.5)/step
            height = math.sin(math.pi*fraction)
            pixels.extend((height,height,height,1.))
    image.pixels.foreach_set(pixels)
    image.pack()
    return image


def _detail(tree, detail, coords):
    nodes, links = tree.nodes, tree.links
    pattern = detail['type']
    if pattern == 'toon':
        facing = nodes.new('ShaderNodeLayerWeight')
        return facing.outputs['Facing']
    mapping = nodes.new('ShaderNodeMapping')
    links.new(coords.outputs['Generated'], mapping.inputs['Vector'])
    mapping.inputs['Rotation'].default_value[2] = math.radians(detail.get('rotation',0.))
    size = max(.0001, detail.get('size',.01))
    scale = min(220.,max(2.,.18/size))
    if 'weave' in detail:
        weave = nodes.new('ShaderNodeTexImage')
        weave.image = _weave(detail)
        scale /= max(1.,min(detail['weave']['pattern_warps'],detail['weave']['pattern_wefts']))
        mapping.inputs['Scale'].default_value = (scale,)*3
        weave.projection = 'BOX'
        weave.projection_blend = .15
        links.new(mapping.outputs['Vector'], weave.inputs['Vector'])
        return weave.outputs['Color']
    if pattern in ('wood','marble','weave'):
        wave = nodes.new('ShaderNodeTexWave')
        wave.wave_type = 'RINGS' if pattern=='wood' else 'BANDS'
        if pattern=='wood':
            wave.rings_direction = 'Z'
        wave.inputs['Scale'].default_value = 2 if pattern=='marble' else scale*.5
        wave.inputs['Distortion'].default_value = 8 if pattern=='marble' else 4 if pattern=='wood' else .2
        links.new(mapping.outputs['Vector'],wave.inputs['Vector'])
        return wave.outputs['Fac']
    if pattern in ('cells','leather'):
        node = nodes.new('ShaderNodeTexVoronoi')
        node.feature = 'DISTANCE_TO_EDGE'
        result = node.outputs['Distance']
    elif pattern=='grid':
        node = nodes.new('ShaderNodeTexChecker')
        result = node.outputs['Fac']
    else:
        node = nodes.new('ShaderNodeTexNoise')
        result = node.outputs['Fac']
        if pattern=='brush':
            mapping.inputs['Scale'].default_value = (1.,65.,1.)
    node.inputs['Scale'].default_value = scale
    links.new(mapping.outputs['Vector'],node.inputs['Vector'])
    return result


def apply(material, override, plan, has_uv):
    from keyshot_catalog import render_spec
    from blender.worker import rgb, rotated_normal_color
    ident = override['catalog_id']
    spec = render_spec(ident)
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    bs = nodes.new('ShaderNodeBsdfPrincipled')
    links.new(bs.outputs['BSDF'],output.inputs['Surface'])
    color = rgb(override.get('color',spec['color']))
    material.diffuse_color = color
    bs.inputs['Base Color'].default_value = color
    for key, socket in [('roughness','Roughness'), ('metallic','Metallic'), ('transmission','Transmission Weight'),
                        ('coat','Coat Weight'), ('ior','IOR'), ('alpha','Alpha'), ('sheen','Sheen Weight'),
                        ('anisotropy','Anisotropic IOR Level'), ('subsurface','Subsurface Weight'),
                        ('emission_strength','Emission Strength')]:
        target = bs.inputs.get(socket)
        if target is None and key=='anisotropy':
            target = bs.inputs.get('Anisotropic')
        if target is not None:
            target.default_value = override.get(key,spec[key])
    bs.inputs['Emission Color'].default_value = color
    coords = nodes.new('ShaderNodeTexCoord')
    detail = spec.get('procedural')
    external = override.get('textures',{})
    maps = {**spec.get('textures',{}),**external}
    height = None
    tangent_normal = None
    if detail:
        signal = _detail(tree,detail,coords)
        if detail.get('colors') and 'color' not in override and 'base_color' not in maps:
            ramp = nodes.new('ShaderNodeValToRGB')
            for stop,rgb_color in zip(ramp.color_ramp.elements,detail['colors']):
                stop.color = [*rgb_color,1.]
            if detail['type']=='toon':
                # Layer Weight.Facing reaches one at grazing angles.
                ramp.color_ramp.elements[0].position = .7
                ramp.color_ramp.elements[1].position = .88
                ramp.color_ramp.elements[0].color = [*detail['colors'][1],1.]
                ramp.color_ramp.elements[1].color = [*detail['colors'][0],1.]
            links.new(signal,ramp.inputs['Fac'])
            links.new(ramp.outputs['Color'],bs.inputs['Base Color'])
        if detail['type']!='toon':
            height = signal
        if spec.get('roughness_range') and 'roughness' not in override and 'roughness' not in maps:
            remap = nodes.new('ShaderNodeMapRange')
            remap.inputs['To Min'].default_value,remap.inputs['To Max'].default_value=spec['roughness_range']
            links.new(signal,remap.inputs['Value'])
            links.new(remap.outputs['Result'],bs.inputs['Roughness'])
    mappings = {}
    applied = []
    for channel,path in maps.items():
        if channel not in external and (channel in override or channel=='base_color' and 'color' in override):
            continue
        # Tangent normal maps require real UV tangents. Height images and the
        # color/scalar channels can instead use generated box coordinates.
        if channel=='normal' and not has_uv:
            continue
        img = nodes.new('ShaderNodeTexImage')
        img.image = _image(path,'sRGB' if channel=='base_color' else 'Non-Color')
        img.label = channel
        if not has_uv:
            img.projection = 'BOX'
            img.projection_blend = .2
        source = override if channel in external else {}
        scale = source.get('texture_scale',plan.get('texture_scale',1))
        rotation = source.get('texture_rotation',plan.get('texture_rotation',0))
        key = scale,rotation
        if key not in mappings:
            mapping = nodes.new('ShaderNodeMapping')
            links.new(coords.outputs['UV' if has_uv else 'Generated'],mapping.inputs['Vector'])
            mapping.inputs['Scale'].default_value = (scale,)*3
            mapping.inputs['Rotation'].default_value[2] = math.radians(rotation)
            mappings[key] = mapping
        mapping = mappings[key]
        links.new(mapping.outputs['Vector'],img.inputs['Vector'])
        signal = img.outputs['Color']
        if channel=='normal':
            normal = nodes.new('ShaderNodeNormalMap')
            links.new(rotated_normal_color(tree,signal,rotation),normal.inputs['Color'])
            tangent_normal = normal.outputs['Normal']
        elif channel=='height':
            height = signal
        else:
            socket = {'base_color':'Base Color','roughness':'Roughness','metallic':'Metallic','alpha':'Alpha',
                      'transmission':'Transmission Weight'}[channel]
            if channel=='base_color' and channel not in external and spec.get('texture_tint'):
                mix = nodes.new('ShaderNodeMixRGB')
                mix.blend_type = 'MULTIPLY'
                mix.inputs[0].default_value = 1.
                mix.inputs[2].default_value = [*spec['texture_tint'],1.]
                links.new(signal,mix.inputs[1])
                signal = mix.outputs['Color']
            if channel=='roughness' and channel not in external and spec.get('roughness_range'):
                remap = nodes.new('ShaderNodeMapRange')
                remap.inputs['To Min'].default_value, remap.inputs['To Max'].default_value = spec['roughness_range']
                links.new(signal,remap.inputs['Value'])
                signal = remap.outputs['Result']
            links.new(signal,bs.inputs[socket])
        applied.append({'channel':channel,'applied':True,'path':str(path),
                        'mapping':'UV' if has_uv else 'Generated box','scale':scale,'rotation_degrees':rotation})
    if height:
        bump = nodes.new('ShaderNodeBump')
        bump.inputs['Distance'].default_value = .0006
        bump.inputs['Strength'].default_value = .2
        links.new(height,bump.inputs['Height'])
        if tangent_normal:
            links.new(tangent_normal,bump.inputs['Normal'])
        tangent_normal = bump.outputs['Normal']
    if tangent_normal:
        links.new(tangent_normal,bs.inputs['Normal'])
    material['wuying_catalog_id'] = ident
    material['provenance'] = json.dumps({'source':'keyshot_catalog','catalog_id':ident,
                                         'resource':override.get('resource'),'license':override.get('license')})
    warnings = ['缺少 UV，切线法线贴图未应用。'] if 'normal' in maps and not has_uv else []
    return {'material':material.name,'maps':applied,'warnings':warnings,'library':[{'catalog_id':ident}]}

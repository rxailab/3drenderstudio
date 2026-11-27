"""Build the GLB used for the reflection/refraction browser regression with Blender."""
import json
from pathlib import Path
import struct
import sys

import bpy


def material(name, rgb, roughness=.35, metallic=0., transmission=0., ior=1.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = (*rgb, 1.)
    shader.inputs['Roughness'].default_value = roughness
    shader.inputs['Metallic'].default_value = metallic
    shader.inputs['Transmission Weight'].default_value = transmission
    shader.inputs['IOR'].default_value = ior
    return mat


def sphere(name, center, radius, mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=radius, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


def solid(name, center, dimensions, mat, bevel=0.):
    bpy.ops.mesh.primitive_cube_add(size=1., location=center)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new('Edge radius', 'BEVEL')
        mod.width = bevel
        mod.segments = 4
        bpy.ops.object.modifier_apply(modifier=mod.name)
        mod = obj.modifiers.new('Weighted faces', 'WEIGHTED_NORMAL')
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.data.materials.append(mat)
    return obj


def platform(x, label, mat):
    bpy.ops.mesh.primitive_cylinder_add(vertices=72, radius=.85, depth=.18, location=(x, 0., .09))
    obj = bpy.context.object
    obj.name = label + ' plinth'
    obj.data.materials.append(mat)
    edge = obj.modifiers.new('Rounded lip', 'BEVEL')
    edge.width = .05
    edge.segments = 4
    bpy.ops.object.modifier_apply(modifier=edge.name)
    mod = obj.modifiers.new('Weighted faces', 'WEIGHTED_NORMAL')
    bpy.ops.object.modifier_apply(modifier=mod.name)


def build(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mirror = material('Mirror polished silver', (.91,.94,.97), .025, 1.)
    glass = material('Optical glass 1.52', (1.,1.,1.), .008, transmission=1., ior=1.52)
    porcelain = material('Satin porcelain', (.67,.69,.70), .29)
    marker = material('Vermilion reference', (.96,.018,.009), .31)
    ink = material('Midnight blue calibration', (.008,.055,.16), .43)
    paper = material('Porcelain white calibration', (.92,.95,.96), .46)
    teal = material('Turquoise calibration', (.018,.56,.53), .35)
    for x, name, mat in ((-1.13,'Silver sphere',mirror),(1.13,'Glass sphere',glass)):
        platform(x, name, porcelain)
        sphere(name, (x,0.,1.04), .88, mat)
    # Seen directly to the left and as a smaller image in the polished sphere.
    solid('Red target', (-2.28,-1.17,.68), (.47,.49,1.36), marker, .085)
    # Geometry (not a screen-space background) behind the transparent sample.
    solid('Reference panel', (1.20,1.43,.98), (2.02,.115,1.96), paper, .05)
    for i in range(7):
        x = .36 + .28*i
        solid(f'Stripe {i+1}', (x,1.362,.98), (.11,.012,1.83), teal if i==0 or i==6 else ink)
    # Give the specular rim and background some context without filling the hero with a floor mesh.
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_yup=True,
                              export_animations=False, export_texcoords=True,
                              export_normals=True, export_cameras=False, export_lights=False)
    # glTF defines zero thickness as a thin-walled surface. These triangles
    # enclose a 1.76-unit sphere, including a real second glass/air boundary.
    data = path.read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    gltf = json.loads(data[20:20+length])
    glass = next(m for m in gltf['materials'] if m['name'] == 'Optical glass 1.52')
    glass['extensions']['KHR_materials_volume'] = {'thicknessFactor': 1.76}
    gltf.setdefault('extensionsUsed', []).append('KHR_materials_volume')
    body = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    body += b' ' * (-len(body) % 4)
    tail = data[20+length:]
    path.write_bytes(struct.pack('<4sIIII', b'glTF', 2, 20+len(body)+len(tail), len(body),
                                 0x4e4f534a) + body + tail)


if __name__ == '__main__':
    options = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    output = Path(options[0]).resolve() if options else Path(__file__).resolve().parents[1] / 'samples' / 'material-optics.glb'
    build(output)

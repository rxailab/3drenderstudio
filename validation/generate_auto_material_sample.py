"""Create a CC0 UV-mapped cotton cushion with no micro-surface textures."""
from pathlib import Path
import bpy

ROOT=Path(__file__).resolve().parents[1]
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,.055))
body=bpy.context.object;body.name='Cotton cushion body'
body.dimensions=(.32,.24,.11)
bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
bevel=body.modifiers.new('Rounded cushion edges','BEVEL');bevel.width=.035;bevel.segments=6
bpy.ops.object.modifier_apply(modifier=bevel.name)
for polygon in body.data.polygons: polygon.use_smooth=True
material=bpy.data.materials.new('Fine woven cotton fabric - cream')
material.use_nodes=True
shader=material.node_tree.nodes.get('Principled BSDF')
shader.inputs['Base Color'].default_value=(.55,.40,.25,1)
shader.inputs['Roughness'].default_value=.86
shader.inputs['Metallic'].default_value=0
body.data.materials.append(material)
body['asset_license']='CC0-1.0'
body['material_description']='Fine woven cotton fabric; cream base color; micro-surface texture is missing.'
path=ROOT/'validation'/'auto-cotton-cushion.glb'
bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',export_extras=True)
print('CREATED_AUTOMATIC_MATERIAL_FIXTURE',str(path))

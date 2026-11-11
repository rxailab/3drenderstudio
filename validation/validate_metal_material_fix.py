"""Real Blender regression for explicit preset channels and fallback provenance."""
import importlib.util
import json
import tempfile
from pathlib import Path
import bpy

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('worker',ROOT/'blender/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
with tempfile.TemporaryDirectory(dir=ROOT/'validation') as temp:
    folder=Path(temp)
    obj=folder/'asset.obj'; obj.write_text('mtllib asset.mtl\n',encoding='utf8')
    mtl=folder/'asset.mtl'
    mtl.write_text('# Fallback materials only; original MTL was not supplied.\nnewmtl Silver\nnewmtl Red\n',encoding='utf8')
    assert worker.fallback_material_names(obj)=={'Silver','Red'}
    mtl.write_text('# ordinary artist supplied MTL\nnewmtl Silver\n',encoding='utf8')
    assert worker.fallback_material_names(obj)==set()
    obj.write_text('mtllib missing.mtl\n',encoding='utf8')
    assert worker.fallback_material_names(obj)==set()

mat=bpy.data.materials.new('Metal fixture');mat.use_nodes=True
nodes=mat.node_tree.nodes;links=mat.node_tree.links;bs=worker.bsdf_of(mat)
node=nodes.new('ShaderNodeTexImage');node.image=bpy.data.images.new('Original label',width=4,height=4)
links.new(node.outputs['Color'],bs.inputs['Base Color'])
links.new(node.outputs['Alpha'],bs.inputs['Alpha'])
for key in ('Roughness','Metallic','Transmission Weight'):
    links.new(node.outputs['Color'],bs.inputs[key])
normal=nodes.new('ShaderNodeNormalMap');links.new(normal.outputs['Normal'],bs.inputs['Normal'])
worker.configure_material(mat,{'object_name':'Fixture','material_name':mat.name,'preset':'brushed_metal','preserve_original':False},{})
assert bs.inputs['Base Color'].links[0].from_node==node
assert bs.inputs['Alpha'].links[0].from_node==node
assert bs.inputs['Normal'].links[0].from_node==normal
for key,value in [('Metallic',1.0),('Roughness',.28),('Transmission Weight',0.0)]:
    assert not bs.inputs[key].is_linked and abs(bs.inputs[key].default_value-value)<1e-6
bpy.ops.mesh.primitive_cube_add();ob=bpy.context.object;ob.name='Fixture';ob.data.materials.append(mat)
result=worker.studio({'light_softness':1},{'style':'white'},[ob])
assert any(s['metallic']>.5 for s in result['material_features'])
assert next(l for l in result['lights'] if l['name']=='Key softbox')['shape']=='RECTANGLE'
result_file=ROOT/'validation/metal-material-fix-results.json'
result_file.write_text(json.dumps({'passed':True,'blender':bpy.app.version_string,'metallic':bs.inputs['Metallic'].default_value,'roughness':bs.inputs['Roughness'].default_value,'base_color_alpha_normal_preserved':True,'metal_key_light_shape':'RECTANGLE','fallback_marker_checks':True},ensure_ascii=False,indent=2),encoding='utf8')
print('METAL_MATERIAL_FIX_VERIFIED')

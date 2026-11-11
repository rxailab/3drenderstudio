"""Real Blender material inspection / edit invariants; never overwrite input assets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import bpy

ROOT=Path(__file__).resolve().parents[1]
def module(name,relative):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
picker=module('surface_inspector','blender/pick_surface.py')
worker=module('studio_worker','blender/worker.py')
source=next((ROOT/'data/fa395f959fd14321b60289804e3cb3af/runs/83637e937b204387b191f7ecfb6118c0').rglob('*.blend'))
before=hashlib.sha256(source.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
hits=picker.surface_hits([{'id':'front','x':.5,'y':.5},{'id':'background','x':.01,'y':.01}])
assert hits[0]['hit'] and not hits[1]['hit']
info=hits[0]['material_info']
assert info['source']=='rendered_scene' and info['editable']
mat=bpy.data.materials[hits[0]['material_render_name']]
bs=worker.bsdf_of(mat)
assert abs(info['roughness']-bs.inputs['Roughness'].default_value)<.00001
assert info['color']==picker.srgb_hex(bs.inputs['Base Color'].default_value)
report={'blender_version':bpy.app.version_string,'real_scene_pick':hits,'source_sha256_unchanged':False}

bpy.ops.wm.read_factory_settings(use_empty=True)
mat=bpy.data.materials.new('Inspector fixture');mat.use_nodes=True
bs=worker.bsdf_of(mat)
bs.inputs['Base Color'].default_value=worker.rgb('#808080')
bs.inputs['Roughness'].default_value=.27
assert picker.material_info(mat)['color']=='#808080'
image=bpy.data.images.new('Brand label fixture',8,8)
tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
mat.node_tree.links.new(tex.outputs['Color'],bs.inputs['Base Color'])
linked=picker.material_info(mat)
assert linked['linked']['color'] and linked['value_basis']=='principled_input_defaults'
assert any(t['name']=='Brand label fixture' and t['channel']=='color' for t in linked['textures'])
assert linked['warnings']
worker.configure_material(mat,{'object_name':'part','material_name':'Inspector fixture','preset':'wood','preserve_original':False},{})
assert bs.inputs['Base Color'].is_linked
assert any(t['name']=='Brand label fixture' for t in picker.material_info(mat)['textures'])
report['linked_channel_is_explicit_default_not_texel']=True
report['srgb_color_round_trip']=True
report['wood_preset_preserves_original_label']=True

plain=bpy.data.materials.new('Unlinked color fixture');plain.use_nodes=True
plain_bs=worker.bsdf_of(plain);plain_bs.inputs['Base Color'].default_value=worker.rgb('#BA1F28')
original=tuple(plain_bs.inputs['Base Color'].default_value)
worker.configure_material(plain,{'object_name':'part','material_name':plain.name,'preset':'brushed_metal','preserve_original':False},{})
assert tuple(plain_bs.inputs['Base Color'].default_value)==original
assert plain_bs.inputs['Metallic'].default_value==1
report['preset_preserves_unlinked_product_color']=True

mixed=bpy.data.materials.new('Unsupported diffuse fixture');mixed.use_nodes=True
tree=mixed.node_tree;old_bs=worker.bsdf_of(mixed);out=next(n for n in tree.nodes if n.type=='OUTPUT_MATERIAL')
diffuse=tree.nodes.new('ShaderNodeBsdfDiffuse');tree.links.new(diffuse.outputs[0],out.inputs['Surface'])
unsupported=picker.material_info(mixed)
assert not unsupported['editable'] and unsupported['roughness'] is None
try:worker.configure_material(mixed,{'object_name':'part','material_name':mixed.name,'preserve_original':True,'roughness':.8},{})
except ValueError:pass
else:raise AssertionError('Unsupported shader scalar edit must be rejected')
worker.configure_material(mixed,{'object_name':'part','material_name':mixed.name,'preset':'ceramic','preserve_original':False},{})
active=out.inputs['Surface'].links[0].from_node
assert active.type=='BSDF_PRINCIPLED' and active!=old_bs
assert abs(active.inputs['Roughness'].default_value-.18)<.001
assert picker.material_info(mixed)['editable']
orphan_value=old_bs.inputs['Roughness'].default_value
worker.configure_material(mixed,{'object_name':'part','material_name':mixed.name,'preserve_original':True,'roughness':.72},{})
assert abs(active.inputs['Roughness'].default_value-.72)<.001
assert old_bs.inputs['Roughness'].default_value==orphan_value
report['unsupported_shader_requires_explicit_preset']=True
report['explicit_preset_replaces_active_shader_not_orphan']=True
report['subsequent_edit_targets_active_shader']=True
report['source_sha256_unchanged']=before==hashlib.sha256(source.read_bytes()).hexdigest()
assert report['source_sha256_unchanged']
(ROOT/'validation/material-inspector-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('MATERIAL_INSPECTOR_VALIDATED')

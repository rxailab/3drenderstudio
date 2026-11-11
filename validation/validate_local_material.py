"""Run with Blender: prove selected mesh/material isolation and label retention."""
import importlib.util
import json
from pathlib import Path
import bpy

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('studio_worker',ROOT/'blender/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
bpy.ops.wm.read_factory_settings(use_empty=True)
material=bpy.data.materials.new('Shared branded material');material.use_nodes=True
bs=worker.bsdf_of(material);bs.inputs['Base Color'].default_value=(.13,.22,.33,1)
bs.inputs['Roughness'].default_value=.25
image=bpy.data.images.new('Original label',4,4)
texture=material.node_tree.nodes.new('ShaderNodeTexImage');texture.image=image
material.node_tree.links.new(texture.outputs['Color'],bs.inputs['Base Color'])
bpy.ops.mesh.primitive_cube_add(location=(-1.1,0,0));first=bpy.context.object;first.name='Selected part';first.data.materials.append(material)
second=first.copy();second.data=first.data;bpy.context.scene.collection.objects.link(second);second.name='Unselected shared mesh';second.location.x=1.1
plan={'material_overrides':[],'object_material_overrides':[{'object_name':first.name,'material_name':material.name,
      'preset':'matte_plastic','preserve_original':True,'roughness':.8}], 'repair':{},'rotation':[0,0,0]}
worker.prepare([first,second],[first,second],plan)
selected=first.material_slots[0].material;untouched=second.material_slots[0].material
assert first.data!=second.data
assert selected!=untouched and untouched==material
assert abs(worker.bsdf_of(selected).inputs['Roughness'].default_value-.8)<.001
assert abs(worker.bsdf_of(untouched).inputs['Roughness'].default_value-.25)<.001
assert worker.bsdf_of(selected).inputs['Base Color'].is_linked
assert worker.bsdf_of(untouched).inputs['Base Color'].is_linked
assert selected['_studio_source_material']=='Shared branded material'
assert worker.bsdf_of(selected).inputs['IOR'].default_value==worker.bsdf_of(untouched).inputs['IOR'].default_value
worker.configure_material(selected,{'preset':'matte_plastic','preserve_original':True,'color':'#112233'},plan)
assert not worker.bsdf_of(selected).inputs['Base Color'].is_linked
assert worker.bsdf_of(untouched).inputs['Base Color'].is_linked
report={'blender_version':bpy.app.version_string,'shared_mesh_isolated':True,'shared_material_isolated':True,
        'roughness_only_preserves_label':True,'explicit_color_changes_selected_only':True,'ior_untouched':True}
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add();bare=bpy.context.object;bare.name='No material mesh'
analysis=worker.analyze([bare],[bare],str(ROOT/'validation/unmaterialed.glb'))
assert analysis['objects'][0]['materials']==['Default material']
assert analysis['objects'][0]['original_materials']==[]
assert analysis['materials'][0]['name']=='Default material'
assert analysis['materials'][0]['source']=='default'
worker.prepare([bare],[bare],{'repair':{},'rotation':[0,0,0]})
assert bare.material_slots[0].material.name=='Default material'
report['unmaterialed_analysis_matches_render_slot']=True
(ROOT/'validation/local-material-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))

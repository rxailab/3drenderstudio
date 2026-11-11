"""Run inside Blender; verify the actual shader graph and shared-surface isolation."""
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from planner import default_plan,validate

spec=importlib.util.spec_from_file_location('studio_worker',ROOT/'blender/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def fixture(folder,no_uv=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    paths={}
    for name,color in [('label',(0.9,0.1,0.1,1)),('normal',(.5,.5,1,1)),('new_color',(.1,.5,.2,1))]:
        image=bpy.data.images.new(name,8,8);image.generated_color=color
        image.filepath_raw=str(folder/(name+'.png'));image.file_format='PNG';image.save()
        paths[name]=image.filepath_raw
    bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,.5))
    selected=bpy.context.object;selected.name='Selected'
    if no_uv:
        for layer in list(selected.data.uv_layers):selected.data.uv_layers.remove(layer)
    shared=bpy.data.materials.new('Shared');shared.use_nodes=True
    selected.data.materials.append(shared)
    bs=worker.bsdf_of(shared);bs.inputs['Metallic'].default_value=.25
    bs.inputs['IOR'].default_value=1.72;bs.inputs['Alpha'].default_value=.62
    logo=shared.node_tree.nodes.new('ShaderNodeTexImage');logo.image=bpy.data.images.load(paths['label'])
    logo.name='Original label'
    shared.node_tree.links.new(logo.outputs['Color'],bs.inputs['Base Color'])
    old_bump=shared.node_tree.nodes.new('ShaderNodeBump');old_bump.name='Original bumps'
    shared.node_tree.links.new(old_bump.outputs['Normal'],bs.inputs['Normal'])
    untouched=selected.copy();untouched.name='Untouched';untouched.location.x=2
    bpy.context.collection.objects.link(untouched)
    assert selected.data==untouched.data
    override={'object_name':'Selected','material_name':'Shared','preserve_original':True,
              'textures':{'roughness':paths['label'],'normal':paths['normal']},
              'source':'https://polyhaven.com/a/test_fixture','license':'CC0-1.0',
              'resource':{'provider':'Synthetic test fixture','asset_id':'test_fixture','resolution':'1k'},
              'texture_scale':2.5,'texture_rotation':30}
    plan=default_plan();plan['object_material_overrides']=[override]
    return selected,untouched,shared,plan,paths


report={'blender_version':bpy.app.version_string,'passed':False}
with tempfile.TemporaryDirectory(prefix='pbr-check-',dir=str(ROOT/'validation')) as temp:
    folder=Path(temp)
    selected,untouched,original,plan,paths=fixture(folder)
    validate(plan)
    operations,warnings=worker.prepare([selected,untouched],[selected,untouched],plan)
    edited=selected.data.materials[0];bs=worker.bsdf_of(edited)
    assert selected.data!=untouched.data and untouched.data.materials[0]==original and edited!=original
    assert bs.inputs['Base Color'].links[0].from_node.name=='Original label'
    logo=bs.inputs['Base Color'].links[0].from_node.image
    rough=bs.inputs['Roughness'].links[0].from_node.image
    assert logo!=rough and logo.colorspace_settings.name=='sRGB' and rough.colorspace_settings.name=='Non-Color'
    assert bs.inputs['Metallic'].default_value==.25
    assert abs(bs.inputs['IOR'].default_value-1.72)<1e-6 and abs(bs.inputs['Alpha'].default_value-.62)<1e-6
    normal=bs.inputs['Normal'].links[0].from_node
    assert normal.type=='NORMAL_MAP' and edited.node_tree.nodes['External normal'].image.colorspace_settings.name=='Non-Color'
    original_bs=worker.bsdf_of(original)
    assert not original_bs.inputs['Roughness'].is_linked and original_bs.inputs['Normal'].links[0].from_node.name=='Original bumps'
    mapping=edited.node_tree.nodes['External resource mapping']
    assert list(mapping.inputs['Scale'].default_value)==[2.5]*3
    assert abs(mapping.inputs['Rotation'].default_value.z-math.radians(30))<1e-6
    assert abs(edited.node_tree.nodes['External normal direction'].inputs['Angle'].default_value+math.radians(30))<1e-6
    provenance=json.loads(edited['provenance'])
    assert provenance['preserves_original_color'] and provenance['license']=='CC0-1.0'
    assert {entry['mapping'] for entry in provenance['maps']}=={'UV'}
    assert all(entry['applied'] for entry in provenance['maps'])
    assert operations[-1]['resources'][0]['maps']==provenance['maps'] and not warnings
    report.update(local_isolation=True,original_label_preserved=True,original_alpha_and_ior_preserved=True,
                  channel_color_spaces_verified=True,original_image_color_space_unchanged=True,
                  mapping_scale_rotation_verified=True,tangent_normal_direction_rotation_verified=True,
                  provenance_recorded=True,uv_maps=provenance['maps'])

    # Resource bytes must stay editable after the source folder disappears.
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/'packed.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(folder/'packed.blend'))
    for image in bpy.data.images:
        if image.source=='FILE':assert image.packed_file
    report['packed_scene_resources_verified']=True

    selected,untouched,original,plan,paths=fixture(folder,no_uv=True)
    operations,warnings=worker.prepare([selected,untouched],[selected,untouched],plan)
    edited=selected.data.materials[0];bs=worker.bsdf_of(edited)
    assert bs.inputs['Roughness'].links[0].from_node.projection=='BOX'
    assert bs.inputs['Normal'].links[0].from_node.name=='Original bumps'
    provenance=json.loads(edited['provenance'])
    assert len(warnings)==1 and any('UV' in message for message in warnings)
    assert next(entry for entry in provenance['maps'] if entry['channel']=='normal')['applied'] is False
    report.update(no_uv_tangent_normal_skipped=True,no_uv_box_projection_verified=True,no_uv_warning_recorded=True)

    selected,untouched,original,plan,paths=fixture(folder)
    plan['object_material_overrides'][0]['textures']['base_color']=paths['new_color']
    worker.prepare([selected,untouched],[selected,untouched],plan)
    edited=selected.data.materials[0];bs=worker.bsdf_of(edited)
    assert bs.inputs['Base Color'].links[0].from_node.name=='External base_color'
    assert bs.inputs['Base Color'].links[0].from_node.image.colorspace_settings.name=='sRGB'
    assert worker.bsdf_of(original).inputs['Base Color'].links[0].from_node.name=='Original label'
    assert not json.loads(edited['provenance'])['preserves_original_color']
    report['explicit_base_color_replacement_verified']=True

report['passed']=True
(ROOT/'validation/external-materials-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('EXTERNAL_MATERIALS_VERIFIED actual Blender shader graphs, shared mesh isolation, packed resources')

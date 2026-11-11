"""Run with Blender: test actual light and world node values, without rendering."""
import importlib.util
import json
import sys
from pathlib import Path

import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from planner import default_plan,validate

spec=importlib.util.spec_from_file_location('studio_worker',ROOT/'blender/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def scene(plan,style='white',dark_emissive=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,.5))
    mesh=bpy.context.object
    if dark_emissive:
        material=bpy.data.materials.new('Dark emissive fixture');material.use_nodes=True
        bs=worker.bsdf_of(material)
        bs.inputs['Base Color'].default_value=(.01,.01,.01,1)
        bs.inputs['Emission Strength'].default_value=2
        mesh.data.materials.append(material)
    result=worker.studio(plan,{'id':'hero','style':style},[mesh])
    actual={entry['name']:bpy.data.lights[entry['name']].energy for entry in result['lights']}
    assert all(abs(entry['energy']-actual[entry['name']])<.00001 for entry in result['lights'])
    world=bpy.context.scene.world.node_tree
    physical=world.nodes['Background'].inputs['Strength'].default_value
    assert abs(result['environment_strength']-physical)<.00001
    if style=='white':
        camera_background=next(n for n in world.nodes if n.type=='BACKGROUND' and n.name!='Background')
        assert camera_background.inputs['Strength'].default_value==1
        assert list(camera_background.inputs['Color'].default_value)==[1,1,1,1]
    return {'lights':actual,'environment':physical,'record':result}


base_plan=default_plan()
base_plan.pop('lighting')
baseline=scene(base_plan)
defaults=scene(default_plan())
assert baseline['lights']==defaults['lights'] and baseline['environment']==defaults['environment']
plan=default_plan();plan['lighting']={'fill_power':.8,'key_power':1.2}
plan['shot_adjustments']={'hero':{'lighting':{'fill_power':.4,'rim_power':1.5,'environment':.5}}}
validate(plan)
effective=worker.effective_render_plan(plan,'hero')
assert effective['lighting']=={'fill_power':.4,'key_power':1.2,'rim_power':1.5,'environment':.5}
assert worker.effective_render_plan(effective,'hero')==effective
assert worker.effective_render_plan(plan,'campaign')['lighting']==plan['lighting']
adjusted=scene(effective)
for name,ratio in [('Key softbox',1.2),('Fill softbox',.4),('Edge softbox',1.5),('Top reflection',1)]:
    assert abs(adjusted['lights'][name]/baseline['lights'][name]-ratio)<.00001
assert abs(adjusted['environment']/baseline['environment']-.5)<.00001

# Material-aware factors must survive: dark materials boost edge; emission cuts fill.
adapted_base=scene(base_plan,dark_emissive=True)
adapted=scene(effective,dark_emissive=True)
for name,ratio in [('Fill softbox',.4),('Edge softbox',1.5)]:
    assert abs(adapted['lights'][name]/adapted_base['lights'][name]-ratio)<.00001
assert adapted_base['lights']['Fill softbox']==baseline['lights']['Fill softbox']*.5
assert adapted_base['lights']['Edge softbox']==baseline['lights']['Edge softbox']*1.25
zero_plan=default_plan();zero_plan['lighting']['environment']=0
zero=scene(zero_plan)
assert zero['environment']==0

report={'blender_version':bpy.app.version_string,'passed':True,'legacy_default_energy_identical':True,
        'absolute_shot_overrides':True,'other_shot_preserved':True,'camera_white_background_preserved':True,
        'material_adaptation_preserved':True,'environment_zero_supported':True,
        'baseline':baseline,'adjusted':adjusted,'adapted_baseline':adapted_base,'adapted':adapted}
(ROOT/'validation/lighting-controls-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('LIGHTING_CONTROLS_VERIFIED actual Blender light/world values')

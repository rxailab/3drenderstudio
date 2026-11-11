"""Run with Blender to check energy independence and camera-facing metal light."""
import importlib.util, json, sys
from pathlib import Path
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from planner import default_plan
spec=importlib.util.spec_from_file_location('worker',ROOT/'blender/worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)

def setup(soft,metal):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,.5));obj=bpy.context.object
    mat=bpy.data.materials.new('fixture');mat.use_nodes=True
    worker.bsdf_of(mat).inputs['Metallic'].default_value=1 if metal else 0
    obj.data.materials.append(mat)
    plan=default_plan();plan['light_softness']=soft
    worker.camera(plan,{'id':'hero','azimuth':35,'elevation':35},[obj])
    bpy.context.view_layer.update()
    return worker.studio(plan,{'id':'hero','style':'white'},[obj])

a=setup(.8,False);b=setup(1,False)
assert all(abs(x['energy']-y['energy'])<1e-5 for x,y in zip(a['lights'],b['lights']))
assert all(x['name']!='Metal reflection softbox' for x in a['lights'])
m=setup(1,True);box=next(x for x in m['lights'] if x['name']=='Metal reflection softbox')
assert box['shape']=='RECTANGLE' and box['target_object']=='Cube'
point=Vector(box['target']);lamp=Vector(box['position']);cam=bpy.context.scene.camera.location
normal=Vector((0,0,1));v=(cam-point).normalized();expected=2*normal.dot(v)*normal-v
assert (lamp-point).normalized().dot(expected)>.999
r={'passed':True,'softness_preserves_power':True,'nonmetal_adds_no_metal_softbox':True,
   'metal_camera_reflection_alignment':True,'metal_softbox':box}
(ROOT/'validation/reflection-light-results.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
print('REFLECTION_LIGHT_TEST_PASSED')

"""Camera semantics plus real Blender view geometry and saved-scene checks."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ai_client
import engine
import planner
import semantic_planner
import studio_store as store
from camera_controls import shot_request,validate_shot,validate_report
from hero_recipe import default_recipe,normalize_recipe
from intelligence_rules import enforce_protections,restrict_patch_schema

ROOT=Path(__file__).resolve().parents[1]


class CameraContractTests(unittest.TestCase):
    def test_new_shots_default_orthographic_without_rewriting_historical_plans(self):
        plan=planner.default_plan()
        self.assertTrue(all(row['projection']=='orthographic' for row in plan['shots']))
        for row in plan['shots']:row.pop('projection')
        original=copy.deepcopy(plan)
        self.assertEqual(planner.validate(plan),original)
        self.assertEqual(shot_request(plan['shots'][0])['projection'],'orthographic')

    def test_view_fields_are_bounded_finite_data(self):
        for field,value in [('ortho_scale',0),('ortho_scale',True),('ortho_scale',float('inf')),
                            ('camera_distance',-1),('camera_distance',float('nan')),
                            ('target_offset',[0,1]),('target_offset',[0,0,11]),
                            ('target_offset',[False,0,0]),('projection','PANO')]:
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):validate_shot({field:value})
        validate_shot({'projection':'perspective','camera_distance':12,'target_offset':[.1,-.2,.3]})

    def test_schema_domains_and_camera_locks_include_dragged_view_fields(self):
        plan=planner.default_plan();schema=ai_client.patch_schema(plan,{})
        view={'id':'hero','projection':'orthographic','ortho_scale':3,'camera_distance':8,'target_offset':[.1,0,0]}
        ai_client._check_schema({'shots':[view]},schema)
        self.assertEqual(semantic_planner._domains({'shots':[view]}),{'camera'})
        enforce_protections({'shots':[view]},['only_camera'],'hero')
        with self.assertRaises(ValueError):enforce_protections({'shots':[view]},['preserve_camera'],'hero')
        restricted=restrict_patch_schema(schema,['preserve_camera'],'hero')
        self.assertFalse(set(restricted['properties'].get('shots',{}).get('items',{}).get('properties',{})) & set(view)-{'id'})

    def test_auto_recipe_preserves_explicit_projection_and_defaults_orthographic(self):
        catalog={'camera':{'type':'PERSP','focal_length':70,'elevation':18},'materials':[]}
        self.assertEqual(default_recipe(catalog)['camera']['projection'],'orthographic')
        catalog['preferred_projection']='perspective'
        self.assertEqual(default_recipe(catalog)['camera']['projection'],'perspective')
        self.assertEqual(normalize_recipe({'camera':{}},catalog)[0]['camera'],{'projection':'perspective'})
        with self.assertRaises(ValueError):normalize_recipe({'camera':{'projection':'orthographic'}},catalog)
        self.assertEqual(default_recipe(catalog,['preserve_camera'])['camera'],{})


NATIVE=r'''
import copy,json,sys
from pathlib import Path
import bpy
from mathutils import Vector
root,out=map(Path,sys.argv[sys.argv.index('--')+1:]);sys.path.insert(0,str(root))
from blender.camera_projection import apply_camera,world_points,snapshot,same_snapshot
from blender.hero_scene import catalog,_checkpoint,_projection
from planner import default_plan
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene;products=[]
for name,position in [('Near',(-1,-1,1)),('Far',(1,1,1))]:
 bpy.ops.mesh.primitive_cube_add(size=1,location=position);obj=bpy.context.object;obj.name=name;products.append(obj)
 material=bpy.data.materials.new(name);material.use_nodes=True;obj.data.materials.append(material)
bpy.ops.object.camera_add(location=(0,-8,1));scene.camera=bpy.context.object
scene.camera.rotation_euler=(Vector((0,0,1))-scene.camera.location).to_track_quat('-Z','Y').to_euler()
scene['studio_product_objects']=json.dumps([obj.name for obj in products])
scene.render.resolution_x=scene.render.resolution_y=64;scene.render.resolution_percentage=100
points=world_points(products);before=catalog(scene,products,points)
legacy=default_plan()
for shot in legacy['shots']:shot.pop('projection')
legacy['shots'][0].update(azimuth=0,elevation=0,focal_length=50)
operations=[];_checkpoint(scene,products,points,legacy,legacy,'hero',operations,[])
converted=catalog(scene,products,points)
widths=[_projection(scene,world_points([obj]))['occupied_width'] for obj in products]
aspects=[]
for x,y in [(64,64),(64,48),(48,64)]:
 scene.render.resolution_x=x;scene.render.resolution_y=y
 apply_camera(scene,{'azimuth':35,'elevation':20,'focal_length':70},products,.12,points)
 aspects.append(_projection(scene,points))
scene.render.resolution_x=scene.render.resolution_y=64
manual=copy.deepcopy(legacy);manual['shots'][0].update(projection='orthographic',azimuth=73,elevation=17,ortho_scale=7,target_offset=[.1,-.05,.07])
_checkpoint(scene,products,points,manual,legacy,'hero',[],[])
manual_camera=catalog(scene,products,points)['camera'];saved=snapshot(scene)
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
scene.render.threads_mode='FIXED';scene.render.threads=2;scene.render.filepath=str(out/'image.png')
scene.render.image_settings.file_format='PNG';bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(out/'scene.blend'));bpy.ops.wm.open_mainfile(filepath=str(out/'scene.blend'),load_ui=False,use_scripts=False)
scene=bpy.context.scene;products=[scene.objects[name] for name in ['Near','Far']];points=world_points(products)
reopened=same_snapshot(saved,snapshot(scene))
followup=copy.deepcopy(manual);followup['exposure']=.2
_checkpoint(scene,products,points,followup,manual,'hero',[],[])
stable=same_snapshot(saved,snapshot(scene))
perspective=copy.deepcopy(followup);perspective['shots'][0].update(projection='perspective',azimuth=0,elevation=0,camera_distance=8,target_offset=[0,0,0])
_checkpoint(scene,products,points,perspective,followup,'hero',[],[])
perspective_camera=catalog(scene,products,points)['camera']
perspective_widths=[_projection(scene,world_points([obj]))['occupied_width'] for obj in products]
after=catalog(scene,products,points)
(out/'measurements.json').write_text(json.dumps({'before_type':before['camera']['type'],'converted':converted['camera'],
 'converted_operations':operations,'widths':widths,'aspects':aspects,'manual':manual_camera,'reopened':reopened,'stable_followup':stable,
 'perspective':perspective_camera,'perspective_widths':perspective_widths,
 'geometry_preserved':before['geometry_sha256']==after['geometry_sha256'],
 'transforms_preserved':before['transforms_sha256']==after['transforms_sha256']}))
'''


@unittest.skipUnless(shutil.which('blender'),'Blender unavailable')
class NativeCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        out=Path(cls.temp.name);script=out/'fixture.py';script.write_text(NATIVE)
        process=subprocess.run([shutil.which('blender'),'-b','--factory-startup','--disable-autoexec','-t','2',
            '--python-exit-code','1','--python',str(script),'--',str(ROOT),str(out)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
        if process.returncode:raise RuntimeError(process.stdout[-6000:])
        cls.result=json.loads((out/'measurements.json').read_text())

    def test_old_checkpoint_converts_to_parallel_projection_with_equal_depth_scale(self):
        row=self.result
        self.assertEqual(row['before_type'],'PERSP');self.assertEqual(row['converted']['type'],'ORTHO')
        self.assertEqual(row['converted']['sensor_fit'],'HORIZONTAL')
        self.assertAlmostEqual(*row['widths'],places=5)
        self.assertTrue(row['converted_operations'])
        self.assertTrue(row['geometry_preserved']);self.assertTrue(row['transforms_preserved'])

    def test_full_product_fits_all_supported_aspects(self):
        for framing in self.result['aspects']:
            self.assertFalse(framing['clipped']);self.assertAlmostEqual(framing['occupancy'],.76,places=5)
            self.assertAlmostEqual((framing['bbox'][0]+framing['bbox'][2])/2,.5,places=5)
            self.assertAlmostEqual((framing['bbox'][1]+framing['bbox'][3])/2,.5,places=5)

    def test_dragged_scale_pan_and_angles_survive_render_save_and_material_followup(self):
        row=self.result;camera=row['manual']
        self.assertAlmostEqual(camera['ortho_scale'],7,places=5)
        self.assertAlmostEqual(camera['azimuth'],73,places=4);self.assertAlmostEqual(camera['elevation'],17,places=4)
        for actual,requested in zip(camera['target_offset'],[.1,-.05,.07]):self.assertAlmostEqual(actual,requested,places=6)
        self.assertTrue(row['reopened']);self.assertTrue(row['stable_followup'])

    def test_explicit_perspective_switch_honors_dolly_distance(self):
        camera=self.result['perspective'];self.assertEqual(camera['type'],'PERSP')
        self.assertAlmostEqual(camera['distance'],8,places=5)
        near,far=self.result['perspective_widths'];self.assertGreater(near,far*1.1)


WORKER=r'''
import json,sys
from pathlib import Path
import bpy
from mathutils import Vector
request=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text());out=Path(request['output_dir'])
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene;bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,1));bpy.context.object.name='Body'
bpy.ops.object.camera_add(location=(0,-6,3));scene.camera=bpy.context.object
scene.camera.rotation_euler=(Vector((0,0,1))-scene.camera.location).to_track_quat('-Z','Y').to_euler()
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
scene.render.resolution_x=scene.render.resolution_y=48;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.threads_mode='FIXED';scene.render.threads=2
scene.render.filepath=str(out/'image.png')
if request.get('fixture_break_hooks'):
 bpy.app.handlers.render_pre.clear();bpy.app.handlers.render_post.clear()
bpy.ops.wm.save_as_mainfile(filepath=str(out/'scene.blend'));bpy.ops.render.render(write_still=True)
(out/'result.json').write_text(json.dumps({'success':True,'render_pipeline_version':3}))
'''


@unittest.skipUnless(shutil.which('blender'),'Blender unavailable')
class GenericCameraAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.runtime=self.root/'app';(self.runtime/'blender').mkdir(parents=True)
        for name in ('camera_entry.py','camera_projection.py'):shutil.copy2(ROOT/'blender'/name,self.runtime/'blender'/name)
        shutil.copy2(ROOT/'camera_controls.py',self.runtime/'camera_controls.py')
        (self.runtime/'blender/worker.py').write_text(WORKER)
        self.data=self.root/'data';self.data.mkdir()
        for module in (store,engine):
            context=patch.object(module,'DATA',self.data);context.start();self.addCleanup(context.stop)
        context=patch.object(engine,'ROOT',self.runtime);context.start();self.addCleanup(context.stop)
        with store.connection() as db:db.execute('CREATE TABLE records(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.plan=planner.default_plan();self.plan['shots'][0].update(ortho_scale=5,target_offset=[.1,0,.05])
        self.job={'id':'job','project_id':'p','status':'running','plan':self.plan}
        store.put('project',{'id':'p','analysis':{'objects':[{'name':'Body','type':'MESH'}]}});store.put('job',self.job)

    def test_no_light_angle_request_still_uses_real_verified_orthographic_camera(self):
        request={'action':'render','plan':self.plan,'shot':self.plan['shots'][0]}
        out=self.data/'p'/'render';result=engine.execute(self.job,request,out)
        self.assertNotIn('light_direction_contract',request)
        proof=result['camera_projection_verification'];self.assertEqual(proof['actual']['type'],'ORTHO')
        self.assertEqual(proof['actual']['sensor_fit'],'HORIZONTAL');self.assertAlmostEqual(proof['actual']['ortho_scale'],5)
        self.assertEqual(validate_report(proof,request['camera_projection_contract'],out),proof)
        (out/'scene.blend').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):validate_report(proof,request['camera_projection_contract'],out)

    def test_worker_bypassing_camera_hook_cannot_publish_a_false_projection_claim(self):
        request={'action':'render','plan':self.plan,'shot':self.plan['shots'][0],'fixture_break_hooks':True}
        with self.assertRaisesRegex(RuntimeError,'钩子'):engine.execute(self.job,request,self.data/'p'/'bad')


if __name__=='__main__':unittest.main()

"""Actual Blender renders for the optional light-direction adapter.

The worker here is an explicit synthetic fixture, not the user's missing
worker. The service, adapter, lights, saved .blend and PNG are all real.
"""
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image,ImageChops,ImageStat

import engine
import planner
import studio_store as store
from lighting_controls import LIGHT_DIRECTION_LIMITS
from blender.light_direction import ANGLE_FIELDS,file_digest,requested_angles,validate_report


WORKER=r'''
import json,math,sys
from pathlib import Path
import bpy
from mathutils import Vector
request=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
out=Path(request['output_dir']);options=request.get('_fixture',{})
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,1));body=bpy.context.object;body.name='Body';body.scale=(1.8,1,2)
material=bpy.data.materials.new('Product');material.diffuse_color=(.6,.2,.1,1);body.data.materials.append(material)
bpy.ops.mesh.primitive_plane_add(size=100,location=(0,0,0));bpy.context.object.name='StudioFloor'
bpy.ops.object.camera_add(location=(6,0,3) if options.get('rotated_camera') else (0,-6,3));camera=bpy.context.object;camera.name='ProductCamera'
camera.rotation_euler=(Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler();scene.camera=camera
if options.get('focus_target'):
    camera.data.dof.use_dof=True;camera.data.dof.focus_object=body
lights={}
for role,position,energy in [('key',(-3,-4,5),700),('fill',(4,-2,3),250),('rim',(1,4,4),400)]:
    name={'key':'KeyLight','fill':'FillLight','rim':'RimLight'}[role]
    if options.get('unknown_role') and role=='key':name='AreaUnknown'
    data=bpy.data.lights.new(name,'AREA');data.energy=energy;data.shape='DISK';data.size=3
    light=bpy.data.objects.new(name,data);scene.collection.objects.link(light);light.location=position
    light.rotation_euler=(Vector((0,0,1))-light.location).to_track_quat('-Z','Y').to_euler();lights[role]=light
if options.get('duplicate_role'):
    duplicate=lights['key'].copy();duplicate.data=lights['key'].data.copy();scene.collection.objects.link(duplicate)
    duplicate.name='KeyLight.001'
if options.get('hidden_collection'):
    hidden=bpy.data.collections.new('HiddenStudio');scene.collection.children.link(hidden)
    hidden.objects.link(lights['key']);scene.collection.objects.unlink(lights['key']);hidden.hide_render=True
if options.get('clear_handlers'):
    bpy.app.handlers.render_pre.clear();bpy.app.handlers.render_post.clear()
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
scene.render.resolution_x=64;scene.render.resolution_y=64;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
scene.render.threads_mode='FIXED';scene.render.threads=2
scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.15
scene.render.filepath=str(out/'product.png')
if not options.get('no_save'):bpy.ops.wm.save_as_mainfile(filepath=str(out/'product.blend'))
bpy.ops.render.render(write_still=True)
if options.get('bad_saved'):
    bpy.app.handlers.save_pre.clear();lights['key'].location=(0,4,5)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'product.blend'))
result={'success':True,'render_pipeline_version':2,
        'fixture_lights':{role:{'position':list(light.matrix_world.translation),'energy':light.data.energy,'size':light.data.size} for role,light in lights.items()}}
(out/'result.json').write_text(json.dumps(result))
'''


class LightDirectionContractTests(unittest.TestCase):
    def test_actual_studio_softbox_names_are_exact_roles_not_fuzzy_matches(self):
        from blender.light_direction import _role
        class Lamp(dict):
            def __init__(self,name):super().__init__();self.name=name
        for role,names in {'key':['Key softbox','Hero | Main softbox'],
                           'fill':['Fill softbox','Hero | Front fill'],
                           'rim':['Edge softbox','Hero | Upper edge']}.items():
            for name in names:self.assertEqual(_role(Lamp(name)),role)
        for name in ('Key.1_2','Key softbox backup','Metal reflection softbox','Unknown front fill'):
            self.assertIsNone(_role(Lamp(name)))

    def test_direction_fields_have_finite_degree_bounds(self):
        self.assertEqual(ANGLE_FIELDS,LIGHT_DIRECTION_LIMITS)
        for key,(lo,hi) in ANGLE_FIELDS.items():
            self.assertEqual(requested_angles({key:lo}),{key:lo})
            self.assertEqual(requested_angles({key:hi}),{key:hi})
            for value in (True,None,'45',float('nan'),float('inf'),lo-.01,hi+.01):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):requested_angles({key:value})
        self.assertEqual(requested_angles({'key_power':1.5}),{})

    def test_missing_or_replayed_execution_proof_is_rejected(self):
        contract={'request_id':'a'*32,'requested':{'key_azimuth':30}}
        for value in (None,{}, {'version':1,'request_id':'b'*32,'requested':contract['requested'],
                              'saved_scene_verified':True,'render_completed':True}):
            with self.subTest(value=value),self.assertRaises(RuntimeError):validate_report(value,contract,Path('/tmp'))


@unittest.skipUnless(shutil.which('blender'),'Blender unavailable: actual light-direction rendering not run')
class LightDirectionBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name);cls.runtime=cls.root/'app';(cls.runtime/'blender').mkdir(parents=True)
        source=Path(__file__).resolve().parents[1]/'blender'
        for name in ('light_direction.py','render_entry.py','inspect_lighting.py','camera_entry.py','camera_projection.py'):shutil.copy2(source/name,cls.runtime/'blender'/name)
        shutil.copy2(source.parent/'camera_controls.py',cls.runtime/'camera_controls.py')
        (cls.runtime/'blender/worker.py').write_text(WORKER,encoding='utf-8')

    def setUp(self):
        self.data=self.root/self._testMethodName;self.data.mkdir()
        for module in (store,engine):
            mocked=patch.object(module,'DATA',self.data);mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'ROOT',self.runtime);mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.plan=planner.default_plan()
        self.plan['shots'][0].update(azimuth=0,elevation=math.degrees(math.atan2(2,6)))
        store.put('project',{'id':'p','version':1,'name':'Fixture','model_path':'synthetic fixture','plan':self.plan,'outputs':[],
                             'analysis':{'objects':[{'name':'Body','type':'MESH'}]}})
        self.job={'id':'job','project_id':'p','status':'running','kind':'preview','quality':'preview','version':1,'plan':self.plan}
        store.put('job',self.job)

    def execute(self,angles=None,options=None,name='render'):
        plan=copy.deepcopy(self.plan);plan['lighting'].update(angles or {})
        if (options or {}).get('rotated_camera'):plan['shots'][0]['azimuth']=90
        request={'action':'render','model_path':'synthetic fixture, not a user model','plan':plan,
                 'shot':plan['shots'][0],'quality':'preview','_fixture':options or {}}
        result=engine.execute(self.job,request,self.data/'p'/name)
        return result,request,self.data/'p'/name

    def test_actual_positions_and_saved_scene_follow_angles_with_product_only_target(self):
        _,_,original=self.execute(name='original')
        result,request,out=self.execute({'key_azimuth':40,'key_elevation':25,'fill_azimuth':-20})
        proof=result['light_direction_verification'];self.assertTrue(proof['saved_scene_verified'])
        self.assertEqual(proof['target']['source'],'scene.wuying_product_target')
        self.assertEqual(proof['target']['point'],[0,0,1])
        by_role={row['role']:row for row in proof['lights']}
        self.assertAlmostEqual(by_role['key']['after']['azimuth'],40,places=3)
        self.assertAlmostEqual(by_role['key']['after']['elevation'],25,places=3)
        self.assertAlmostEqual(by_role['fill']['after']['azimuth'],-20,places=3)
        self.assertAlmostEqual(by_role['fill']['before']['elevation'],by_role['fill']['after']['elevation'],places=3)
        for row in proof['lights']:
            self.assertAlmostEqual(row['before']['distance'],row['after']['distance'],places=4)
            self.assertEqual(row['before']['light_data'],row['after']['light_data'])
        self.assertEqual(result['fixture_lights']['rim']['position'],[1,4,4])
        record=engine.output_record({**self.job,'plan':request['plan']},request['shot'],out,result)
        self.assertEqual(record['blend_sha256'],file_digest(out/'product.blend'))
        self.assertEqual({row['role'] for row in record['metrics']['studio_light_directions']},{'key','fill'})
        self.assertEqual(validate_report(proof,request['light_direction_contract'],out),proof)
        with Image.open(original/'product.png') as previous,Image.open(out/'product.png') as updated:
            self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(previous.convert('RGB'),updated.convert('RGB'))).mean),1)

    def test_same_relative_direction_follows_rotated_camera(self):
        front,_,_=self.execute({'key_azimuth':0,'key_elevation':30},name='front')
        rotated,_,_=self.execute({'key_azimuth':0,'key_elevation':30},{'rotated_camera':True},name='rotated')
        a=front['light_direction_verification']['lights'][0]['after']['position']
        b=rotated['light_direction_verification']['lights'][0]['after']['position']
        self.assertAlmostEqual(a[0],0,places=4);self.assertLess(a[1],0)
        self.assertGreater(b[0],0);self.assertAlmostEqual(b[1],0,places=4)
        self.assertAlmostEqual(a[2],b[2],places=4)

    def test_no_light_angle_request_keeps_lamps_and_verifies_default_camera(self):
        result,request,_=self.execute()
        self.assertNotIn('light_direction_contract',request)
        self.assertNotIn('light_direction_verification',result)
        self.assertEqual(result['camera_projection_verification']['actual']['type'],'ORTHO')
        self.assertEqual(result['fixture_lights']['key']['position'],[-3,-4,5])

    def test_unknown_or_duplicate_role_cannot_be_reported_as_applied(self):
        for mode in ('unknown_role','duplicate_role'):
            with self.subTest(mode=mode),self.assertRaisesRegex(RuntimeError,'无法唯一确认'):
                self.execute({'key_azimuth':0},{mode:True},name=mode)

    def test_missing_hook_or_saved_scene_is_rejected(self):
        for mode,phrase in [('clear_handlers','钩子'),('no_save','保存的 Blender'),('bad_saved','场景与实际出图')]:
            with self.subTest(mode=mode),self.assertRaisesRegex(RuntimeError,phrase):
                self.execute({'key_azimuth':0},{mode:True},name=mode)

    def test_explicit_camera_target_works_without_guessing_a_scene_bbox(self):
        project=store.get('project','p');project['analysis']['objects']=[];store.put('project',project)
        result,_,_=self.execute({'key_azimuth':-45},{'focus_target':True})
        self.assertEqual(result['light_direction_verification']['target']['source'],'scene.wuying_product_target')
        self.assertEqual(result['light_direction_verification']['target']['point'],[0,0,1])

    def test_missing_product_target_refuses_to_include_the_floor(self):
        project=store.get('project','p');project['analysis']['objects']=[];store.put('project',project)
        with self.assertRaisesRegex(RuntimeError,'缺少明确的产品对象'):
            self.execute({'key_azimuth':0})

    def test_output_tampering_invalidates_the_execution_report(self):
        result,request,out=self.execute({'key_azimuth':0})
        proof=result['light_direction_verification']
        (out/'product.blend').write_bytes(b'not the verified scene')
        with self.assertRaisesRegex(RuntimeError,'实际输出文件不一致'):
            validate_report(proof,request['light_direction_contract'],out)

    def test_cached_image_without_direction_proof_is_rerendered(self):
        _,_,out=self.execute(name='old')
        project=store.get('project','p');project.update(slug='fixture',status='ready')
        project['plan']['lighting']['key_azimuth']=30;project['plan']['visual_review']=False
        project['outputs']=[{'version':1,'quality':'preview','shot_id':'hero','job_id':'old',
                             'png_url':'/files/p/old/product.png','jpg_url':'/files/p/old/product.jpg',
                             'blend_url':'/files/p/old/product.blend','result_url':'/files/p/old/result.json'}]
        store.put('project',project)
        job={**self.job,'status':'queued','plan':project['plan'],'shot_ids':['hero'],'completed':[],'reused':[]}
        store.put('job',job)
        with patch.object(engine,'execute',side_effect=RuntimeError('fresh render requested')) as execute:
            engine._run_job(job['id'])
        execute.assert_called_once()
        self.assertEqual(store.get('job',job['id'])['status'],'failed')
        self.assertIn('fresh render requested',store.get('job',job['id'])['error'])
        self.assertEqual(store.get('job',job['id'])['reused'],[])

    def test_read_only_inspection_measures_old_worker_scene_without_modifying_it(self):
        result,request,out=self.execute(name='source')
        record=engine.output_record(self.job,request['shot'],out,result);digest=file_digest(out/'product.blend')
        measured=engine.inspect_lighting(store.get('project','p'),record)
        self.assertEqual(set(measured['available_roles']),{'key','fill','rim'})
        self.assertEqual(measured['unavailable_roles'],{})
        key=next(row for row in measured['directions'] if row['role']=='key')
        self.assertAlmostEqual(key['azimuth'],math.degrees(math.atan2(-3,4)),places=3)
        self.assertEqual(key['target'],[0,0,1])
        self.assertEqual(measured['source']['sha256'],digest)
        self.assertEqual(file_digest(out/'product.blend'),digest)

    def test_inspection_marks_unknown_role_unavailable_without_guessing(self):
        result,request,out=self.execute(options={'unknown_role':True})
        record=engine.output_record(self.job,request['shot'],out,result)
        measured=engine.inspect_lighting(store.get('project','p'),record)
        self.assertEqual(set(measured['available_roles']),{'fill','rim'})
        self.assertIn('无法唯一确认',measured['unavailable_roles']['key'])
        self.assertEqual({row['role'] for row in measured['directions']},{'fill','rim'})

    def test_inspection_does_not_offer_a_light_inside_a_hidden_collection(self):
        result,request,out=self.execute(options={'hidden_collection':True})
        record=engine.output_record(self.job,request['shot'],out,result)
        measured=engine.inspect_lighting(store.get('project','p'),record)
        self.assertNotIn('key',measured['available_roles'])
        self.assertIn('不可见',measured['unavailable_roles']['key'])

    def test_inspection_source_change_is_a_hard_stale_error(self):
        result,request,out=self.execute();record=engine.output_record(self.job,request['shot'],out,result)
        original=engine.subprocess.run
        def changed(*args,**kwargs):
            value=original(*args,**kwargs)
            with (out/'product.blend').open('ab') as stream:stream.write(b'changed-after-inspection')
            return value
        with patch.object(engine.subprocess,'run',side_effect=changed),self.assertRaises(engine.LightingInspectionStale):
            engine.inspect_lighting(store.get('project','p'),record)


if __name__=='__main__':unittest.main()

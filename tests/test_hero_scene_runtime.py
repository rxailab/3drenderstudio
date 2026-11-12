"""Real local Blender contracts for the source-scene photography executor."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image
from hero_recipe import default_recipe
from hero_state import compare_scene_states,state_digest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'blender/hero_scene.py'

FIXTURE=r'''
import bpy,json,sys
from pathlib import Path
from mathutils import Vector
target=Path(sys.argv[sys.argv.index('--')+1])
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,1));body=bpy.context.object;body.name='Body';body.scale=(1.6,1,2)
mat=bpy.data.materials.new('Paint');mat.use_nodes=True;p=mat.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(.4,.02,.03,1);p.inputs['Roughness'].default_value=.35;p.inputs['Metallic'].default_value=.6
body.data.materials.append(mat)
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,-.51,1.5));label=bpy.context.object;label.name='Brand label';label.scale=(.6,.02,.18)
logo=bpy.data.materials.new('Logo');logo.use_nodes=True;logo.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(.8,.8,.8,1);label.data.materials.append(logo)
bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.002));floor=bpy.context.object;floor.name='Studio floor'
fm=bpy.data.materials.new('Floor');fm.use_nodes=True;floor.data.materials.append(fm)
bpy.ops.object.camera_add(location=(3,-5,3));camera=bpy.context.object;camera.name='Product camera';camera.data.lens=65
camera.rotation_euler=(Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler();scene.camera=camera
for name,position,energy in [('KeyLight',(-3,-4,5),350),('FillLight',(3,-2,3),120),('RimLight',(1,3,4),180)]:
 data=bpy.data.lights.new(name,'AREA');data.energy=energy;data.size=3
 light=bpy.data.objects.new(name,data);scene.collection.objects.link(light);light.location=position
 light.rotation_euler=(Vector((0,0,1))-light.location).to_track_quat('-Z','Y').to_euler()
scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.25
scene['studio_product_objects']=json.dumps(['Body','Brand label'])
scene['studio_plan']=json.dumps({'shots':[{'azimuth':999,'focal_length':150}]})
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1;scene.cycles.use_denoising=False
scene.render.resolution_x=64;scene.render.resolution_y=64;scene.render.resolution_percentage=100
scene.render.threads_mode='FIXED';scene.render.threads=2
bpy.ops.wm.save_as_mainfile(filepath=str(target))
'''

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

@unittest.skipUnless(shutil.which('blender'),'Blender unavailable: native photography contracts not run')
class HeroSceneRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.folder=Path(cls.temp.name);cls.source=cls.folder/'source.blend';fixture=cls.folder/'fixture.py';fixture.write_text(FIXTURE)
        run=subprocess.run([shutil.which('blender'),'-b','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(fixture),'--',str(cls.source)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
        if run.returncode:raise RuntimeError(run.stdout[-5000:])
        cls.original_hash=sha(cls.source);cls.sequence=0;cls.optimized_result=None
        cls.inspection=cls.invoke('inspect')['catalog']

    @classmethod
    def invoke(cls,action='render',source=None,expect_success=True,executable=None,**fields):
        cls.sequence+=1;out=cls.folder/('out-'+str(cls.sequence));out.mkdir()
        source=source or cls.source
        request={'action':action,'source_blend':str(source),'source_sha256':sha(source),
                 'output_dir':str(out),'request_id':'fixture-'+str(cls.sequence),'resolution':64,'samples':1,'force_cpu':True,
                 'target_aspect':'1:1',**fields}
        path=out/'request.json';path.write_text(json.dumps(request,ensure_ascii=False))
        run=subprocess.run([executable or shutil.which('blender'),'-b','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(SCRIPT),'--',str(path)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
        (out/'log.txt').write_text(run.stdout)
        result=json.loads((out/'result.json').read_text()) if (out/'result.json').exists() else {'success':False,'error':run.stdout[-3000:]}
        if expect_success and (run.returncode or not result.get('success')):raise AssertionError(str(result)+'\n'+run.stdout[-5000:])
        if not expect_success and (run.returncode==0 or result.get('success')):raise AssertionError('Expected native refusal')
        result['_out']=out;result['_request']=request
        return result

    @classmethod
    def optimized(cls):
        if cls.optimized_result is None:
            recipe=default_recipe(cls.inspection)
            body=next(m for m in cls.inspection['materials'] if m['name']=='Paint')
            recipe['surfaces']=[{'id':body['id'],'roughness':.36,'metallic':0,'specular':.45,'coat_weight':.04,'coat_roughness':.3,'reason':'Known painted fixture'}]
            cls.optimized_result=cls.invoke(recipe=recipe)
        return cls.optimized_result

    def test_inspection_uses_actual_scene_and_registered_product_only(self):
        c=self.inspection
        self.assertEqual(c['product_object_names'],['Body','Brand label'])
        self.assertEqual(c['bounds']['dimensions'],[1.6,1.02,2.0])
        self.assertLess(c['camera']['azimuth'],90);self.assertEqual(c['camera']['focal_length'],65)
        self.assertTrue(c['ground']['editable'])
        self.assertTrue(next(m for m in c['materials'] if m['name']=='Logo')['protected'])
        self.assertEqual({row['role'] for row in c['light_directions']},{'key','fill','rim'})
        self.assertEqual(sha(self.source),self.original_hash)

    def test_recipe_renders_and_reopens_preserved_geometry_colors_and_labels(self):
        result=self.optimized();proof=result['proof']
        for key in ('geometry_preserved','transforms_preserved','color_preserved','resources_preserved','labels_preserved','saved_scene_verified','render_completed'):
            self.assertTrue(proof[key],key)
        self.assertAlmostEqual(result['catalog']['camera']['occupancy'],.84,places=3)
        self.assertFalse(result['checks']['clipped'])
        self.assertAlmostEqual(result['catalog']['lighting']['key']['azimuth'],-45,places=3)
        for role,ratio in (('key',1.35),('fill',.75),('rim',.5)):
            self.assertAlmostEqual(result['catalog']['lighting'][role]['energy'],self.inspection['lighting'][role]['energy']*ratio,places=3)
        self.assertEqual(proof['source_sha256'],self.original_hash)
        self.assertEqual(proof['png_sha256'],sha(result['_out']/'image.png'))
        self.assertEqual(proof['blend_sha256'],sha(result['_out']/'scene.blend'))
        self.assertEqual(sha(self.source),self.original_hash)
        with Image.open(result['_out']/'image.png') as image:self.assertEqual(image.size,(64,64))
        self.assertGreater(result['metrics']['product']['eligible_opaque_samples'],30)
        self.assertGreater(result['metrics']['background']['samples'],0)

    def test_empty_recipe_keeps_actual_photography(self):
        result=self.invoke(recipe={})
        self.assertEqual(result['operations'],[])
        self.assertEqual(result['catalog']['camera'],self.inspection['camera'])
        self.assertEqual(result['catalog']['lights'],self.inspection['lights'])
        self.assertEqual(result['catalog']['materials'],self.inspection['materials'])

    def test_final_resolution_does_not_change_accepted_scene_state(self):
        optimized=self.optimized()
        # Final output reuses the selected native scene, rather than fitting
        # the same camera recipe independently at another raster resolution.
        again=self.invoke(source=optimized['_out']/'scene.blend',recipe={},resolution=2000,samples=2)
        self.assertEqual(again['catalog']['actual_state_sha256'],optimized['catalog']['actual_state_sha256'])
        self.assertTrue(compare_scene_states(optimized['catalog'],again['catalog'])['equivalent'])
        self.assertEqual(again['operations'],[])

    def test_state_comparison_allows_only_small_physical_roundoff(self):
        original=self.optimized()['catalog'];tiny=copy.deepcopy(original)
        tiny['scene_state']['camera']['shift_x']+=4.284083843e-8
        tiny['scene_state']['camera']['matrix_world'][0][3]+=5e-7
        tiny['scene_state_sha256']=state_digest(tiny['scene_state'])
        checked=compare_scene_states(original,tiny)
        self.assertTrue(checked['equivalent']);self.assertTrue(checked['roundoff_only'])
        self.assertEqual(checked['roundoff_count'],2)
        actual=copy.deepcopy(original);actual['scene_state']['camera']['shift_x']+=.001
        actual['scene_state_sha256']=state_digest(actual['scene_state'])
        checked=compare_scene_states(original,actual)
        self.assertFalse(checked['equivalent']);self.assertEqual(checked['differences'][0]['path'],'$.camera.shift_x')
        # A tiny model at a very large world origin must not inherit a large
        # relative-to-coordinate tolerance for camera translations.
        small=copy.deepcopy(original);small['scene_state']['product_scale']=.01
        small['scene_state']['camera']['matrix_world'][0][3]=100000000.0
        small['scene_state_sha256']=state_digest(small['scene_state'])
        moved=copy.deepcopy(small);moved['scene_state']['camera']['matrix_world'][0][3]+=5e-7
        moved['scene_state_sha256']=state_digest(moved['scene_state'])
        checked=compare_scene_states(small,moved)
        self.assertFalse(checked['equivalent']);self.assertEqual(checked['differences'][0]['tolerance'],2e-8)

    def test_state_comparison_rejects_real_light_surface_and_exact_product_changes(self):
        original=self.optimized()['catalog']
        for domain,field,delta in [('lights','energy',.01),('surfaces','roughness',.001)]:
            changed=copy.deepcopy(original);changed['scene_state'][domain][0][field]+=delta
            changed['scene_state_sha256']=state_digest(changed['scene_state'])
            self.assertFalse(compare_scene_states(original,changed)['equivalent'])
        for field in ('geometry_sha256','transforms_sha256','color_sha256','resources_sha256','labels_sha256'):
            changed=copy.deepcopy(original);changed[field]='0'*64;changed['scene_state']['product'][field]='0'*64
            changed['scene_state_sha256']=state_digest(changed['scene_state'])
            checked=compare_scene_states(original,changed)
            self.assertFalse(checked['equivalent']);self.assertEqual(checked['differences'][0]['path'],'$.product.'+field)
        changed=copy.deepcopy(original);changed['scene_state']['camera']['focal_length']+=.01
        changed['scene_state_sha256']=state_digest(changed['scene_state'])
        self.assertFalse(compare_scene_states(original,changed)['equivalent'])

    def test_state_comparison_requires_fresh_complete_and_checksum_matched_evidence(self):
        original=self.optimized()['catalog']
        legacy=copy.deepcopy(original);legacy.pop('scene_state')
        self.assertEqual(compare_scene_states(original,legacy)['reason'],'missing_scene_state')
        stale=copy.deepcopy(original);stale['scene_state']['camera']['focal_length']+=1
        self.assertEqual(compare_scene_states(original,stale)['reason'],'scene_state_checksum_mismatch')
        missing=copy.deepcopy(original);missing['scene_state']['camera'].pop('matrix_world')
        missing['scene_state_sha256']=state_digest(missing['scene_state'])
        self.assertEqual(compare_scene_states(missing,missing)['reason'],'invalid_scene_state_schema')
        for value in (float('nan'),float('inf'),True):
            with self.subTest(invalid=value):
                invalid=copy.deepcopy(original);invalid['scene_state']['exposure']=value
                if value is True:invalid['scene_state_sha256']=state_digest(invalid['scene_state'])
                self.assertFalse(compare_scene_states(original,invalid)['equivalent'])
        self.assertFalse(compare_scene_states({'scene_state':{}},{'scene_state':{}})['equivalent'])

    def base_plan(self,optimized):
        camera=optimized['catalog']['camera']
        return {'aspect':'1:1','margin':.08,'exposure':optimized['catalog']['exposure'],'light_softness':1,
                'rotation':[0,0,0],'pose':{},'repair':{'smooth':False,'normals':False},'style':'soft',
                'texture_scale':1,'texture_rotation':0,'material_overrides':[],'object_material_overrides':[],
                'lighting':{'key_power':1,'fill_power':1,'rim_power':1,'environment':1,
                    **{role+'_'+axis:optimized['catalog']['lighting'][role][axis] for role in ('key','fill','rim') for axis in ('azimuth','elevation')}},
                'shots':[{'id':'hero','kind':'full','style':'white',**{key:camera[key] for key in ('azimuth','elevation','focal_length')}}]}

    def test_checkpoint_applies_absolute_camera_and_light_with_power_ratio(self):
        optimized=self.optimized();baseline=self.base_plan(optimized);plan=copy.deepcopy(baseline)
        plan['shots'][0]['azimuth']=30;plan['lighting']['key_azimuth']=20;plan['lighting']['key_power']=1.5;plan['margin']=.1;plan['aspect']='4:3'
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero',target_aspect=None)
        c=result['catalog'];self.assertAlmostEqual(c['camera']['azimuth'],30,places=3)
        self.assertAlmostEqual(c['lighting']['key']['azimuth'],20,places=3)
        self.assertAlmostEqual(c['lighting']['key']['energy'],optimized['catalog']['lighting']['key']['energy']*1.5,places=3)
        self.assertAlmostEqual(c['camera']['occupancy'],.8,places=3)
        with Image.open(result['_out']/'image.png') as image:self.assertEqual(image.size,(64,48))

    def test_checkpoint_explicit_color_is_reported_without_false_preservation(self):
        optimized=self.optimized();baseline=self.base_plan(optimized);plan=copy.deepcopy(baseline)
        plan['object_material_overrides']=[{'object_name':'Body','material_name':'Paint','color':'#234567'}]
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero')
        proof=result['proof'];self.assertFalse(proof['color_preserved']);self.assertTrue(proof['labels_preserved']);self.assertTrue(proof['request_match'])
        self.assertEqual(proof['request_plan_sha256'],digest(plan));self.assertEqual(proof['request_baseline_plan_sha256'],digest(baseline))
        changes=proof['authorized_color_changes'];self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['path'],'object_material_overrides[0].color')
        self.assertEqual(changes[0]['requested_color'],'#234567');self.assertEqual(changes[0]['bindings'],[{'object_name':'Body','slot_index':0}])

    def test_checkpoint_executes_first_local_gloss_override_from_instruction_patch(self):
        from ai_client import apply_patch
        from planner import default_plan
        optimized=self.optimized();baseline={**default_plan(),**self.base_plan(optimized)}
        analysis={'objects':[{'name':'Body','materials':['Paint']}],
                  'materials':[{'name':'Paint','suggested_preset':'painted_metal'}]}
        plan=apply_patch(baseline,{'object_material_overrides':[
            {'object_name':'Body','material_name':'Paint','roughness':.22}],
            'lighting':{'key_power':1.6,'fill_power':1.4}},analysis)
        self.assertTrue(plan['object_material_overrides'][0]['preserve_original'])
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero')
        surface=next(row for row in result['catalog']['materials'] if row['name']=='Paint')
        self.assertAlmostEqual(surface['roughness'],.22,places=5)
        self.assertAlmostEqual(result['catalog']['lighting']['key']['energy'],optimized['catalog']['lighting']['key']['energy']*1.6,places=3)
        self.assertAlmostEqual(result['catalog']['lighting']['fill']['energy'],optimized['catalog']['lighting']['fill']['energy']*1.4,places=3)
        self.assertEqual(result['catalog']['camera'],optimized['catalog']['camera'])
        for field in ('color_preserved','geometry_preserved','transforms_preserved','resources_preserved','labels_preserved'):
            self.assertTrue(result['proof'][field],field)

    def test_checkpoint_global_scalar_override_keeps_shader_despite_plan_default_preset(self):
        from ai_client import apply_patch
        from planner import default_plan
        optimized=self.optimized();baseline={**default_plan(),**self.base_plan(optimized)}
        plan=apply_patch(baseline,{'material_overrides':[{'name':'Paint','roughness':.24}]},
                         {'materials':[{'name':'Paint'}]})
        self.assertEqual(plan['material_overrides'][0]['preset'],'matte_plastic')
        self.assertTrue(plan['material_overrides'][0]['preserve_original'])
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero')
        surface=next(row for row in result['catalog']['materials'] if row['name']=='Paint')
        previous=next(row for row in optimized['catalog']['materials'] if row['name']=='Paint')
        self.assertAlmostEqual(surface['roughness'],.24,places=5)
        for field in ('metallic','specular','coat_weight','coat_roughness'):
            self.assertEqual(surface[field],previous[field])
        self.assertTrue(result['proof']['color_preserved'])

    def test_checkpoint_still_rejects_preservation_toggle_on_existing_override(self):
        optimized=self.optimized();baseline=self.base_plan(optimized)
        baseline['object_material_overrides']=[{'object_name':'Body','material_name':'Paint',
                                               'preset':'painted_metal','preserve_original':False,'roughness':.36}]
        plan=copy.deepcopy(baseline)
        plan['object_material_overrides'][0].update(preserve_original=True,roughness=.22)
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero',expect_success=False)
        self.assertFalse((result['_out']/'image.png').exists())

    def test_checkpoint_applies_explicit_local_preset_without_changing_label(self):
        optimized=self.optimized();baseline=self.base_plan(optimized);plan=copy.deepcopy(baseline)
        plan['object_material_overrides']=[{'object_name':'Body','material_name':'Paint',
                                           'preset':'polished_metal','preserve_original':False}]
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero')
        surface=next(row for row in result['catalog']['materials'] if row['name']=='Paint')
        self.assertAlmostEqual(surface['roughness'],.09,places=4)
        self.assertAlmostEqual(surface['metallic'],1,places=4)
        self.assertTrue(result['proof']['labels_preserved'])
        self.assertTrue(result['proof']['geometry_preserved'])
        self.assertTrue(result['proof']['color_preserved'])

    def test_checkpoint_uses_current_shot_overrides_and_hashes_original_plan(self):
        optimized=self.optimized();baseline=self.base_plan(optimized)
        actual=copy.deepcopy(baseline['lighting'])
        baseline['lighting'].update(key_power=2,fill_power=1.8,key_azimuth=80)
        baseline['exposure']=.5
        baseline['shot_adjustments']={'hero':{'exposure_delta':-.5,'softness_multiplier':1,'lighting':actual},
                                      'other':{'exposure_delta':2,'lighting':{'key_power':3}}}
        plan=copy.deepcopy(baseline)
        plan['shot_adjustments']['hero']['lighting'].update(key_power=1.6,fill_power=1.25,key_azimuth=-25,key_elevation=52)
        plan['shot_adjustments']['hero'].update(exposure_delta=.25,softness_multiplier=1.2)
        result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero')
        current=result['catalog'];previous=optimized['catalog']
        self.assertAlmostEqual(current['exposure'],.75,places=5)
        self.assertAlmostEqual(current['lighting']['key']['azimuth'],-25,places=3)
        self.assertAlmostEqual(current['lighting']['key']['elevation'],52,places=3)
        self.assertAlmostEqual(current['lighting']['key']['energy'],previous['lighting']['key']['energy']*1.6,places=3)
        self.assertAlmostEqual(current['lighting']['fill']['energy'],previous['lighting']['fill']['energy']*1.25,places=3)
        self.assertAlmostEqual(current['lighting']['key']['size'],previous['lighting']['key']['size']*1.2,places=5)
        self.assertEqual(result['proof']['request_plan_sha256'],digest(plan))
        self.assertEqual(result['proof']['request_baseline_plan_sha256'],digest(baseline))

    def test_unsupported_checkpoint_geometry_and_preset_fail_without_render(self):
        optimized=self.optimized();baseline=self.base_plan(optimized)
        for changes in ({'rotation':[0,0,10]},{'object_material_overrides':[{'object_name':'Body','material_name':'Paint','preset':'rubber'}]}):
            plan={**copy.deepcopy(baseline),**changes}
            result=self.invoke(source=optimized['_out']/'scene.blend',plan=plan,baseline_plan=baseline,shot_id='hero',expect_success=False)
            self.assertFalse((result['_out']/'image.png').exists())

    def test_stale_source_is_rejected_before_render(self):
        result=self.invoke(source_sha256='0'*64,expect_success=False)
        self.assertIn('源场景已变化',result['error']);self.assertFalse((result['_out']/'image.png').exists())

    def test_recipe_cannot_modify_color_or_protected_logo(self):
        bad=self.invoke(recipe={'surfaces':[{'id':'m1','color':'#FFFFFF'}]},expect_success=False)
        self.assertFalse((bad['_out']/'image.png').exists())
        logo=next(m for m in self.inspection['materials'] if m['name']=='Logo')
        result=self.invoke(recipe={'surfaces':[{'id':logo['id'],'roughness':.2,'reason':'Unsafe logo change'}]})
        self.assertTrue(result['proof']['labels_preserved']);self.assertTrue(result['warnings'])


if __name__=='__main__':unittest.main()

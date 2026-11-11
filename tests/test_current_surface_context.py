"""Current native surface evidence, independent of old plan presets."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import instruction_service as service
import planner
import target_evidence


SCRIPT = Path(__file__).resolve().parents[1] / 'blender' / 'inspect_parts.py'
SPEC = importlib.util.spec_from_file_location('current_surface_inspector', SCRIPT)
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)


def evidence(values=None, linked=None):
    bounds={'min':[0,0,0],'max':[1,1,1]}
    return {'method':'scene_part_catalog','generation_version':1,
            'source_snapshot':{'output_id':'source','shot_id':'hero','quality':'hero','version':8},
            'catalog_pages':[{'label':'PART_CATALOG_1','width':1600,'height':1200}],
            'targets':[{'id':'t1','object_name':'Cabinet','material_name':'Original red',
                        'bounds':bounds,'components':[{'bounds':bounds,'triangle_count':12}],
                        'geometry_digest':'a'*64,'thumbnail':{'page':0,'rect':[0,0,.1,.2]},
                        'surface_values':values if values is not None else {'roughness':.36,'metallic':.2},
                        'linked_surface_fields':linked or []}]}


class CurrentSurfaceContextTests(unittest.TestCase):
    def setUp(self):
        self.project={'version':8,'plan':planner.default_plan(),
                      'analysis':{'objects':[{'name':'Cabinet','type':'MESH','materials':['Original red']}],
                                  'materials':[{'name':'Original red','suggested_preset':'matte_plastic'}]}}

    def test_actual_surface_values_win_over_old_plan_assumptions(self):
        self.project['plan']['material_overrides']=[{'name':'Original red','preset':'matte_plastic','roughness':.8}]
        rows=service._current_material_values(self.project,evidence())
        self.assertEqual(rows,[{'object_name':'Cabinet','material_name':'Original red',
                               'values':{'roughness':.36,'metallic':.2},'linked_fields':[],
                               'basis':'saved_scene_principled'}])
        self.assertEqual(self.project['plan']['material_overrides'][0]['roughness'],.8)

    def test_stale_or_non_catalog_evidence_is_not_current_material_measurement(self):
        old=evidence();old['source_snapshot']['version']=7
        self.assertEqual(service._current_material_values(self.project,old),[])
        self.assertEqual(service._current_material_values(self.project,{}),[])

    def test_linked_roughness_has_no_claimed_constant_default(self):
        rows=service._current_material_values(self.project,evidence({'metallic':.2},['roughness']))
        self.assertEqual(rows[0]['linked_fields'],['roughness'])
        self.assertNotIn('roughness',rows[0]['values'])

    def test_invalid_surface_claims_cannot_enter_public_context(self):
        for values,linked in (({'roughness':float('nan')},[]),({'roughness':True},[]),
                              ({'roughness':1.1},[]),({'specular':.5},[]),
                              ({'roughness':.2},['roughness']),({},['unexpected'])):
            with self.subTest(values=values,linked=linked):
                self.assertEqual(service._current_material_values(self.project,evidence(values,linked)),[])

    def test_material_identity_must_exist_in_current_analysis(self):
        unknown=evidence();unknown['targets'][0]['material_name']='Invented'
        self.assertEqual(service._current_material_values(self.project,unknown),[])

    def test_native_checkpoint_exposes_real_followup_capabilities(self):
        self.project['plan']['scene_checkpoints']={'hero':'a'*32}
        native=service._context(self.project,'hero',None,[])['execution_capabilities']
        self.assertFalse(native['material_preset_replacement'])
        self.assertEqual(set(native['material_fields']),{'roughness','metallic','transmission','color'})
        self.assertEqual(native['lighting_power_mode'],'relative_to_saved_scene')
        self.assertNotIn('execution_capabilities',service._context(self.project,'campaign',None,[]))

    def test_current_lamp_measurements_preserve_power_units_and_discard_invalid_values(self):
        row={'role':'key','object_name':'Key','camera':'Camera','azimuth':-35,'elevation':45,
             'position':[1,2,3],'target':[0,0,1],'energy':1000,'size':2,'size_y':3,
             'type':'AREA','shape':'RECTANGLE','secret':'not public'}
        output={'version':8,'metrics':{'studio_light_directions':[row]}}
        measured=service._current_light_directions(self.project,output)[0]
        self.assertEqual(measured['energy'],1000)
        self.assertEqual(measured['shape'],'RECTANGLE')
        self.assertNotIn('secret',measured)
        row.update(energy=float('inf'),size=-1,type='EXECUTE',shape='unknown')
        measured=service._current_light_directions(self.project,output)[0]
        for field in ('energy','size','type','shape'):self.assertNotIn(field,measured)

    def test_material_receipt_shows_actual_before_without_changing_stored_diff(self):
        before=planner.default_plan();after=copy.deepcopy(before)
        after['object_material_overrides']=[{'object_name':'Cabinet','material_name':'Original red',
                                            'preset':'matte_plastic','roughness':.22}]
        changes=service._changes(before,after);original=copy.deepcopy(changes)
        records=service._current_material_values(self.project,evidence())
        rows=service._receipt_changes(changes,before,after,measured_materials=records)
        row=next(item for item in rows if item['path']=='object_material_overrides')
        self.assertEqual(changes,original)
        self.assertEqual(row['before'],[])
        self.assertEqual(row['after'],after['object_material_overrides'])
        self.assertEqual(row['effective_before'],[{'object_name':'Cabinet','material_name':'Original red','roughness':.36}])
        self.assertNotIn('metallic',row['effective_before'][0])

    def test_material_receipt_matches_exact_pair_and_changed_unlinked_scalar_only(self):
        before=planner.default_plan()
        before['object_material_overrides']=[{'object_name':'Cabinet','material_name':'Original red',
                                             'preset':'matte_plastic','roughness':.8,'metallic':.2}]
        after=copy.deepcopy(before);after['object_material_overrides'][0]['roughness']=.22
        records=service._current_material_values(self.project,evidence())
        records.append({'object_name':'Other','material_name':'Original red','values':{'roughness':.1},
                        'linked_fields':[],'basis':'saved_scene_principled'})
        rows=service._receipt_changes(service._changes(before,after),before,after,measured_materials=records)
        self.assertEqual(rows[0]['effective_before'][0]['roughness'],.36)
        self.assertEqual(rows[0]['effective_before'][0]['metallic'],.2)
        records[0]['linked_fields']=['roughness']
        rows=service._receipt_changes(service._changes(before,after),before,after,measured_materials=records)
        self.assertNotIn('effective_before',rows[0])

    def test_shared_material_receipt_does_not_guess_one_objects_value(self):
        before=planner.default_plan();after=copy.deepcopy(before)
        after['material_overrides']=[{'name':'Original red','roughness':.22,'preset':'matte_plastic'}]
        records=service._current_material_values(self.project,evidence())
        rows=service._receipt_changes(service._changes(before,after),before,after,measured_materials=records)
        self.assertNotIn('effective_before',rows[0])


@unittest.skipUnless(shutil.which('blender'),'Blender unavailable: actual surface socket test not run')
class CurrentSurfaceBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name)
        code=r'''
import bpy,importlib.util,json,sys
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--')+1]);script=Path(sys.argv[sys.argv.index('--')+2])
spec=importlib.util.spec_from_file_location('inspector',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def material(name,roughness):
    value=bpy.data.materials.new(name);value.use_nodes=True
    node=value.node_tree.nodes.get('Principled BSDF');node.inputs['Roughness'].default_value=roughness
    node.inputs['Metallic'].default_value=.3
    return value,node
plain,node=material('Native red.001',.36)
linked,linked_node=material('Texture roughness',.88)
texture=linked.node_tree.nodes.new('ShaderNodeTexNoise')
linked.node_tree.links.new(texture.outputs['Fac'],linked_node.inputs['Roughness'])
other,_=material('Different native surface',.6)
complex_value,complex_node=material('Unused Principled',.15)
tree=complex_value.node_tree;output=tree.nodes.get('Material Output');emission=tree.nodes.new('ShaderNodeEmission')
tree.links.new(emission.outputs[0],output.inputs['Surface'])
scene=root/'actual.blend';bpy.ops.wm.save_as_mainfile(filepath=str(scene))
import hashlib
before=hashlib.sha256(scene.read_bytes()).hexdigest()
result={'plain':module._surface_measurement([plain]),'linked':module._surface_measurement([linked]),
        'conflicting':module._surface_measurement([plain,other]),
        'complex':module._surface_measurement([complex_value]),
        'missing':module._surface_measurement([None])}
result['unchanged']=before==hashlib.sha256(scene.read_bytes()).hexdigest()
(root/'measurement.json').write_text(json.dumps(result))
'''
        build=cls.root/'measure.py';build.write_text(code,encoding='utf-8')
        result=subprocess.run([shutil.which('blender'),'-b','--factory-startup','--disable-autoexec',
                               '-t','2','--python-exit-code','1','--python',str(build),'--',str(cls.root),str(SCRIPT)],
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=45)
        if result.returncode:raise AssertionError(result.stdout[-5000:])
        cls.result=json.loads((cls.root/'measurement.json').read_text())

    def test_real_native_roughness_is_measured_without_changing_scene(self):
        self.assertAlmostEqual(self.result['plain']['surface_values']['roughness'],.36,places=6)
        self.assertAlmostEqual(self.result['plain']['surface_values']['metallic'],.3,places=6)
        self.assertTrue(self.result['unchanged'])

    def test_real_linked_socket_does_not_leak_unused_default_as_current_roughness(self):
        self.assertNotIn('roughness',self.result['linked']['surface_values'])
        self.assertEqual(self.result['linked']['linked_surface_fields'],['roughness'])

    def test_real_slot_disagreement_and_unconnected_principled_remain_unknown(self):
        self.assertNotIn('roughness',self.result['conflicting']['surface_values'])
        self.assertIn('metallic',self.result['conflicting']['surface_values'])
        self.assertEqual(self.result['complex']['surface_values'],{})
        self.assertEqual(self.result['missing']['surface_values'],{})


if __name__=='__main__':unittest.main()

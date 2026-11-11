"""Real planner/service integration for photography intent recovery.

Model responses are fixtures. Images, evidence and SQLite projects are temporary;
the regression harness blocks external networking and reads of the real .env.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
import engine
import instruction_service as service
import planner
import studio_store as store


CONFIG={'enabled':True,'provider':'anthropic','base_url':'https://example.invalid',
        'api_key':'fixture-token','model':'fixture-vision'}
GOAL='改善灯光镜头凸显物体材质反光'


def response(values):
    return {'stop_reason':'tool_use','content':[{
        'type':'tool_use','name':ai_client.TOOL_NAME,
        'input':{'status':'apply','patch':copy.deepcopy(values),
                 'summary':'准备调整摄影参数。','notes':[],'unhandled':[],
                 'target_bindings':[]},
    }]}


class PhotographyFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for module in (store,engine,service):
            mocked=patch.object(module,'DATA',self.root);mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear();self.addCleanup(service._INFLIGHT.clear)
        folder=self.root/'fixture/runs/source';folder.mkdir(parents=True)
        self.png=folder/'hero.png';Image.new('RGB',(24,24),'white').save(self.png)
        (folder/'result.json').write_text(json.dumps({
            'success':True,'product_samples':[{'object_name':'Panel','material_name':'Steel','x':.4,'y':.3}],
        }),encoding='utf-8')
        self.source={'output_id':'source','shot_id':'hero','quality':'preview','version':3}
        self.project={'id':'fixture','name':'摄影调整集成样例','version':3,'status':'ready','history':[],
            'metadata':{},'plan':planner.default_plan(),
            'analysis':{'objects':[{'name':'Panel','type':'MESH','materials':['Steel']}],
                        'materials':[{'name':'Steel','suggested_preset':'brushed_metal'}]},
            'outputs':[{'job_id':'source','shot_id':'hero','quality':'preview','version':3,
                        'png_url':'/files/fixture/runs/source/hero.png',
                        'result_url':'/files/fixture/runs/source/result.json'}]}
        store.put('project',copy.deepcopy(self.project))
        for name in ('get_config','get_vision_config'):
            mocked=patch('ai_config.'+name,return_value=copy.deepcopy(CONFIG));mocked.start();self.addCleanup(mocked.stop)
        for name in ('record_result','record_vision_result'):
            mocked=patch('ai_config.'+name);mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'pick_output',side_effect=AssertionError('No Blender call is permitted'))
        mocked.start();self.addCleanup(mocked.stop)

    def apply(self,text,key='prepare',partial_mode=None):
        # Retain regression coverage for the explicitly selected legacy wire.
        return service.apply_instruction('fixture',{
            'text':text,'expected_version':3,'shot_id':'hero','source':self.source,'protocol':'legacy_v1',
            **({'partial_mode':partial_mode} if partial_mode is not None else {}),
        },key)

    def confirm(self,prepared,key='confirm'):
        return service.confirm_instruction('fixture',{
            'proposal_id':prepared['instruction_result']['proposal_id'],'expected_version':3,
        },key)

    def test_broad_reflection_goal_applies_actual_lighting_without_dummy_camera_edit(self):
        with patch.object(ai_client,'_post',return_value=response({'lighting':{'rim_power':1.3}})) as send:
            result=self.apply(GOAL)
            repeated=self.apply(GOAL)
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['instruction_result']['status'],'applied')
        self.assertEqual(result['version'],4)
        self.assertEqual(result['plan']['shots'],self.project['plan']['shots'])
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['lighting']['rim_power'],1.3)
        self.assertTrue(repeated['instruction_result']['replayed'])
        self.assertEqual(len(repeated['history']),1)
        self.assertEqual(store.all_records('job'),[])
        notes='；'.join(result['instruction_result']['notes'])
        self.assertIn('镜头',notes)
        self.assertRegex(notes,r'保持|不变|保留|沿用|未(?:做|进行)?(?:调整|修改)')

    def test_broad_goal_respects_explicit_camera_preservation(self):
        text=GOAL+'，保持镜头不变'
        with patch.object(ai_client,'_post',return_value=response({'light_softness':1.4})) as send:
            result=self.apply(text)
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['plan']['shots'],self.project['plan']['shots'])
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['light_softness'],1.4)
        self.assertIn('preserve_camera',result['instruction_result']['protections'])

    def test_exact_missing_focal_length_is_completed_against_original_effective_plan(self):
        self.project['plan']['exposure']=.1
        self.project['plan']['shot_adjustments']={'hero':{'exposure_delta':.4}}
        store.put('project',copy.deepcopy(self.project))
        text='曝光提高0.2EV，焦距设为85mm'
        with patch.object(ai_client,'_post',return_value=response({'exposure':.7})) as send:
            result=self.apply(text)
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['instruction_result']['status'],'applied')
        effective=engine.effective_plan(result['plan'],'hero')
        self.assertAlmostEqual(effective['exposure'],.7)
        self.assertEqual(next(shot for shot in effective['shots'] if shot['id']=='hero')['focal_length'],85)
        self.assertEqual(result['plan']['exposure'],.1)
        self.assertEqual([shot for shot in result['plan']['shots'] if shot['id']!='hero'],
                         [shot for shot in self.project['plan']['shots'] if shot['id']!='hero'])
        self.assertEqual(len(result['history']),1)

    def test_valid_ai_relative_step_is_preserved_with_existing_shot_adjustment(self):
        self.project['plan']['shot_adjustments']={'hero':{'softness_multiplier':1.5}}
        store.put('project',copy.deepcopy(self.project))
        text='灯光柔和一点，焦距设为85mm'
        # A 20% softening is a legitimate AI choice. The built-in grammar's
        # 40% fallback step must neither replace it nor apply to it again.
        with patch.object(ai_client,'_post',return_value=response({'light_softness':1.8})) as send:
            result=self.apply(text)
            repeated=self.apply(text)
        self.assertEqual(send.call_count,1)
        effective=engine.effective_plan(result['plan'],'hero')
        self.assertAlmostEqual(effective['light_softness'],1.8)
        self.assertEqual(next(shot for shot in effective['shots'] if shot['id']=='hero')['focal_length'],85)
        self.assertEqual(result['plan']['light_softness'],self.project['plan']['light_softness'])
        self.assertTrue(repeated['instruction_result']['replayed'])
        self.assertEqual(len(repeated['history']),1)

    def test_unresolved_camera_keeps_remaining_text_while_relative_light_applies_once(self):
        text='曝光提高0.2EV，镜头稍微拉近，颜色不变'
        bad=response({'exposure':.2})
        with patch.object(ai_client,'_post',side_effect=[bad,bad]) as send:
            applied=self.apply(text)
            receipt=applied['instruction_result']
            self.assertEqual(send.call_count,2)
            self.assertEqual(receipt['status'],'partially_applied')
            self.assertTrue(receipt['partial'])
            self.assertEqual(applied['version'],4)
            self.assertEqual(len(applied['history']),1)
            self.assertEqual(applied['history'][0]['plan'],self.project['plan'])
            self.assertTrue(receipt['changes'])
            self.assertEqual(receipt['preview_shot_ids'],['hero'])
            self.assertEqual(len(receipt['pending']),1)
            self.assertEqual(receipt['pending'][0]['quote'],'镜头稍微拉近')
            self.assertEqual(receipt['pending'][0]['action'],'clarify_parameters')
            self.assertIn('颜色不变',receipt['remaining_text'])
            self.assertNotIn('曝光提高',receipt['remaining_text'])
            self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))
            repeated=self.apply(text)
            self.assertEqual(send.call_count,2)
        self.assertEqual(applied['version'],4)
        self.assertEqual(applied['plan']['shots'],self.project['plan']['shots'])
        self.assertEqual(applied['plan']['object_material_overrides'],self.project['plan']['object_material_overrides'])
        self.assertAlmostEqual(engine.effective_plan(applied['plan'],'hero')['exposure'],.2)
        self.assertAlmostEqual(engine.effective_plan(repeated['plan'],'hero')['exposure'],.2)
        self.assertTrue(repeated['instruction_result']['replayed'])
        self.assertEqual(repeated['instruction_result']['id'],applied['instruction_result']['id'])
        self.assertEqual(len(repeated['history']),1)
        self.assertEqual(store.all_records('job'),[])

    def test_valid_material_and_lighting_survive_independent_camera_omission(self):
        text='Panel是红色金属；曝光提高0.2EV；镜头稍微拉近'
        bad=response({'exposure':.2,'object_material_overrides':[
            {'object_name':'Panel','material_name':'Steel','preset':'painted_metal','color':'#B93832'},
        ]})
        with patch.object(ai_client,'_post',side_effect=[bad,bad]) as send:
            applied=self.apply(text)
            self.assertEqual(applied['instruction_result']['status'],'partially_applied')
            self.assertTrue(applied['instruction_result']['partial'])
            self.assertEqual(applied['version'],4)
            self.assertEqual(len(applied['history']),1)
            self.assertEqual(applied['instruction_result']['remaining_text'],'镜头稍微拉近')
            self.assertEqual(len(applied['instruction_result']['pending']),1)
            self.assertEqual(applied['instruction_result']['preview_shot_ids'],['hero'])
            self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))
            repeated=self.apply(text)
            self.assertEqual(send.call_count,2)
        materials=applied['plan']['object_material_overrides']
        self.assertEqual(len(materials),1)
        self.assertEqual((materials[0]['object_name'],materials[0]['preset'],materials[0]['color']),
                         ('Panel','painted_metal','#B93832'))
        self.assertAlmostEqual(engine.effective_plan(applied['plan'],'hero')['exposure'],.2)
        self.assertEqual(applied['plan']['shots'],self.project['plan']['shots'])
        self.assertEqual(repeated['plan']['object_material_overrides'],materials)
        self.assertAlmostEqual(engine.effective_plan(repeated['plan'],'hero')['exposure'],.2)
        self.assertTrue(repeated['instruction_result']['replayed'])
        self.assertEqual(repeated['version'],4);self.assertEqual(len(repeated['history']),1)
        self.assertEqual(store.all_records('job'),[])

    def test_recovered_photography_proposal_remains_bound_to_actual_source_image(self):
        bad=response({'exposure':.2})
        with patch.object(ai_client,'_post',side_effect=[bad,bad]) as send:
            prepared=self.apply('曝光提高0.2EV，镜头稍微拉近',partial_mode='review')
            Image.new('RGB',(24,24),'gray').save(self.png)
            with self.assertRaises(service.InstructionError) as caught:
                self.confirm(prepared)
            self.assertEqual(send.call_count,2)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(caught.exception.error_code,'instruction_proposal_stale')
        self.assertEqual(store.get('project','fixture')['version'],3)
        self.assertEqual(store.get('project','fixture')['history'],[])


if __name__=='__main__':
    unittest.main()

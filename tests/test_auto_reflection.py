import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import engine
from planner import default_plan


class AutomaticReflectionTests(unittest.TestCase):
    def setUp(self):
        self.plan=default_plan()
        self.result={'studio':{'material_features':[{'name':'steel','metallic':1,'transmission':0}]}}
        self.review={'source':'ai','provider':'fixture','model':'vision-fixture','realism':{'causes':[{'aspect':'金属反射','evidence':'台面高光不明显',
            'suggestion':'增加反光层次','requires_user':False}]}}

    def test_real_metal_review_produces_bounded_lighting_only(self):
        patch=engine.automatic_reflection_patch(self.review,self.result,self.plan,'顶部拉丝金属没有反光')
        self.assertEqual(set(patch),{'lighting'})
        self.assertGreater(patch['lighting']['key_power'],self.plan['lighting']['key_power'])
        self.assertLess(patch['lighting']['fill_power'],self.plan['lighting']['fill_power'])

    def test_no_ai_no_metal_no_permission_or_capped_are_not_guessed(self):
        self.assertEqual(engine.automatic_reflection_patch({'source':'rules'},self.result,self.plan),{})
        self.assertIn('lighting',engine.automatic_reflection_patch({'source':'rules'},self.result,self.plan,'台面金属没有反光'))
        self.assertEqual(engine.automatic_reflection_patch({'source':'rules'},self.result,self.plan,'红色柜体反光不足'),{})
        self.assertEqual(engine.automatic_reflection_patch(self.review,{'studio':{'material_features':[]}},self.plan),{})
        self.assertEqual(engine.automatic_reflection_patch(self.review,self.result,self.plan,'只分析，不调整'),{})
        plan=default_plan();plan['lighting']['key_power']=3;plan['lighting']['fill_power']=.1
        self.assertEqual(engine.automatic_reflection_patch(self.review,self.result,plan),{})

    def test_empty_model_patch_still_renders_reflection_candidate(self):
        baseline={**self.review,'patch':{},'score':.5}
        improved={**self.review,'patch':{},'score':.8}
        plan=default_plan();plan['max_corrections']=1
        job={'id':'test','plan':plan,'quality':'preview'}
        project={'id':'fixture','model_path':'fixture.glb','analysis':{}}
        shot=plan['shots'][0]
        original={'shot_id':shot['id'],'png_url':'/original.png'}
        candidate={'shot_id':shot['id'],'png_url':'/candidate.png'}
        def diagnose(job,record,result,plan,analysis):
            review=baseline if record is original else improved
            record['review']=review.copy()
            return record['review']
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(engine,'get',return_value={'status':'running'}), \
             patch.object(engine,'update_job'),patch.object(engine,'inspect_preview',side_effect=diagnose), \
             patch.object(engine,'execute',return_value=self.result) as render, \
             patch.object(engine,'output_record',return_value=candidate), \
             patch.object(engine,'persist_review_candidate'),patch.object(engine,'atomic_json'):
            best,best_plan,round_number=engine.review_and_correct(job,project,shot,Path(folder)/'render',original,self.result)
        self.assertEqual(render.call_count,1)
        self.assertEqual(round_number,1)
        self.assertEqual(best['png_url'],'/candidate.png')
        self.assertIn('lighting',best_plan['shot_adjustments'][shot['id']])

    def test_instruction_light_constraints_also_block_automatic_followup_edits(self):
        review={**self.review,'patch':{'exposure':-.7,'light_softness':1.4},'score':.5,'needs_user':[]}
        plan=default_plan();plan['last_instruction']={'protected_fields':['exposure','light_softness','lighting']}
        job={'id':'test','plan':plan,'quality':'preview'}
        project={'id':'fixture','model_path':'fixture.glb','analysis':{}}
        shot=plan['shots'][0];original={'shot_id':shot['id'],'png_url':'/original.png'}
        def diagnose(job,record,result,plan,analysis):
            record['review']=review.copy();return record['review']
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(engine,'get',return_value={'status':'running'}), \
             patch.object(engine,'update_job'),patch.object(engine,'inspect_preview',side_effect=diagnose), \
             patch.object(engine,'execute') as render, \
             patch.object(engine,'persist_review_candidate'),patch.object(engine,'atomic_json'):
            best,best_plan,round_number=engine.review_and_correct(job,project,shot,Path(folder)/'render',original,self.result)
        render.assert_not_called()
        self.assertEqual(round_number,0)
        self.assertNotIn('shot_adjustments',best_plan)
        self.assertIn('保留相关参数',best['corrections'][0]['decision_reason'])


if __name__=='__main__':unittest.main()

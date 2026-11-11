import copy
import math
import unittest
from unittest.mock import patch

import ai_client
import ai_config
import engine
import material_agent
from planner import default_plan
from quality_gate import EVALUATION_PROTOCOL, compare_candidates


def output(score=.6, *, source='ai', clipped=False, fraction=.02, samples=80):
    return {'shot_id':'hero', 'checks':{'kind':'complete_product','clipped':clipped},
            'metrics':{'product':{'method':'camera-ray samples','eligible_opaque_samples':samples,
                                  'opaque_clipped_fraction':fraction}},
            'review':{'source':source,'provider':'fixture-provider','model':'fixture-vision',
                      'evaluation_protocol':EVALUATION_PROTOCOL,'score':score}}


class CandidateQualityTests(unittest.TestCase):
    def test_same_evaluator_with_clear_gain_and_no_measured_damage_is_adoptable(self):
        decision=compare_candidates(output(.6),output(.7))
        self.assertTrue(decision['accepted'])
        self.assertAlmostEqual(decision['gain'],.1)

    def test_small_score_changes_are_not_claimed_as_improvement(self):
        for score in (.599,.6,.601,.62):
            with self.subTest(score=score):
                self.assertFalse(compare_candidates(output(),output(score))['accepted'])

    def test_missing_or_different_evaluator_identity_is_not_comparable(self):
        for field,value in [('provider','other'),('model','other'),('model',None),('evaluation_protocol','next')]:
            candidate=output(.9);candidate['review'][field]=value
            with self.subTest(field=field,value=value):
                self.assertFalse(compare_candidates(output(),candidate)['comparable'])
        before=output();after=output(.9)
        before['review'].pop('model');after['review'].pop('model')
        self.assertFalse(compare_candidates(before,after)['accepted'])

    def test_invalid_and_boolean_scores_are_not_adopted(self):
        for value in (None,True,-.1,1.1,math.nan,math.inf):
            with self.subTest(score=value):
                self.assertFalse(compare_candidates(output(),output(value))['comparable'])

    def test_higher_ai_score_does_not_override_new_product_clipping(self):
        decision=compare_candidates(output(),output(.95,clipped=True))
        self.assertTrue(decision['comparable'])
        self.assertFalse(decision['accepted'])
        self.assertIn('裁切',decision['reason'])

    def test_higher_ai_score_does_not_override_increased_opaque_clipping(self):
        decision=compare_candidates(output(),output(.95,fraction=.35))
        self.assertFalse(decision['guard_passed'])
        self.assertIn('高光剪切',decision['reason'])

    def test_lost_measured_evidence_does_not_count_as_a_clean_candidate(self):
        for candidate in (output(.95,samples=5),output(.95)):
            if candidate['metrics']['product']['eligible_opaque_samples']==80:
                candidate['checks']={}
            self.assertFalse(compare_candidates(output(),candidate)['accepted'])

    def test_detail_framing_and_insufficient_baseline_sampling_are_not_misused(self):
        before=output(samples=2);after=output(.8,samples=2,clipped=True)
        before['checks']['kind']='detail';after['checks']['kind']='detail'
        self.assertTrue(compare_candidates(before,after)['accepted'])
        self.assertFalse(compare_candidates(output(source='rules',samples=2),output(.8,source='rules',samples=2))['accepted'])

    def test_rules_require_actual_matching_sampling_method(self):
        before=output(.5,source='rules',fraction=.5)
        after=output(.9,source='rules',fraction=.1)
        self.assertTrue(compare_candidates(before,after)['accepted'])
        after['metrics']['product']['method']='different sample protocol'
        self.assertFalse(compare_candidates(before,after)['comparable'])

    def test_material_trials_use_the_same_measurement_guard(self):
        before=[output()];after=[output(.95,clipped=True)]
        result=material_agent._compare_reviews(before,after)
        self.assertIsNotNone(result)
        self.assertFalse(result['guard_passed'])
        self.assertIn('裁切',result['reason'])
        self.assertIsNone(material_agent._compare_reviews(before,before+after))
        self.assertIsNone(material_agent._compare_reviews(before*2,after*2))


class MeasuredEvidenceTests(unittest.TestCase):
    def test_visual_model_cannot_erase_measured_exposure_or_framing_warning(self):
        measured={'method':'camera-ray product samples, background excluded','product_samples':80,
                  'eligible_opaque_samples':80,'opaque_clipped_fraction':.4,'product_mean':240}
        optimistic={'score':.99,'patch':{},'issues':[],'needs_user':[],
                    'realism':{'summary':'视觉模型认为画面良好。','causes':[],'priority_actions':[]}}
        record={'png_url':'/files/fixture/mock.png'}
        result={'checks':{'kind':'complete_product','clipped':True}}
        config={'enabled':True,'api_key':'test-only','provider':'fixture-provider','model':'fixture-vision'}
        with patch.object(engine,'product_metrics',return_value=copy.deepcopy(measured)), \
             patch.object(ai_config,'get_vision_config',return_value=config), \
             patch.object(ai_config,'record_vision_result'), \
             patch.object(ai_client,'review_preview',return_value=copy.deepcopy(optimistic)):
            review=engine.diagnose_render(record,result,default_plan(),{})
        self.assertEqual(review['source'],'ai')
        self.assertEqual(review['evaluation_protocol'],EVALUATION_PROTOCOL)
        self.assertEqual({issue['code'] for issue in review['measured_issues']},
                         {'opaque_highlight_clipping','projected_product_clipping'})
        self.assertEqual(len(review['issues']),2)
        self.assertEqual(review['status'],'manual_review')
        self.assertTrue(review['realism']['summary'].startswith('测量发现：'))
        self.assertEqual(review['measurement_evidence']['eligible_opaque_samples'],80)
        self.assertEqual(record['metrics']['product']['opaque_clipped_fraction'],.4)


if __name__=='__main__':
    unittest.main()

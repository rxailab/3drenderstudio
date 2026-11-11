"""Grounded studio planning is a two-call workflow, not an open-ended agent."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import hero_ai
import hero_recipe
import target_evidence


CONFIG={'enabled':True,'provider':'anthropic','base_url':'https://example.invalid',
        'api_key':'fixture-token','model':'fixture-vision'}
CATALOG={'product_object_names':['Door','Top','Logo'],'bounds':{'min':[-1,-.5,0],'max':[1,.5,2]},
         'camera':{'azimuth':35,'elevation':18,'focal_length':70,'occupancy':.72},
         'ground':{'name':'Studio floor','editable':True},'materials':[
             {'id':'m1','name':'Paint','object_names':['Door'],'roughness':.35,'metallic':.6,
              'editable':['roughness','metallic','specular','coat_weight','coat_roughness'],'protected':False},
             {'id':'m2','name':'Steel','object_names':['Top'],'roughness':.25,'metallic':1,
              'editable':['roughness','anisotropic'],'protected':False},
             {'id':'m3','name':'Brand','object_names':['Logo'],'editable':['roughness'],'protected':True}]}


def wire(value,tool='submit_hero_recipe'):
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':tool,'input':value}]}


def planned(recipe=None):
    return {'summary':'调整柜门光影、台面反射和背景，保留原有配色。',
            'diagnosis':[{'area':'正面门板','evidence':'大面积颜色过于均匀。','action':'调整前方柔光和主补光比例。'}],
            'recipe':recipe or hero_recipe.default_recipe(CATALOG)}


class HeroAIRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.before=Path(self.tmp.name)/'before.png';self.after=Path(self.tmp.name)/'after.png'
        Image.new('RGB',(16,16),(180,40,40)).save(self.before)
        Image.new('RGB',(16,16),(190,45,45)).save(self.after)

    def test_scene_grounding_and_independent_uneditable_surfaces(self):
        recipe=hero_recipe.default_recipe(CATALOG)
        recipe['surfaces']=[{'id':'m1','roughness':.36,'metallic':0,'reason':'依据提供的涂层说明调整反射。'},
                            {'id':'m2','roughness':.28,'metallic':.7,'reason':'保留连接驱动的金属度。'},
                            {'id':'m3','roughness':.4,'reason':'不能修改品牌。'},
                            {'id':'invented','roughness':.4,'reason':'未知目标应独立跳过。'}]
        original=copy.deepcopy(CATALOG)
        with patch('ai_client._post',return_value=wire(planned(recipe))) as send:
            result=hero_ai.plan_hero(CATALOG,self.before,'自动优化主图',CONFIG)
        self.assertEqual(send.call_count,1);self.assertEqual(result['llm_calls'],1)
        self.assertEqual(result['source'],'ai');self.assertTrue(result['recipe']['lighting'])
        self.assertEqual([row['id'] for row in result['recipe']['surfaces']],['m1','m2'])
        self.assertNotIn('metallic',result['recipe']['surfaces'][1]);self.assertEqual(len(result['skipped']),3)
        self.assertEqual(CATALOG,original)
        context=json.loads(send.call_args.args[1]['messages'][0]['content'][-1]['text'])
        self.assertEqual(context['actual_scene']['camera']['focal_length'],70)
        self.assertIn('immutable',send.call_args.args[1]['system'])

    def test_invalid_or_executable_or_color_data_never_reaches_recipe(self):
        for bad in ({'script':'import os'}, {'surfaces':[{'id':'m1','color':'#ffffff'}]},
                    {'camera':{'occupancy':True}}, {'lighting':{'key':{'power':float('inf')}}},
                    {'camera':{'elevation_delta':-80}}, {'version':True}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):hero_recipe.validate_recipe(bad,CATALOG)
        reply=planned();reply['recipe']['surfaces']=[{'id':'m1','color':'#fff000'}]
        with patch('ai_client._post',return_value=wire(reply)) as send:
            result=hero_ai.plan_hero(CATALOG,self.before,'优化',CONFIG)
        self.assertEqual(send.call_count,1);self.assertEqual(result['source'],'preset')
        self.assertEqual(result['recipe']['surfaces'],[]);self.assertTrue(result['skipped'])

    def test_disabled_or_blocked_models_cannot_be_called_or_upgraded(self):
        for config in ({**CONFIG,'enabled':False},{**CONFIG,'model':'gpt-5.5-2026-04-23'},
                       {**CONFIG,'model':'deepseek-v4-pro'},{**CONFIG,'api_key':''}):
            with self.subTest(model=config['model']),patch('ai_client._post') as send:
                plan=hero_ai.plan_hero(CATALOG,self.before,'优化',config)
                comparison=hero_ai.compare_hero(self.before,self.after,CATALOG,plan['recipe'],{},config)
            send.assert_not_called();self.assertEqual(plan['llm_calls']+comparison['llm_calls'],0)
            self.assertEqual(comparison['decision'],'uncertain')

    def test_plan_and_pairwise_review_use_only_two_calls_with_honest_image_labels(self):
        compare={'decision':'candidate','confidence':.82,'reason':'门板层次更清楚，脚轮完整且保留接地阴影。'}
        with patch('ai_client._post',side_effect=[wire(planned()),wire(compare,'compare_hero_renders')]) as send:
            plan=hero_ai.plan_hero(CATALOG,self.before,'优化主图',CONFIG)
            result=hero_ai.compare_hero(self.before,self.after,CATALOG,plan['recipe'],{'guards_passed':True},CONFIG)
        self.assertEqual(send.call_count,2);self.assertEqual(plan['llm_calls']+result['llm_calls'],2)
        self.assertEqual(result['decision'],'candidate')
        content=send.call_args_list[1].args[1]['messages'][0]['content']
        self.assertEqual([row['type'] for row in content],['text','image','text','image','text'])
        self.assertTrue(content[0]['text'].startswith('BASELINE:'))
        self.assertTrue(content[2]['text'].startswith('CANDIDATE:'))
        self.assertNotIn('REFERENCE_IMAGE',json.dumps(content))

    def test_bad_comparison_stops_without_extra_call_or_acceptance(self):
        for result in ({'decision':'candidate','confidence':True,'reason':'好'},
                       {'decision':'candidate','confidence':.99,'reason':'好','execute':'x'},
                       {'decision':'candidate','confidence':float('nan'),'reason':'好'},
                       {'decision':'unknown','confidence':.9,'reason':'好'}):
            with self.subTest(result=result),patch('ai_client._post',return_value=wire(result,'compare_hero_renders')) as send:
                answer=hero_ai.compare_hero(self.before,self.after,CATALOG,hero_recipe.default_recipe(CATALOG),{},CONFIG)
            self.assertEqual(send.call_count,1);self.assertEqual(answer['decision'],'uncertain')
            self.assertEqual(answer['llm_calls'],1)

    def test_provider_failure_falls_back_once_without_secret_in_metadata(self):
        with patch('ai_client._post',side_effect=ValueError('服务暂不可用')) as send:
            plan=hero_ai.plan_hero(CATALOG,self.before,'优化',CONFIG)
            comparison=hero_ai.compare_hero(self.before,self.after,CATALOG,plan['recipe'],{},CONFIG)
        self.assertEqual(send.call_count,2);self.assertEqual(plan['source'],'preset')
        self.assertEqual(comparison['decision'],'uncertain')
        self.assertNotIn('fixture-token',json.dumps([plan,comparison]))

    def test_protections_remove_domains_before_any_native_execution(self):
        recipe=hero_recipe.default_recipe(CATALOG);recipe['surfaces']=[{'id':'m1','roughness':.4}]
        protected,notes=hero_recipe.normalize_recipe(recipe,CATALOG,['preserve_camera','preserve_light_direction','preserve_materials'])
        self.assertEqual(protected['camera'],{});self.assertEqual(protected['lighting'],{})
        self.assertEqual(protected['surfaces'],[]);self.assertTrue(protected['background'])
        self.assertGreaterEqual(len(notes),3)
        only_light=hero_recipe.validate_recipe(recipe,CATALOG,['only_lighting'])
        self.assertTrue(only_light['lighting']);self.assertEqual(only_light['camera'],{})
        self.assertEqual(only_light['background'],{});self.assertEqual(only_light['surfaces'],[])

    def test_unknown_floor_keeps_shadow_controls_out_and_retains_other_work(self):
        catalog={**CATALOG,'ground':{'name':None,'editable':False}}
        recipe,notes=hero_recipe.normalize_recipe(hero_recipe.default_recipe(CATALOG),catalog)
        self.assertEqual(recipe['background'],{'clean_white':True})
        self.assertTrue(recipe['lighting']);self.assertTrue(notes)
        self.assertEqual(recipe['surfaces'],[])

    def test_duplicate_surface_or_out_of_bounds_values_do_not_double_apply(self):
        for surfaces in ([{'id':'m1','roughness':.3},{'id':'m1','roughness':.6}],
                         [{'id':'m1','coat_weight':.9}]):
            with self.subTest(surfaces=surfaces),self.assertRaises(ValueError):
                hero_recipe.validate_recipe({'surfaces':surfaces},CATALOG)

    def test_native_hero_source_remains_eligible_for_followup_target_evidence(self):
        snapshot={'output_id':'job','shot_id':'hero','version':3,'quality':'hero'}
        self.assertTrue(target_evidence._snapshot(snapshot))
        self.assertFalse(target_evidence._snapshot({**snapshot,'quality':'invented'}))


if __name__=='__main__':unittest.main()

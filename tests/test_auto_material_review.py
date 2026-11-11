import copy
import json
import unittest
from unittest.mock import patch

import ai_client
from planner import default_plan

CONFIG={'enabled':True,'provider':'anthropic','model':'fixture-model','api_key':'test-only'}
ANALYSIS={'objects':[
    {'name':'Bag body','type':'MESH','materials':['Woven canvas'],'uv_layers':['UVMap']},
    {'name':'Rail','type':'MESH','materials':['亮光金属_1'],'uv_layers':['UVMap']},
    {'name':'Unseen','type':'MESH','materials':['Woven canvas'],'uv_layers':['UVMap']},
    {'name':'Unknown','type':'MESH','materials':['Material.001'],'uv_layers':[]}],
    'materials':[{'name':name,'suggested_preset':'matte_plastic','suggestion_confidence':.55}
                 for name in ['Woven canvas','亮光金属_1','Material.001']]}


def decision(query='fine cotton fabric', targets=None):
    return {'query':query,'targets':targets or [{'object_name':'Bag body','material_name':'Woven canvas'}],
            'reason':'包身缺少细密织物高光，原材质名称明确是帆布，补充同类微表面法线。','confidence':.85}


class AutomaticMaterialReviewTests(unittest.TestCase):
    def setUp(self):
        self.plan=default_plan()
        self.metrics={'visible_material_targets':[
            {'object_name':'Bag body','material_name':'Woven canvas'},
            {'object_name':'Rail','material_name':'亮光金属_1'},
            {'object_name':'Unknown','material_name':'Material.001'}]}
        self.base={'issues':[],'patch':{},'score':.7,'needs_user':[],
                   'realism':{'summary':'真实图中织物细节较平。','causes':[],'priority_actions':[]}}

    def review(self, requests=None, omitted=False, plan=None, metrics=None):
        output=copy.deepcopy(self.base)
        if not omitted: output['material_requests']=requests if requests is not None else [decision()]
        response={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':output}]}
        with patch.object(ai_client,'_post',return_value=response) as sender:
            result=ai_client.review_preview(plan or self.plan,ANALYSIS,None,self.metrics if metrics is None else metrics,CONFIG)
        return result,sender

    def test_legacy_review_omission_stays_compatible_and_non_executable(self):
        result,_=self.review(omitted=True,metrics={})
        self.assertEqual(result,self.base)
        self.assertEqual(result.get('material_requests',[]),[])

    def test_new_actionable_review_requires_explicit_material_decision_and_retries(self):
        corrected={**self.base,'material_requests':[]}
        def response(value):
            return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':value}]}
        with patch.object(ai_client,'_post',side_effect=[response(self.base),response(corrected)]) as sender:
            result=ai_client.review_preview(self.plan,ANALYSIS,None,self.metrics,CONFIG)
        self.assertEqual(result['material_requests'],[])
        self.assertEqual(sender.call_count,2)
        self.assertIn('material_requests',sender.call_args[0][1]['tools'][0]['input_schema']['required'])
        correction=json.loads(sender.call_args[0][1]['messages'][-1]['content'][-1]['text'])
        self.assertEqual(correction['structured_correction']['validation_error']['missing_fields'],['material_requests'])

    def test_request_confidence_remains_calibrated_for_reversible_trial(self):
        request={**decision(),'confidence':.5}
        result,sender=self.review([request])
        self.assertEqual(result['material_requests'][0]['confidence'],.5)
        self.assertIn('never inflate it',sender.call_args[0][1]['system'])

    def test_visible_same_family_request_survives_without_parameter_changes(self):
        original=copy.deepcopy(self.plan)
        result,sender=self.review()
        self.assertEqual(result['material_requests'],[decision()])
        self.assertEqual(result['patch'],{})
        self.assertEqual(self.plan,original)
        body=sender.call_args[0][1]
        context=json.loads(body['messages'][0]['content'][0]['text'])
        targets=context['automatic_materials']['eligible_targets']
        self.assertEqual({t['family'] for t in targets},{'fabric','metal'})
        self.assertFalse(context['automatic_materials']['use_base_color'])
        self.assertIn('Do not ask the user to find a resource',body['system'])

    def test_actual_metal_name_overrides_importer_plastic_guess(self):
        request=decision('subtle smooth metal', [{'object_name':'Rail','material_name':'亮光金属_1'}])
        result,_=self.review([request])
        self.assertEqual(result['material_requests'][0]['query'],'subtle smooth metal')

    def test_cross_object_material_pair_cannot_be_composed_from_known_names(self):
        wrong=decision(targets=[{'object_name':'Rail','material_name':'Woven canvas'}])
        with self.assertRaisesRegex(ValueError,'目标'):
            self.review([wrong])

    def test_hidden_or_unknown_targets_are_not_automated(self):
        for target in [ {'object_name':'Unseen','material_name':'Woven canvas'},
                        {'object_name':'Unknown','material_name':'Material.001'},
                        {'object_name':'Made up','material_name':'Woven canvas'}]:
            with self.subTest(target=target),self.assertRaises(ValueError):self.review([decision(targets=[target])])

    def test_same_pair_cannot_be_requested_twice(self):
        with self.assertRaisesRegex(ValueError,'重复'):self.review([decision(),decision()])
        request=decision();request['targets']*=2
        with self.assertRaisesRegex(ValueError,'重复'):self.review([request])

    def test_query_cannot_change_family_or_contain_urls_or_code(self):
        for query in ['polished metal','cotton fabric metal','https://example.com/fabric.png','fabric; exec("x")','cloth /etc/passwd']:
            with self.subTest(query=query),self.assertRaises(ValueError):self.review([decision(query)])

    def test_explicit_current_preset_has_precedence_over_original_name(self):
        self.plan['object_material_overrides']=[{'object_name':'Bag body','material_name':'Woven canvas',
            'preset':'leather','preserve_original':False,'source':'user'}]
        with self.assertRaisesRegex(ValueError,'材质类型'):self.review([decision()])
        result,_=self.review([decision('fine leather')])
        self.assertEqual(result['material_requests'][0]['query'],'fine leather')

    def test_existing_external_resources_are_not_replaced_automatically(self):
        for global_override in [False,True]:
            plan=copy.deepcopy(self.plan)
            if global_override:
                plan['material_overrides']=[{'name':'Woven canvas','textures':{'normal':'owned.png'}}]
            else:
                plan['object_material_overrides']=[{'object_name':'Bag body','material_name':'Woven canvas',
                    'resource':{'provider':'Poly Haven','asset_id':'stretch_poplin'}}]
            with self.subTest(global_override=global_override),self.assertRaises(ValueError):self.review(plan=plan)

    def test_legacy_images_without_measured_visible_pairs_cannot_request_resources(self):
        result,sender=self.review([],metrics={})
        schema=sender.call_args[0][1]['tools'][0]['input_schema']
        self.assertEqual(schema['properties']['material_requests']['maxItems'],0)
        for prop in schema['properties']['material_requests']['items']['properties']['targets']['items']['properties'].values():
            self.assertNotIn('enum',prop)
        self.assertEqual(result['material_requests'],[])
        with self.assertRaises(ValueError):self.review(metrics={})

    def test_auto_fields_are_strict_and_bounded(self):
        for change in [ {'download_url':'https://example.com'}, {'color':'#ff0000'},
                        {'query':'fabric '*40},{'confidence':float('nan')},{'confidence':True}]:
            request={**decision(),**change}
            with self.subTest(change=change),self.assertRaises(ValueError):self.review([request])
        with self.assertRaises(ValueError):self.review([decision(),decision(),decision()])

    def test_actual_shader_readings_are_distinguished_from_import_guesses(self):
        self.metrics['visible_material_targets'][0]['material_info']={
            'source':'rendered_scene','editable':True,'roughness':.5,'metallic':0,
            'linked':{'roughness':True,'metallic':False,'normal':False},
            'textures':[{'name':'roughness input','channel':'roughness'}]}
        _,sender=self.review()
        body=sender.call_args[0][1]
        context=json.loads(body['messages'][0]['content'][0]['text'])
        self.assertIn('fallback defaults',context['material_parameter_semantics']['limitations'])
        self.assertTrue(context['automatic_materials']['eligible_targets'][0]['rendered_material_info']['linked']['roughness'])
        self.assertIn('wrong unlinked metallic/roughness scalar',body['system'])

    def test_actual_complex_shader_is_excluded_without_requesting_preset_replacement(self):
        self.metrics['visible_material_targets'][0]['material_info']={'source':'rendered_scene','editable':False}
        with self.assertRaises(ValueError):self.review()
        result,sender=self.review([])
        context=json.loads(sender.call_args[0][1]['messages'][0]['content'][0]['text'])
        self.assertTrue(any('Principled' in t['reason'] for t in context['automatic_materials']['excluded_targets']))


if __name__=='__main__':unittest.main()

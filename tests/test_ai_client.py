"""Protocol and plan-safety checks; network is mocked, never uses a real token."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import ai_client
import ai_config
from planner import default_plan, instruct, validate

CONFIG={'provider':'anthropic','base_url':'https://api.hai.network/anthropic','api_key':'test-secret-do-not-log','model':'claude-sonnet-4-6','enabled':True}
ANALYSIS={'features':{'surfaces':['plastic']},'objects':[{'name':'Bottle','type':'MESH','materials':['Plastic']},{'name':'Cap','type':'MESH','materials':['Plastic']}],
          'materials':[{'name':'Plastic','roughness':.4,'metallic':0,'transmission':0,'suggested_preset':'matte_plastic'}],'animations':[]}


class Response(io.BytesIO):
    pass


def response(data):
    return Response(json.dumps(data).encode())


def tool_response(patch_value):
    return {'content':[{'type':'tool_use','name':ai_client.TOOL_NAME,'input':{'patch':patch_value}}],'stop_reason':'tool_use'}


class AIClientTests(unittest.TestCase):
    def setUp(self):
        self.plan=default_plan()

    def test_native_anthropic_protocol_and_once_only_rules(self):
        with patch('ai_client.urllib.request.build_opener') as opener, patch('ai_config.get_config',return_value=CONFIG), patch('ai_config.record_result') as record:
            opener.return_value.open.return_value=response(tool_response({'margin':.17,'light_softness':1.4}))
            result=instruct(self.plan,'增加留白，反光柔和一点',ANALYSIS)
            req=opener.return_value.open.call_args[0][0]
            self.assertEqual(req.full_url,'https://api.hai.network/anthropic/v1/messages')
            headers={k.lower():v for k,v in req.header_items()}
            self.assertEqual(headers['x-api-key'],CONFIG['api_key'])
            self.assertEqual(headers['anthropic-version'],'2023-06-01')
            self.assertNotIn('authorization',headers)
            body=json.loads(req.data)
            context=json.loads(body['messages'][0]['content'][0]['text'])
            self.assertEqual(context['current_plan']['margin'],.12)
            self.assertEqual(context['current_plan']['light_softness'],1)
            self.assertEqual(body['tool_choice']['name'],ai_client.TOOL_NAME)
            self.assertEqual(result['margin'],.17)
            self.assertEqual(result['source'],'ai')
            self.assertTrue(result['last_instruction']['changes'])
            self.assertEqual(self.plan['margin'],.12)
            self.assertNotIn(CONFIG['api_key'],json.dumps(result))
            record.assert_called_once()

    def test_openai_compatibility(self):
        config={**CONFIG,'provider':'openai','base_url':'https://example.test/openai/v1'}
        with patch('ai_client.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'patch':{'exposure':.2}})}}]})
            result=ai_client.request_plan(self.plan,'提亮',ANALYSIS,config)
            req=opener.return_value.open.call_args[0][0]
            self.assertEqual(req.full_url,'https://example.test/openai/v1/chat/completions')
            self.assertIn('Authorization',dict(req.header_items()))
            self.assertNotIn('X-api-key',dict(req.header_items()))
            self.assertEqual(result['exposure'],.2)

    def test_refuse_unsafe_patch_and_fake_references(self):
        for value in [{'margin':3},{'exposure':float('nan')},{'source':'user'},{'shots':[{'id':'missing','elevation':30}]},
                      {'shots':[{'id':'hero','style':'dark'}]},{'material_overrides':[{'name':'Plastic','textures':{'normal':'C:/secret'}}]},
                      {'pose':{'object':'invented','rotation':[0,90,0]}},{'material_overrides':[{'name':'invented','roughness':.2}]}]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                ai_client.apply_patch(self.plan,value,ANALYSIS)

    def test_preserve_metadata_and_shader_maps(self):
        plan=copy.deepcopy(self.plan)
        plan['pending']=['keep'];plan['shot_adjustments']={'hero':{'exposure_delta':.1}}
        plan['material_overrides']=[{'name':'Plastic','preset':'matte_plastic','textures':{'base_color':'local-owned.png'},'license':'CC0'}]
        result=ai_client.apply_patch(plan,{'material_overrides':[{'name':'Plastic','roughness':.6}]},ANALYSIS)
        self.assertEqual(result['shot_adjustments'],plan['shot_adjustments'])
        self.assertEqual(result['pending'],['keep'])
        self.assertEqual(result['material_overrides'][0]['textures'],{'base_color':'local-owned.png'})
        self.assertNotIn('color',result['material_overrides'][0])
        new=ai_client.apply_patch(self.plan,{'material_overrides':[{'name':'Plastic','roughness':.6}]},ANALYSIS)['material_overrides'][0]
        self.assertTrue(new['preserve_original'])
        self.assertNotIn('metallic',new)
        self.assertNotIn('transmission',new)

    def test_object_material_isolation_and_existing_local_color(self):
        plan=copy.deepcopy(self.plan)
        plan['object_material_overrides']=[{'object_name':'Cap','material_name':'Plastic','color':'#FF0000','preserve_original':True}]
        result=ai_client.apply_patch(plan,{'object_material_overrides':[{'object_name':'Cap','material_name':'Plastic','color':'#FFFFFF'}]},ANALYSIS)
        self.assertEqual(result['object_material_overrides'][0]['color'],'#FFFFFF')
        self.assertEqual(result['material_overrides'],[])
        self.assertEqual(plan['object_material_overrides'][0]['color'],'#FF0000')
        self.assertTrue(ai_client.parameter_diff(plan,result))
        with self.assertRaises(ValueError):
            ai_client.apply_patch(plan,{'object_material_overrides':[{'object_name':'Missing','material_name':'Plastic','roughness':.3}]},ANALYSIS)

    def test_local_downloaded_maps_follow_explicit_parameter_edits(self):
        plan=copy.deepcopy(self.plan)
        plan['object_material_overrides']=[{'object_name':'Cap','material_name':'Plastic','preserve_original':True,
            'textures':{'roughness':'rough.png','normal':'normal.png','base_color':'label.png'},'license':'CC0'}]
        result=ai_client.apply_patch(plan,{'object_material_overrides':[{'object_name':'Cap','material_name':'Plastic','roughness':.7}]},ANALYSIS)
        self.assertEqual(result['object_material_overrides'][0]['textures'],{'normal':'normal.png','base_color':'label.png'})
        result=ai_client.apply_patch(plan,{'object_material_overrides':[{'object_name':'Cap','material_name':'Plastic','preset':'ceramic'}]},ANALYSIS)
        self.assertNotIn('textures',result['object_material_overrides'][0])
        self.assertNotIn('license',result['object_material_overrides'][0])
        self.assertIn('roughness',plan['object_material_overrides'][0]['textures'])

    def test_slotless_legacy_analysis_uses_renderer_default_material(self):
        for other_material in [[],ANALYSIS['materials']]:
            analysis={'objects':[{'name':'Slotless','type':'MESH','materials':[]}],'materials':other_material}
            change={'object_material_overrides':[{'object_name':'Slotless','material_name':'Default material','color':'#335577'}]}
            with patch('ai_client._post',return_value=tool_response(change)) as send:
                result=ai_client.request_plan(self.plan,'所选部件蓝色',analysis,CONFIG,selections=[{'object_name':'Slotless','material_name':'Default material'}])
                self.assertEqual(result['object_material_overrides'][0]['material_name'],'Default material')
                payload=send.call_args[0][1]
                context=json.loads(payload['messages'][0]['content'][0]['text'])
                self.assertEqual(context['objects'][0]['materials'],['Default material'])
            with self.assertRaises(ValueError):
                ai_client.apply_patch(self.plan,{'object_material_overrides':[{'object_name':'Slotless','material_name':'Invented','color':'#335577'}]},analysis)

    def test_selection_restricts_actual_pair_and_no_global_material_edit(self):
        selection=[{'id':'point1','x':.5,'y':.5,'object_name':'Cap','material_name':'Plastic'}]
        for value in [{'material_overrides':[{'name':'Plastic','color':'#FF0000'}]},
                      {'object_material_overrides':[{'object_name':'Bottle','material_name':'Plastic','color':'#FF0000'}]}]:
            with self.subTest(value=value),patch('ai_client._post',return_value=tool_response(value)),self.assertRaises(ValueError):
                ai_client.request_plan(self.plan,'红色',ANALYSIS,CONFIG,selections=selection)
        with patch('ai_client._post',return_value=tool_response({'object_material_overrides':[{'object_name':'Cap','material_name':'Plastic','color':'#FF0000'}]})):
            result=ai_client.request_plan(self.plan,'红色',ANALYSIS,CONFIG,selections=selection)
            self.assertEqual(result['object_material_overrides'][0]['object_name'],'Cap')

    def test_missing_key_falls_back_to_rules_without_network(self):
        with patch('ai_config.get_config',return_value={**CONFIG,'api_key':''}),patch('ai_client._post') as send:
            result=instruct(self.plan,'增加留白',ANALYSIS)
            self.assertEqual(result['source'],'rules');send.assert_not_called()

    def test_error_messages_never_echo_response_or_credentials(self):
        for code in [400,401,403,404,429,500]:
            with self.subTest(code=code),patch('ai_client.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=HTTPError('https://example.test',code,'upstream echoed '+CONFIG['api_key'],{},io.BytesIO(CONFIG['api_key'].encode()))
                with self.assertRaises(ValueError) as failure: ai_client.test_connection(CONFIG)
                self.assertNotIn(CONFIG['api_key'],str(failure.exception))
                self.assertIn(str(code),str(failure.exception))
        for error in [TimeoutError(),URLError(CONFIG['api_key'])]:
            with patch('ai_client.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaises(ValueError) as failure: ai_client.test_connection(CONFIG)
                self.assertNotIn(CONFIG['api_key'],str(failure.exception))

    def test_redirect_never_forwards_key(self):
        with self.assertRaises(ValueError):
            ai_client.NoRedirect().redirect_request(None,None,302,'',{},'https://attacker.test')

    def test_reject_truncated_refused_and_wrong_tool(self):
        cases=[{'stop_reason':'max_tokens','content':[]},{'stop_reason':'refusal','content':[]},
               {'stop_reason':'tool_use','content':[{'type':'tool_use','name':'run_shell','input':{}}]}]
        for data in cases:
            with self.subTest(data=data),patch('ai_client._post',return_value=data),self.assertRaises(ValueError):
                ai_client.request_plan(self.plan,'调整',ANALYSIS,CONFIG)

    def test_reject_invalid_and_oversize_json(self):
        for raw in [b'not json',b'{"a":1,"a":2}',b'{"a":NaN}',b'x'*(ai_client.MAX_RESPONSE+1)]:
            with patch('ai_client.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value=Response(raw)
                with self.assertRaises(ValueError): ai_client.test_connection(CONFIG)

    def test_connection_test_does_not_send_assets_or_store_response(self):
        with patch('ai_client.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response({'content':[{'type':'text','text':'OK '+CONFIG['api_key']}],'stop_reason':'end_turn','usage':{'input_tokens':8,'output_tokens':1}})
            result=ai_client.test_connection(CONFIG)
            self.assertNotIn(CONFIG['api_key'],json.dumps(result))
            payload=json.loads(opener.return_value.open.call_args[0][0].data)
            self.assertEqual(payload['max_tokens'],64)
            self.assertNotIn('features',json.dumps(payload))

    def test_multimodal_thumbnail_and_visual_review_limits(self):
        from PIL import Image
        import base64
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'render.png';Image.new('RGB',(2048,1024),'white').save(path)
            review={'issues':[],'patch':{'exposure':-.2},'score':.9,'needs_user':['颜色无参考，需要人工确认'],
                    'realism':{'summary':'高光略亮，颜色仍需参考。','causes':[],'priority_actions':['比较降低曝光后的细节。']}}
            data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':review}]}
            with patch('ai_client._post',return_value=data) as send:
                result=ai_client.review_preview(self.plan,ANALYSIS,path,{'style':'white'},CONFIG)
                self.assertEqual(result['patch'],{'exposure':-.2})
                image=send.call_args[0][1]['messages'][0]['content'][0]
                self.assertEqual(image['source']['media_type'],'image/jpeg')
                with Image.open(io.BytesIO(base64.b64decode(image['source']['data']))) as im:
                    self.assertEqual(im.size,(1024,512))
            review['patch']={'material_overrides':[{'name':'Plastic','color':'#FFFFFF'}]}
            with patch('ai_client._post',return_value=data),self.assertRaises(ValueError):
                ai_client.review_preview(self.plan,ANALYSIS,path,{},CONFIG)

    def test_failed_ai_does_not_mutate_plan_or_fall_back_silently(self):
        with patch('ai_config.get_config',return_value=CONFIG),patch('ai_config.record_result') as record,patch('ai_client.request_plan',side_effect=ValueError('AI 请求失败')):
            with self.assertRaises(ValueError): instruct(self.plan,'增加留白',ANALYSIS)
            self.assertEqual(self.plan['margin'],.12)
            self.assertFalse(record.call_args[0][1])

    def review_result(self,review):
        data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':review}]}
        with patch('ai_client._post',return_value=data):
            return ai_client.review_preview(self.plan,ANALYSIS,None,{},CONFIG)

    def review_fixture(self):
        return {'issues':[{'code':'flat_fill','severity':'warning','message':'侧面光照偏平。','confidence':.6,
                           'object_name':'Bottle','material_name':'Plastic'}],
                'patch':{'exposure':-.2},'score':.6,'needs_user':[],
                'realism':{'summary':'侧面光照偏平，需参考材质目标。',
                           'causes':[{'aspect':'光照','evidence':'侧面与正面亮度接近。','confidence':.6,
                                      'suggestion':'降低补光。','requires_user':False}],
                           'priority_actions':['先调整曝光并比较预览。']}}

    def test_review_optional_null_identities_are_absent_without_mutating_response(self):
        review=self.review_fixture()
        review['issues'][0].update(object_name=None,material_name=None)
        before=copy.deepcopy(review)
        result=self.review_result(review)
        self.assertNotIn('object_name',result['issues'][0])
        self.assertNotIn('material_name',result['issues'][0])
        self.assertEqual(result['patch'],{'exposure':-.2})
        self.assertEqual(review,before)
        # Strings still need genuine object/material identity; never manufacture one.
        review['issues'][0].update(object_name='Invented',material_name='Invented')
        result=self.review_result(review)
        self.assertNotIn('object_name',result['issues'][0])
        self.assertNotIn('material_name',result['issues'][0])

    def test_review_type_errors_have_safe_field_paths_and_never_coerce_parameters(self):
        cases=[(('score',),'0.6','$.score','number','string'),
               (('score',),True,'$.score','number','boolean'),
               (('patch','exposure'),None,'$.patch.exposure','number','null'),
               (('patch','exposure'),'0','$.patch.exposure','number','string'),
               (('issues',0,'confidence'),None,'$.issues[0].confidence','number','null'),
               (('issues',0,'object_name'),42,'$.issues[0].object_name','string','number'),
               (('issues',0,'material_name'),False,'$.issues[0].material_name','string','boolean'),
               (('realism','causes',0,'requires_user'),CONFIG['api_key'],'$.realism.causes[0].requires_user','boolean','string'),
               (('realism','causes',0,'confidence'),None,'$.realism.causes[0].confidence','number','null'),
               (('needs_user',),None,'$.needs_user','array','null'),
               (('issues',0),None,'$.issues[0]','object','null')]
        for keys,value,path,expected,actual in cases:
            with self.subTest(path=path,value_type=actual):
                review=self.review_fixture(); target=review
                for key in keys[:-1]: target=target[key]
                target[keys[-1]]=value
                with self.assertRaises(ValueError) as failure: self.review_result(review)
                message=str(failure.exception)
                self.assertIn(path,message)
                self.assertIn('应为 '+expected,message)
                self.assertIn('收到 '+actual,message)
                self.assertNotIn(CONFIG['api_key'],message)

    def test_review_null_compatibility_keeps_other_safety_checks(self):
        for patch_value in [{'exposure':4.1},{'exposure':float('inf')},{'light_softness':0},
                            {'material_overrides':None},{'source':None}]:
            review=self.review_fixture();review['issues'][0]['object_name']=None;review['patch']=patch_value
            with self.subTest(patch_value=patch_value),self.assertRaises(ValueError): self.review_result(review)
        review=self.review_fixture();review['issues'][0][CONFIG['api_key']]=None
        with self.assertRaises(ValueError) as failure: self.review_result(review)
        self.assertNotIn(CONFIG['api_key'],str(failure.exception))
        # Required null references in a mutation cannot acquire the review exception.
        with self.assertRaises(ValueError):
            ai_client.apply_patch(self.plan,{'object_material_overrides':[{'object_name':None,'material_name':'Plastic','roughness':.4}]},ANALYSIS)

    def test_all_schema_errors_supply_only_safe_paths_and_known_fields(self):
        cases=[({'value':{'secret-upstream-field':'private'}},{'type':'object','properties':{'value':{'type':'object','properties':{}}}},'unknown_fields','$.value'),
               ({'value':{}},{'type':'object','properties':{'value':{'type':'object','properties':{'required_known':{'type':'string'}},'required':['required_known']}}},'missing_fields','$.value'),
               ([1,2],{'type':'array','maxItems':1,'items':{'type':'number'}},'array_length','$'),
               ({'value':4},{'type':'object','properties':{'value':{'type':'number','maximum':1}}},'number_range','$.value'),
               ('secret-upstream-text',{'type':'string','maxLength':2},'text_length','$'),
               ('secret-upstream-text',{'type':'string','pattern':'^#123456$'},'text_pattern','$'),
               ('secret-upstream-text',{'type':'string','enum':['allowed']},'enum','$')]
        for value,schema,code,path in cases:
            with self.subTest(code=code),self.assertRaises(ai_client.SchemaValidationError) as failure:
                ai_client._check_schema(value,schema)
            self.assertEqual(failure.exception.feedback['code'],code)
            self.assertEqual(failure.exception.feedback['path'],path)
            self.assertIn(path,str(failure.exception))
            self.assertNotIn('secret-upstream',str(failure.exception))
            self.assertNotIn('secret-upstream',json.dumps(failure.exception.feedback))
            if code=='missing_fields': self.assertEqual(failure.exception.feedback['missing_fields'],['required_known'])

    def test_review_schema_error_retries_once_with_same_image_context_and_schema(self):
        valid=self.review_fixture();invalid=copy.deepcopy(valid);invalid['realism']='自由文本无法作为结构化诊断'
        before=copy.deepcopy(self.plan)
        for provider in ['anthropic','openai']:
            def wire(value):
                if provider=='anthropic':
                    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':value}]}
                return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
            with self.subTest(provider=provider),patch('ai_client._post',side_effect=[wire(invalid),wire(valid)]) as send,patch('ai_client.apply_patch',wraps=ai_client.apply_patch) as apply,patch('ai_client._image',return_value={'type':'image','source':{'type':'fixture'}}) as image:
                result=ai_client.review_preview(self.plan,ANALYSIS,'fixture.png',{'known_metric':1},{**CONFIG,'provider':provider})
                self.assertEqual(result,valid)
                self.assertEqual(send.call_count,2)
                apply.assert_called_once()
                image.assert_called_once()
                first=send.call_args_list[0][0][1];second=send.call_args_list[1][0][1]
                self.assertEqual(first['messages'][-1]['content'],second['messages'][-1]['content'][:-1])
                self.assertEqual(first.get('tools'),second.get('tools'))
                self.assertEqual(first.get('system'),second.get('system'))
                self.assertEqual(first.get('response_format'),second.get('response_format'))
                feedback=json.loads(second['messages'][-1]['content'][-1]['text'])['structured_correction']['validation_error']
                self.assertEqual(feedback,{'code':'type','path':'$.realism','expected':'object','actual':'string'})
                self.assertNotIn(invalid['realism'],json.dumps(second,ensure_ascii=False))
        self.assertEqual(self.plan,before)

    def test_review_two_invalid_responses_never_execute_parameters_or_retry_again(self):
        invalid=self.review_fixture();invalid['realism']='invalid'
        data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':invalid}]}
        before=copy.deepcopy(self.plan)
        with patch('ai_client._post',return_value=data) as send,patch('ai_client.apply_patch') as apply,self.assertRaises(ai_client.SchemaValidationError):
            ai_client.review_preview(self.plan,ANALYSIS,None,{},CONFIG)
        self.assertEqual(send.call_count,2);apply.assert_not_called()
        self.assertEqual(self.plan,before)

    def test_review_network_json_and_protocol_errors_never_retry_or_execute(self):
        cases=[ValueError('network failure'),
               {'stop_reason':'end_turn','content':[{'type':'text','text':'{"score":0.5, invalid JSON'}]},
               {'stop_reason':'tool_use','content':[{'type':'tool_use','name':'run_shell','input':{}}]},
               {'stop_reason':'max_tokens','content':[]}]
        for value in cases:
            with self.subTest(value=value),patch('ai_client._post',**({'side_effect':value} if isinstance(value,Exception) else {'return_value':value})) as send,patch('ai_client.apply_patch') as apply,self.assertRaises(ValueError):
                ai_client.review_preview(self.plan,ANALYSIS,None,{},CONFIG)
            send.assert_called_once();apply.assert_not_called()

    def test_review_valid_first_response_does_not_retry(self):
        review=self.review_fixture()
        data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':review}]}
        with patch('ai_client._post',return_value=data) as send:
            self.assertEqual(ai_client.review_preview(self.plan,ANALYSIS,None,{},CONFIG),review)
        send.assert_called_once()

    def test_review_distinguishes_imported_materials_from_current_overrides(self):
        plan=copy.deepcopy(self.plan);analysis=copy.deepcopy(ANALYSIS)
        analysis['materials'][0]['roughness']=.58
        plan['material_overrides']=[{'name':'Plastic','preset':'matte_plastic','roughness':.4,'preserve_original':True}]
        plan['object_material_overrides']=[{'object_name':'Bottle','material_name':'Plastic','roughness':.65,'preserve_original':True},
                                           {'object_name':'Cap','material_name':'Plastic','roughness':.35,'preserve_original':True}]
        data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':self.review_fixture()}]}
        with patch('ai_client._post',return_value=data) as send:
            ai_client.review_preview(plan,analysis,None,{},CONFIG)
        body=send.call_args[0][1];context=json.loads(body['messages'][0]['content'][0]['text'])
        self.assertEqual(context['materials'][0]['roughness'],.58)
        self.assertEqual(context['current_plan']['material_overrides'][0]['roughness'],.4)
        self.assertEqual([m['roughness'] for m in context['current_plan']['object_material_overrides']],[.65,.35])
        semantics=context['material_parameter_semantics']
        self.assertIn('imported asset baseline',semantics['materials_source'])
        self.assertTrue(semantics['precedence'][0].startswith('current_plan.object_material_overrides'))
        self.assertTrue(semantics['precedence'][1].startswith('current_plan.material_overrides'))
        self.assertIn('No effective shader values have been read',semantics['limitations'])
        self.assertIn('Never cite an imported roughness',body['system'])
        self.assertIn('textures/connected nodes',body['system'])
        self.assertIn('Do not invent or calculate effective shader values',body['system'])
        self.assertNotIn('effective_roughness',json.dumps(context))

    def test_review_captured_diagnostic_extra_field_is_safely_ignored(self):
        captured=json.loads((Path(__file__).resolve().parents[1]/'validation'/'ai-cabinet-exact-debug.json').read_text(encoding='utf-8'))
        review=captured['responses'][0];before=copy.deepcopy(review)
        self.assertEqual(review['realism']['causes'][1]['case'],'placeholder')
        data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':review}]}
        with patch('ai_client._post',return_value=data) as send:
            result=ai_client.review_preview(self.plan,ANALYSIS,None,{},CONFIG)
        send.assert_called_once()
        self.assertNotIn('case',result['realism']['causes'][1])
        self.assertEqual(result['realism']['summary'],review['realism']['summary'])
        self.assertEqual(result['patch'],review['patch'])
        self.assertIn('$.realism.causes[1]',result['normalization_notes'][0])
        self.assertNotIn('placeholder',json.dumps(result['normalization_notes']))
        self.assertEqual(review,before)

    def test_review_captured_broken_json_realism_is_never_trimmed_or_accepted(self):
        captured=json.loads((Path(__file__).resolve().parents[1]/'validation'/'ai-cabinet-exact-debug.json').read_text(encoding='utf-8'))
        review=captured['responses'][1]
        self.assertIsInstance(review['realism'],str)
        with self.assertRaises(ValueError): ai_client._json(review['realism'])
        data={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':review}]}
        with patch('ai_client._post',return_value=data) as send,patch('ai_client.apply_patch') as apply,self.assertRaises(ai_client.SchemaValidationError) as failure:
            ai_client.review_preview(self.plan,ANALYSIS,None,{},CONFIG)
        self.assertEqual(send.call_count,2);apply.assert_not_called()
        self.assertEqual(failure.exception.feedback['path'],'$.realism')

    def test_review_valid_encoded_diagnostic_object_decodes_and_retains_required_validation(self):
        review=self.review_fixture()
        expected=copy.deepcopy(review['realism'])
        review['realism']['private-extra-field']='private-extra-value'
        review['realism']['causes'][0]['private-extra-field']='private-extra-value'
        review['realism']=json.dumps(review['realism'])
        result=self.review_result(review)
        self.assertEqual(result['realism'],expected)
        self.assertEqual(len(result['normalization_notes']),3)
        self.assertNotIn('private-extra',json.dumps(result['normalization_notes']))
        for encoded in ['{"summary":"x","summary":"y"}', '{"summary":"x","causes":NaN,"priority_actions":[]}', '[]', '{}']:
            review['realism']=encoded
            with self.subTest(encoded=encoded),self.assertRaises(ValueError): self.review_result(review)

    def test_review_diagnostic_compatibility_does_not_sanitize_patch_or_issues(self):
        for field in ['patch','issue']:
            review=self.review_fixture()
            review['realism']['causes'][0]['case']='placeholder'
            target=review['patch'] if field=='patch' else review['issues'][0]
            target['private-extra-field']='private-extra-value'
            with self.subTest(field=field),patch('ai_client.apply_patch') as apply,self.assertRaises(ai_client.SchemaValidationError) as failure:
                self.review_result(review)
            apply.assert_not_called()
            self.assertEqual(failure.exception.feedback['code'],'unknown_fields')
            self.assertEqual(failure.exception.feedback['path'],'$.patch' if field=='patch' else '$.issues[0]')
            self.assertNotIn('private-extra',str(failure.exception))

    def test_visual_adjustment_validation(self):
        for patch_value in [{'visual_review':'yes'},{'shot_adjustments':{'invented':{'exposure_delta':.1}}},
                            {'shot_adjustments':{'hero':{'exposure_delta':5}}},{'shot_adjustments':{'hero':{'softness_multiplier':0}}}]:
            with self.subTest(patch_value=patch_value),self.assertRaises(ValueError): validate({**self.plan,**patch_value})


if __name__=='__main__':unittest.main()

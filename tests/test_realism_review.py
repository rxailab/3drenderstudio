import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import ai_client
import engine
import studio_store as store
from planner import default_plan


CONFIG={'provider':'anthropic','base_url':'https://api.hai.network/anthropic','api_key':'isolated-test-key','model':'test-model','enabled':True}


def model_review():
    return {'issues':[{'code':'flat_fill','severity':'warning','message':'右侧反差偏弱','confidence':.8}],
        'patch':{'light_softness':.8},'score':.7,'needs_user':['真实材质仍需用户确认'],
        'realism':{'summary':'右侧明暗层次偏弱，影响体积感。',
            'causes':[{'aspect':'光照层次','evidence':'图像右侧的侧板与正面亮度接近；推测补光偏强。','confidence':.8,
                'suggestion':'先调整光源柔和度，重新预览比较侧面层次。','requires_user':False}],
            'priority_actions':['比较调整光源柔和度后的真实预览。']}}


class RealismClientTests(unittest.TestCase):
    def test_real_image_and_question_are_sent_with_evidence_constraints(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'real-render.png';Image.new('RGB',(32,24),'red').save(image)
            answer=model_review()
            response={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':answer}]}
            with patch('ai_client._post',return_value=response) as send:
                actual=ai_client.review_preview(default_plan(),{},image,{'framing':{'clipped':False}},CONFIG,question='为什么现在不够逼真？')
            payload=send.call_args.args[1]
            self.assertEqual(payload['messages'][0]['content'][0]['type'],'image')
            self.assertEqual(json.loads(payload['messages'][0]['content'][1]['text'])['question'],'为什么现在不够逼真？')
            self.assertIn('visible image evidence',payload['system'])
            self.assertIn('contact shadows',payload['system'])
            self.assertIn('Never change color',payload['system'])
            self.assertEqual(actual['realism'],answer['realism'])

    def test_realism_structure_and_automatic_patch_are_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'render.png';Image.new('RGB',(8,8),'red').save(image)
            for change in ['boolean','evidence','geometry','color']:
                answer=model_review()
                if change=='boolean': answer['realism']['causes'][0]['requires_user']='false'
                if change=='evidence': answer['realism']['causes'][0]['evidence']=''
                if change=='geometry': answer['patch']={'rotation':[0,0,90]}
                if change=='color': answer['patch']={'material_overrides':[{'name':'Body','color':'#FFFFFF'}]}
                response={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_preview_review','input':answer}]}
                with self.subTest(change=change),patch('ai_client._post',return_value=response),self.assertRaises(ValueError):
                    ai_client.review_preview(default_plan(),{},image,{},CONFIG)


class RealismOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(store,'DATA',self.root),patch.object(engine,'DATA',self.root),
            patch('ai_config.get_vision_config',return_value={**CONFIG,'enabled':False,'api_key':''}),patch('ai_config.record_vision_result')]
        self.mocks=[item.start() for item in self.patches]
        with store.connection() as c:c.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        folder=self.root/'fixture/runs/old/hero';folder.mkdir(parents=True)
        Image.new('RGB',(32,32),(50,20,10)).save(folder/'shot.png')
        self.result={'success':True,'checks':{'method':'projected evaluated bounding boxes','kind':'complete_product','clipped':False,'occupancy':.7},'product_samples':[]}
        (folder/'result.json').write_text(json.dumps(self.result))
        original_plan=default_plan();original_plan['exposure']=.25
        (folder/'request.json').write_text(json.dumps({'plan':original_plan,'shot':original_plan['shots'][0]}))
        self.output={'job_id':'old','shot_id':'hero','quality':'preview','version':1,'png_url':'/files/fixture/runs/old/hero/shot.png',
            'result_url':'/files/fixture/runs/old/hero/result.json','metrics':{},'corrections':[{'round':0,'score':.4},{'round':1,'score':.7,'applied':True}],
            'review':{'source':'rules','score':.7}}
        current_plan=default_plan();current_plan['exposure']=2
        self.project={'id':'fixture','version':9,'plan':current_plan,'history':[],'outputs':[self.output],'analysis':{}}
        store.put('project',self.project)
        self.body={'output_id':'old','shot_id':'hero','quality':'preview','version':1,'question':'为什么不够逼真？'}

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def test_historical_diagnosis_uses_saved_plan_and_never_changes_current_version(self):
        with patch('ai_config.get_vision_config',return_value=CONFIG),patch('ai_client.review_preview',return_value=model_review()) as model:
            actual=engine.review_output('fixture',self.body)
        self.assertEqual(model.call_args.args[0]['exposure'],.25)
        self.assertEqual(model.call_args.args[2],self.root/'fixture/runs/old/hero/shot.png')
        self.assertEqual(actual['version'],9);self.assertEqual(actual['plan'],self.project['plan'])
        self.assertEqual(actual['history'],[])
        self.assertEqual(actual['outputs'][0]['corrections'],self.output['corrections'])
        self.assertEqual(actual['outputs'][0]['review']['source'],'ai')
        self.assertFalse(actual['outputs'][0]['review']['parameter_changes_applied'])
        self.assertEqual(actual['outputs'][0]['review_history'][0]['score'],.7)

    def test_no_key_does_not_invent_visual_causes(self):
        with patch('ai_client.review_preview') as model:
            actual=engine.review_output('fixture',self.body)
        model.assert_not_called();review=actual['outputs'][0]['review']
        self.assertEqual(review['source'],'rules');self.assertEqual(review['realism']['causes'],[])
        self.assertIn('不能单独证明',review['realism']['summary'])
        self.assertEqual(review['patch'],{})

    def test_current_metal_review_queues_real_preview_with_original_question(self):
        project=store.get('project','fixture');project['version']=1;store.put('project',project)
        self.result['studio']={'material_features':[{'name':'metal','metallic':1,'transmission':0}]}
        (self.root/'fixture/runs/old/hero/result.json').write_text(json.dumps(self.result))
        answer=model_review();answer['realism']['causes']=[{'aspect':'金属反光','evidence':'台面高光不明显',
            'suggestion':'优化反射','requires_user':False,'confidence':.7}]
        for option in ({},{'diagnostic_only':False}):
            body={**self.body,'question':'台面金属反光不明显',**option}
            with self.subTest(option=option),patch('ai_config.get_vision_config',return_value=CONFIG),patch('ai_client.review_preview',return_value=copy.deepcopy(answer)),\
                 patch.object(engine,'submit',return_value={'id':'auto-preview'}) as submit:
                actual=engine.review_output('fixture',body)
            self.assertEqual(submit.call_args.kwargs['review_question'],body['question'])
            self.assertTrue(submit.call_args.kwargs['force_review'])
            self.assertEqual(actual['outputs'][0]['review']['automatic_review_job_id'],'auto-preview')

    def test_diagnostic_only_current_metal_review_never_mutates_or_queues(self):
        project=store.get('project','fixture');project['version']=1
        project['outputs'][0]['effective_plan']=copy.deepcopy(project['plan'])
        project['auto_material']={'status':'unchanged','stage':'现有材质保留'}
        store.put('project',project);before=copy.deepcopy(project)
        self.result['studio']={'material_features':[{'name':'metal','metallic':1,'transmission':0}]}
        (self.root/'fixture/runs/old/hero/result.json').write_text(json.dumps(self.result))
        answer=model_review();answer['realism']['causes']=[{'aspect':'金属反光','evidence':'台面缺少高光层次',
            'suggestion':'增强主光并降低补光，重新渲染比较反射','requires_user':False,'confidence':.95}]
        answer['material_requests']=[{'query':'fine brushed metal','reason':'需要更明显的金属微表面'}]
        question='台面金属反光不明显'
        self.assertTrue(engine.automatic_reflection_patch({**answer,'source':'ai'},self.result,project['plan'],question))
        with patch('ai_config.get_vision_config',return_value=CONFIG),patch('ai_client.review_preview',return_value=answer),\
             patch.object(engine,'submit') as submit,patch.object(engine,'automatic_reflection_patch') as optimize,\
             patch('material_agent.schedule_after_preview') as materials,patch('material_agent.submit') as material_submit:
            actual=engine.review_output('fixture',{**self.body,'question':question,'diagnostic_only':True})
        submit.assert_not_called();optimize.assert_not_called();materials.assert_not_called();material_submit.assert_not_called()
        self.assertEqual(actual['version'],before['version']);self.assertEqual(actual['plan'],before['plan'])
        self.assertEqual(actual['history'],before['history']);self.assertEqual(actual['auto_material'],before['auto_material'])
        self.assertEqual(actual['outputs'][0]['corrections'],before['outputs'][0]['corrections'])
        self.assertEqual(len(actual['outputs']),1);self.assertEqual(store.all_records('job'),[])
        self.assertEqual(store.all_records('material_task'),[])
        review=actual['outputs'][0]['review']
        self.assertEqual(review['source'],'ai');self.assertEqual(review['mode'],'diagnostic_only')
        self.assertFalse(review['parameter_changes_applied']);self.assertNotIn('automatic_review_job_id',review)
        self.assertEqual(review['question'],question)
        self.assertEqual(actual['outputs'][0]['review_history'][0],before['outputs'][0]['review'])

    def test_diagnostic_only_flag_rejects_non_boolean_before_read_or_inference(self):
        with patch.object(engine,'get') as read,patch('ai_client.review_preview') as model,patch.object(engine,'submit') as submit:
            for value in ('true','false',1,0,None,{},[]):
                with self.subTest(value=value),self.assertRaisesRegex(ValueError,'必须为布尔值'):
                    engine.review_output('fixture',{**self.body,'diagnostic_only':value})
        read.assert_not_called();model.assert_not_called();submit.assert_not_called()

    def test_real_api_failure_is_explicit_and_keeps_measured_fallback(self):
        with patch('ai_config.get_vision_config',return_value=CONFIG),patch('ai_client.review_preview',side_effect=ValueError('AI 服务超时')):
            actual=engine.review_output('fixture',self.body)
        review=actual['outputs'][0]['review']
        self.assertEqual(review['source'],'rules');self.assertTrue(review['ai_unavailable'])
        self.assertTrue(any('AI 服务超时' in item for item in review['needs_user']))
        self.assertFalse(self.mocks[-1].call_args.args[1])

    def test_no_guess_from_current_plan_and_unsafe_paths_are_rejected(self):
        (self.root/'fixture/runs/old/hero/request.json').unlink()
        with patch('ai_client.review_preview') as model,self.assertRaises(ValueError):
            engine.review_output('fixture',self.body)
        model.assert_not_called()
        project=store.get('project','fixture');project['outputs'][0]['png_url']='/files/../outside.png';store.put('project',project)
        with self.assertRaises(ValueError):engine.review_output('fixture',self.body)

    def test_unknown_snapshot_and_oversize_question_rejected_before_ai(self):
        with patch('ai_client.review_preview') as model:
            for change in [{'output_id':'wrong'},{'question':'x'*2001}]:
                with self.assertRaises(ValueError):engine.review_output('fixture',{**self.body,**change})
        model.assert_not_called()

    def test_rules_report_only_measured_exposure_and_framing(self):
        image=self.root/'fixture/runs/old/hero/shot.png';Image.new('RGB',(32,32),'white').save(image)
        result={**self.result,'checks':{'kind':'complete_product','clipped':True},
            'product_samples':[{'x':.5,'y':.5,'material_name':'Body'} for _ in range(40)],
            'studio':{'material_features':[{'name':'Body','metallic':0,'transmission':0,'emission':0,'luminance':.2}]}}
        record=copy.deepcopy(self.output)
        review=engine.diagnose_render(record,result,default_plan(),{})
        self.assertEqual([c['aspect'] for c in review['realism']['causes']],['产品曝光','完整图构图'])
        self.assertEqual(review['patch'],{'exposure':-.7})


if __name__=='__main__':unittest.main()

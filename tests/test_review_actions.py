import copy
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

import ai_client
import ai_config
import engine
import review_actions as actions
import studio_store as store
from planner import default_plan

ANALYSIS={'objects':[{'name':'Body','type':'MESH','materials':['Paint']}],
    'materials':[{'name':'Paint','roughness':.8,'suggested_preset':'matte_plastic'}]}
CONFIG={'enabled':True,'provider':'anthropic','model':'test-model','api_key':'test-key'}


class ReviewActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old=(store.DATA,actions.DATA,engine.QUEUE)
        store.DATA=actions.DATA=self.root;engine.QUEUE=queue.Queue()
        with store.connection() as conn:conn.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        (self.root/'fixture').mkdir();Image.new('RGB',(8,8),'white').save(self.root/'fixture/image.png')
        self.review={'source':'ai','checked_at':12.5,'patch':{'exposure':-.5},'needs_user':['需要改成亮面金属吗？'],
            'realism':{'summary':'反光平','causes':[{'aspect':'补光','evidence':'过平','suggestion':'降低补光','confidence':.5,'requires_user':False}]}}
        self.project={'id':'fixture','version':4,'history':[],'plan':default_plan(),'analysis':ANALYSIS,
            'outputs':[{'job_id':'render','shot_id':'hero','quality':'preview','version':4,'png_url':'/files/fixture/image.png','review':self.review}]}
        store.put('project',self.project)
        self.body={'output_id':'render','shot_id':'hero','quality':'preview','version':4,'review_checked_at':12.5,
            'answers':[],'instruction':'','selected_causes':[0]}
        self.config_patch=patch.object(ai_config,'get_config',return_value={'enabled':False});self.config_patch.start()
        self.record_patch=patch.object(ai_config,'record_result');self.record_patch.start()

    def tearDown(self):
        self.record_patch.stop();self.config_patch.stop()
        store.DATA,actions.DATA,engine.QUEUE=self.old;self.tmp.cleanup()

    def propose(self,**extra):return actions.propose('fixture',{**self.body,**extra})['proposal']

    def apply(self,p):return actions.apply('fixture',{'proposal_id':p['id']})

    def metal_fixture(self):
        self.project['analysis']={'objects':[{'name':'不锈钢台面','type':'MESH','materials':['亮光金属']},{'name':'铝条','type':'MESH','materials':['铝']},{'name':'柜体','type':'MESH','materials':['红塑料']}],
            'materials':[{'name':x} for x in ['亮光金属','铝','红塑料']]}
        self.review['realism']['causes']=[{'aspect':'金属高光','evidence':'台面和铝条反光弱','suggestion':'调整金属反射','confidence':.8,'requires_user':False}]
        info={'editable':True,'roughness':.8,'metallic':0.0,'linked':{'metallic':False,'roughness':False}}
        visible=[{'object_name':o,'material_name':m,'material_info':info} for o,m in [('不锈钢台面','亮光金属'),('铝条','铝'),('柜体','红塑料')]]
        (self.root/'fixture/result.json').write_text(json.dumps({'success':True,'visible_materials':visible},ensure_ascii=False),encoding='utf-8')
        self.project['outputs'][0]['result_url']='/files/fixture/result.json'
        store.put('project',self.project)

    def test_checked_metal_issue_uses_visible_actual_slots_without_changing_color(self):
        self.metal_fixture()
        p=self.propose()
        changes=p['patch']['object_material_overrides']
        self.assertEqual({c['object_name'] for c in changes},{'不锈钢台面','铝条'})
        self.assertTrue(all(c['metallic']==1 and c['roughness']==.35 and 'color' not in c for c in changes))
        result=self.apply(p)
        self.assertTrue(all(m['preserve_original'] for m in result['plan']['object_material_overrides']))

    def test_explicit_brushed_metal_limits_named_target_and_survives_empty_ai_patch(self):
        self.metal_fixture()
        suggestion={'patch':{},'summary':'检查完成','needs_user':[],'manual_actions':[]}
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',return_value=suggestion):
            p=self.propose(instruction='把台面改成拉丝金属，保留颜色')
        self.assertEqual(p['patch']['object_material_overrides'],[{'object_name':'不锈钢台面','material_name':'亮光金属','preset':'brushed_metal'}])
        self.assertTrue(p['can_apply'])

    def test_unselected_or_linked_metal_is_not_guessed(self):
        self.metal_fixture()
        self.assertFalse(self.propose(selected_causes=[])['can_apply'])
        path=self.root/'fixture/result.json'; result=json.loads(path.read_text(encoding='utf-8'))
        for entry in result['visible_materials']: entry['material_info']['linked']['metallic']=True
        path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        self.assertEqual(actions.selected_metal_changes(self.project,self.project['outputs'][0],self.review,'',[],[0]),[])

    def test_ai_global_and_other_metal_targets_are_scoped_to_requested_part(self):
        self.metal_fixture()
        suggestion={'patch':{'material_overrides':[{'name':'亮光金属','preset':'brushed_metal'}],
            'object_material_overrides':[{'object_name':'铝条','material_name':'铝','preset':'brushed_metal'}]},
            'summary':'更新金属','needs_user':[],'manual_actions':[]}
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',return_value=suggestion):
            p=self.propose(instruction='把台面改成拉丝金属，保留颜色')
        self.assertNotIn('material_overrides',p['patch'])
        self.assertEqual(p['patch']['object_material_overrides'],[{'object_name':'不锈钢台面','material_name':'亮光金属','preset':'brushed_metal'}])

    def test_ai_wildcard_global_is_never_applied_to_other_materials(self):
        self.metal_fixture()
        suggestion={'patch':{'material_overrides':[{'name':'*','preset':'brushed_metal'}]},
            'summary':'更新金属','needs_user':[],'manual_actions':[]}
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',return_value=suggestion):
            p=self.propose(instruction='把台面改成拉丝金属，保留颜色')
        self.assertNotIn('material_overrides',p['patch'])
        self.assertEqual(p['patch']['object_material_overrides'],[{'object_name':'不锈钢台面','material_name':'亮光金属','preset':'brushed_metal'}])

    def test_invalid_ai_output_uses_truthful_local_explicit_finish_fallback(self):
        self.metal_fixture()
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',side_effect=ValueError('AI 返回了不允许修改的字段')):
            p=self.propose(instruction='将台面和铝条设置为拉丝金属，保留颜色和标识，其余灯光不变')
        self.assertEqual(p['origin'],'rules')
        self.assertIn('AI 修改方案无效',p['summary'])
        self.assertEqual(set(p['patch']),{'object_material_overrides'})
        self.assertEqual({m['object_name'] for m in p['patch']['object_material_overrides']},{'不锈钢台面','铝条'})
        self.assertTrue(all(m['preset']=='brushed_metal' for m in p['patch']['object_material_overrides']))
        self.assertTrue(p['manual_actions'])
        self.assertTrue(p['can_apply'])

    def test_invalid_ai_output_is_not_masked_for_uninterpretable_instruction(self):
        self.metal_fixture()
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',side_effect=ValueError('invalid')):
            with self.assertRaisesRegex(ValueError,'invalid'):
                self.propose(instruction='照参考图修改材质')

    def test_generate_persists_answers_without_render_or_version_change(self):
        p=self.propose(answers=[{'index':0,'status':'skipped','text':'暂不改材质'}])
        self.assertEqual(p['answers'][0]['question'],self.review['needs_user'][0])
        self.assertEqual(store.get('project','fixture')['version'],4)
        self.assertEqual(store.get('project','fixture')['plan'],self.project['plan'])
        self.assertEqual(store.all_records('job'),[])
        self.assertEqual(self.propose(answers=[{'index':0,'status':'skipped','text':'暂不改材质'}])['id'],p['id'])

    def test_apply_scopes_light_to_current_shot_and_is_idempotent(self):
        self.project['plan']['shot_adjustments']={'hero':{'exposure_delta':.3,'softness_multiplier':1.5},'campaign':{'exposure_delta':.2}}
        store.put('project',self.project)
        p=self.propose();result=self.apply(p)
        self.assertEqual(result['version'],5);self.assertEqual(result['plan']['exposure'],0)
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['exposure'],-.5)
        self.assertEqual(engine.effective_plan(result['plan'],'campaign')['exposure'],.2)
        self.assertEqual(len(result['history']),1);self.assertEqual(result['jobs'][0]['shot_ids'],['hero'])
        again=self.apply(p);self.assertEqual(again['version'],5);self.assertEqual(len(again['jobs']),1)

    def test_default_lighting_is_not_a_spurious_change(self):
        self.project['plan'].pop('lighting',None);self.project['outputs'][0]['review']['patch']={'exposure':0,'light_softness':1}
        store.put('project',self.project)
        p=self.propose();self.assertFalse(p['can_apply']);self.assertEqual(p['changes'],[])
        with self.assertRaisesRegex(ValueError,'没有可执行'):self.apply(p)

    def test_ai_lighting_uses_absolute_shot_values_and_preserves_other_shots(self):
        self.project['plan']['lighting']={'fill_power':.8}
        self.project['plan']['shot_adjustments']={'hero':{'lighting':{'fill_power':.6}}}
        store.put('project',self.project)
        suggestion={'patch':{'lighting':{'fill_power':.5}},'summary':'降低当前图补光','needs_user':[],'manual_actions':[]}
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',return_value=suggestion) as model:
            p=self.propose(instruction='降低补光至0.5');self.assertEqual(model.call_args.args[0]['lighting']['fill_power'],.6)
        result=self.apply(p)
        self.assertEqual(result['plan']['lighting']['fill_power'],.8)
        self.assertEqual(engine.effective_plan(result['plan'],'hero')['lighting']['fill_power'],.5)
        self.assertEqual(engine.effective_plan(result['plan'],'campaign')['lighting']['fill_power'],.8)

    def test_stale_snapshot_review_and_active_job_are_rejected(self):
        for extra in ({'version':3},{'output_id':'other'},{'review_checked_at':True},{'review_checked_at':12.6}):
            with self.assertRaises(ValueError):self.propose(**extra)
        p=self.propose();latest=store.get('project','fixture');latest['outputs'][0]['review']['realism']['summary']='new';store.put('project',latest)
        with self.assertRaises(ValueError):self.apply(p)
        store.put('job',{'id':'running','project_id':'fixture','status':'running'})
        with self.assertRaises(ValueError):self.propose()

    def test_concurrent_review_change_during_network_discards_candidate(self):
        def change(*args,**kwargs):
            p=store.get('project','fixture');p['outputs'][0]['review']['checked_at']=13;store.put('project',p)
            return {'patch':{},'summary':'none','needs_user':[],'manual_actions':[]}
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',side_effect=change):
            with self.assertRaises(ValueError):self.propose()
        self.assertNotIn('review_proposals',store.get('project','fixture'))

    def test_submit_failure_rolls_back_and_allows_retry(self):
        p=self.propose()
        with patch.object(engine,'submit',side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError):self.apply(p)
        restored=store.get('project','fixture');self.assertEqual(restored['version'],4)
        self.assertEqual(restored['review_proposals'][0]['status'],'proposed')
        self.assertEqual(self.apply(p)['version'],5)

    def test_forged_patch_cannot_override_saved_proposal(self):
        p=self.propose()
        with self.assertRaises(ValueError):actions.apply('fixture',{'proposal_id':p['id'],'patch':{'exposure':4}})
        self.assertEqual(store.get('project','fixture')['version'],4)

    def test_invalid_or_duplicate_answers_rejected(self):
        for answers in ([{'index':0,'status':'answered','text':''}],[{'index':False,'status':'confirmed'}],
            [{'index':99,'status':'confirmed'}],[{'index':0,'status':'confirmed'},{'index':0,'status':'confirmed'}]):
            with self.assertRaises(ValueError):self.propose(answers=answers)
        with self.assertRaises(ValueError):self.propose(selected_causes=[0,0])

    def test_empty_proposal_saved_and_latest_questions_are_context(self):
        reply={'patch':{},'summary':'先确认材质','needs_user':['哪一个部件？'],'manual_actions':['倒角需建模']}
        with patch.object(ai_config,'get_config',return_value=CONFIG),patch.object(ai_client,'request_review_actions',return_value=reply) as model:
            first=self.propose(instruction='想要更真实');self.assertFalse(first['can_apply'])
            second=self.propose(instruction='台面是拉丝金属')
            self.assertEqual(model.call_args.kwargs['previous']['needs_user'],['哪一个部件？'])
            self.assertEqual(len(store.get('project','fixture')['review_proposals']),2)


class ReviewActionClientTests(unittest.TestCase):
    def call(self,reply,**values):
        args={'plan':default_plan(),'analysis':ANALYSIS,'image_path':None,
              'review':{'needs_user':['改金属吗？'],'realism':{'causes':[{'aspect':'反光'}]}},
              'answers':[],'instruction':'降低补光','selected_causes':[0],'shot_id':'hero','config':CONFIG}
        args.update(values)
        response={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'submit_review_actions','input':reply}]}
        with patch.object(ai_client,'_post',return_value=response) as post:
            result=ai_client.request_review_actions(**args)
            return result,post.call_args.args[1]

    def result(self,patch_value):return {'patch':patch_value,'summary':'待应用','needs_user':[],'manual_actions':[]}

    def test_lighting_schema_and_confirmation_semantics(self):
        result,body=self.call(self.result({'lighting':{'fill_power':.5}}),answers=[{'index':0,'status':'confirmed','text':''}])
        schema=body['tools'][0]['input_schema']['properties']['patch']['properties']
        self.assertNotIn('material_overrides',schema)
        self.assertIn('NOT permission',body['system']);self.assertIn('skipped',body['system'])
        self.assertEqual(schema['shots']['items']['properties']['id']['enum'],['hero'])

    def test_explicit_material_allows_only_preset_replacement(self):
        value={'object_material_overrides':[{'object_name':'Body','material_name':'Paint','preset':'polished_metal'}]}
        _,body=self.call(self.result(value),instruction='柜体改成抛光金属')
        fields=body['tools'][0]['input_schema']['properties']['patch']['properties']['object_material_overrides']['items']
        self.assertIn('preset',fields['required']);self.assertNotIn('roughness',fields['properties']);self.assertNotIn('color',fields['properties'])
        with self.assertRaises(ValueError):self.call(self.result(value),instruction='降低补光')

    def test_unknown_actions_invalid_references_and_geometry_rejected(self):
        for value in ({'bevel':.1},{'lighting':{'fill_power':0}},{'shots':[{'id':'campaign','elevation':25}]},
                      {'object_material_overrides':[{'object_name':'Invented','material_name':'Paint','preset':'glass'}]}):
            with self.assertRaises(ValueError):self.call(self.result(value),instruction='玻璃材质')

    def test_no_selected_causes_or_instruction_cannot_propose_changes(self):
        with self.assertRaises(ValueError):self.call(self.result({'exposure':.5}),instruction='',selected_causes=[])


if __name__=='__main__':unittest.main()

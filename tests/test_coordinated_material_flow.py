"""Shared material intent through real parsing, planning and atomic storage."""
import copy
import unittest
from unittest.mock import patch

import ai_client
import engine
import instruction_service as service
import studio_store as store
import test_material_decision_flow as fixture


GROUP='钥匙孔、钥匙、轮子的支架都是不锈钢反光的'
REMAINING='轮子的支架都是不锈钢反光的'


def material_row(ident,phrase,number,status='ready'):
    return {'id':ident,'status':status,'summary':'调整已识别部位的不锈钢反光材质。',
            'patch':{'object_material_overrides':[fixture.surface(number,preset='polished_metal')]},
            'target_bindings':[fixture.binding(phrase,['t'+str(number)])]}


def pending(ident):
    return {'id':ident,'status':'needs_clarification','summary':'一处支架与轮胎共用表面，尚不能独立修改。',
            'patch':{},'target_bindings':[]}


class CoordinatedMaterialFlowTests(unittest.TestCase):
    setUp=fixture.MaterialDecisionFlowTests.setUp
    apply=fixture.MaterialDecisionFlowTests.apply

    def test_shared_material_list_applies_two_parts_and_keeps_only_brackets(self):
        self.body['text']=GROUP
        answer=fixture.wire([material_row('r1','钥匙孔',1),material_row('r2','钥匙',2),pending('r3')])
        with patch.object(ai_client,'_post',return_value=fixture.reply(answer)) as send:
            result=self.apply();replay=self.apply()
        receipt=result['instruction_result']
        self.assertEqual(result['version'],4);self.assertEqual(receipt['status'],'partially_applied')
        self.assertEqual(receipt['remaining_text'],REMAINING)
        self.assertEqual([row['quote'] for row in receipt['ready']],['钥匙孔都是不锈钢反光的','钥匙都是不锈钢反光的'])
        self.assertTrue(all(row['source_quote']==GROUP for row in receipt['ready']+receipt['pending']))
        self.assertEqual(receipt['preview_shot_ids'],['hero'])
        self.assertEqual(replay['version'],4);self.assertTrue(replay['instruction_result']['replayed'])
        indexed={row['object_name']:row for row in result['plan']['object_material_overrides']}
        for name in ('Opaque.001','Opaque.002'):
            self.assertEqual(indexed[name]['preset'],'polished_metal');self.assertEqual(indexed[name]['roughness'],.09)
        self.assertNotIn('Opaque.003',indexed)
        self.assertEqual(indexed['Opaque.004'],self.project['plan']['object_material_overrides'][-1])
        self.assertNotIn(service.PROPOSALS_KEY,store.get('project','fixture'))
        self.assertEqual(send.call_count,1);self.assertEqual(store.all_records('job'),[])

    def test_broad_beauty_reuses_lighting_once_and_materials_apply_independently(self):
        self.body['text']='让整个物体更好看，'+GROUP+'，调整灯光'
        answer=fixture.wire([
            {'id':'r1','status':'needs_clarification','summary':'主观目标无法确定性验证，请说明具体方面。','patch':{},'target_bindings':[]},
            material_row('r2','钥匙孔',1),material_row('r3','钥匙',2),pending('r4'),
            {'id':'r5','status':'ready','summary':'调整当前镜头主光与补光。',
             'patch':{'lighting':{'key_power':1.3,'fill_power':1.2}},'target_bindings':[]}])
        with patch.object(ai_client,'_post',return_value=fixture.reply(answer)) as send:result=self.apply()
        receipt=result['instruction_result']
        self.assertEqual(result['version'],4);self.assertEqual(len(result['history']),1)
        self.assertEqual(receipt['status'],'partially_applied');self.assertEqual(receipt['remaining_text'],REMAINING)
        self.assertEqual([row['label'] for row in receipt['pending']],['轮子的支架'])
        active=engine.effective_plan(result['plan'],'hero')
        self.assertEqual(active['lighting']['key_power'],1.3);self.assertEqual(active['lighting']['fill_power'],1.2)
        self.assertEqual(result['plan']['lighting'],self.project['plan']['lighting'])
        self.assertEqual(engine.effective_plan(result['plan'],'campaign')['lighting'],self.project['plan']['lighting'])
        lights=[row for row in receipt['changes'] if '.lighting.' in row['path']]
        self.assertEqual(len(lights),2)
        self.assertTrue(all(row['effective_before']==1 for row in lights))
        self.assertEqual(send.call_count,1)

    def test_matching_shared_material_subjects_leave_pending_without_fake_change(self):
        self.body['text']=GROUP
        current=store.get('project','fixture')
        current['plan']['object_material_overrides']=[
            fixture.surface(n,preset='polished_metal',roughness=.09,preserve_original=False,source='user') for n in (1,2)]
        store.put('project',current);self.project=copy.deepcopy(current)
        answer=fixture.wire([material_row('r1','钥匙孔',1,'already_satisfied'),
                             material_row('r2','钥匙',2,'already_satisfied'),pending('r3')])
        with patch.object(ai_client,'_post',return_value=fixture.reply(answer)) as send:result=self.apply()
        receipt=result['instruction_result']
        self.assertEqual(receipt['status'],'no_change');self.assertTrue(receipt['partial'])
        self.assertEqual(receipt['remaining_text'],REMAINING);self.assertEqual(receipt['changes'],[])
        self.assertEqual(result['version'],3);self.assertEqual(result['history'],[])
        self.assertEqual(result['plan'],self.project['plan']);self.assertEqual(receipt['preview_shot_ids'],[])
        self.assertEqual(send.call_count,1);self.assertEqual(store.all_records('job'),[])


if __name__=='__main__':unittest.main()

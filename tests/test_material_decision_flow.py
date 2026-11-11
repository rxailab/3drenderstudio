"""Screenshot material request through the real planner and persistence layer.

External AI replies are fixtures. Current-image samples and the project store
are isolated; no credentials, user projects, or external renderer are used.
"""
import copy
import importlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

import ai_client
import engine
import instruction_service as service
import planner
import studio_store as store


TEXT='上表面是反光的拉丝不锈钢，箱体是红色金属有光泽'
SOURCE={'output_id':'source','shot_id':'hero','quality':'preview','version':3}
CONFIG={'enabled':True,'provider':'anthropic','base_url':'https://example.invalid',
        'api_key':'fixture-private-key-never-sent','model':'fixture-vision'}


def surface(number,**values):
    return {'object_name':'Opaque.%03d'%number,'material_name':'Shared',**values}


def binding(phrase,ids):
    return {'phrase':phrase,'purpose':'material','target_ids':ids}


def rows(body_status='ready',top_status='ready'):
    return [
        {'id':'r1','status':top_status,'summary':'上表面使用反光拉丝金属。',
         'patch':{'object_material_overrides':[surface(1,preset='brushed_metal')]},
         'target_bindings':[binding('上表面',['t1'])]},
        {'id':'r2','status':body_status,'summary':'箱体使用有光泽的红色金属。' if body_status=='ready' else '请确认箱体的完整部位。',
         'patch':{'object_material_overrides':[surface(2,preset='painted_metal',color='#B93832'),
                                               surface(3,preset='painted_metal',color='#B93832')]} if body_status in ('ready','already_satisfied') else {},
         'target_bindings':[binding('箱体',['t2','t3'])] if body_status in ('ready','already_satisfied') else []},
    ]


def wire(parts=None):
    return {'requirement_results':rows() if parts is None else parts,'notes':[]}


def legacy(value,status='apply',parts=None):
    output={'status':status,'patch':copy.deepcopy(value),'summary':'准备修改上表面和箱体。',
            'notes':[],'unhandled':[],
            'target_bindings':[binding('上表面',['t1']),binding('箱体',['t2','t3'])] if value else []}
    if parts is not None:output['requirement_results']=copy.deepcopy(parts)
    return output


def reply(value):
    return {'stop_reason':'tool_use','content':[{'type':'tool_use','name':ai_client.TOOL_NAME,'input':copy.deepcopy(value)}]}


class MaterialDecisionFlowTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        for module in (store,engine,service):
            mocked=patch.object(module,'DATA',self.root);mocked.start();self.addCleanup(mocked.stop)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear();self.addCleanup(service._INFLIGHT.clear)
        folder=self.root/'fixture/runs/source';folder.mkdir(parents=True)
        Image.new('RGB',(32,32),'white').save(folder/'hero.png')
        self.result_path=folder/'result.json'
        self.result={'success':True,'product_samples':[{'object_name':'Opaque.%03d'%n,'material_name':'Shared','x':n/6,'y':.5} for n in range(1,5)]}
        self.result_path.write_text(json.dumps(self.result))
        plan=planner.default_plan()
        plan['material_overrides']=[{'name':'Shared','preset':'matte_plastic','color':'#909090'}]
        plan['object_material_overrides']=[surface(1,preset='polished_metal',roughness=.08,preserve_original=False),
                                           surface(2,preset='painted_metal',color='#707070',roughness=.95,preserve_original=False),
                                           surface(4,preset='polished_metal',roughness=.07,preserve_original=False)]
        self.project={'id':'fixture','name':'柜体语义决策回归','slug':'fixture','version':3,'status':'ready','history':[],
                      'metadata':{},'plan':plan,
                      'analysis':{'objects':[{'name':'Opaque.%03d'%n,'type':'MESH','materials':['Shared']} for n in range(1,5)],
                                  'materials':[{'name':'Shared','suggested_preset':'matte_plastic'}]},
                      'outputs':[{'job_id':'source','shot_id':'hero','quality':'preview','version':3,
                                  'png_url':'/files/fixture/runs/source/hero.png','result_url':'/files/fixture/runs/source/result.json'}]}
        store.put('project',copy.deepcopy(self.project));self.project=store.get('project','fixture')
        # These fixtures exercise the retained v1 response contract explicitly;
        # production-default v2 execution is covered by test_semantic_pipeline_v16.
        self.body={'text':TEXT,'expected_version':3,'shot_id':'hero','source':SOURCE,'protocol':'legacy_v1'}
        for name in ('get_config','get_vision_config'):
            mocked=patch('ai_config.'+name,return_value=CONFIG);mocked.start();self.addCleanup(mocked.stop)
        for name in ('record_result','record_vision_result'):
            mocked=patch('ai_config.'+name);mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'runtime_status',return_value={'capabilities':{'inspect_parts':False,'pick':False}})
        mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(engine,'submit',side_effect=AssertionError('Instruction preparation must not submit preview'))
        mocked.start();self.addCleanup(mocked.stop)

    def apply(self,key='request-1'):
        return service.apply_instruction('fixture',self.body,key)

    def assert_unchanged(self):
        self.assertEqual(store.get('project','fixture'),self.project)
        self.assertEqual(store.all_records('job'),[])

    def assert_materials(self,result):
        self.assertEqual(result['version'],4)
        self.assertEqual(result['instruction_result']['status'],'applied')
        indexed={row['object_name']:row for row in result['plan']['object_material_overrides']}
        self.assertEqual(indexed['Opaque.001']['preset'],'brushed_metal')
        self.assertEqual(indexed['Opaque.001']['roughness'],.3)
        for name in ('Opaque.002','Opaque.003'):
            self.assertEqual(indexed[name]['preset'],'painted_metal')
            self.assertEqual(indexed[name]['color'],'#B93832')
            self.assertEqual(indexed[name]['roughness'],.28)
        self.assertEqual(indexed['Opaque.004'],self.project['plan']['object_material_overrides'][2])
        self.assertEqual(result['plan']['material_overrides'],self.project['plan']['material_overrides'])
        self.assertEqual(result['plan']['lighting'],self.project['plan']['lighting'])
        self.assertEqual(result['plan']['shots'],self.project['plan']['shots'])
        self.assertEqual(len(result['history']),1)
        self.assertEqual(store.all_records('job'),[])

    def test_exact_screenshot_sentence_applies_both_materials_once_and_replays_without_ai(self):
        with patch.object(ai_client,'_post',return_value=reply(wire())) as send:
            first=self.apply();again=self.apply()
        self.assert_materials(first)
        self.assertEqual(again['version'],4);self.assertTrue(again['instruction_result']['replayed'])
        self.assertEqual(send.call_count,1)
        schema=send.call_args.args[1]['tools'][0]['input_schema']
        self.assertIn('requirement_results',schema['properties'])
        for key in ('status','patch','unhandled','target_bindings'):self.assertNotIn(key,schema['properties'])

    def test_partial_label_with_identical_all_ready_wrapper_is_safely_derived_to_apply(self):
        parts=rows();merged={'object_material_overrides':sum([part['patch']['object_material_overrides'] for part in parts],[])}
        with patch.object(ai_client,'_post',return_value=reply(legacy(merged,'partial_available',parts))) as send:
            result=self.apply()
        self.assert_materials(result);self.assertEqual(send.call_count,1)

    def test_second_screenshot_state_conflict_is_corrected_inside_two_call_budget(self):
        merged={'object_material_overrides':sum([part['patch']['object_material_overrides'] for part in rows()],[])}
        bad=legacy(merged,'needs_clarification')
        with patch.object(ai_client,'_post',side_effect=[reply(bad),reply(wire())]) as send:
            result=self.apply()
        self.assert_materials(result);self.assertEqual(send.call_count,2)

    def test_persistent_status_conflict_has_no_mutation_and_sanitized_diagnostic(self):
        merged={'object_material_overrides':sum([part['patch']['object_material_overrides'] for part in rows()],[])}
        bad=legacy(merged,'needs_clarification')
        with patch.object(ai_client,'_post',return_value=reply(bad)) as send:
            with self.assertRaises(ai_client.ResponseContractError) as caught:self.apply()
        self.assertEqual(send.call_count,2);self.assert_unchanged()
        self.assertEqual(caught.exception.error_code,'instruction_response_invalid')
        self.assertFalse(caught.exception.applied)
        records=service.error_diagnostics('fixture');self.assertEqual(len(records),1)
        self.assertEqual(records[0][1]['text'],TEXT)
        self.assertEqual(records[0][1]['id'],caught.exception.diagnostic_id)
        encoded=json.dumps(records,ensure_ascii=False)
        self.assertNotIn(CONFIG['api_key'],encoded);self.assertNotIn('base64',encoded)
        self.assertNotIn(str(self.root),encoded)

    def test_explicit_review_keeps_top_for_confirmation_and_retains_original_body_clause(self):
        self.body['partial_mode']='review'
        parts=rows(body_status='needs_clarification')
        duplicate=legacy(parts[0]['patch'],'partial_available',parts)
        duplicate['target_bindings']=copy.deepcopy(parts[0]['target_bindings'])
        with patch.object(ai_client,'_post',return_value=reply(duplicate)) as send:
            prepared=self.apply()
            self.assertEqual(prepared['version'],3);self.assertEqual(prepared['history'],[])
            self.assertEqual(prepared['instruction_result']['status'],'needs_confirmation')
            body={'proposal_id':prepared['instruction_result']['proposal_id'],'expected_version':3}
            done=service.confirm_instruction('fixture',body,'confirm-1')
            replay=service.confirm_instruction('fixture',body,'confirm-1')
        self.assertEqual(send.call_count,1)
        self.assertEqual(done['version'],4);self.assertEqual(replay['version'],4)
        self.assertEqual(done['instruction_result']['remaining_text'],TEXT.split('，')[1])
        indexed={row['object_name']:row for row in done['plan']['object_material_overrides']}
        self.assertEqual(indexed['Opaque.001']['roughness'],.3)
        self.assertEqual(indexed['Opaque.002'],self.project['plan']['object_material_overrides'][1])
        self.assertNotIn('Opaque.003',indexed)

    def test_matching_grounded_top_does_not_block_requested_body_change(self):
        current=store.get('project','fixture')
        current['plan']=ai_client.apply_patch(current['plan'],{'object_material_overrides':[surface(1,preset='brushed_metal',roughness=.3)]},current['analysis'])
        store.put('project',current);self.project=copy.deepcopy(current)
        with patch.object(ai_client,'_post',return_value=reply(wire(rows(top_status='already_satisfied')))) as send:
            result=self.apply()
        self.assert_materials(result);self.assertEqual(send.call_count,1)

    def test_protected_red_body_only_changes_finish_through_service(self):
        current=store.get('project','fixture')
        current['plan']['object_material_overrides']=[surface(1,preset='polished_metal',roughness=.08),
            *[surface(n,preset='painted_metal',color='#B93832',roughness=.95,preserve_original=False,source='user') for n in (2,3)],
            surface(4,preset='polished_metal',roughness=.07)]
        store.put('project',current);self.project=copy.deepcopy(current)
        self.body['text']='保持颜色不变，箱体是红色金属有光泽'
        answer=wire([{'id':'r1','status':'ready','summary':'保留红色金属，增强光泽。',
                      'patch':{'object_material_overrides':[surface(n,roughness=.28) for n in (2,3)]},
                      'target_bindings':[binding('箱体',['t2','t3'])]}])
        with patch.object(ai_client,'_post',return_value=reply(answer)) as send:result=self.apply()
        self.assertEqual(result['version'],4);self.assertEqual(send.call_count,1)
        indexed={row['object_name']:row for row in result['plan']['object_material_overrides']}
        for n in (2,3):
            self.assertEqual(indexed['Opaque.%03d'%n]['color'],'#B93832')
            self.assertEqual(indexed['Opaque.%03d'%n]['preset'],'painted_metal')
            self.assertEqual(indexed['Opaque.%03d'%n]['roughness'],.28)
        self.assertEqual(indexed['Opaque.001'],self.project['plan']['object_material_overrides'][0])
        self.assertEqual(indexed['Opaque.004'],self.project['plan']['object_material_overrides'][-1])

    def test_all_matching_user_materials_do_not_increment_version_for_provenance(self):
        current=store.get('project','fixture')
        current['plan']['object_material_overrides']=[
            surface(1,preset='brushed_metal',roughness=.3,preserve_original=False,source='user'),
            *[surface(n,preset='painted_metal',color='#B93832',roughness=.28,preserve_original=False,source='user') for n in (2,3)],
            surface(4,preset='polished_metal',roughness=.07)]
        store.put('project',current);self.project=copy.deepcopy(current)
        answer=wire(rows(body_status='already_satisfied',top_status='already_satisfied'))
        with patch.object(ai_client,'_post',return_value=reply(answer)) as send:result=self.apply()
        self.assertEqual(result['version'],3);self.assertEqual(result['history'],[])
        self.assertEqual(result['instruction_result']['status'],'no_change')
        self.assertEqual(result['plan']['object_material_overrides'],self.project['plan']['object_material_overrides'])
        self.assertEqual(store.all_records('job'),[]);self.assertEqual(send.call_count,1)

    def test_source_change_during_correction_still_prevents_every_write(self):
        merged={'object_material_overrides':sum([part['patch']['object_material_overrides'] for part in rows()],[])}
        calls=[]
        def respond(*args):
            calls.append(1)
            if len(calls)==1:return reply(legacy(merged,'needs_clarification'))
            self.result['product_samples'][0]['x']=.9
            self.result_path.write_text(json.dumps(self.result))
            return reply(wire())
        with patch.object(ai_client,'_post',side_effect=respond):
            with self.assertRaises(service.InstructionError):self.apply()
        self.assertEqual(len(calls),2);self.assert_unchanged()

    def test_diagnostics_are_bounded_and_bundle_never_includes_raw_provider_data(self):
        error=SimpleNamespace(error_code='instruction_response_invalid',applied=False,
                              feedback={'code':'decision_conflict','path':'$.status','api_key':'DO-NOT-EXPORT'},
                              response_shapes=[{'attempt':1,'status':'needs_clarification','keys':['status','patch'],
                                                'raw_response':'DO-NOT-EXPORT','api_key':'DO-NOT-EXPORT',
                                                'requirement_rows':[{'id':'r1','status':'ready','patch_keys':['object_material_overrides'],
                                                                     'raw_values':{'api_key':'DO-NOT-EXPORT'}}]}])
        for _ in range(12):service._record_response_error(self.project,TEXT,SOURCE,error)
        records=service.error_diagnostics('fixture');self.assertEqual(len(records),10);self.assert_unchanged()
        for _,record in records:
            self.assertNotIn('DO-NOT-EXPORT',json.dumps(record));self.assertFalse(record['applied'])
        env=Path(__file__).resolve().parents[1]/'.env';original_exists=Path.exists
        with patch.object(Path,'exists',lambda path:False if path.absolute()==env else original_exists(path)):
            app=importlib.import_module('app')
        with patch.object(app,'DATA',self.root):
            response=app.app.test_client().get('/api/projects/fixture/bundle')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            files=[name for name in archive.namelist() if name.startswith('instruction-errors/')]
            self.assertEqual(len(files),10)
            self.assertTrue(all(b'DO-NOT-EXPORT' not in archive.read(name) for name in files))

    def test_logging_failure_does_not_change_known_uncommitted_error(self):
        error=SimpleNamespace(error_code='instruction_response_invalid',applied=False,feedback={},response_shapes=[])
        with patch.object(store,'atomic_json',side_effect=OSError('fixture disk unavailable')):
            service._record_response_error(self.project,TEXT,SOURCE,error)
        self.assertFalse(hasattr(error,'diagnostic_id'));self.assert_unchanged()

    def test_long_unknown_structure_stays_within_export_record_budget(self):
        keys=[('field_%03d_'%index)+'x'*65 for index in range(40)]
        row={'id':'r1','status':'ready','patch_keys':keys,'binding_count':160}
        shape={'attempt':1,'format':'legacy','status':'partial_available','keys':keys,'patch_keys':keys,
               'requirement_rows':[copy.deepcopy(row) for _ in range(20)]}
        error=SimpleNamespace(error_code='instruction_response_invalid',applied=False,
                              feedback={'code':'decision_conflict','path':'$.patch'},response_shapes=[shape,shape])
        service._record_response_error(self.project,TEXT,SOURCE,error)
        path=self.root/'fixture/instruction-errors'/(error.diagnostic_id+'.json')
        self.assertLessEqual(path.stat().st_size,service.MAX_ERROR_DIAGNOSTIC_BYTES)
        records=service.error_diagnostics('fixture');self.assertEqual(len(records),1)
        self.assertEqual(records[0][1]['text'],TEXT)
        self.assertTrue(records[0][1]['structure_truncated']);self.assert_unchanged()


if __name__=='__main__':unittest.main()

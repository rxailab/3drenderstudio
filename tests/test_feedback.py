import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app as server
from planner import default_plan


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'fixture').mkdir();(self.root/'fixture/preview.png').write_bytes(b'test path only; inference is mocked')
        self.project={'id':'fixture','version':3,'plan':default_plan(),'analysis':{},'history':[],
            'outputs':[{'job_id':'source-job','shot_id':'hero','version':3,'quality':'preview','png_url':'/files/fixture/preview.png'}]}
        self.body={'output_id':'source-job','shot_id':'hero','version':3,'quality':'preview','points':[{'id':'p1','x':.5,'y':.5}],'text':'这个部位调成蓝色哑光'}
        self.hits=[{'id':'p1','hit':True,'object_name':'Bottle','material_name':'Body','x':.5,'y':.5}]
        self.config={'enabled':True,'api_key':'test-key-never-sent','model':'test-model'}
        self.patchers=[patch.object(server,'DATA',self.root),patch.object(server,'get',side_effect=lambda *args:copy.deepcopy(self.project)),
            patch.object(server,'all_records',return_value=[]),patch.object(server.ai_config,'get_config',return_value=self.config),
            patch.object(server.engine,'pick_output',return_value={'hits':self.hits}),patch.object(server,'put'),
            patch.object(server,'instruct',return_value=default_plan()),patch.object(server.engine,'submit',return_value={'id':'new-preview'}),
            patch.object(server.engine,'project_view',return_value={'id':'fixture','version':4}),
            # The route and its lifetime reservation share this isolated project.
            patch.object(server.project_lifecycle.store,'get',side_effect=lambda *args:copy.deepcopy(self.project))]
        self.mocks=[p.start() for p in self.patchers]
        self.client=server.app.test_client()

    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.tmp.cleanup()

    def test_exact_snapshot_image_and_hits_used_then_real_job_queued(self):
        result=self.client.post('/api/projects/fixture/feedback',json=self.body)
        self.assertEqual(result.status_code,200)
        call=self.mocks[6].call_args
        self.assertEqual(call.kwargs['selections'],self.hits)
        self.assertEqual(call.kwargs['image_path'],self.root/'fixture/preview.png')
        self.mocks[7].assert_called_once()
        self.assertEqual(self.mocks[7].call_args.args[1:4],('preview','preview',['hero']))
        written=self.mocks[5].call_args.args[1]
        self.assertEqual(written['version'],4)
        self.assertEqual(written['feedback'][0]['job_id'],'new-preview')

    def test_stale_and_forged_snapshot_never_reaches_model(self):
        for change in [{'version':2},{'output_id':'other-job'}]:
            self.assertEqual(self.client.post('/api/projects/fixture/feedback',json={**self.body,**change}).status_code,400)
        self.mocks[6].assert_not_called()

    def test_no_key_or_background_does_not_apply(self):
        self.config['api_key']=''
        self.assertEqual(self.client.post('/api/projects/fixture/feedback',json=self.body).status_code,400)
        self.config['api_key']='test-key'
        self.mocks[4].return_value={'hits':[{'hit':False}]}
        self.assertEqual(self.client.post('/api/projects/fixture/feedback',json=self.body).status_code,400)
        self.mocks[6].assert_not_called()
        self.mocks[7].assert_not_called()

    def test_user_edit_during_inference_is_not_overwritten(self):
        changed={**self.project,'version':4}
        self.mocks[1].side_effect=[copy.deepcopy(self.project),changed]
        response=self.client.post('/api/projects/fixture/feedback',json=self.body)
        self.assertEqual(response.status_code,400)
        self.mocks[5].assert_not_called()
        self.mocks[7].assert_not_called()

    def test_complex_shader_is_rejected_before_saving_scalar_only_ai_edit(self):
        self.hits[0]['material_info']={'editable':False}
        changed=default_plan();changed['object_material_overrides']=[{'object_name':'Bottle','material_name':'Body','roughness':.6,'preserve_original':True}]
        self.mocks[6].return_value=changed
        response=self.client.post('/api/projects/fixture/feedback',json=self.body)
        self.assertEqual(response.status_code,400)
        self.mocks[5].assert_not_called();self.mocks[7].assert_not_called()


if __name__=='__main__':unittest.main()

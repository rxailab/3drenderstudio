import copy
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine
import material_edit
import studio_store as store
from planner import default_plan


class MaterialEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old_data=store.DATA;self.old_queue=engine.QUEUE
        store.DATA=self.root;engine.QUEUE=queue.Queue()
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        self.project={'id':'fixture','version':3,'plan':default_plan(),'analysis':{'objects':[]},'history':[],
            'outputs':[{'job_id':'render','shot_id':'hero','quality':'preview','version':3}]}
        store.put('project',self.project)
        self.body={'output_id':'render','shot_id':'hero','quality':'preview','version':3,
            'points':[{'id':'one','x':.3,'y':.4}],'patch':{'roughness':.8}}
        self.hit={'id':'one','hit':True,'object_name':'Selected','material_name':'Shared',
                  'material_info':{'editable':True,'linked':{'color':True}}}
        self.pick=patch.object(engine,'pick_output',return_value={'hits':[self.hit]})
        self.mock_pick=self.pick.start()

    def tearDown(self):
        self.pick.stop();store.DATA=self.old_data;engine.QUEUE=self.old_queue;self.tmp.cleanup()

    def apply(self,**changes):
        return material_edit.apply_selection('fixture',{**self.body,**changes})

    def test_no_ai_needed_and_only_explicit_fields_change(self):
        with patch('ai_config.get_config',side_effect=AssertionError('No AI configuration should be needed')):
            result=self.apply()
        entry=result['plan']['object_material_overrides'][0]
        self.assertEqual(entry,{'object_name':'Selected','material_name':'Shared','preserve_original':True,'roughness':.8,'source':'user'})
        self.assertEqual(result['plan']['material_overrides'],[])
        self.assertEqual(result['version'],4)
        self.assertEqual(result['jobs'][0]['kind'],'preview')
        self.assertEqual(result['jobs'][0]['shot_ids'],['hero'])
        self.assertEqual(result['history'][0]['plan'],self.project['plan'])

    def test_snapshot_is_resolved_again_and_forged_names_ignored(self):
        result=self.apply(object_name='Unselected',material_name='Other')
        self.mock_pick.assert_called_once()
        self.assertEqual(result['plan']['object_material_overrides'][0]['object_name'],'Selected')

    def test_duplicate_points_edit_same_pair_only_once(self):
        self.mock_pick.return_value={'hits':[self.hit,{**self.hit,'id':'two'}]}
        result=self.apply(points=[*self.body['points'],{'id':'two','x':.4,'y':.4}])
        self.assertEqual(len(result['plan']['object_material_overrides']),1)
        self.assertEqual(len(result['material_edits'][0]['targets']),1)

    def test_preset_resets_old_pbr_overrides_but_keeps_explicit_color(self):
        self.project['plan']['object_material_overrides']=[{'object_name':'Selected','material_name':'Shared','preserve_original':True,'color':'#112233','roughness':.9,'metallic':0}]
        store.put('project',self.project)
        result=self.apply(patch={'preset':'polished_metal','transmission':.1})
        entry=result['plan']['object_material_overrides'][0]
        self.assertEqual(entry['color'],'#112233');self.assertEqual(entry['preset'],'polished_metal')
        self.assertFalse(entry['preserve_original']);self.assertNotIn('roughness',entry);self.assertNotIn('metallic',entry)
        self.assertEqual(entry['transmission'],.1)

    def test_subsequent_scalar_change_keeps_existing_preset_semantics(self):
        self.project['plan']['object_material_overrides']=[{'object_name':'Selected','material_name':'Shared','preset':'glass','preserve_original':False}]
        store.put('project',self.project)
        entry=self.apply()['plan']['object_material_overrides'][0]
        self.assertEqual(entry['preset'],'glass');self.assertFalse(entry['preserve_original'])

    def test_scalar_replaces_only_its_downloaded_texture_channel(self):
        self.project['plan']['object_material_overrides']=[{'object_name':'Selected','material_name':'Shared',
            'preserve_original':True,'textures':{'roughness':'rough.png','normal':'normal.png','base_color':'label.png'},'license':'CC0'}]
        store.put('project',self.project)
        entry=self.apply()['plan']['object_material_overrides'][0]
        self.assertEqual(entry['textures'],{'normal':'normal.png','base_color':'label.png'})
        self.assertEqual(entry['roughness'],.8)

    def test_builtin_preset_replaces_downloaded_surface_maps(self):
        self.project['plan']['object_material_overrides']=[{'object_name':'Selected','material_name':'Shared',
            'preserve_original':True,'textures':{'roughness':'rough.png','normal':'normal.png'},'license':'CC0','texture_scale':4}]
        store.put('project',self.project)
        entry=self.apply(patch={'preset':'ceramic'})['plan']['object_material_overrides'][0]
        self.assertNotIn('textures',entry);self.assertNotIn('license',entry);self.assertNotIn('texture_scale',entry)
        self.assertEqual(entry['preset'],'ceramic')

    def test_reset_only_removes_selected_local_override(self):
        other={'object_name':'Unselected','material_name':'Shared','roughness':.5}
        self.project['plan']['object_material_overrides']=[{'object_name':'Selected','material_name':'Shared','roughness':.8},other]
        self.project['plan']['material_overrides']=[{'name':'Shared','preset':'glass'}]
        store.put('project',self.project)
        result=self.apply(patch={},reset=True)
        self.assertEqual(result['plan']['object_material_overrides'],[other])
        self.assertEqual(result['plan']['material_overrides'],self.project['plan']['material_overrides'])

    def test_stale_background_and_empty_material_rejected(self):
        with self.assertRaises(ValueError):self.apply(version=2)
        self.mock_pick.assert_not_called()
        for hit in [{'hit':False},{'hit':True,'object_name':'Selected','material_name':None}]:
            self.mock_pick.return_value={'hits':[hit]}
            with self.assertRaises(ValueError):self.apply()
        self.assertEqual(store.get('project','fixture')['version'],3)

    def test_invalid_patch_fails_without_saving(self):
        for change in [{},{'color':'#nothex'},{'roughness':float('nan')},{'roughness':2},{'roughness':True},{'textures':{'base_color':'C:/secret.png'}},{'preset':'unknown'}]:
            with self.assertRaises((ValueError,TypeError)):self.apply(patch=change)
        self.assertEqual(store.get('project','fixture')['version'],3)
        self.assertEqual(store.all_records('job'),[])

    def test_unsupported_shader_needs_explicit_preset(self):
        self.mock_pick.return_value={'hits':[{**self.hit,'material_info':{'editable':False}}]}
        with self.assertRaisesRegex(ValueError,'预设'):self.apply()
        self.assertEqual(self.apply(patch={'preset':'matte_plastic'})['version'],4)

    def test_active_job_and_concurrent_edit_are_preserved(self):
        store.put('job',{'id':'active','project_id':'fixture','status':'running'})
        with self.assertRaises(ValueError):self.apply()
        self.mock_pick.assert_not_called()
        store.put('job',{'id':'active','project_id':'fixture','status':'completed'})
        def change_version(*args):
            p=store.get('project','fixture');p['version']=4;store.put('project',p)
            return {'hits':[self.hit]}
        self.mock_pick.side_effect=change_version
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(store.get('project','fixture')['plan'],self.project['plan'])

    def test_submit_failure_rolls_back_material_change(self):
        with patch.object(engine,'submit',side_effect=ValueError('queue unavailable')):
            with self.assertRaises(ValueError):self.apply()
        result=store.get('project','fixture')
        self.assertEqual(result['version'],3);self.assertEqual(result['plan'],self.project['plan'])


if __name__=='__main__':unittest.main()

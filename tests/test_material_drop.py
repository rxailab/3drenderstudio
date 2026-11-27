"""A drop is one versioned edit to an exported object and material pair."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as web
import hero_optimizer
import keyshot_catalog
import studio_store
import viewport_service
from planner import default_plan


class MaterialDropTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for mock in (patch.object(studio_store,'DATA',Path(folder.name)),patch.object(web.engine,'yield_to_user'),
                     patch.object(web.engine,'project_view',side_effect=self.public),
                     patch.object(viewport_service,'_current'),
                     patch.object(viewport_service,'_cached',return_value={'material_slots':{'Body':{'Paint.001':'Paint'}}}),
                     patch.object(viewport_service,'TASKS',{})):
            mock.start()
            self.addCleanup(mock.stop)
        self.client=web.app.test_client()
        self.id='a'*32
        self.path='/api/projects/'+self.id
        plan=default_plan()
        plan['object_material_overrides']=[{'object_name':'Trim','material_name':'Paint','roughness':.2}]
        studio_store.put('project',{'id':self.id,'version':1,'plan':plan,'history':[]})
        self.materials=[row['id'] for row in keyshot_catalog.catalog()['materials'][:2]]

    @staticmethod
    def public(ident):
        project=studio_store.get('project',ident)
        redo=project.pop('_material_redo',{})
        project['can_redo']=bool(redo.get('steps') and redo['version']==project['version'])
        return project

    def drop(self,version,ident=None,**patch_body):
        task_id=format(version,'032x')
        viewport_service.TASKS[task_id]={'project_id':self.id,'version':version,'shot_id':'hero','status':'ready',
                                        '_folder':'.','_fingerprint':'test'}
        body={'expected_version':version,'viewport_id':task_id,'shot_id':'hero','object_name':'Body',
              'material_name':'Paint','catalog_id':ident or self.materials[0]}
        return self.client.post(self.path+'/materials/drop',json={**body,**patch_body})

    def history(self,action,version):
        return self.client.post(self.path+'/'+action,json={'expected_version':version})

    def test_replacement_undo_redo_and_branching(self):
        first=self.drop(1).get_json()
        self.assertEqual(first['version'],2)
        trim,body=first['plan']['object_material_overrides']
        self.assertEqual(trim,{'object_name':'Trim','material_name':'Paint','roughness':.2})
        self.assertEqual(body['catalog_id'],self.materials[0])
        self.assertEqual(self.drop(2).get_json()['version'],2)
        self.assertEqual(self.drop(2,self.materials[1]).get_json()['version'],3)
        self.assertTrue(self.history('undo',3).get_json()['can_redo'])
        undone=self.history('undo',4).get_json()
        self.assertEqual(undone['plan']['object_material_overrides'],[trim])
        self.assertEqual(self.history('redo',5).get_json()['plan'],first['plan'])
        self.assertFalse(self.history('redo',6).get_json()['can_redo'])
        self.history('undo',7)
        self.history('undo',8)
        self.assertFalse(self.drop(9,self.materials[1]).get_json()['can_redo'])
        self.assertEqual(self.history('redo',10).status_code,409)

    def test_rejects_stale_versions_unexported_slots_and_ids(self):
        for fields in ({'object_name':'Other'},{'material_name':'Paint.001'},{'catalog_id':'missing'}):
            self.assertEqual(self.drop(1,**fields).status_code,400)
        self.assertEqual(self.drop(1,shot_id='other').status_code,409)
        self.assertEqual(self.drop(1).status_code,200)
        self.assertEqual(self.drop(1,self.materials[1]).status_code,409)
        self.assertEqual(self.history('undo',1).status_code,409)
        self.assertEqual(studio_store.get('project',self.id)['version'],2)

    def test_checkpoint_authorization_is_specific_to_the_dropped_slot(self):
        plan=default_plan()
        updated={**plan,'object_material_overrides':[{'object_name':'Body','material_name':'Paint','catalog_id':self.materials[0]}]}
        request={'plan':updated,'baseline_plan':plan}
        change={'object_name':'Body','material_name':'Paint','catalog_id':self.materials[0],'previous_catalog_id':None,
                'path':'object_material_overrides[0].catalog_id','bindings':[{'object_name':'Body','slot_index':0}]}
        proof={'request_match':True,'request_plan_sha256':hero_optimizer.fingerprint(updated),
               'request_baseline_plan_sha256':hero_optimizer.fingerprint(plan),'untouched_materials_verified':True,
               'authorized_catalog_changes':[change]}
        self.assertTrue(hero_optimizer._authorized_catalog_edit(proof,request))
        for bad in ({'object_name':'Trim'},{'catalog_id':self.materials[1]},{'bindings':[]},{'previous_catalog_id':self.materials[1]}):
            self.assertFalse(hero_optimizer._authorized_catalog_edit({**proof,'authorized_catalog_changes':[{**change,**bad}]},request))

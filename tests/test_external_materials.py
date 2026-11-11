import copy
import unittest

from planner import default_plan,validate


class ExternalMaterialPlanTests(unittest.TestCase):
    def setUp(self):
        self.entry={'object_name':'Selected','material_name':'Shared','preserve_original':True,
                    'textures':{'roughness':'D:/project/materials/roughness.jpg','normal':'D:/project/materials/normal.png'},
                    'source':'https://polyhaven.com/a/fabric_pattern_05','license':'CC0-1.0',
                    'resource':{'provider':'Poly Haven','asset_id':'fabric_pattern_05','name':'Fabric Pattern 05',
                        'url':'https://polyhaven.com/a/fabric_pattern_05','license_url':'https://polyhaven.com/license',
                        'resolution':'1k','downloaded_at':1234.5,'manifest_path':'D:/project/materials/manifest.json'},
                    'texture_scale':2,'texture_rotation':30}

    def plan(self,entry=None):
        plan=default_plan();plan['object_material_overrides']=[copy.deepcopy(entry or self.entry)]
        return plan

    def test_resource_metadata_and_maps_are_preserved_without_invented_color(self):
        plan=self.plan();result=validate(plan)
        self.assertEqual(result['object_material_overrides'],[self.entry])
        self.assertNotIn('color',result['object_material_overrides'][0])
        self.assertNotIn('base_color',result['object_material_overrides'][0]['textures'])
        result['object_material_overrides'][0]['resource']['asset_id']='other'
        self.assertEqual(plan['object_material_overrides'][0]['resource']['asset_id'],'fabric_pattern_05')

    def test_map_channels_and_paths_cannot_contain_urls_or_invalid_values(self):
        for textures in [[],{'executable':'D:/run.py'},{'normal':None},{'roughness':''},
                         {'roughness':'https://example.com/roughness.png'},{'normal':'D:/test\x00.png'}]:
            with self.subTest(textures=textures),self.assertRaises(ValueError):
                validate(self.plan({**self.entry,'textures':textures}))

    def test_metadata_must_remain_bounded_nonexecutable_data(self):
        for extra in [{'resource':{'script':'run()'}},{'resource':{'downloaded_at':float('nan')}},
                      {'resource':{'asset_id':42}},{'source':{}},{'license':'x'*257},
                      {'texture_scale':0},{'texture_scale':True},{'texture_rotation':361}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                validate(self.plan({**self.entry,**extra}))

    def test_legacy_global_import_uses_same_map_validation(self):
        plan=default_plan()
        plan['material_overrides']=[{'name':'Shared','preset':'fabric','textures':{'roughness':'D:/materials/r.jpg'}}]
        self.assertEqual(validate(plan),plan)
        plan['material_overrides'][0]['textures']['unknown']='D:/materials/secret.txt'
        with self.assertRaises(ValueError):validate(plan)


if __name__=='__main__':unittest.main()

"""Data-only grounding for model-owned intent; no language parser is called."""
import copy
import unittest
from unittest.mock import patch

import target_evidence as evidence


SOURCE = {'output_id':'source', 'shot_id':'hero', 'quality':'preview', 'version':4}


def fixture(groups=4, split=True):
    rows=[]; objects=[]
    for group in range(groups):
        for half in range(2 if split else 1):
            number=len(rows)+1; name='Mesh.%03d'%number
            bounds={'min':[group*3,0,half*.5], 'max':[group*3+1,1,(half+1)*.5]}
            rows.append({'id':'t'+str(number),'object_name':name,'material_name':'Shared',
                         'bounds':bounds,'components':[{'bounds':copy.deepcopy(bounds),'triangle_count':12}],
                         'geometry_digest':'a'*64,'thumbnail':{'page':0,'rect':[0,0,.1,.2]}})
            objects.append({'name':name,'type':'MESH','materials':['Shared']})
    catalog={'method':'scene_part_catalog','generation_version':1,'source_snapshot':copy.deepcopy(SOURCE),
             'catalog_pages':[{'label':'PART_CATALOG_1','width':1600,'height':1200}], 'targets':rows}
    return catalog, {'objects':objects,'materials':[{'name':'Shared'}]}


def binding(ids, phrase='像刚打过蜡一样的那些面', purpose='material', **extra):
    return {'phrase':phrase,'purpose':purpose,'target_ids':ids,**extra}


def local_rows(rows, **values):
    return {'object_material_overrides':[{'object_name':row['object_name'],'material_name':row['material_name'],
                                          **(values or {'roughness':.22})} for row in rows]}


class SemanticEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.catalog,self.analysis=fixture()
        self.ids=[row['id'] for row in self.catalog['targets']]

    def resolve(self, bindings=None, catalog=None, version=4):
        return evidence.resolve_semantic_bindings(bindings if bindings is not None else [binding(self.ids)],
                                                  self.catalog if catalog is None else catalog,self.analysis,version)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(evidence.SemanticEvidenceError) as caught:function(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)

    def test_arbitrary_model_label_does_not_need_keywords_or_literal_user_substring(self):
        for phrase in ('像刚打过蜡一样的那些面','the understated lustre of satin enamel','刚才讨论的外壳'):
            with self.subTest(phrase=phrase):
                rows=self.resolve([binding(self.ids,phrase)])
                self.assertEqual(rows[0]['phrase'],phrase)
                self.assertEqual(rows[0]['instance_count'],4)
                self.assertEqual(len(rows[0]['targets']),8)

    def test_neither_resolution_nor_coverage_calls_legacy_language_helpers(self):
        with patch.object(evidence,'_requested_count',side_effect=AssertionError('keyword count called')), \
             patch.object(evidence,'material_assignments',side_effect=AssertionError('language parser called')), \
             patch.object(evidence,'resolve_bindings',side_effect=AssertionError('legacy resolver called')), \
             patch.object(evidence,'validate_binding_coverage',side_effect=AssertionError('legacy coverage called')):
            resolved=self.resolve([binding(self.ids,expected_instances=4)])
            evidence.validate_semantic_binding_coverage(resolved,local_rows(self.catalog['targets']),self.analysis)

    def test_split_meshes_are_counted_by_actual_geometry_groups(self):
        resolved=self.resolve([binding(self.ids,expected_instances=4)])
        self.assertEqual(resolved[0]['expected_instances'],4)
        self.assertEqual(resolved[0]['instance_count'],4)
        self.assertEqual(resolved[0]['instance_basis'],'scene_geometry_components')
        self.assert_code('semantic_count_mismatch',self.resolve,[binding(self.ids[:4],expected_instances=4)])

    def test_single_mesh_with_separated_components_can_establish_multiple_instances(self):
        catalog,analysis=fixture(groups=1,split=False)
        row=catalog['targets'][0];row['bounds']['max'][0]=10
        row['components']=[{'bounds':{'min':[n*3,0,0],'max':[n*3+1,1,.5]},'triangle_count':12} for n in range(4)]
        resolved=evidence.resolve_semantic_bindings([binding(['t1'],expected_instances=4)],catalog,analysis,4)
        self.assertEqual(resolved[0]['instance_count'],4)

    def test_ray_samples_do_not_claim_physical_instance_counts(self):
        rays={'method':'camera_ray_samples','source_snapshot':copy.deepcopy(SOURCE),
              'targets':[{k:row[k] for k in ('id','object_name','material_name')}|{'samples':[{'x':.5,'y':.5}]}
                         for row in self.catalog['targets']]}
        resolved=self.resolve(catalog=rays)
        self.assertNotIn('instance_count',resolved[0])
        self.assert_code('semantic_count_unverified',self.resolve,[binding(self.ids,expected_instances=4)],rays)

    def test_current_version_and_analysis_identity_are_required(self):
        self.assert_code('semantic_evidence_missing',self.resolve,version=5)
        changed=copy.deepcopy(self.catalog);changed['targets'][0]['material_name']='Forged'
        self.assert_code('semantic_evidence_missing',self.resolve,catalog=changed)

    def test_unknown_and_repeated_ids_or_binding_fields_are_rejected(self):
        for bindings in ([binding(['t999'])],[binding(['t1','t1'])],
                         [binding(['t1']),binding(['t1'])],
                         [binding(['t1'],script='bad')]):
            with self.subTest(bindings=bindings):
                self.assert_code('semantic_binding_invalid',self.resolve,bindings)

    def test_expected_count_is_structural_integer_not_coerced_text(self):
        for count in (None,True,0,-1,'4',4.0,10001):
            with self.subTest(count=count):
                self.assert_code('semantic_binding_invalid',self.resolve,[binding(self.ids,expected_instances=count)])

    def test_local_patch_must_cover_all_bound_pairs_exactly(self):
        resolved=self.resolve()
        evidence.validate_semantic_binding_coverage(resolved,local_rows(self.catalog['targets']),self.analysis)
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,
                         resolved,local_rows(self.catalog['targets'][:-1]),self.analysis)
        selected=self.resolve([binding(['t1'])])
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,
                         selected,local_rows(self.catalog['targets'][:2]),self.analysis)

    def test_global_material_change_requires_all_known_users_to_be_bound(self):
        global_patch={'material_overrides':[{'name':'Shared','roughness':.22}]}
        evidence.validate_semantic_binding_coverage(self.resolve(),global_patch,self.analysis)
        selected=self.resolve([binding(['t1'])])
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,
                         selected,global_patch,self.analysis)
        outside=copy.deepcopy(self.analysis);outside['objects'].append({'name':'Unpictured','type':'MESH','materials':['Shared']})
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,
                         self.resolve(),global_patch,outside)

    def test_identity_only_or_duplicate_material_rows_cannot_count_as_edits(self):
        resolved=self.resolve([binding(['t1'])]);row=self.catalog['targets'][0]
        empty={'object_material_overrides':[{k:row[k] for k in ('object_name','material_name')}]}
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,resolved,empty,self.analysis)
        repeated=local_rows([row,row])
        self.assert_code('semantic_binding_invalid',evidence.validate_semantic_binding_coverage,resolved,repeated,self.analysis)

    def test_camera_target_cannot_widen_outside_grounded_object(self):
        resolved=self.resolve([binding(['t1'],purpose='camera')])
        valid={'shots':[{'id':'hero','target_object':'Mesh.001'}]}
        evidence.validate_semantic_binding_coverage(resolved,valid,self.analysis)
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,
                         resolved,{'shots':[{'id':'hero','target_object':'Mesh.002'}]},self.analysis)
        self.assert_code('semantic_binding_coverage',evidence.validate_semantic_binding_coverage,[],valid,self.analysis)

    def test_lighting_only_patch_needs_no_part_binding(self):
        self.assertEqual(evidence.resolve_semantic_bindings([],{},self.analysis,4),[])
        evidence.validate_semantic_binding_coverage([],{'lighting':{'key_power':1.5}},self.analysis)

    def test_resolution_does_not_mutate_evidence_or_model_response(self):
        raw=[binding(self.ids,expected_instances=4)]
        original=(copy.deepcopy(raw),copy.deepcopy(self.catalog),copy.deepcopy(self.analysis))
        resolved=self.resolve(raw);resolved[0]['targets'][0]['object_name']='changed after resolve'
        self.assertEqual((raw,self.catalog,self.analysis),original)


if __name__=='__main__':unittest.main()

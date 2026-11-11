import copy
import tempfile
import unittest
from pathlib import Path
from asset_provenance import enrich_project


class ProvenanceTests(unittest.TestCase):
    def test_only_explicit_placeholder_metadata_is_corrected_without_touching_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'abc'/'originals';folder.mkdir(parents=True)
            asset=folder/'cabinet.obj';asset.write_text('mtllib cabinet.mtl\nv 0 0 0\n',encoding='utf8')
            (folder/'cabinet.mtl').write_text('# Fallback materials only; original MTL was not supplied.\nnewmtl Metal\nnewmtl Custom\n',encoding='utf8')
            p={'id':'abc','model_path':str(asset),'plan':{'light_softness':1},'analysis':{'materials':[{'name':'Metal','source':'model','confidence':1,'suggested_preset':'matte_plastic'},{'name':'Custom','source':'user','confidence':1}],'warnings':[]}}
            old=copy.deepcopy(p['plan']);enrich_project(p,root)
            self.assertEqual(p['analysis']['materials'][0]['source'],'fallback')
            self.assertIsNone(p['analysis']['materials'][0]['suggested_preset'])
            self.assertEqual(p['analysis']['materials'][1]['source'],'user')
            self.assertEqual(p['plan'],old)
            enrich_project(p,root);self.assertEqual(len(p['analysis']['warnings']),1)
            (folder/'cabinet.mtl').write_text('# supplied by artist\nnewmtl Metal\n',encoding='utf8')
            untouched={'id':'abc','model_path':str(asset),'analysis':{'materials':[{'name':'Metal','source':'model'}]}}
            self.assertEqual(enrich_project(untouched,root)['analysis']['materials'][0]['source'],'model')

    def test_outside_asset_ignored(self):
        p={'id':'other','model_path':__file__,'analysis':{'materials':[]}}
        self.assertEqual(enrich_project(p,Path(__file__).parent),p)

if __name__=='__main__': unittest.main()

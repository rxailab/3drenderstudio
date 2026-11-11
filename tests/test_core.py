import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path
from app import app, safe_path, extract_archive, check_resources
from planner import default_plan, validate, instruct

class CoreTests(unittest.TestCase):
    def test_plan_rejects_nonfinite_and_extreme(self):
        for value in [float('nan'),float('inf'),-1,2]:
            p=default_plan();p['margin']=value
            with self.assertRaises(ValueError): validate(p)

    @patch('ai_config.get_config',return_value={'enabled':False,'api_key':'','model':''})
    def test_natural_language_is_replayable(self,_config):
        p=default_plan();n=instruct(p,'反光柔和一点，增加留白，换成俯拍')
        self.assertEqual(p['margin'],.12)
        self.assertGreater(n['margin'],p['margin'])
        self.assertGreater(n['light_softness'],p['light_softness'])
        self.assertEqual(n['shots'][0]['elevation'],80)
        self.assertEqual(n['source'],'rules')

    def test_archive_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            for bad in ['../outside.obj','C:/outside.obj','/outside.obj','a/../../outside.obj']:
                with self.assertRaises(ValueError): safe_path(base,bad)
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:z.writestr('../outside.obj','bad')
            buf.seek(0)
            with self.assertRaises(ValueError): extract_archive(buf,base)

    def test_mtl_relative_nested_resources(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);(base/'models').mkdir();(base/'textures').mkdir()
            (base/'models/a.mtl').write_text('map_Kd ../textures/a.png')
            check_resources(base)
            (base/'models/a.mtl').write_text('map_Kd ../../outside.png')
            with self.assertRaises(ValueError):check_resources(base)

    def test_local_origin(self):
        c=app.test_client()
        self.assertEqual(c.get('/api/system').status_code,200)
        self.assertEqual(c.post('/api/projects',headers={'Origin':'https://evil.example'}).status_code,403)

if __name__=='__main__':unittest.main()

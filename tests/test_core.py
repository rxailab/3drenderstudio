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
    def test_bound_render_cache_requires_actual_shot_exposure(self):
        from engine import viewport_exposure_matches
        plan={'viewport_backdrop':'silver-beam','exposure':1.6,'shot_adjustments':{'detail':{'exposure_delta':-.6}}}
        stale={'studio':{'exposure':0},'effective_settings':{'exposure':1.6}}
        self.assertFalse(viewport_exposure_matches(stale,plan,'hero'))
        self.assertFalse(viewport_exposure_matches({},plan,'hero'))
        self.assertTrue(viewport_exposure_matches({'studio':{'exposure':1.60000002}},plan,'hero'))
        self.assertTrue(viewport_exposure_matches({'studio':{'exposure':1}},plan,'detail'))
    def test_viewport_backdrop_must_identify_a_local_studio_preset(self):
        from studio_backdrops import catalog
        for preset in catalog():
            self.assertEqual(validate({**default_plan(),'viewport_backdrop':preset})['viewport_backdrop'],preset)
        for invalid in (None,{},'../other','https://example.com/environment.hdr'):
            with self.assertRaises(ValueError):
                validate({**default_plan(),'viewport_backdrop':invalid})

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

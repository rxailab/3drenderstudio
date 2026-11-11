import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import app as web
from planner import default_plan


class MaterialRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root/'fixture'/'materials'/'resource'
        self.folder.mkdir(parents=True)
        self.texture = self.folder/'roughness.png'
        self.texture.write_bytes(b'owned texture fixture')
        (self.folder/'manifest.json').write_text(json.dumps({'license':'CC0','provider':'Poly Haven'}))
        self.plan = default_plan()
        self.plan['object_material_overrides'] = [{'object_name':'Body','material_name':'Paint',
            'preserve_original':True,'textures':{'roughness':str(self.texture)}}]
        self.data = patch.object(web,'DATA',self.root)
        self.data.start()

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def test_local_download_paths_are_confined_to_current_project(self):
        web.validate_project_plan('fixture',self.plan)
        outside=self.root/'other.png';outside.write_bytes(b'not owned')
        self.plan['object_material_overrides'][0]['textures']['roughness']=str(outside)
        with self.assertRaisesRegex(ValueError,'当前项目'):
            web.validate_project_plan('fixture',self.plan)

    def test_bundle_keeps_maps_and_license_manifest_once(self):
        project={'id':'fixture','slug':'fixture','outputs':[],'plan':self.plan}
        # Repeated use of the same texture should not produce ambiguous zip entries.
        self.plan['object_material_overrides'].append(dict(self.plan['object_material_overrides'][0]))
        task={'id':'material-task','project_id':'fixture','status':'completed'}
        with patch.object(web,'get',return_value=project),patch.object(web,'all_records',side_effect=lambda kind:[task] if kind=='material_task' else []), \
             patch.object(web.project_lifecycle.store,'get',return_value=project):
            response=web.app.test_client().get('/api/projects/fixture/bundle')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            names=archive.namelist()
            self.assertEqual(names.count('materials/resource/roughness.png'),1)
            self.assertIn('materials/resource/manifest.json',names)
            self.assertIn('material-tasks/material-task.json',names)
            self.assertEqual(archive.read('materials/resource/roughness.png'),self.texture.read_bytes())


if __name__=='__main__': unittest.main()

"""The realtime viewer can fetch its generated model, without exposing uploads."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as web


class ViewportFileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.project='a'*32
        self.digest='b'*64
        self.folder=self.root/self.project/'viewport'/self.digest
        self.folder.mkdir(parents=True)
        (self.folder/'scene.glb').write_bytes(b'glTF-test-asset')
        (self.folder/'other.glb').write_bytes(b'other')
        original=self.root/self.project/'originals'
        original.mkdir()
        (original/'product.glb').write_bytes(b'original')
        self.data_patch=patch.object(web,'DATA',self.root)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.client=web.app.test_client()

    def test_generated_viewport_asset_is_available_with_cache_query(self):
        response=self.client.get(f'/files/{self.project}/viewport/{self.digest}/scene.glb?v=abcdef')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,b'glTF-test-asset')

    def test_arbitrary_or_uploaded_glbs_are_not_exposed(self):
        for route in (f'/files/{self.project}/originals/product.glb',
                      f'/files/{self.project}/viewport/{self.digest}/other.glb',
                      f'/files/{self.project}/viewport/not-a-digest/scene.glb',
                      f'/files/{self.project}/viewport/{self.digest}/../scene.glb'):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code,404)


if __name__=='__main__': unittest.main()

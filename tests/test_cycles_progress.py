import unittest

from engine import cycles_sample_batch, CyclesTileProgress


class CyclesProgressTests(unittest.TestCase):
    def test_only_completed_batch_not_planning_or_duplicate_summary(self):
        self.assertEqual(cycles_sample_batch('00:06.328  cycles           | Rendered 1 samples in 0.02 seconds (0.02 seconds per sample), occupancy: 0.06'), 1)
        self.assertEqual(cycles_sample_batch('00:06.562  cycles           | Rendered 23 samples in 0.23 seconds (0.01 seconds per sample), occupancy: 0.42'), 23)
        self.assertIsNone(cycles_sample_batch('00:06.328  cycles           | Will path trace 23 samples at the resolution divider 1'))
        self.assertIsNone(cycles_sample_batch('                            | Rendered 24 samples in 2.336498 seconds'))
        self.assertIsNone(cycles_sample_batch('EVENT:{"stage":"正在渲染"}'))

    def test_tiled_render_waits_for_confirmed_writes_and_deduplicates(self):
        tiles=CyclesTileProgress();tiles.begin(4096,4096)
        self.assertIsNone(tiles.consume('00:03.750 cycles | Using tile size of (2048, 2048)'))
        self.assertEqual(tiles.total,4)
        tiles.consume('cycles | Write tile at 0, 0')
        first=tiles.consume('cycles | Tile written in 0.4 seconds.')
        self.assertEqual((first['tile_completed'],first['tile_total'],first['tile_progress'],first['sample_progress']),(1,4,25.0,None))
        tiles.consume('cycles | Write tile at 0, 0')
        self.assertEqual(tiles.consume('cycles | Tile written in 0.4 seconds.')['tile_completed'],1)
        for xy in ('2048, 0','0, 2048','2048, 2048'):
            tiles.consume('cycles | Write tile at '+xy)
            final=tiles.consume('cycles | Tile written in 0.4 seconds.')
        self.assertEqual(final['tile_progress'],100)
        self.assertEqual(final['stage'],'正在完成渲染与保存')
        tiles.begin(512,512)
        self.assertIsNone(tiles.total)
        self.assertFalse(tiles.completed)

    def test_missing_dimensions_do_not_invent_tile_count(self):
        tiles=CyclesTileProgress();tiles.begin(None,None)
        tiles.consume('cycles | Using tile size of (2048, 2048)')
        self.assertIsNone(tiles.total)


if __name__ == '__main__':
    unittest.main()

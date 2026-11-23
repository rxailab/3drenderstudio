"""A wall-mounted product is known as one, seen from its front, and from about level.

From a user's pictures: a wall cabinet stood on a van floor with its keyhole back plate to the camera, and the same
cabinet redrawn at another size when the image model hung it on a wall itself.
"""
import unittest
from unittest.mock import patch

import mounting
import scene_pose
import scene_stylist


def cabinet(**changes):
    project = {'name': 'Tool Cabinet', 'metadata': {'product_category': ''}, 'orientation': {},
               'analysis': {'objects': [{'name': 'Body'}, {'name': 'Door_L'}, {'name': 'Door_R'}]}}
    project.update(changes)
    return project


class MountingTests(unittest.TestCase):
    def test_wall_mounting_is_read_from_the_name_the_category_the_vision_note_and_the_parts(self):
        for project in (cabinet(name='Wall-Mounted Tool Cabinet'), cabinet(name='壁挂工具柜'), cabinet(name='挂墙工具箱'),
                        cabinet(metadata={'product_category': 'wall cabinet'}), cabinet(name='Key box that mounts on the wall'),
                        cabinet(orientation={'product': 'wall-mounted storage cabinet'}),
                        cabinet(analysis={'objects': [{'name': 'Body'}, {'name': 'Keyhole_slot.001'}]}),
                        cabinet(analysis={'objects': [{'name': 'Wall_bracket'}]})):
            with self.subTest(project=project['name'], category=project['metadata'], parts=project['analysis']):
                self.assertEqual(mounting.of(project), 'wall')
        self.assertEqual(mounting.of(cabinet(), hint='hang it on the wall of the garage'), None)       # a hint is not a mounting...
        self.assertEqual(mounting.of(cabinet(), hint='wall-mounted'), 'wall')                          # ...unless it says so

    def test_products_that_stand_or_are_held_are_not_wall_mounted_and_the_user_has_the_last_word(self):
        for name in ('Service Cart', 'Combination Pliers', 'Rolling Tool Chest', 'Cordless Angle Grinder', 'Drywall saw', 'Drywall Box Cutter',
                     'Drywall Rack', 'Network Firewall Box', 'Freestanding cabinet, no wall mounting needed', '落地工具柜'):
            self.assertIsNone(mounting.of(cabinet(name=name)), name)
        # a lock's keyhole or a caster's mounting hole does not hang a chest on a wall
        for part in ('Lock_Keyhole', 'mounting_hole_1', 'Drywall_mount'):
            self.assertIsNone(mounting.of(cabinet(name='Rolling Tool Chest', analysis={'objects': [{'name': part}]})), part)
        self.assertEqual(mounting.of(cabinet(name='壁掛工具櫃')), 'wall')                               # Traditional Chinese too
        self.assertIsNone(mounting.of(cabinet(name='Wall-Mounted Tool Cabinet', metadata={'mounting': 'free'})))
        self.assertEqual(mounting.of(cabinet(name='Storage box', metadata={'mounting': 'wall'})), 'wall')


class RulesPathTests(unittest.TestCase):
    def test_without_the_strategist_a_wall_cabinet_gets_its_wall_and_no_hands_it_did_not_ask_for(self):
        import scene_audience
        project = {'id': 'p', 'version': 1, 'name': 'Wall-Mounted Tool Cabinet', 'metadata': {}, 'analysis': {'objects': []},
                   'plan': {'shots': [{'id': 'hero', 'azimuth': 35, 'elevation': 24}], 'dimensions_mm': [850, 220, 480]}}
        with patch.object(scene_stylist, '_config', return_value=None), patch.object(scene_stylist, 'image_model_ready', return_value=True, create=True):
            proposal = scene_stylist.propose(project, '', 'auto', None, None, 'auto')
        self.assertEqual(proposal['mounting'], 'wall'); self.assertFalse(proposal['complex'])
        self.assertNotEqual(proposal['segment_id'], 'mobile_tradesperson')
        if proposal.get('scene'):
            self.assertIn('wall', proposal['scene'])
        self.assertFalse(proposal.get('people'))


class FrontTests(unittest.TestCase):
    def test_a_pose_that_turns_the_front_away_from_the_camera_is_never_offered(self):
        base = [0.0, 0.0, 0.0]
        for azimuth in (-35, 0, 35):
            kept = [row[0] for row in scene_pose.facing_camera(scene_pose.first_poses(base, azimuth), base, azimuth)]
            self.assertIn('as_is@0', kept)
            for ident, _, rotation in scene_pose.first_poses(base, azimuth):
                facing = scene_pose.facing(base, rotation, azimuth)
                self.assertEqual(ident in kept, ident == 'as_is@0' or facing >= scene_pose.FACING, (azimuth, ident, facing))
        # turned round, the back plate is toward the camera: never kept
        self.assertLess(scene_pose.facing(base, [0, 0, 180], 35), 0)
        self.assertEqual(scene_pose.facing_camera([('turned_round', 'x', [0, 0, 180])], base, 35), [])

    def test_a_product_with_doors_drawers_or_a_wall_mounting_has_a_front(self):
        self.assertTrue(scene_pose.fronted(cabinet()))
        self.assertTrue(scene_pose.fronted(cabinet(name='Wall-Mounted Box', analysis={'objects': [{'name': 'Shell'}]})))
        self.assertFalse(scene_pose.fronted(cabinet(name='Pliers', analysis={'objects': [{'name': 'Jaw'}, {'name': 'Handle'}]})))


class ViewTests(unittest.TestCase):
    def test_a_wall_product_is_seen_from_in_front_and_about_level(self):
        plan = {'shots': [{'id': 'hero', 'azimuth': 35, 'elevation': 24}]}
        with patch.object(scene_stylist, '_hero', return_value={'azimuth': 35, 'elevation': 24}):
            self.assertEqual(scene_stylist._view(plan, 65, 40, 'wall'), (40.0, 12.0))
            self.assertEqual(scene_stylist._view(plan, -10, 5, 'wall'), (-10.0, 5.0))
            self.assertEqual(scene_stylist._view(plan, 65, 40), (65.0, 40.0))          # a product set down: as before


if __name__ == '__main__':
    unittest.main()

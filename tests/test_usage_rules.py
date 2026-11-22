"""The written-down rules for holding and using common hand tools: which tool a name means, and how they merge with the agent's own."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import articulation
import usage_rules


class KindTests(unittest.TestCase):
    def test_a_tool_is_known_by_its_english_or_chinese_name_and_the_specific_kind_wins(self):
        self.assertEqual(usage_rules.kinds('06_diagonal_cutting_pliers'), ['cutters'])        # cutters cover pliers
        self.assertEqual(usage_rules.kinds('斜口钳'), ['cutters'])
        self.assertEqual(usage_rules.kinds('combination pliers'), ['pliers'])
        self.assertEqual(usage_rules.kinds('11_claw_hammer'), ['hammer'])
        self.assertEqual(usage_rules.kinds('羊角锤'), ['hammer'])
        self.assertEqual(usage_rules.kinds('15_hand_saw'), ['saw'])
        self.assertEqual(usage_rules.kinds('wood handle screwdriver'), ['screwdriver'])
        self.assertEqual(usage_rules.kinds('数显卡尺'), ['caliper'])
        self.assertEqual(usage_rules.kinds('tape measure 25ft'), ['tape'])

    def test_a_word_that_belongs_to_another_tool_does_not_count(self):
        self.assertEqual(usage_rules.kinds('SDS hammer drill'), ['drill'])                  # a drill, not a hammer
        self.assertEqual(usage_rules.kinds('电锤'), [])
        self.assertEqual(usage_rules.kinds('pipe cutter'), [])                              # not cutting pliers
        self.assertEqual(usage_rules.kinds('cordless impact driver'), ['drill'])            # not a screwdriver
        self.assertEqual(usage_rules.kinds('锂电起子机'), ['drill'])
        self.assertEqual(usage_rules.kinds('jigsaw'), [])
        self.assertEqual(usage_rules.kinds('rolling tool cabinet', 'sawdust'), [])          # whole words only
        self.assertEqual(usage_rules.kinds(''), [])


class RuleTests(unittest.TestCase):
    def test_the_rules_are_concrete_about_the_hand_and_the_work(self):
        cutters = usage_rules.canonical('diagonal cutting pliers')
        self.assertIn('no finger pinches the head, the jaws or the pivot', cutters[0])
        self.assertIn('deep in the jaws close to the pivot, square to the cutting edges', cutters[1])
        hammer = ' '.join(usage_rules.canonical('claw hammer'))
        for words in ('lower half of the handle', 'strikes the nail head squarely', 'perpendicular to the surface',
                      'nail shank sits deep in the claw\'s V-slot', 'handle is levered back'):
            self.assertIn(words, hammer)
        self.assertIn('teeth sit on the cut line', ' '.join(usage_rules.canonical('hand saw')))
        self.assertIn('seated in the screw head', ' '.join(usage_rules.canonical('screwdriver')))
        self.assertIn('hook is caught on the edge', ' '.join(usage_rules.canonical('tape measure')))
        self.assertIn('jaws close squarely on both faces', ' '.join(usage_rules.canonical('caliper')))
        self.assertIn('不捏钳头', usage_rules.canonical('斜口钳', language='zh')[0])
        # Every kind has as many Chinese rules as English ones, and none is cut short where rules are passed on.
        for kind, _, _, _, en, zh in usage_rules.KINDS:
            self.assertEqual(len(en), len(zh), kind)
            self.assertTrue(all(len(rule) <= usage_rules.LENGTH for rule in en + zh), kind)

    def test_the_written_rules_come_first_then_the_agents_own_without_repeats_and_within_the_limit(self):
        own = ['Grip the wire in the jaws.', 'grip the wire in the jaws', 'handles in the palm']
        rules = usage_rules.merged(['diagonal cutting pliers'], own)
        self.assertEqual(len(rules), 5)
        self.assertEqual(rules[3:], ['Grip the wire in the jaws', 'handles in the palm'])
        self.assertEqual(len(usage_rules.merged('claw hammer', ['a %d' % n for n in range(9)])), usage_rules.LIMIT)
        self.assertEqual(usage_rules.merged('rolling tool cabinet', ['lid opens upward']), ['lid opens upward'])
        self.assertEqual(usage_rules.merged('rolling tool cabinet', None), [])

    def test_every_name_of_the_product_is_read(self):
        project = {'name': '我的产品', 'model_path': '/x/06_diagonal_cutting_pliers.glb', 'metadata': {'product_category': '手动工具'},
                   'articulation': {'product_kind': 'side cutters'}}
        words = usage_rules.words_for(project, 'cutter')
        self.assertEqual(words, ['cutter', '我的产品', '手动工具', 'side cutters', '06_diagonal_cutting_pliers'])
        self.assertEqual(usage_rules.kinds(*words), ['cutters'])
        self.assertEqual(usage_rules.words_for(None, 'hammer'), ['hammer'])

    def test_the_pose_agent_is_asked_for_rules_of_the_same_concreteness(self):
        for words in ('never just "properly" or "correctly"', 'never pinched at the head or the pivot', 'the lower half of the handle',
                      'the wire deep in the jaws near the pivot, never at the tips', 'nail shank seated in the claw\'s slot'):
            self.assertIn(words, articulation.ARTICULATOR)


if __name__ == '__main__':
    unittest.main()

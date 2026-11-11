"""Actual material property semantics; no config, models or renderer used."""
import copy
import unittest

import material_intent as intent
from intelligence_rules import InstructionOutcome


TEXT = '上表面是反光的拉丝不锈钢，箱体是红色金属有光泽'
TOP = {'object_name': 'pasted__pCylinder19', 'material_name': 'Shared'}
BODY = {'object_name': 'pCube31', 'material_name': 'Shared'}
BINDINGS = [
    {'phrase': '上表面', 'purpose': 'material', 'targets': [TOP]},
    {'phrase': '箱体', 'purpose': 'material', 'targets': [BODY]},
]


def patch():
    return {'object_material_overrides': [dict(TOP, preset='brushed_metal'),
                                          dict(BODY, preset='painted_metal', color='#B93832')]}


class MaterialIntentTests(unittest.TestCase):
    def test_exact_screenshot_request_completes_existing_finish_defaults(self):
        original = patch(); before = copy.deepcopy(original)
        result = intent.complete_missing(original, TEXT, BINDINGS)
        self.assertEqual([entry['roughness'] for entry in result['object_material_overrides']], [.3, .28])
        self.assertEqual(original, before)
        self.assertEqual(set(result), {'object_material_overrides'})
        self.assertEqual([(entry['object_name'], entry['material_name']) for entry in result['object_material_overrides']],
                         [(TOP['object_name'], 'Shared'), (BODY['object_name'], 'Shared')])
        intent.validate(result, TEXT, BINDINGS)

    def test_existing_matte_scalars_are_not_inherited_for_explicit_gloss(self):
        plan = patch()
        for entry in plan['object_material_overrides']: entry['roughness'] = .95
        result = intent.complete_missing(patch(), TEXT, BINDINGS, plan=plan)
        self.assertEqual([entry['roughness'] for entry in result['object_material_overrides']], [.3, .28])
        self.assertTrue(all(entry['roughness'] == .95 for entry in plan['object_material_overrides']))
        intent.validate(result, TEXT, BINDINGS, plan=plan)

    def test_plan_preset_can_supply_default_without_replacing_preserved_color(self):
        plan = {'object_material_overrides': [dict(BODY, preset='painted_metal', color='#B93832', roughness=.95)]}
        value = {'object_material_overrides': [dict(BODY)]}
        result = intent.complete_missing(value, '箱体是红色金属有光泽', BINDINGS, ['preserve_color'], plan)
        self.assertEqual(result['object_material_overrides'][0], dict(BODY, roughness=.28))
        intent.validate(result, '箱体是红色金属有光泽', BINDINGS, ['preserve_color'], plan)

    def test_explicit_valid_patch_finish_is_preserved(self):
        value = patch()
        value['object_material_overrides'][0]['roughness'] = .4
        value['object_material_overrides'][1]['roughness'] = .2
        result = intent.complete_missing(value, TEXT, BINDINGS)
        self.assertEqual(result, value)
        intent.validate(result, TEXT, BINDINGS)

    def test_explicit_matte_patch_requires_correction_not_silent_replacement(self):
        for number in (.9, 1):
            with self.subTest(number=number):
                value = patch(); value['object_material_overrides'][1]['roughness'] = number
                result = intent.complete_missing(value, TEXT, BINDINGS)
                self.assertEqual(result['object_material_overrides'][1]['roughness'], number)
                with self.assertRaisesRegex(intent.MaterialIntentError, '箱体.*粗糙度') as caught:
                    intent.validate(result, TEXT, BINDINGS)
                self.assertEqual(caught.exception.code, 'material_property_conflict')

    def test_explicit_user_number_is_authoritative_over_general_finish_wording(self):
        text = '箱体是红色金属有光泽粗糙度设为0.75'
        value = {'object_material_overrides': [dict(BODY, preset='painted_metal', color='#B93832')]}
        result = intent.complete_missing(value, text, BINDINGS)
        self.assertEqual(result['object_material_overrides'][0]['roughness'], .75)
        intent.validate(result, text, BINDINGS)

    def test_explicit_numeric_conflict_is_not_overwritten(self):
        text = '箱体是红色金属有光泽粗糙度设为0.35'
        value = {'object_material_overrides': [dict(BODY, preset='painted_metal', color='#B93832', roughness=.2)]}
        result = intent.complete_missing(value, text, BINDINGS)
        self.assertEqual(result, value)
        with self.assertRaisesRegex(intent.MaterialIntentError, '明确数值'):
            intent.validate(result, text, BINDINGS)

    def test_percentage_and_out_of_range_numeric_values(self):
        value = {'object_material_overrides': [dict(BODY, preset='painted_metal', color='#B93832')]}
        result = intent.complete_missing(value, '箱体是红色金属粗糙度设为25%', BINDINGS)
        self.assertEqual(result['object_material_overrides'][0]['roughness'], .25)
        for text in ('箱体是红色金属粗糙度设为2', '箱体是红色金属粗糙度设为0.2粗糙度改为0.4'):
            with self.subTest(text=text), self.assertRaises(intent.MaterialIntentError) as caught:
                intent.complete_missing(value, text, BINDINGS)
            self.assertEqual(caught.exception.code, 'material_user_constraint')

    def test_blue_white_black_and_green_cannot_satisfy_red_body(self):
        for color in ('#174DCF', '#FFFFFF', '#000000', '#257C36'):
            with self.subTest(color=color):
                value = patch(); value['object_material_overrides'][1]['color'] = color
                result = intent.complete_missing(value, TEXT, BINDINGS)
                self.assertEqual(result['object_material_overrides'][1]['color'], color)
                with self.assertRaisesRegex(intent.MaterialIntentError, '箱体.*红色'):
                    intent.validate(result, TEXT, BINDINGS)

    def test_red_family_permits_warm_dark_and_muted_red_finishes(self):
        for color in ('#B93832', '#C8322A', '#702332', '#B9583A', '#9A1347'):
            with self.subTest(color=color):
                value = patch(); value['object_material_overrides'][1]['color'] = color
                intent.validate(intent.complete_missing(value, TEXT, BINDINGS), TEXT, BINDINGS)

    def test_plain_material_assignment_does_not_invent_gloss_requirement(self):
        text = '上表面是拉丝不锈钢，箱体是红色金属'
        result = intent.complete_missing(patch(), text, BINDINGS)
        self.assertTrue(all('roughness' not in entry for entry in result['object_material_overrides']))
        intent.validate(result, text, BINDINGS)

    def test_negative_finish_does_not_receive_gloss_defaults(self):
        for value in ('哑光拉丝不锈钢', '不反光的拉丝不锈钢', '没有光泽的拉丝不锈钢',
                      '不需要反光的拉丝不锈钢', '无需反光的拉丝不锈钢', '不用反光的拉丝不锈钢'):
            with self.subTest(value=value):
                text = '上表面是' + value
                material = {'object_material_overrides': [dict(TOP, preset='brushed_metal')]}
                self.assertEqual(intent.complete_missing(material, text, BINDINGS), material)

    def test_negative_red_is_not_a_positive_color_requirement(self):
        for value in ('不要红色金属', '不需要红色金属', '无需红色金属'):
            with self.subTest(value=value):
                self.assertFalse(intent._requests_red(value))
                material = {'object_material_overrides': [dict(BODY, preset='painted_metal', color='#174DCF')]}
                intent.validate(material, '箱体是' + value, BINDINGS)

    def test_completion_does_not_expand_to_shared_global_or_unrelated_local_material(self):
        value = patch()
        value['material_overrides'] = [{'name': 'Shared', 'preset': 'painted_metal', 'roughness': .9}]
        value['object_material_overrides'].append({'object_name': 'Wheel91', 'material_name': 'Shared',
                                                  'preset': 'rubber', 'color': '#151515', 'roughness': .8})
        before = copy.deepcopy(value)
        result = intent.complete_missing(value, TEXT, BINDINGS)
        self.assertEqual(result['material_overrides'], before['material_overrides'])
        self.assertEqual(result['object_material_overrides'][2], before['object_material_overrides'][2])
        self.assertEqual(len(result['object_material_overrides']), 3)

    def test_missing_grounding_is_not_filled_by_guessing_existing_entries(self):
        result = intent.complete_missing(patch(), TEXT, [])
        self.assertEqual(result, patch())
        with self.assertRaisesRegex(intent.MaterialIntentError, '已定位部位') as caught:
            intent.validate(result, TEXT, [])
        self.assertEqual(caught.exception.code, 'material_target_unresolved')

    def test_material_and_only_lighting_protections_prevent_completion(self):
        for protections in (['preserve_material'], ['only_lighting']):
            value = patch(); original = copy.deepcopy(value)
            with self.subTest(protections=protections), self.assertRaises(InstructionOutcome):
                intent.complete_missing(value, TEXT, BINDINGS, protections)
            self.assertEqual(value, original)

    def test_color_protection_does_not_allow_a_new_preset_or_color(self):
        with self.assertRaises(InstructionOutcome):
            intent.complete_missing(patch(), TEXT, BINDINGS, ['preserve_color'])

    def test_explicit_global_request_stays_on_existing_named_global_entry(self):
        text = '共用材质Shared是红色金属有光泽'
        value = {'material_overrides': [{'name': 'Shared', 'preset': 'painted_metal', 'color': '#B93832'}]}
        result = intent.complete_missing(value, text, [])
        self.assertEqual(result['material_overrides'][0]['roughness'], .28)
        self.assertNotIn('object_material_overrides', result)
        intent.validate(result, text, [])


if __name__ == '__main__':
    unittest.main()

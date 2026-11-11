import copy
import unittest

import engine
from planner import default_plan,validate,LIGHTING_DEFAULTS,LIGHT_DIRECTION_LIMITS


class LightingControlsTests(unittest.TestCase):
    def test_defaults_and_legacy_plans_keep_existing_lighting(self):
        plan=default_plan()
        self.assertEqual(plan['lighting'],LIGHTING_DEFAULTS)
        self.assertEqual(validate(plan),plan)
        old=copy.deepcopy(plan);old.pop('lighting')
        self.assertEqual(validate(old),old)
        self.assertNotIn('lighting',engine.effective_plan(old,'hero'))

    def test_allowed_boundaries(self):
        plan=default_plan()
        plan['lighting']={'key_power':.1,'fill_power':3,'rim_power':.1,'environment':0}
        plan['shot_adjustments']={'hero':{'lighting':{'environment':3,'key_power':3}}}
        self.assertEqual(validate(plan),plan)

    def test_rejects_invalid_global_and_shot_lighting(self):
        bad=[None,[],{'fill_power':0},{'rim_power':3.01},{'key_power':True},
             {'environment':-.01},{'environment':float('nan')},{'environment':float('inf')},
             {'key_power':'1'},{'script':'anything'}]
        for value in bad:
            for per_shot in (False,True):
                with self.subTest(value=value,per_shot=per_shot):
                    plan=default_plan()
                    if per_shot:plan['shot_adjustments']={'hero':{'lighting':value}}
                    else:plan['lighting']=value
                    with self.assertRaises(ValueError):validate(plan)

    def test_per_shot_is_absolute_override_not_repeated_multiplier(self):
        plan=default_plan();plan['lighting']={'fill_power':.8,'key_power':1.5}
        plan['shot_adjustments']={'hero':{'lighting':{'fill_power':.4,'environment':0},'exposure_delta':-.3}}
        original=copy.deepcopy(plan)
        hero=engine.effective_plan(plan,'hero')
        self.assertEqual(hero['lighting'],{'fill_power':.4,'key_power':1.5,'environment':0})
        self.assertEqual(engine.effective_plan(hero,'hero'),hero)
        self.assertEqual(engine.effective_plan(plan,'campaign')['lighting'],plan['lighting'])
        self.assertEqual(plan,original)
        self.assertEqual(hero['shot_adjustments'],{})

    def test_legacy_plan_can_have_a_local_lighting_override(self):
        plan=default_plan();plan.pop('lighting')
        plan['shot_adjustments']={'hero':{'lighting':{'fill_power':.5}}}
        validate(plan)
        self.assertEqual(engine.effective_plan(plan,'hero')['lighting'],{'fill_power':.5})
        self.assertNotIn('lighting',engine.effective_plan(plan,'campaign'))

    def test_defaults_are_not_shared_between_projects(self):
        first=default_plan();first['lighting']['fill_power']=.5
        self.assertEqual(default_plan()['lighting']['fill_power'],1)

    def test_direction_bounds_have_no_invented_defaults(self):
        original=default_plan()
        self.assertTrue(set(LIGHT_DIRECTION_LIMITS).isdisjoint(original['lighting']))
        for name,(low,high) in LIGHT_DIRECTION_LIMITS.items():
            for value in (low,high):
                for local in (False,True):
                    with self.subTest(name=name,value=value,local=local):
                        plan=copy.deepcopy(original)
                        if local:plan['shot_adjustments']={'hero':{'lighting':{name:value}}}
                        else:plan['lighting'][name]=value
                        self.assertEqual(validate(plan),plan)
            for value in (low-.01,high+.01,True,float('nan'),float('inf'),'30',None):
                for local in (False,True):
                    with self.subTest(name=name,value=value,local=local):
                        plan=copy.deepcopy(original)
                        if local:plan['shot_adjustments']={'hero':{'lighting':{name:value}}}
                        else:plan['lighting'][name]=value
                        with self.assertRaises(ValueError):validate(plan)

    def test_effective_direction_is_absolute_and_does_not_move_other_shots(self):
        plan=default_plan();plan['lighting']['key_azimuth']=140
        plan['shot_adjustments']={'hero':{'lighting':{'key_azimuth':0,'fill_elevation':35,'fill_power':1.6}}}
        original=copy.deepcopy(plan)
        hero=engine.effective_plan(plan,'hero')
        self.assertEqual(hero['lighting']['key_azimuth'],0)
        self.assertEqual(hero['lighting']['fill_elevation'],35)
        self.assertEqual(hero['lighting']['fill_power'],1.6)
        self.assertNotIn('key_elevation',hero['lighting'])
        self.assertEqual(engine.effective_plan(hero,'hero'),hero)
        self.assertEqual(engine.effective_plan(plan,'campaign')['lighting'],plan['lighting'])
        self.assertEqual(plan,original)


if __name__=='__main__':unittest.main()

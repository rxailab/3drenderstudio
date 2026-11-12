"""Instruction automation: advisory counts, scope repair and the specialist agents.

Every model response is scripted; no network call is made. The catalogue is a
small cabinet whose geometry reproduces the reported failure: a body of three
separate panels, a key sitting inside its lock, and four identical casters.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import ai_client
import instruction_agents as agents
import semantic_planner
import target_evidence
from intelligence_rules import InstructionOutcome
from planner import default_plan

CONFIG = {'enabled': True, 'provider': 'anthropic', 'base_url': 'https://example.invalid',
          'api_key': 'fixture-token', 'model': 'fixture-vision'}
SOURCE = {'output_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 4}
PARTS = [  # name, material, bounds
    ('左边板', '红色塑料', [0, 0, 0], [.1, 4, 8]),
    ('右边板', '红色塑料', [6, 0, 0], [6.1, 4, 8]),
    ('后板', '红色塑料', [1, 3.5, 0], [5, 3.6, 8]),
    ('钥匙', 'default', [3, -.6, 4], [3.2, .1, 4.4]),
    ('锁芯', 'default', [2.9, -.1, 3.9], [3.3, .2, 4.5]),
    ('轮子_1', '黑色', [0, 0, -1], [.5, .5, -.5]),
    ('轮子_2', '黑色', [5.5, 0, -1], [6, .5, -.5]),
    ('轮子_3', '黑色', [0, 3.5, -1], [.5, 4, -.5]),
    ('轮子_4', '黑色', [5.5, 3.5, -1], [6, 4, -.5]),
    ('轮子支架_1', '亮光金属', [0, 0, -.5], [.5, .5, -.2]),
]
BODY, KEY, LOCK, WHEELS, BRACKET = ['t1', 't2', 't3'], 't4', 't5', ['t6', 't7', 't8', 't9'], 't10'
USER_TEXT = '柜体是红色喷漆金属带反光，钥匙和钥匙孔是不锈钢会反光'
BODY_QUOTE, KEY_QUOTE = '柜体是红色喷漆金属带反光', '钥匙和钥匙孔是不锈钢会反光'
PAINT = {'preset': 'painted_metal', 'color': '#B01818', 'roughness': .22, 'coat': .7}
STEEL = {'roughness': .18, 'metallic': 1}
PAINT_GRANTS = [{'capability': 'material_replacement', 'quote': '喷漆金属'}, {'capability': 'color_change', 'quote': '红色'}]


def cabinet():
    targets = []; objects = []
    for index, (name, material, low, high) in enumerate(PARTS):
        bounds = {'min': list(low), 'max': list(high)}
        targets.append({'id': 't%d' % (index + 1), 'object_name': name, 'material_name': material, 'bounds': bounds,
                        'components': [{'bounds': copy.deepcopy(bounds), 'triangle_count': 12}],
                        'geometry_digest': '%064x' % index, 'thumbnail': {'page': 0, 'rect': [0, 0, .1, .2]}})
        objects.append({'name': name, 'type': 'MESH', 'materials': [material]})
    catalog = {'method': 'scene_part_catalog', 'generation_version': 1, 'source_snapshot': copy.deepcopy(SOURCE),
               'catalog_pages': [{'label': 'PART_CATALOG_1', 'width': 1600, 'height': 1200}], 'targets': targets}
    materials = [{'name': name} for name in dict.fromkeys(part[1] for part in PARTS)]
    return catalog, {'objects': objects, 'materials': materials}


def pair(ident):
    name, material = PARTS[int(ident[1:]) - 1][:2]
    return {'object_name': name, 'material_name': material}


def local(ids, values):
    return {'object_material_overrides': [{**pair(ident), **values} for ident in ids]}


def bind(phrase, ids, count=0, quote=''):
    value = {'phrase': phrase, 'purpose': 'material', 'target_ids': list(ids), 'expected_instances': count}
    if quote is not None:
        value['count_quote'] = quote
    return value


def row(ident, quote, patch_value=None, bindings=None, *, status='ready', grants=None, domains=('material',), kind='adjustment'):
    return {'id': ident, 'quote': quote, 'interpretation': '按原要求调整这些部件的表面。', 'kind': kind,
            'domains': list(domains), 'depends_on': [], 'status': status, 'protections': [],
            'authorizations': grants or [], 'patch': patch_value or {}, 'target_bindings': bindings or [],
            'summary': '调整表面参数。' if status == 'ready' else '无法可靠区分目标部件。'}


def wire(*rows):
    return {'protocol': 'semantic_v2', 'requirements': list(rows), 'notes': [], 'summary': '按要求更新表面参数。'}


def reply(value):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': copy.deepcopy(value)}]}


def located(*groups, unresolved=()):
    return {'groups': [{'phrase': phrase, 'target_ids': list(ids), 'confidence': confidence,
                        'expected_instances': count, 'count_quote': quote}
                       for phrase, ids, confidence, count, quote in groups],
            'unresolved': [{'phrase': phrase, 'reason': reason} for phrase, reason in unresolved]}


def finished(values, grants=(), summary='按描述设置表面参数。'):
    return {'finishes': [{'phrase': phrase, 'values': value} for phrase, value in values.items()],
            'authorizations': list(grants), 'summary': summary}


class AgentFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.png = root / 'hero.png'; Image.new('RGB', (24, 24), 'white').save(self.png)
        page = root / 'catalog.png'; Image.new('RGB', (64, 64), 'white').save(page)
        self.catalog, self.analysis = cabinet()
        self.context = {'active_shot_id': 'hero', 'current_version': 4, 'instruction_protocol': 'semantic_v2',
                        'target_evidence': target_evidence.public_evidence(self.catalog, self.analysis, 4),
                        '_target_image_paths': [{'label': 'PART_CATALOG_1', 'path': str(page)}],
                        'agent_team': {'enabled': True, 'max_calls': 6, 'verify': False}}

    def plan(self, text, replies, **team):
        context = copy.deepcopy(self.context); context['agent_team'].update(team)
        with patch('ai_client._post', side_effect=[reply(value) for value in replies]) as send:
            try:
                result = ai_client.request_plan(default_plan(), text, self.analysis, CONFIG, image_path=self.png, context=context)
            except InstructionOutcome as outcome:
                result = outcome
        self.send = send
        return result

    def sent(self, call):
        body = self.send.call_args_list[call].args[1]
        return [json.loads(block['text']) for block in body['messages'][-1]['content']
                if block['type'] == 'text' and block['text'].startswith('{')]

    def overrides(self, plan):
        return {entry['object_name']: {key: value for key, value in entry.items()
                                       if key in ('preset', 'color', 'roughness', 'metallic', 'coat')}
                for entry in plan['object_material_overrides']}


class AdvisoryCountTests(AgentFixture):
    def user_decision(self, count_quote=''):
        return wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY, 1, count_quote)], grants=PAINT_GRANTS),
                    row('r2', KEY_QUOTE, local([KEY, LOCK], STEEL), [bind('钥匙', [KEY], 1, count_quote), bind('钥匙孔', [LOCK], 1, count_quote)]))

    def test_geometry_reproduces_the_reported_failure(self):
        rows = {target['id']: target for target in self.catalog['targets']}
        self.assertEqual(target_evidence.spatial_instance_count([rows[ident] for ident in BODY]), 3)
        self.assertEqual(target_evidence.spatial_instance_count([rows[KEY], rows[LOCK]]), 1)

    def test_the_reported_instruction_is_applied_in_one_call(self):
        result = self.plan(USER_TEXT, [self.user_decision()])
        self.assertIsInstance(result, dict)
        self.assertEqual(self.send.call_count, 1)
        applied = self.overrides(result)
        for name in ('左边板', '右边板', '后板'):
            self.assertEqual(applied[name], PAINT)
        self.assertEqual(applied['钥匙'], STEEL); self.assertEqual(applied['锁芯'], STEEL)
        self.assertNotIn('轮子_1', applied)
        bindings = {binding['phrase']: binding for binding in result['last_instruction']['target_bindings']}
        self.assertEqual(bindings['柜体']['inferred_instances'], 1)
        self.assertEqual(bindings['柜体']['instance_count'], 3)
        self.assertNotIn('expected_instances', bindings['柜体'])

    def test_without_the_count_basis_the_same_decision_was_rejected(self):
        # Decisions written before count_quote keep their strict meaning.
        legacy = wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY, 1, None)], grants=PAINT_GRANTS),
                      row('r2', KEY_QUOTE, local([KEY, LOCK], STEEL), [bind('钥匙和钥匙孔', [KEY, LOCK], 2, None)]))
        result = self.plan(USER_TEXT, [legacy, legacy], enabled=False)
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual({item['diagnostic_code'] for item in result.pending}, {'semantic_count_mismatch'})

    def test_a_count_quote_the_user_never_wrote_does_not_make_the_count_binding(self):
        result = self.plan(USER_TEXT, [self.user_decision(count_quote='一个')])
        self.assertIsInstance(result, dict)
        self.assertEqual(self.send.call_count, 1)

    def test_a_count_the_user_stated_is_enforced_and_the_measurement_is_fed_back(self):
        text = '四个轮子换成更哑的橡胶'
        short = wire(row('r1', text, local(WHEELS[:3], {'roughness': .9}), [bind('四个轮子', WHEELS[:3], 4, '四个')]))
        full = wire(row('r1', text, local(WHEELS, {'roughness': .9}), [bind('四个轮子', WHEELS, 4, '四个')]))
        result = self.plan(text, [short, full])
        self.assertIsInstance(result, dict)
        self.assertEqual(self.send.call_count, 2)
        correction = self.sent(1)[-1]['execution_correction']
        issue = correction['issues'][0]
        self.assertEqual((issue['code'], issue['expected_instances'], issue['measured_instances']), ('semantic_count_mismatch', 4, 3))
        self.assertEqual(issue['measured_groups'], [['t6'], ['t7'], ['t8']])
        self.assertEqual(len(result['object_material_overrides']), 4)
        binding = result['last_instruction']['target_bindings'][0]
        self.assertEqual((binding['expected_instances'], binding['instance_count']), (4, 4))

    def test_repeated_parts_are_marked_for_the_model(self):
        public = target_evidence.model_evidence(target_evidence.public_evidence(self.catalog, self.analysis, 4))
        wheels = [target for target in public['targets'] if target['id'] in WHEELS]
        self.assertEqual({target['similar_group_size'] for target in wheels}, {4})
        self.assertEqual(len({target['similar_group'] for target in wheels}), 1)
        self.assertNotIn('similar_group', next(target for target in public['targets'] if target['id'] == KEY))


class ScopeRepairTests(AgentFixture):
    def resolved(self, ids):
        return target_evidence.resolve_semantic_bindings([{'phrase': '目标', 'purpose': 'material', 'target_ids': list(ids)}],
                                                         self.catalog, self.analysis, 4)

    def test_shared_material_edit_is_narrowed_to_the_bound_users(self):
        patch_value = {'material_overrides': [{'name': '红色塑料', 'roughness': .2}]}
        repaired, repairs = semantic_planner.repair_material_scope(patch_value, self.resolved(BODY[:2]), self.analysis)
        self.assertEqual(repairs, ['localised_shared_material'])
        self.assertNotIn('material_overrides', repaired)
        self.assertEqual([entry['object_name'] for entry in repaired['object_material_overrides']], ['左边板', '右边板'])

    def test_whole_material_edit_stays_global_when_every_user_is_bound(self):
        patch_value = {'material_overrides': [{'name': '红色塑料', 'roughness': .2}]}
        repaired, repairs = semantic_planner.repair_material_scope(patch_value, self.resolved(BODY), self.analysis)
        self.assertEqual((repaired, repairs), (patch_value, []))

    def test_uniform_values_reach_bound_targets_the_patch_left_out(self):
        repaired, repairs = semantic_planner.repair_material_scope(local(BODY[:1], {'roughness': .2}), self.resolved(BODY), self.analysis)
        self.assertEqual(repairs, ['extended_to_bound_targets'])
        self.assertEqual({entry['object_name']: entry['roughness'] for entry in repaired['object_material_overrides']},
                         {'左边板': .2, '右边板': .2, '后板': .2})

    def test_different_values_are_never_guessed_onto_missing_targets(self):
        patch_value = {'object_material_overrides': [{**pair('t1'), 'roughness': .2}, {**pair('t2'), 'roughness': .6}]}
        self.assertEqual(semantic_planner.repair_material_scope(patch_value, self.resolved(BODY), self.analysis), (patch_value, []))

    def test_an_unbound_edit_is_only_dropped_on_the_final_pass(self):
        patch_value = local(BODY + [BRACKET], {'roughness': .2})
        self.assertEqual(semantic_planner.repair_material_scope(patch_value, self.resolved(BODY), self.analysis), (patch_value, []))
        repaired, repairs = semantic_planner.repair_material_scope(patch_value, self.resolved(BODY), self.analysis, drop_unbound=True)
        self.assertEqual(repairs, ['dropped_unbound_edit'])
        self.assertEqual(len(repaired['object_material_overrides']), 3)

    def test_planner_is_told_which_targets_disagree_before_anything_is_dropped(self):
        text = '柜体更亮一些'
        wide = wire(row('r1', text, local(BODY + [BRACKET], {'roughness': .2}), [bind('柜体', BODY)], kind='visual_goal'))
        result = self.plan(text, [wide, wide], enabled=False)
        self.assertEqual(self.send.call_count, 2)
        issue = self.sent(1)[-1]['execution_correction']['issues'][0]
        self.assertEqual((issue['code'], issue['edited_without_binding']), ('semantic_binding_coverage', [BRACKET]))
        self.assertIsInstance(result, dict)
        self.assertNotIn('轮子支架_1', self.overrides(result))
        self.assertIn({'agent': 'executor', 'repairs': ['dropped_unbound_edit']}, result['last_instruction']['agent_trace'])


class SpecialistTests(AgentFixture):
    def test_locator_regrounds_a_requirement_the_planner_could_not_and_keeps_its_values(self):
        text = '四个轮子换成更哑的橡胶'
        wrong = wire(row('r1', text, local(WHEELS[:2] + [BRACKET], {'roughness': .9}), [bind('四个轮子', WHEELS[:2] + [BRACKET], 4, '四个')]))
        result = self.plan(text, [wrong, wrong, located(('四个轮子', WHEELS, .93, 4, '四个'))])
        self.assertIsInstance(result, dict)
        self.assertEqual(self.send.call_count, 3)
        self.assertEqual(self.overrides(result), {'轮子_%d' % n: {'roughness': .9} for n in range(1, 5)})
        request = self.sent(2)[-1]
        self.assertEqual(request['requirement']['quote'], text)
        # The bracket touches its wheel, so three targets measured as two groups.
        self.assertEqual(request['previous_attempt']['failure']['measured_instances'], 2)
        trace = result['last_instruction']['agent_trace']
        self.assertEqual(trace[0], {'agent': 'planner', 'calls': 2, 'ready': 0, 'pending': 1})
        step = next(item for item in trace if item.get('requirement') == 'r1')
        self.assertEqual((step['result'], step['locator']['result'], step['locator']['targets']), ('resolved', 'located', 4))
        self.assertNotIn('finisher', step)

    def test_locator_and_finisher_complete_a_requirement_the_planner_left_open(self):
        open_row = row('r2', KEY_QUOTE, status='needs_clarification')
        decision = wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY)], grants=PAINT_GRANTS), open_row)
        result = self.plan(USER_TEXT, [decision, located(('钥匙', [KEY], .9, 0, ''), ('钥匙孔', [LOCK], .8, 0, '')),
                                       finished({'钥匙': STEEL, '钥匙孔': STEEL})])
        self.assertIsInstance(result, dict)
        self.assertEqual(self.send.call_count, 3)
        applied = self.overrides(result)
        self.assertEqual((applied['钥匙'], applied['锁芯'], applied['左边板']), (STEEL, STEEL, PAINT))
        finisher = self.sent(2)[-1]
        self.assertEqual([group['phrase'] for group in finisher['located']], ['钥匙', '钥匙孔'])
        self.assertIn('coat', finisher['allowed_fields'])
        self.assertEqual(result['last_instruction']['ready'][1]['status'], 'ready')

    def test_a_finisher_cannot_grant_itself_a_colour_change(self):
        open_row = row('r1', '钥匙更亮一些', status='needs_clarification')
        overreach = finished({'钥匙': {'preset': 'polished_metal', 'color': '#FFD700'}})
        result = self.plan('钥匙更亮一些', [wire(open_row), located(('钥匙', [KEY], .9, 0, '')), overreach])
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'needs_clarification')
        step = next(item for item in result.agent_trace if item.get('requirement') == 'r1')
        self.assertEqual((step['result'], step['reason']), ('rejected', 'semantic_authorization_missing'))

    def test_an_unsure_locator_changes_nothing_but_other_work_still_applies(self):
        decision = wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY)], grants=PAINT_GRANTS),
                        row('r2', KEY_QUOTE, status='needs_clarification'))
        result = self.plan(USER_TEXT, [decision, located(('钥匙', [KEY], .3, 0, ''))])
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'partial_available')
        self.assertEqual(self.send.call_count, 2)
        applied = self.overrides(result.proposal['candidate_plan'])
        self.assertIn('左边板', applied); self.assertNotIn('钥匙', applied)
        step = next(item for item in result.agent_trace if item.get('requirement') == 'r2')
        self.assertEqual((step['result'], step['locator']['reason']), ('pending', 'ambiguous'))

    def test_what_the_locator_cannot_find_stays_pending(self):
        decision = wire(row('r1', '把抽屉滑轨换成黑色', status='needs_clarification'))
        result = self.plan('把抽屉滑轨换成黑色', [decision, located(unresolved=[('抽屉滑轨', 'not_in_catalogue')])])
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.agent_trace[1]['locator'], {'result': 'unresolved', 'reason': 'not_in_catalogue'})
        self.assertEqual((result.pending[0]['action'], result.pending[0]['reason']), ('locate_part', agents.PENDING_REASONS['not_in_catalogue']))

    def test_each_open_requirement_is_handled_against_the_latest_decision(self):
        text = '钥匙更亮一些，四个轮子更哑一些，抽屉滑轨换成黑色'
        decision = wire(row('r1', '钥匙更亮一些，', status='needs_clarification'), row('r2', '四个轮子更哑一些，', status='needs_clarification'),
                        row('r3', '抽屉滑轨换成黑色', status='needs_clarification'))
        result = self.plan(text, [decision,
                                  located(('钥匙', [KEY], .9, 0, '')), finished({'钥匙': {'roughness': .08}}),
                                  located(('四个轮子', WHEELS, .9, 4, '四个')), finished({'四个轮子': {'roughness': .95}}),
                                  located(unresolved=[('抽屉滑轨', 'not_in_catalogue')])])
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual((result.status, self.send.call_count), ('partial_available', 6))
        applied = self.overrides(result.proposal['candidate_plan'])
        self.assertEqual(applied['钥匙'], {'roughness': .08}); self.assertEqual(applied['轮子_3'], {'roughness': .95})
        self.assertEqual([item['id'] for item in result.pending], ['r3'])
        self.assertEqual(result.pending[0]['reason'], agents.PENDING_REASONS['not_in_catalogue'])
        self.assertEqual([step['result'] for step in result.agent_trace if step.get('requirement')], ['resolved', 'resolved', 'pending'])

    def test_the_call_budget_is_respected(self):
        decision = wire(row('r1', KEY_QUOTE, status='needs_clarification'))
        result = self.plan(KEY_QUOTE, [decision, located(('钥匙', [KEY], .9, 0, ''), ('钥匙孔', [LOCK], .9, 0, ''))], max_calls=1)
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(result.agent_trace[1]['result'], 'unavailable')

    def test_a_failed_specialist_call_leaves_the_planner_result_intact(self):
        decision = wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY)], grants=PAINT_GRANTS),
                        row('r2', KEY_QUOTE, status='needs_clarification'))
        context = copy.deepcopy(self.context)
        with patch('ai_client._post', side_effect=[reply(decision), ValueError('AI 请求超时')]) as send:
            with self.assertRaises(InstructionOutcome) as caught:
                ai_client.request_plan(default_plan(), USER_TEXT, self.analysis, CONFIG, image_path=self.png, context=context)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(caught.exception.status, 'partial_available')
        self.assertIn('左边板', self.overrides(caught.exception.proposal['candidate_plan']))

    def test_specialists_stay_out_when_the_team_is_off_or_nothing_is_pending(self):
        decision = wire(row('r1', KEY_QUOTE, status='needs_clarification'))
        self.plan(KEY_QUOTE, [decision], enabled=False)
        self.assertEqual(self.send.call_count, 1)
        done = wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY)], grants=PAINT_GRANTS))
        result = self.plan(BODY_QUOTE, [done])
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(result['last_instruction']['agent_trace'], [{'agent': 'planner', 'calls': 1, 'ready': 1, 'pending': 0}])

    def test_requirements_held_back_by_a_preservation_are_not_reopened(self):
        keep = {**row('r2', '别的都不要动', status='needs_clarification', domains=(), kind='preservation'), 'summary': '保留范围不明确。'}
        decision = wire(row('r1', '钥匙更亮一些，', local([KEY], {'roughness': .1}), [bind('钥匙', [KEY])], kind='visual_goal'), keep)
        result = self.plan('钥匙更亮一些，别的都不要动', [decision, decision])
        self.assertIsInstance(result, InstructionOutcome)
        self.assertEqual(self.send.call_count, 1)

    def test_settings_come_from_the_environment(self):
        self.assertEqual(agents.settings({}), {'enabled': True, 'max_calls': agents.DEFAULT_MAX_CALLS, 'verify': True})
        self.assertEqual(agents.settings({'AI_AGENT_TEAM': 'off', 'AI_AGENT_MAX_CALLS': '3', 'AI_AGENT_VERIFY': '0'}),
                         {'enabled': False, 'max_calls': 3, 'verify': False})
        self.assertEqual(agents.settings({'AI_AGENT_MAX_CALLS': '99'})['max_calls'], 12)
        self.assertEqual(agents.settings({'AI_AGENT_MAX_CALLS': 'many'})['max_calls'], agents.DEFAULT_MAX_CALLS)


def verdict(*selections):
    return {'selections': [{'index': index, 'wrong_ids': list(wrong), 'confidence': confidence}
                           for index, (wrong, confidence) in enumerate(selections)]}


class VerifierTests(AgentFixture):
    """A second opinion on the planner's selections: it may narrow them, never widen."""
    TEXT = '四个轮子换成更哑的橡胶'

    def wide(self):
        # Tyres and one bracket. The bracket touches its wheel, so the stated count of four still measures as four.
        ids = WHEELS + [BRACKET]
        return wire(row('r1', self.TEXT, local(ids, {'roughness': .9}), [bind('四个轮子', ids, 4, '四个')]))

    def test_the_count_check_alone_accepts_a_selection_that_is_too_wide(self):
        result = self.plan(self.TEXT, [self.wide()])
        self.assertIn('轮子支架_1', self.overrides(result))

    def test_parts_the_verifier_is_sure_about_are_taken_out_before_anything_is_applied(self):
        result = self.plan(self.TEXT, [self.wide(), verdict(([BRACKET], .92))], verify=True)
        self.assertIsInstance(result, dict)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(sorted(self.overrides(result)), ['轮子_1', '轮子_2', '轮子_3', '轮子_4'])
        binding = result['last_instruction']['target_bindings'][0]
        self.assertEqual((binding['target_ids'], binding['instance_count']), (WHEELS, 4))
        trace = result['last_instruction']['agent_trace']
        self.assertIn({'agent': 'verifier', 'checked': 1, 'narrowed': 1, 'removed': 1, 'result': 'narrowed'}, trace)
        self.assertIn({'agent': 'executor', 'repairs': ['dropped_unbound_edit']}, trace)
        request = self.sent(1)[-1]
        self.assertEqual(request['selections'][0]['phrase'], '四个轮子')
        self.assertEqual([item['id'] for item in request['selections'][0]['selected']], WHEELS + [BRACKET])

    def test_an_unsure_or_agreeing_verifier_changes_nothing(self):
        for answer, outcome in ((verdict(([BRACKET], .4)), 'confirmed'), (verdict(([], .95)), 'confirmed')):
            with self.subTest(answer=answer):
                result = self.plan(self.TEXT, [self.wide(), answer], verify=True)
                self.assertIn('轮子支架_1', self.overrides(result))
                self.assertEqual(next(step for step in result['last_instruction']['agent_trace'] if step['agent'] == 'verifier')['result'], outcome)

    def test_a_verifier_cannot_add_targets_or_empty_a_selection(self):
        everything = verdict((WHEELS + [BRACKET], .99))
        result = self.plan(self.TEXT, [self.wide(), everything], verify=True)
        self.assertEqual(len(self.overrides(result)), 5)
        step = next(step for step in result['last_instruction']['agent_trace'] if step['agent'] == 'verifier')
        self.assertEqual((step['result'], step['disputed']), ('disputed', 1))
        foreign = verdict(([KEY], .99))      # not in the selection: ignored
        result = self.plan(self.TEXT, [self.wide(), foreign], verify=True)
        self.assertEqual(len(self.overrides(result)), 5)

    def test_narrowing_that_breaks_a_stated_count_is_not_accepted(self):
        # Dropping two tyres leaves three groups where the user asked for four.
        result = self.plan(self.TEXT, [self.wide(), verdict((WHEELS[:2], .95))], verify=True)
        self.assertEqual(len(self.overrides(result)), 5)
        step = next(step for step in result['last_instruction']['agent_trace'] if step['agent'] == 'verifier')
        self.assertEqual(step['result'], 'rejected')

    def test_single_part_selections_and_pending_rows_are_not_sent_for_verification(self):
        decision = wire(row('r1', '钥匙更亮一些', local([KEY], {'roughness': .1}), [bind('钥匙', [KEY])], kind='visual_goal'))
        self.plan('钥匙更亮一些', [decision], verify=True)
        self.assertEqual(self.send.call_count, 1)

    def test_a_failed_verifier_call_leaves_the_plan_as_planned(self):
        context = copy.deepcopy(self.context); context['agent_team']['verify'] = True
        with patch('ai_client._post', side_effect=[reply(self.wide()), ValueError('AI 请求超时')]) as send:
            result = ai_client.request_plan(default_plan(), self.TEXT, self.analysis, CONFIG, image_path=self.png, context=context)
        self.assertEqual((send.call_count, len(self.overrides(result))), (2, 5))
        self.assertEqual(result['last_instruction']['agent_trace'][1], {'agent': 'verifier', 'result': 'unavailable', 'reason': 'transport'})


class ServiceFlowTests(unittest.TestCase):
    """The same decisions through the real instruction service and its commit guards."""
    def setUp(self):
        import ai_config, engine, instruction_service as service, scene_parts, studio_store as store
        self.service, self.store = service, store
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for module in (store, engine, service):
            self.mock(module, 'DATA', self.root)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear(); self.addCleanup(service._INFLIGHT.clear)
        folder = self.root / 'fixture/runs/source'; folder.mkdir(parents=True)
        Image.new('RGB', (32, 32), 'white').save(folder / 'hero.png')
        page = folder / 'catalog.png'; Image.new('RGB', (64, 64), 'white').save(page)
        (folder / 'result.json').write_text(json.dumps({'success': True, 'product_samples': []}), encoding='utf-8')
        self.catalog, analysis = cabinet()
        store.put('project', {'id': 'fixture', 'name': '工具柜', 'slug': 'fixture', 'version': 4, 'status': 'ready', 'history': [],
                              'metadata': {}, 'plan': default_plan(), 'analysis': analysis,
                              'outputs': [{'job_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 4,
                                           'png_url': '/files/fixture/runs/source/hero.png',
                                           'result_url': '/files/fixture/runs/source/result.json'}]})
        for name in ('get_config', 'get_vision_config'):
            self.mock(ai_config, name, return_value=CONFIG)
        for name in ('record_result', 'record_vision_result'):
            self.mock(ai_config, name)
        self.mock(engine, 'runtime_status', return_value={'capabilities': {'pick': False, 'inspect_parts': True,
                                                                           'light_direction': False, 'hero_optimize': False}})
        self.mock(scene_parts, 'prepare', return_value={'evidence': copy.deepcopy(self.catalog),
                                                        'images': [{'label': 'PART_CATALOG_1', 'path': str(page)}]})
        self.mock(scene_parts, 'verify', return_value=True)
        self.mock(scene_parts, 'guard', return_value={'identity': {}})
        self.team = {'enabled': True, 'max_calls': 6, 'verify': False}
        self.mock(agents, 'settings', side_effect=lambda: dict(self.team))
        self.send = self.mock(ai_client, '_post')

    def mock(self, module, name, *args, **kwargs):
        mocked = patch.object(module, name, *args, **kwargs)
        result = mocked.start(); self.addCleanup(mocked.stop)
        return result

    def apply(self, text, replies):
        self.send.side_effect = [reply(value) for value in replies]
        return self.service.apply_instruction('fixture', {'text': text, 'expected_version': 4, 'shot_id': 'hero',
                                                          'source': copy.deepcopy(SOURCE)}, 'request-1')

    def test_the_reported_instruction_is_committed_without_any_confirmation(self):
        decision = wire(row('r1', BODY_QUOTE, local(BODY, PAINT), [bind('柜体', BODY, 1)], grants=PAINT_GRANTS),
                        row('r2', KEY_QUOTE, local([KEY, LOCK], STEEL), [bind('钥匙', [KEY], 1), bind('钥匙孔', [LOCK], 1)]))
        receipt = self.apply(USER_TEXT, [decision])['instruction_result']
        self.assertEqual((receipt['status'], receipt['applied_version'], self.send.call_count), ('applied', 5, 1))
        self.assertNotIn('pending', receipt)
        self.assertEqual(receipt['target_verification'], 'scene_geometry_catalog')
        self.assertEqual({binding['phrase']: (binding['inferred_instances'], binding['instance_count'])
                          for binding in receipt['target_bindings']}, {'柜体': (1, 3), '钥匙': (1, 1), '钥匙孔': (1, 1)})
        saved = self.store.get('project', 'fixture')
        self.assertEqual(saved['version'], 5)
        self.assertEqual({entry['object_name'] for entry in saved['plan']['object_material_overrides']},
                         {'左边板', '右边板', '后板', '钥匙', '锁芯'})
        self.assertEqual(len(saved['history']), 1)        # one undo step restores everything

    def test_specialists_finish_inside_the_same_request_and_are_recorded(self):
        text = '四个轮子换成更哑的橡胶'
        wrong = wire(row('r1', text, local(WHEELS[:2] + [BRACKET], {'roughness': .9}), [bind('四个轮子', WHEELS[:2] + [BRACKET], 4, '四个')]))
        receipt = self.apply(text, [wrong, wrong, located(('四个轮子', WHEELS, .93, 4, '四个'))])['instruction_result']
        self.assertEqual((receipt['status'], self.send.call_count), ('applied', 3))
        step = next(item for item in receipt['agent_trace'] if item.get('requirement') == 'r1')
        self.assertEqual(step['result'], 'resolved')
        self.assertEqual(receipt['target_bindings'][0]['expected_instances'], 4)
        self.assertEqual(len(self.store.get('project', 'fixture')['plan']['object_material_overrides']), 4)

    def test_verification_runs_by_default_and_its_narrowing_is_what_gets_saved(self):
        self.team['verify'] = True
        text = '四个轮子换成更哑的橡胶'; ids = WHEELS + [BRACKET]
        wide = wire(row('r1', text, local(ids, {'roughness': .9}), [bind('四个轮子', ids, 4, '四个')]))
        receipt = self.apply(text, [wide, verdict(([BRACKET], .9))])['instruction_result']
        self.assertEqual((receipt['status'], self.send.call_count), ('applied', 2))
        saved = self.store.get('project', 'fixture')['plan']['object_material_overrides']
        self.assertEqual(sorted(entry['object_name'] for entry in saved), ['轮子_1', '轮子_2', '轮子_3', '轮子_4'])
        self.assertEqual(receipt['target_bindings'][0]['target_ids'], WHEELS)

    def test_what_stays_open_is_reported_with_the_agents_reason_and_nothing_is_saved(self):
        from intelligence_rules import InstructionOutcome as Outcome
        decision = wire(row('r1', '把抽屉滑轨换成黑色', status='needs_clarification'))
        self.send.side_effect = [reply(decision), reply(located(unresolved=[('抽屉滑轨', 'not_in_catalogue')]))]
        with self.assertRaises(Outcome) as caught:
            self.service.apply_instruction('fixture', {'text': '把抽屉滑轨换成黑色', 'expected_version': 4, 'shot_id': 'hero',
                                                       'source': copy.deepcopy(SOURCE)}, 'request-2')
        self.assertEqual(caught.exception.pending[0]['reason'], agents.PENDING_REASONS['not_in_catalogue'])
        self.assertEqual(self.store.get('project', 'fixture')['version'], 4)


if __name__ == '__main__':
    unittest.main()

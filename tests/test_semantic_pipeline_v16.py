"""Production instruction flow with model-owned language interpretation.

Only model responses and unavailable native-runtime capabilities are fixtures.
The actual service, planner, schema, evidence reader, snapshots, and SQLite
receipts run together. These tests prove execution contracts, not the quality
of a real model's language understanding or the appearance of a rendered image.
"""
import copy
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import ai_client
import ai_config
import engine
import instruction_service as service
import intelligence_rules
import planner
import scene_parts
import studio_store as store
import target_evidence


CONFIG = {'enabled': True, 'provider': 'anthropic', 'base_url': 'https://example.invalid',
          'api_key': 'fixture-key-never-sent', 'model': 'fixture-vision'}
SOURCE = {'output_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3}


def row(ident, quote, *, patch_value=None, kind='adjustment', domains=None,
        status='ready', protections=None, bindings=None, authorizations=None, depends_on=None):
    return {'id': ident, 'quote': quote, 'interpretation': '根据整句要求和当前场景选择可执行参数。',
            'kind': kind, 'domains': ['lighting'] if domains is None else domains,
            'status': status, 'protections': protections or [], 'authorizations': authorizations or [],
            'depends_on': depends_on or [],
            'patch': patch_value or {}, 'target_bindings': bindings or [],
            'summary': '已准备对应的参数调整。' if status == 'ready' else '保留约束或等待真实部件证据。'}


def wire(*requirements, **extra):
    return {'protocol': 'semantic_v2', 'requirements': list(requirements),
            'notes': [], 'summary': '参数修改完成后需要查看实际预览。', **extra}


def reply(value):
    return {'stop_reason': 'tool_use', 'content': [
        {'type': 'tool_use', 'name': ai_client.TOOL_NAME, 'input': copy.deepcopy(value)}]}


def binding(phrase, ids, expected_instances=0, purpose='material'):
    return {'phrase': phrase, 'purpose': purpose, 'target_ids': ids,
            'expected_instances': expected_instances}


def surface(number, **values):
    return {'object_name': 'Opaque.%03d' % number, 'material_name': 'Shared', **values}


@contextmanager
def reject_legacy_language_calls():
    """Every former language authority on the online path must remain unused."""
    targets = {
        planner: ('extract_protections', 'preservation_only', 'rule_patch'),
        service: ('extract_protections', '_wants_reference', '_reference'),
        scene_parts: ('wants_inspection',),
        intelligence_rules: ('extract_protections', 'instruction_clauses', 'material_clause'),
        ai_client: ('extract_protections', 'preservation_only', 'check_instruction_support',
                    '_instruction_requirements', 'instruction_clauses', '_requirement_domains',
                    '_explicit_photography_action', '_rule_photography',
                    '_resolve_semantic_intents', '_semantic_visual',
                    '_requires_semantic_interpretation'),
        target_evidence: ('resolve_bindings', '_requested_count', 'material_assignments',
                          'validate_binding_coverage', '_whole_target', 'named_global_material'),
    }
    with ExitStack() as stack:
        for module, names in targets.items():
            for name in names:
                if not hasattr(module, name):
                    continue
                stack.enter_context(patch.object(module, name, side_effect=AssertionError(
                    'Online semantic_v2 called legacy language helper: ' + module.__name__ + '.' + name)))
        yield


class SemanticPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module in (store, engine, service):
            self.mock(module, 'DATA', self.root)
        with store.connection() as connection:
            connection.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
        service._INFLIGHT.clear(); self.addCleanup(service._INFLIGHT.clear)
        self.folder = self.root / 'fixture/runs/source'; self.folder.mkdir(parents=True)
        self.image_path = self.folder / 'hero.png'
        Image.new('RGB', (32, 32), 'white').save(self.image_path)
        self.result_path = self.folder / 'result.json'
        samples = [{'object_name': 'Opaque.%03d' % n, 'material_name': 'Shared',
                    'x': n / 6, 'y': .5} for n in range(1, 5)]
        self.result_path.write_text(json.dumps({'success': True, 'product_samples': samples}), encoding='utf-8')
        base = planner.default_plan()
        base['object_material_overrides'] = [surface(1, preset='painted_metal', color='#B93832',
                                                    roughness=.68, preserve_original=False)]
        project = {'id': 'fixture', 'name': '整句语义回归', 'slug': 'fixture', 'version': 3,
                   'status': 'ready', 'history': [], 'metadata': {}, 'plan': base,
                   'analysis': {'objects': [{'name': 'Opaque.%03d' % n, 'type': 'MESH',
                                             'materials': ['Shared']} for n in range(1, 5)],
                                'materials': [{'name': 'Shared', 'suggested_preset': 'matte_plastic'}]},
                   'outputs': [{'job_id': 'source', 'shot_id': 'hero', 'quality': 'preview', 'version': 3,
                                'png_url': '/files/fixture/runs/source/hero.png',
                                'result_url': '/files/fixture/runs/source/result.json'}]}
        store.put('project', project); self.original = store.get('project', 'fixture')
        self.body = {'text': '', 'expected_version': 3, 'shot_id': 'hero', 'source': copy.deepcopy(SOURCE)}
        for name in ('get_config', 'get_vision_config'):
            self.mock(ai_config, name, return_value=CONFIG)
        for name in ('record_result', 'record_vision_result'):
            self.mock(ai_config, name)
        self.mock(engine, 'runtime_status', return_value={'capabilities': {
            'pick': False, 'inspect_parts': False, 'light_direction': False, 'hero_optimize': False}})
        self.submit = self.mock(engine, 'submit', side_effect=AssertionError('Instruction must not start a render'))
        self.mock(engine, 'pick_output', side_effect=AssertionError('Native Blender is not part of this fixture'))
        self.send = self.mock(ai_client, '_post')

    def mock(self, module, name, *args, **kwargs):
        mocked = patch.object(module, name, *args, **kwargs)
        result = mocked.start(); self.addCleanup(mocked.stop)
        return result

    def apply(self, text, value=None, key='semantic-request', **body_fields):
        if value is not None:
            self.send.return_value = reply(value)
        return service.apply_instruction('fixture', {**self.body, 'text': text, **body_fields}, key)

    def assert_unmodified(self):
        saved = store.get('project', 'fixture')
        self.assertEqual(saved['plan'], self.original['plan'])
        self.assertEqual(saved['version'], self.original['version'])
        self.assertEqual(saved['history'], [])
        self.assertNotIn('instruction_receipts', saved)
        self.assertNotIn(service.PROPOSALS_KEY, saved)
        self.assertNotIn('fixture', service._INFLIGHT)
        self.assertEqual(store.all_records('job'), [])
        self.submit.assert_not_called()

    def facts(self, call=0):
        body = self.send.call_args_list[call].args[1]
        user = next(message for message in body['messages'] if message['role'] == 'user')
        return json.loads(user['content'][-1]['text'])

    def images(self, call=0):
        body = self.send.call_args_list[call].args[1]
        user = next(message for message in body['messages'] if message['role'] == 'user')
        return [block for block in user['content'] if block['type'] in ('image', 'image_url')]

    def attach_reference(self):
        folder = self.root / 'fixture/references'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'reference.png'
        Image.new('RGB', (32, 32), 'blue').save(path)
        project = store.get('project', 'fixture'); project['metadata']['reference'] = str(path)
        store.put('project', project)
        self.original = store.get('project', 'fixture')
        return path

    def test_default_service_uses_whole_utterance_without_any_legacy_keyword_authority(self):
        text = '这家伙看着像闷在罩子里，给它透口气。'
        value = wire(row('r1', text, kind='visual_goal',
                         patch_value={'lighting': {'key_power': 2.1, 'fill_power': 1.8}, 'light_softness': 1.5}))
        self.assertEqual(service.DEFAULT_INSTRUCTION_PROTOCOL, 'semantic_v2')
        with reject_legacy_language_calls():
            result = self.apply(text, value)
        receipt = result['instruction_result']
        self.assertEqual(receipt['status'], 'applied')
        self.assertEqual(receipt['source'], 'ai')
        self.assertTrue(receipt['used_image'])
        self.assertEqual(result['version'], 4)
        self.assertEqual(self.send.call_count, 1)
        facts = self.facts()
        self.assertEqual(facts['instruction'], text)
        self.assertEqual(facts['request_context']['instruction_protocol'], 'semantic_v2')
        effective = engine.effective_plan(result['plan'], 'hero')
        self.assertEqual(effective['lighting']['key_power'], 2.1)
        self.assertEqual(effective['lighting']['fill_power'], 1.8)
        self.assertEqual(result['plan']['lighting'], self.original['plan']['lighting'])
        self.assertEqual(result['plan']['shots'], self.original['plan']['shots'])
        self.submit.assert_not_called()

    def test_model_can_bind_unanticipated_material_phrase_to_real_surface_without_keyword_gate(self):
        text = '这一大片做出刚擦拭过的润感，别油腻。'
        value = wire(row('r1', text, kind='visual_goal', domains=['material'],
                         patch_value={'object_material_overrides': [surface(1, roughness=.32)]},
                         bindings=[binding('这一大片', ['t1'])]))
        with reject_legacy_language_calls():
            result = self.apply(text, value)
        modified = result['plan']['object_material_overrides'][0]
        self.assertEqual(modified['roughness'], .32)
        self.assertEqual(modified['color'], '#B93832')
        self.assertEqual(modified['preset'], 'painted_metal')
        receipt = result['instruction_result']
        self.assertEqual(receipt['target_bindings'][0]['target_ids'], ['t1'])
        self.assertEqual(receipt['target_verification'], 'render_samples')
        self.assertEqual(self.send.call_count, 1)

    def test_partial_ready_work_applies_immediately_and_replay_does_not_repeat_relative_change(self):
        preserved = '红漆的那个味儿留着'
        ready = '别让正脸这么憋屈'
        pending = '最下面那一撮也拾掇一下'
        text = '；'.join((preserved, ready, pending)) + '。'
        value = wire(
            row('r1', preserved, kind='preservation', domains=[], status='already_satisfied',
                protections=['preserve_color', 'preserve_material']),
            row('r2', ready, kind='visual_goal', patch_value={'lighting': {'key_power': 2.0, 'fill_power': 1.7}}),
            row('r3', pending, kind='adjustment', domains=['material'], status='needs_clarification'))
        with reject_legacy_language_calls():
            result = self.apply(text, value)
            replay = self.apply(text, value)
        receipt = result['instruction_result']
        self.assertEqual(receipt['status'], 'partially_applied')
        self.assertTrue(receipt['partial'])
        self.assertEqual(result['version'], 4)
        self.assertEqual(result['plan']['object_material_overrides'], self.original['plan']['object_material_overrides'])
        self.assertEqual(set(receipt['protections']), {'preserve_color', 'preserve_material'})
        self.assertIn(preserved, receipt['remaining_text'])
        self.assertIn(pending, receipt['remaining_text'])
        self.assertNotIn(ready, receipt['remaining_text'])
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(self.send.call_count, 1)
        saved = store.get('project', 'fixture')
        self.assertEqual(len(saved['history']), 1)
        self.assertEqual(len(saved['instruction_receipts']), 1)
        self.assertEqual(engine.effective_plan(saved['plan'], 'hero')['lighting']['key_power'], 2.0)

    def test_negation_cannot_disappear_from_exact_source_coverage(self):
        text = '别动那层红漆；让正脸透亮些。'
        # Dropping one character reverses the preservation requirement. Exact
        # quote matching and complete coverage must catch this without parsing it.
        bad = wire(row('r1', '动那层红漆', kind='preservation', domains=[],
                       status='already_satisfied', protections=['preserve_material']),
                   row('r2', '让正脸透亮些', kind='visual_goal',
                       patch_value={'lighting': {'key_power': 2.0}}))
        with reject_legacy_language_calls(), self.assertRaises(ai_client.ResponseContractError):
            self.apply(text, bad)
        self.assertEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_missing_independent_part_does_not_block_another_material_edit(self):
        ready = '这一大片做出刚擦过的润感'
        pending = '那个小突起也拾掇一下'
        text = ready + '；' + pending + '。'
        value = wire(
            row('r1', ready, kind='visual_goal', domains=['material'],
                patch_value={'object_material_overrides': [surface(1, roughness=.32)]},
                bindings=[binding('这一大片', ['t1'])]),
            row('r2', pending, kind='visual_goal', domains=['material'], status='needs_clarification'))
        with reject_legacy_language_calls():
            result = self.apply(text, value)
        self.assertEqual(result['instruction_result']['status'], 'partially_applied')
        self.assertEqual(result['plan']['object_material_overrides'][0]['roughness'], .32)
        self.assertEqual(result['instruction_result']['pending'][0]['quote'], pending)
        self.assertEqual(self.send.call_count, 1)

    def test_failed_target_row_keeps_independent_lighting_after_bounded_correction(self):
        ready = '给正脸透口气'
        pending = '那个小突起看着润一点'
        text = ready + '；' + pending + '。'
        value = wire(
            row('r1', ready, kind='visual_goal', patch_value={'lighting': {'key_power': 1.8}}),
            row('r2', pending, kind='visual_goal', domains=['material'],
                patch_value={'object_material_overrides': [surface(1, roughness=.32)]},
                bindings=[binding('那个小突起', ['t999'])]))
        with reject_legacy_language_calls():
            result = self.apply(text, value)
        receipt = result['instruction_result']
        self.assertEqual(receipt['status'], 'partially_applied')
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(engine.effective_plan(result['plan'], 'hero')['lighting']['key_power'], 1.8)
        self.assertEqual(result['plan']['object_material_overrides'], self.original['plan']['object_material_overrides'])
        self.assertEqual(receipt['pending'][0]['quote'], pending)
        self.assertEqual(len(result['history']), 1)

    def test_preservation_only_receipt_stays_on_semantic_route_and_does_not_save_a_version(self):
        text = '红漆的那个味儿给我原封不动留着。'
        value = wire(row('r1', text, kind='preservation', domains=[], status='already_satisfied',
                         protections=['preserve_color', 'preserve_material']))
        with reject_legacy_language_calls():
            result = self.apply(text, value)
        self.assertEqual(result['version'], 3)
        self.assertEqual(result['history'], [])
        receipt = result['instruction_result']
        self.assertEqual(receipt['status'], 'no_change')
        self.assertEqual(receipt['source'], 'ai')
        self.assertEqual(set(receipt['protections']), {'preserve_color', 'preserve_material'})
        self.assertEqual(self.send.call_count, 1)

    def test_unresolved_preservation_blocks_otherwise_executable_patch(self):
        text = '这一处的关系别破坏；正脸再通透一些。'
        value = wire(
            row('r1', '这一处的关系别破坏', kind='preservation', domains=[], status='needs_clarification'),
            row('r2', '正脸再通透一些', kind='visual_goal', patch_value={'lighting': {'key_power': 2.0}}))
        with reject_legacy_language_calls(), self.assertRaises(ValueError):
            self.apply(text, value)
        self.assertLessEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_structured_preservation_cannot_be_overridden_by_another_requirement(self):
        text = '正脸亮起来；这层红漆给我原封不动留着。'
        value = wire(
            row('r1', '正脸亮起来', kind='visual_goal', domains=['lighting', 'material'],
                patch_value={'lighting': {'key_power': 2.0}, 'object_material_overrides': [surface(1, roughness=.2)]},
                bindings=[binding('正脸', ['t1'])]),
            row('r2', '这层红漆给我原封不动留着', kind='preservation', domains=[],
                status='already_satisfied', protections=['preserve_material']))
        with reject_legacy_language_calls(), self.assertRaises(ValueError):
            self.apply(text, value)
        self.assertEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_old_model_wire_is_not_silently_accepted_by_default_online_service(self):
        old = {'status': 'apply', 'patch': {'exposure': .4}, 'summary': '旧格式', 'notes': [], 'unhandled': []}
        with reject_legacy_language_calls(), self.assertRaises(ai_client.ResponseContractError):
            self.apply('让画面通透些', old)
        self.assertEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_unknown_target_id_never_changes_an_existing_surface(self):
        text = '这一大片看起来润一些。'
        value = wire(row('r1', text, kind='visual_goal', domains=['material'],
                         patch_value={'object_material_overrides': [surface(1, roughness=.3)]},
                         bindings=[binding('这一大片', ['t999'])]))
        with reject_legacy_language_calls(), self.assertRaises(ValueError):
            self.apply(text, value)
        self.assertLessEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_global_shared_material_edit_cannot_escape_one_surface_binding(self):
        text = '就这一大片看起来润一些。'
        value = wire(row('r1', text, kind='visual_goal', domains=['material'],
                         patch_value={'material_overrides': [{'name': 'Shared', 'roughness': .3}]},
                         bindings=[binding('这一大片', ['t1'])]))
        with reject_legacy_language_calls(), self.assertRaises(ValueError):
            self.apply(text, value)
        self.assertLessEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_numeric_part_commitment_requires_geometry_not_counted_visible_names(self):
        text = '让下面的四只脚都像刚擦拭过一样。'
        value = wire(row('r1', text, kind='visual_goal', domains=['material'],
                         patch_value={'object_material_overrides': [surface(n, roughness=.3) for n in range(1, 5)]},
                         bindings=[binding('下面的四只脚', ['t1', 't2', 't3', 't4'], expected_instances=4)]))
        with reject_legacy_language_calls(), self.assertRaises(ValueError):
            self.apply(text, value)
        self.assertLessEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_invalid_parameters_and_unknown_commands_are_rejected_before_commit(self):
        text = '让这一大片看起来润一些。'
        for changes in ({'lighting': {'key_power': 999}}, {'run_python': 'print(1)'}):
            with self.subTest(changes=changes):
                self.send.reset_mock()
                value = wire(row('r1', text, kind='visual_goal', patch_value=changes))
                with reject_legacy_language_calls(), self.assertRaises(ValueError):
                    self.apply(text, value)
                self.assertEqual(self.send.call_count, 2)
                self.assert_unmodified()

    def test_source_image_changed_during_inference_blocks_lighting_only_commit(self):
        text = '给正脸透口气。'
        value = wire(row('r1', text, kind='visual_goal', patch_value={'lighting': {'key_power': 2.0}}))
        def alter_source(*args, **kwargs):
            Image.new('RGB', (32, 32), 'black').save(self.image_path)
            return reply(value)
        self.send.side_effect = alter_source
        with reject_legacy_language_calls(), self.assertRaises(service.InstructionError) as caught:
            self.apply(text)
        self.assertEqual(caught.exception.status_code, 409)
        self.assert_unmodified()

    def test_unused_reference_is_not_read_or_hashed_merely_because_it_is_attached(self):
        reference = self.attach_reference()
        text = '先别理会那张参考照，只给正脸透口气。'
        value = wire(row('r1', text, kind='visual_goal', patch_value={'lighting': {'key_power': 1.8}}))
        original_digest = service._file_digest
        def guarded_digest(path):
            if path == reference:
                raise AssertionError('Unused reference bytes must not enter the evidence guard')
            return original_digest(path)
        with reject_legacy_language_calls(), patch.object(service, '_file_digest', side_effect=guarded_digest):
            result = self.apply(text, value)
        self.assertFalse(result['instruction_result']['used_reference'])
        self.assertTrue(self.facts()['reference_available'])
        self.assertEqual(len(self.images()), 1)
        self.assertEqual(self.send.call_count, 1)

    def test_reference_context_is_model_requested_and_committed_with_exact_evidence(self):
        self.attach_reference()
        text = '沿着那张示意里的明暗关系调整正脸。'
        value = wire(row('r1', text, kind='visual_goal', patch_value={'lighting': {'key_power': 1.8}}))
        self.send.side_effect = [reply(wire(context_request='reference_image')), reply(value)]
        with reject_legacy_language_calls():
            result = self.apply(text)
        self.assertTrue(result['instruction_result']['used_reference'])
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual([len(self.images(index)) for index in (0, 1)], [1, 2])
        self.assertFalse(self.facts(0)['image_evidence']['reference_image'])
        self.assertTrue(self.facts(1)['image_evidence']['reference_image'])
        self.assertEqual(result['plan']['last_instruction']['protocol'], 'semantic_v2')
        self.assertIn('sha256', result['plan']['last_instruction']['reference_evidence'])

    def test_reference_replaced_after_model_read_prevents_commit(self):
        reference = self.attach_reference()
        text = '沿着那张示意里的明暗关系调整正脸。'
        value = wire(row('r1', text, kind='visual_goal', patch_value={'lighting': {'key_power': 1.8}}))
        calls = []
        def respond(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return reply(wire(context_request='reference_image'))
            Image.new('RGB', (32, 32), 'red').save(reference)
            return reply(value)
        self.send.side_effect = respond
        with reject_legacy_language_calls(), self.assertRaises(service.InstructionError) as caught:
            self.apply(text)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.send.call_count, 2)
        self.assert_unmodified()

    def test_explicit_review_confirms_the_same_partial_semantic_plan_without_second_model_request(self):
        ready = '给正脸透口气'
        pending = '那个小突起也拾掇一下'
        text = ready + '；' + pending + '。'
        value = wire(row('r1', ready, kind='visual_goal', patch_value={'lighting': {'key_power': 1.8}}),
                     row('r2', pending, kind='visual_goal', domains=['material'], status='needs_clarification'))
        with reject_legacy_language_calls():
            prepared = self.apply(text, value, partial_mode='review')
            receipt = prepared['instruction_result']
            self.assertEqual(receipt['status'], 'needs_confirmation')
            self.assertEqual(prepared['version'], 3)
            body = {'proposal_id': receipt['proposal_id'], 'expected_version': 3}
            confirmed = service.confirm_instruction('fixture', body, 'confirm-semantic')
            replay = service.confirm_instruction('fixture', body, 'confirm-semantic')
        self.assertEqual(confirmed['version'], 4)
        self.assertEqual(confirmed['instruction_result']['status'], 'applied')
        self.assertTrue(confirmed['instruction_result']['partial'])
        self.assertEqual(confirmed['instruction_result']['remaining_text'], pending)
        self.assertEqual(engine.effective_plan(confirmed['plan'], 'hero')['lighting']['key_power'], 1.8)
        self.assertTrue(replay['instruction_result']['replayed'])
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(len(store.get('project', 'fixture')['history']), 1)

    def test_client_protocol_flag_does_not_allow_supplied_candidates_or_receipts(self):
        for extra in ({'candidate_plan': self.original['plan']},
                      {'last_instruction': {'protocol': 'semantic_v2', 'source_spans': []}},
                      {'proposal': {'protocol': 'semantic_v2'}}):
            with self.subTest(extra=extra), self.assertRaises(service.InstructionError):
                self.apply('正脸透亮些', protocol='semantic_v2', **extra)
        self.send.assert_not_called()
        self.assert_unmodified()

    def test_offline_legacy_control_is_explicit_and_does_not_claim_image_understanding(self):
        disabled = {'enabled': False, 'provider': 'anthropic', 'api_key': '', 'model': ''}
        with patch.object(ai_config, 'get_config', return_value=disabled), \
             patch.object(ai_config, 'get_vision_config', return_value=disabled):
            result = self.apply('曝光提高0.5EV', protocol='legacy_v1')
        receipt = result['instruction_result']
        self.assertEqual(receipt['source'], 'rules')
        self.assertFalse(receipt['used_image'])
        self.assertFalse(receipt['used_reference'])
        self.assertEqual(engine.effective_plan(result['plan'], 'hero')['exposure'], .5)
        self.send.assert_not_called()

    def test_default_offline_adjustment_ignores_an_unused_attached_reference(self):
        self.attach_reference()
        disabled = {'enabled': False, 'provider': 'anthropic', 'api_key': '', 'model': ''}
        with patch.object(ai_config, 'get_config', return_value=disabled), \
             patch.object(ai_config, 'get_vision_config', return_value=disabled):
            result = self.apply('曝光提高0.5EV')
        receipt = result['instruction_result']
        self.assertEqual(receipt['source'], 'rules')
        self.assertFalse(receipt['used_image'])
        self.assertFalse(receipt['used_reference'])
        self.assertEqual(engine.effective_plan(result['plan'], 'hero')['exposure'], .5)
        self.send.assert_not_called()

    def test_text_route_stays_semantic_with_unused_attachment_and_disabled_vision(self):
        self.attach_reference()
        disabled = {'enabled': False, 'provider': 'anthropic', 'api_key': '', 'model': ''}
        text = '给正脸透口气，别看那张附件。'
        value = wire(row('r1', text, kind='visual_goal', patch_value={'lighting': {'key_power': 1.8}}))
        with patch.object(ai_config, 'get_vision_config', return_value=disabled), reject_legacy_language_calls():
            result = self.apply(text, value, source=None)
        receipt = result['instruction_result']
        self.assertEqual(receipt['protocol'], 'semantic_v2')
        self.assertEqual(receipt['source'], 'ai')
        self.assertFalse(receipt['used_image'])
        self.assertFalse(receipt['used_reference'])
        self.assertFalse(self.facts()['reference_available'])
        self.assertEqual(self.send.call_count, 1)

    def test_default_offline_reference_comparison_is_explicitly_unsupported(self):
        self.attach_reference()
        disabled = {'enabled': False, 'provider': 'anthropic', 'api_key': '', 'model': ''}
        with patch.object(ai_config, 'get_config', return_value=disabled), \
             patch.object(ai_config, 'get_vision_config', return_value=disabled), \
             self.assertRaises(intelligence_rules.InstructionOutcome) as caught:
            self.apply('按参考图调整灯光')
        self.assertEqual(caught.exception.status, 'unsupported')
        self.send.assert_not_called()
        self.assert_unmodified()


if __name__ == '__main__':
    unittest.main()

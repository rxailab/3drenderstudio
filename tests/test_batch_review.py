"""Tests for 2x2 contact-sheet page triage (brandkit_review.triage_pages) and batched scene checking
(scene_imagegen.check_batch, BatchChecker, listing_scenes.recheck_scenes)."""
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brandkit_review  # noqa: E402
import listing_scenes  # noqa: E402
import scene_imagegen  # noqa: E402


def _make_png(path, color=(200, 50, 40), size=(600, 600)):
    Image.new('RGB', size, color).save(path)
    return path


def _good_scores(product=22):
    return {'product': product, 'hierarchy': 18, 'composition': 17, 'task': 13, 'color': 9, 'independence': 8}


def _fidelity_row(slot, **overrides):
    row = {name: True for name in scene_imagegen.FIDELITY_FLAGS}
    row.update(slot=slot, person_visible=False, face_visible=False, issues=[], note='画面正常')
    row.update(overrides)
    return row


class TriagePagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='triage_test_'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ref = _make_png(self.tmp / 'hero.png', (180, 30, 30))
        self.product = {'title': 'Tool Cabinet', 'model': 'G-01D', 'headline': '', 'subline': '', 'language': 'zh',
                        'features': [], 'specs': [], 'figures': [], 'size_mm': 900}

    def test_triage_passes_clean_pages_and_defers_flagged_or_hard_issue_pages(self):
        items = [
            {'kind': 'T12', 'png': _make_png(self.tmp / 'T12.png'), 'job': {'canvas': [1500, 1500], 'assets': {'hero': {}}},
             'measured': {'canvas_px': [1500, 1500]}, 'has_hard_issues': False},
            {'kind': 'T01', 'png': _make_png(self.tmp / 'T01.png'), 'job': {'canvas': [1500, 1500], 'assets': {'hero': {}}},
             'measured': {'canvas_px': [1500, 1500]}, 'has_hard_issues': False},
            {'kind': 'T06', 'png': _make_png(self.tmp / 'T06.png'), 'job': {'canvas': [1500, 1500], 'assets': {'hero': {}}},
             'measured': {'canvas_px': [1500, 1500]}, 'has_hard_issues': False},
            {'kind': 'T05', 'png': _make_png(self.tmp / 'T05.png'), 'job': {'canvas': [1500, 1500], 'assets': {'hero': {}}},
             'measured': {'canvas_px': [1500, 1500]}, 'has_hard_issues': True},
        ]
        seen = []

        def fake_ask(config, system, facts, schema, image_path=None, reference_path=None, caller=None, **kw):
            seen.append((system, caller, image_path, reference_path, len(facts['pages'])))
            self.assertTrue(Path(image_path).is_file())
            with Image.open(image_path) as sheet:
                self.assertGreater(sheet.width, 800)
                self.assertGreater(sheet.height, 800)
            return {'pages': [
                {'slot': 1, 'needs_deep_review': False, 'scores': _good_scores(), 'vetoes': [], 'summary': 'T12 清晰均衡'},
                {'slot': 2, 'needs_deep_review': True, 'scores': _good_scores(), 'vetoes': [], 'summary': 'T01 引线需复核'},
                {'slot': 3, 'needs_deep_review': False, 'scores': _good_scores(product=10), 'vetoes': [], 'summary': 'T06 偏低'},
                {'slot': 4, 'needs_deep_review': False, 'scores': _good_scores(), 'vetoes': [], 'summary': 'T05 表面正常'},
            ]}

        with patch.object(brandkit_review, '_configs', return_value=({'model': 't'}, {'model': 'v'})), \
                patch.object(brandkit_review, '_ask', side_effect=fake_ask):
            passed = brandkit_review.triage_pages(items, self.tmp, self.product, reference=self.ref)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], 'brandkit_review.triage')
        self.assertEqual(seen[0][4], 4)
        # Only slot 1 (T12) clears every gate: slot 2 flagged, slot 3 score=75 < 80, slot 4 has_hard_issues=True
        self.assertEqual(list(passed.keys()), ['T12'])
        self.assertEqual(passed['T12']['score'], 87)
        self.assertTrue(passed['T12']['triage'])
        # Temporary contact sheet is cleaned up
        self.assertFalse(list(self.tmp.glob('_triage_*.jpg')))

    def test_triage_falls_back_on_transient_error_but_propagates_quota_refusal(self):
        items = [
            {'kind': 'T12', 'png': _make_png(self.tmp / 'a.png'), 'job': {'canvas': [1500, 1500], 'assets': {}}, 'measured': {}, 'has_hard_issues': False},
            {'kind': 'T06', 'png': _make_png(self.tmp / 'b.png'), 'job': {'canvas': [1500, 1500], 'assets': {}}, 'measured': {}, 'has_hard_issues': False},
        ]
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'v'})), \
                patch.object(brandkit_review, '_ask', side_effect=RuntimeError('temporary timeout')):
            self.assertEqual(brandkit_review.triage_pages(items, self.tmp, self.product), {})
        with patch.object(brandkit_review, '_configs', return_value=(None, {'model': 'v'})), \
                patch.object(brandkit_review, '_ask', side_effect=RuntimeError('HTTP 402 quota exceeded')):
            with self.assertRaises(RuntimeError):
                brandkit_review.triage_pages(items, self.tmp, self.product)


class SceneBatchCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='scene_batch_test_'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ref = _make_png(self.tmp / 'ref.png', (200, 40, 40), (512, 512))
        self.s1 = _make_png(self.tmp / 's1.png', (40, 120, 200), (512, 512))
        self.s2 = _make_png(self.tmp / 's2.png', (40, 180, 100), (512, 512))
        self.s3 = _make_png(self.tmp / 's3.png', (160, 140, 40), (512, 512))
        self.c1 = _make_png(self.tmp / 'c1.jpg', (120, 120, 120), (800, 400))
        self.c2 = _make_png(self.tmp / 'c2.jpg', (120, 120, 120), (800, 400))
        self.c3 = _make_png(self.tmp / 'c3.jpg', (120, 120, 120), (800, 400))

    def test_check_batch_combines_three_scenes_into_one_vision_call(self):
        items = [
            {'reference_paths': [self.ref], 'generated_path': self.s1, 'product': 'Cabinet', 'people': False, 'closeup': self.c1, 'mounting': 'wall'},
            {'reference_paths': [self.ref], 'generated_path': self.s2, 'product': 'Cabinet', 'people': True, 'framing': 'hands', 'closeup': self.c2},
            {'reference_paths': [self.ref], 'generated_path': self.s3, 'product': 'Cabinet', 'people': False, 'closeup': self.c3},
        ]
        posts = []

        def fake_post(cfg, body):
            posts.append(body)
            return {'ok': True}

        fake_answer = {'results': [
            _fidelity_row(1, person_visible=False),
            _fidelity_row(2, person_visible=True, face_visible=True),               # hands requested -> face fails
            _fidelity_row(3, surroundings_new=False, issues=['scratched bench']),   # worn surroundings fails
        ]}

        import ai_client, ai_config
        with patch.object(ai_config, 'get_vision_config', return_value={'enabled': True, 'api_key': 'k', 'model': 'claude-haiku-5-5', 'provider': 'anthropic'}), \
                patch.object(ai_client, '_post', side_effect=fake_post), \
                patch.object(ai_client, '_response', return_value=fake_answer):
            verdicts = scene_imagegen.check_batch(items)

        # Only ONE vision API call for all 3 scenes, carrying only 2 stitched images (GENERATED_SHEET + CLOSEUP_SHEET)
        self.assertEqual(len(posts), 1)
        self.assertEqual(ai_client._image_count(posts[0]), 2)
        self.assertEqual(verdicts[0]['status'], 'passed')
        self.assertEqual(verdicts[1]['status'], 'failed')
        self.assertFalse(verdicts[1]['people']['ok'])
        self.assertEqual(verdicts[2]['status'], 'failed')
        self.assertFalse(verdicts[2]['flags']['surroundings_new'])

    def test_batch_checker_coalesces_parallel_workers_and_handles_early_leave(self):
        calls = []

        def fake_batch(items):
            calls.append(len(items))
            return [{'status': 'passed', 'flags': {}, 'issues': [], 'note': f'slot {i}'} for i in range(len(items))]

        batch = scene_imagegen.BatchChecker(expected=3, timeout=5.0)
        results = {}

        def worker(idx, leave_early=False):
            with batch.participant():
                if leave_early:
                    batch.leave_current()
                    results[idx] = 'left'
                    return
                results[idx] = batch.submit({'reference_paths': [self.ref], 'generated_path': self.s1, 'product': f'P{idx}'})

        with patch.object(scene_imagegen, 'check_batch', side_effect=fake_batch):
            threads = [
                threading.Thread(target=worker, args=(0, False)),
                threading.Thread(target=worker, args=(1, True)),   # e.g. scene_lock failed before vision check
                threading.Thread(target=worker, args=(2, False)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        self.assertEqual(calls, [2])
        self.assertEqual(results[1], 'left')
        self.assertEqual(results[0]['status'], 'passed')
        self.assertEqual(results[2]['status'], 'passed')

    def test_recheck_scenes_batches_multiple_errored_scenes(self):
        rows = [
            {'path': str(self.s1), 'check': 'unchecked', 'check_error': True,
             'recheck': {'reference': str(self.ref), 'closeup': str(self.c1), 'product': 'Cabinet', 'people': False}},
            {'path': str(self.s2), 'check': 'unchecked', 'check_error': True,
             'recheck': {'reference': str(self.ref), 'closeup': str(self.c2), 'product': 'Cabinet', 'people': False}},
        ]
        seen = []

        def fake_batch(items):
            seen.append(len(items))
            return [{'status': 'passed', 'flags': {}, 'issues': []}, {'status': 'failed', 'flags': {'parts_consistent': False}, 'issues': ['missing']}]

        with patch.object(scene_imagegen, 'check_batch', side_effect=fake_batch):
            out, notes = listing_scenes.recheck_scenes(rows)

        self.assertEqual(seen, [2])
        self.assertEqual(out[0]['check'], 'passed')
        self.assertEqual(out[1]['check'], 'failed')
        self.assertEqual(out[1]['usage_retry'], 0)
        self.assertEqual(len(notes), 2)

    def test_perspective_hint_and_flag_reject_opposite_wall_vanishing_point(self):
        hint_pos = scene_imagegen._perspective_hint(35, 18, 'wall')
        self.assertIn('FRONT-RIGHT', hint_pos)
        self.assertIn('toward the LEFT', hint_pos)
        hint_neg = scene_imagegen._perspective_hint(-30, 15, 'wall')
        self.assertIn('FRONT-LEFT', hint_neg)
        self.assertIn('toward the RIGHT', hint_neg)
        p_text = scene_imagegen.prompt('Tool cabinet', 660, 'A clean workshop wall', mounting='wall', azimuth=35, elevation=18)
        self.assertIn('CAMERA & WALL PERSPECTIVE (CRITICAL)', p_text)
        raw = _fidelity_row(1, perspective_consistent=False, issues=['Wall recedes right while cabinet recedes left'])
        verdict = scene_imagegen._verdict_from_answer(raw, people=False, mounting='wall', azimuth=35, elevation=18)
        self.assertEqual(verdict['status'], 'failed')
        self.assertFalse(verdict['flags']['perspective_consistent'])
        self.assertIn('透视方向', verdict['note'])
        self.assertTrue(any('toward the LEFT' in issue for issue in verdict['issues']))


if __name__ == '__main__':
    unittest.main()

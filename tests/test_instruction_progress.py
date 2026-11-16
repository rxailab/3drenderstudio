import threading
import unittest
from unittest.mock import patch

import instruction_progress
import instruction_service as service


class InstructionProgressTests(unittest.TestCase):
    def tearDown(self):
        instruction_progress.clear()
        with service.LOCK:
            service._INFLIGHT.clear()

    @patch.object(service, 'get', return_value={'id': 'p'})
    def test_only_public_stage_for_the_requested_project(self, _get):
        with service.LOCK:
            service._INFLIGHT['p'] = {'token': 'secret', 'key_hash': 'secret', 'digest': 'secret',
                                      'activity': {'agent': '场景检查', 'stage': '读取场景'}}
        self.assertEqual(service.instruction_progress_view('p'), {'agent': '场景检查', 'stage': '读取场景'})
        self.assertIsNone(service.instruction_progress_view('other'))
        service._report_instruction_progress('p', 'wrong', 'bad', 'bad')
        self.assertEqual(service.instruction_progress_view('p')['agent'], '场景检查')
        service._report_instruction_progress('p', 'secret', '材质 Agent', '处理材质')
        result = service.instruction_progress_view('p')
        result['stage'] = 'changed locally'
        self.assertEqual(service.instruction_progress_view('p')['stage'], '处理材质')

    def test_progress_callback_is_isolated_between_threads_and_cleared(self):
        events = []
        instruction_progress.bind(lambda agent, stage: events.append((agent, stage)))
        thread = threading.Thread(target=lambda: instruction_progress.report('other', 'other'))
        thread.start(); thread.join()
        instruction_progress.report('planner', 'running')
        instruction_progress.clear()
        instruction_progress.report('late', 'ignored')
        self.assertEqual(events, [('planner', 'running')])

    @patch.object(service, 'get', return_value={'id': 'p'})
    def test_http_progress_contains_only_public_fields(self, _get):
        import app
        with service.LOCK:
            service._INFLIGHT['p'] = {'token': 'secret', 'digest': 'secret',
                                      'activity': {'agent': '方案规划 Agent', 'stage': '规划中'}}
        with app.app.test_client() as client:
            response = client.get('/api/projects/p/instruction-progress', headers={'Host': '127.0.0.1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'activity': {'agent': '方案规划 Agent', 'stage': '规划中'}})

"""L2 recovery through real control/ledger code; API responses are simulated."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_resources as resources
import test_delegated_l1 as delegated
import test_runtime_controller as fixtures
from dot_runtime import api_recovery, planning
from dot_runtime.controller import read_json, file_hash
from dot_runtime.gpt_dot import researched_request
from dot_runtime.input_material import material_packet, read_evidence, encoded
from dot_runtime.report_markdown import final


def plan_response(plan=None):
    return delegated.call_response('propose_plan', {'plan_json': json.dumps(plan or fixtures.fixture_plan())})


class L2RecoveryTests(unittest.TestCase):
    setUp = resources.ResourceTests.setUp
    controller = resources.ResourceTests.controller
    bridge = resources.ResourceTests.bridge
    allocate = resources.ResourceTests.allocate

    def test_accumulated_research_reaches_plan_without_new_l1_review_or_lost_evidence(self):
        b = self.bridge([plan_response()]); self.allocate(b)
        observations = [{'path': f'research/{i}/record.json', 'status': 'FAIL' if i == 23 else 'PASS',
                         'question': 'Locate the SDK', 'summary': '历史调查材料' * 3000,
                         'findings': ['Exact tool path still needs verification']} for i in range(24)]
        before = copy.deepcopy(observations)
        authority = self.delegation_path.read_bytes()
        with patch('dot_runtime.research.context', return_value=observations):
            result = b.call('dot_plan', {'requirements': 'Keep LED behavior and all original acceptance criteria'})
        self.assertIn('plan', result)
        self.assertEqual(len(self.requests), 1)
        self.assertLess(len(json.dumps(self.requests[0], ensure_ascii=False).encode()), 250000)
        archives = list((b.root / 'api/materials').glob('*.json'))
        self.assertTrue(any(read_json(p) == before for p in archives))
        self.assertEqual(observations, before)
        self.assertEqual(self.delegation_path.read_bytes(), authority)
        self.assertFalse(b.workers.calls)
        self.assertFalse((b.root / 'preflight-report.json').exists())

    def test_dot_reads_omitted_evidence_itself_without_product_dispatch(self):
        b = self.bridge([]); self.allocate(b)
        packet = material_packet(b.root, {'failure': 'SDK unavailable', 'log': 'x' * 30000}, 2000)
        arguments = {'path': packet['source_record'], 'sha256': packet['source_sha256'], 'offset': '0'}
        self.responses.extend([delegated.call_response('read_evidence', arguments), plan_response()])
        researched_request(b.client(), 'Inspect evidence ' + json.dumps(packet))
        output = self.requests[-1]['input'][-1]
        self.assertEqual(output['call_id'], 'call-test')
        self.assertIn('SDK unavailable', output['output'])
        self.assertIn('next_offset', output['output'])
        self.assertFalse(b.workers.calls)
        with self.assertRaisesRegex(ValueError, 'changed'):
            read_evidence(b.root, {**arguments, 'sha256': '0' * 64})
        for path in ('../delegation.json', 'api/secret.json', 'C:/outside.json'):
            with self.assertRaises(ValueError):
                read_evidence(b.root, {**arguments, 'path': path})

    def test_l1_status_is_concise_and_original_research_remains_available(self):
        from dot_runtime.presentation import compact
        original = {'state': 'NEEDS_INFO', 'research': [
            {'path': f'research/{i}/record.json', 'status': 'FAIL', 'sha256': 'f' * 64,
             'summary': '调查历史' * 10000, 'evidence_excerpt': [{'output': 'log' * 10000}]}
            for i in range(40)]}
        before = copy.deepcopy(original)
        reply = compact(original)
        self.assertEqual(reply['research_record_count'], 40)
        self.assertEqual(reply['research_omitted_count'], 8)
        self.assertTrue(all(item['status'] == 'FAIL' for item in reply['research']))
        self.assertTrue(all(isinstance(item['summary'], str) and item['summary_abridged'] for item in reply['research']))
        self.assertLess(len(encoded(reply)), 25000)
        self.assertEqual(original, before)

    def test_l1_can_reapprove_equivalent_concise_requirements_then_continue_same_task(self):
        from dot_runtime import requirements_review
        b = self.bridge([plan_response()]); b.settings['requirements_first'] = True
        b.settings['environment_context'] = 'Environment evidence. ' * 720
        self.allocate(b)
        original_authority = self.delegation_path.read_bytes()
        concise = 'Return the specified value; keep all constraints and independent tests.'
        # Keep the baseline within its 200 KB gate, but exhaust the combined API
        # payload with other valid material to exercise independent reapproval.
        oversized = concise + '\n' + ('Repeated background only. ' * 7900)[:199000]
        b.call('l1_prepare_requirements', {'requirements': oversized})
        self.assertEqual(b.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'L1'})['state'], 'REQUIREMENTS_APPROVED')
        refused = b.call('dot_plan', {'requirements': oversized})
        self.assertEqual(refused['input_rejection']['outcome'], 'NOT_SENT')
        self.assertEqual(len(self.requests), 0)
        self.assertFalse(b.call('dot_plan', {'requirements': concise})['accepted'])
        self.assertEqual(len(self.requests), 0, 'Unapproved concise replacement cannot bypass the baseline')
        self.assertEqual(b.call('l1_prepare_requirements', {'requirements': concise})['state'], 'REQUIREMENTS_PENDING')
        approved = b.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'L1'})
        self.assertEqual(approved['requirements_approval']['state'], 'REQUIREMENTS_APPROVED')
        self.assertIn('dot_plan', approved['next'])
        self.assertIn('plan', b.call('dot_plan', {'requirements': concise}))
        self.assertEqual(requirements_review.review_count(b.root), 2)
        self.assertEqual(requirements_review.revision_count(b.root), 1)
        self.assertEqual(b.workers.calls, ['review', 'review'])
        self.assertEqual(self.delegation_path.read_bytes(), original_authority)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse((b.root / 'preflight-report.json').exists())

    def test_long_tool_history_is_reduced_without_replay_or_losing_reasoning_ids(self):
        b = self.bridge([]); self.allocate(b)
        client = b.client(); client.local_tools = None; client.host_tools = None
        client.research = Mock(return_value={'status': 'PASS', 'summary': 'evidence' * 40000})
        for i in range(8):
            response = delegated.call_response('request_investigation', {'question': 'Check ' + str(i)})
            response['output'][0]['call_id'] = 'call-' + str(i)
            response['output'].insert(0, {'type': 'reasoning', 'id': 'rs-' + str(i),
                                         'summary': [], 'encrypted_content': 'opaque-' + str(i)})
            self.responses.append(response)
        self.responses.append(plan_response())
        researched_request(client, 'Read evidence; preserve all failed results')
        self.assertEqual(client.research.call_count, 8)
        self.assertEqual(len(self.requests), 9)
        history = self.requests[-1]['input'][2:]
        self.assertEqual([x['call_id'] for x in history if x.get('type') == 'function_call_output'],
                         ['call-' + str(i) for i in range(8)])
        self.assertEqual([x['encrypted_content'] for x in history if x.get('type') == 'reasoning'],
                         ['opaque-' + str(i) for i in range(8)])
        self.assertLess(len(encoded(history)), 100000)
        self.assertTrue(list((b.root / 'api/materials').glob('*.json')))

    def test_one_pure_draft_correction_retains_rejection_and_budget(self):
        bad = delegated.call_response('propose_plan', {'plan_json': '{'})
        b = self.bridge([bad, plan_response()]); self.allocate(b)
        original = self.delegation_path.read_bytes()
        result = b.call('dot_plan', {'requirements': 'Unchanged approved scope'})
        self.assertIn('plan', result)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(planning.history(b.root)), 1)
        self.assertEqual(planning.history(b.root)[0]['code'], 'PLAN_JSON')
        self.assertEqual(len(read_json(b.root / 'api/ledger.json')['attempts']), 2)
        self.assertFalse(b.workers.calls)
        self.assertEqual(self.delegation_path.read_bytes(), original)
        self.assertIn('L2 DRAFT CORRECTION', json.dumps(self.requests[-1]))

    def test_repeated_invalid_draft_stops_at_l1_decision_not_final(self):
        bad = delegated.call_response('propose_plan', {'plan_json': '{'})
        b = self.bridge([bad, bad]); self.allocate(b)
        result = b.call('dot_plan', {'requirements': 'Keep scope'})
        self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(planning.history(b.root)), 2)
        self.assertFalse((b.root / 'preflight-report.json').exists())
        self.assertFalse(b.workers.calls)

    def test_uncertain_api_is_never_automatically_retried(self):
        b = self.bridge([]); self.allocate(b)
        client = b.client()
        client.send = Mock(side_effect=api_recovery.ApiReview({'version': 1, 'code': 'API_TIMEOUT', 'outcome': 'UNKNOWN'}))
        from dot_runtime.gpt_dot import propose_plan
        with self.assertRaises(api_recovery.ApiReview):
            propose_plan(client, 'Keep scope', fixtures.fixture_plan(), '')
        client.send.assert_called_once()
        self.assertFalse(planning.history(b.root))

    def test_large_final_report_has_small_view_and_unchanged_original(self):
        b = self.bridge([])
        report = {'outcome': 'FAILED', 'reason': 'User stopped; no HIL evidence',
                  'l1_submission': {'summary': 'Implementation incomplete', 'dot_report_sha256': 'f' * 64},
                  'issues': [{'root_cause': 'test_infrastructure', 'failure': {'log': 'LOG' * 2000000}}],
                  'resource_governance': {'actual_usage': {'api_calls': 29},
                      'dot_api_reported_usage': {'unreported_requests': 1},
                      'history': [{'record': {'kind': 'l1_allocation', 'payload': {
                          'before': {'api_calls': 30}, 'after': {'reason': 'Diagnosed repair needs another request',
                                                              'limits': {'api_calls': 40}}}}}]}}
        before = copy.deepcopy(report)
        descriptor = final(b.root / 'run', report)
        text = Path(descriptor['path']).read_text(encoding='utf-8')
        self.assertLess(len(text.encode('utf-8')), 16000)
        for required in ('FAILED', 'no HIL evidence', 'test_infrastructure', 'unreported_requests', 'final-report.json',
                         'Diagnosed repair needs another request', '"api_calls": 30', '"api_calls": 40'):
            self.assertIn(required, text)
        self.assertEqual(report, before)
        self.assertEqual(file_hash(descriptor['path']), descriptor['sha256'])


if __name__ == '__main__':
    unittest.main()

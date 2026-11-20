"""Oversized evidence regression; real local controller, simulated API/worker outputs."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

import test_runtime_controller as fixtures
import test_delegated_l1 as delegated
import test_resources as resource_fixtures
from dot_runtime.controller import read_json, file_hash
from dot_runtime.gpt_dot import ResponsesClient, DotAdapter
from dot_runtime.input_material import InputRejected, triage_packet, encoded
from dot_runtime.presentation import read_record


def large_failure():
    value = fixtures.result('FAIL', 'task_plan_or_design')
    value['summary'] = 'LED service lies outside main/ and tests/; environment also failed.'
    value['runtime_evidence']['command_events'][0]['aggregated_output'] = '原始日志\n' * 25000
    return value


def diagnosis(cause='task_plan_or_design'):
    result = {'status': 'FAIL', 'summary': 'Requested implementation exceeds authorized paths',
              'findings': ['L1 must assess required scope; do not retry implementation'],
              'root_cause': cause, 'root_cause_key': 'fixture.led-service.scope',
              'evidence': [{'kind': 'review', 'description': 'Worker reported scope conflict',
                            'command': '', 'output': '', 'exit_code': 0, 'test_count': 0}]}
    result.update(fixtures.triage_fields(cause))
    return {'id': 'response-triage', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]}


class InputMaterialTests(unittest.TestCase):
    setUp = resource_fixtures.ResourceTests.setUp
    controller = resource_fixtures.ResourceTests.controller
    bridge = resource_fixtures.ResourceTests.bridge
    allocate = resource_fixtures.ResourceTests.allocate
    prepared = resource_fixtures.ResourceTests.prepared

    def test_complete_original_is_preserved_and_paged_view_is_explicit(self):
        b, c = self.prepared()
        failure = large_failure(); before = copy.deepcopy(failure)
        packet = triage_packet(c.run_dir, failure, [])
        self.assertTrue(packet['abridged'])
        self.assertLess(len(encoded(packet)), 65000)
        self.assertEqual(failure, before)
        path = b.root / packet['source_record']
        self.assertEqual(read_json(path), {'failure': before, 'previous_issues': []})
        self.assertEqual(file_hash(path), packet['source_sha256'])
        page = read_record(b.root, packet['source_record'], '0')
        self.assertEqual(page['sha256'], packet['source_sha256'])
        self.assertIsNotNone(page['next_offset'])

    def test_large_failed_worker_reaches_dot_and_owner_decision_not_final_unknown(self):
        b, c = self.prepared()
        b.workers.overrides = [('implementation', large_failure())]
        send = Mock(side_effect=[delegated.call_response('run_approved_workflow'), diagnosis()])
        b.client_factory = lambda m,p,d: ResponsesClient(m,p,d,send)
        result = b.call('dot_run', {})
        self.assertEqual(result['state'], 'DECISION_PENDING')
        self.assertIsNone(result['report'])
        self.assertEqual(result['implementation_attempts'], 1)
        self.assertEqual(send.call_count, 2)
        prompt = send.call_args.args[0]['input'][1]['content']
        self.assertLess(len(prompt.encode('utf-8')), 250000)
        self.assertIn('source_record', prompt)
        self.assertEqual(read_json(c.state_path)['issues'][0]['root_cause'], 'task_plan_or_design')
        self.assertFalse((b.run_dir / 'final-report.json').exists())

    def test_unsent_refusal_preserves_pending_failure_and_usage_and_can_revise(self):
        b, c = self.prepared()
        b.workers.overrides = [('implementation', large_failure())]
        send = Mock(return_value=delegated.call_response('run_approved_workflow'))
        b.client_factory = lambda m,p,d: ResponsesClient(m,p,d,send)
        original_prompt = type(c).prompt
        def oversized(controller, role, instruction):
            value = original_prompt(controller, role, instruction)
            return value + 'x' * 260000 if role == 'triage' else value
        with patch.object(type(c), 'prompt', oversized):
            result = b.call('dot_run', {})
        self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(result['input_rejection']['outcome'], 'NOT_SENT')
        self.assertEqual(result['state'], 'DECISION_PENDING')
        state = read_json(c.state_path)
        self.assertIsNone(state['active_run']); self.assertIsNone(state['report'])
        self.assertTrue(state['pending_triage']['failure'])
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertEqual(state['root_cause_counts'], {})
        self.assertEqual(state['issues'], [])
        send.assert_called_once()  # Only the dispatch request; rejected triage was never sent.
        self.assertEqual(len(read_json(b.root / 'api/ledger.json')['attempts']), 2)  # plan + dispatch
        original = self.delegation_path.read_bytes()
        result = b.call('l1_revise', {'requirements': 'Same target; corrected execution approach',
            'plan_json': json.dumps(fixtures.fixture_plan())})
        self.assertEqual(result['state'], 'REVIEW_PENDING')
        self.assertEqual(result['implementation_attempts'], 1)
        self.assertEqual(self.delegation_path.read_bytes(), original)
        self.assertFalse((b.run_dir / 'final-report.json').exists())
        self.assertTrue(list((b.root / 'input-rejections').glob('*.json')))
        for kind in ('requirements', 'plan'):
            b.call('l1_approve', {'kind': kind, 'l1_session_id': 'L1'})
        self.assertEqual(b.status()['state'], 'EXECUTING')
        self.assertEqual(b.status()['implementation_attempts'], 1)

    def test_planning_refusal_can_be_corrected_without_ledger_charge_or_final_report(self):
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})])
        self.allocate(b)
        result = b.call('dot_plan', {'requirements': '中' * 100000})
        self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(result['input_rejection']['outcome'], 'NOT_SENT')
        self.assertFalse(self.requests)
        self.assertEqual(result['recovery']['coordinator'], 'L1')
        self.assertFalse(result['recovery']['user_action_required'])
        self.assertEqual(result['input_rejection']['planning_component_bytes']['approved_requirements'], 300000)
        self.assertFalse((b.root / 'api/ledger.json').exists())
        self.assertFalse((b.root / 'preflight-report.json').exists())
        self.assertIn('plan', b.call('dot_plan', {'requirements': 'Return the specified value'}))

    def test_abridged_insufficient_evidence_returns_to_l1_without_unknown_final(self):
        b, c = self.prepared()
        client = ResponsesClient('fixture-model', b.root / 'api', self.original, Mock(return_value=diagnosis('needs_analysis')))
        client.governance_root = b.root
        c.adapter = DotAdapter(client, b.workers)
        self.assertEqual(c.triage(large_failure(), self.project), 'stop')
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertIn('source_record', read_json(b.run_dir / 'decision-request.json'))

    def test_unresolved_worker_is_analyzed_read_only_without_clearing_product_guard(self):
        b, c = self.prepared()
        send = Mock(return_value=diagnosis()); c.adapter = DotAdapter(ResponsesClient('fixture-model', b.root / 'api', self.original, send), b.workers)
        c.triage(fixtures.result('UNKNOWN'), self.project)
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertTrue(c.state['active_run'])
        self.assertEqual(c.state['implementation_attempts'], 0)
        send.assert_called_once()

    def test_many_prior_failures_are_bounded_without_deleting_history(self):
        b, c = self.prepared()
        previous = [{'id': str(i), 'failure': large_failure()} for i in range(30)]
        packet = triage_packet(c.run_dir, large_failure(), previous)
        self.assertLess(len(encoded(packet)), 65000)
        self.assertEqual(len(read_json(b.root / packet['source_record'])['previous_issues']), 30)

    def test_input_exception_cannot_be_swallowed_as_generic_unknown(self):
        b, c = self.prepared()
        send = Mock()
        adapter = DotAdapter(ResponsesClient('fixture-model', b.root / 'api', self.original, send), b.workers)
        before = (b.root / 'api/ledger.json').read_bytes()
        with self.assertRaises(InputRejected):
            adapter.run('triage', 'x' * 260000, self.project, b.run_dir / 'not-started', 15)
        self.assertEqual((b.root / 'api/ledger.json').read_bytes(), before)
        send.assert_not_called()

    def test_undiagnosed_small_report_remains_open_and_l1_can_request_scope(self):
        b, c = self.prepared()
        client = ResponsesClient('fixture-model', b.root / 'api', self.original,
                                 Mock(return_value=diagnosis('needs_analysis')))
        client.governance_root = b.root
        c.adapter = DotAdapter(client, b.workers)
        c.triage(fixtures.result('FAIL'), self.project)
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertEqual(c.state['root_cause_counts'], {})
        proposal = resource_fixtures.amendment({'allowed_paths': ['value.py', 'components/']})
        reply = b.call('l1_request_authorization', {'proposal_json': json.dumps(proposal), 'l1_session_id': 'L1'})
        self.assertEqual(reply['state'], 'AUTHORIZATION_PENDING')
        self.assertIsNone(reply['report'])
        self.assertEqual(read_json(self.delegation_path)['allowed_paths'], ['value.py'])

    def test_completed_invalid_l2_response_needs_investigation_not_final_report(self):
        b, c = self.prepared()
        response = diagnosis(); response['output'][0]['content'][0]['text'] = 'not-json'
        client = ResponsesClient('fixture-model', b.root / 'api', self.original, Mock(return_value=response))
        client.governance_root = b.root; c.adapter = DotAdapter(client, b.workers)
        c.triage(fixtures.result('FAIL'), self.project)
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertIsNone(c.state['active_run'])

    def test_unresolved_analysis_process_does_not_allow_product_replay(self):
        b, c = self.prepared()
        c.adapter = fixtures.FakeAdapter([('triage', fixtures.result('UNKNOWN'))])
        c.triage(fixtures.result('FAIL'), self.project)
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertEqual(c.state['active_run']['role'], 'triage')
        with self.assertRaises(ValueError): c.revise(self.requirements, self.plan)
        self.assertEqual(c.adapter.calls, ['triage'])

    def test_policy_and_schema_reject_old_undiagnosed_terminal_route(self):
        import sys
        from dot_runtime.adapter import ROOT_CAUSES
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from validate_repo import validate_input_material_policy
        self.assertNotIn('unknown', ROOT_CAUSES)
        value = read_json(fixtures.ROOT / 'policy/input-material.json')
        self.assertEqual(validate_input_material_policy(value), [])
        for key in ('auto_finalize_undiagnosed_cause', 'preserve_original_evidence',
                    'local_rejection_consumes_api_attempt', 'clear_unresolved_product_operation'):
            bad = dict(value); bad[key] = not bad[key]
            self.assertTrue(validate_input_material_policy(bad))

"""Behavioral tests for delegated authority; fake AI results do not attest live API/Harness operation."""
from datetime import datetime, timedelta, timezone
from closure_fixtures import assessment, reason as closure_reason
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_runtime_controller as fixtures
from dot_runtime.controller import Controller, atomic_json, file_hash, read_json
from dot_runtime.delegation import attach_delegation, approve_l1, submit_l1_report
from dot_runtime.gpt_dot import ResponsesClient, DotAdapter, execute, propose_plan, one_call
from dot_runtime.bridge import Bridge


def delegation(raw_input):
    return {'schema_version': 1, 'authority': 'delegated_l1',
        'source_message': 'User delegates requirements and plan approval; reviews only final report.',
        'raw_input_sha256': file_hash(raw_input),
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'hard_constraints': ['Return the specified value; no external actions'], 'allowed_paths': ['value.py'],
        'external_delivery': False,
        'limits': {'implementation_attempts': 3, 'same_root_cause_attempts': 2, 'infrastructure_retries': 1,
            'timeout_seconds': 15, 'l1_reviews': 6, 'revisions': 2, 'api_calls': 8,
            'api_max_output_tokens': 2000, 'wall_time_seconds': 3600}}


def call_response(name, arguments=None):
    return {'id': 'resp-test', 'status': 'completed', 'output': [{'type': 'function_call',
        'name': name, 'arguments': json.dumps(arguments or {}), 'call_id': 'call-test'}]}


class DelegatedTests(unittest.TestCase):
    controller = fixtures.ControllerTests.controller

    def setUp(self):
        fixtures.ControllerTests.setUp(self)
        self.raw = self.root / 'raw.md'
        self.raw.write_text('Implement the specified fixture value', encoding='utf-8')
        self.delegation_path = self.root / 'delegation.json'
        atomic_json(self.delegation_path, delegation(self.raw))
        self.c = self.controller()
        attach_delegation(self.c, self.delegation_path, self.raw)

    def approved(self):
        approve_l1(self.c, 'requirements', 'l1-author', self.c.adapter)
        approve_l1(self.c, 'plan', 'l1-author', self.c.adapter)
        return self.c

    def test_delegated_full_cycle_has_only_final_human_approval(self):
        c = self.approved()
        self.assertEqual(c.state['state'], 'EXECUTING')
        self.assertEqual(c.state['approvals']['plan']['authority'], 'delegated_l1')
        c.run()
        self.assertEqual(c.state['report']['outcome'], 'SUCCESS')
        with self.assertRaises(ValueError):
            c.approve('report', lambda *_: True)
        submit_l1_report(c, 'AC fulfilled; evidence independently checked', 'l1-author')
        c.approve('report', lambda *_: True)
        self.assertEqual(c.state['state'], 'DELIVERY_READY')
        self.assertFalse(read_json(self.run_dir / 'final-report.json')['delivery_executed'])

    def test_no_human_impersonation_for_intermediate_approval(self):
        with self.assertRaises(ValueError):
            self.c.approve('requirements', lambda *_: True)
        with self.assertRaises(ValueError):
            approve_l1(self.c, 'report', 'l1-author', self.c.adapter)

    def test_author_cannot_be_its_independent_reviewer(self):
        approve_l1(self.c, 'requirements', 'session-1', self.c.adapter)
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertNotIn('requirements', self.c.state['approvals'])

    def test_rejected_review_reflows_without_human_checkpoint(self):
        reviewer = fixtures.FakeAdapter([('review', fixtures.result('FAIL'))])
        approve_l1(self.c, 'requirements', 'l1-author', reviewer)
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.c.revise(self.requirements, self.plan)
        self.assertEqual(self.c.state['revision_count'], 1)
        self.assertEqual(len(self.c.state['l1_reviews']), 1)

    def test_changed_raw_input_or_envelope_rejected(self):
        self.approved()
        self.raw.write_text('Weaken target', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.c.check_approvals()

    def test_changed_delegation_cannot_expand_budget(self):
        value = read_json(self.delegation_path)
        value['limits']['implementation_attempts'] = 100
        atomic_json(self.delegation_path, value)
        with self.assertRaises(ValueError):
            approve_l1(self.c, 'requirements', 'l1-author', self.c.adapter)

    def test_plan_paths_and_budgets_cannot_expand(self):
        for field, value in [('allowed_paths', ['.']), ('budgets', dict(fixtures.fixture_plan()['budgets'], implementation_attempts=4))]:
            plan = fixtures.fixture_plan()
            plan[field] = value
            atomic_json(self.plan, plan)
            with self.assertRaises(ValueError):
                approve_l1(self.c, 'requirements', 'l1-author', self.c.adapter)

    def test_changed_review_evidence_prevents_dispatch(self):
        self.approved()
        evidence = self.c.state['l1_reviews'][0]['evidence_files'][0]
        (self.run_dir / evidence['path']).write_text('tampered', encoding='utf-8')
        self.c.run()
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertNotIn('implementation', self.c.adapter.calls)

    def test_review_attempt_is_reserved_and_unknown_is_not_retried(self):
        reviewer = fixtures.FakeAdapter([('review', fixtures.result('UNKNOWN'))])
        approve_l1(self.c, 'requirements', 'l1-author', reviewer)
        self.assertEqual(len(self.c.state['l1_reviews']), 1)
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(self.c.state['report'])
        self.assertIsNotNone(self.c.state['active_run'])
        with self.assertRaises(ValueError):
            approve_l1(self.c, 'requirements', 'l1-author', reviewer)
        self.assertEqual(len(reviewer.calls), 1)

    def test_l1_cannot_upgrade_failed_outcome(self):
        self.c.report('FAILED', 'Missing required environment', l1_assessment=assessment())
        submit_l1_report(self.c, 'Text cannot turn this into success', 'l1-author')
        self.assertEqual(read_json(self.run_dir / 'final-report.json')['outcome'], 'FAILED')
        self.c.approve('report', lambda *_: True)
        self.assertEqual(self.c.state['state'], 'BLOCKED')
        with self.assertRaises(ValueError):
            self.c.revise(self.requirements, self.plan)

    def test_failure_report_can_be_reviewed_after_expiry(self):
        self.c.report('FAILED', 'Budget exhausted', l1_assessment=assessment())
        value = read_json(self.delegation_path)
        value['expires_at'] = '2000-01-01T00:00:00+00:00'
        atomic_json(self.delegation_path, value)
        submit_l1_report(self.c, 'No execution completed', 'l1-author')
        self.c.approve('report', lambda *_: True)
        self.assertEqual(self.c.state['state'], 'BLOCKED')

    def test_final_human_review_is_not_timed_as_execution(self):
        import time
        c = self.approved()
        c.run()
        c.state['delegated_started_at'] = time.time() - 4000
        c.save()
        submit_l1_report(c, 'Completed evidence is unchanged; review is later', 'l1-author')
        c.approve('report', lambda *_: True)
        self.assertEqual(c.state['state'], 'DELIVERY_READY')
        with self.assertRaises(ValueError):
            c.run()

    def client(self, send, **limits):
        value = read_json(self.delegation_path)
        value['limits'].update(limits)
        return ResponsesClient('configured-model', self.root / 'api', value, send=send)

    def test_api_plans_are_drafts_and_validated(self):
        client = self.client(lambda *_: call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())}))
        plan = propose_plan(client, 'requirements', fixtures.fixture_plan(), 'Python test environment')
        self.assertEqual(plan['allowed_paths'], ['value.py'])
        self.assertFalse(self.c.state['approvals'])

    def test_api_function_allowlist_rejects_approval_and_multiple_calls(self):
        with self.assertRaises(ValueError):
            one_call(call_response('approve'), {'run_approved_workflow', 'stop'})
        response = call_response('run_approved_workflow')
        response['output'] *= 2
        with self.assertRaises(ValueError):
            one_call(response, {'run_approved_workflow'})

    def test_api_dispatch_uses_actual_controller_result(self):
        c = self.approved()
        client = self.client(lambda *_: call_response('run_approved_workflow'))
        execute(client, c)
        self.assertEqual(c.state['state'], 'REPORT_PENDING')
        self.assertEqual(c.state['report']['outcome'], 'SUCCESS')
        self.assertNotIn('report', c.state['approvals'])

    def test_api_failure_consumes_budget_and_has_no_auto_retry(self):
        attempts = []
        def broken(*args):
            attempts.append(args)
            raise ValueError('network failure')
        client = self.client(broken, api_calls=1)
        with self.assertRaises(ValueError):
            client.request('test')
        client = self.client(broken, api_calls=1)  # Simulate restart.
        with self.assertRaises(ValueError):
            client.request('test')
        self.assertEqual(len(attempts), 1)
        self.assertEqual(read_json(self.root / 'api/ledger.json')['attempts'][0]['status'], 'FAILED_OR_UNKNOWN')

    def test_incomplete_api_response_cannot_dispatch(self):
        from dot_runtime.api_recovery import ApiReview
        self.approved()
        client = self.client(lambda *_: dict(call_response('run_approved_workflow'), status='incomplete'))
        with self.assertRaises(ApiReview):
            execute(client, self.c)
        self.assertNotIn('implementation', self.c.adapter.calls)

    def test_bridge_exposes_no_final_approval_or_delivery_tool(self):
        settings = {'project': str(self.project), 'control_dir': str(self.root / 'bridge'),
            'raw_input': str(self.raw), 'delegation': str(self.delegation_path), 'model': 'configured-model',
            'environment_context': 'Python test environment'}
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-secret'}):
            bridge = Bridge(settings, workers=self.c.adapter)
        names = {tool['name'] for tool in bridge.tools()}
        self.assertNotIn('approve_report', names)
        self.assertNotIn('deliver', names)
        self.assertNotIn('OPENAI_API_KEY', bridge.worker_environment)
        with self.assertRaises(ValueError):
            bridge.call('l1_approve', {'kind': 'report', 'l1_session_id': 'author', 'path': 'escape'})

    def test_stdio_protocol_discovery_and_errors(self):
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from l1_bridge import dispatch
        class Stub:
            def tools(self):
                return []
        response = dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, Stub())
        self.assertEqual(response['result']['protocolVersion'], '2025-03-26')
        self.assertIsNone(dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, Stub()))
        self.assertIn('error', dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'unknown'}, Stub()))

    def test_bridge_lifecycle_with_actual_test_command_and_mock_models(self):
        # Two approval reviews, then implementation is FakeAdapter call 3.
        (self.project / 'test_value.py').write_text(
            'import unittest\nfrom value import value\nclass ValueTest(unittest.TestCase):\n'
            '    def test_value(self):\n        self.assertEqual(value(), 3)\n', encoding='utf-8')
        plan = fixtures.fixture_plan()
        plan['verification']['test_command'] = [sys.executable, '-B', '-m', 'unittest', '-v']
        plan['verification']['test_contract']['required_tests'] = ['test_value.ValueTest.test_value']
        responses = [call_response('propose_plan', {'plan_json': json.dumps(plan)}), call_response('run_approved_workflow')]
        def factory(model, path, envelope):
            return ResponsesClient(model, path, envelope, send=lambda *_: responses.pop(0))
        settings = {'project': str(self.project), 'control_dir': str(self.root / 'bridge'),
            'raw_input': str(self.raw), 'delegation': str(self.delegation_path), 'model': 'configured-model',
            'environment_context': 'Python test environment'}
        bridge = Bridge(settings, client_factory=factory, workers=fixtures.FakeAdapter())
        bridge.call('dot_plan', {'requirements': self.requirements.read_text(encoding='utf-8')})
        self.assertEqual(bridge.call('dot_initialize', {})['state'], 'REVIEW_PENDING')
        for kind in ('requirements', 'plan'):
            bridge.call('l1_approve', {'kind': kind, 'l1_session_id': 'author'})
        state = bridge.call('dot_run', {})
        self.assertEqual(state['state'], 'REPORT_PENDING')
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        self.assertEqual(state['final_report']['approved_test_commands'][0]['observed_test_count'], 1)
        bridge.call('l1_submit_report', {'summary': 'Actual fixture test passed; AI mocked', 'l1_session_id': 'author'})
        c = Controller(bridge.run_dir)
        c.approve('report', lambda *_: True)
        self.assertEqual(c.state['state'], 'DELIVERY_READY')
        self.assertEqual(len(read_json(bridge.root / 'api/ledger.json')['attempts']), 2)

    def test_revision_limit_requests_l1_assessment(self):
        self.c.state['revision_count'] = 2
        self.c.transition('DECISION_PENDING', 'Fixture review request')
        self.c.revise(self.requirements, self.plan)
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(self.c.state['report'])

    def test_expired_execution_budget_prevents_new_review(self):
        import time
        self.c.state['delegated_started_at'] = time.time() - 4000
        self.c.save()
        with self.assertRaises(ValueError):
            approve_l1(self.c, 'requirements', 'author', self.c.adapter)
        self.assertFalse(self.c.adapter.calls)


if __name__ == '__main__':
    unittest.main()

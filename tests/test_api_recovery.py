"""API pause/review invariants with synthetic receipts; no external API or product execution."""
from closure_fixtures import assessment, reason as closure_reason
import copy
import io
import json
import socket
import ssl
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.error

import test_delegated_l1 as delegated
import test_resources as resource_fixtures
import test_onboarding as onboarding_fixtures
import test_runtime_controller as fixtures
from dot_runtime import api_recovery as api, resources
from dot_runtime.gpt_dot import ResponsesClient, DotAdapter, transport
from dot_runtime.controller import atomic_json, read_json, digest


def proposal(action='retry', timeout=300):
    return {'action': action, 'timeout_seconds': timeout, 'reason': 'Recorded API timeout',
            'evidence': 'Local request ledger', 'remaining_work': 'Plan then independently verify',
            'expected_benefit': 'A longer bounded wait may receive the plan'}


class ApiRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'control'; self.root.mkdir()
        self.project = self.base / 'product'; self.project.mkdir()
        (self.project / 'input.txt').write_text('Original')
        self.raw = self.base / 'raw.md'; self.raw.write_text('Original request')
        self.original = delegated.delegation(self.raw)
        self.original['adaptive_limits'] = True
        self.original['limits']['timeout_seconds'] = 600
        resources.adjust(self.root, self.original, resource_fixtures.allocation(self.original['limits']), 'L1')

    def client(self, send):
        client = ResponsesClient('fixture-model', self.root / 'api', self.original, send)
        client.governance_root = self.root
        return client

    def fail(self, outcome='UNKNOWN', code='API_TIMEOUT'):
        def send(*_):
            raise api.ApiReview({'version': 1, 'code': code, 'outcome': outcome})
        with self.assertRaises(api.ApiReview): self.client(send).request('Plan fixture')
        return read_json(self.root / 'api/ledger.json')['attempts'][-1]

    def review(self, value=None):
        return api.review(self.root, self.original, value or proposal(), 'L1', self.project)

    def decide(self, approved=True):
        pending = api.load(self.root)['pending']
        return api.decide(self.root, self.original, self.project, pending['sha256'], approved, 'native-session')

    def test_timeout_pauses_without_replay_or_zeroing_usage(self):
        old = self.fail()
        self.assertEqual(old['timeout_seconds'], 120)
        send = Mock()
        with self.assertRaisesRegex(ValueError, 'unresolved'): self.client(send).request('Retry')
        send.assert_not_called()
        self.assertEqual(api.report(self.root)['state'], 'API_REVIEW_PENDING')
        self.assertEqual(resources.report(self.root, self.original)['dot_api_reported_usage']['unreported_requests'], 1)

    def test_l1_can_extend_wait_within_existing_ceiling_but_cannot_unlock_unknown(self):
        self.fail()
        self.review(proposal('configure', 480))
        self.assertEqual(api.load(self.root)['timeout_seconds'], 480)
        self.assertTrue(api.unresolved(self.root))
        for wait in (601, 0, True):
            with self.subTest(wait=wait), self.assertRaises(ValueError): self.review(proposal('configure', wait))

    def test_known_not_sent_requires_l1_assessment_then_consumes_new_attempt(self):
        old = self.fail('NOT_SENT', 'API_DNS')
        self.assertTrue(self.review()['accepted'])
        send = Mock(return_value=delegated.call_response('stop'))
        self.client(send).request('New request')
        self.assertEqual(send.call_args.args[1], 300)
        attempts = api.ledger(self.root)['attempts']
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], old)
        self.assertEqual(api.report(self.root)['state'], 'READY')

    def test_ambiguous_request_requires_native_approval_and_second_failure_needs_new_review(self):
        old = self.fail()
        self.assertEqual(self.review()['state'], 'API_AUTHORIZATION_PENDING')
        self.assertTrue(api.unresolved(self.root))
        self.assertTrue(self.decide()['accepted'])
        self.assertFalse(api.unresolved(self.root))
        self.fail()
        self.assertEqual(api.ledger(self.root)['attempts'][0], old)
        self.assertEqual(len(api.unresolved(self.root)), 1)
        self.assertEqual(api.report(self.root)['state'], 'API_REVIEW_PENDING')

    def test_decline_and_forged_or_stale_decision_do_not_allow_retry(self):
        self.fail(); self.review()
        with self.assertRaises(ValueError): api.decide(self.root, self.original, self.project, 'wrong', True, 'user')
        (self.project / 'input.txt').write_text('Changed')
        with self.assertRaises(ValueError): self.decide()
        self.assertFalse(self.decide(False)['accepted'])
        self.assertTrue(api.unresolved(self.root))

    def test_stale_request_can_be_reassessed_without_erasing_previous_proposal(self):
        self.fail(); self.review()
        old = copy.deepcopy(api.load(self.root)['pending'])
        (self.project / 'input.txt').write_text('Changed')
        self.review(proposal(timeout=400))
        self.assertEqual(api.load(self.root)['pending']['supersedes'], old['sha256'])
        self.assertTrue(self.decide()['accepted'])
        self.assertEqual(api.load(self.root)['events'][0]['record']['details'], old)

    def test_quota_deadline_final_report_active_worker_and_legacy_unknown_are_not_bypassed(self):
        self.fail()
        limits = copy.deepcopy(self.original['limits']); limits['api_calls'] = 1
        resources.adjust(self.root, self.original, resource_fixtures.allocation(limits), 'L1')
        with self.assertRaises(resources.BudgetReview): self.review()
        resources.adjust(self.root, self.original, resource_fixtures.allocation(self.original['limits']), 'L1')
        report = self.root / 'preflight-report.json'; atomic_json(report, {'outcome': 'FAILED'})
        with self.assertRaises(ValueError): self.review()
        report.unlink()
        (self.root / 'run').mkdir()
        atomic_json(self.root / 'run/state.json', {'active_run': {'role': 'implementation'}, 'report': None})
        with self.assertRaises(ValueError): self.review()
        (self.root / 'run/state.json').unlink()
        value = api.ledger(self.root); value['attempts'][0].pop('diagnostic')
        atomic_json(self.root / 'api/ledger.json', value)
        with self.assertRaisesRegex(ValueError, 'Legacy'): self.review()

    def test_expired_authority_cannot_be_extended_by_api_review(self):
        from datetime import datetime
        self.fail()
        expired = datetime.fromisoformat(self.original['expires_at']).timestamp() + 1
        with patch('time.time', return_value=expired), self.assertRaises(resources.BudgetReview):
            self.review()

    def test_response_incomplete_preserves_known_usage_and_allows_review_not_dispatch(self):
        response = dict(delegated.call_response('run_approved_workflow'), status='incomplete',
                        usage={'input_tokens': 10, 'output_tokens': 3, 'total_tokens': 13})
        with self.assertRaises(api.ApiReview): self.client(lambda *_: response).request('fixture')
        self.assertEqual(api.ledger(self.root)['attempts'][0]['usage'], response['usage'])
        self.assertTrue(self.review()['accepted'])

    def test_tampered_review_history_is_rejected(self):
        self.review(proposal('configure'))
        journal = read_json(self.root / 'api-review.json'); journal['timeout_seconds'] = 9999
        atomic_json(self.root / 'api-review.json', journal)
        with self.assertRaises(ValueError): api.load(self.root)


class ApiTransportTests(unittest.TestCase):
    def failure(self, exc):
        opener = Mock(); opener.open.side_effect = exc
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-fixture-secret'}), \
             patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(api.ApiReview) as caught: transport({'model': 'fixture'}, 300)
        self.assertNotIn('sk-fixture-secret', str(caught.exception))
        self.assertNotIn('sk-fixture-secret', json.dumps(caught.exception.diagnostic))
        return caught.exception.diagnostic

    def test_transport_classifies_timeout_dns_tls_and_refusal_without_secret_leak(self):
        for exc, code, outcome in [(TimeoutError('sk-fixture-secret'), 'API_TIMEOUT', 'UNKNOWN'),
             (socket.gaierror('sk-fixture-secret'), 'API_DNS', 'NOT_SENT'),
             (ssl.SSLCertVerificationError('sk-fixture-secret'), 'API_TLS_CERTIFICATE', 'NOT_SENT'),
             (ConnectionRefusedError('sk-fixture-secret'), 'API_CONNECT_REFUSED', 'NOT_SENT'),
             (ConnectionResetError('sk-fixture-secret'), 'API_CONNECTION', 'UNKNOWN')]:
            with self.subTest(code=code):
                value = self.failure(urllib.error.URLError(exc))
                self.assertEqual((value['code'], value['outcome']), (code, outcome))

    def test_http_rejection_and_server_unknown_are_distinct(self):
        for status, outcome in [(401, 'REJECTED'), (429, 'REJECTED'), (503, 'UNKNOWN')]:
            value = self.failure(urllib.error.HTTPError('https://api.openai.com/v1/responses', status,
                'sk-fixture-secret', {'x-request-id': 'req_fixture'}, io.BytesIO(b'{"error":{"message":"sk-fixture-secret"}}')))
            self.assertEqual(value['outcome'], outcome)
            self.assertEqual(value['request_id'], 'req_fixture')


class ApiBridgeTests(unittest.TestCase):
    setUp = resource_fixtures.ResourceTests.setUp
    controller = resource_fixtures.ResourceTests.controller
    bridge = resource_fixtures.ResourceTests.bridge
    allocate = resource_fixtures.ResourceTests.allocate
    prepared = resource_fixtures.ResourceTests.prepared

    def test_reviewed_failure_can_add_facts_initialize_and_finish_without_erasing_history(self):
        import test_clarifications as clarification
        (self.project / 'test_value.py').write_text('import unittest\nfrom value import value\n'
            'class TestValue(unittest.TestCase):\n    def test_value(self): self.assertEqual(value(),3)\n')
        b = self.bridge([]); self.allocate(b)
        send = Mock(side_effect=api.ApiReview({'version':1,'code':'API_DNS','outcome':'NOT_SENT'}))
        b.client_factory = lambda m,p,d: ResponsesClient(m,p,d,send)
        b.call('dot_plan', {'requirements':'Fixture'})
        old = copy.deepcopy(api.ledger(b.root)['attempts'][0])
        self.assertTrue(b.call('l1_review_api', {'proposal_json':json.dumps(proposal(timeout=10)),
                                             'l1_session_id':'L1'})['accepted'])
        facts = b.call('l1_add_facts', {'facts_json':json.dumps(clarification.supplement()), 'l1_session_id':'L1'})
        self.assertTrue(facts['accepted'])
        plan = fixtures.fixture_plan()
        plan['verification']['test_contract']['required_tests'] = ['test_value.TestValue.test_value']
        send.side_effect = [delegated.call_response('propose_plan', {'plan_json':json.dumps(plan)}),
                            delegated.call_response('run_approved_workflow')]
        b.call('dot_plan', {'requirements':'Fixture'})
        self.assertEqual(b.call('dot_initialize', {})['state'], 'REVIEW_PENDING')
        for kind in ('requirements','plan'):
            b.call('l1_approve', {'kind':kind,'l1_session_id':'L1'})
        result = b.call('dot_run', {})
        self.assertEqual(result['report']['outcome'], 'SUCCESS')
        self.assertEqual(api.ledger(b.root)['attempts'][0], old)
        self.assertEqual(len(result['final_report']['api_review']['attempts']), 3)
        self.assertEqual(result['implementation_attempts'], 1)

    def test_policy_rejects_silent_unknown_retry_or_budget_reset(self):
        import sys
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from validate_repo import validate_api_recovery_policy
        policy = read_json(fixtures.ROOT / 'policy/api-recovery.json')
        self.assertEqual(validate_api_recovery_policy(policy), [])
        for key in ('ambiguous_retry_requires_native_approval','automatic_retry','clear_old_unknown',
                    'reset_usage','reopen_final_report','legacy_interruption_recovery'):
            changed = copy.deepcopy(policy); changed[key] = not changed[key]
            self.assertTrue(validate_api_recovery_policy(changed), key)

    def test_planning_timeout_returns_review_without_final_report(self):
        b = self.bridge([]); self.allocate(b)
        send = Mock(side_effect=api.ApiReview({'version': 1, 'code': 'API_TIMEOUT', 'outcome': 'UNKNOWN'}))
        b.client_factory = lambda m, p, d: ResponsesClient(m, p, d, send)
        result = b.call('dot_plan', {'requirements': 'Fixture'})
        self.assertEqual(result['state'], 'API_REVIEW_PENDING')
        self.assertEqual(result['operation_status'], 'COMPLETED_API_REVIEW')
        self.assertFalse((b.root / 'preflight-report.json').exists())
        self.assertFalse((b.run_dir / 'state.json').exists())
        b.call('dot_plan', {'requirements': 'No blind retry'})
        send.assert_called_once()
        result = b.call('l1_stop', {'reason': closure_reason('Cannot resolve this request', ['settings-binding.json'])})
        self.assertEqual(result['preflight_report']['outcome'], 'UNKNOWN')

    def test_dispatch_api_failure_preserves_approved_work_without_starting_workers(self):
        b, c = self.prepared()
        calls = list(b.workers.calls)
        approvals = copy.deepcopy(c.state['approvals'])
        send = Mock(side_effect=api.ApiReview({'version': 1, 'code': 'API_TIMEOUT', 'outcome': 'UNKNOWN'}))
        b.client_factory = lambda m, p, d: ResponsesClient(m, p, d, send)
        result = b.call('dot_run', {})
        self.assertEqual(result['state'], 'API_REVIEW_PENDING')
        self.assertIsNone(result['report'])
        self.assertEqual(read_json(c.state_path)['approvals'], approvals)
        self.assertEqual(b.workers.calls, calls)
        self.assertFalse(b.call('dot_run', {})['accepted'])
        send.assert_called_once()

    def test_triage_timeout_preserves_pending_failure_and_clears_only_api_activity(self):
        b, c = self.prepared()
        send = Mock(side_effect=api.ApiReview({'version': 1, 'code': 'API_TIMEOUT', 'outcome': 'UNKNOWN'}))
        client = ResponsesClient('fixture-model', b.root / 'api', self.original, send)
        client.governance_root = b.root
        c.adapter = DotAdapter(client, b.workers)
        failure = fixtures.result('FAIL', 'implementation')
        with self.assertRaises(api.ApiReview): c.triage(failure, self.project)
        state = read_json(c.state_path)
        self.assertIsNone(state['active_run'])
        self.assertIsNone(state['report'])
        self.assertEqual(state['pending_triage']['failure'], failure)
        self.assertEqual(state['implementation_attempts'], 0)
        self.assertEqual(b.status()['state'], 'API_REVIEW_PENDING')


class NativeApiTests(unittest.TestCase):
    request = onboarding_fixtures.OnboardingTests.request
    prepare = onboarding_fixtures.OnboardingTests.prepare
    confirm = onboarding_fixtures.OnboardingTests.confirm

    def setUp(self):
        onboarding_fixtures.OnboardingTests.setUp(self)
        self.draft['adaptive_limits'] = True
        self.confirm()
        self.original = read_json(self.task / 'delegation.json')
        self.root = self.task / 'control'; self.root.mkdir()
        resources.adjust(self.root, self.original, resource_fixtures.allocation(self.original['limits']), 'native-session')
        client = ResponsesClient('fixture-model', self.root / 'api', self.original,
            Mock(side_effect=api.ApiReview({'version': 1, 'code': 'API_TIMEOUT', 'outcome': 'UNKNOWN'})))
        client.governance_root = self.root
        with self.assertRaises(api.ApiReview): client.request('Fixture planning')

    def test_real_pipe_dispatch_binds_native_identity_and_preserves_all_original_records(self):
        paths = ['confirmation.json', 'delegation.json', 'raw-input.md', 'control/api/ledger.json']
        before = {p: (self.task / p).read_bytes() for p in paths}
        result = self.request('bridge', name='l1_review_api', arguments={'proposal_json': json.dumps(proposal())})
        self.assertEqual(result['state'], 'API_AUTHORIZATION_PENDING')
        pending = self.request('api_retry_preview')
        self.assertEqual(pending['actor'], 'native-session')
        result = self.request('api_retry_decide', sha256=pending['sha256'], approved=True)
        self.assertTrue(result['accepted'])
        self.assertEqual({p: (self.task / p).read_bytes() for p in paths}, before)
        self.assertEqual(self.request('status')['api_review']['state'], 'READY')

    def test_native_route_cannot_clear_host_interruption_or_model_supply_approval(self):
        with self.assertRaises(ValueError):
            self.request('bridge', name='l1_review_api', arguments={'proposal_json':json.dumps(proposal()), 'l1_session_id':'forged'})
        self.request('bridge', name='l1_review_api', arguments={'proposal_json':json.dumps(proposal())})
        atomic_json(self.task / 'interrupted.json', {'name':'dot_plan','status':'UNKNOWN'})
        with self.assertRaisesRegex(ValueError, 'interrupted'): self.request('api_retry_preview')
        self.assertTrue(api.unresolved(self.root))

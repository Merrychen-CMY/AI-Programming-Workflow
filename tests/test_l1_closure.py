"""Finalization boundaries with real isolated controller state, fake models/workers."""
import copy
import json
import unittest
from unittest.mock import patch

import test_resources as resources_fixture
import test_delegated_l1 as delegated
import test_runtime_controller as fixtures
from closure_fixtures import assessment, reason
from dot_runtime.closure import validate_assessment
from dot_runtime.controller import Controller, read_json
from dot_runtime.gpt_dot import execute
from dot_runtime.delegation import approve_l1


class ClosureTests(unittest.TestCase):
    setUp = resources_fixture.ResourceTests.setUp
    bridge = resources_fixture.ResourceTests.bridge
    allocate = resources_fixture.ResourceTests.allocate
    prepared = resources_fixture.ResourceTests.prepared
    controller = resources_fixture.ResourceTests.controller

    def assert_paused(self, c):
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertFalse((c.run_dir / 'final-report.json').exists())
        decision = read_json(c.run_dir / 'decision-request.json')
        self.assertEqual(decision['owner'], 'L2')
        self.assertEqual(decision['escalation_owner'], 'L1')

    def test_every_automatic_non_success_is_paused_and_archived(self):
        b, c = self.prepared()
        before = copy.deepcopy(c.state)
        for outcome in ('FAILED', 'UNKNOWN', 'INCOMPLETE'):
            c.report(outcome, 'Automatic failure recommendation')
            self.assert_paused(c)
        self.assertEqual(len(list((c.run_dir / 'decision-requests').glob('*.json'))), 3)
        self.assertEqual(c.state['implementation_attempts'], before['implementation_attempts'])
        self.assertEqual(c.state['sessions'], before['sessions'])

    def test_dot_execution_stop_is_only_a_proposal(self):
        b, c = self.prepared()
        class Client:
            root = b.root / 'api'
            def request(self, prompt, **kwargs):
                assert 'First reason about feasible solutions' in prompt
                return delegated.call_response('stop', {'reason': 'Alternative needs additional authority'})
        before = list(b.workers.calls)
        execute(Client(), c)
        self.assert_paused(c)
        self.assertEqual(b.workers.calls, before)

    def test_planning_stop_can_be_replanned_without_final_report(self):
        b = self.bridge([delegated.call_response('stop', {'reason': 'Need permission; consider restricted alternative'}),
                         delegated.call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})])
        self.allocate(b)
        result = b.call('dot_plan', {'requirements': 'Implement fixture value'})
        self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
        self.assertIsNone(result['preflight_report'])
        result = b.call('dot_plan', {'requirements': 'Implement fixture value'})
        self.assertTrue(b.status()['plan_ready'])
        self.assertEqual(len(read_json(b.root / 'api/ledger.json')['attempts']), 2)

    def test_bridge_unresolved_process_preserves_guard_and_no_new_dispatch(self):
        b, c = self.prepared()
        active = {'role': 'implementation', 'identity': 'unresolved-fixture'}
        c.state['active_run'] = active
        c.save()
        ledger = read_json(b.root / 'api/ledger.json')
        result = b.call('dot_run', {})
        c = Controller(b.run_dir)
        self.assert_paused(c)
        self.assertEqual(c.state['active_run'], active)
        self.assertEqual(read_json(b.root / 'api/ledger.json'), ledger)
        self.assertEqual(result['next_owner'], 'L1')

    def test_execution_file_error_is_recoverable_decision_not_final(self):
        b, c = self.prepared()
        with patch.object(c, 'make_candidate', side_effect=OSError('Fixture storage unavailable')):
            c.run()
        self.assert_paused(c)
        self.assertIn('Fixture storage unavailable', read_json(c.run_dir / 'decision-request.json')['reason'])
        self.assertEqual(c.state['implementation_attempts'], 1)

    def test_bare_or_incomplete_l1_reason_cannot_finalize_or_mutate(self):
        b, c = self.prepared()
        before = c.state_path.read_bytes()
        invalid = ['Unknown', '{}', 'null', reason()[:-1]]
        for key in assessment():
            value = assessment(); del value[key]
            invalid.append(json.dumps(value))
        for value in invalid:
            result = b.call('l1_stop', {'reason': value})
            self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
            self.assertEqual(c.state_path.read_bytes(), before)
            self.assertFalse((c.run_dir / 'final-report.json').exists())

    def test_l1_assessment_required_before_final_then_report_review_is_separate(self):
        b, c = self.prepared()
        c.report('FAILED', 'L2 suggests stopping')
        self.assert_paused(c)
        with self.assertRaises(ValueError):
            b.call('l1_submit_report', {'summary': 'Cannot substitute final review for closure assessment', 'l1_session_id': 'L1'})
        result = b.call('l1_stop', {'reason': reason()})
        self.assertEqual(result['report']['outcome'], 'FAILED')
        report = read_json(c.run_dir / 'final-report.json')
        self.assertEqual(report['l1_closure_assessment']['assessment'], assessment())
        self.assertTrue(report['l1_closure_assessment']['state_sha256'])
        self.assertNotIn('l1_submission', report)
        self.assertEqual(result['next_owner'], 'L1')
        b.call('l1_submit_report', {'summary': 'Reviewed failure evidence', 'l1_session_id': 'L1'})
        self.assertEqual(b.status()['next_owner'], 'user')

    def test_explicit_unknown_final_does_not_clear_unresolved_operation(self):
        b, c = self.prepared()
        c.state['active_run'] = {'role': 'implementation', 'id': 'fixture-old'}
        c.save()
        b.call('l1_stop', {'reason': reason()})
        c = Controller(b.run_dir)
        self.assertEqual(c.state['report']['outcome'], 'UNKNOWN')
        self.assertEqual(c.state['active_run']['id'], 'fixture-old')
        previous = (c.run_dir / 'final-report.json').read_bytes()
        with self.assertRaises(ValueError):
            c.report('FAILED', 'Overwrite', l1_assessment=assessment())
        self.assertEqual((c.run_dir / 'final-report.json').read_bytes(), previous)

    def test_preflight_assessment_is_required_too(self):
        b = self.bridge([]); self.allocate(b)
        b.preflight_report('Automatic planning failure')
        self.assertFalse((b.root / 'preflight-report.json').exists())
        self.assertFalse(b.call('l1_stop', {'reason': 'unknown'})['accepted'])
        result = b.call('l1_stop', {'reason': reason()})
        self.assertEqual(result['preflight_report']['l1_closure_assessment']['assessment'], assessment())

    def test_assessment_rejects_unknown_basis_and_empty_options(self):
        for key, invalid in [('termination_basis', 'unknown'), ('solutions_considered', []),
                             ('evidence', []), ('authority_review', ' '), ('resource_review', None)]:
            value = assessment(); value[key] = invalid
            with self.assertRaises(ValueError):
                validate_assessment(value)

    def test_governance_policy_cannot_grant_dot_finalization(self):
        import sys
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from validate_repo import validate_closure_policy
        policy = read_json(fixtures.ROOT / 'policy/closure.json')
        self.assertEqual(validate_closure_policy(policy), [])
        for key in ('l2_may_finalize_failure', 'non_success_requires_explicit_l1_assessment',
                    'l2_first_considers_in_scope_solutions', 'insufficient_evidence_is_terminal'):
            value = dict(policy); value[key] = not value[key]
            self.assertTrue(validate_closure_policy(value))


class LegacyClosureTests(unittest.TestCase):
    setUp = delegated.DelegatedTests.setUp
    approved = delegated.DelegatedTests.approved
    controller = fixtures.ControllerTests.controller

    def test_legacy_review_budget_pauses_without_dispatch(self):
        self.c.state['l1_reviews'] = [{'reserved': True}] * read_json(self.delegation_path)['limits']['l1_reviews']
        self.c.save()
        approve_l1(self.c, 'requirements', 'fixture-L1', self.c.adapter)
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(self.c.state['report'])
        self.assertFalse(self.c.adapter.calls)

    def test_legacy_implementation_budget_pauses_without_reset(self):
        c = self.approved()
        c.state['implementation_attempts'] = c.state['approved_plan']['budgets']['implementation_attempts']
        c.save()
        before = list(c.adapter.calls)
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertEqual(c.adapter.calls, before)

    def test_legacy_facility_budget_preserves_failure_and_requests_assessment(self):
        c = self.approved()
        c.state['infrastructure_retries'] = c.state['approved_plan']['budgets']['infrastructure_retries']
        c.adapter = fixtures.FakeAdapter([('triage', fixtures.result('PASS', 'test_infrastructure'))])
        c.save()
        c.triage(fixtures.result('FAIL'), self.project)
        self.assertEqual(c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertEqual(c.state['infrastructure_retries'], 1)
        self.assertEqual(len(c.state['issues']), 1)


if __name__ == '__main__':
    unittest.main()

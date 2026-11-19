"""Finalization boundaries with real isolated controller state, fake models/workers."""
import copy
import json
import unittest
from datetime import datetime
from unittest.mock import patch

import test_resources as resources_fixture
import test_delegated_l1 as delegated
import test_runtime_controller as fixtures
from closure_fixtures import assessment, reason
from dot_runtime.closure import validate_assessment
from dot_runtime.controller import Controller, atomic_json, read_json
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
        result = b.call('l1_stop', {'reason': reason(control=True)})
        self.assertEqual(result['report']['outcome'], 'FAILED')
        report = read_json(c.run_dir / 'final-report.json')
        self.assertEqual(report['l1_closure_assessment']['assessment'], assessment(control=True))
        self.assertTrue(report['l1_closure_assessment']['state_sha256'])
        self.assertNotIn('l1_submission', report)
        self.assertEqual(result['next_owner'], 'L1')
        b.call('l1_submit_report', {'summary': 'Reviewed failure evidence', 'l1_session_id': 'L1'})
        self.assertEqual(b.status()['next_owner'], 'user')

    def test_explicit_unknown_final_does_not_clear_unresolved_operation(self):
        b, c = self.prepared()
        c.state['active_run'] = {'role': 'implementation', 'id': 'fixture-old'}
        c.save()
        b.call('l1_stop', {'reason': reason(control=True)})
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
        evidence = ['settings-binding.json']
        result = b.call('l1_stop', {'reason': reason(evidence=evidence)})
        self.assertEqual(result['preflight_report']['l1_closure_assessment']['assessment'], assessment(evidence=evidence))
        self.assertEqual(result['state'], 'REPORT_PENDING')
        self.assertEqual(result['next_owner'], 'L1')
        b.call('l1_submit_report', {'summary': 'Checked preflight evidence', 'l1_session_id': 'native-L1'})
        self.assertEqual(b.status()['next_owner'], 'user')

    def test_fake_evidence_and_forged_user_stop_cannot_finalize(self):
        b, c = self.prepared()
        before = c.state_path.read_bytes()
        value = assessment(evidence=['does-not-exist.json'])
        self.assertFalse(b.call('l1_stop', {'reason': json.dumps(value)})['accepted'])
        value = assessment(control=True); value['termination_basis'] = 'user_requested_stop'
        self.assertFalse(b.call('l1_stop', {'reason': json.dumps(value)})['accepted'])
        self.assertEqual(c.state_path.read_bytes(), before)

    def test_deep_control_relative_evidence_is_verified_with_its_real_hash(self):
        from dot_runtime.closure import evidence_records
        from dot_runtime.controller import file_hash
        from dot_runtime.snapshot import _filesystem_path
        b = self.bridge([])
        name = 'run/preflight-requirements/' + '/'.join(['deep-review-evidence-' + str(i) * 35 for i in range(8)]) + '/result.json'
        target = b.root / name
        disk = _filesystem_path(target)
        disk.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        self.addCleanup(shutil.rmtree, _filesystem_path(b.root / 'run/preflight-requirements'))
        disk.write_text('{"status":"FAIL","evidence":"Recorded independently"}', encoding='utf-8')
        value = assessment(evidence=[name])
        self.assertEqual(evidence_records(value, b.root), [{'path': name, 'sha256': file_hash(target)}])
        self.assertEqual(validate_assessment(value, root=b.root), value)

    def test_assessment_rejects_unknown_basis_and_empty_options(self):
        for key, invalid in [('termination_basis', 'unknown'), ('solutions_considered', []),
                             ('evidence', []), ('authority_review', ' '), ('resource_review', None)]:
            value = assessment(); value[key] = invalid
            with self.assertRaises(ValueError):
                validate_assessment(value)

    def total_limit_reason(self, evidence=None):
        value = assessment(evidence=evidence, control=True)
        value['termination_basis'] = 'unadjustable_total_limit'
        return json.dumps(value)

    def test_allocated_limit_is_not_total_authorized_limit(self):
        b, c = self.prepared()
        self.assertTrue(self.allocate(b, implementation_attempts=1)['accepted'])
        c.state['implementation_attempts'] = 1
        c.save()
        before = c.state_path.read_bytes()
        result = b.call('l1_stop', {'reason': self.total_limit_reason()})
        self.assertFalse(result['accepted'])
        self.assertIn('user ceiling or deadline', result['reason'])
        self.assertEqual(c.state_path.read_bytes(), before)

    def test_actual_total_implementation_ceiling_can_support_l1_closure(self):
        b, c = self.prepared()
        c.state['implementation_attempts'] = self.original['limits']['implementation_attempts']
        c.save()
        result = b.call('l1_stop', {'reason': self.total_limit_reason()})
        self.assertEqual(result['report']['outcome'], 'FAILED')
        record = read_json(c.run_dir / 'final-report.json')
        self.assertEqual(record['resource_governance']['actual_usage']['implementation_attempts'], 8)
        self.assertEqual(record['l1_closure_assessment']['verified_evidence'][0]['path'], 'run/state.json')

    def test_pending_authorization_prevents_total_limit_closure_even_after_deadline(self):
        from dot_runtime import resources
        b, c = self.prepared()
        proposal = resources_fixture.amendment({'limits': dict(self.original['limits'], implementation_attempts=9)})
        resources.propose_authorization(b.root, self.original, proposal, 'host-L1', c.state)
        deadline = datetime.fromisoformat(self.original['expires_at']).timestamp()
        with patch('time.time', return_value=deadline + 1):
            result = b.call('l1_stop', {'reason': self.total_limit_reason()})
        self.assertFalse(result['accepted'])
        self.assertIn('pending user authorization', result['reason'])
        self.assertFalse((c.run_dir / 'final-report.json').exists())

    def test_actual_user_deadline_can_support_preflight_l1_closure(self):
        b = self.bridge([])
        self.allocate(b)
        deadline = datetime.fromisoformat(self.original['expires_at']).timestamp()
        with patch('time.time', return_value=deadline + 1):
            result = b.call('l1_stop', {'reason': self.total_limit_reason(['settings-binding.json'])})
        self.assertEqual(result['preflight_report']['outcome'], 'FAILED')
        self.assertEqual(result['preflight_report']['resource_governance']['actual_usage']['implementation_attempts'], 0)

    def test_pending_api_user_authorization_also_prevents_total_limit_closure(self):
        from dot_runtime import api_recovery
        b, c = self.prepared()
        c.state['implementation_attempts'] = self.original['limits']['implementation_attempts']
        c.save()
        journal = api_recovery.load(b.root)
        journal['pending'] = {'fixture': 'Native user has not decided the API replacement request'}
        api_recovery.save(b.root, journal, 'authorization_requested', journal['pending'])
        result = b.call('l1_stop', {'reason': self.total_limit_reason()})
        self.assertFalse(result['accepted'])
        self.assertIn('pending user authorization', result['reason'])
        self.assertFalse((c.run_dir / 'final-report.json').exists())

    def test_actual_preflight_api_total_is_counted_without_controller(self):
        b = self.bridge([])
        self.allocate(b)
        (b.root / 'api').mkdir(exist_ok=True)
        atomic_json(b.root / 'api/ledger.json', {'attempts': [{'status': 'COMPLETED'}] * self.original['limits']['api_calls']})
        result = b.call('l1_stop', {'reason': self.total_limit_reason(['api/ledger.json'])})
        self.assertEqual(result['preflight_report']['outcome'], 'FAILED')
        self.assertEqual(result['preflight_report']['resource_governance']['actual_usage']['api_calls'], 30)

    def test_zero_retry_ceiling_is_not_total_exhaustion_without_recorded_need(self):
        from dot_runtime import resources
        original = copy.deepcopy(self.original)
        original['limits']['infrastructure_retries'] = 0
        atomic_json(self.delegation_path, original)
        b = self.bridge([])
        value = json.loads(self.total_limit_reason(['settings-binding.json']))
        b.call('dot_status', {})
        with self.assertRaisesRegex(ValueError, 'user ceiling or deadline'):
            validate_assessment(value, root=b.root, original=original)
        resources.pause(b.root, original, resources.BudgetReview('infrastructure_retries', 0, 1, 0, 0))
        self.assertEqual(validate_assessment(value, root=b.root, original=original), value)

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

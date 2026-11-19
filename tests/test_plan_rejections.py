"""Recover only proven plan refusals; mocks never count as live model or product evidence."""
from closure_fixtures import assessment, reason as closure_reason
import copy
import json
import sys
import time
import unittest
from unittest.mock import patch

import test_clarifications as clarification
import test_delegated_l1 as delegated
import test_runtime_controller as fixtures
from dot_runtime.bridge import Bridge
from dot_runtime.controller import Controller, atomic_json, read_json
from dot_runtime import planning


def proposed(plan):
    return delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)})


def phase_tasks():
    plan = fixtures.fixture_plan()
    plan['tasks'] = [{'id': key, 'instruction': key, 'depends_on': []}
                     for key in ('read', 'standard', 'implement', 'build', 'doxygen', 'hil', 'review')]
    return plan


class PlanRejectionTests(unittest.TestCase):
    setUp = delegated.DelegatedTests.setUp
    controller = fixtures.ControllerTests.controller
    bridge = clarification.ClarificationTests.bridge

    def test_policy_rejects_budget_reinterpretation_and_unknown_bypass(self):
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from validate_repo import validate_planning_policy
        value = read_json(fixtures.ROOT / 'policy/planning.json')
        self.assertEqual(validate_planning_policy(value), [])
        for key in ('unknown_replay_allowed', 'rejection_resets_counters',
                    'verification_phases_are_implementation_tasks', 'independent_review_required',
                    'legacy_locked_task_auto_migration', 'completed_rejection_clears_only_current_inflight'):
            changed = copy.deepcopy(value)
            changed[key] = not changed[key]
            self.assertTrue(validate_planning_policy(changed), key)
        changed = copy.deepcopy(value)
        changed['implementation_budget_unit'] = 'per_task_retries'
        self.assertTrue(validate_planning_policy(changed))
        changed = copy.deepcopy(value)
        changed['replanning_consumes'] = []
        self.assertTrue(validate_planning_policy(changed))

    def test_overpartitioned_plan_is_completed_rejection_without_dispatch_or_final(self):
        b = self.bridge([proposed(phase_tasks())])
        original = self.raw.read_bytes(), self.delegation_path.read_bytes()
        result = b.call('dot_plan', {'requirements': 'All seven phases are required'})
        self.assertEqual(result['state'], 'PLAN_REJECTED')
        self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(result['planning']['code'], 'IMPLEMENTATION_DISPATCH_BUDGET')
        self.assertEqual(result['planning']['details']['initial_task_dispatches'], 7)
        self.assertFalse((b.root / 'preflight-report.json').exists())
        self.assertFalse((b.run_dir / 'state.json').exists())
        self.assertFalse(b.workers.calls)
        self.assertEqual(read_json(b.root / 'api/ledger.json')['attempts'][0]['status'], 'COMPLETED')
        self.assertEqual(original, (self.raw.read_bytes(), self.delegation_path.read_bytes()))
        self.assertEqual(len(planning.history(b.root)), 1)
        self.assertFalse(b.call('dot_initialize', {})['accepted'])
        self.assertFalse(b.call('l1_revise', {'requirements': 'x', 'plan_json': '{}'})['accepted'])

    def test_feedback_drives_corrected_draft_with_budget_and_history_preserved(self):
        good = fixtures.fixture_plan()
        good['tasks'][0]['instruction'] = 'Read existing code; implement requirements and documentation'
        good['verification']['test_instructions'] = 'Build and run independent required tests; HIL remains required when applicable'
        good['verification']['review_instructions'] = 'Independently review code, standard and documentation evidence'
        b = self.bridge([proposed(phase_tasks()), proposed(good)])
        b.call('dot_plan', {'requirements': 'All seven phases are required'})
        before = read_json(b.root / 'api/ledger.json')
        result = b.call('dot_plan', {'requirements': 'All seven phases are required'})
        self.assertEqual(result['plan'], good)
        prompt = json.dumps(self.requests[-1])
        self.assertIn('IMPLEMENTATION_DISPATCH_BUDGET', prompt)
        self.assertIn('ONLY the list of L3', prompt)
        b.call('dot_initialize', {})
        state = Controller(b.run_dir).state
        self.assertEqual(state['implementation_attempts'], 0)
        self.assertEqual(state['delegated_started_at'], before['started_at'])
        self.assertEqual(state['revision_count'], 0)  # An unapproved rejected draft is not an implemented baseline.
        self.assertEqual(len(read_json(b.root / 'api/ledger.json')['attempts']), 2)
        for kind in ('requirements', 'plan'):
            b.call('l1_approve', {'kind': kind, 'l1_session_id': 'author'})
        c = Controller(b.run_dir)
        self.assertEqual(c.state['approved_plan']['budgets']['implementation_attempts'], 3)
        c.stop_with_report('Fixture stops before any product execution', l1_assessment=assessment(control=True))
        self.assertEqual(len(read_json(b.run_dir / 'final-report.json')['planning_rejections']), 1)

    def test_identical_bad_output_and_feedback_does_not_loop_across_restart(self):
        b = self.bridge([proposed(phase_tasks()), proposed(phase_tasks())])
        b.call('dot_plan', {'requirements': 'fixed'})
        b.call('dot_plan', {'requirements': 'fixed'})
        restart = Bridge(b.settings, client_factory=b.client_factory, workers=b.workers)
        result = restart.call('dot_plan', {'requirements': 'fixed'})
        self.assertFalse(result['accepted'])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(planning.history(b.root)), 2)

    def test_malformed_or_overscoped_proposals_are_known_refusals(self):
        for label, mutate in [('json', lambda p: None), ('root', lambda p: []),
                              ('paths', lambda p: dict(p, allowed_paths=['.'])),
                              ('budget', lambda p: dict(p, budgets=dict(p['budgets'], implementation_attempts=4))),
                              ('role', lambda p: dict(p, tasks=[dict(p['tasks'][0], role='test')]))]:
            with self.subTest(label=label):
                reply = proposed(mutate(fixtures.fixture_plan()))
                if label == 'json': reply = delegated.call_response('propose_plan', {'plan_json': '{'})
                b = self.bridge([reply])
                result = b.call('dot_plan', {'requirements': label})
                self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
                self.assertFalse(b.workers.calls)
        self.assertFalse((b.root / 'preflight-report.json').exists())

    def initialize_paused(self, b):
        b.call('dot_plan', {'requirements': 'initial'})
        b.call('dot_initialize', {})
        b.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'author'})

    def test_local_revision_rejection_leaves_original_baseline_and_counters_intact(self):
        b = self.bridge([proposed(fixtures.fixture_plan())])
        self.initialize_paused(b)
        before = (b.run_dir / 'state.json').read_bytes()
        result = b.call('l1_revise', {'requirements': 'revised', 'plan_json': json.dumps(phase_tasks())})
        self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(before, (b.run_dir / 'state.json').read_bytes())
        self.assertFalse((b.root / 'revisions').exists())
        self.assertFalse(b.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'author'})['accepted'])
        b.call('l1_revise', {'requirements': 'revised', 'plan_json': json.dumps(fixtures.fixture_plan())})
        state = Controller(b.run_dir).state
        self.assertEqual(state['state'], 'REVIEW_PENDING')
        self.assertEqual(state['revision_count'], 1)
        self.assertFalse(state['approvals'])
        for kind in ('requirements', 'plan'):
            b.call('l1_approve', {'kind': kind, 'l1_session_id': 'author'})
        self.assertEqual(Controller(b.run_dir).state['state'], 'EXECUTING')

    def test_replanning_requires_staging_new_baseline_before_old_approval(self):
        b = self.bridge([proposed(fixtures.fixture_plan()), proposed(phase_tasks()), proposed(fixtures.fixture_plan())])
        self.initialize_paused(b)
        b.call('dot_plan', {'requirements': 'revised'})
        result = b.call('dot_plan', {'requirements': 'revised'})
        self.assertTrue(result['plan'])
        self.assertFalse(b.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'author'})['accepted'])

    def test_revised_plan_must_fit_remaining_not_original_dispatch_count(self):
        b = self.bridge([proposed(fixtures.fixture_plan())])
        self.initialize_paused(b)
        c = Controller(b.run_dir)
        c.state['implementation_attempts'] = 2
        c.save()
        plan = fixtures.fixture_plan()
        plan['tasks'] *= 2
        plan['tasks'][1] = dict(plan['tasks'][1], id='second')
        result = b.call('l1_revise', {'requirements': 'two units', 'plan_json': json.dumps(plan)})
        self.assertEqual(result['planning']['details']['implementation_used'], 2)
        self.assertEqual(Controller(b.run_dir).state['implementation_attempts'], 2)
        self.assertEqual(Controller(b.run_dir).state['revision_count'], 0)

    def test_api_budget_is_not_reset_after_rejection(self):
        value = read_json(self.delegation_path)
        value['limits']['api_calls'] = 1
        atomic_json(self.delegation_path, value)
        b = self.bridge([proposed(phase_tasks())])
        b.call('dot_plan', {'requirements': 'x'})
        paused = b.call('dot_plan', {'requirements': 'x'})
        self.assertIsNone(paused['preflight_report'])
        self.assertEqual(paused['state'], 'PLAN_REJECTED')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(planning.history(b.root)), 2)
        self.assertFalse((b.root / 'preflight-report.json').exists())

    def test_real_transport_failure_and_unexpected_errors_are_not_plan_rejections(self):
        b = self.bridge([])
        with patch('dot_runtime.bridge.propose_plan', side_effect=RuntimeError('unexpected internal failure')):
            with self.assertRaises(RuntimeError): b.call('dot_plan', {'requirements': 'x'})
        self.assertFalse(planning.history(b.root))
        def broken(payload, timeout): raise OSError('transport lost')
        client = b.client()
        client.send = broken
        with self.assertRaises(OSError): client.request('x')
        with self.assertRaisesRegex(ValueError, 'unresolved'): client.request('x')
        self.assertEqual(len(read_json(b.root / 'api/ledger.json')['attempts']), 1)

    def test_rejection_record_write_failure_does_not_return_completed(self):
        b = self.bridge([proposed(phase_tasks())])
        with patch('dot_runtime.planning.record_rejection', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): b.call('dot_plan', {'requirements': 'x'})


if __name__ == '__main__':
    unittest.main()

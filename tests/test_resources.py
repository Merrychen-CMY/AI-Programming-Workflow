"""Adaptive budgets: real local state/ledgers, deterministic model and worker fixtures."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import unittest
import sys
from unittest.mock import patch

import test_clarifications as clarification
import test_delegated_l1 as delegated
import test_runtime_controller as fixtures
import test_onboarding as onboarding
from dot_runtime import resources
from dot_runtime.bridge import Bridge
from dot_runtime.controller import Controller, atomic_json, digest, read_json
from dot_runtime.delegation import approve_l1, check_binding


def allocation(limits):
    return dict(limits=copy.deepcopy(limits), reason='Remaining work needs bounded resources',
                evidence='Recorded issues and API ledger', remaining_work='Implement and independently verify',
                expected_benefit='One targeted repair resolves the diagnosed defect', strategy='Use the recorded diagnosis')


def amendment(changes):
    return dict(changes=changes, reason='Current authority cannot cover the remaining work',
                evidence='Recorded controller checkpoint', remaining_work='Replan and verify',
                expected_benefit='Complete the requested deliverable', risk='Additional bounded resource use',
                resume_plan='Allocate resources, review changed scope and resume only known operations')


class ResourceTests(unittest.TestCase):
    controller = fixtures.ControllerTests.controller
    bridge = clarification.ClarificationTests.bridge

    def setUp(self):
        delegated.DelegatedTests.setUp(self)
        self.original = read_json(self.delegation_path)
        self.original['adaptive_limits'] = True
        self.original['limits'].update(implementation_attempts=8, same_root_cause_attempts=5,
                                       infrastructure_retries=4, api_calls=30, l1_reviews=10, revisions=6)
        atomic_json(self.delegation_path, self.original)

    def allocate(self, b, **changes):
        limits = copy.deepcopy(self.original['limits'])
        limits.update(changes)
        return b.call('l1_adjust_limits', {'proposal_json': json.dumps(allocation(limits)), 'l1_session_id': 'host-L1'})

    def test_policy_rejects_authority_or_history_boundary_drift(self):
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from validate_repo import validate_resource_policy
        value = read_json(fixtures.ROOT / 'policy/resources.json')
        self.assertEqual(validate_resource_policy(value), [])
        for key in ('legacy_auto_migration', 'usage_reset_allowed', 'model_supplied_human_approval',
                    'history_deletion_allowed', 'unknown_auto_clear', 'final_report_auto_reopen',
                    'independent_verification_required', 'scope_change_requires_replan_and_independent_review'):
            changed = copy.deepcopy(value); changed[key] = not changed[key]
            self.assertTrue(validate_resource_policy(changed), key)

    def prepared(self):
        plan = fixtures.fixture_plan()
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)})])
        self.allocate(b)
        b.call('dot_plan', {'requirements': 'Implement fixture value'})
        b.call('dot_initialize', {})
        for kind in ('requirements', 'plan'):
            b.call('l1_approve', {'kind': kind, 'l1_session_id': 'host-L1'})
        self.run_dir = b.run_dir
        c = self.controller(b.workers)
        return b, c

    def pause(self, b, c):
        with self.assertRaises(resources.BudgetReview) as caught:
            c.run()
        c.state = read_json(c.state_path)
        resources.pause(b.root, self.original, caught.exception, c.state)
        self.assertIsNone(c.state['active_run'])
        self.assertIsNone(c.state['report'])
        return caught.exception.details

    def test_first_allocation_required_without_api_or_worker_or_final_report(self):
        b = self.bridge([])
        result = b.call('dot_plan', {'requirements': 'x'})
        self.assertEqual(result['state'], 'BUDGET_REVIEW_PENDING')
        self.assertEqual(result['operation_status'], 'COMPLETED_BUDGET_REVIEW')
        self.assertFalse(self.requests)
        self.assertFalse(b.workers.calls)
        self.assertFalse((b.root / 'preflight-report.json').exists())
        self.assertTrue(self.allocate(b)['accepted'])

    def test_invalid_adjustment_is_clean_refusal_and_cannot_expand_user_ceiling(self):
        b = self.bridge([])
        for change in [dict(implementation_attempts=9), dict(revisions=-1), dict(api_calls=True)]:
            self.assertFalse(self.allocate(b, **change)['accepted'])
        self.assertFalse((b.root / 'resources.json').exists())
        bad = allocation(self.original['limits']); bad['approved'] = True
        self.assertFalse(b.call('l1_adjust_limits', {'proposal_json': json.dumps(bad), 'l1_session_id': 'host'})['accepted'])

    def test_legacy_task_does_not_gain_new_authority(self):
        value = copy.deepcopy(self.original); value.pop('adaptive_limits')
        atomic_json(self.delegation_path, value)
        b = self.bridge([])
        self.assertFalse(self.allocate(b)['accepted'])
        self.assertNotIn('resources', b.status())

    def test_initial_dispatch_shortfall_is_l1_review_and_retains_bad_draft(self):
        plan = fixtures.fixture_plan()
        plan['tasks'] = [dict(id='task' + str(i), instruction='Implementation unit ' + str(i), depends_on=[]) for i in range(4)]
        fixed = copy.deepcopy(plan); fixed['budgets']['implementation_attempts'] = 4
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)}),
                         delegated.call_response('propose_plan', {'plan_json': json.dumps(fixed)})])
        self.allocate(b, implementation_attempts=3)
        result = b.call('dot_plan', {'requirements': 'Four genuine implementation units'})
        self.assertEqual(result['state'], 'BUDGET_REVIEW_PENDING')
        self.assertEqual(result['resources']['budget_review']['requested'], 4)
        self.assertFalse(self.allocate(b, implementation_attempts=3)['accepted'])
        self.assertTrue(self.allocate(b, implementation_attempts=5)['accepted'])
        self.assertIn('plan', b.call('dot_plan', {'requirements': 'Four genuine implementation units'}))
        self.assertEqual(len(read_json(b.root / 'api/ledger.json')['attempts']), 2)

    def test_api_pause_does_not_reserve_request_or_reset_start_time(self):
        b = self.bridge([clarification.needs_info(), clarification.needs_info()])
        self.allocate(b, api_calls=1)
        b.call('dot_plan', {'requirements': 'a'})
        before = read_json(b.root / 'api/ledger.json')
        result = b.call('dot_plan', {'requirements': 'b'})
        self.assertEqual(result['state'], 'BUDGET_REVIEW_PENDING')
        self.assertEqual(before, read_json(b.root / 'api/ledger.json'))
        self.allocate(b, api_calls=2)
        b.call('dot_plan', {'requirements': 'b'})
        after = read_json(b.root / 'api/ledger.json')
        self.assertEqual(after['started_at'], before['started_at'])
        self.assertEqual(len(after['attempts']), 2)

    def test_implementation_pause_resumes_repair_after_l2_without_repeating_completed_tasks(self):
        b, c = self.prepared()
        self.allocate(b, implementation_attempts=1)
        b.workers.overrides = [('test', fixtures.result('FAIL')), ('triage', fixtures.result('PASS', 'implementation'))]
        self.assertEqual(self.pause(b, c)['key'], 'implementation_attempts')
        self.assertEqual(c.state['completed_tasks'], ['build'])
        self.assertTrue(c.state['pending_repair'])
        self.assertEqual(b.workers.calls.count('implementation'), 1)
        self.assertFalse(self.allocate(b, implementation_attempts=1)['accepted'])
        self.allocate(b, implementation_attempts=2)
        state = c.run()
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        self.assertEqual(state['implementation_attempts'], 2)
        self.assertEqual(state['completed_tasks'], ['build'])
        self.assertEqual(len(state['issues']), 1)
        report = read_json(c.run_dir / 'final-report.json')['resource_governance']
        self.assertEqual(report['actual_usage']['implementation_attempts'], 2)
        self.assertTrue(any(e['record']['kind'] == 'budget_review' for e in report['history']))
        self.assertEqual(read_json(self.delegation_path), self.original)

    def test_same_root_checkpoint_requires_real_new_capacity_and_keeps_history(self):
        b, c = self.prepared()
        self.allocate(b, same_root_cause_attempts=1)
        b.workers.overrides = [('implementation', fixtures.result('FAIL')), ('triage', fixtures.result('PASS', 'implementation'))]
        self.assertEqual(self.pause(b, c)['key'], 'same_root_cause_attempts')
        self.assertFalse(self.allocate(b, same_root_cause_attempts=1)['accepted'])
        self.allocate(b, same_root_cause_attempts=2)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(c.state['root_cause_counts']['implementation:fixture.value.defect'], 1)

    def test_lowered_root_limit_is_checked_again_before_a_pending_repair(self):
        b, c = self.prepared()
        self.allocate(b, implementation_attempts=1)
        b.workers.overrides = [('implementation', fixtures.result('FAIL')), ('triage', fixtures.result('PASS', 'implementation'))]
        self.assertEqual(self.pause(b, c)['key'], 'implementation_attempts')
        self.assertTrue(self.allocate(b, implementation_attempts=2, same_root_cause_attempts=1)['accepted'])
        self.assertEqual(self.pause(b, c)['key'], 'same_root_cause_attempts')
        self.assertEqual(c.state['implementation_attempts'], 1)
        self.allocate(b, implementation_attempts=2, same_root_cause_attempts=2)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')

    def test_facility_pause_reserves_retry_once_after_allocation(self):
        b, c = self.prepared()
        self.allocate(b, infrastructure_retries=0)
        b.workers.overrides = [('test', fixtures.result('ERROR')), ('triage', fixtures.result('PASS', 'test_infrastructure'))]
        self.assertEqual(self.pause(b, c)['key'], 'infrastructure_retries')
        self.assertEqual(c.state['infrastructure_retries'], 0)
        self.allocate(b, infrastructure_retries=1)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(c.state['infrastructure_retries'], 1)
        self.assertEqual(c.state['implementation_attempts'], 1)

    def test_triage_budget_pause_preserves_failure_before_any_new_implementation(self):
        b, c = self.prepared()
        b.workers.overrides = [('implementation', fixtures.result('FAIL')), ('triage', fixtures.result('PASS', 'implementation'))]
        normal = b.workers.run
        def paused(role, *args):
            if role == 'triage':
                raise resources.BudgetReview('api_calls', 1, 1, 1, 30)
            return normal(role, *args)
        with patch.object(b.workers, 'run', side_effect=paused):
            self.pause(b, c)
        self.assertEqual(c.state['pending_triage']['failure']['status'], 'FAIL')
        self.assertEqual(len(c.state['issues']), 0)
        self.assertEqual(c.state['implementation_attempts'], 1)
        self.allocate(b)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(b.workers.calls[2:5], ['implementation', 'triage', 'implementation'])
        self.assertEqual(len(c.state['issues']), 1)

    def test_completed_usage_cannot_be_lowered_by_l1_or_user_amendment(self):
        b, c = self.prepared()
        self.assertFalse(self.allocate(b, l1_reviews=1)['accepted'])
        proposal = amendment({'limits': dict(self.original['limits'], l1_reviews=1)})
        with self.assertRaisesRegex(ValueError, 'cumulative usage'):
            resources.propose_authorization(b.root, self.original, proposal, 'host', c.state)

    def test_real_triage_adapter_can_resume_without_orphan_output_directory(self):
        from dot_runtime.gpt_dot import DotAdapter
        b, c = self.prepared()
        self.allocate(b, api_calls=1)
        b.workers.overrides = [('implementation', fixtures.result('FAIL'))]
        c.adapter = DotAdapter(b.client(), b.workers)
        self.assertEqual(self.pause(b, c)['key'], 'api_calls')
        self.assertFalse((c.run_dir / 'sessions/0002-triage').exists())
        reply = fixtures.result('PASS', 'implementation')
        reply.pop('runtime_evidence')
        reply['evidence'][0]['test_count'] = 0
        self.responses.append({'id': 'triage-fixture', 'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(reply)}]}]})
        self.allocate(b, api_calls=2)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(c.state['implementation_attempts'], 2)
        self.assertEqual(len(c.state['issues']), 1)

    def test_reviews_pause_before_new_review_workspace(self):
        b, c = self.prepared()
        c.state['state'] = 'PLAN_PENDING'; c.save()
        self.allocate(b, l1_reviews=2)
        with self.assertRaises(resources.BudgetReview):
            approve_l1(c, 'plan', 'host', b.workers)
        self.assertFalse((c.run_dir / 'l1-review-inputs/3').exists())
        self.assertEqual(len(c.state['l1_reviews']), 2)

    def test_allocation_change_between_planning_initialization_and_approval_is_not_scope_drift(self):
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})])
        self.allocate(b)
        b.call('dot_plan', {'requirements': 'one implementation task'})
        self.allocate(b, implementation_attempts=2, timeout_seconds=5, infrastructure_retries=0)
        b.call('dot_initialize', {})
        b.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'host'})
        self.allocate(b, implementation_attempts=1, timeout_seconds=4, infrastructure_retries=0)
        b.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'host'})
        c = Controller(b.run_dir); c.check_approvals()
        self.assertEqual(c.state['state'], 'EXECUTING')
        self.assertEqual(c.state['approved_plan']['budgets']['implementation_attempts'], 3)
        self.assertEqual(resources.controller_limits(c.state)['implementation_attempts'], 1)
        self.assertEqual(read_json(b.run_dir / 'l1-review-inputs/2/current-resource-allocation.json')['implementation_attempts'], 1)

    def test_initialization_budget_pause_does_not_leave_an_unbound_partial_run(self):
        plan = fixtures.fixture_plan()
        plan['tasks'].append(dict(id='second', instruction='Complete second unit', depends_on=['build']))
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)})])
        self.allocate(b); b.call('dot_plan', {'requirements': 'two implementation units'})
        self.allocate(b, implementation_attempts=1)
        self.assertEqual(b.call('dot_initialize', {})['state'], 'BUDGET_REVIEW_PENDING')
        self.assertFalse((b.run_dir / 'state.json').exists())
        self.allocate(b, implementation_attempts=2)
        self.assertEqual(b.call('dot_initialize', {})['state'], 'REVIEW_PENDING')
        self.assertTrue(Controller(b.run_dir).state['delegation'])

    def test_facts_revision_pause_preserves_previous_facts(self):
        b = self.bridge([])
        self.allocate(b, revisions=0)
        result = b.call('l1_add_facts', {'facts_json': json.dumps(clarification.supplement()), 'l1_session_id': 'host'})
        self.assertEqual(result['state'], 'BUDGET_REVIEW_PENDING')
        self.assertFalse((b.root / 'facts-index.json').exists())
        self.allocate(b, revisions=1)
        self.assertTrue(b.call('l1_add_facts', {'facts_json': json.dumps(clarification.supplement()), 'l1_session_id': 'host'})['accepted'])

    def test_authorization_requires_exact_request_and_unchanged_state(self):
        b, c = self.prepared()
        request = resources.propose_authorization(b.root, self.original,
            amendment({'limits': dict(self.original['limits'], implementation_attempts=12)}), 'host', c.state)
        self.assertEqual(b.status()['state'], 'AUTHORIZATION_PENDING')
        self.assertFalse(self.allocate(b)['accepted'])
        self.assertFalse(b.call('dot_run', {})['accepted'])
        for signature, state in [('wrong', c.state), (request['sha256'], dict(c.state, implementation_attempts=1))]:
            with self.assertRaisesRegex(ValueError, 'changed'):
                resources.decide_authorization(b.root, self.original, signature, True, 'native-user', state)
        self.assertEqual(resources.authority(self.original, b.root)['limits']['implementation_attempts'], 8)
        resources.decide_authorization(b.root, self.original, request['sha256'], True, 'native-user', c.state)
        self.assertEqual(resources.authority(self.original, b.root)['limits']['implementation_attempts'], 12)
        self.assertTrue(self.allocate(b, implementation_attempts=12)['accepted'])
        c.check_approvals()  # Resource-only authorization does not change the product baseline.

    def test_decline_preserves_authority_and_records_explicit_decision(self):
        b = self.bridge([])
        request = resources.propose_authorization(b.root, self.original, amendment({'allowed_paths': ['.']}), 'host')
        resources.decide_authorization(b.root, self.original, request['sha256'], False, 'native-user')
        self.assertEqual(resources.authority(self.original, b.root), self.original)
        self.assertEqual(resources.read(b.root, self.original)['events'][-1]['record']['kind'], 'user_authorization_declined')

    def test_shrinking_authority_clamps_active_allocation(self):
        b = self.bridge([]); self.allocate(b)
        request = resources.propose_authorization(b.root, self.original,
            amendment({'limits': dict(self.original['limits'], api_calls=2)}), 'host')
        resources.decide_authorization(b.root, self.original, request['sha256'], True, 'native-user')
        self.assertEqual(resources.effective(self.original, b.root)['limits']['api_calls'], 2)

    def test_expired_original_can_be_extended_without_rewriting_original_or_ledger(self):
        self.original['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        atomic_json(self.delegation_path, self.original)
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})])
        self.assertEqual(b.call('dot_plan', {'requirements': 'x'})['resources']['budget_review']['key'], 'expires_at')
        future = datetime.now(timezone.utc) + timedelta(days=2)
        request = resources.propose_authorization(b.root, self.original,
            amendment({'expires_at': future.isoformat()}), 'host')
        resources.decide_authorization(b.root, self.original, request['sha256'], True, 'native-user')
        self.allocate(b)
        b.call('dot_plan', {'requirements': 'x'})
        b.call('dot_initialize', {})
        self.assertEqual(check_binding(Controller(b.run_dir).state)['expires_at'], future.isoformat())
        self.assertEqual(read_json(self.delegation_path), self.original)

    def test_wall_time_pause_retains_clock_and_requires_sufficient_new_allocation(self):
        b = self.bridge([clarification.needs_info()]); self.allocate(b, wall_time_seconds=2)
        b.call('dot_plan', {'requirements': 'x'})
        ledger = read_json(b.root / 'api/ledger.json'); ledger['started_at'] = time.time() - 10
        atomic_json(b.root / 'api/ledger.json', ledger)
        result = b.call('dot_plan', {'requirements': 'y'})
        self.assertEqual(result['resources']['budget_review']['key'], 'wall_time_seconds')
        self.assertFalse(self.allocate(b, wall_time_seconds=2)['accepted'])
        self.assertTrue(self.allocate(b, wall_time_seconds=60)['accepted'])
        self.assertEqual(read_json(b.root / 'api/ledger.json')['started_at'], ledger['started_at'])

    def test_unknown_or_final_cannot_be_unlocked_by_adjustment(self):
        b, c = self.prepared()
        c.state['active_run'] = {'role': 'unknown'}; c.save()
        self.assertFalse(self.allocate(b)['accepted'])
        c.stop_with_report('Unknown process fixture')
        self.assertFalse(self.allocate(b)['accepted'])
        self.assertEqual(c.state['report']['outcome'], 'UNKNOWN')

    def test_unsupported_permissions_and_historical_changes_are_refused(self):
        b = self.bridge([])
        for key in ('external_delivery', 'active_run', 'implementation_attempts', 'independent_test_required', 'api_key'):
            with self.assertRaises(ValueError):
                resources.propose_authorization(b.root, self.original, amendment({key: False}), 'host')
        self.assertFalse((b.root / 'resources.json').exists())

    def test_journal_tampering_is_detected(self):
        b = self.bridge([]); self.allocate(b)
        value = read_json(b.root / 'resources.json'); value['current']['api_calls'] = 999
        atomic_json(b.root / 'resources.json', value)
        with self.assertRaisesRegex(ValueError, 'changed'):
            b.status()

    def test_preflight_final_includes_all_allocations_and_usage(self):
        b = self.bridge([]); self.allocate(b)
        result = b.call('l1_stop', {'reason': 'No feasible remaining plan'})
        report = result['preflight_report']['resource_governance']
        self.assertEqual(report['actual_usage']['api_calls'], 0)
        self.assertEqual(len(report['history']), 1)
        with self.assertRaisesRegex(ValueError, 'final failure'):
            self.allocate(b)

    def test_api_token_accounting_distinguishes_unreported_usage(self):
        reply = clarification.needs_info()
        reply['usage'] = {'input_tokens': 40, 'output_tokens': 10, 'total_tokens': 50}
        b = self.bridge([reply, clarification.needs_info()]); self.allocate(b)
        b.call('dot_plan', {'requirements': 'a'})
        b.call('dot_plan', {'requirements': 'b'})
        value = b.status()['resources']['dot_api_reported_usage']
        self.assertEqual(value, {'reported_requests': 1, 'unreported_requests': 1,
                                'input_tokens': 40, 'output_tokens': 10, 'total_tokens': 50})


class NativeAuthorizationTests(unittest.TestCase):
    request = onboarding.OnboardingTests.request
    prepare = onboarding.OnboardingTests.prepare
    confirm = onboarding.OnboardingTests.confirm

    def setUp(self):
        onboarding.OnboardingTests.setUp(self)
        self.draft['adaptive_limits'] = True
        self.confirm()
        self.settings_saved = read_json(self.task / 'bridge.json')
        self.bridge = Bridge(self.settings_saved, workers=fixtures.FakeAdapter())
        self.original = read_json(self.task / 'delegation.json')

    def stage(self, changes):
        return self.request('bridge', name='l1_request_authorization',
                            arguments={'proposal_json': json.dumps(amendment(changes))})

    def test_native_scope_authorization_requires_replanning_and_preserves_original(self):
        before = (self.task / 'delegation.json').read_bytes()
        self.stage({'authorized_context': 'New explicitly approved acceptance criteria'})
        pending = self.request('authorization_preview')
        self.request('authorization_decide', sha256=pending['sha256'], approved=True)
        self.assertEqual(self.bridge.status()['state'], 'NEEDS_INFO')
        self.assertEqual(before, (self.task / 'delegation.json').read_bytes())
        self.assertEqual(resources.authority(self.original, self.bridge.root)['authorized_context'], 'New explicitly approved acceptance criteria')

    def test_public_tool_cannot_supply_human_decision_or_actor(self):
        for arguments in [{'approved': True}, {'proposal_json': '{}', 'l1_session_id': 'forged'}]:
            with self.assertRaises(ValueError):
                self.request('bridge', name='l1_request_authorization', arguments=arguments)

    def test_initialized_scope_change_invalidates_old_approvals_until_revised(self):
        from dot_runtime.gpt_dot import ResponsesClient
        plan = fixtures.fixture_plan()
        replies = [delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)}) for _ in range(2)]
        b = self.bridge
        b.client_factory = lambda m, p, d: ResponsesClient(m, p, d, lambda *_: replies.pop(0))
        with patch.object(onboarding.o, 'Bridge', return_value=b):
            self.request('bridge', name='l1_adjust_limits', arguments={'proposal_json': json.dumps(allocation(self.original['limits']))})
            self.request('bridge', name='dot_plan', arguments={'requirements': 'initial'})
            self.request('bridge', name='dot_initialize', arguments={})
            for kind in ('requirements', 'plan'):
                self.request('bridge', name='l1_approve', arguments={'kind': kind})
            self.stage({'allowed_paths': ['value.py'], 'authorized_context': 'Explicit updated requirement'})
            pending = self.request('authorization_preview')
            self.request('authorization_decide', sha256=pending['sha256'], approved=True)
            c = Controller(b.run_dir)
            self.assertEqual(c.state['state'], 'DECISION_PENDING')
            self.assertFalse(c.state['approvals'])
            self.assertFalse(self.request('bridge', name='dot_run', arguments={})['accepted'])
            self.request('bridge', name='dot_plan', arguments={'requirements': 'new requirement'})
            self.request('bridge', name='l1_revise', arguments={'requirements': 'new requirement', 'plan_json': json.dumps(plan)})
            for kind in ('requirements', 'plan'):
                self.request('bridge', name='l1_approve', arguments={'kind': kind})
            c = Controller(b.run_dir); c.check_approvals()
            self.assertEqual(c.state['revision_count'], 1)
            self.assertEqual(len(c.state['l1_reviews']), 4)
            self.assertEqual(c.state['implementation_attempts'], 0)

    def test_interruption_blocks_native_authorization_but_not_status(self):
        self.stage({'allowed_paths': ['input.txt']})
        atomic_json(self.task / 'interrupted.json', {'status': 'UNKNOWN'})
        with self.assertRaisesRegex(ValueError, 'Unknown operation'):
            self.request('authorization_preview')
        self.request('status')

    def test_original_delegation_hash_guard_applies_before_resource_management(self):
        changed = dict(self.original, allowed_paths=['extra'])
        atomic_json(self.task / 'delegation.json', changed)
        with self.assertRaisesRegex(ValueError, 'delegation changed'):
            self.stage({'allowed_paths': ['.']})


if __name__ == '__main__':
    unittest.main()

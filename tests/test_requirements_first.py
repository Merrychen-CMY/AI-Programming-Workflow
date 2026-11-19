"""Pre-plan requirements approval, with fake independent reviewers only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_runtime_controller as fixtures
from test_delegated_l1 import delegation
from dot_runtime import clarifications, requirements_review as rr, resources
from dot_runtime.controller import Controller, atomic_json, file_hash, initialize, read_json
from dot_runtime.delegation import ApprovalRejected, approve_l1, attach_delegation


class RequirementsFirstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'value.py').write_text('def value():\n    return 1\n', encoding='utf-8')
        self.control = self.root / 'control'
        self.control.mkdir()
        self.raw = self.root / 'raw.md'
        self.raw.write_text('Return 3; Python only; no external actions.', encoding='utf-8')
        self.authority = self.root / 'delegation.json'
        atomic_json(self.authority, delegation(self.raw))
        self.settings = {'project': str(self.project), 'control_dir': str(self.control),
                         'raw_input': str(self.raw), 'delegation': str(self.authority),
                         'model': 'fixture-model', 'environment_context': '{}'}
        self.workers = fixtures.FakeAdapter()
        self.text = 'Return 3. Independently test value() == 3; no external actions.'

    def approved(self):
        rr.prepare(self.settings, self.text)
        return rr.approve(self.settings, 'l1-author', self.workers)

    def initialized(self):
        self.approved()
        plan = self.control / 'plan.json'
        atomic_json(plan, fixtures.fixture_plan())
        run = self.control / 'run'
        initialize(self.project, self.control / 'requirements.md', plan, run)
        controller = Controller(run)
        controller.state['clarification_root'] = str(self.control)
        controller.save()
        attach_delegation(controller, self.authority, self.raw)
        return controller

    def test_no_planning_before_independent_approval(self):
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings, self.text)
        rr.prepare(self.settings, self.text)
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings, self.text)
        self.assertFalse((self.control / 'api/ledger.json').exists())
        self.assertFalse((self.control / 'run').exists())

    def test_approval_has_no_plan_and_reserves_review_before_dispatch(self):
        seen = []
        def inspect(role, workspace):
            self.assertEqual(role, 'review')
            self.assertFalse((workspace / 'plan.json').exists())
            value = read_json(self.control / rr.STATE)
            self.assertEqual(len(value['reviews']), 1)
            self.assertIsNotNone(value['active_run'])
            usage = read_json(workspace / 'sources/usage.json')
            self.assertEqual(usage['actual_usage']['l1_reviews'], 1)
            self.assertEqual(usage['actual_usage']['api_calls'], 0)
            seen.append(file_hash(workspace / 'raw-input.md'))
        self.workers.hook = inspect
        self.assertEqual(self.approved()['state'], 'REQUIREMENTS_APPROVED')
        receipt = rr.require_approved(self.settings, self.text)
        self.assertEqual(receipt['authority'], 'delegated_l1')
        self.assertEqual(seen, [file_hash(self.raw)])
        self.assertEqual(read_json(self.control / 'api/ledger.json')['attempts'], [])
        self.assertEqual(rr.review_count(self.control), 1)
        self.assertTrue(rr.started_at(self.control))

    def test_idempotent_prepare_and_approval_do_not_charge_again(self):
        self.approved()
        before = (self.control / rr.STATE).read_bytes()
        rr.prepare(self.settings, self.text)
        rr.approve(self.settings, 'l1-author', self.workers)
        self.assertEqual((self.control / rr.STATE).read_bytes(), before)
        self.assertEqual(self.workers.calls, ['review'])

    def test_exact_text_and_project_are_bound(self):
        self.approved()
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings, self.text + '\nWeakened AC')
        (self.project / 'value.py').write_text('changed product baseline', encoding='utf-8')
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings)

    def test_raw_input_and_delegation_are_bound(self):
        self.approved()
        self.raw.write_text('Changed original target', encoding='utf-8')
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings)

    def test_new_facts_invalidate_approval_and_preserve_review_usage(self):
        self.approved()
        supplement = {'reason': 'Measured local environment', 'facts': [
            {'name': 'Python', 'value': '3.14', 'source': 'local version evidence', 'stage': 'planning'}]}
        clarifications.append(self.control, supplement, 'l1-author', read_json(self.authority))
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings)
        rr.prepare(self.settings, self.text)
        self.assertEqual(rr.revision_count(self.control), 1)
        self.assertEqual(rr.approve(self.settings, 'l1-author', self.workers)['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(rr.review_count(self.control), 2)

    def test_requirements_edits_consume_revisions_without_resetting_clock(self):
        self.approved()
        start = rr.started_at(self.control)
        rr.prepare(self.settings, self.text + '\nDocument observed test evidence.')
        self.assertEqual(rr.revision_count(self.control), 1)
        self.assertEqual(rr.review_count(self.control), 1)
        self.assertEqual(rr.approve(self.settings, 'l1-author', self.workers)['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(rr.started_at(self.control), start)
        self.assertEqual(len(list((self.control / 'requirements-review/drafts').glob('*.md'))), 2)

    def test_author_and_reused_reviewer_cannot_approve(self):
        rr.prepare(self.settings, self.text)
        self.assertEqual(rr.approve(self.settings, 'session-1', self.workers)['state'], 'DECISION_PENDING')
        self.assertIsNone(rr.status(self.control)['approval'])
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings)
        self.workers = fixtures.FakeAdapter(reused_session=True)
        first = rr.approve(self.settings, 'l1-author', self.workers)
        self.assertEqual(first['state'], 'REQUIREMENTS_APPROVED')
        rr.prepare(self.settings, self.text + '\nRetain evidence.')
        self.assertEqual(rr.approve(self.settings, 'l1-author', self.workers)['state'], 'DECISION_PENDING')

    def test_packet_tampering_rejects_approval_without_final_report(self):
        self.workers.hook = lambda _, workspace: (workspace / 'sources/usage.json').write_text('{}')
        self.assertEqual(self.approved()['state'], 'DECISION_PENDING')
        self.assertIsNone(rr.status(self.control)['approval'])
        self.assertFalse((self.control / 'preflight-report.json').exists())

    def test_project_changed_during_review_never_receives_approval(self):
        self.workers.hook = lambda *_: (self.project / 'value.py').write_text('changed during review', encoding='utf-8')
        self.assertEqual(self.approved()['state'], 'DECISION_PENDING')
        self.assertIsNone(rr.status(self.control)['approval'])
        self.assertEqual(rr.review_count(self.control), 1)
        self.assertIsNone(rr.status(self.control)['active_run'])

    def test_completed_evidence_tampering_prevents_planning(self):
        self.approved()
        (self.control / 'requirements-review/outputs/1/result.json').write_text('{}')
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings)

    def test_unknown_review_requires_native_reconciliation_and_keeps_history(self):
        self.workers.overrides = [('review', fixtures.result('UNKNOWN'))]
        self.assertEqual(self.approved()['state'], 'DECISION_PENDING')
        active = rr.status(self.control)['active_run']
        for operation in (lambda: rr.prepare(self.settings, self.text),
                          lambda: rr.approve(self.settings, 'l1-author', self.workers),
                          lambda: rr.require_approved(self.settings)):
            with self.assertRaises(ApprovalRejected):
                operation()
        with self.assertRaises(ApprovalRejected):
            rr.reconcile_review(self.settings, active, {'authority': 'delegated_l1'})
        native = {'authority': 'local_harness_user', 'session': 'native-user-session',
                  'all_processes_stopped': True, 'evidence': ['requirements-review/outputs/1/result.json']}
        with self.assertRaises(ApprovalRejected):
            rr.reconcile_review(self.settings, {**active, 'operation_id': 'stale'}, native)
        rr.reconcile_review(self.settings, active, native)
        self.assertEqual(rr.review_count(self.control), 1)
        self.assertEqual(rr.status(self.control)['reviews'][0]['status'], 'UNKNOWN')
        self.assertIsNone(rr.status(self.control)['approval'])
        self.assertEqual(rr.approve(self.settings, 'l1-author', self.workers)['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(rr.review_count(self.control), 2)

    def test_reviewer_exception_reserves_unknown_and_cannot_replay(self):
        class Broken:
            def run(self, *args):
                raise OSError('simulated startup outcome ambiguity')
        rr.prepare(self.settings, self.text)
        value = rr.approve(self.settings, 'l1-author', Broken())
        self.assertIsNotNone(value['active_run'])
        self.assertEqual(value['reviews'][0]['status'], 'UNKNOWN')
        with self.assertRaises(ApprovalRejected):
            rr.approve(self.settings, 'l1-author', self.workers)

    def test_adoption_retains_evidence_and_skips_second_requirements_review(self):
        controller = self.initialized()
        original = (self.control / rr.STATE).read_bytes()
        result = rr.adopt(controller, self.settings)
        self.assertEqual(result['state'], 'PLAN_PENDING')
        self.assertEqual(len(result['l1_reviews']), 1)
        self.assertEqual(self.workers.calls, ['review'])
        for entry in result['l1_reviews'][0]['evidence_files']:
            self.assertEqual(file_hash(controller.run_dir / entry['path']), entry['sha256'])
        self.assertEqual((self.control / rr.STATE).read_bytes(), original)
        approve_l1(controller, 'plan', 'l1-author', self.workers)
        controller.check_approvals()
        controller.check_evidence()
        self.assertEqual(controller.state['state'], 'EXECUTING')
        self.assertEqual(len(controller.state['l1_reviews']), 2)
        self.assertEqual(self.workers.calls, ['review', 'review'])
        with self.assertRaises(ApprovalRejected):
            rr.adopt(controller, self.settings)

    def test_adoption_refuses_snapshot_mismatch(self):
        controller = self.initialized()
        controller.state['initial_snapshot']['id'] = 'wrong snapshot'
        with self.assertRaises(ApprovalRejected):
            rr.adopt(controller, self.settings)
        self.assertFalse(controller.state['approvals'])

    def test_review_budget_exhaustion_cannot_dispatch_or_finalize(self):
        authority = read_json(self.authority)
        authority['limits']['l1_reviews'] = 1
        atomic_json(self.authority, authority)
        self.workers.overrides = [('review', fixtures.result('FAIL'))]
        self.approved()
        with self.assertRaises(ApprovalRejected):
            rr.approve(self.settings, 'l1-author', self.workers)
        self.assertEqual(self.workers.calls, ['review'])
        self.assertFalse((self.control / 'preflight-report.json').exists())

    def test_adaptive_scope_change_invalidates_but_allocation_change_does_not(self):
        original = read_json(self.authority)
        original['adaptive_limits'] = True
        atomic_json(self.authority, original)
        explanation = {'reason': 'Use existing authorization', 'evidence': 'initial goals',
                       'remaining_work': 'requirements and plan', 'expected_benefit': 'bounded review',
                       'strategy': 'one independent review before planning'}
        resources.adjust(self.control, original, {'limits': original['limits'], **explanation}, 'l1-author')
        self.approved()
        limits = {**original['limits'], 'api_calls': 7}
        resources.adjust(self.control, original, {'limits': limits, **explanation}, 'l1-author')
        rr.require_approved(self.settings)
        journal = resources.read(self.control, original)
        journal['amendments']['allowed_paths'] = ['value.py', 'tests']
        resources.save(self.control, journal, 'test_native_scope_amendment', {'fixture': True})
        with self.assertRaises(ApprovalRejected):
            rr.require_approved(self.settings)


class NativeRequirementsFlowTests(unittest.TestCase):
    """Actual native Python dispatch/Bridge order; API/review/OS probes are fake."""
    def setUp(self):
        from dot_runtime import onboarding
        from dot_runtime.bridge import Bridge
        from dot_runtime.gpt_dot import ResponsesClient
        from test_delegated_l1 import call_response
        self.o = onboarding
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'product'
        self.project.mkdir()
        (self.project / 'value.py').write_text('def value():\n    return 1\n', encoding='utf-8')
        (self.project / 'test_value.py').write_text('import unittest\nfrom value import value\n'
            'class ValueTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 3)\n', encoding='utf-8')
        self.task = self.base / 'task'
        self.text = 'Return 3; verify value() == 3 independently. No external actions.'
        self.host = {'model': 'fixture-model', 'python': sys.executable, 'codex': 'fixture-codex'}
        self.draft = read_json(fixtures.ROOT / 'templates/harness-start.example.json')
        self.draft.update(project=str(self.project), raw_input=self.text,
            topics={key: '明确条件：Python测试，无设备或发布' for key in onboarding.TOPICS},
            allowed_paths=['value.py'], hard_constraints=['No external actions'],
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            environment_checks=[{'name': 'Python', 'kind': 'file', 'path': sys.executable,
                                 'required': True, 'evidence': 'Fixture uses the current interpreter'}])
        self.plan = fixtures.fixture_plan()
        self.plan['verification']['test_command'] = [sys.executable, '-B', '-m', 'unittest', '-v']
        self.plan['verification']['test_contract']['required_tests'] = ['test_value.ValueTest.test_value']
        self.responses = [call_response('propose_plan', {'plan_json': json.dumps(self.plan)}),
                          call_response('run_approved_workflow')]
        self.workers = fixtures.FakeAdapter()
        def factory(model, path, authority):
            return ResponsesClient(model, path, authority, send=lambda *_: self.responses.pop(0))
        for patcher in (patch.object(onboarding, 'tool_check', return_value={'status': 'PASS', 'exit_code': 0}),
                        patch.object(onboarding, 'Bridge', side_effect=lambda settings: Bridge(settings, factory, self.workers))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, action, **fields):
        return self.o.dispatch({'action': action, 'task': str(self.task),
                               'session': 'native-l1-session', **fields})

    def tool(self, name, **arguments):
        return self.request('bridge', name=name, arguments=arguments)

    def confirmed(self):
        environment = self.request('environment', settings=self.host)
        atomic_json(self.task / 'environment.json', {'settings': self.host, **environment})
        prepared = self.request('inspect', draft=self.draft, settings=self.host)
        self.request('confirm', draft=self.draft, settings=self.host, digest=prepared['digest'])
        self.assertTrue(read_json(self.task / 'bridge.json')['requirements_first'])
        self.assertTrue(read_json(self.task / 'delegation.json')['adaptive_limits'])
        self.allocation = {'limits': self.draft['limits'], 'reason': 'Initial allocation',
            'evidence': 'Confirmed scope and baseline', 'remaining_work': 'Review, implement and verify',
            'expected_benefit': 'Complete the bounded task', 'strategy': 'Independent review and verification'}
        self.tool('l1_adjust_limits', proposal_json=json.dumps(self.allocation))

    def test_native_flow_reviews_requirements_before_any_dot_call_and_finishes(self):
        self.confirmed()
        refusal = self.tool('dot_plan', requirements=self.text)
        self.assertEqual(refusal['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(len(self.responses), 2)
        self.assertEqual(self.workers.calls, [])
        self.tool('l1_prepare_requirements', requirements=self.text)
        approved = self.tool('l1_approve', kind='requirements')
        self.assertEqual(approved['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(len(self.responses), 2)
        self.assertEqual(self.workers.calls, ['review'])
        self.tool('dot_plan', requirements=self.text)
        initialized = self.tool('dot_initialize')
        self.assertEqual(initialized['state'], 'PLAN_PENDING')
        self.assertEqual(len(initialized['l1_reviews']), 1)
        self.assertEqual(self.workers.calls, ['review'])
        from dot_runtime.presentation import compact
        displayed = compact(initialized)['latest_review']['record']
        detail = self.tool('dot_read_record', path=displayed, offset='0')
        self.assertIn('session-1', detail['content'])
        self.assertEqual(self.tool('l1_approve', kind='plan')['state'], 'EXECUTING')
        finished = self.tool('dot_run')
        self.assertEqual(finished['report']['outcome'], 'SUCCESS')
        with self.assertRaisesRegex(ValueError, 'L1 must submit'):
            self.request('report_preview')
        self.tool('l1_submit_report', summary='One actual local unittest passed; independent sessions reviewed the same candidate.')
        report = self.request('report_preview')
        reviewed = self.request('report_confirm', sha256=report['sha256'])
        self.assertEqual(reviewed['state'], 'DELIVERY_READY')
        state = read_json(self.task / 'control/run/state.json')
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertEqual(len(state['l1_reviews']), 2)
        self.assertEqual(len(read_json(self.task / 'control/api/ledger.json')['attempts']), 2)

    def test_preflight_unknown_keeps_resource_mutation_protected(self):
        self.confirmed()
        self.workers.overrides = [('review', fixtures.result('UNKNOWN'))]
        self.tool('l1_prepare_requirements', requirements=self.text)
        unknown = self.tool('l1_approve', kind='requirements')
        from dot_runtime.presentation import compact
        self.assertIn('l1_prepare_recovery', compact(unknown)['next'])
        self.assertIn('workflow_recover_operation', compact(unknown)['next'])
        self.assertNotIn('l1_revise', compact(unknown)['next'])
        before = (self.task / 'control/resources.json').read_bytes()
        response = self.tool('l1_adjust_limits', proposal_json=json.dumps(self.allocation))
        self.assertFalse(response['accepted'])
        self.assertEqual((self.task / 'control/resources.json').read_bytes(), before)
        self.assertIsNotNone(rr.status(self.task / 'control')['active_run'])

    def test_preflight_review_rejection_and_revision_prompt_use_preplan_approval_tools(self):
        self.confirmed()
        self.workers.overrides = [('review', fixtures.result('FAIL'))]
        self.tool('l1_prepare_requirements', requirements=self.text)
        rejected = self.tool('l1_approve', kind='requirements')
        from dot_runtime.presentation import compact
        self.assertIn('l1_prepare_requirements', compact(rejected)['next'])
        self.assertIn('l1_approve kind=requirements before dot_plan', compact(rejected)['next'])
        refusal = self.tool('l1_revise', requirements=self.text, plan_json=json.dumps(fixtures.fixture_plan()))
        self.assertFalse(refusal['accepted'])
        self.assertIn('l1_prepare_requirements', refusal['reason'])
        self.assertIn('l1_approve kind=requirements before dot_plan', refusal['reason'])
        self.assertEqual(self.workers.calls, ['review'])
        self.assertEqual(len(self.responses), 2)

    def test_preflight_fact_supplement_requests_requirements_reapproval_before_planning(self):
        self.confirmed()
        self.tool('l1_prepare_requirements', requirements=self.text)
        self.tool('l1_approve', kind='requirements')
        supplement = {'reason': 'Clarify the existing local test environment', 'facts': [
            {'name': 'Test framework', 'value': 'unittest', 'source': 'Confirmed project test file', 'stage': 'planning'}]}
        response = self.tool('l1_add_facts', facts_json=json.dumps(supplement))
        self.assertTrue(response['accepted'])
        self.assertIn('l1_prepare_requirements', response['next'])
        self.assertIn('then l1_approve kind=requirements, then dot_plan', response['next'])
        self.assertEqual(len(self.responses), 2)
        self.assertEqual(self.workers.calls, ['review'])

    def test_preflight_unknown_final_report_preserves_actual_operation_state(self):
        self.confirmed()
        self.workers.overrides = [('review', fixtures.result('UNKNOWN'))]
        self.tool('l1_prepare_requirements', requirements=self.text)
        self.tool('l1_approve', kind='requirements')
        closure = {'summary': 'L1 evaluated the available recovery evidence and cannot establish a feasible safe continuation.',
            'solutions_considered': ['Reviewed independent session output; no verified process reconciliation is available.'],
            'authority_review': 'No authority to bypass unreconciled review execution.',
            'resource_review': 'More reviews would replay an unresolved operation; usage retained.',
            'termination_basis': 'no_feasible_solution', 'evidence': ['requirements-review/state.json']}
        response = self.tool('l1_stop', reason=json.dumps(closure))
        self.assertEqual(response['preflight_report']['outcome'], 'UNKNOWN')
        self.assertIsNotNone(response['preflight_report']['requirements_review']['active_run'])
        self.assertEqual(response['preflight_report']['requirements_review']['reviews'][0]['status'], 'UNKNOWN')

    def test_native_closure_evidence_uses_the_same_control_relative_paths_as_record_reader(self):
        self.confirmed()
        self.tool('l1_prepare_requirements', requirements=self.text)
        self.tool('l1_approve', kind='requirements')
        self.tool('dot_plan', requirements=self.text)
        self.tool('dot_initialize')
        self.workers.overrides = [('review', fixtures.result('FAIL'))]
        self.assertEqual(self.tool('l1_approve', kind='plan')['state'], 'DECISION_PENDING')
        evidence = 'run/l1-reviews/2/result.json'
        self.assertIn('FAIL', self.tool('dot_read_record', path=evidence, offset='0')['content'])
        closure = {'summary': 'L1 assessed the rejected plan and the recorded alternatives before closure.',
            'solutions_considered': ['No feasible implementation/verification plan is supported by the fixture evidence.'],
            'authority_review': 'Existing scope and independent verification requirements remain binding.',
            'resource_review': 'Review attempts retained; more resources do not establish a viable fixture plan.',
            'termination_basis': 'no_feasible_solution', 'evidence': [evidence]}
        response = self.tool('l1_stop', reason=json.dumps(closure))
        self.assertEqual(response['state'], 'REPORT_PENDING')
        self.assertEqual(response['report']['outcome'], 'FAILED')


if __name__ == '__main__':
    unittest.main()

"""Real isolated controller recovery; model outputs and human approvals are fixtures."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_runtime_controller as fixtures
from dot_runtime.adapter import RESULT_SCHEMA, TRIAGE_SCHEMA, _valid_report, _valid_triage
from dot_runtime.controller import Controller, atomic_json, read_json, file_hash
from dot_runtime.gpt_dot import DotAdapter


def diagnosis(cause='implementation', **updates):
    value = fixtures.result('FAIL', cause)
    value.pop('runtime_evidence')
    value['evidence'][0]['test_count'] = 0
    value.update(fixtures.triage_fields(cause))
    value.update(updates)
    return value


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.ControllerTests.setUp
    controller = fixtures.ControllerTests.controller
    approved = fixtures.ControllerTests.approved

    def paused(self, cause='implementation', **updates):
        c = self.approved(fixtures.FakeAdapter([('triage', diagnosis(cause, **updates))]))
        c.request_l1_assessment('Investigate the recorded failure before further work')
        return c

    def investigate(self, c):
        return c.investigate(json.dumps({'question': 'Identify a bounded in-scope recovery and its verification',
            'evidence': ['run/decision-request.json']}))

    def decide(self, c, action='repair'):
        return c.resume_decision(json.dumps({'action': action,
            'reason': 'The evidenced proposal fits the unchanged approved baseline and remaining allocation',
            'evidence': ['run/decision-request.json']}))

    def test_readonly_investigation_keeps_usage_and_history_and_never_dispatches_product(self):
        c = self.paused()
        c.state['implementation_attempts'] = 1
        c.state['completed_tasks'] = ['build']
        c.state['root_cause_counts'] = {'implementation:prior': 1}
        c.save()
        before = read_json(c.state_path)
        project = (self.project / 'README.md').read_bytes()
        state = self.investigate(c)
        self.assertEqual(state['state'], 'DECISION_PENDING')
        self.assertEqual(c.adapter.calls, ['triage'])
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertEqual(state['completed_tasks'], ['build'])
        self.assertEqual(state['root_cause_counts'], before['root_cause_counts'])
        self.assertEqual(state['history'][:len(before['history'])], before['history'])
        self.assertEqual((self.project / 'README.md').read_bytes(), project)
        self.assertIsNone(state['report'])

    def test_investigation_preserves_actual_unresolved_product_operation(self):
        c = self.paused()
        active = {'role': 'implementation', 'pid': 1234, 'output': 'recorded-worker'}
        c.state['active_run'] = copy.deepcopy(active); c.save()
        self.investigate(c)
        self.assertEqual(c.state['active_run'], active)
        self.assertIsNone(c.state['analysis_activity'])
        with self.assertRaisesRegex(ValueError, 'Unresolved operations'):
            self.decide(c)
        self.assertEqual(c.adapter.calls, ['triage'])

    def test_unresolved_analysis_cannot_be_overwritten_by_another_analysis(self):
        c = self.paused()
        c.state['analysis_activity'] = {'role': 'triage', 'output': 'old-analysis'}; c.save()
        with self.assertRaisesRegex(ValueError, 'unresolved analysis'):
            self.investigate(c)
        self.assertEqual(c.adapter.calls, [])
        self.assertEqual(read_json(c.state_path)['analysis_activity']['output'], 'old-analysis')

    def test_historical_unknown_failure_does_not_recreate_reconciled_operation(self):
        c = self.paused()
        c.state['issues'] = [{'id': 'ISSUE-0001', 'failure': fixtures.result('UNKNOWN'),
                             'analysis': diagnosis('needs_analysis'), 'root_cause': 'needs_analysis'}]
        c.state['active_run'] = None; c.save()
        self.investigate(c)
        self.assertIsNone(c.state['active_run'])

    def test_preapproval_investigation_uses_unapproved_draft_and_cannot_resume_execution(self):
        c = self.controller(fixtures.FakeAdapter([('triage', diagnosis())]))
        c.request_l1_assessment('Independent requirement review needs investigation')
        self.investigate(c)
        self.assertEqual(c.adapter.calls, ['triage'])
        self.assertEqual(c.state['approvals'], {})
        with self.assertRaisesRegex(ValueError, 'approval missing'):
            self.decide(c)

    def test_repair_requires_l1_decision_then_reverifies_all_checks(self):
        c = self.paused()
        self.investigate(c)
        self.assertEqual(c.adapter.calls, ['triage'])
        self.assertEqual(self.decide(c)['state'], 'EXECUTING')
        self.assertEqual(c.adapter.calls, ['triage'])
        self.assertEqual(c.state['implementation_attempts'], 0)
        state = c.run()
        self.assertEqual(c.adapter.calls, ['triage', 'implementation', 'test', 'review'])
        self.assertEqual(state['report']['outcome'], 'SUCCESS')

    def test_reverify_preserves_completed_tasks_and_runs_independent_checks(self):
        c = self.paused('test_infrastructure')
        c.state['completed_tasks'] = ['build']
        c.state['implementation_attempts'] = 1; c.save()
        self.investigate(c)
        self.decide(c, 'reverify')
        state = c.run()
        self.assertEqual(c.adapter.calls, ['triage', 'test', 'review'])
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertEqual(state['infrastructure_retries'], 1)
        self.assertEqual(state['report']['outcome'], 'SUCCESS')

    def test_reverify_cannot_skip_incomplete_implementation(self):
        c = self.paused('test_infrastructure')
        self.investigate(c)
        with self.assertRaisesRegex(ValueError, 'Incomplete implementation'):
            self.decide(c, 'reverify')
        self.assertEqual(c.state['state'], 'DECISION_PENDING')

    def test_permission_gap_and_incomplete_evidence_cannot_be_resumed(self):
        c = self.paused('implementation', permission_gaps=['Device flashing not authorized'],
                        recommended_action='request_authorization')
        self.investigate(c)
        with self.assertRaisesRegex(ValueError, 'no complete authorized proposal'):
            self.decide(c)
        self.assertIsNone(c.state['report'])

    def test_scope_or_evidence_changes_require_review_not_direct_resume(self):
        c = self.paused()
        self.investigate(c)
        self.requirements.write_text('A different product target', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'approval missing'):
            self.decide(c)
        self.assertEqual(c.adapter.calls, ['triage'])

    def test_external_product_edit_prevents_direct_resume(self):
        c = self.paused(); self.investigate(c)
        (self.project / 'README.md').write_text('External edit', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Project changed'):
            self.decide(c)

    def test_an_older_issue_cannot_authorize_recovery_of_a_new_decision(self):
        c = self.paused(); self.investigate(c)
        c.request_l1_assessment('A separate new problem needs assessment')
        with self.assertRaisesRegex(ValueError, 'fresh L2 proposal'):
            self.decide(c)
        self.assertEqual(c.adapter.calls, ['triage'])

    def test_readonly_and_baseline_decisions_never_grant_execution(self):
        c = self.paused()
        for action in ('continue_investigation', 'revise_baseline'):
            self.assertEqual(self.decide(c, action)['state'], 'DECISION_PENDING')
        self.assertEqual(c.adapter.calls, [])
        self.assertIsNone(c.state['report'])

    def test_missing_evidence_and_path_traversal_are_rejected_before_dispatch(self):
        c = self.paused()
        for evidence in ([], ['../secret.json'], ['run/triage-inputs/missing.json']):
            with self.assertRaises((ValueError, OSError)):
                c.investigate({'question': 'Inspect failure', 'evidence': evidence})
        self.assertEqual(c.adapter.calls, [])

    def test_empty_analysis_cannot_become_an_execution_permission(self):
        c = self.paused(solutions=[], investigation_steps=[])
        self.investigate(c)
        issue = c.state['issues'][-1]
        self.assertEqual(issue['root_cause'], 'needs_analysis')
        self.assertIn('triage_contract_error', issue['analysis'])
        with self.assertRaisesRegex(ValueError, 'no complete authorized proposal'):
            self.decide(c)

    def test_same_baseline_revision_preserves_completed_work_and_invalidates_candidate(self):
        c = self.paused()
        c.state['completed_tasks'] = ['build']; c.state['implementation_attempts'] = 1
        c.make_candidate(); old = copy.deepcopy(c.state['candidate']); c.save()
        state = c.revise(self.requirements, self.plan)
        self.assertEqual(state['completed_tasks'], ['build'])
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertIsNone(state['candidate']); self.assertEqual(state['approvals'], {})
        c.approve('requirements', lambda *_: True); c.approve('plan', lambda *_: True)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertNotIn('implementation', c.adapter.calls)
        self.assertEqual(c.adapter.calls, ['test', 'review'])
        self.assertNotEqual(c.state['candidate']['path'], old['path'])

    def test_task_change_invalidates_dependents_but_retains_independent_completed_tasks(self):
        c = self.approved()
        old = copy.deepcopy(c.state['approved_plan'])
        old['tasks'] = [{'id': 'a', 'instruction': 'A', 'depends_on': []},
                        {'id': 'b', 'instruction': 'B', 'depends_on': ['a']},
                        {'id': 'c', 'instruction': 'C', 'depends_on': []}]
        atomic_json(self.plan, old)
        c.state['approved_plan'] = old; c.state['completed_tasks'] = ['a', 'b', 'c']
        c.state['implementation_attempts'] = 3; c.request_l1_assessment('Review task A change')
        new = copy.deepcopy(old); new['tasks'][0]['instruction'] = 'A revised'
        updated = self.root / 'changed-plan.json'; atomic_json(updated, new)
        state = c.revise(self.requirements, updated)
        self.assertEqual(state['completed_tasks'], ['c'])
        self.assertEqual(state['implementation_attempts'], 3)
        entry = next(h for h in reversed(state['history']) if h.get('action') == 'revised_project_baseline')
        self.assertEqual(entry['invalidated_completed_tasks'], ['a', 'b'])

    def test_verification_only_change_preserves_implementation_but_requires_approval(self):
        c = self.paused(); c.state['completed_tasks'] = ['build']; c.save()
        changed = read_json(self.plan)
        changed['verification']['test_instructions'] += '; additionally test boundary values'
        staged = self.root / 'new-plan.json'; atomic_json(staged, changed)
        state = c.revise(self.requirements, staged)
        self.assertEqual(state['completed_tasks'], ['build'])
        self.assertEqual(state['state'], 'REVIEW_PENDING')
        self.assertEqual(state['approvals'], {})

    def test_changed_requirements_or_unrecorded_product_change_invalidates_reuse(self):
        c = self.paused(); c.state['completed_tasks'] = ['build']; c.save()
        changed = self.root / 'changed-requirements.md'; changed.write_text('New target', encoding='utf-8')
        self.assertEqual(c.revise(changed, self.plan)['completed_tasks'], [])


class TriageContractTests(unittest.TestCase):
    def test_separate_schema_does_not_change_ordinary_worker_contract(self):
        ordinary = diagnosis()
        for key in set(TRIAGE_SCHEMA['required']) - set(RESULT_SCHEMA['required']):
            ordinary.pop(key)
        self.assertTrue(_valid_report(ordinary))
        self.assertFalse(_valid_triage(ordinary))

    def test_missing_solution_and_investigation_is_not_valid_triage(self):
        value = diagnosis(solutions=[], investigation_steps=[])
        self.assertFalse(_valid_triage(value))
        value = diagnosis('needs_analysis', solutions=[], investigation_steps=['Read the exact failed command log'])
        self.assertTrue(_valid_triage(value))

    def test_unknown_diagnosis_and_permission_gap_cannot_claim_ready_repair(self):
        self.assertFalse(_valid_triage(diagnosis('needs_analysis', recommended_action='repair')))
        self.assertFalse(_valid_triage(diagnosis(permission_gaps=['outside scope'])))
        value = diagnosis(); value['solutions'][0]['permission_gaps'] = ['device access']
        self.assertFalse(_valid_triage(value))

    def test_solution_requires_benefit_and_concrete_verification(self):
        value = diagnosis(); value['solutions'][0]['verification_steps'] = []
        self.assertFalse(_valid_triage(value))
        value = diagnosis(); value['solutions'][0]['expected_benefit'] = ''
        self.assertFalse(_valid_triage(value))


if __name__ == '__main__':
    unittest.main()

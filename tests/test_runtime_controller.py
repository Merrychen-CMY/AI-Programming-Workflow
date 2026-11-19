"""Controller rejection and recovery tests with a deterministic fake worker.

These do not call Codex or attest product/hardware acceptance.
"""
from closure_fixtures import assessment, reason as closure_reason
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from dot_runtime.controller import (Controller, atomic_json, initialize, observed_test_count,
                                    operator_confirm, ordered_tasks, read_json, run_lock)


def fixture_plan():
    return {'schema_version': 1, 'description': 'Implement fixture function from requirements',
            'coding_standard': 'none', 'standard_scope_reason': 'Python-only test fixture',
            'allowed_paths': ['value.py'],
            'environment': {'python': '3.12'},
            'tasks': [{'id': 'build', 'instruction': 'Add value function', 'depends_on': []}],
            'verification': {'test_report_format': 'unittest', 'test_command': ['python', '-B', '-m', 'unittest'], 'test_instructions': 'Independently test value',
                             'review_instructions': 'Independently review value'},
            'budgets': {'implementation_attempts': 3, 'same_root_cause_attempts': 2,
                        'infrastructure_retries': 1, 'timeout_seconds': 15}}


def result(status='PASS', cause='needs_analysis', output='Ran 3 tests in 0.001s\n\nOK\n'):
    return {'status': status, 'summary': 'Fixture evidence', 'root_cause': cause, 'root_cause_key': 'fixture.value.defect', 'findings': [],
            'evidence': [{'kind': 'review', 'description': 'Inspected fixture', 'output': output,
                          'command': '', 'exit_code': 0, 'test_count': 3}],
            'runtime_evidence': {'command_events': [{'command': 'python -B -m unittest',
                                  'exit_code': 0, 'aggregated_output': output}]}}


class FakeAdapter:
    def __init__(self, overrides=None, hook=None, reused_session=False):
        self.overrides = list(overrides or [])
        self.calls = []
        self.hook = hook
        self.reused_session = reused_session

    def run(self, role, prompt, workspace, output_dir, timeout_seconds):
        self.calls.append(role)
        if role == 'implementation':
            (workspace / 'value.py').write_text(f'def value():\n    return {len(self.calls)}\n', encoding='utf-8')
        report = result()
        if self.overrides and self.overrides[0][0] == role:
            report = self.overrides.pop(0)[1]
        report = copy.deepcopy(report)
        report['session_id'] = 'reused' if self.reused_session else f'session-{len(self.calls)}'
        output_dir.mkdir(parents=True)
        atomic_json(output_dir / 'result.json', report)
        report['runtime_evidence'] = dict(report.get('runtime_evidence', {}), result_path=str(output_dir / 'result.json'))
        if self.hook:
            self.hook(role, workspace)
        return report


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'README.md').write_text('Fixture project', encoding='utf-8')
        self.requirements = self.root / 'requirements.md'
        self.requirements.write_text('Return the specified fixture value; no external actions.', encoding='utf-8')
        self.plan = self.root / 'plan.json'
        atomic_json(self.plan, fixture_plan())
        self.run_dir = self.root / 'run'
        initialize(self.project, self.requirements, self.plan, self.run_dir)

    def controller(self, adapter=None):
        def fake_check(command, workspace, output_dir, timeout_seconds):
            output_dir.mkdir(parents=True)
            log = output_dir / 'test.log'
            output = 'Ran 3 tests in 0.001s\nOK\n'
            log.write_text(output, encoding='utf-8')
            return {'status': 'PASS', 'command': command, 'exit_code': 0, 'output': output, 'log_path': str(log)}
        return Controller(self.run_dir, adapter or FakeAdapter(), check_runner=fake_check)

    def approved(self, adapter=None):
        c = self.controller(adapter)
        # Test-only callback; CLI deliberately has no noninteractive approval bypass.
        c.approve('requirements', lambda *_: True)
        c.approve('plan', lambda *_: True)
        return c

    def test_cannot_run_after_only_requirements_approval(self):
        c = self.controller()
        with self.assertRaises(ValueError):
            c.run()
        c.approve('requirements', lambda *_: True)
        with self.assertRaises(ValueError):
            c.run()
        self.assertEqual(c.adapter.calls, [])

    def test_success_waits_for_exact_report_review_and_never_delivers(self):
        c = self.approved()
        state = c.run()
        self.assertEqual(c.adapter.calls, ['implementation', 'test', 'review'])
        self.assertEqual(state['state'], 'REPORT_PENDING')
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        state = c.approve('report', lambda *_: True)
        self.assertEqual(state['state'], 'DELIVERY_READY')
        self.assertFalse(read_json(self.run_dir / 'final-report.json')['delivery_executed'])

    def test_changed_requirement_stops_before_any_worker(self):
        c = self.approved()
        self.requirements.write_text('Changed AC', encoding='utf-8')
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertFalse(c.adapter.calls)

    def test_native_report_review_records_distinct_human_authority(self):
        c = self.approved()
        c.run()
        state = c.approve('report', lambda *_: True, authority='local_harness_user')
        self.assertEqual(state['state'], 'DELIVERY_READY')
        self.assertEqual(state['approvals']['report']['authority'], 'local_harness_user')
        self.assertFalse(read_json(self.run_dir / 'final-report.json')['delivery_executed'])

    def test_native_decline_does_not_create_report_approval(self):
        c = self.approved()
        c.run()
        with self.assertRaisesRegex(ValueError, 'declined'):
            c.approve('report', lambda *_: False, authority='local_harness_user')
        self.assertNotIn('report', c.state['approvals'])
        self.assertEqual(c.state['state'], 'REPORT_PENDING')

    def test_rejected_report_can_be_revised_without_authorizing_a_retry(self):
        c = self.approved()
        c.run()
        self.assertEqual(c.reject('report', 'Missing required case', lambda *_: True)['state'], 'BLOCKED')
        with self.assertRaises(ValueError):
            c.run()
        c.revise(self.requirements, self.plan)
        self.assertEqual(c.state['state'], 'REVIEW_PENDING')
        self.assertEqual(c.state['implementation_attempts'], 1)

    def test_changed_input_during_human_approval_rejected(self):
        c = self.controller()
        def race(*_):
            self.requirements.write_text('Changed', encoding='utf-8')
            return True
        with self.assertRaises(ValueError):
            c.approve('requirements', race)

    def test_worker_cannot_self_approve_by_adding_plan_field(self):
        plan = read_json(self.plan)
        plan['approval'] = {'status': 'APPROVED'}
        atomic_json(self.plan, plan)
        with self.assertRaises(ValueError):
            self.controller().run()

    def test_report_tampering_rejected(self):
        c = self.approved()
        c.run()
        (self.run_dir / 'final-report.json').write_text('{"outcome":"SUCCESS"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            c.approve('report', lambda *_: True)

    def test_changed_candidate_invalidates_report_approval(self):
        c = self.approved()
        c.run()
        (self.project / 'value.py').write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            c.approve('report', lambda *_: True)

    def test_candidate_change_during_report_review_rejected(self):
        c = self.approved()
        c.run()
        def during_review(*_):
            (self.project / 'value.py').write_text('changed while user reads report', encoding='utf-8')
            return True
        with self.assertRaises(ValueError):
            c.approve('report', during_review)

    def test_evidence_tampering_invalidates_report_approval(self):
        c = self.approved()
        c.run()
        path = next((self.run_dir / 'sessions').rglob('result.json'))
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            c.approve('report', lambda *_: True)

    def test_l4_failure_goes_through_l2_then_new_candidate_and_both_checks(self):
        adapter = FakeAdapter([('test', result('FAIL', 'implementation')),
                               ('triage', result('PASS', 'implementation'))])
        c = self.approved(adapter)
        state = c.run()
        self.assertEqual(adapter.calls, ['implementation', 'test', 'triage', 'implementation', 'test', 'review'])
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        self.assertEqual(state['implementation_attempts'], 2)
        test_ids = [s['candidate_id'] for s in state['sessions'] if s['role'] == 'test']
        self.assertNotEqual(test_ids[0], test_ids[1])

    def test_successful_retry_completes_original_failed_task(self):
        plan = read_json(self.plan)
        plan['budgets']['implementation_attempts'] = 2
        atomic_json(self.plan, plan)
        adapter = FakeAdapter([('implementation', result('FAIL', 'implementation')), ('triage', result('PASS', 'implementation'))])
        c = self.approved(adapter)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(adapter.calls, ['implementation', 'triage', 'implementation', 'test', 'review'])
        self.assertEqual(c.state['completed_tasks'], ['build'])

    def test_exhaustion_requests_assessment_without_reset_after_reload(self):
        adapter = FakeAdapter([('test', result('FAIL', 'implementation')), ('triage', result('PASS', 'implementation')),
                               ('test', result('FAIL', 'implementation')), ('triage', result('PASS', 'implementation'))])
        c = self.approved(adapter)
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        c = self.controller()
        self.assertEqual(c.state['implementation_attempts'], 2)
        with self.assertRaises(ValueError):
            c.run()
        with self.assertRaises(ValueError):
            c.approve('report', lambda *_: True)
        c.stop_with_report('L1 evaluated alternatives', l1_assessment=assessment())
        self.assertEqual(c.approve('report', lambda *_: True)['state'], 'BLOCKED')
        c.revise(self.requirements, self.plan)
        self.assertEqual(c.state['implementation_attempts'], 2)

    def test_requirement_failure_routes_to_decision_not_implementation(self):
        adapter = FakeAdapter([('test', result('FAIL', 'product_requirement')), ('triage', result('PASS', 'product_requirement'))])
        c = self.approved(adapter)
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertEqual(adapter.calls, ['implementation', 'test', 'triage'])
        self.assertTrue((self.run_dir / 'decision-request.json').exists())

    def test_infrastructure_retest_preserves_candidate(self):
        adapter = FakeAdapter([('test', result('ERROR', 'test_infrastructure')), ('triage', result('PASS', 'test_infrastructure'))])
        c = self.approved(adapter)
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(adapter.calls, ['implementation', 'test', 'triage', 'test', 'review'])
        self.assertEqual(c.state['implementation_attempts'], 1)
        self.assertEqual(c.state['infrastructure_retries'], 1)

    def test_crashed_dispatch_is_unknown_and_not_replayed(self):
        c = self.approved()
        c.state['active_run'] = {'role': 'implementation', 'sequence': 1}
        c.state['implementation_attempts'] = 1
        c.save()
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertEqual(c.adapter.calls, ['triage'])
        self.assertEqual(c.state['active_run'], {'role': 'implementation', 'sequence': 1})
        with self.assertRaises(ValueError):
            c.revise(self.requirements, self.plan)
        c.reconcile(lambda *_: True)
        c.revise(self.requirements, self.plan)
        self.assertEqual(c.state['implementation_attempts'], 1)

    def test_unknown_worker_gets_readonly_triage_but_is_not_retried(self):
        c = self.approved(FakeAdapter([('implementation', result('UNKNOWN'))]))
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertTrue(c.state['active_run'])
        self.assertEqual(c.adapter.calls, ['implementation', 'triage'])

    def test_reused_session_cannot_pass_independence(self):
        c = self.approved(FakeAdapter(reused_session=True))
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])

    def test_verifier_modifying_candidate_is_rejected(self):
        def tamper(role, workspace):
            if role == 'test':
                (workspace / 'value.py').write_text('changed', encoding='utf-8')
        c = self.approved(FakeAdapter(hook=tamper))
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertNotIn('review', c.adapter.calls)

    def test_missing_real_tests_cannot_pass_with_claimed_count(self):
        c = self.approved(FakeAdapter([('test', result(output='0 tests; fine'))]))
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])

    def test_dependency_cycle_and_missing_dependency_rejected(self):
        for deps in (['build'], ['missing']):
            plan = fixture_plan()
            plan['tasks'][0]['depends_on'] = deps
            with self.assertRaises(ValueError):
                ordered_tasks(plan)

    def test_second_controller_cannot_hold_lock(self):
        with run_lock(self.run_dir):
            with self.assertRaises(ValueError):
                with run_lock(self.run_dir):
                    pass

    def test_state_inside_product_is_rejected(self):
        with self.assertRaises(ValueError):
            initialize(self.project, self.requirements, self.plan, self.project / 'run')

    def test_reinitialization_cannot_clear_budget(self):
        with self.assertRaises(ValueError):
            initialize(self.project, self.requirements, self.plan, self.run_dir)

    def test_zero_test_and_failed_runner_outputs_rejected(self):
        self.assertEqual(observed_test_count(result(output='Ran 0 tests in 0.0s\nOK\n'), 'unittest'), 0)
        self.assertEqual(observed_test_count(result(output='1 passed, 2 failed'), 'pytest'), 0)
        self.assertEqual(observed_test_count(result(output='100% tests passed, 0 tests failed out of 5'), 'ctest'), 5)
        self.assertEqual(observed_test_count(result(output='Ran 3 tests in 0.0s\nOK (skipped=3)\n'), 'unittest'), 0)

    def test_out_of_scope_implementation_is_rejected(self):
        def escape(role, workspace):
            if role == 'implementation':
                (workspace / 'unapproved.py').write_text('escape', encoding='utf-8')
        c = self.approved(FakeAdapter(hook=escape))
        self.assertEqual(c.run()['state'], 'DECISION_PENDING')
        self.assertIsNone(c.state['report'])
        self.assertNotIn('test', c.adapter.calls)

    def test_revision_binds_current_project_without_erasing_history(self):
        c = self.approved(FakeAdapter([('implementation', result('FAIL')), ('triage', result('PASS', 'product_requirement'))]))
        c.run()
        before = c.state['initial_snapshot']['id']
        c.revise(self.requirements, self.plan)
        self.assertNotEqual(before, c.state['initial_snapshot']['id'])
        self.assertEqual(c.state['implementation_attempts'], 1)

    def test_c_clause_failure_returns_through_dot_before_repair(self):
        plan = read_json(self.plan)
        plan['coding_standard'] = 'huawei-c'
        plan['verification']['doxygen'] = {'executable': 'doxygen', 'expected_version': '1.18.0',
                                           'include': ['.'], 'timeout_seconds': 10}
        atomic_json(self.plan, plan)
        adapter = FakeAdapter([('triage', result('PASS', 'implementation'))])
        c = self.approved(adapter)
        calls = []
        c.approved_documentation = lambda workspace: {'status': 'PASS', 'evidence': ['Synthetic documentation flow fixture']}
        def compliance_fixture(workspace):
            calls.append(workspace)
            errors = ['Doxygen contract missing'] if len(calls) == 1 else []
            c.state['compliance'] = {'status': 'INCOMPLETE' if errors else 'PASS', 'errors': errors}
            return errors
        c.c_compliance = compliance_fixture
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(adapter.calls, ['implementation', 'test', 'review', 'triage', 'implementation', 'test', 'review'])

    def compliance_review_fixture(self):
        """Synthetic child results exercise controller provenance binding only."""
        catalog = copy.deepcopy(read_json(ROOT / 'standards/embedded-c/v1.3/huawei/coverage.json'))
        parent = next(clause for clause in catalog['clauses'] if clause['id'] == 'HW-R-13.9')
        parent['subrequirements'] = [{
            'id': 'HW-R-13.9.SUB-91', 'source_page': 59, 'source_basis': 'exception',
            'project_level': 'MUST', 'requirement': 'Synthetic child requirement.',
            'applicability': 'Synthetic target.', 'verification': 'Synthetic independent review.',
            'source_note': 'Protocol test fixture, not product or PDF acceptance.',
        }]
        def row(clause):
            value = {'id': clause['id'], 'applicability': 'APPLICABLE',
                     'applicability_reason': 'Synthetic fixture scope.', 'result': 'PASS',
                     'verification_method': 'review', 'reason': 'Synthetic review only.'}
            if clause.get('subrequirements'):
                value['subrequirements'] = [row(child) for child in clause['subrequirements']]
            return value
        return catalog, {'clauses': [row(clause) for clause in catalog['clauses']]}

    def run_compliance_fixture(self, catalog, record, review_hook=None):
        report = result(output=json.dumps(record))
        catalog_path = self.root / 'synthetic-catalog.json'
        atomic_json(catalog_path, catalog)
        with patch('dot_runtime.controller.CATALOG', catalog_path):
            c = self.approved()
            c.run()
            c.adapter.overrides = [('review', report)]
            if review_hook:
                c.adapter.hook = lambda role, workspace: review_hook(c, catalog_path)
            errors = c.c_compliance(Path(c.state['candidate']['path']))
        return c, errors

    def test_compliance_children_receive_real_review_identity_and_evidence(self):
        catalog, record = self.compliance_review_fixture()
        c, errors = self.run_compliance_fixture(catalog, record)
        self.assertEqual(errors, [])
        parent = next(row for row in c.state['compliance']['record']['clauses'] if row['id'] == 'HW-R-13.9')
        child = parent['subrequirements'][0]
        self.assertEqual(child['verifier_session_id'], c.state['sessions'][-1]['result']['session_id'])
        self.assertEqual(child['evidence'], parent['evidence'])
        self.assertTrue((self.run_dir / child['evidence'][0]['path']).is_file())
        self.assertEqual(c.state['compliance']['record']['coverage_revision'], catalog['coverage_revision'])
        self.assertNotIn('verifier_session_id', record['clauses'][0])

    def test_compliance_controller_does_not_invent_missing_child_conclusions(self):
        catalog, record = self.compliance_review_fixture()
        parent = next(row for row in record['clauses'] if row['id'] == 'HW-R-13.9')
        del parent['subrequirements']
        c, errors = self.run_compliance_fixture(catalog, record)
        self.assertTrue(any('missing subrequirement' in error for error in errors))
        saved_parent = next(row for row in c.state['compliance']['record']['clauses'] if row['id'] == 'HW-R-13.9')
        self.assertNotIn('subrequirements', saved_parent)
        self.assertEqual(c.state['compliance']['status'], 'INCOMPLETE')

    def test_compliance_controller_preserves_unexecuted_child_result(self):
        catalog, record = self.compliance_review_fixture()
        parent = next(row for row in record['clauses'] if row['id'] == 'HW-R-13.9')
        parent['subrequirements'][0]['result'] = 'NOT_RUN'
        c, errors = self.run_compliance_fixture(catalog, record)
        self.assertTrue(any('SUB-91' in error and 'NOT_RUN' in error for error in errors))
        saved_parent = next(row for row in c.state['compliance']['record']['clauses'] if row['id'] == 'HW-R-13.9')
        self.assertEqual(saved_parent['subrequirements'][0]['result'], 'NOT_RUN')

    def test_compliance_controller_rejects_malformed_child_array(self):
        catalog, record = self.compliance_review_fixture()
        parent = next(row for row in record['clauses'] if row['id'] == 'HW-R-13.9')
        parent['subrequirements'] = {'id': 'HW-R-13.9.SUB-91', 'result': 'PASS'}
        c, errors = self.run_compliance_fixture(catalog, record)
        self.assertTrue(any('must be an array' in error for error in errors))
        self.assertEqual(c.state['compliance']['status'], 'INCOMPLETE')

    def test_compliance_catalog_change_during_review_is_rejected(self):
        catalog, record = self.compliance_review_fixture()
        def change_catalog(controller, path):
            modified = read_json(path)
            modified['clauses'][0]['project_requirement'] += ' Changed during review.'
            atomic_json(path, modified)
        c, errors = self.run_compliance_fixture(catalog, record, change_catalog)
        self.assertTrue(any('changed during' in error for error in errors))
        self.assertEqual(c.state['compliance']['status'], 'INCOMPLETE')

    def test_compliance_catalog_requires_approved_candidate_baseline_before_worker(self):
        catalog, record = self.compliance_review_fixture()
        c = self.approved()
        c.run()
        before = list(c.adapter.calls)
        catalog_path = self.root / 'unapproved-catalog.json'
        atomic_json(catalog_path, catalog)
        with patch('dot_runtime.controller.CATALOG', catalog_path):
            errors = c.c_compliance(Path(c.state['candidate']['path']))
        self.assertTrue(any('baseline' in error for error in errors))
        self.assertEqual(c.adapter.calls, before)

    def test_compliance_cannot_bind_another_sessions_report_as_current_evidence(self):
        catalog, record = self.compliance_review_fixture()
        report = result(output=json.dumps(record))
        catalog_path = self.root / 'synthetic-catalog.json'
        atomic_json(catalog_path, catalog)
        with patch('dot_runtime.controller.CATALOG', catalog_path):
            c = self.approved()
            c.run()
            old_result_path = c.state['sessions'][-1]['result']['runtime_evidence']['result_path']
            c.adapter.overrides = [('review', report)]
            real_worker = c.worker
            def altered_result(role, instruction, workspace):
                current = real_worker(role, instruction, workspace)
                current['runtime_evidence']['result_path'] = old_result_path
                return current
            c.worker = altered_result
            errors = c.c_compliance(Path(c.state['candidate']['path']))
        self.assertTrue(any('registered artifact' in error for error in errors))


if __name__ == '__main__':
    unittest.main()

"""Real local restoration commands; model results and approvals remain fixtures."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_runtime_controller as fixtures
from dot_runtime.controller import atomic_json, read_json
from dot_runtime.facilities import selected_actions, validate_settings
from dot_runtime.checks import run_check
from dot_runtime.resources import BudgetReview


class FacilityRecoveryTests(unittest.TestCase):
    setUp = fixtures.ControllerTests.setUp
    approved = fixtures.ControllerTests.approved

    def controller(self, adapter=None):
        c = fixtures.ControllerTests.controller(self, adapter)
        fixture_check = c.check_runner
        c.check_runner = lambda command, *args: (run_check(command, *args) if '-c' in command
                                                else fixture_check(command, *args))
        return c

    def approve_facility(self, script):
        plan = read_json(self.plan)
        plan['verification']['facility_recovery'] = {'actions': [{
            'id': 'restore-local-fixture', 'kind': 'command',
            'description': 'Prepare the approved local test facility file; no product or external operations',
            'command': [sys.executable, '-B', '-c', script]}]}
        atomic_json(self.plan, plan)

    def diagnosis(self, **updates):
        value = fixtures.result('PASS', 'test_infrastructure')
        value.update(fixtures.triage_fields('test_infrastructure'))
        value['facility_actions'] = ['restore-local-fixture']
        value.update(updates)
        return value

    def test_real_l4_restoration_runs_once_before_independent_reverification(self):
        self.approve_facility("from pathlib import Path; Path('ready.txt').write_text('ready', encoding='utf-8')")
        seen = []
        def inspect(role, workspace):
            if role == 'test':
                seen.append((self.run_dir / 'facility-workspace/ready.txt').exists())
        adapter = fixtures.FakeAdapter([('test', fixtures.result('ERROR', 'test_infrastructure')),
                                       ('triage', self.diagnosis())], hook=inspect)
        c = self.approved(adapter)
        state = c.run()
        self.assertEqual(seen, [False, True])
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        self.assertEqual(state['infrastructure_retries'], 1)
        self.assertEqual(state['implementation_attempts'], 1)
        record = read_json(self.run_dir / 'final-report.json')['facility_recoveries'][0]
        self.assertEqual(record['status'], 'PASS')
        self.assertEqual(record['commands'][0]['exit_code'], 0)
        self.assertEqual(record['solutions'], self.diagnosis()['solutions'])
        from dot_runtime.presentation import read_record
        log = Path(record['commands'][0]['log_path'])
        self.assertEqual(read_record(self.run_dir.parent, log.relative_to(self.run_dir.parent).as_posix(), '0')['sha256'],
                         record['commands'][0]['sha256'])
        Path(record['commands'][0]['log_path']).write_text('altered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Facility recovery evidence'):
            c.check_evidence()

    def test_unapproved_or_missing_restoration_stops_for_l1_without_retry(self):
        for selected in ([], ['restore-local-fixture']):
            with self.subTest(selected=selected):
                c = self.approved(fixtures.FakeAdapter([('triage', self.diagnosis(facility_actions=selected))]))
                disposition = c.triage(fixtures.result('ERROR'), self.project)
                self.assertEqual(disposition, 'stop')
                self.assertEqual(c.state['state'], 'DECISION_PENDING')
                self.assertEqual(c.state['infrastructure_retries'], 0)
                self.assertIsNone(c.state['report'])
                self.assertFalse((self.run_dir / 'facility-workspace').exists())
                # Restore the same test fixture's preapproval stage, not a production recovery path.
                c.state['state'] = 'REVIEW_PENDING'; c.state['approvals'] = {}; c.save()

    def test_failed_restoration_returns_to_l2_and_l1_without_automatic_final_report(self):
        self.approve_facility('raise SystemExit(7)')
        adapter = fixtures.FakeAdapter([('test', fixtures.result('ERROR', 'test_infrastructure')),
                                       ('triage', self.diagnosis())])
        c = self.approved(adapter)
        state = c.run()
        self.assertEqual(state['state'], 'DECISION_PENDING')
        self.assertIsNone(state['report'])
        self.assertEqual(state['facility_recoveries'][0]['commands'][0]['exit_code'], 7)
        self.assertEqual(state['infrastructure_retries'], 1)
        self.assertEqual(adapter.calls.count('triage'), 2)

    def test_restoration_cannot_modify_product_and_still_accept(self):
        self.approve_facility("from pathlib import Path; Path(" + repr(str(self.project / 'value.py')) + ").write_text('tampered')")
        adapter = fixtures.FakeAdapter([('test', fixtures.result('ERROR', 'test_infrastructure')),
                                       ('triage', self.diagnosis())])
        c = self.approved(adapter)
        state = c.run()
        self.assertEqual(state['state'], 'DECISION_PENDING')
        self.assertIsNone(state['report'])
        self.assertEqual(adapter.calls.count('test'), 1)

    def test_automatic_repair_gets_full_l2_solution_and_original_failure(self):
        diagnosis = fixtures.result('PASS', 'implementation')
        diagnosis.update(fixtures.triage_fields('implementation'))
        diagnosis['solutions'][0].update(description='Preserve the signed range while correcting the boundary',
            verification_steps=['Exercise the exact minimum and maximum'], required_resources=['One L3 dispatch'])
        c = self.approved(fixtures.FakeAdapter([('triage', diagnosis)]))
        failure = fixtures.result('FAIL', 'implementation')
        failure['summary'] = 'Original boundary evidence'
        self.assertEqual(c.triage(failure, self.project), 'repair')
        assignment = c.state['pending_repair']
        for text in ('Preserve the signed range', 'Exercise the exact minimum', 'One L3 dispatch',
                     'permission_gaps', 'Original boundary evidence', 'value.py'):
            self.assertIn(text, assignment)

    def test_invalid_facility_contract_and_forged_action_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Malformed facility'):
            validate_settings({'actions': [{'id': 'x', 'kind': 'command', 'description': 'x', 'shell': 'arbitrary'}]})
        with self.assertRaisesRegex(ValueError, 'explicit approved'):
            selected_actions(fixtures.fixture_plan(), {'facility_actions': [{}]})
        with self.assertRaisesRegex(ValueError, 'not in the approved plan'):
            selected_actions(fixtures.fixture_plan(), {'facility_actions': ['new-action']})

    def test_time_refusal_before_restoration_does_not_create_unknown_or_consume_retry(self):
        c = self.approved()
        c.state['pending_facility_analysis'] = fixtures.triage_fields('test_infrastructure')
        c.state['pending_facility_retry'] = True
        c.save()
        with patch.object(c, 'operation_timeout', side_effect=BudgetReview('wall_time_seconds', 15, 1, 15, 15)):
            with self.assertRaises(BudgetReview):
                c.restore_facilities()
        self.assertIsNone(c.state['active_run'])
        self.assertTrue(c.state['pending_facility_retry'])
        self.assertEqual(c.state['infrastructure_retries'], 0)
        self.assertFalse(c.state.get('facility_recoveries'))
        self.assertFalse((self.run_dir / 'facility-workspace').exists())

    def test_one_second_allocation_can_dispatch_without_rounding_to_zero(self):
        self.approve_facility("print('local fixture ready')")
        c = self.approved()
        c.state['pending_facility_analysis'] = self.diagnosis()
        c.state['pending_facility_retry'] = True
        c.save()
        captured = []
        original = c.check_runner
        c.check_runner = lambda command, workspace, output, timeout: (
            captured.append(timeout) or original(command, workspace, output, timeout))
        with patch.object(c, 'operation_timeout', return_value=1), patch('time.monotonic', side_effect=[100, 100.01]):
            self.assertTrue(c.restore_facilities())
        self.assertEqual(captured, [1])
        self.assertEqual(c.state['infrastructure_retries'], 1)


if __name__ == '__main__':
    unittest.main()

"""Native stopping records intent without clearing uncertainty or dispatching work."""
import copy
import json
import unittest
from unittest.mock import patch

from dot_runtime import user_stop
from dot_runtime.bridge import Bridge, TOOLS
from dot_runtime.controller import Controller, atomic_json, read_json
from dot_runtime.onboarding import dispatch
import test_operation_recovery as recovery_tests


class UserStopTests(unittest.TestCase):
    def setUp(self):
        self.f = recovery_tests.OperationRecoveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.task, self.root, self.run = self.f.task, self.f.root, self.f.run
        self.session = self.f.session
        self.settings = read_json(self.task / 'bridge.json')
        state = read_json(self.run / 'state.json')
        state['clarification_root'] = str(self.root)  # Native Bridge initialization always binds this.
        atomic_json(self.run / 'state.json', state)

    def call(self, action, **kwargs):
        return dispatch({'action': action, 'task': str(self.task), 'session': self.session, **kwargs})

    def confirm(self):
        preview = self.call('stop_preview')
        return self.call('stop_confirm', sha256=preview['sha256'], decision='stop')

    def assessment(self):
        return {'summary': 'Native user explicitly requested stop; unresolved operation retained',
                'solutions_considered': ['Stop further dispatch and preserve evidence for review'],
                'authority_review': 'Original user chose stop in the native dialog',
                'resource_review': 'All accrued use retained without another API call',
                'termination_basis': 'user_requested_stop', 'evidence': ['user-stop.json']}

    def test_preview_pauses_before_asking_without_mutating_state_or_marker(self):
        before = (self.run / 'state.json').read_bytes()
        marker = (self.task / 'interrupted.json').read_bytes()
        preview = self.call('stop_preview')
        self.assertTrue(preview['dispatch_paused'])
        self.assertFalse((self.root / 'user-stop.json').exists())
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.assertEqual(marker, (self.task / 'interrupted.json').read_bytes())
        self.assertEqual(Bridge(self.settings).status()['state'], 'USER_STOP_PENDING')

    def test_confirm_creates_bound_native_receipt_only_and_keeps_every_guard(self):
        before = (self.run / 'state.json').read_bytes()
        marker = (self.task / 'interrupted.json').read_bytes()
        result = self.confirm()
        self.assertFalse(result['finalized'])
        record = read_json(self.root / 'user-stop.json')
        self.assertEqual(user_stop.validate_receipt(self.root, record), record)
        self.assertEqual(record['session'], self.session)
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.assertEqual(marker, (self.task / 'interrupted.json').read_bytes())
        self.assertFalse((self.run / 'final-report.json').exists())
        self.assertTrue(self.call('stop_preview')['already_confirmed'])
        self.assertEqual((self.f.project / 'main.txt').read_text(), 'original product')

    def test_explicit_continue_clears_only_unconfirmed_stop_pause(self):
        before = (self.run / 'state.json').read_bytes()
        marker = (self.task / 'interrupted.json').read_bytes()
        preview = self.call('stop_preview')
        result = self.call('stop_confirm', sha256=preview['sha256'], decision='continue')
        self.assertEqual(result['status'], 'USER_STOP_NOT_REQUESTED')
        self.assertIsNone(user_stop.status(self.root))
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.assertEqual(marker, (self.task / 'interrupted.json').read_bytes())
        self.assertEqual(len(list((self.root / 'user-stop-decisions').glob('*.json'))), 1)

    def test_task_change_rejects_confirmation_and_keeps_pause(self):
        preview = self.call('stop_preview')
        state = read_json(self.run / 'state.json')
        state['implementation_attempts'] += 1
        atomic_json(self.run / 'state.json', state)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.call('stop_confirm', sha256=preview['sha256'], decision='stop')
        self.assertTrue(user_stop.status(self.root)['dispatch_paused'])
        self.assertFalse((self.root / 'user-stop.json').exists())

    def test_original_native_session_required_and_no_model_approval_fields(self):
        self.session = 'other-session'
        with self.assertRaisesRegex(ValueError, 'original native session'):
            self.call('stop_preview')
        self.session = self.f.session
        with self.assertRaisesRegex(ValueError, 'Invalid private'):
            self.call('stop_preview', approved=True)

    def test_pending_or_confirmed_stop_blocks_model_work_before_client_creation(self):
        for confirmed in (False, True):
            self.confirm() if confirmed else self.call('stop_preview')
            bridge = Bridge(self.settings)
            with patch.object(bridge, 'client', side_effect=AssertionError('No API client while stopped')):
                for name in ('dot_plan', 'dot_run', 'dot_investigate', 'l1_resume_decision',
                             'l1_revise', 'l1_adjust_limits', 'l1_approve'):
                    args = {key: 'fixture' for key in TOOLS[name][1]}
                    with self.subTest(confirmed=confirmed, tool=name):
                        result = bridge.call(name, args)
                        self.assertFalse(result['accepted'])
                        self.assertIn('USER_STOP', result['reason'])

    def test_controller_entry_cannot_bypass_native_pause(self):
        self.call('stop_preview')
        controller = Controller(self.run)
        before = copy.deepcopy(controller.state)
        for action in (controller.run, lambda: controller.investigate('{}'),
                       lambda: controller.resume_decision('{}'),
                       lambda: controller.worker('triage', 'do not run', self.f.project)):
            with self.assertRaisesRegex(ValueError, 'USER_STOP'):
                action()
        self.assertEqual(before, read_json(self.run / 'state.json'))

    def test_native_stop_then_l1_assessment_finalizes_without_replaying_unknown(self):
        self.confirm()
        marker = (self.task / 'interrupted.json').read_bytes()
        result = self.call('bridge', name='l1_stop', arguments={'reason': json.dumps(self.assessment())})
        self.assertEqual(result['state'], 'REPORT_PENDING')
        report = read_json(self.run / 'final-report.json')
        self.assertEqual(report['l1_closure_assessment']['assessment']['termination_basis'], 'user_requested_stop')
        self.assertEqual(read_json(self.run / 'state.json')['implementation_attempts'], 1)
        self.assertTrue(read_json(self.run / 'state.json')['active_run'])
        self.assertEqual(marker, (self.task / 'interrupted.json').read_bytes())

    def test_pending_dialog_is_not_a_user_stop_authorization(self):
        self.call('stop_preview')
        result = Bridge(self.settings).call('l1_stop', {'reason': json.dumps(self.assessment())})
        self.assertFalse(result['accepted'])
        self.assertFalse((self.run / 'final-report.json').exists())

    def test_receipt_cannot_be_rebound_to_other_task_or_session(self):
        self.confirm()
        record = read_json(self.root / 'user-stop.json')
        record['task_binding_sha256'] = 'f' * 64
        atomic_json(self.root / 'user-stop.json', record)
        with self.assertRaisesRegex(ValueError, 'not bound'):
            user_stop.validate_receipt(self.root, record)

    def test_approved_local_transaction_blocks_new_stop_writes_without_deadlock(self):
        for name in ('operation-recovery-pending.json', 'initialization-recovery-pending.json', 'runtime-upgrade-pending.json'):
            atomic_json(self.task / name, {'fixture': 'approved local write transaction'})
            with self.assertRaisesRegex(ValueError, 'finish that exact local transaction'):
                self.call('stop_preview')
            self.assertFalse((self.root / 'user-stop-pending.json').exists())
            self.assertFalse((self.root / 'user-stop.json').exists())
            (self.task / name).unlink()

    def test_api_ledger_and_initial_review_changes_make_native_preview_stale(self):
        for name in ('api-review.json', 'api/ledger.json', 'requirements-review/state.json'):
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            preview = self.call('stop_preview')
            atomic_json(path, {'fixture': 'changed while native dialog was open'})
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.call('stop_confirm', sha256=preview['sha256'], decision='stop')
            path.unlink()


if __name__ == '__main__':
    unittest.main()

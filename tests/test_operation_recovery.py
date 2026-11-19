"""Recovery transactions use isolated records; no product, API or Codex calls."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dot_runtime import operation_recovery as r
from dot_runtime.controller import atomic_json, digest, file_hash, initialize, read_json, run_lock
from dot_runtime.onboarding import fingerprint
from test_delegated_l1 import delegation
from test_runtime_controller import fixture_plan


class OperationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.task = self.base / 'task'; self.task.mkdir()
        self.project = self.base / 'product'; self.project.mkdir()
        (self.project / 'main.txt').write_text('original product')
        self.root = self.task / 'control'; self.root.mkdir()
        self.run = self.root / 'run'
        self.session = 'native-recovery-fixture'
        (self.task / 'raw-input.md').write_text('User original request')
        original = delegation(self.task / 'raw-input.md')
        settings = {'project': str(self.project), 'control_dir': str(self.root),
                    'raw_input': str(self.task / 'raw-input.md'), 'delegation': str(self.task / 'delegation.json'),
                    'model': 'fixture', 'codex': 'codex', 'environment_context': '{}'}
        draft = {'fixture': True}
        for name, value in [('bridge', settings), ('draft', draft), ('delegation', original)]:
            atomic_json(self.task / (name + '.json'), value)
        self.receipt = {'authority': 'local_harness_user', 'session': self.session,
                        'bridge_sha256': digest(settings), 'draft_sha256': digest(draft),
                        'delegation_sha256': digest(original), 'baseline': fingerprint(self.project)}
        atomic_json(self.task / 'confirmation.json', self.receipt)
        (self.root / 'requirements.md').write_text('Fixture requirements')
        atomic_json(self.root / 'plan.json', fixture_plan())
        initialize(self.project, self.root / 'requirements.md', self.root / 'plan.json', self.run)
        state = read_json(self.run / 'state.json')
        state.update(state='DECISION_PENDING', implementation_attempts=1, completed_tasks=['build'],
                     active_run={'role': 'implementation', 'output': str(self.run / 'sessions/one')},
                     approvals={'requirements': {'fixture': True}}, candidate={'id': 'retained-candidate'})
        atomic_json(self.run / 'state.json', state)
        atomic_json(self.task / 'interrupted.json', {'name': 'dot_run', 'status': 'UNKNOWN', 'at': 'old-marker'})
        self.evidence = self.run / 'operator-observations.json'
        atomic_json(self.evidence, {'observed': 'Local process-tree and workspace review evidence fixture'})
        self.proposal = {'reason': 'Resume after checked local interruption',
                         'analysis': 'Read the original operation record and independently reconcile effects',
                         'resume_plan': 'Investigate preserved candidate, independently review and revalidate',
                         'side_effects': 'Operator must verify the cited observations before confirming',
                         'evidence': [{'path': 'run/operator-observations.json', 'sha256': file_hash(self.evidence),
                                       'description': 'Actual locally recorded operator observations (fixture)'}]}
        self.probe = patch.object(r, 'process_status', return_value={'status': 'STOPPED', 'checks': [{'method': 'fixture only'}]})
        self.probe.start(); self.addCleanup(self.probe.stop)

    def prepare(self):
        return r.prepare(self.task, self.receipt, self.session, self.proposal)

    def request(self, action, **kwargs):
        with run_lock(self.task):
            return r.dispatch(self.task, self.receipt, {'action': action, 'session': self.session, **kwargs})

    def commit(self, preview=None):
        preview = preview or self.request('operation_preview')
        return self.request('operation_commit', sha256=preview['sha256'])

    def test_prepare_and_preview_never_clear_guard_or_replay(self):
        before = read_json(self.run / 'state.json')
        marker = (self.task / 'interrupted.json').read_bytes()
        self.prepare()
        files = r._files(self.task)
        preview = self.request('operation_preview')
        self.assertTrue(preview['ready'])
        self.assertEqual(files, r._files(self.task))
        self.assertEqual(marker, (self.task / 'interrupted.json').read_bytes())
        self.assertEqual(before, read_json(self.run / 'state.json'))

    def test_native_commit_retains_originals_completed_work_and_counters(self):
        self.prepare()
        before = (self.run / 'state.json').read_bytes()
        confirmation = (self.task / 'confirmation.json').read_bytes()
        result = self.commit()
        self.assertEqual(result['status'], 'OPERATION_RECONCILED')
        self.assertFalse(result['operation_replayed'])
        state = read_json(self.run / 'state.json')
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertEqual(state['completed_tasks'], ['build'])
        self.assertEqual(state['candidate'], {'id': 'retained-candidate'})
        self.assertEqual(state['state'], 'DECISION_PENDING')
        self.assertIsNone(state['active_run'])
        self.assertEqual(state['approvals'], {})
        self.assertEqual(confirmation, (self.task / 'confirmation.json').read_bytes())
        archive = self.task / 'operation-recovery-archives' / result['record']['sha256']
        self.assertEqual(before, (archive / 'control/run/state.json').read_bytes())
        self.assertTrue((archive / 'interrupted.json').exists())
        self.assertFalse((self.task / 'interrupted.json').exists())
        self.assertEqual((self.project / 'main.txt').read_text(), 'original product')

    def test_live_or_unqueryable_process_is_not_overridden_by_approval(self):
        self.prepare()
        for status in ('ACTIVE', 'UNKNOWN'):
            with self.subTest(status=status), patch.object(r, 'process_status', return_value={'status': status}):
                preview = self.request('operation_preview')
                self.assertFalse(preview['ready'])
                with self.assertRaisesRegex(ValueError, 'cannot override'):
                    self.commit(preview)
                self.assertTrue((self.task / 'interrupted.json').exists())

    def test_legacy_negative_scan_still_records_native_attestation_requirement(self):
        self.prepare()
        probe = {'status': 'REQUIRES_NATIVE_ATTESTATION', 'checks': [],
                 'independent_scan': {'status': 'NO_RELEVANT_PROCESS', 'inspected_count': 20}}
        with patch.object(r, 'process_status', return_value=probe):
            result = self.commit()
        self.assertEqual(result['record']['processes']['status'], 'REQUIRES_NATIVE_ATTESTATION')
        self.assertEqual(result['record']['authority'], 'local_harness_user')

    def test_unknown_api_is_retained_while_local_host_recovers_without_deadlock(self):
        self.prepare()
        api = self.root / 'api'; api.mkdir()
        atomic_json(api / 'ledger.json', {'attempts': [{'status': 'RESERVED', 'id': 'request'}]})
        before = (api / 'ledger.json').read_bytes()
        result = self.commit()
        self.assertEqual(before, (api / 'ledger.json').read_bytes())
        self.assertEqual(result['record']['api_review']['state'], 'API_REVIEW_PENDING')
        self.assertIn('l1_review_api', result['next'])
        self.assertTrue(read_json(self.run / 'decision-request.json')['unresolved_api_retained'])
        self.assertFalse(result['product_execution_started'])

    def test_final_reports_cannot_be_cleared(self):
        self.prepare()
        atomic_json(self.run / 'final-report.json', {'outcome': 'FAILED'})
        with self.assertRaisesRegex(ValueError, 'final report'):
            self.request('operation_preview')

    def test_stale_product_ledger_or_evidence_rejected_during_confirmation(self):
        for target in ('product', 'ledger', 'evidence'):
            with self.subTest(target=target):
                self.prepare()
                p = self.request('operation_preview')
                if target == 'product':
                    path = self.project / 'main.txt'
                elif target == 'ledger':
                    path = self.root / 'ordinary-new-record.json'
                else:
                    path = self.evidence
                before = path.read_bytes() if path.exists() else None
                path.write_text('changed')
                with self.assertRaises(ValueError):
                    self.commit(p)
                self.assertTrue((self.task / 'interrupted.json').exists())
                if before is None: path.unlink()
                else: path.write_bytes(before)

    def test_proposal_cannot_inject_pid_approval_or_escape_evidence_root(self):
        for field in ('approved', 'all_processes_stopped', 'pid'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                r.prepare(self.task, self.receipt, self.session, {**self.proposal, field: True})
        self.proposal['evidence'][0]['path'] = '../confirmation.json'
        with self.assertRaisesRegex(ValueError, 'Invalid recovery path'):
            self.prepare()

    def test_wrong_session_cannot_stage_or_commit(self):
        with self.assertRaisesRegex(ValueError, 'original native session'):
            r.prepare(self.task, self.receipt, 'wrong', self.proposal)
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'original native session'):
            r.dispatch(self.task, self.receipt, {'action': 'operation_preview', 'session': 'wrong'})

    def test_active_controller_lock_prevents_recovery(self):
        self.prepare()
        with run_lock(self.run), self.assertRaisesRegex(ValueError, 'Another controller'):
            self.request('operation_preview')

    def test_partial_commit_can_finish_once_without_resetting_usage(self):
        self.prepare()
        real = r.atomic_json
        def fail(path, value):
            if Path(path).name == 'state.json':
                raise OSError('fixture write interrupted')
            real(path, value)
        with patch.object(r, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            self.commit()
        self.assertTrue((self.task / r.PENDING).exists())
        pending = self.request('operation_preview')
        self.assertTrue(pending['finish_required'])
        result = self.request('operation_finish')
        self.assertEqual(result['status'], 'OPERATION_RECONCILED')
        self.assertEqual(read_json(self.run / 'state.json')['implementation_attempts'], 1)
        self.assertEqual(len(read_json(self.task / r.JOURNAL)['records']), 1)
        self.assertEqual(self.request('operation_finish')['status'], 'NO_PENDING_OPERATION_RECOVERY')

    def test_pending_transaction_blocks_all_bridge_mutations_but_allows_reads_and_native_finish(self):
        from dot_runtime.bridge import Bridge, TOOLS
        from dot_runtime.onboarding import dispatch
        self.prepare()
        real = r.atomic_json
        def fail(path, value):
            if Path(path).name == 'state.json':
                raise OSError('fixture recovery interrupted before state replacement')
            real(path, value)
        with patch.object(r, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            self.commit()
        before = r._files(self.task)
        bridge = Bridge(read_json(self.task / 'bridge.json'))
        # Includes report submission, L1 closure and resource/baseline mutations.
        for name, (_, fields) in TOOLS.items():
            if name in ('dot_status', 'dot_read_record'):
                continue
            with self.subTest(tool=name), self.assertRaisesRegex(ValueError, 'OPERATION_RECOVERY_PENDING'):
                bridge.call(name, {field: 'fixture' for field in fields})
            self.assertEqual(before, r._files(self.task))
        status = bridge.call('dot_status', {})
        self.assertEqual(status['state'], 'OPERATION_RECOVERY_PENDING')
        self.assertIn('workflow_recover_operation', status['next'])
        bridge.call('dot_read_record', {'path': 'operation-recovery-proposal.json', 'offset': '0'})
        self.assertEqual(before, r._files(self.task))
        self.assertFalse((self.root / 'settings-binding.json').exists())
        request = {'task': str(self.task), 'session': self.session}
        self.assertTrue(dispatch({**request, 'action': 'operation_preview'})['finish_required'])
        self.assertEqual(dispatch({**request, 'action': 'operation_finish'})['status'], 'OPERATION_RECONCILED')
        self.assertEqual(read_json(self.run / 'state.json')['implementation_attempts'], 1)

    def test_pending_transaction_blocks_direct_controller_mutations_before_artifact_writes(self):
        from dot_runtime.controller import Controller
        controller = Controller(self.run)
        atomic_json(self.task / r.PENDING, {'fixture': 'approved transaction in progress'})
        before, memory = r._files(self.task), copy.deepcopy(controller.state)
        calls = {
            'save': controller.save,
            'transition': lambda: controller.transition('EXECUTING', 'fixture'),
            'check_approvals': controller.check_approvals,
            'approve_requirements': lambda: controller.approve('requirements'),
            'approve_report': lambda: controller.approve('report'),
            'reject': lambda: controller.reject('report', 'fixture'),
            'revise': lambda: controller.revise(self.root / 'requirements.md', self.root / 'plan.json'),
            'reconcile': controller.reconcile,
            'approved_test': lambda: controller.approved_test(self.project),
            'approved_documentation': lambda: controller.approved_documentation(self.project),
            'worker': lambda: controller.worker('triage', 'fixture', self.project),
            'request_l1_assessment': lambda: controller.request_l1_assessment('fixture'),
            'report': lambda: controller.report('SUCCESS', 'fixture'),
            'stop_with_report': lambda: controller.stop_with_report('fixture'),
            'triage': lambda: controller.triage({'status': 'UNKNOWN'}, self.project),
            'investigate': lambda: controller.investigate('{}'),
            'resume_decision': lambda: controller.resume_decision('{}'),
            'make_candidate': controller.make_candidate,
            'c_compliance': lambda: controller.c_compliance(self.project),
            'run': controller.run,
            'initialize': lambda: initialize(self.project, self.root / 'requirements.md', self.root / 'plan.json', self.run),
        }
        for name, call in calls.items():
            with self.subTest(entry=name), self.assertRaisesRegex(ValueError, 'OPERATION_RECOVERY_PENDING'):
                call()
            self.assertEqual(before, r._files(self.task))
            self.assertEqual(memory, controller.state)

    def test_interrupted_transaction_rejects_new_product_changes(self):
        self.prepare()
        real = r.atomic_json
        def fail(path, value):
            if Path(path).name == r.JOURNAL:
                raise OSError('fixture interrupted before final write')
            real(path, value)
        with patch.object(r, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            self.commit()
        (self.project / 'main.txt').write_text('unexpected write')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            self.request('operation_finish')
        self.assertTrue((self.task / r.PENDING).exists())

    def test_usage_clock_and_irrelevant_process_count_do_not_stale_approval(self):
        self.prepare()
        probe = {'status': 'REQUIRES_NATIVE_ATTESTATION', 'independent_scan': {'status': 'NO_RELEVANT_PROCESS', 'inspected_count': 5}}
        with patch.object(r, 'process_status', return_value=probe):
            p = self.request('operation_preview')
        probe['independent_scan']['inspected_count'] = 7
        with patch.object(r, 'process_status', return_value=probe):
            self.assertEqual(self.commit(p)['status'], 'OPERATION_RECONCILED')

    def test_preflight_requirements_review_recovers_in_same_transaction(self):
        (self.run / 'state.json').unlink()
        from dot_runtime import requirements_review
        active = {'role': 'l1_independent_review', 'output': str(self.root / 'review-output'),
                  'operation_id': 'original-review', 'review_number': 1}
        value = {'schema_version': 1, 'state': 'REQUIREMENTS_PENDING', 'active_run': active,
                 'approval': None, 'reviews': [{'status': 'UNKNOWN', 'operation_id': 'original-review'}],
                 'preparation_revisions': 2, 'started_at': 1234}
        value['sha256'] = digest(value)
        path = self.root / requirements_review.STATE
        path.parent.mkdir()
        atomic_json(path, value)
        atomic_json(self.task / 'interrupted.json', {'name': 'l1_approve', 'status': 'UNKNOWN'})
        self.prepare()
        result = self.commit()
        updated = read_json(path)
        self.assertEqual(updated['state'], 'REQUIREMENTS_PENDING')
        self.assertIsNone(updated['active_run'])
        self.assertEqual(updated['reviews'], value['reviews'])
        self.assertEqual(updated['preparation_revisions'], 2)
        self.assertIsNone(updated['approval'])
        self.assertEqual(result['record']['binding']['preflight_requirements_review'], active)
        self.assertTrue(updated['recoveries'][0]['native_record']['all_processes_stopped'])

    def test_process_changes_during_native_confirmation_are_rejected(self):
        self.prepare()
        preview = self.request('operation_preview')
        with patch.object(r, 'process_status', return_value={'status': 'ACTIVE'}):
            with self.assertRaisesRegex(ValueError, 'changed during native'):
                self.commit(preview)
        self.assertTrue((self.task / 'interrupted.json').exists())

    def test_preinit_changed_baseline_is_explicitly_adopted_and_requirements_can_be_reviewed(self):
        from dot_runtime import onboarding, requirements_review, initialization_recovery
        from dot_runtime.bridge import Bridge
        from test_runtime_controller import FakeAdapter
        (self.run / 'state.json').unlink()
        original_confirmation = (self.task / 'confirmation.json').read_bytes()
        (self.project / 'main.txt').write_text('Observed external change before initialization')
        atomic_json(self.task / 'interrupted.json', {'name': 'l1_prepare_requirements', 'status': 'UNKNOWN'})
        self.prepare()
        preview = self.request('operation_preview')
        self.assertTrue(preview['baseline_adoption']['applies_before_initialization'])
        self.assertTrue(preview['baseline_adoption']['changed'])
        self.assertEqual(preview['baseline_adoption']['before'], self.receipt['baseline'])
        current = fingerprint(self.project)
        self.assertEqual(preview['baseline_adoption']['after'], current)
        self.commit(preview)
        self.assertEqual(initialization_recovery.effective_baseline(self.task, self.receipt), current)
        self.assertEqual(original_confirmation, (self.task / 'confirmation.json').read_bytes())
        self.assertEqual(read_json(self.root / 'planning-status.json')['status'], 'NEEDS_INFO')
        worker = FakeAdapter()
        with patch.object(onboarding, 'Bridge', side_effect=lambda settings: Bridge(settings, workers=worker)):
            staged = onboarding.dispatch({'action': 'bridge', 'task': str(self.task), 'session': self.session,
                'name': 'l1_prepare_requirements', 'arguments': {'requirements': 'Review the user-adopted baseline; retain original goals and all independent tests.'}})
            self.assertNotEqual(staged.get('accepted'), False)
            approved = onboarding.dispatch({'action': 'bridge', 'task': str(self.task), 'session': self.session,
                'name': 'l1_approve', 'arguments': {'kind': 'requirements'}})
        self.assertNotEqual(approved.get('accepted'), False, approved.get('reason'))
        self.assertEqual(requirements_review.status(self.root)['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(worker.calls, ['review'])
        self.assertFalse((self.run / 'state.json').exists())

    def test_later_initialization_recovery_supersedes_operation_adoption_without_clock_order(self):
        from dot_runtime import initialization_recovery as initial
        (self.run / 'state.json').unlink()
        (self.project / 'main.txt').write_text('First baseline adopted through operation recovery')
        self.prepare(); self.commit()
        self.assertEqual(initial.effective_baseline(self.task, self.receipt), fingerprint(self.project))
        (self.project / 'main.txt').write_text('Later baseline adopted through initialization recovery')
        baseline = fingerprint(self.project)
        entry = {'sequence': 1, 'previous_sha256': None,
                 'original_confirmation_sha256': file_hash(self.task / 'confirmation.json'),
                 'session': self.session, 'authority': 'local_harness_user',
                 'at': '2000-01-01T00:00:00+00:00', 'baseline_after': baseline}
        entry['sha256'] = digest(entry)
        atomic_json(self.task / initial.JOURNAL, {'schema_version': 1, 'records': [entry]})
        self.assertEqual(initial.effective_baseline(self.task, self.receipt), baseline)
        atomic_json(self.task / 'interrupted.json', {'name': 'l1_prepare_requirements', 'status': 'UNKNOWN'})
        (self.project / 'main.txt').write_text('Subsequent operation recovery after initialization recovery')
        self.prepare()
        preview = self.request('operation_preview')
        self.assertEqual(preview['baseline_adoption']['initialization_recovery_sha256'], entry['sha256'])
        self.assertEqual(preview['baseline_adoption']['before'], baseline)
        self.commit(preview)
        self.assertEqual(initial.effective_baseline(self.task, self.receipt), fingerprint(self.project))

    def test_initialized_operation_recovery_does_not_adopt_a_new_startup_baseline(self):
        from dot_runtime.initialization_recovery import effective_baseline
        (self.project / 'main.txt').write_text('Existing initialized product change requiring normal review')
        self.prepare()
        preview = self.request('operation_preview')
        self.assertFalse(preview['baseline_adoption']['applies_before_initialization'])
        self.assertFalse(preview['baseline_adoption']['changed'])
        self.commit(preview)
        self.assertEqual(effective_baseline(self.task, self.receipt), self.receipt['baseline'])


class ProcessProbeTests(unittest.TestCase):
    def test_real_self_process_is_active(self):
        self.assertEqual(r.pid_status(os.getpid()), 'ACTIVE')

    def test_missing_boot_identity_does_not_hide_a_known_live_pid(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = Path(temporary)
            marker = {'name': 'dot_run', 'status': 'UNKNOWN', 'process': {'pid': os.getpid(), 'boot_id': None}}
            result = r.process_status(task, {'project': temporary}, None, marker)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertIsNone(result['independent_scan'])

    def test_runtime_process_record_does_not_claim_tree_stop_from_parent_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = r.record_process_start(temporary, os.getpid())
            self.assertEqual(record['process_tree_status'], 'UNKNOWN')
            end = r.record_process_end(temporary, os.getpid())
            self.assertEqual(end['status'], 'EXITED')
            self.assertEqual(end['process_tree_status'], 'UNKNOWN')
            with self.assertRaisesRegex(ValueError, 'must be fresh'):
                r.record_process_start(temporary, os.getpid())

    def test_scan_excludes_own_ancestors_but_keeps_other_workers_and_children(self):
        rows = [{'pid': os.getpid(), 'ppid': 41, 'name': 'python', 'command': 'harness_workflow.py'},
                {'pid': 41, 'ppid': 1, 'name': 'codex', 'command': 'codex own host'},
                {'pid': 42, 'ppid': 1, 'name': 'codex', 'command': 'codex exec --cd C:/product'},
                {'pid': 43, 'ppid': 42, 'name': 'compiler', 'command': 'compiler detached arguments'}]
        with patch.object(r, 'process_inventory', return_value=rows):
            result = r.independent_process_check(Path('C:/task'), {'project': 'C:/product', 'codex': 'codex'})
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertEqual({x['pid'] for x in result['matches']}, {42, 43})

    def test_scan_failure_preserves_actionable_gap(self):
        with patch.object(r, 'process_inventory', side_effect=ValueError('PID 123 permission denied')):
            result = r.independent_process_check(Path('C:/task'), {'project': 'C:/product'})
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('PID 123', result['reason'])

    def test_real_native_inventory_or_explicit_permission_gap(self):
        try:
            rows = r.process_inventory()
        except ValueError as exc:
            self.assertTrue(str(exc))
            return
        self.assertTrue(any(row['pid'] == os.getpid() for row in rows))


if __name__ == '__main__':
    unittest.main()

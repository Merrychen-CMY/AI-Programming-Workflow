"""Real local state migration tests; fake Codex results are not product acceptance."""
import copy
import json
import unittest
from unittest.mock import patch

import test_onboarding as onboarding_fixtures
import test_runtime_controller as controller_fixtures
from test_runtime_controller import fixture_plan, FakeAdapter
from dot_runtime import upgrades as u
from dot_runtime.controller import Controller, atomic_json, initialize, read_json, file_hash, run_lock
from dot_runtime.delegation import attach_delegation, approve_l1
from dot_runtime.snapshot import snapshot_project


class TaskUpgradeTests(unittest.TestCase):
    request = onboarding_fixtures.OnboardingTests.request
    prepare = onboarding_fixtures.OnboardingTests.prepare
    confirm = onboarding_fixtures.OnboardingTests.confirm
    controller = controller_fixtures.ControllerTests.controller

    def setUp(self):
        onboarding_fixtures.OnboardingTests.setUp(self)
        self.settings['workflow_sha256'] = 'a' * 64
        self.confirm()
        self.source = {'version': '3.4.1', 'manifest_sha256': 'a' * 64}
        self.run_dir = self.task / 'control/run'

    def initialized(self):
        root = self.task / 'control'; root.mkdir(exist_ok=True)
        req, plan = root / 'requirements.md', root / 'plan.json'
        req.write_text('Add value function; no external actions', encoding='utf-8')
        atomic_json(plan, fixture_plan())
        initialize(self.project, req, plan, self.run_dir)
        c = self.controller(FakeAdapter())
        attach_delegation(c, self.task / 'delegation.json', self.task / 'raw-input.md')
        c.state['clarification_root'] = str(root); c.save()
        return c

    def upgrade(self):
        preview = self.request('upgrade_preview', source=self.source)
        return self.request('upgrade_commit', source=self.source, sha256=preview['sha256'])

    def test_preinitialization_upgrade_preserves_every_original_input(self):
        paths = ['confirmation.json', 'bridge.json', 'draft.json', 'delegation.json', 'raw-input.md']
        original = {p: (self.task / p).read_bytes() for p in paths}
        self.assertEqual(self.upgrade()['status'], 'UPGRADED')
        for p, contents in original.items():
            self.assertEqual((self.task / p).read_bytes(), contents)
        records, current = u.journal(self.task, read_json(self.task / 'confirmation.json'))
        self.assertEqual(current, file_hash(u.ROOT / 'MANIFEST.json'))
        self.assertEqual(len(records['records']), 1)
        self.assertFalse((self.task / u.PENDING).exists())
        self.assertFalse(self.run_dir.exists())

    def test_completed_implementation_is_not_replayed_but_all_checks_rerun(self):
        c = self.initialized()
        approve_l1(c, 'requirements', 'native-session', c.adapter)
        approve_l1(c, 'plan', 'native-session', c.adapter)
        c.state['implementation_attempts'] = 1
        c.worker('implementation', 'Add value', self.project)
        c.state['checkpoint'] = snapshot_project(self.project, self.run_dir / 'checkpoint-fixture')
        c.state['completed_tasks'] = ['build']; c.save()
        c.make_candidate(); c.transition('VERIFYING', 'Fixture paused before verification')
        old_candidate = copy.deepcopy(c.state['candidate'])
        old_reviews, old_sessions = copy.deepcopy(c.state['l1_reviews']), copy.deepcopy(c.state['sessions'])
        self.upgrade()
        c.state = read_json(c.state_path)
        self.assertEqual(c.state['state'], 'REVIEW_PENDING')
        self.assertFalse(c.state['approvals']); self.assertIsNone(c.state['candidate'])
        self.assertEqual(c.state['implementation_attempts'], 1)
        self.assertEqual(c.state['completed_tasks'], ['build'])
        self.assertEqual(c.state['l1_reviews'], old_reviews)
        self.assertEqual(c.state['sessions'], old_sessions)
        with self.assertRaises(ValueError): c.run()
        approve_l1(c, 'requirements', 'native-session', c.adapter)
        approve_l1(c, 'plan', 'native-session', c.adapter)
        result = c.run()
        self.assertEqual(result['report']['outcome'], 'SUCCESS')
        self.assertEqual(c.adapter.calls.count('implementation'), 1)
        self.assertEqual(c.adapter.calls[-2:], ['test', 'review'])
        self.assertNotEqual(result['candidate']['id'], old_candidate['id'])
        report = read_json(self.run_dir / 'final-report.json')
        self.assertEqual(len(report['runtime_upgrades']), 1)
        self.assertEqual(len(report['approved_test_commands']), 1)

    def test_decision_and_unspent_issue_work_are_not_resolved_by_upgrade(self):
        c = self.initialized()
        c.state.update(state='DECISION_PENDING', implementation_attempts=2, revision_count=1,
                       infrastructure_retries=1, root_cause_counts={'defect': 2}, pending_repair='fix recorded defect',
                       pending_clarification_revision=True)
        c.save(); before = read_json(c.state_path)
        self.upgrade(); after = read_json(c.state_path)
        self.assertEqual(after['state'], 'DECISION_PENDING')
        for key in ['implementation_attempts', 'revision_count', 'infrastructure_retries',
                    'root_cause_counts', 'pending_repair', 'pending_clarification_revision']:
            self.assertEqual(before[key], after[key])

    def test_unknown_and_final_are_rejected_without_changes(self):
        c = self.initialized()
        for change in [dict(active_run={'role': 'test'}), dict(report={'outcome': 'FAILED'}),
                       dict(state='BLOCKED'), dict(state='DELIVERY_READY')]:
            with self.subTest(change=change):
                state = copy.deepcopy(c.state); state.update(change); atomic_json(c.state_path, state)
                with self.assertRaises(ValueError): self.upgrade()
                self.assertEqual(read_json(c.state_path), state)
                self.assertFalse((self.task / u.JOURNAL).exists())

    def test_unresolved_api_and_outer_interruption_are_not_cleared(self):
        root = self.task / 'control/api'; root.mkdir(parents=True)
        atomic_json(root / 'ledger.json', {'attempts': [{'status': 'UNKNOWN'}]})
        with self.assertRaisesRegex(ValueError, 'API attempt'): self.upgrade()
        (root / 'ledger.json').unlink()
        atomic_json(self.task / 'interrupted.json', {'name': 'dot_plan'})
        with self.assertRaisesRegex(ValueError, 'UNKNOWN'): self.upgrade()
        self.assertTrue((self.task / 'interrupted.json').exists())

    def test_stale_native_confirmation_rejected(self):
        preview = self.request('upgrade_preview', source=self.source)
        (self.project / 'new.txt').write_text('changed during approval')
        with self.assertRaisesRegex(ValueError, 'changed during native confirmation'):
            self.request('upgrade_commit', source=self.source, sha256=preview['sha256'])
        self.assertFalse((self.task / u.PENDING).exists())

    def test_original_delegation_mutation_rejected(self):
        d = read_json(self.task / 'delegation.json'); d['limits']['api_calls'] += 1
        atomic_json(self.task / 'delegation.json', d)
        with self.assertRaisesRegex(ValueError, 'Original confirmation'): self.upgrade()

    def test_wrong_host_identity_and_unsupported_source_rejected(self):
        with self.assertRaisesRegex(ValueError, 'session identity'):
            self.request('upgrade_preview', source=self.source, session='model-forged')
        self.source['version'] = '3.3.0'
        with self.assertRaisesRegex(ValueError, 'No reviewed forward migration'): self.upgrade()

    def test_controller_lock_blocks_upgrade(self):
        self.initialized()
        with run_lock(self.run_dir):
            with self.assertRaisesRegex(ValueError, 'Another controller'): self.upgrade()

    def test_resource_journal_usage_and_pending_authorization_remain_byte_identical(self):
        from dot_runtime import resources
        from test_resources import allocation, amendment
        self.task = self.base / 'adaptive-task'; self.run_dir = self.task / 'control/run'
        self.draft['adaptive_limits'] = True; self.confirm()
        c = self.initialized(); root = self.task / 'control'
        original = read_json(self.task / 'delegation.json')
        resources.adjust(root, original, allocation(original['limits']), 'native-session', c.state)
        resources.propose_authorization(root, original, amendment({'allowed_paths': ['value.py', 'new.py']}), 'native-session', c.state)
        (root / 'api').mkdir()
        atomic_json(root / 'api/ledger.json', {'started_at': 1234, 'attempts': [{'status': 'COMPLETED', 'spent': 2}]})
        paths = [root / 'resources.json', root / 'api/ledger.json', self.task / 'delegation.json']
        before = [p.read_bytes() for p in paths]
        self.upgrade()
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertIsNotNone(resources.read(root, original)['pending'])

    def test_partial_state_write_recovers_exactly_without_replaying_work(self):
        self.initialized()
        real = u.atomic_json
        def fail_journal(path, value):
            if path.name == u.JOURNAL: raise OSError('injected interruption')
            return real(path, value)
        with patch.object(u, 'atomic_json', side_effect=fail_journal):
            with self.assertRaises(OSError): self.upgrade()
        self.assertTrue((self.task / u.PENDING).exists())
        with self.assertRaisesRegex(ValueError, 'UPGRADE_PENDING'): self.request('status')
        after_state = (self.task / u.STATE).read_bytes()
        result = self.request('upgrade_recover')
        self.assertEqual(result['status'], 'UPGRADED')
        self.assertEqual((self.task / u.STATE).read_bytes(), after_state)
        self.assertEqual(self.request('upgrade_recover')['status'], 'NO_PENDING_UPGRADE')

    def test_recovery_rejects_changes_instead_of_overwriting_them(self):
        self.initialized()
        with patch.object(u, '_finish', side_effect=OSError('injected crash')):
            with self.assertRaises(OSError): self.upgrade()
        (self.project / 'external.txt').write_text('external change')
        with self.assertRaises(ValueError): self.request('upgrade_recover')
        self.assertTrue((self.task / u.PENDING).exists())

    def test_crash_after_journal_commit_does_not_append_duplicate_upgrade(self):
        self.initialized()
        with patch.object(u.Path, 'unlink', side_effect=OSError('crash before pending marker removal')):
            with self.assertRaises(OSError): self.upgrade()
        committed = (self.task / u.JOURNAL).read_bytes()
        self.assertEqual(self.request('upgrade_recover')['status'], 'UPGRADED')
        self.assertEqual((self.task / u.JOURNAL).read_bytes(), committed)
        state = read_json(self.task / u.STATE)
        self.assertEqual(len([h for h in state['history'] if h.get('action') == 'runtime_upgrade']), 1)

    def test_journal_tampering_and_double_upgrade_rejected(self):
        self.upgrade()
        with self.assertRaises(ValueError): self.upgrade()
        log = read_json(self.task / u.JOURNAL); log['records'][0]['session'] = 'forged'
        atomic_json(self.task / u.JOURNAL, log)
        with self.assertRaisesRegex(ValueError, 'journal chain'): self.request('status')


if __name__ == '__main__': unittest.main()

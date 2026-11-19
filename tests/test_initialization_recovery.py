"""Local-only recovery tests. No actual API, Codex or product acceptance."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_onboarding as fixtures
from test_runtime_controller import fixture_plan, FakeAdapter
from dot_runtime import onboarding as o, initialization_recovery as r
from dot_runtime.controller import atomic_json, read_json, digest, file_hash, initialize, InitializationIncomplete, run_lock, ROOT
from dot_runtime.bridge import Bridge
from dot_runtime.gpt_dot import ResponsesClient


class InitializationRecoveryTests(unittest.TestCase):
    request = fixtures.OnboardingTests.request
    prepare = fixtures.OnboardingTests.prepare
    confirm = fixtures.OnboardingTests.confirm

    def setUp(self):
        fixtures.OnboardingTests.setUp(self)
        self.settings['workflow_sha256'] = 'a' * 64
        self.confirm()
        fixtures.pin_legacy_fixture(self.task)
        self.root = self.task / 'control'; self.root.mkdir(exist_ok=True)
        self.run = self.root / 'run'; self.run.mkdir()
        self.partial = self.run / 'initial-project'; self.partial.mkdir()
        (self.partial / 'partial.txt').write_text('partial original copy')
        (self.root / 'requirements.md').write_text('Requirements fixture')
        atomic_json(self.root / 'plan.json', fixture_plan())
        from dot_runtime import clarifications
        atomic_json(self.root / 'planning-status.json', {'status': 'PLAN_READY',
            'facts_sha256': clarifications.signature(self.root), 'plan_sha256': digest(fixture_plan()),
            'requirements_sha256': digest('Requirements fixture')})
        (self.root / 'api').mkdir()
        atomic_json(self.root / 'api/ledger.json', {'started_at': 1234, 'attempts': [
            {'status': 'COMPLETED', 'usage': {'input_tokens': 100, 'output_tokens': 23, 'total_tokens': 123}}]})
        atomic_json(self.task / 'interrupted.json', {'name': 'dot_initialize', 'status': 'UNKNOWN'})
        self.source = {'version': '3.5.0', 'manifest_sha256': 'a' * 64}

    def preview(self):
        return self.request('initialization_preview', source=self.source)

    def commit(self, preview=None):
        preview = preview or self.preview()
        return self.request('initialization_commit', source=self.source, sha256=preview['sha256'])

    def originals(self):
        return {p: (self.task / p).read_bytes() for p in ['confirmation.json', 'draft.json', 'delegation.json',
            'raw-input.md', 'bridge.json', 'control/api/ledger.json', 'control/plan.json', 'control/requirements.md']}

    def test_preview_is_read_only_and_exact_consumption_is_preserved(self):
        before = r._files(self.task)
        p = self.preview()
        self.assertEqual(p['api_calls'], 1); self.assertEqual(p['usage']['total_tokens'], 123)
        self.assertFalse(p['baseline_changed']); self.assertEqual(before, r._files(self.task))

    def test_current_and_recent_sources_have_a_reviewed_recovery_path(self):
        versions = ['3.8.0', '3.9.1', '3.10.0', '3.11.0', '3.12.1', (ROOT / 'VERSION').read_text().strip()]
        before = r._files(self.task)
        for version in versions:
            with self.subTest(version=version):
                self.source['version'] = version
                self.assertEqual(self.preview()['source']['version'], version)
        self.assertEqual(before, r._files(self.task))
        self.source['version'] = '3.13.0'
        with patch.object(r, '_target', return_value={'version': '3.13.0', 'manifest_sha256': 'b' * 64}):
            self.assertEqual(self.preview()['source']['version'], '3.13.0')
        self.source['version'] = '9.0.0'
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.preview()

    def test_originals_and_partial_files_retained_and_same_plan_can_initialize(self):
        before = self.originals(); partial = (self.partial / 'partial.txt').read_bytes()
        old_plan_status = (self.root / 'planning-status.json').read_bytes()
        result = self.commit()
        self.assertEqual(result['status'], 'INITIALIZATION_RECOVERED')
        self.assertEqual(before, self.originals())
        self.assertFalse((self.task / 'interrupted.json').exists())
        self.assertEqual((self.root / 'planning-status.json').read_bytes(), old_plan_status)
        archived = self.task / 'initialization-archives' / result['record']['sha256']
        self.assertEqual((archived / 'control/run/initial-project/partial.txt').read_bytes(), partial)
        state = initialize(self.project, self.root / 'requirements.md', self.root / 'plan.json', self.run)
        self.assertEqual(state['implementation_attempts'], 0)
        self.assertFalse(state['approvals']); self.assertEqual(state['state'], 'REVIEW_PENDING')

    def test_changed_baseline_needs_native_adoption_and_new_plan(self):
        before = self.originals()
        (self.project / 'added.txt').write_text('New baseline')
        p = self.preview(); self.assertTrue(p['baseline_changed'])
        self.commit(p)
        self.assertEqual(before, self.originals())
        self.assertEqual(read_json(self.root / 'planning-status.json')['status'], 'NEEDS_INFO')
        receipt = read_json(self.task / 'confirmation.json')
        self.assertEqual(r.effective_baseline(self.task, receipt), o.fingerprint(self.project))
        status = self.request('status')
        self.assertEqual(status['state'], 'NEEDS_INFO')
        self.assertEqual(len(status['initialization_recoveries']), 1)
        # A read-only client fixture suffices: initialization must refuse before any API.
        def factory(settings):
            return Bridge(settings, client_factory=lambda m,p,d: ResponsesClient(m,p,d,send=lambda *_: self.fail('No API')))
        with patch.object(o, 'Bridge', side_effect=factory):
            result = self.request('bridge', name='dot_initialize', arguments={})
        self.assertFalse(result['accepted']); self.assertFalse((self.run/'state.json').exists())

    def test_project_or_ledger_changes_during_approval_rejected(self):
        for target in ('project', 'ledger'):
            with self.subTest(target=target):
                p = self.preview()
                if target == 'project': (self.project / 'changed.txt').write_text('changed')
                else:
                    ledger=read_json(self.root/'api/ledger.json'); ledger['attempts'].append({'status':'COMPLETED'})
                    atomic_json(self.root/'api/ledger.json',ledger)
                with self.assertRaisesRegex(ValueError, 'changed during native'): self.commit(p)
                self.assertTrue((self.task/'interrupted.json').exists())

    def test_wrong_session_wrong_source_or_edited_confirmation_inputs_rejected(self):
        with self.assertRaisesRegex(ValueError, 'native session'):
            self.request('initialization_preview', source=self.source, session='forged')
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.request('initialization_preview', source={**self.source,'version':'3.4.1'})
        d=read_json(self.task/'delegation.json'); d['limits']['api_calls']+=1
        atomic_json(self.task/'delegation.json',d)
        with self.assertRaisesRegex(ValueError,'inputs changed'): self.preview()

    def test_unresolved_api_wrong_operation_and_final_report_never_unlock(self):
        for name, value in [('control/api/ledger.json',{'attempts':[{'status':'RESERVED'}]}),
                            ('interrupted.json',{'name':'dot_run','status':'UNKNOWN'}),
                            ('control/preflight-report.json',{'outcome':'FAILED'}),
                            ('control/run/state.json',{'implementation_attempts':0})]:
            path=self.task/name; before=path.read_bytes() if path.exists() else None
            atomic_json(path,value)
            with self.assertRaises(ValueError): self.preview()
            if before is None: path.unlink()
            else: path.write_bytes(before)
        self.assertTrue((self.task/'interrupted.json').exists())

    def test_active_controller_and_unexpected_evidence_block_recovery(self):
        with run_lock(self.run):
            with self.assertRaisesRegex(ValueError, 'Another controller'): self.preview()
        (self.run/'sessions').mkdir()
        with self.assertRaisesRegex(ValueError,'Unexpected run records'): self.preview()

    def test_partial_recovery_finishes_once_and_keeps_new_baseline_and_history(self):
        (self.project/'new.txt').write_text('Changed')
        before=self.originals(); real=r.atomic_json
        def fail(path,value):
            if Path(path).name=='runtime-upgrades.json': raise OSError('fixture commit interruption')
            real(path,value)
        with patch.object(r,'atomic_json',side_effect=fail):
            with self.assertRaises(OSError): self.commit()
        self.assertTrue((self.task/r.PENDING).exists())
        with self.assertRaisesRegex(ValueError,'RECOVERY_PENDING'): self.request('status')
        result=self.request('initialization_finish')
        self.assertEqual(result['status'],'INITIALIZATION_RECOVERED')
        self.assertEqual(before,self.originals())
        self.assertEqual(self.request('initialization_finish')['status'],'NO_PENDING_INITIALIZATION_RECOVERY')
        self.assertEqual(len(read_json(self.task/r.JOURNAL)['records']),1)

    def test_changed_product_after_recovery_crash_rejected(self):
        real=r.atomic_json
        def fail(path,value):
            if Path(path).name==r.JOURNAL: raise OSError('fixture interruption')
            real(path,value)
        with patch.object(r,'atomic_json',side_effect=fail):
            with self.assertRaises(OSError): self.commit()
        (self.project/'changed.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'snapshot changed'): self.request('initialization_finish')
        self.assertTrue((self.task/r.PENDING).exists())

    def test_recovery_intent_tampering_rejected(self):
        atomic_json(self.task/r.PENDING,{'entry':{'target':r._target()},'sha256':'wrong'})
        with self.assertRaisesRegex(ValueError,'intent damaged'): self.request('initialization_finish')

    def test_new_copy_failure_has_durable_diagnostic_and_no_state(self):
        target=self.root/'new-run'
        def failing_copy(project,destination):
            destination.mkdir(); (destination/'part.txt').write_text('partial')
            raise OSError('fixture disk write failure')
        with patch('dot_runtime.controller.snapshot_project',side_effect=failing_copy):
            with self.assertRaises(InitializationIncomplete):
                initialize(self.project,self.root/'requirements.md',self.root/'plan.json',target)
        record=read_json(target/'initialization.json')
        self.assertEqual(record['status'],'FAILED'); self.assertIn('disk write',record['error'])
        self.assertFalse((target/'state.json').exists())

    def test_completed_local_failure_without_unknown_is_recoverable(self):
        (self.task/'interrupted.json').unlink()
        atomic_json(self.run/'initialization.json',{'operation':'dot_initialize','status':'FAILED','error':'fixture'})
        self.assertEqual(self.commit()['status'],'INITIALIZATION_RECOVERED')

    def test_bridge_returns_known_initialization_failure_and_repeat_refusal_without_api(self):
        # A new control directory avoids resetting or deleting the older fixture.
        settings=read_json(self.task/'bridge.json'); settings['control_dir']=str(self.task/'fresh-control')
        root=Path(settings['control_dir']); root.mkdir()
        for name in ('requirements.md','plan.json','planning-status.json'):
            (root/name).write_bytes((self.root/name).read_bytes())
        bridge=Bridge(settings,client_factory=lambda m,p,d: ResponsesClient(m,p,d,send=lambda *_: self.fail('No API')))
        with patch('dot_runtime.controller.snapshot_project',side_effect=OSError('fixture copy failure')):
            result=bridge.call('dot_initialize',{})
        self.assertEqual(result['operation_status'],'COMPLETED_INITIALIZATION_FAILED')
        self.assertEqual(result['initialization']['status'],'FAILED')
        result=bridge.call('dot_initialize',{})
        self.assertEqual(result['operation_status'],'COMPLETED_REJECTED')
        self.assertFalse((root/'run/state.json').exists())

    def test_recovered_changed_baseline_can_plan_initialize_and_independently_review(self):
        from dot_runtime.gpt_dot import ResponsesClient
        from dot_runtime import clarifications
        from dot_runtime.delegation import approve_l1
        from dot_runtime.controller import Controller
        from test_plan_rejections import proposed
        (self.project/'added.txt').write_text('reviewed new baseline')
        self.commit()
        # Keep the already consumed request and start time while using a live fixture clock.
        # The production recovery above never edits this ledger; fixture time is set for further work.
        import time
        ledger=read_json(self.root/'api/ledger.json'); ledger['started_at']=time.time()
        original=read_json(self.task/'delegation.json')
        ledger.update(delegation_sha256=digest(original),model=self.settings['model'])
        atomic_json(self.root/'api/ledger.json',ledger)
        worker=FakeAdapter()
        def factory(settings):
            return Bridge(settings,workers=worker,client_factory=lambda m,p,d: ResponsesClient(m,p,d,send=lambda *_: proposed(fixture_plan())))
        with patch.object(o,'Bridge',side_effect=factory):
            planned=self.request('bridge',name='dot_plan',arguments={'requirements':'Adopt reviewed baseline and implement fixture'})
            self.assertIn('plan',planned)
            initialized=self.request('bridge',name='dot_initialize',arguments={})
            self.assertEqual(initialized['state'],'REVIEW_PENDING')
        state=read_json(self.run/'state.json')
        self.assertEqual(state['initial_snapshot']['id'],o.fingerprint(self.project)['id'])
        self.assertEqual(len(read_json(self.root/'api/ledger.json')['attempts']),2)
        c=Controller(self.run,worker)
        approve_l1(c,'requirements','native-session',worker)
        self.assertEqual(worker.calls,['review'])
        self.assertTrue((self.run/'l1-review-inputs/1/initialization-recoveries.json').exists())

    def test_unknown_run_files_are_rejected(self):
        (self.run/'unrecorded.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Unexpected'): self.preview()

    def test_long_partial_snapshot_is_retained_and_readable_after_archiving(self):
        from dot_runtime.snapshot import _filesystem_path, _root
        import shutil, tempfile
        def cleanup_long_paths():
            expected=Path(self.tmp.name).absolute()
            if self.base.absolute()!=expected or expected.parent!=Path(tempfile.gettempdir()).absolute():
                raise ValueError('Unexpected temporary cleanup target')
            _root(expected,exists=True);shutil.rmtree(_filesystem_path(expected))
        self.addCleanup(cleanup_long_paths)
        relative=Path('build')/('component_'*7)/('object_'*9)/'artifact.obj'
        dest=self.partial/relative
        _filesystem_path(dest.parent).mkdir(parents=True)
        _filesystem_path(dest).write_bytes(b'original long evidence')
        result=self.commit()
        archived=self.task/'initialization-archives'/result['record']['sha256']/'control/run/initial-project'/relative
        self.assertGreater(len(str(archived)),260)
        self.assertEqual(_filesystem_path(archived).read_bytes(),b'original long evidence')
        self.assertIn(archived.relative_to(self.task).as_posix(),r._files(self.task))

    def test_old_archive_tamper_after_commit_is_detected_during_pending_finish(self):
        real=r.atomic_json
        def fail(path,value):
            if Path(path).name==r.JOURNAL: raise OSError('fixture crash')
            real(path,value)
        with patch.object(r,'atomic_json',side_effect=fail):
            with self.assertRaises(OSError): self.commit()
        archived=next((self.task/'initialization-archives').rglob('partial.txt'))
        archived.write_text('tampered')
        with self.assertRaisesRegex(ValueError,'records changed'): self.request('initialization_finish')


class NativeInitializationCommitTests(unittest.TestCase):
    request = fixtures.OnboardingTests.request
    prepare = fixtures.OnboardingTests.prepare
    confirm = fixtures.OnboardingTests.confirm
    allocate = fixtures.OnboardingTests.allocate
    approve_requirements = fixtures.OnboardingTests.approve_requirements

    def setUp(self):
        fixtures.OnboardingTests.setUp(self)
        def cleanup_long_task():
            import shutil, tempfile
            from dot_runtime.snapshot import _filesystem_path, _root
            expected = Path(self.tmp.name).absolute()
            if self.base.absolute() != expected or expected.parent != Path(tempfile.gettempdir()).absolute():
                raise ValueError('Unexpected temporary cleanup target')
            _root(expected, exists=True)
            shutil.rmtree(_filesystem_path(expected))
        self.addCleanup(cleanup_long_task)
        self.settings['workflow_sha256'] = file_hash(ROOT / 'MANIFEST.json')
        self.confirm()
        self.root = self.task / 'control'
        self.run = self.root / 'run'
        self.workers = FakeAdapter()
        from test_plan_rejections import proposed
        self.native_bridge = patch.object(o, 'Bridge', side_effect=lambda settings: Bridge(settings,
            workers=self.workers, client_factory=lambda m, p, d: ResponsesClient(m, p, d,
                send=lambda *_: proposed(fixture_plan()))))
        self.native_bridge.start()
        self.addCleanup(self.native_bridge.stop)
        self.allocate()
        self.approve_requirements('Complete the approved input parsing task; no external actions.')
        self.request('bridge', name='dot_plan', arguments={
            'requirements': 'Complete the approved input parsing task; no external actions.'})
        self.source = {'version': (ROOT / 'VERSION').read_text().strip(),
                       'manifest_sha256': self.settings['workflow_sha256']}

    def originals(self):
        return {name: (self.task / name).read_bytes() for name in (
            'confirmation.json', 'delegation.json', 'raw-input.md', 'bridge.json', 'draft.json',
            'control/api/ledger.json', 'control/resources.json', 'control/requirements-review/state.json')}

    def initialize(self):
        return self.request('bridge', name='dot_initialize', arguments={})

    def recover(self):
        preview = self.request('initialization_preview', source=self.source)
        return self.request('initialization_commit', source=self.source, sha256=preview['sha256'])

    def assert_completed_once(self, originals):
        state = read_json(self.run / 'state.json')
        self.assertEqual(state['state'], 'PLAN_PENDING')
        self.assertEqual(len(state['l1_reviews']), 1)
        self.assertEqual(state['implementation_attempts'], 0)
        self.assertTrue(state['delegation'])
        self.assertEqual(state['approvals']['requirements']['reviewer_session_id'],
                         read_json(self.root / 'requirements-review/state.json')['approval']['reviewer_session_id'])
        self.assertEqual(self.workers.calls, ['review'])
        self.assertEqual(originals, self.originals())

    def test_binding_failure_commits_no_state_and_recovers_without_repeating_review(self):
        before = self.originals()
        with patch('dot_runtime.delegation.prepare_delegation', side_effect=OSError('fixture binding write failure')):
            result = self.initialize()
        self.assertEqual(result['operation_status'], 'COMPLETED_INITIALIZATION_FAILED')
        self.assertFalse((self.run / 'state.json').exists())
        self.recover()
        self.initialize()
        self.assert_completed_once(before)

    def test_review_copy_failure_has_no_partial_state_and_preserves_original_approval(self):
        before = self.originals()
        real_write = Path.write_bytes
        def fail_copy(path, content):
            if 'preflight-requirements' in path.parts:
                real_write(path, content[:5])
                raise OSError('fixture partial review import')
            return real_write(path, content)
        with patch.object(Path, 'write_bytes', fail_copy):
            result = self.initialize()
        self.assertEqual(result['operation_status'], 'COMPLETED_INITIALIZATION_FAILED')
        self.assertFalse((self.run / 'state.json').exists())
        result = self.recover()
        archive = self.task / 'initialization-archives' / result['record']['sha256']
        self.assertTrue((archive / 'control/run/preflight-requirements').is_dir())
        self.initialize()
        self.assert_completed_once(before)

    def test_state_atomic_replace_failure_archives_uncommitted_temporary(self):
        before = self.originals()
        from dot_runtime import controller as controller_module
        real_replace = controller_module.os.replace
        def fail_replace(source, destination):
            if Path(destination).name == 'state.json' and Path(destination).parent.name == 'run':
                raise OSError('fixture state replace failure')
            return real_replace(source, destination)
        with patch.object(controller_module.os, 'replace', side_effect=fail_replace):
            result = self.initialize()
        self.assertEqual(result['operation_status'], 'COMPLETED_INITIALIZATION_FAILED')
        self.assertFalse((self.run / 'state.json').exists())
        temporary = next(self.run.glob('state.json.*.tmp'))
        original = temporary.read_bytes()
        result = self.recover()
        archive = self.task / 'initialization-archives' / result['record']['sha256']
        from dot_runtime.snapshot import _filesystem_path
        self.assertEqual(_filesystem_path(archive / 'control/run' / temporary.name).read_bytes(), original)
        self.assertFalse(temporary.exists())
        self.initialize()
        self.assert_completed_once(before)

    def test_initial_operation_replace_failure_requires_native_interruption_review(self):
        before = self.originals()
        from dot_runtime import controller as controller_module
        real_replace = controller_module.os.replace
        def fail_replace(source, destination):
            if Path(destination).name == 'initialization.json':
                raise OSError('fixture initial operation replace failure')
            return real_replace(source, destination)
        with patch.object(controller_module.os, 'replace', side_effect=fail_replace):
            with self.assertRaisesRegex(OSError, 'initial operation replace'):
                self.initialize()
        self.assertFalse((self.run / 'state.json').exists())
        self.assertFalse((self.run / 'initialization.json').exists())
        temporary = next(self.run.glob('initialization.json.*.tmp'))
        original = temporary.read_bytes()
        atomic_json(self.task / 'interrupted.json', {'name': 'dot_initialize', 'status': 'UNKNOWN'})
        result = self.recover()
        archive = self.task / 'initialization-archives' / result['record']['sha256']
        from dot_runtime.snapshot import _filesystem_path
        self.assertEqual(_filesystem_path(archive / 'control/run' / temporary.name).read_bytes(), original)
        self.initialize()
        self.assert_completed_once(before)

    def make_legacy_partial(self, bound=False):
        from dot_runtime.controller import Controller
        from dot_runtime.delegation import attach_delegation
        initialize(self.project, self.root / 'requirements.md', self.root / 'plan.json', self.run)
        controller = Controller(self.run)
        controller.state['clarification_root'] = str(self.root)
        controller.save()
        if bound:
            attach_delegation(controller, self.task / 'delegation.json', self.task / 'raw-input.md')
            partial = self.run / 'preflight-requirements/partial.json'
            partial.parent.mkdir()
            partial.write_text('partial old review import')
        atomic_json(self.task / 'interrupted.json', {'name': 'dot_initialize', 'status': 'UNKNOWN'})
        return controller

    def test_old_state_without_delegation_is_archived_and_original_review_is_reused(self):
        before = self.originals()
        self.make_legacy_partial()
        state_bytes = (self.run / 'state.json').read_bytes()
        from dot_runtime import operation_recovery
        with self.assertRaisesRegex(ValueError, 'dedicated'):
            operation_recovery._guard(self.task, read_json(self.task / 'confirmation.json'), 'native-session')
        result = self.recover()
        archive = self.task / 'initialization-archives' / result['record']['sha256']
        self.assertEqual((archive / 'control/run/state.json').read_bytes(), state_bytes)
        self.assertFalse((self.task / 'interrupted.json').exists())
        self.initialize()
        self.assert_completed_once(before)

    def test_old_bound_state_without_review_adoption_uses_the_same_recovery(self):
        before = self.originals()
        self.make_legacy_partial(bound=True)
        self.assertTrue(self.request('initialization_preview', source=self.source)['incomplete_native_state'])
        self.recover()
        self.initialize()
        self.assert_completed_once(before)

    def test_executed_or_approved_state_is_never_reset_as_partial_initialization(self):
        controller = self.make_legacy_partial(bound=True)
        original = copy.deepcopy(controller.state)
        for key, value in [('implementation_attempts', 1), ('sessions', [{'role': 'implementation'}]),
                           ('candidate', {'id': 'existing'}), ('approvals', {'plan': {}}),
                           ('state', 'PLAN_PENDING'), ('active_run', {'role': 'implementation'})]:
            with self.subTest(key=key):
                atomic_json(self.run / 'state.json', {**original, key: value})
                with self.assertRaisesRegex(ValueError, 'initialized task'):
                    self.request('initialization_preview', source=self.source)
        atomic_json(self.run / 'state.json', original)

    def test_full_state_commit_before_host_loss_is_kept_for_normal_reconciliation(self):
        before = self.originals()
        from dot_runtime import controller as controller_module, operation_recovery
        real_write = controller_module.atomic_json
        def interrupted_after_commit(path, value):
            real_write(path, value)
            if Path(path) == self.run / 'state.json':
                raise OSError('fixture host result lost after complete state commit')
        with patch.object(controller_module, 'atomic_json', side_effect=interrupted_after_commit):
            with self.assertRaisesRegex(OSError, 'complete state commit'):
                self.initialize()
        atomic_json(self.task / 'interrupted.json', {'name': 'dot_initialize', 'status': 'UNKNOWN'})
        self.assert_completed_once(before)
        with self.assertRaisesRegex(ValueError, 'initialized task'):
            self.request('initialization_preview', source=self.source)
        _, _, state, _ = operation_recovery._guard(self.task, read_json(self.task / 'confirmation.json'), 'native-session')
        self.assertEqual(state['state'], 'PLAN_PENDING')
        self.assertTrue((self.task / 'interrupted.json').exists())

    def test_native_state_recovery_itself_can_finish_after_state_archive(self):
        before = self.originals()
        self.make_legacy_partial(bound=True)
        real_write = r.atomic_json
        def fail_journal(path, value):
            if Path(path).name == r.JOURNAL:
                raise OSError('fixture recovery interruption after archive')
            return real_write(path, value)
        with patch.object(r, 'atomic_json', side_effect=fail_journal):
            with self.assertRaises(OSError):
                self.recover()
        self.assertFalse((self.run / 'state.json').exists())
        self.assertTrue((self.task / r.PENDING).exists())
        self.request('initialization_finish')
        self.initialize()
        self.assert_completed_once(before)


if __name__ == '__main__':
    unittest.main()

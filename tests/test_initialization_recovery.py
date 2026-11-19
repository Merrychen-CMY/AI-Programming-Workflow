"""Local-only recovery tests. No actual API, Codex or product acceptance."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_onboarding as fixtures
from test_runtime_controller import fixture_plan, FakeAdapter
from dot_runtime import onboarding as o, initialization_recovery as r
from dot_runtime.controller import atomic_json, read_json, digest, initialize, InitializationIncomplete, run_lock
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


if __name__ == '__main__':
    unittest.main()

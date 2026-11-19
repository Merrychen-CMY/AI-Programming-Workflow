"""Real Markdown artifacts with fake AI workers; no live API or product execution."""
import unittest
from pathlib import Path
import copy
from unittest.mock import patch

import test_delegated_l1 as delegated
import test_requirements_first as requirements
import test_review_handoff as handoff
import test_onboarding as onboarding
import test_runtime_controller as fixtures
from closure_fixtures import assessment, reason as closure_reason
from dot_runtime.controller import file_hash, read_json
from dot_runtime.delegation import approve_l1, submit_l1_report
from dot_runtime.presentation import compact, read_record
from dot_runtime import requirements_review


class ReviewMarkdownTests(unittest.TestCase):
    setUp = delegated.DelegatedTests.setUp
    controller = delegated.DelegatedTests.controller
    approved = delegated.DelegatedTests.approved

    def test_requirements_and_plan_have_distinct_files_and_actual_approval(self):
        self.approved()
        descriptors = [r['markdown_report'] for r in self.c.state['l1_reviews']]
        self.assertNotEqual(descriptors[0]['path'], descriptors[1]['path'])
        for record in descriptors:
            path = Path(record['path'])
            self.assertTrue(path.is_absolute())
            self.assertEqual(record['sha256'], file_hash(path))
            text = path.read_text(encoding='utf-8')
            self.assertIn('L1 已在委托范围内批准', text)
            self.assertIn('审核基线与完整证据记录', text)
            self.assertIn('session-', text)
            self.assertIn('l1_reviews', text)

    def test_rejected_review_is_readable_without_claiming_approval(self):
        result = fixtures.result('FAIL')
        result['findings'] = ['Missing hardware evidence; do not guess a PASS.']
        approve_l1(self.c, 'requirements', 'author', fixtures.FakeAdapter([('review', result)]))
        report = self.c.state['l1_reviews'][-1]['markdown_report']
        text = Path(report['path']).read_text(encoding='utf-8')
        self.assertIn('L1 未批准', text)
        self.assertIn(result['findings'][0], text)
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertFalse(self.c.state['approvals'])

    def test_completed_review_with_changed_baseline_pauses_and_has_report(self):
        self.c.adapter.hook = lambda *_: self.requirements.write_text('Changed during review', encoding='utf-8')
        approve_l1(self.c, 'requirements', 'author', self.c.adapter)
        record = self.c.state['l1_reviews'][-1]
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(self.c.state['active_run'])
        self.assertFalse(self.c.state['approvals'])
        self.assertIn('Inputs changed', record['refusal'])
        self.assertIn('Inputs changed', Path(record['markdown_report']['path']).read_text(encoding='utf-8'))

    def test_completed_review_with_changed_delegation_refuses_approval_and_has_report(self):
        from dot_runtime.controller import atomic_json
        def change_authority(*_):
            value = read_json(self.delegation_path)
            value['hard_constraints'].append('Changed during review')
            atomic_json(self.delegation_path, value)
        self.c.adapter.hook = change_authority
        approve_l1(self.c, 'requirements', 'author', self.c.adapter)
        record = self.c.state['l1_reviews'][-1]
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertIsNone(self.c.state['active_run'])
        self.assertFalse(self.c.state['approvals'])
        self.assertIn('delegation changed', record['refusal'])
        self.assertIn(record['refusal'], Path(record['markdown_report']['path']).read_text(encoding='utf-8'))

    def test_report_write_failure_reentry_preserves_review_and_approval_without_redispatch(self):
        with patch('dot_runtime.report_markdown.review', side_effect=OSError('fixture disk refusal')):
            approve_l1(self.c, 'requirements', 'author', self.c.adapter)
        record = self.c.state['l1_reviews'][-1]
        original_result, original_approval = copy.deepcopy(record['result']), copy.deepcopy(self.c.state['approvals'])
        self.assertEqual(record['markdown_error']['code'], 'MARKDOWN_GENERATION_FAILED')
        self.assertEqual(self.c.state['state'], 'PLAN_PENDING')
        approve_l1(self.c, 'requirements', 'author', self.c.adapter)
        self.assertEqual(self.c.adapter.calls, ['review'])
        self.assertEqual(len(self.c.state['l1_reviews']), 1)
        self.assertEqual(self.c.state['approvals'], original_approval)
        self.assertEqual(self.c.state['l1_reviews'][0]['result'], original_result)
        self.assertNotIn('markdown_error', self.c.state['l1_reviews'][0])
        self.assertTrue(Path(self.c.state['l1_reviews'][0]['markdown_report']['path']).is_file())

    def test_missing_unknown_review_markdown_can_be_generated_without_clearing_active_run(self):
        worker = fixtures.FakeAdapter([('review', fixtures.result('UNKNOWN'))])
        with patch('dot_runtime.report_markdown.review', side_effect=OSError('fixture disk refusal')):
            approve_l1(self.c, 'requirements', 'author', worker)
        active = copy.deepcopy(self.c.state['active_run'])
        approve_l1(self.c, 'requirements', 'author', worker)
        self.assertEqual(worker.calls, ['review'])
        self.assertEqual(self.c.state['active_run'], active)
        self.assertEqual(self.c.state['l1_reviews'][0]['status'], 'UNKNOWN')
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')

    def test_final_success_contains_original_outcome_evidence_and_task_completion(self):
        c = self.approved()
        c.run()
        original = file_hash(c.run_dir / 'final-report.json')
        submit_l1_report(c, '已核对成功证据。', 'author')
        record = read_json(c.run_dir / 'final-report.json')
        text = Path(record['markdown_report']['path']).read_text(encoding='utf-8')
        self.assertEqual(record['l1_submission']['dot_report_sha256'], original)
        for required in ('已核对成功证据', 'SUCCESS', original, 'pending_tasks', 'approved_test_commands'):
            self.assertIn(required, text)
        c.approve('report', lambda *_: True)
        self.assertEqual(c.state['state'], 'DELIVERY_READY')

    def test_resubmission_preserves_previous_markdown_and_failed_outcome(self):
        self.c.report('FAILED', 'Fixture blocked', l1_assessment=assessment())
        submit_l1_report(self.c, 'First assessment', 'author')
        first = read_json(self.c.run_dir / 'final-report.json')['markdown_report']
        submit_l1_report(self.c, 'Second assessment preserves FAILED.', 'author')
        second = read_json(self.c.run_dir / 'final-report.json')['markdown_report']
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(file_hash(first['path']), first['sha256'])
        self.assertEqual(read_json(self.c.run_dir / 'final-report.json')['outcome'], 'FAILED')

    def test_final_markdown_write_failure_preserves_submission_and_blocks_human_review_until_repaired(self):
        self.c.report('FAILED', 'Fixture blocked', l1_assessment=assessment())
        with patch('dot_runtime.report_markdown.final', side_effect=OSError('fixture disk refusal')):
            submit_l1_report(self.c, 'Original assessment', 'author')
        before = read_json(self.c.run_dir / 'final-report.json')
        self.assertTrue(before['markdown_required'])
        self.assertEqual(before['markdown_error']['code'], 'MARKDOWN_GENERATION_FAILED')
        with self.assertRaisesRegex(ValueError, 'Markdown report is pending'):
            self.c.approve('report', lambda *_: True)
        submit_l1_report(self.c, 'Must not replace original assessment on repair', 'author')
        after = read_json(self.c.run_dir / 'final-report.json')
        self.assertEqual(after['l1_submission'], before['l1_submission'])
        self.assertEqual(after['implementation_attempts'], before['implementation_attempts'])
        self.assertEqual(after['issues'], before['issues'])
        self.assertNotIn('markdown_error', after)
        self.c.approve('report', lambda *_: True)
        self.assertEqual(self.c.state['state'], 'BLOCKED')

    def test_partial_file_write_is_not_published_and_can_be_retried(self):
        self.c.report('FAILED', 'Fixture blocked', l1_assessment=assessment())
        original_open = Path.open
        class PartialWriter:
            def __init__(self, output): self.output = output
            def __enter__(self): return self
            def __exit__(self, *_): self.output.close()
            def write(self, data):
                self.output.write(data[:20])
                self.output.flush()
                raise OSError('fixture disk full after partial write')
        # Generated temp names include a nonce between .md and .tmp.
        def inject_partial(path, mode='r', *args, **kwargs):
            output = original_open(path, mode, *args, **kwargs)
            return PartialWriter(output) if '.md.' in path.name and path.name.endswith('.tmp') and mode == 'xb' else output
        with patch.object(Path, 'open', inject_partial):
            submit_l1_report(self.c, 'Original partial-write assessment', 'author')
        report = read_json(self.c.run_dir / 'final-report.json')
        self.assertEqual(report['markdown_error']['code'], 'MARKDOWN_GENERATION_FAILED')
        self.assertEqual(list((self.c.run_dir / 'reports').glob('*.md')), [])
        self.assertEqual(list((self.c.run_dir / 'reports').glob('*.tmp')), [])
        submission = report['l1_submission']
        submit_l1_report(self.c, 'Restore only the stored report', 'author')
        repaired = read_json(self.c.run_dir / 'final-report.json')
        self.assertEqual(repaired['l1_submission'], submission)
        self.assertEqual(file_hash(repaired['markdown_report']['path']), repaired['markdown_report']['sha256'])

    def test_changed_markdown_refuses_final_approval_before_or_during_confirmation(self):
        self.c.report('FAILED', 'Fixture blocked', l1_assessment=assessment())
        submit_l1_report(self.c, 'Retained failure', 'author')
        path = Path(read_json(self.c.run_dir / 'final-report.json')['markdown_report']['path'])
        original = path.read_bytes()
        path.write_text('Altered outcome', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Markdown report changed'):
            self.c.approve('report', lambda *_: True)
        path.write_bytes(original)
        def mutate(*_):
            path.write_text('Altered during native confirmation', encoding='utf-8')
            return True
        with self.assertRaisesRegex(ValueError, 'Markdown report changed'):
            self.c.approve('report', mutate)
        self.assertNotIn('report', self.c.state['approvals'])
        self.assertEqual(self.c.state['state'], 'REPORT_PENDING')

    def test_report_creation_and_verification_support_long_windows_task_paths(self):
        from dot_runtime.report_markdown import final, verify
        from dot_runtime.snapshot import _filesystem_path
        root = self.root / ('nested-task-' * 8) / ('nested-control-' * 10) / 'run'
        report = {'outcome': 'FAILED', 'reason': 'No feasible solution', 'l1_submission': {
            'summary': 'Retained failure', 'dot_report_sha256': 'a' * 64}}
        report['markdown_report'] = final(root, report)
        path = Path(report['markdown_report']['path'])
        self.assertTrue(path.is_relative_to(self.root))
        def remove_long_fixture():
            _filesystem_path(path).unlink()
            for directory in (path.parent, *path.parent.parents):
                if directory == self.root:
                    break
                _filesystem_path(directory).rmdir()
        self.addCleanup(remove_long_fixture)
        self.assertGreater(len(report['markdown_report']['path']), 260)
        self.assertEqual(verify(report, root), report['markdown_report'])
        self.assertIn('Retained failure', _filesystem_path(path).read_text(encoding='utf-8'))

    def test_verification_rejects_parent_traversal_even_when_hash_matches(self):
        from dot_runtime.report_markdown import verify
        outside = self.root / 'outside.md'
        outside.write_text('Outside task report', encoding='utf-8')
        report = {'markdown_report': {'path': str(self.c.run_dir / '..' / 'outside.md'),
                                     'sha256': file_hash(outside)}}
        with self.assertRaisesRegex(ValueError, 'inside the task report directory'):
            verify(report, self.c.run_dir)


class PreflightReviewMarkdownTests(unittest.TestCase):
    setUp = requirements.RequirementsFirstTests.setUp
    approved = requirements.RequirementsFirstTests.approved

    def test_preplan_review_artifact_and_idempotent_approval(self):
        value = self.approved()
        record = value['reviews'][0]['markdown_report']
        self.assertEqual(file_hash(record['path']), record['sha256'])
        original = Path(record['path']).read_bytes()
        requirements_review.approve(self.settings, 'l1-author', self.workers)
        self.assertEqual(Path(record['path']).read_bytes(), original)
        self.assertEqual(self.workers.calls, ['review'])
        relative = Path(record['path']).relative_to(self.control).as_posix()
        self.assertEqual(read_record(self.control, relative, '0')['sha256'], record['sha256'])

    def test_preplan_markdown_repair_does_not_review_changed_baseline_again(self):
        with patch('dot_runtime.report_markdown.review', side_effect=OSError('fixture disk refusal')):
            value = self.approved()
        approval = copy.deepcopy(value['approval'])
        (self.project / 'value.py').write_text('Changed after completed review', encoding='utf-8')
        result = requirements_review.approve(self.settings, 'l1-author', self.workers)
        self.assertEqual(self.workers.calls, ['review'])
        self.assertEqual(result['approval'], approval)
        self.assertTrue(Path(result['reviews'][0]['markdown_report']['path']).is_file())
        with self.assertRaises(ValueError):
            requirements_review.require_approved(self.settings)


class BridgeReportFilesTests(unittest.TestCase):
    setUp = handoff.HandoffTests.setUp
    approve = handoff.HandoffTests.approve

    def test_only_new_review_reply_delivers_artifact_and_compact_preserves_it(self):
        result = self.approve('requirements')
        self.assertEqual(len(result['report_files']), 1)
        self.assertEqual(compact(result)['report_files'], result['report_files'])
        status = self.bridge.call('dot_status', {})
        self.assertNotIn('report_files', status)
        self.assertEqual(compact(status)['latest_review']['markdown_report'], result['report_files'][0])

    def test_submission_delivers_bound_final_markdown(self):
        self.approve('requirements')
        self.approve('plan')
        self.bridge.call('dot_run', {})
        result = self.bridge.call('l1_submit_report', {'summary': 'Checked fixture results', 'l1_session_id': 'author'})
        self.assertEqual(result['report_files'], [result['final_report']['markdown_report']])
        self.assertEqual(compact(result)['final_report']['markdown_report'], result['report_files'][0])

    def test_bridge_returns_completed_report_pending_then_only_repairs_file(self):
        with patch('dot_runtime.report_markdown.review', side_effect=OSError('fixture disk refusal')):
            result = self.approve('requirements')
        self.assertEqual(result['operation_status'], 'COMPLETED_REPORT_PENDING')
        self.assertEqual(result['report_files'], [])
        self.assertIn('Retry l1_approve', compact(self.bridge.status())['next'])
        result = self.approve('requirements')
        self.assertEqual(self.workers.calls, ['review'])
        self.assertEqual(len(result['report_files']), 1)


class NativeReportMarkdownTests(unittest.TestCase):
    setUp = onboarding.OnboardingTests.setUp
    request = onboarding.OnboardingTests.request
    prepare = onboarding.OnboardingTests.prepare
    confirm = onboarding.OnboardingTests.confirm
    allocate = onboarding.OnboardingTests.allocate

    def test_preflight_preview_and_confirmation_bind_the_shown_markdown(self):
        self.confirm()
        self.request('bridge', name='l1_stop', arguments={
            'reason': closure_reason('No feasible plan', ['settings-binding.json'])})
        submitted = self.request('bridge', name='l1_submit_report', arguments={'summary': 'Reviewed alternatives'})
        preview = self.request('report_preview')
        self.assertEqual(preview['markdown_report'], submitted['report_files'][0])
        path = Path(preview['markdown_report']['path'])
        original = path.read_bytes()
        path.write_text('Changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Markdown report changed'):
            self.request('report_confirm', sha256=preview['sha256'])
        self.assertFalse((self.task / 'preflight-review.json').exists())
        path.write_bytes(original)
        result = self.request('report_confirm', sha256=preview['sha256'])
        self.assertTrue(result['reviewed'])
        self.assertFalse(result['execution_authorized'])

    def test_preflight_submission_file_failure_and_retry_preserve_original_assessment(self):
        self.confirm()
        self.request('bridge', name='l1_stop', arguments={
            'reason': closure_reason('No feasible plan', ['settings-binding.json'])})
        with patch('dot_runtime.report_markdown.final', side_effect=OSError('fixture disk refusal')):
            result = self.request('bridge', name='l1_submit_report', arguments={'summary': 'Original preflight assessment'})
        self.assertEqual(result['operation_status'], 'COMPLETED_REPORT_PENDING')
        before = copy.deepcopy(result['preflight_report']['l1_submission'])
        with self.assertRaisesRegex(ValueError, 'Markdown report is pending'):
            self.request('report_preview')
        result = self.request('bridge', name='l1_submit_report', arguments={'summary': 'Do not overwrite original'})
        self.assertEqual(result['preflight_report']['l1_submission'], before)
        self.assertEqual(len(result['report_files']), 1)
        self.assertIsNotNone(self.request('report_preview')['markdown_report'])

    def test_native_preplan_report_repair_is_local_even_if_project_changed_after_review(self):
        from dot_runtime.bridge import Bridge
        workers = fixtures.FakeAdapter()
        with patch.object(onboarding.o, 'Bridge', side_effect=lambda settings: Bridge(settings, workers=workers)):
            self.confirm()
            self.allocate()
            self.request('bridge', name='l1_prepare_requirements', arguments={'requirements': 'Review input handling only.'})
            with patch('dot_runtime.report_markdown.review', side_effect=OSError('fixture disk refusal')):
                result = self.request('bridge', name='l1_approve', arguments={'kind': 'requirements'})
            self.assertEqual(compact(result)['operation_status'], 'COMPLETED_REPORT_PENDING')
            (self.project / 'input.txt').write_text('Changed after completed review', encoding='utf-8')
            result = self.request('bridge', name='l1_approve', arguments={'kind': 'requirements'})
            self.assertEqual(workers.calls, ['review'])
            self.assertEqual(len(result['report_files']), 1)
            with self.assertRaisesRegex(ValueError, 'Original project changed'):
                self.request('bridge', name='dot_plan', arguments={'requirements': 'Review input handling only.'})


if __name__ == '__main__':
    unittest.main()

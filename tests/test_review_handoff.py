"""Reproduce missing source packets, oversized replies, and wrong-phase approval."""
import copy
import json
from pathlib import Path
import sys
import unittest

import test_delegated_l1 as delegated
import test_runtime_controller as fixtures
from dot_runtime.controller import atomic_json, read_json, digest
from dot_runtime.bridge import Bridge
from dot_runtime.gpt_dot import ResponsesClient
from dot_runtime.presentation import compact, read_record
from dot_runtime.delegation import approve_l1


class HandoffTests(unittest.TestCase):
    def setUp(self):
        fixtures.ControllerTests.setUp(self)
        self.raw = self.root / 'raw.md'
        self.raw.write_text('Return 3. Python-only. No external actions.', encoding='utf-8')
        self.authorization = self.root / 'delegation.json'
        atomic_json(self.authorization, delegated.delegation(self.raw))
        self.plan_value = fixtures.fixture_plan()
        self.plan_value['verification']['test_command'] = [sys.executable, '-B', '-m', 'unittest', '-v']
        (self.project / 'test_value.py').write_text('import unittest\nfrom value import value\n'
            'class Test(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 3)\n')
        self.responses = [delegated.call_response('propose_plan', {'plan_json': json.dumps(self.plan_value)}),
                          delegated.call_response('run_approved_workflow')]
        def factory(model, path, envelope):
            return ResponsesClient(model, path, envelope, send=lambda *_: self.responses.pop(0))
        self.settings = {'project': str(self.project), 'control_dir': str(self.root / 'control'),
            'raw_input': str(self.raw), 'delegation': str(self.authorization), 'model': 'fixture-model',
            'environment_context': json.dumps({'inspection': {'ready': True, 'baseline': {'id': 'fixture'}}})}
        atomic_json(self.root / 'bridge.json', self.settings)
        atomic_json(self.root / 'confirmation.json', {'bridge_sha256': digest(self.settings),
                    'delegation_sha256': digest(read_json(self.authorization))})
        self.workers = fixtures.FakeAdapter()
        self.bridge = Bridge(self.settings, factory, self.workers)
        self.bridge.call('dot_plan', {'requirements': self.raw.read_text()})
        self.bridge.call('dot_initialize', {})

    def approve(self, kind):
        return self.bridge.call('l1_approve', {'kind': kind, 'l1_session_id': 'author'})

    def test_normal_path_with_source_receipts_and_real_test_process(self):
        self.assertEqual(self.approve('requirements')['state'], 'PLAN_PENDING')
        packet = self.bridge.run_dir / 'l1-review-inputs/1'
        self.assertTrue(read_json(packet / 'sources/inspection.json')['ready'])
        usage = read_json(packet / 'sources/usage.json')
        self.assertEqual(usage['actual_usage']['l1_reviews'], 1)
        self.assertEqual(usage['actual_usage']['implementation_attempts'], 0)
        self.assertEqual(usage['actual_usage']['api_calls'], 1)
        index = read_json(packet / 'source-index.json')
        self.assertTrue(any(x['path'].endswith('/templates/execution-plan.md') for x in index['records']))
        self.assertEqual(self.approve('plan')['state'], 'EXECUTING')
        result = self.bridge.call('dot_run', {})
        self.assertEqual(result['report']['outcome'], 'SUCCESS')
        self.assertEqual(result['final_report']['approved_test_commands'][0]['observed_test_count'], 1)
        self.assertEqual(result['revision_count'], 0)

    def test_wrong_phase_is_completed_refusal_then_can_approve(self):
        before = (self.bridge.run_dir / 'state.json').read_bytes()
        response = self.approve('plan')
        self.assertEqual(response['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual((self.bridge.run_dir / 'state.json').read_bytes(), before)
        self.assertEqual(self.workers.calls, [])
        self.assertEqual(self.approve('requirements')['state'], 'PLAN_PENDING')

    def test_rejected_review_cannot_be_followed_by_plan_approval(self):
        self.workers.overrides = [('review', fixtures.result('FAIL'))]
        self.assertEqual(self.approve('requirements')['state'], 'DECISION_PENDING')
        before = (self.bridge.run_dir / 'state.json').read_bytes()
        self.assertEqual(self.approve('plan')['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual(self.bridge.call('dot_run', {})['operation_status'], 'COMPLETED_REJECTED')
        self.assertEqual((self.bridge.run_dir / 'state.json').read_bytes(), before)
        self.assertEqual(self.workers.calls, ['review'])
        self.assertEqual(len(self.responses), 1)

    def test_packet_tampering_does_not_approve(self):
        def corrupt(role, workspace):
            (workspace / 'sources/usage.json').write_text('{}')
        self.workers.hook = corrupt
        self.assertEqual(self.approve('requirements')['state'], 'DECISION_PENDING')
        state = read_json(self.bridge.run_dir / 'state.json')
        self.assertFalse(state['approvals'])

    def test_large_history_keeps_latest_findings_before_host_spill(self):
        result = self.approve('requirements')
        large = copy.deepcopy(result)
        large['l1_reviews'] *= 20
        for item in large['l1_reviews']:
            item['result']['runtime_evidence'] = {'output': 'past output ' * 100000}
        large['l1_reviews'][-1]['result']['findings'] = ['Latest actionable source problem']
        before = copy.deepcopy(large)
        reply = compact(large)
        self.assertLess(len(json.dumps(reply)), 20000)
        self.assertEqual(reply['latest_review']['findings'], ['Latest actionable source problem'])
        self.assertEqual(reply['next'], 'l1_approve kind=plan')
        self.assertEqual(large, before)

    def test_extreme_latest_findings_are_explicitly_abridged(self):
        result = self.approve('requirements')
        result['l1_reviews'][-1]['result']['findings'] = ['finding ' * 2000 for _ in range(50)]
        reply = compact(result)
        self.assertLess(len(json.dumps(reply)), 20000)
        self.assertTrue(reply['details_abridged'])
        self.assertEqual(reply['latest_review']['record'], 'run/l1-reviews/1/result.json')

    def test_record_pages_reconstruct_full_original_without_writes(self):
        self.approve('requirements')
        name = 'run/l1-review-inputs/1/source-index.json'
        original = (self.bridge.root / name).read_bytes()
        pages, offset = [], 0
        while offset is not None:
            value = self.bridge.call('dot_read_record', {'path': name, 'offset': str(offset)})
            self.assertLessEqual(len(value['content']), 6000)
            pages.append(value['content']); offset = value['next_offset']
        self.assertEqual(''.join(pages), original.decode('utf-8'))
        self.assertEqual((self.bridge.root / name).read_bytes(), original)

    def test_review_without_result_file_links_to_recorded_state(self):
        result = self.approve('requirements')
        result['l1_reviews'][-1]['evidence_files'] = []
        self.assertEqual(compact(result)['latest_review']['record'], 'run/state.json')

    def test_recovery_history_is_compact_and_readable(self):
        result = self.approve('requirements')
        result['operation_recoveries'] = [{'original': 'recorded unresolved operation'}] * 20
        reply = compact(result)
        self.assertEqual(reply['operation_recoveries_count'], 20)
        self.assertNotIn('operation_recoveries', reply)
        from dot_runtime.controller import atomic_json
        atomic_json(self.bridge.root / 'operation-recovery-history.json', {'events': result['operation_recoveries']})
        record = self.bridge.call('dot_read_record', {'path': 'operation-recovery-history.json', 'offset': '0'})
        self.assertIn('recorded unresolved operation', record['content'])

    def test_record_rejects_traversal_secret_files_and_bad_offsets(self):
        for name in ('../delegation.json', 'run/l1-reviews/../../../../secret.json', 'api/secret-request.json',
                     'C:/secret.json', 'run\\state.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_record(self.bridge.root, name, '0')
        for offset in ('-1', '0.2', '', '１２'):
            with self.assertRaises(ValueError): read_record(self.bridge.root, 'plan.json', offset)

    def test_native_stop_records_can_be_read_without_granting_authority(self):
        from dot_runtime.controller import atomic_json
        for name in ('user-stop.json', 'user-stop-pending.json', 'user-stop-decisions/fixture.json'):
            path = self.bridge.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, {'fixture': 'Read-only data is not a native user decision'})
            result = read_record(self.bridge.root, name, '0')
            self.assertIn('Read-only data', result['content'])
            self.assertIn('not new authority', result['instructions'])

    def test_packet_and_record_reader_support_deep_windows_paths(self):
        from dot_runtime.review_packet import prepare
        from dot_runtime.snapshot import _filesystem_path
        from dot_runtime.controller import Controller
        c = Controller(self.bridge.run_dir)
        workspace = c.run_dir / 'l1-review-inputs' / ('long-' + 'x' * 180)
        _filesystem_path(workspace).mkdir(parents=True)
        def cleanup():
            import shutil
            if not workspace.resolve().is_relative_to(self.root.resolve()) or not workspace.name.startswith('long-'):
                raise AssertionError('Unexpected test cleanup path')
            shutil.rmtree(_filesystem_path(workspace))
        self.addCleanup(cleanup)
        entries = prepare(c, workspace, read_json(self.authorization), self.bridge.root, 1)
        deepest = max(entries, key=lambda item: len(item['path']))
        self.assertGreater(len(str(c.run_dir / deepest['path'])), 260)
        page = read_record(self.bridge.root, 'run/' + deepest['path'], '0')
        self.assertEqual(page['sha256'], deepest['sha256'])


if __name__ == '__main__':
    unittest.main()

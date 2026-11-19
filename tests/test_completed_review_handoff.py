"""Regression for persisted approval + oversized host reply + stale planning proof.

Native decisions and reviewers are fixtures; no paid models or product work.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_requirements_first as native
from test_resources import amendment
from test_delegated_l1 import call_response
from dot_runtime import operation_recovery as recovery, requirements_review as rr, resources
from dot_runtime.controller import atomic_json, digest, file_hash, read_json
from dot_runtime.presentation import compact


class CompletedReviewTests(unittest.TestCase):
    def setUp(self):
        native.NativeRequirementsFlowTests.setUp(self)
        from dot_runtime.controller import ROOT
        self.host['workflow_sha256'] = file_hash(ROOT / 'MANIFEST.json')
    request = native.NativeRequirementsFlowTests.request
    tool = native.NativeRequirementsFlowTests.tool
    confirmed = native.NativeRequirementsFlowTests.confirmed

    def approved(self):
        self.confirmed()
        self.root = self.task / 'control'
        self.tool('l1_prepare_requirements', requirements=self.text)
        return self.tool('l1_approve', kind='requirements')

    def test_separate_large_approval_copies_are_bounded_without_mutating_records(self):
        result = self.approved()
        for key in ('requirements_review', 'requirements_approval', 'requirements_preparation'):
            result[key] = copy.deepcopy(result['requirements_review'])
            result[key]['reviews'][0]['result']['runtime_evidence'] = {'output': '原始日志' * 900000}
        before = digest(result)
        reply = compact(result)
        self.assertLess(len(json.dumps(reply, ensure_ascii=False).encode()), 40000)
        self.assertEqual(before, digest(result))
        for key in ('requirements_review', 'requirements_approval', 'requirements_preparation'):
            self.assertEqual(reply[key]['review_count'], 1)
            self.assertEqual(reply[key]['latest_review']['status'], 'PASS')
            self.assertEqual(reply[key]['record'], rr.STATE)
        shared = result['requirements_review']
        alias_reply = compact({'state': 'REQUIREMENTS_APPROVED', 'requirements_review': shared,
                               'requirements_approval': shared})
        self.assertEqual(alias_reply['requirements_approval']['review_count'], 1)

    def test_current_approval_and_native_amendment_reach_dot_without_new_facts(self):
        self.draft['limits']['revisions'] = 5
        self.draft['hard_constraints'].append('Original revision ceiling is 5')
        self.approved()
        original = (self.task / 'delegation.json').read_bytes()
        revisions = rr.revision_count(self.root)
        updated = {**self.draft['limits'], 'revisions': 8}
        self.tool('l1_request_authorization', proposal_json=json.dumps(amendment({'limits': updated})))
        pending = self.request('authorization_preview')
        self.request('authorization_decide', sha256=pending['sha256'], approved=True)
        self.tool('l1_adjust_limits', proposal_json=json.dumps({**self.allocation, 'limits': updated}))
        # Previously returned NEEDS_INFO without the controller receipts. No
        # fabricated supplement is necessary to send the corrected packet once.
        from dot_runtime import clarifications
        atomic_json(self.root / 'planning-status.json', {'status': 'NEEDS_INFO',
            'base_request_sha256': digest({'requirements': self.text, 'facts': clarifications.signature(self.root)}),
            'request_sha256': 'old-incomplete-packet', 'gaps': []})
        captured = []
        def send(payload, _timeout):
            text = payload['input'][-1]['content']
            marker = 'CURRENT CONTROLLER HANDOFF (validated receipts, not new facts):\n'
            packet = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])[0]
            captured.append(packet)
            self.assertEqual(packet['requirements_approval']['review_number'], 1)
            self.assertEqual(packet['current_operating_limits']['revisions'], 8)
            self.assertEqual(packet['current_user_authority']['limits']['revisions'], 8)
            self.assertEqual(packet['approved_authorization_amendments'][0]['request_sha256'], pending['sha256'])
            self.assertIn('Original revision ceiling is 5', text)
            return call_response('request_clarification', {'reason': 'A genuine test-environment gap remains',
                'gaps': [{'name': 'test device', 'reason': 'Need source evidence', 'stage': 'planning'}]})
        from dot_runtime.bridge import Bridge
        from dot_runtime.gpt_dot import ResponsesClient
        bridge = Bridge(read_json(self.task / 'bridge.json'),
            lambda m, p, d: ResponsesClient(m, p, d, send), self.workers)
        bridge.call('dot_plan', {'requirements': self.text})
        again = bridge.call('dot_plan', {'requirements': self.text})
        self.assertIn('Unchanged', again['reason'])
        self.assertEqual(len(captured), 1)
        self.assertEqual(rr.revision_count(self.root), revisions)
        self.assertEqual(rr.review_count(self.root), 1)
        self.assertEqual(original, (self.task / 'delegation.json').read_bytes())

    def stopped_review(self):
        self.approved()
        value = rr._load(self.root)
        start = datetime.now(timezone.utc) - timedelta(minutes=2)
        stamp = lambda n: (start + timedelta(seconds=n)).isoformat()
        worker = self.root / 'requirements-review/outputs/1/process-record.json'
        atomic_json(worker, {'pid': 901234, 'status': 'EXITED', 'started_at': stamp(3),
                             'ended_at': stamp(4), 'process_tree_status': 'UNKNOWN'})
        value['reviews'][0]['at'] = stamp(2)
        value['approval']['at'] = stamp(5)
        value['reviews'][0]['evidence_files'].append({'path': worker.relative_to(self.root).as_posix(),
                                                   'sha256': file_hash(worker)})
        rr._save(self.root, value)
        atomic_json(self.task / 'host-process.json', {'pid': 901233, 'operation': 'l1_approve',
            'status': 'EXITED', 'started_at': stamp(1), 'ended_at': stamp(6), 'process_tree_status': 'UNKNOWN'})
        atomic_json(self.task / 'interrupted.json', {'name': 'l1_approve', 'at': stamp(0), 'status': 'UNKNOWN'})
        self.settings = read_json(self.task / 'bridge.json')
        self.marker = read_json(self.task / 'interrupted.json')
        for p in (patch.object(recovery, 'pid_status', return_value='STOPPED'),
                  patch.object(recovery, '_windows_process_inventory', return_value=[]),
                  patch.object(recovery, 'process_inventory', return_value=[])):
            p.start(); self.addCleanup(p.stop)

    def test_completed_review_native_recovery_preserves_approval_and_all_usage(self):
        self.stopped_review()
        proposal = {'reason': 'Host reply overflow after completed review', 'analysis': 'Completed approval and evidence verified',
            'resume_plan': 'Continue planning without another review', 'side_effects': 'Read-only review; native user verifies process evidence',
            'evidence': [{'path': rr.STATE, 'sha256': file_hash(self.root / rr.STATE), 'description': 'Persisted completed approval'}]}
        self.tool('l1_prepare_recovery', proposal_json=json.dumps(proposal))
        original = {name: (self.task / name).read_bytes() for name in
                    ('confirmation.json', 'control/' + rr.STATE, 'control/api/ledger.json', 'control/resources.json')}
        preview = self.request('operation_preview')
        self.assertTrue(preview['ready'])
        self.assertEqual(preview['processes']['status'], 'REQUIRES_NATIVE_ATTESTATION')
        self.assertTrue((self.task / 'interrupted.json').exists())
        recovered = self.request('operation_commit', sha256=preview['sha256'])
        self.assertIn('dot_plan', recovered['next'])
        for name, content in original.items(): self.assertEqual((self.task / name).read_bytes(), content)
        self.assertFalse((self.task / 'interrupted.json').exists())
        self.assertEqual(self.workers.calls, ['review'])
        self.tool('dot_plan', requirements=self.text)
        self.assertEqual(self.tool('dot_initialize')['state'], 'PLAN_PENDING')
        self.assertEqual(self.workers.calls, ['review'])

    def test_active_descendant_or_unqueryable_parent_cannot_be_approved(self):
        self.stopped_review()
        for observed in ('ACTIVE', 'UNKNOWN'):
            with self.subTest(observed=observed), patch.object(recovery, 'pid_status', return_value=observed), \
                    patch.object(recovery, 'process_creation_time', return_value=None):
                self.assertEqual(recovery.completed_review_processes(self.task, self.settings, None, self.marker)['status'], observed)
        rows = [{'pid': 902000, 'ppid': 901234, 'name': 'child'}]
        with patch.object(recovery, '_windows_process_inventory', return_value=rows), \
                patch.object(recovery, 'process_inventory', return_value=rows):
            self.assertEqual(recovery.completed_review_processes(self.task, self.settings, None, self.marker)['status'], 'ACTIVE')

    def test_pid_reuse_requires_os_creation_after_recorded_completion(self):
        self.stopped_review()
        with patch.object(recovery, 'pid_status', return_value='ACTIVE'), \
                patch.object(recovery, 'process_creation_time', return_value=datetime.now(timezone.utc).timestamp()):
            result = recovery.completed_review_processes(self.task, self.settings, None, self.marker)
            self.assertEqual(result['status'], 'REQUIRES_NATIVE_ATTESTATION')
            self.assertTrue(all(c['pid_reused'] for c in result['checks']))

    def test_stale_tampered_or_product_review_never_uses_completed_shortcut(self):
        self.stopped_review()
        self.assertIsNone(recovery.completed_review_processes(self.task, self.settings, {'state': 'PLAN_PENDING'}, self.marker))
        self.assertIsNone(recovery.completed_review_processes(self.task, self.settings, None, {**self.marker, 'at': datetime.now(timezone.utc).isoformat()}))
        evidence = self.root / 'requirements-review/outputs/1/process-record.json'
        evidence.write_text('{}')
        self.assertIsNone(recovery.completed_review_processes(self.task, self.settings, None, self.marker))

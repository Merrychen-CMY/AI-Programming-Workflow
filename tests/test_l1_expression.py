"""Selective writing adoption boundaries and wiring, not real-model language evaluation."""
from closure_fixtures import assessment, reason as closure_reason
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from dot_runtime import communication
from dot_runtime.controller import read_json
from dot_runtime.delegation import approve_l1, submit_l1_report
from dot_runtime.gpt_dot import ResponsesClient
from l1_bridge import dispatch
import test_delegated_l1 as delegated
import test_runtime_controller as fixtures


class L1ExpressionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.value = communication.load_policy()

    def test_full_skill_updates_and_global_scope_are_rejected(self):
        for key, invalid in [('adoption', 'full_upstream_skill'), ('scope', 'all_agents')]:
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression'][key] = invalid
            with self.subTest(field=key):
                self.assertTrue(communication.validate_communication_policy(changed))
        for key in ('full_upstream_skill_enabled', 'automatic_upstream_updates'):
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression']['boundaries'][key] = True
            with self.subTest(boundary=key):
                self.assertTrue(communication.validate_communication_policy(changed))

    def test_style_cannot_override_acceptance_evidence_or_authority(self):
        for key in ('style_may_change_acceptance_or_outcome',
                    'style_may_hide_failures_risks_or_unknowns',
                    'style_may_rewrite_structured_receipts_or_evidence',
                    'blanket_negative_word_ban', 'extra_user_approval_or_model_call_required',
                    'measured_effectiveness_claimed'):
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression']['boundaries'][key] = True
            with self.subTest(boundary=key):
                self.assertTrue(communication.validate_communication_policy(changed))
        changed = copy.deepcopy(self.value)
        changed['l1_user_expression']['boundaries'][
            'existing_requirements_evidence_authority_and_c_standards_take_precedence'] = False
        self.assertTrue(communication.validate_communication_policy(changed))

    def test_missing_malformed_or_weakened_profile_is_rejected(self):
        for invalid in (None, [], {}):
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression'] = invalid
            with self.subTest(profile=invalid):
                self.assertTrue(communication.validate_communication_policy(changed))
        for key in ('source', 'boundaries', 'rules'):
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression'][key] = None
            with self.subTest(field=key):
                self.assertTrue(communication.validate_communication_policy(changed))
        changed = copy.deepcopy(self.value)
        changed['l1_user_expression']['rules'][3]['level'] = 'MAY'
        self.assertTrue(communication.validate_communication_policy(changed))
        changed['l1_user_expression']['rules'].pop()
        self.assertTrue(communication.validate_communication_policy(changed))

    def test_unreviewed_source_and_missing_license_are_rejected_offline(self):
        changed = copy.deepcopy(self.value)
        changed['l1_user_expression']['source']['commit'] = 'main'
        self.assertTrue(communication.validate_communication_policy(changed))
        self.assertEqual(communication.validate_l1_source_notice(ROOT), [])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertTrue(communication.validate_l1_source_notice(root))
            notice = root / communication.L1_SOURCE['license_path']
            notice.parent.mkdir(parents=True)
            notice.write_text('Removed copyright', encoding='utf-8')
            self.assertTrue(communication.validate_l1_source_notice(root))

    def test_only_adopted_instructions_enter_l1_prompt(self):
        prompt = communication.l1_prompt()
        for n in range(1, 6):
            self.assertEqual(prompt.count(f'L1-EXPR-{n:02}'), 1)
        self.assertNotIn('L1-EXPR-', communication.prompt())
        for metadata in ('https://github.com/Adkid-Zephyr', communication.L1_SOURCE['commit'],
                         communication.L1_SOURCE['skill_sha256']):
            self.assertNotIn(metadata, prompt)

    def test_progress_format_rejects_missing_reordered_or_merged_headings(self):
        for invalid in (None, {}, [],
                        dict(communication.PROGRESS_FORMAT, headings=list(reversed(communication.PROGRESS_FORMAT['headings']))),
                        dict(communication.PROGRESS_FORMAT, headings=['【类型】【时间】', '【进度】'])):
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression']['progress_report'] = invalid
            with self.subTest(progress=invalid):
                self.assertTrue(communication.validate_communication_policy(changed))

    def test_progress_format_cannot_grant_authority_or_hide_required_fields(self):
        for key, invalid in [('silence_is_approval', True), ('new_execution_gate', True),
                             ('all_fields_required', False), ('headings_on_separate_lines', False),
                             ('timestamp_source', 'example_time'), ('next_step_field', '需要你'),
                             ('scope', 'all_agent_messages'), ('instructions', [])]:
            changed = copy.deepcopy(self.value)
            changed['l1_user_expression']['progress_report'][key] = invalid
            with self.subTest(field=key):
                self.assertTrue(communication.validate_communication_policy(changed))

    def test_progress_format_reaches_l1_and_template_but_not_dot(self):
        prompt = communication.l1_prompt()
        headings = communication.PROGRESS_FORMAT['headings']
        self.assertIn('\n'.join(headings), prompt)
        template = (ROOT / 'templates/l1-progress.md').read_text(encoding='utf-8')
        block = template.split('```text\n', 1)[1].split('```', 1)[0]
        actual = [line for line in block.splitlines() if line.startswith('【')]
        self.assertEqual(actual, headings)
        for instruction in self.value['l1_user_expression']['progress_report']['instructions']:
            self.assertIn(instruction, prompt)
            self.assertNotIn(instruction, communication.prompt())
        for path in ['AGENTS.md', 'L1.md', 'harness/prompt.md']:
            content = (ROOT / path).read_text(encoding='utf-8')
            self.assertIn('templates/l1-progress.md', content)
            for heading in headings:
                self.assertIn(heading, content)
        index = read_json(ROOT / 'INDEX.json')
        self.assertIn('templates/l1-progress.md', index['role_reading']['L1'])

    def test_progress_examples_do_not_claim_user_example_as_executed_evidence(self):
        prompt = communication.l1_prompt()
        template = (ROOT / 'templates/l1-progress.md').read_text(encoding='utf-8')
        for sample in ['202611191419', '四组host 基线测试已全部通过', 'obj/_rt_entry.S']:
            self.assertNotIn(sample, prompt)
            self.assertNotIn(sample, template)


class L1ExpressionRuntimeTests(unittest.TestCase):
    setUp = delegated.DelegatedTests.setUp
    controller = fixtures.ControllerTests.controller
    approved = delegated.DelegatedTests.approved

    def test_l1_initialize_receives_scoped_rules_without_actions(self):
        stub = Mock()
        response = dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, stub)
        content = response['result']['instructions']
        self.assertIn(communication.l1_prompt(), content)
        self.assertIn('L1-EXPR-04', content)
        self.assertEqual(response['result']['capabilities'], {'tools': {}})
        self.assertEqual(stub.mock_calls, [])

    def test_independent_review_keeps_its_veto_with_expression_guidance(self):
        reviewer = fixtures.FakeAdapter([('review', fixtures.result('FAIL'))])
        captured = []
        run = reviewer.run
        def record(role, prompt, workspace, output, timeout):
            captured.append(prompt)
            return run(role, prompt, workspace, output, timeout)
        with patch.object(reviewer, 'run', side_effect=record):
            approve_l1(self.c, 'requirements', 'l1-author', reviewer)
        self.assertIn(communication.l1_prompt(), captured[0])
        self.assertEqual(reviewer.calls, ['review'])
        self.assertEqual(self.c.state['state'], 'DECISION_PENDING')
        self.assertNotIn('requirements', self.c.state['approvals'])

    def test_dot_keeps_shared_prompt_without_l1_style_or_extra_call(self):
        send = Mock(return_value=delegated.call_response('stop', {'reason': 'Fixture'}))
        client = ResponsesClient('configured-model', self.root / 'api', delegated.delegation(self.raw), send=send)
        original = 'AC: latency <=100 ms; observed 130 ms; FAILED; do not reframe.'
        client.request(original)
        send.assert_called_once()
        payload = send.call_args.args[0]
        self.assertIn(communication.prompt(), payload['input'][0]['content'])
        self.assertNotIn('L1-EXPR-', payload['input'][0]['content'])
        self.assertEqual(payload['input'][1]['content'], original)

    def test_expression_change_invalidates_old_approval_before_execution(self):
        path = self.root / 'communication.json'
        path.write_bytes(communication.POLICY.read_bytes())
        with patch.object(communication, 'POLICY', path):
            c = self.approved()
            calls = list(c.adapter.calls)
            value = read_json(path)
            value['l1_user_expression']['rules'][0]['instruction'] += ' Additional reviewed guidance.'
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(c.run()['state'], 'DECISION_PENDING')
            self.assertEqual(c.adapter.calls, calls)

    def test_expression_change_invalidates_frozen_candidate(self):
        path = self.root / 'communication.json'
        path.write_bytes(communication.POLICY.read_bytes())
        with patch.object(communication, 'POLICY', path):
            c = self.approved()
            c.make_candidate()
            value = read_json(path)
            value['l1_user_expression']['rules'][4]['instruction'] += ' Additional reviewed guidance.'
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Candidate.*stale'):
                c.check_candidate()

    def test_invalid_profile_prevents_initialize_and_api_dispatch(self):
        path = self.root / 'communication.json'
        value = communication.load_policy()
        value['l1_user_expression']['boundaries']['style_may_change_acceptance_or_outcome'] = True
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        send, stub = Mock(), Mock()
        client = ResponsesClient('configured-model', self.root / 'api', delegated.delegation(self.raw), send=send)
        with patch.object(communication, 'POLICY', path):
            response = dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, stub)
            self.assertIn('error', response)
            with self.assertRaises(ValueError):
                client.request('Fixture')
        send.assert_not_called()
        self.assertEqual(stub.mock_calls, [])

    def assert_report_preserved(self, summary):
        path = self.run_dir / 'final-report.json'
        original = read_json(path)
        archived_bytes = path.read_bytes()
        calls = list(self.c.adapter.calls)
        submit_l1_report(self.c, summary, 'l1-author')
        submitted = read_json(path)
        submission = submitted.pop('l1_submission')
        self.assertEqual(submitted, original)
        self.assertEqual(submission['outcome'], original['outcome'])
        self.assertEqual(submission['summary'], summary)
        self.assertEqual(self.c.adapter.calls, calls)
        self.assertEqual(self.c.state['state'], 'REPORT_PENDING')
        self.assertNotIn('report', self.c.state['approvals'])
        self.assertTrue(any(p.read_bytes() == archived_bytes for p in (self.run_dir / 'reports').glob('*.json')))

    def test_success_summary_preserves_prior_failures_and_all_evidence(self):
        self.c.adapter = fixtures.FakeAdapter([
            ('test', fixtures.result('FAIL', 'implementation')),
            ('triage', fixtures.result('PASS', 'implementation'))])
        c = self.approved()
        c.run()
        self.assertEqual(c.state['report']['outcome'], 'SUCCESS')
        self.assertGreater(c.state['implementation_attempts'], 1)
        self.assert_report_preserved('Final candidate passed after repair; earlier failure and consumption remain in the attached record.')

    def test_failed_summary_preserves_original_report_and_does_not_approve(self):
        self.c.report('FAILED', 'AC <=100 ms; observed 130 ms', l1_assessment=assessment())
        self.assert_report_preserved('FAILED: latency exceeds the approved threshold; no acceptance change.')

    def test_unknown_summary_preserves_original_report_without_replay(self):
        self.c.report('UNKNOWN', 'Execution result cannot be verified', l1_assessment=assessment())
        self.assert_report_preserved('UNKNOWN: result remains unverified; no replay or delivery performed.')

    def test_incomplete_summary_preserves_unexecuted_required_check(self):
        self.c.report('INCOMPLETE', 'Required board test NOT_RUN', l1_assessment=assessment())
        self.assert_report_preserved('INCOMPLETE: required board test remains NOT_RUN; host tests alone are insufficient.')


if __name__ == '__main__':
    unittest.main()

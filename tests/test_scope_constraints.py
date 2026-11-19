"""Scope rules reach actual CLI/API inputs; model behavior itself is not certified."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from dot_runtime import adapter, requirements_review, scope_constraints
from dot_runtime.delegation import approve_l1
from dot_runtime.gpt_dot import ResponsesClient, propose_plan
import test_runtime_adapter as adapter_fixtures
import test_runtime_controller as controller_fixtures
import test_delegated_l1 as delegated_fixtures
import test_requirements_first as requirements_fixtures
from test_delegated_l1 import call_response, delegation


class WorkerScopeTests(unittest.TestCase):
    setUp = adapter_fixtures.AdapterTests.setUp
    fake_popen = adapter_fixtures.AdapterTests.fake_popen
    fake_session = adapter_fixtures.AdapterTests.fake_session
    run_adapter = adapter_fixtures.AdapterTests.run_adapter

    def test_all_worker_roles_receive_scope_and_original_minimum_change_text(self):
        for role in adapter.ROLES:
            with self.subTest(role=role):
                self.output = self.root / 'runs' / role
                if role == 'review':
                    self.report['evidence'] = [{'kind': 'review', 'description': 'Read value.py',
                        'command': '', 'output': 'value.py meets assigned AC', 'exit_code': 0,
                        'test_count': 0}]
                if role == 'triage':
                    self.report.update(controller_fixtures.triage_fields('needs_analysis'))
                self.assertEqual(self.run_adapter(role)['status'], 'PASS')
                self.assertEqual(self.inputs[-1].count(scope_constraints.MINIMUM_CHANGE), 1)
                self.assertIn(scope_constraints.ROLE_RULES[role], self.inputs[-1])
                self.assertIn('allowed objects/paths', self.inputs[-1])
                self.assertIn('smallest relevant lookup', self.inputs[-1])
                self.assertIn('Stop when the assigned acceptance conditions are met', self.inputs[-1])
                self.assertEqual((self.output / 'prompt.txt').read_text(encoding='utf-8'), self.inputs[-1])

    def test_implementation_role_does_not_expand_diagnosis_request_authority(self):
        self.run_adapter('implementation')
        self.assertIn('do not edit code or run tests for that request', self.inputs[0])
        self.assertIn('A role label is not extra authority', self.inputs[0])
        self.assertIn('do not add unrelated tests', self.inputs[0])

    def test_review_cannot_run_tests_merely_because_plan_contains_them(self):
        self.run_adapter('review')
        self.assertIn('do not edit code, run builds or run tests', self.inputs[0])
        self.assertIn('Do not run tests merely because the reviewed plan lists them', self.inputs[0])
        self.assertNotIn('Run only the tests and commands authorized', self.inputs[0])
        self.assertEqual(self.sessions[0]['sandbox'], 'read-only')

    def test_test_assignment_keeps_authorized_verification_without_extra_work(self):
        self.run_adapter('test')
        self.assertIn('Run only the tests and commands authorized by the approved verification plan', self.inputs[0])
        self.assertIn('Do not add tests, change product code or repair failures', self.inputs[0])
        self.assertNotIn('Do not run tests merely because the reviewed plan lists them', self.inputs[0])

    def test_nonblocking_observations_do_not_authorize_repairs_or_weaken_failure_gate(self):
        self.run_adapter('implementation')
        self.assertIn('explicitly nonblocking observations', self.inputs[0])
        self.assertIn('for L1 to present to the user; do not repair them', self.inputs[0])
        self.assertIn('Keep acceptance failures in findings', self.inputs[0])
        self.assertIn('Never hide a required failure', self.inputs[0])
        self.output = self.root / 'runs' / 'failure'
        self.report['findings'] = ['Required AC does not hold']
        self.assertEqual(self.run_adapter('implementation')['status'], 'ERROR')


def captured_review_session(captured):
    """A fake Codex transport with no commands and a distinct session each call."""
    def run_session(process, **options):
        session = 'scope-review-' + str(len(captured) + 1)
        report = {'status': 'PASS', 'summary': 'Scoped source review completed', 'findings': [],
                  'root_cause': 'needs_analysis', 'root_cause_key': '', 'evidence': [
                      {'kind': 'review', 'description': 'Inspected requirements.md and raw-input.md',
                       'command': '', 'output': 'The recorded required result and scope match.',
                       'exit_code': 0, 'test_count': 0}]}
        output = options['output_dir']
        (output / 'result.json').write_text(json.dumps(report), encoding='utf-8')
        (output / 'events.jsonl').write_text(json.dumps({'type': 'thread.started', 'thread_id': session}) + '\n'
            + json.dumps({'type': 'turn.completed', 'usage': {}}) + '\n', encoding='utf-8')
        captured.append(options['instructions'])
    return run_session


def review_process():
    process = Mock(pid=43210, returncode=0)
    process.poll.return_value = 0
    return process


class PreplanReviewScopeTests(unittest.TestCase):
    setUp = requirements_fixtures.RequirementsFirstTests.setUp

    def test_native_requirements_review_receives_read_only_scope_at_cli_boundary(self):
        captured = []
        requirements_review.prepare(self.settings, self.text)
        with patch.object(adapter.subprocess, 'Popen', side_effect=lambda *_, **kwargs: review_process()), \
                patch.object(adapter, 'run_session', side_effect=captured_review_session(captured)), \
                patch('dot_runtime.operation_recovery.boot_identity', return_value='fixture-boot'):
            result = requirements_review.approve(self.settings, 'l1-author', adapter.CodexAdapter('fixture-codex'))
        self.assertEqual(result['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(len(captured), 1)
        self.assertIn('requirements before DOT planning', captured[0])
        self.assertIn(scope_constraints.prompt('review'), captured[0])
        self.assertEqual(json.loads((self.control / 'api/ledger.json').read_text(encoding='utf-8'))['attempts'], [])


class PlanAndApiScopeTests(unittest.TestCase):
    setUp = delegated_fixtures.DelegatedTests.setUp
    controller = delegated_fixtures.DelegatedTests.controller

    def test_independent_post_initialize_requirements_and_plan_reviews_receive_same_rules(self):
        captured = []
        with patch.object(adapter.subprocess, 'Popen', side_effect=lambda *_, **kwargs: review_process()), \
                patch.object(adapter, 'run_session', side_effect=captured_review_session(captured)), \
                patch('dot_runtime.operation_recovery.boot_identity', return_value='fixture-boot'):
            reviewer = adapter.CodexAdapter('fixture-codex')
            approve_l1(self.c, 'requirements', 'l1-author', reviewer)
            approve_l1(self.c, 'plan', 'l1-author', reviewer)
        self.assertEqual(self.c.state['state'], 'EXECUTING')
        self.assertEqual(len(captured), 2)
        for content in captured:
            self.assertIn(scope_constraints.prompt('review'), content)
            self.assertIn('Do not execute project commands', content)

    def test_dot_planner_gets_scope_rules_in_developer_message_without_extra_api_call(self):
        send = Mock(return_value=call_response('propose_plan', {'plan_json': json.dumps(controller_fixtures.fixture_plan())}))
        client = ResponsesClient('fixture-model', self.root / 'scope-api', delegation(self.raw), send=send)
        plan = propose_plan(client, 'Implement the specified value only', controller_fixtures.fixture_plan(),
                            'Recorded fixture environment')
        self.assertEqual(plan['allowed_paths'], ['value.py'])
        self.assertEqual(send.call_count, 1)
        payload = send.call_args.args[0]
        self.assertEqual(payload['input'][0]['role'], 'developer')
        self.assertIn(scope_constraints.prompt('dot'), payload['input'][0]['content'])
        self.assertEqual(payload['input'][0]['content'].count(scope_constraints.MINIMUM_CHANGE), 1)
        self.assertIn('Keep explanation/diagnosis/review assignments read-only', payload['input'][0]['content'])
        self.assertIn('Implement the specified value only', payload['input'][1]['content'])
        self.assertEqual(self.c.state['implementation_attempts'], 0)


if __name__ == '__main__':
    unittest.main()

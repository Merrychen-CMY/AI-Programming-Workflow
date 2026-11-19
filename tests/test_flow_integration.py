"""Cross-module flow, isolated files and real unittest subprocess; all AI is simulated."""
import copy
import json
import sys
import unittest

import test_requirements_first as initial
import test_runtime_controller as fixtures
from test_delegated_l1 import call_response
from test_resources import allocation
from test_investigation_resume import diagnosis
from closure_fixtures import reason
from dot_runtime.bridge import Bridge
from dot_runtime.controller import Controller, atomic_json, read_json
from dot_runtime.gpt_dot import ResponsesClient


def triage_response(cause='implementation', **updates):
    return {'id': 'fixture-triage-response', 'status': 'completed', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(diagnosis(cause, **updates))}]}]}


class CompleteFlowTests(unittest.TestCase):
    def setUp(self):
        initial.RequirementsFirstTests.setUp(self)
        self.original = read_json(self.authority)
        self.original['adaptive_limits'] = True
        self.original['limits'].update(l1_reviews=12, revisions=8, api_calls=20)
        atomic_json(self.authority, self.original)
        self.settings['requirements_first'] = True
        self.plan = fixtures.fixture_plan()
        self.plan['verification']['test_command'] = [sys.executable, '-B', '-m', 'unittest', '-v']
        (self.project / 'test_value.py').write_text(
            'import unittest\nfrom value import value\nclass Test(unittest.TestCase):\n'
            '    def test_value(self):\n        self.assertEqual(value(), 3)\n', encoding='utf-8')
        self.responses = []
        self.payloads = []
        def send(payload, timeout):
            self.payloads.append(payload)
            return self.responses.pop(0)
        self.bridge = Bridge(self.settings, workers=self.workers,
            client_factory=lambda m, p, d: ResponsesClient(m, p, d, send))
        self.assertTrue(self.allocate()['accepted'])

    def allocate(self, **limits):
        operating = dict(self.original['limits'], **limits)
        return self.bridge.call('l1_adjust_limits', {'proposal_json': json.dumps(allocation(operating)),
                                                    'l1_session_id': 'host-L1'})

    def prepared(self):
        b = self.bridge
        b.call('l1_prepare_requirements', {'requirements': self.text})
        b.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'host-L1'})
        self.assertEqual(self.workers.calls, ['review'])
        self.assertFalse(self.payloads, 'DOT planning must follow independent requirements approval')
        self.responses.append(call_response('propose_plan', {'plan_json': json.dumps(self.plan)}))
        b.call('dot_plan', {'requirements': self.text})
        self.assertEqual(b.call('dot_initialize', {})['state'], 'PLAN_PENDING')
        self.assertEqual(self.workers.calls, ['review'], 'Initialization carries the real prior review; no duplicate review')
        b.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'host-L1'})
        return b, Controller(b.run_dir)

    def test_paused_problem_can_investigate_and_l1_resume_through_bridge(self):
        b, c = self.prepared()
        c.request_l1_assessment('Recorded failure requires a bounded L2 solution')
        self.responses.append(triage_response())
        state = b.call('dot_investigate', {'investigation_json': json.dumps({
            'question': 'Provide an authorized repair and validation proposal', 'evidence': ['run/decision-request.json']})})
        self.assertEqual(state['state'], 'DECISION_PENDING')
        self.assertEqual(self.workers.calls, ['review', 'review'])
        self.assertEqual(state['implementation_attempts'], 0)
        state = b.call('l1_resume_decision', {'decision_json': json.dumps({'action': 'repair',
            'reason': 'The proposal fits the approved value.py scope and resources', 'evidence': ['run/decision-request.json']})})
        self.assertEqual(state['state'], 'EXECUTING')
        self.assertEqual(self.workers.calls, ['review', 'review'])
        self.responses.append(call_response('run_approved_workflow'))
        state = b.call('dot_run', {})
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        self.assertEqual(state['implementation_attempts'], 1)
        report = read_json(b.run_dir / 'final-report.json')
        self.assertEqual(report['approved_test_commands'][-1]['observed_test_count'], 1)
        self.assertEqual(len(report['l1_reviews']), 2)

    def test_exhausted_implementation_allocation_allows_verification_only_revision_without_redispatch(self):
        b, c = self.prepared()
        self.workers.overrides = [('review', fixtures.result('FAIL', 'verification_plan_or_test'))]
        self.responses.extend([call_response('run_approved_workflow'), triage_response('verification_plan_or_test')])
        state = b.call('dot_run', {})
        self.assertEqual(state['state'], 'DECISION_PENDING')
        self.assertEqual(state['implementation_attempts'], 1)
        before_calls = list(self.workers.calls)
        self.assertTrue(self.allocate(implementation_attempts=1)['accepted'])
        revised = copy.deepcopy(self.plan)
        revised['budgets']['implementation_attempts'] = 1
        revised['verification']['review_instructions'] += '; cite the independently tested requirement explicitly'
        self.responses.append(call_response('propose_plan', {'plan_json': json.dumps(revised)}))
        proposal = b.call('dot_plan', {'requirements': self.text})
        self.assertIn('plan', proposal)
        state = b.call('l1_revise', {'requirements': self.text, 'plan_json': json.dumps(revised)})
        self.assertEqual(state['state'], 'REVIEW_PENDING')
        c = Controller(b.run_dir)
        self.assertEqual(c.state['completed_tasks'], ['build'])
        self.assertIsNone(c.state['candidate'])
        for kind in ('requirements', 'plan'):
            b.call('l1_approve', {'kind': kind, 'l1_session_id': 'host-L1'})
        self.responses.append(call_response('run_approved_workflow'))
        state = b.call('dot_run', {})
        self.assertEqual(state['report']['outcome'], 'SUCCESS')
        self.assertEqual(state['implementation_attempts'], 1)
        self.assertNotIn('implementation', self.workers.calls[len(before_calls):])
        self.assertEqual(len(read_json(b.run_dir / 'final-report.json')['approved_test_commands']), 2)

    def test_initial_final_failure_requires_l1_submission_before_user_review(self):
        b = self.bridge
        b.call('l1_prepare_requirements', {'requirements': self.text})
        state = b.call('l1_stop', {'reason': reason(evidence=['requirements.md'])})
        self.assertEqual(state['state'], 'REPORT_PENDING')
        self.assertFalse(state['report']['l1_submitted'])
        state = b.call('l1_submit_report', {'summary': 'L1 checked recorded alternatives; this fixture is intentionally closed',
                                           'l1_session_id': 'host-L1'})
        self.assertTrue(state['report']['l1_submitted'])
        self.assertEqual(state['preflight_report']['l1_submission']['outcome'], 'FAILED')
        self.assertTrue(list((b.root / 'preflight-reports').glob('l1-*.json')))
        self.assertEqual(self.workers.calls, [])


if __name__ == '__main__':
    unittest.main()

"""Same-task recovery with real ledgers and mock models; no live API/firmware claims."""
import copy
import json
import time
import unittest
import sys

import test_runtime_controller as fixtures
import test_delegated_l1 as delegated
from dot_runtime.bridge import Bridge
from dot_runtime.controller import Controller, atomic_json, digest, read_json
from dot_runtime.gpt_dot import ResponsesClient
from dot_runtime import clarifications


def supplement(value='fixture-5.0'):
    return {'reason': 'Clarify existing environment without changing the target', 'facts': [
        {'name': 'ESP-IDF version', 'value': value, 'source': 'User clarification message; not a tool probe', 'stage': 'planning'}]}


def needs_info():
    return delegated.call_response('request_clarification', {'reason': 'Compiler version needed',
        'gaps': [{'name': 'ESP-IDF version', 'reason': 'Select compatible interfaces', 'stage': 'planning'}]})


class ClarificationTests(unittest.TestCase):
    controller = fixtures.ControllerTests.controller
    setUp = delegated.DelegatedTests.setUp

    def bridge(self, responses):
        self.requests = []
        self.responses = list(responses)
        def send(payload, timeout):
            self.requests.append(payload)
            return self.responses.pop(0)
        settings = {'project': str(self.project), 'control_dir': str(self.root / 'bridge'),
                    'raw_input': str(self.raw), 'delegation': str(self.delegation_path),
                    'model': 'fixture-model', 'environment_context': 'Original compiler unknown'}
        return Bridge(settings, workers=fixtures.FakeAdapter(), client_factory=lambda m, p, d: ResponsesClient(m, p, d, send))

    def plan_response(self):
        return delegated.call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})

    def add(self, bridge, value=None):
        return bridge.call('l1_add_facts', {'facts_json': json.dumps(value or supplement()), 'l1_session_id': 'native-L1'})

    def test_needs_info_is_recoverable_and_does_not_dispatch(self):
        bridge = self.bridge([needs_info()])
        result = bridge.call('dot_plan', {'requirements': 'original requirements'})
        self.assertEqual(result['state'], 'NEEDS_INFO')
        self.assertEqual(result['planning']['gaps'][0]['stage'], 'planning')
        self.assertFalse((bridge.root / 'preflight-report.json').exists())
        self.assertFalse(bridge.workers.calls)
        self.assertEqual(read_json(bridge.root / 'api/ledger.json')['attempts'][0]['status'], 'COMPLETED')

    def test_identical_input_does_not_blindly_repeat_api(self):
        bridge = self.bridge([needs_info()])
        bridge.call('dot_plan', {'requirements': 'original requirements'})
        result = bridge.call('dot_plan', {'requirements': 'original requirements'})
        self.assertFalse(result['accepted'])
        self.assertEqual(len(self.requests), 1)

    def test_clarify_replan_initialize_preserves_authority_time_and_attempts(self):
        bridge = self.bridge([needs_info(), self.plan_response()])
        original = (self.raw.read_bytes(), self.delegation_path.read_bytes())
        bridge.call('dot_plan', {'requirements': 'original requirements'})
        ledger_before = read_json(bridge.root / 'api/ledger.json')
        binding = (bridge.root / 'settings-binding.json').read_bytes()
        self.assertTrue(self.add(bridge)['accepted'])
        self.assertEqual(read_json(bridge.root / 'api/ledger.json'), ledger_before)
        bridge.call('dot_plan', {'requirements': 'original requirements'})
        self.assertIn('fixture-5.0', json.dumps(self.requests[-1]))
        self.assertIn('User clarification message', json.dumps(self.requests[-1]))
        bridge.call('dot_initialize', {})
        state = Controller(bridge.run_dir).state
        self.assertEqual(state['revision_count'], 1)
        self.assertEqual(state['implementation_attempts'], 0)
        self.assertEqual(state['delegated_started_at'], ledger_before['started_at'])
        self.assertEqual(len(read_json(bridge.root / 'api/ledger.json')['attempts']), 2)
        self.assertEqual(binding, (bridge.root / 'settings-binding.json').read_bytes())
        self.assertEqual(original, (self.raw.read_bytes(), self.delegation_path.read_bytes()))
        bridge.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'author'})
        review_input = bridge.run_dir / 'l1-review-inputs/1/fact-supplements.json'
        self.assertIn('fixture-5.0', review_input.read_text())
        self.assertIn('clarifications', Controller(bridge.run_dir).state['approvals']['requirements']['baseline'])

    def test_facts_invalidate_previously_drafted_plan(self):
        bridge = self.bridge([self.plan_response()])
        bridge.call('dot_plan', {'requirements': 'requirements'})
        self.add(bridge)
        self.assertFalse(bridge.status()['plan_ready'])
        self.assertFalse(bridge.call('dot_initialize', {})['accepted'])
        self.assertFalse((bridge.run_dir / 'state.json').exists())

    def test_invalid_or_authority_changing_structure_is_rejected_without_record(self):
        bridge = self.bridge([])
        for value in [dict(supplement(), limits={'api_calls': 999}),
                      {'reason': 'x', 'facts': []}, supplement('UNKNOWN'), supplement('REPLACE_WITH_VERSION')]:
            result = self.add(bridge, value)
            self.assertFalse(result['accepted'])
        self.assertFalse(clarifications.records(bridge.root))
        self.assertFalse(self.requests)

    def test_facts_consume_revision_budget_across_bridge_restarts(self):
        bridge = self.bridge([])
        self.assertTrue(self.add(bridge)['accepted'])
        restart = Bridge(bridge.settings, client_factory=bridge.client_factory, workers=bridge.workers)
        self.assertTrue(self.add(restart, supplement('fixture-5.1'))['accepted'])
        self.assertFalse(self.add(restart, supplement('fixture-5.2'))['accepted'])
        self.assertEqual(len(clarifications.records(bridge.root)), 2)

    def test_duplicate_facts_do_not_consume_a_second_revision(self):
        bridge = self.bridge([])
        self.assertTrue(self.add(bridge)['accepted'])
        self.assertFalse(self.add(bridge)['accepted'])
        self.assertEqual(len(clarifications.records(bridge.root)), 1)

    def test_recovered_flow_runs_real_fixture_check_and_reports_fact_history(self):
        (self.project / 'test_value.py').write_text(
            'import unittest\nfrom value import value\nclass ValueTest(unittest.TestCase):\n'
            '    def test_value(self):\n        self.assertEqual(value(), 3)\n', encoding='utf-8')
        plan = fixtures.fixture_plan()
        plan['verification']['test_command'] = [sys.executable, '-B', '-m', 'unittest', '-v']
        bridge = self.bridge([needs_info(), delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)}),
                              delegated.call_response('run_approved_workflow')])
        bridge.call('dot_plan', {'requirements': 'fixture value 3'})
        self.add(bridge)
        bridge.call('dot_plan', {'requirements': 'fixture value 3'})
        bridge.call('dot_initialize', {})
        for kind in ('requirements', 'plan'):
            bridge.call('l1_approve', {'kind': kind, 'l1_session_id': 'author'})
        status = bridge.call('dot_run', {})
        self.assertEqual(status['report']['outcome'], 'SUCCESS')
        self.assertEqual(status['final_report']['approved_test_commands'][0]['observed_test_count'], 1)
        self.assertEqual(len(status['final_report']['fact_supplements']), 1)
        self.assertEqual(len(read_json(bridge.root / 'api/ledger.json')['attempts']), 3)

    def test_machine_policy_cannot_allow_reset_or_skip_review(self):
        sys.path.insert(0, str(fixtures.ROOT / 'scripts'))
        from validate_repo import validate_clarification_policy
        for path in ('policy/onboarding.json', 'policy/simple-flow.json', 'policy/workflow.json'):
            value = read_json(fixtures.ROOT / path)['clarification_policy']
            self.assertEqual(validate_clarification_policy(value), [])
            for key in ('budget_reset_allowed', 'expiry_extension_allowed', 'unknown_or_final_report_auto_resume',
                        'requires_independent_baseline_review', 'same_task_supplements_allowed'):
                changed = copy.deepcopy(value)
                changed[key] = not changed[key]
                self.assertTrue(validate_clarification_policy(changed))

    def test_wall_time_is_not_reset_while_waiting_for_facts(self):
        bridge = self.bridge([needs_info()])
        bridge.call('dot_plan', {'requirements': 'x'})
        ledger = read_json(bridge.root / 'api/ledger.json')
        ledger['started_at'] = time.time() - 4000
        atomic_json(bridge.root / 'api/ledger.json', ledger)
        self.assertFalse(self.add(bridge)['accepted'])
        self.assertEqual(read_json(bridge.root / 'api/ledger.json'), ledger)

    def test_unknown_api_attempt_blocks_supplement(self):
        bridge = self.bridge([needs_info()])
        bridge.call('dot_plan', {'requirements': 'x'})
        ledger = read_json(bridge.root / 'api/ledger.json')
        ledger['attempts'][0]['status'] = 'FAILED_OR_UNKNOWN'
        atomic_json(bridge.root / 'api/ledger.json', ledger)
        self.assertFalse(self.add(bridge)['accepted'])

    def test_final_stop_remains_final(self):
        bridge = self.bridge([delegated.call_response('stop', {'reason': 'Outside authority'})])
        with self.assertRaises(ValueError):
            bridge.call('dot_plan', {'requirements': 'x'})
        with self.assertRaisesRegex(ValueError, 'no automatic resumption'):
            self.add(bridge)

    def test_fact_history_tampering_is_detected(self):
        bridge = self.bridge([])
        self.add(bridge)
        index = read_json(bridge.root / 'facts-index.json')
        path = bridge.root / 'facts' / index[0]['file']
        value = read_json(path)
        value['supplement']['facts'][0]['value'] = 'tampered'
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, 'history changed'):
            bridge.status()

    def test_paused_initialized_task_requires_revision_and_fresh_approvals(self):
        bridge = self.bridge([self.plan_response(), self.plan_response()])
        bridge.call('dot_plan', {'requirements': 'requirements'})
        bridge.call('dot_initialize', {})
        bridge.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'author'})
        self.assertTrue(self.add(bridge)['accepted'])
        self.assertFalse(bridge.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'author'})['accepted'])
        proposed = bridge.call('dot_plan', {'requirements': 'revised with environment'})
        bridge.call('l1_revise', {'requirements': 'revised with environment', 'plan_json': json.dumps(proposed['plan'])})
        state = Controller(bridge.run_dir).state
        self.assertEqual(state['revision_count'], 1)  # One supplement/revision transaction, not two.
        self.assertEqual(len(state['l1_reviews']), 1)
        self.assertFalse(state['approvals'])
        self.assertFalse(state['pending_clarification_revision'])
        for kind in ('requirements', 'plan'):
            bridge.call('l1_approve', {'kind': kind, 'l1_session_id': 'author'})
        Controller(bridge.run_dir).check_approvals()
        self.assertEqual(Controller(bridge.run_dir).state['state'], 'EXECUTING')
        self.assertFalse(self.add(bridge)['accepted'])

    def test_unresolved_worker_forbids_facts(self):
        bridge = self.bridge([self.plan_response()])
        bridge.call('dot_plan', {'requirements': 'requirements'})
        bridge.call('dot_initialize', {})
        c = Controller(bridge.run_dir)
        c.transition('DECISION_PENDING', 'fixture')
        c.state['active_run'] = {'role': 'implementation'}
        c.save()
        self.assertFalse(self.add(bridge)['accepted'])

    def test_pending_gap_cannot_approve_old_plan_or_revise_before_replanning(self):
        bridge = self.bridge([self.plan_response(), needs_info(), self.plan_response()])
        bridge.call('dot_plan', {'requirements': 'requirements'})
        bridge.call('dot_initialize', {})
        bridge.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'author'})
        bridge.call('dot_plan', {'requirements': 'requirements with new missing fact'})
        reviews = len(Controller(bridge.run_dir).state['l1_reviews'])
        self.assertFalse(bridge.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'author'})['accepted'])
        self.assertFalse(bridge.call('l1_revise', {'requirements': 'new', 'plan_json': json.dumps(fixtures.fixture_plan())})['accepted'])
        self.assertEqual(len(Controller(bridge.run_dir).state['l1_reviews']), reviews)
        self.add(bridge)
        bridge.call('dot_plan', {'requirements': 'requirements with new fact supplied'})
        self.assertFalse(bridge.call('l1_approve', {'kind': 'plan', 'l1_session_id': 'author'})['accepted'])


if __name__ == '__main__':
    unittest.main()

"""Communication policy boundaries and real prompt wiring, not model truth/efficiency tests."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from dot_runtime import communication
from dot_runtime.gpt_dot import ResponsesClient
from dot_runtime.delegation import attach_delegation, approve_l1
from l1_bridge import dispatch
import test_runtime_controller as fixtures
from test_delegated_l1 import delegation, call_response


class CommunicationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads(communication.POLICY.read_text(encoding='utf-8'))

    def test_policy_and_all_four_maxims_are_present(self):
        self.assertEqual(communication.validate_communication_policy(self.value), [])

    def test_concise_exchange_reaches_l1_and_each_worker_without_hiding_failures(self):
        from dot_runtime.scope_constraints import prompt, ROLE_RULES
        prompts = [communication.l1_prompt(), *(prompt(role) for role in ROLE_RULES)]
        for text in prompts:
            for rule in self.value['agent_exchange_rules']:
                self.assertIn(rule, text)
        self.value['agent_exchange_rules'] = []
        self.assertTrue(communication.validate_communication_policy(self.value))

    def test_missing_or_duplicate_maxim_is_refused(self):
        self.value['maxims'].pop()
        self.assertTrue(communication.validate_communication_policy(self.value))
        self.value['maxims'].append(copy.deepcopy(self.value['maxims'][-1]))
        self.assertTrue(communication.validate_communication_policy(self.value))

    def test_required_maxim_cannot_be_downgraded(self):
        self.value['maxims'][1]['level'] = 'MAY'
        self.assertTrue(communication.validate_communication_policy(self.value))

    def test_brevity_cannot_remove_evidence_failures_or_requirements(self):
        for key in ('evidence_and_hard_constraints_before_brevity', 'preserve_all_failures_and_attempts'):
            with self.subTest(key=key):
                self.value['boundaries'][key] = False
                self.assertTrue(communication.validate_communication_policy(self.value))
                self.value['boundaries'][key] = True

    def test_silence_acknowledgment_and_cooperation_cannot_grant_authority(self):
        for key in ('silence_or_acknowledgment_is_approval', 'implicit_intent_grants_authority', 'cooperation_means_agreement'):
            with self.subTest(key=key):
                self.value['boundaries'][key] = True
                self.assertTrue(communication.validate_communication_policy(self.value))
                self.value['boundaries'][key] = False

    def test_delta_cannot_assume_context_or_file_access(self):
        for key, invalid in [('delta_requires_accessible_matching_baseline', False),
                             ('reference_alone_proves_recipient_access', True),
                             ('stateless_api_may_assume_prior_context', True)]:
            with self.subTest(key=key):
                self.value['boundaries'][key] = invalid
                self.assertTrue(communication.validate_communication_policy(self.value))
                self.value['boundaries'][key] = not invalid

    def test_guide_cannot_become_new_wire_schema_or_approval(self):
        for key in ('guide_is_new_wire_schema', 'purpose_label_grants_authority'):
            with self.subTest(key=key):
                self.value['message_guide'][key] = True
                self.assertTrue(communication.validate_communication_policy(self.value))
                self.value['message_guide'][key] = False

    def test_no_extra_confirmation_or_user_gate(self):
        for key in ('extra_user_forms_or_checkpoints', 'extra_confirmation_round_required',
                    'changes_to_tool_schema_or_state_machine'):
            with self.subTest(key=key):
                self.value['boundaries'][key] = True
                self.assertTrue(communication.validate_communication_policy(self.value))
                self.value['boundaries'][key] = False

    def test_fixed_length_and_unsupported_efficiency_claim_refused(self):
        self.value['message_guide']['fixed_word_limit'] = 100
        self.assertTrue(communication.validate_communication_policy(self.value))
        self.value['message_guide']['fixed_word_limit'] = None
        self.value['source']['measured_efficiency_gain_claimed'] = True
        self.assertTrue(communication.validate_communication_policy(self.value))

    def test_policy_is_not_automatic_semantic_proof(self):
        self.value['boundaries']['all_message_semantics_automatically_verified'] = True
        self.assertTrue(communication.validate_communication_policy(self.value))

    def test_malformed_policy_shapes_report_errors(self):
        self.assertTrue(communication.validate_communication_policy(None))
        for key in ('maxims', 'boundaries', 'message_guide', 'source', 'evaluation'):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.value)
                changed[key] = None
                self.assertTrue(communication.validate_communication_policy(changed))


class CommunicationRuntimeTests(unittest.TestCase):
    setUp = fixtures.ControllerTests.setUp
    controller = fixtures.ControllerTests.controller
    approved = fixtures.ControllerTests.approved

    def client(self, send):
        raw = self.root / 'raw.md'
        raw.write_text('Known fixture task', encoding='utf-8')
        return ResponsesClient('configured-model', self.root / 'api', delegation(raw), send=send)

    def test_api_developer_prompt_applies_once_without_extra_round_trip(self):
        send = Mock(return_value=call_response('stop', {'reason': 'Fixture'}))
        client = self.client(send)
        original = 'FULL REQUIREMENTS: preserve this input, even if repeated.'
        client.request(original)
        self.assertEqual(send.call_count, 1)
        payload = send.call_args.args[0]
        self.assertEqual(payload['input'][1]['content'], original)
        self.assertEqual(payload['input'][0]['role'], 'developer')
        for n in range(1, 5):
            self.assertEqual(payload['input'][0]['content'].count(f'COMM-{n:02}'), 1)
        self.assertEqual(len(fixtures.read_json(client.root / 'ledger.json')['attempts']), 1)

    def test_input_cap_counts_added_instructions_and_does_not_truncate(self):
        send = Mock()
        client = self.client(send)
        from dot_runtime.input_material import InputRejected
        with self.assertRaisesRegex(InputRejected, 'local limit is 250000'):
            client.request('x' * 249999)
        send.assert_not_called()
        self.assertFalse((client.root / 'ledger.json').exists())

    def test_l1_mcp_initialize_receives_rules_without_new_tools(self):
        stub = Mock()
        result = dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, stub)['result']
        for n in range(1, 5):
            self.assertIn(f'COMM-{n:02}', result['instructions'])
        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertEqual(stub.mock_calls, [])

    def test_independent_l1_review_receives_communication_boundaries(self):
        raw = self.root / 'raw.md'
        raw.write_text('Known fixture task', encoding='utf-8')
        path = self.root / 'delegation.json'
        fixtures.atomic_json(path, delegation(raw))
        c = self.controller()
        attach_delegation(c, path, raw)
        run = c.adapter.run
        prompts = []
        def capture(role, prompt, workspace, output, timeout):
            prompts.append(prompt)
            return run(role, prompt, workspace, output, timeout)
        with patch.object(c.adapter, 'run', side_effect=capture):
            approve_l1(c, 'requirements', 'l1-author', c.adapter)
        self.assertIn('COMM-02', prompts[0])
        self.assertIn('沉默当同意', prompts[0])
        self.assertEqual(c.state['approvals']['requirements']['authority'], 'delegated_l1')

    def test_policy_change_blocks_old_plan_before_dispatch(self):
        path = self.root / 'communication.json'
        path.write_bytes(communication.POLICY.read_bytes())
        with patch.object(communication, 'POLICY', path):
            c = self.approved()
            value = json.loads(path.read_text(encoding='utf-8'))
            value['maxims'][0]['instruction'] += ' 经复核的新增说明。'
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(c.run()['state'], 'DECISION_PENDING')
            self.assertEqual(c.adapter.calls, [])

    def test_policy_change_invalidates_frozen_candidate(self):
        path = self.root / 'communication.json'
        path.write_bytes(communication.POLICY.read_bytes())
        with patch.object(communication, 'POLICY', path):
            c = self.approved()
            c.make_candidate()
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'Candidate.*stale'):
                c.check_candidate()

    def test_invalid_policy_stops_api_before_dispatch(self):
        send = Mock()
        client = self.client(send)
        path = self.root / 'invalid.json'
        path.write_text('{}', encoding='utf-8')
        with patch.object(communication, 'POLICY', path), self.assertRaises(ValueError):
            client.request('Known task')
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

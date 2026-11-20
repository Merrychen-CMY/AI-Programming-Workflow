"""Subscription routing and ledger evidence; synthetic responses, no external calls."""
import io
import os
import json
import unittest
from unittest.mock import patch, Mock

import test_api_recovery as fixtures
from dot_runtime.gpt_dot import transport, function
from dot_runtime import api_recovery
from dot_runtime.controller import read_json


class ChatGPTTransportTests(unittest.TestCase):
    setUp = fixtures.ApiRecoveryTests.setUp
    client = fixtures.ApiRecoveryTests.client
    def test_membership_payload_and_usage_record_match_wire_contract(self):
        reply = {'id': 'resp_fixture', 'status': 'completed', 'output': [],
                 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
        send = Mock(return_value=reply)
        with patch.dict(os.environ, {'REQPILOT_AUTH_MODE': 'chatgpt'}):
            result = self.client(send).request('Plan', tools=[function('read', {'path': {'type': 'string'}})])
        payload = send.call_args.args[0]
        self.assertTrue(payload['stream'])
        self.assertFalse(payload['store'])
        self.assertNotIn('max_output_tokens', payload)
        self.assertEqual(payload['tools'][0]['type'], 'namespace')
        self.assertEqual(result['usage']['total_tokens'], 15)
        ledger = read_json(self.root / 'api/ledger.json')['attempts'][-1]
        self.assertEqual(ledger['auth_mode'], 'chatgpt')
        self.assertIn('provider_managed', ledger['output_limit_enforcement'])
        saved = read_json(self.root / 'api' / (ledger['id'] + '-request.json'))
        self.assertEqual(saved, payload)

    def test_membership_without_host_does_not_fallback_to_present_api_key(self):
        with patch.dict(os.environ, {'REQPILOT_AUTH_MODE': 'chatgpt', 'OPENAI_API_KEY': 'fixture-paid-key'}, clear=True), \
                patch('urllib.request.build_opener') as network:
            with self.assertRaises(api_recovery.ApiReview) as caught:
                transport({'input': []}, 1)
            self.assertEqual(caught.exception.diagnostic['outcome'], 'NOT_SENT')
            network.assert_not_called()

    def test_quota_stops_for_l1_review_without_another_model_request(self):
        diagnostic = {'version': 1, 'code': 'CHATGPT_LIMIT', 'outcome': 'REJECTED', 'http_status': 429}
        def fail(*_):
            raise api_recovery.ApiReview(diagnostic)
        with patch.dict(os.environ, {'REQPILOT_AUTH_MODE': 'chatgpt'}):
            with self.assertRaises(api_recovery.ApiReview): self.client(fail).request('Plan')
            again = Mock()
            with self.assertRaisesRegex(ValueError, 'unresolved'): self.client(again).request('Retry')
            again.assert_not_called()
        self.assertEqual(api_recovery.report(self.root)['state'], 'API_REVIEW_PENDING')

    def test_host_failure_preserves_diagnostic_and_never_opens_key_transport(self):
        diagnostic = {'version': 1, 'code': 'CHATGPT_LIMIT', 'outcome': 'REJECTED'}
        with patch.dict(os.environ, {'REQPILOT_AUTH_MODE': 'chatgpt', 'REQPILOT_HOST_RELAY': '1', 'OPENAI_API_KEY': 'fixture'}), \
                patch('dot_runtime.host_tools.pipe_relay', return_value=lambda request: {'status': 'FAIL', 'diagnostic': diagnostic}), \
                patch('urllib.request.build_opener') as network:
            with self.assertRaises(api_recovery.ApiReview) as caught: transport({}, 1)
            self.assertEqual(caught.exception.diagnostic, diagnostic)
            network.assert_not_called()

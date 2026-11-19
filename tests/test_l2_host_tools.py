"""Host relay evidence and GPT continuation; no real API or host account operations."""
import io
import json
import unittest

import test_clarifications as fixture
import test_delegated_l1 as delegated
from dot_runtime import research
from dot_runtime.gpt_dot import researched_request
from dot_runtime.host_tools import HostTools, pipe_relay
from dot_runtime.input_material import InputRejected


class HostToolTests(unittest.TestCase):
    controller = fixture.ClarificationTests.controller
    setUp = delegated.DelegatedTests.setUp
    bridge = fixture.ClarificationTests.bridge

    def test_bridge_attaches_transient_host_capability_and_records_known_failure(self):
        bridge = self.bridge([])
        bridge.host_relay = lambda request: {'status': 'FAIL', 'summary': 'Host permission denied'}
        client = bridge.client()
        self.assertIsInstance(client.host_tools, HostTools)
        result = client.host_tools.run('host_call', {'name': 'pwsh', 'arguments_json': '{}'})
        self.assertEqual(result['status'], 'FAIL')
        self.assertFalse(research.active(bridge.root))
        self.assertEqual(len(research.records(bridge.root)), 1)
        self.assertFalse((bridge.root / 'preflight-report.json').exists())

    def test_unknown_preserves_record_and_blocks_replay(self):
        bridge = self.bridge([])
        bridge.host_relay = lambda request: {'status': 'UNKNOWN', 'summary': 'Lost host reply'}
        host = bridge.client().host_tools
        result = host.run('host_call', {'name': 'jobs', 'arguments_json': '{}'})
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertTrue(research.active(bridge.root))
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            host.run('host_call', {'name': 'jobs', 'arguments_json': '{}'})

    def test_discovery_and_image_results_continue_with_real_call_ids(self):
        bridge = self.bridge([])
        bridge.host_relay = lambda request: {'status': 'PASS', 'content': [],
            'images': [{'type': 'input_image', 'detail': 'auto', 'image_url': 'data:image/png;base64,ZmFrZQ=='}]}
        client = bridge.client()
        requests = []
        client.root.mkdir(parents=True, exist_ok=True)
        def request(prompt, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                return {'id': 'response1', 'output': [{'type': 'function_call', 'call_id': 'actual-call-id',
                    'name': 'host_call', 'arguments': json.dumps({'name': 'read_image', 'arguments_json': '{}'})}]}
            return {'id': 'response2', 'output': []}
        client.request = request
        result = researched_request(client, 'Inspect the evidence')
        self.assertEqual(result['id'], 'response2')
        self.assertIn('host_tools', [tool['name'] for tool in requests[0]['tools']])
        history = requests[1]['input_items']
        self.assertEqual(history[1]['call_id'], 'actual-call-id')
        self.assertEqual(history[2]['content'][1]['type'], 'input_image')
        self.assertNotIn('base64', history[1]['output'])

    def test_invalid_pipe_result_never_becomes_success(self):
        for value in ('', '{}\n', '{"host_response":"wrong","result":{"status":"PASS"}}\n'):
            self.assertEqual(pipe_relay(io.StringIO(value), io.StringIO())({})['status'], 'UNKNOWN')

    def test_image_bytes_have_separate_budget_without_bypassing_text_limit(self):
        bridge = self.bridge([delegated.call_response('done')])
        image = {'role': 'user', 'content': [{'type': 'input_image', 'detail': 'auto',
            'image_url': 'data:image/png;base64,' + 'a' * 300000}]}
        client = bridge.client()
        client.request('inspect image', input_items=[image])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]['input'][-1], image)
        with self.assertRaises(InputRejected):
            client.request('x' * 260000, input_items=[image])
        image['content'][0]['image_url'] = 'data:image/png;base64,' + 'a' * 32000000
        with self.assertRaises(InputRejected):
            client.request('inspect image', input_items=[image])
        self.assertEqual(len(self.requests), 1, 'local material rejection must not send or reserve another API call')


if __name__ == '__main__':
    unittest.main()

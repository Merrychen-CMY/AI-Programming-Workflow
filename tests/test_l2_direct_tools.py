"""Real local tools and mocked Responses protocol; no paid API or product hardware."""
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import test_clarifications as fixture
import test_delegated_l1 as delegated
from dot_runtime import api_recovery, research, resources
from dot_runtime.controller import atomic_json, file_hash, read_json
from dot_runtime.gpt_dot import researched_request
from dot_runtime.input_material import InputRejected


class DirectToolTests(unittest.TestCase):
    controller = fixture.ClarificationTests.controller
    setUp = delegated.DelegatedTests.setUp
    bridge = fixture.ClarificationTests.bridge

    def tools(self):
        self.b = self.bridge([])
        self.client = self.b.client()
        return self.client.local_tools

    def test_direct_files_directory_edit_and_bounded_read_with_evidence(self):
        tools = self.tools()
        path = tools.workspace / 'notes' / 'observations.md'
        self.assertEqual(tools.run('local_mkdir', {'path': str(path.parent)})['status'], 'PASS')
        created = tools.run('local_write', {'path': str(path), 'text': 'Observed SDK', 'expected_sha256': ''})
        self.assertEqual(created['status'], 'PASS')
        read = tools.run('local_read', {'path': str(path), 'offset': 0, 'limit': 8})
        self.assertEqual(read['text'], 'Observed')
        self.assertEqual(read['next_offset'], 8)
        edited = tools.run('local_edit', {'path': str(path), 'old': 'SDK', 'new': 'compiler', 'expected_sha256': read['sha256']})
        self.assertEqual(edited['status'], 'PASS')
        self.assertEqual(path.read_text(), 'Observed compiler')
        listing = tools.run('local_list', {'path': str(path.parent), 'offset': 0, 'limit': 1})
        self.assertEqual(listing['entries'], ['observations.md'])
        self.assertEqual(len(research.records(self.b.root)), 5)
        self.assertFalse(self.b.workers.calls)
        self.assertEqual(resources.usage(self.b.root)['implementation_attempts'], 0)

    def test_write_rejects_stale_hash_outside_paths_control_records_and_bad_types(self):
        tools = self.tools()
        for path in (self.root / 'outside.txt', self.b.root / 'api/ledger.json', self.project / '.git/config', self.project / 'unapproved.txt'):
            result = tools.run('local_write', {'path': str(path), 'text': 'not authorized', 'expected_sha256': ''})
            self.assertEqual(result['status'], 'FAIL')
        path = self.project / 'value.py'
        before = path.read_bytes()
        result = tools.run('local_write', {'path': str(path), 'text': 'stale edit', 'expected_sha256': ''})
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(path.read_bytes(), before)
        result = tools.run('local_read', {'path': str(path), 'offset': True, 'limit': 10})
        self.assertEqual(result['status'], 'FAIL')
        self.assertFalse(research.active(self.b.root))

    def test_real_command_returns_output_exit_status_and_removes_api_secret(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture-secret-not-a-key'}):
            tools = self.tools()
        result = tools.run('local_command', {'argv': [sys.executable, '-c',
            'import os,sys;print(sys.version);print("key="+str("OPENAI_API_KEY" in os.environ))'],
            'cwd': str(self.root), 'timeout_seconds': 10})
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('key=False', result['output'])
        folder = (self.b.root / result['record']).parent
        process = read_json(folder / 'worker/process-record.json')
        self.assertEqual(process['status'], 'EXITED')
        self.assertGreater(process['pid'], 0)
        self.assertTrue((folder / 'worker/check.log').exists())
        self.assertNotIn('fixture-secret-not-a-key', json.dumps(research.records(self.b.root)))

    def test_failed_command_returns_to_dot_without_finalizing(self):
        tools = self.tools()
        result = tools.run('local_command', {'argv': [sys.executable, '-c', 'print("missing SDK");raise SystemExit(2)'],
                                            'cwd': str(self.project), 'timeout_seconds': 10})
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['exit_code'], 2)
        self.assertFalse(research.active(self.b.root))
        self.assertFalse((self.b.root / 'preflight-report.json').exists())

    def test_real_timeout_retains_log_and_process_tree_evidence(self):
        tools = self.tools()
        result = tools.run('local_command', {'argv': [sys.executable, '-c', 'import time; print("started",flush=True); time.sleep(30)'],
                                            'cwd': str(self.project), 'timeout_seconds': 1})
        self.assertEqual(result['status'], 'TIMEOUT', result)
        self.assertIn('started', result['output'])
        folder = (self.b.root / result['record']).parent
        self.assertEqual(read_json(folder / 'worker/process-record.json')['process_tree_status'], 'STOPPED')
        self.assertFalse(research.active(self.b.root))

    def test_failed_tool_is_returned_and_dot_can_choose_another_lookup(self):
        b = self.bridge([
            delegated.call_response('local_read', {'path': 'does-not-exist.txt', 'offset': 0, 'limit': 50}),
            delegated.call_response('local_list', {'path': '.', 'offset': 0, 'limit': 10}),
            delegated.call_response('stop', {'reason': 'Fixture observations collected'})])
        response = researched_request(b.client(), 'Find the source file')
        self.assertEqual(response['output'][0]['name'], 'stop')
        outputs = [item for item in self.requests[-1]['input'] if item.get('type') == 'function_call_output']
        self.assertIn('"status": "FAIL"', outputs[0]['output'])
        self.assertIn('value.py', outputs[1]['output'])
        self.assertEqual(resources.usage(b.root)['api_calls'], 3)
        self.assertFalse((b.root / 'preflight-report.json').exists())

    def test_tool_loop_preserves_reasoning_call_ids_outputs_and_accounting(self):
        b = self.bridge([])
        first = delegated.call_response('local_read', {'path': 'value.py', 'offset': 0, 'limit': 300})
        first['output'].insert(0, {'type': 'reasoning', 'id': 'rs_test', 'summary': [], 'encrypted_content': 'fixture-encrypted'})
        self.responses.extend([first, delegated.call_response('stop', {'reason': 'Fixture complete'})])
        response = researched_request(b.client(), 'Inspect the existing source only', tools=[])
        self.assertEqual(response['output'][0]['name'], 'stop')
        second = self.requests[-1]
        self.assertEqual(second['input'][2:4], first['output'])
        self.assertEqual(second['input'][4]['type'], 'function_call_output')
        self.assertEqual(second['input'][4]['call_id'], 'call-test')
        self.assertIn('value.py', second['input'][4]['output'])
        self.assertEqual(second['include'], ['reasoning.encrypted_content'])
        self.assertEqual(resources.usage(b.root)['api_calls'], 2)
        self.assertFalse(b.workers.calls)
        self.assertEqual(len(research.records(b.root)), 1)

    def test_complete_tool_history_counts_towards_unsent_material_limit(self):
        self.tools()
        with self.assertRaises(InputRejected):
            self.client.request('small prompt', input_items=[{'type': 'function_call_output', 'call_id': 'a', 'output': 'x' * 260000}])
        self.assertEqual(self.requests, [])
        self.assertFalse((self.b.root / 'api/ledger.json').exists())

    def test_unknown_local_operation_blocks_followup_without_replay(self):
        tools = self.tools()
        with patch('dot_runtime.local_tools.run_check', return_value={'status': 'UNKNOWN', 'exit_code': None, 'output': ''}):
            result = tools.run('local_command', {'argv': ['fixture'], 'cwd': str(self.project), 'timeout_seconds': 1})
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(len(research.active(self.b.root)), 1)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            tools.run('local_list', {'path': '.', 'offset': 0, 'limit': 2})
        self.assertFalse(self.b.call('dot_plan', {'requirements': 'No replay'})['accepted'])

    def test_web_search_uses_accounted_model_request_and_preserves_citations(self):
        tools = self.tools()
        self.responses.append({'id': 'resp-search', 'status': 'completed', 'usage': {'total_tokens': 17}, 'output': [
            {'type': 'web_search_call', 'id': 'ws_1', 'status': 'completed', 'action': {'type': 'search', 'query': 'SDK docs'}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Observed documentation',
                'annotations': [{'type': 'url_citation', 'url': 'https://example.org/docs', 'title': 'Fixture docs'}]}]}]})
        result = tools.run('web_search', {'query': 'SDK docs'})
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['citations'][0]['url'], 'https://example.org/docs')
        self.assertEqual(self.requests[0]['tools'], [{'type': 'web_search'}])
        self.assertEqual(self.requests[0]['model'], 'fixture-model')
        self.assertEqual(resources.usage(self.b.root)['api_calls'], 1)
        self.assertFalse(self.b.workers.calls)

    def test_search_failure_uses_api_review_without_duplicate_local_unknown(self):
        tools = self.tools()
        def rejected(*args):
            raise api_recovery.ApiReview({'version': 1, 'code': 'API_HTTP', 'http_status': 400, 'outcome': 'REJECTED'})
        self.client.send = rejected
        with self.assertRaises(api_recovery.ApiReview):
            tools.run('web_search', {'query': 'SDK docs'})
        self.assertFalse(research.active(self.b.root))
        self.assertEqual(research.context(self.b.root)[0]['status'], 'FAIL')
        self.assertEqual(read_json(self.b.root / 'api/ledger.json')['attempts'][0]['status'], 'FAILED_OR_UNKNOWN')

    def test_fetch_preserves_bytes_and_text_with_real_local_http(self):
        tools = self.tools()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write('Measured fixture source'.encode())
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = tools.run('web_fetch', {'url': 'http://127.0.0.1:' + str(server.server_port) + '/source'})
            self.assertEqual(result['status'], 'PASS', result)
            self.assertEqual(result['text'], 'Measured fixture source')
            self.assertEqual(file_hash(result['path']), result['sha256'])
            Path(result['path']).write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'changed'):
                research.records(self.b.root)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()

"""Protocol fixtures and a local fake subprocess; no Codex inference or paid API."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from dot_runtime import app_server, adapter


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(app_server.time, 'monotonic', return_value=0).start()
        self.addCleanup(patch.stopall)
        self.process = Mock(stdin=io.StringIO())
        self.events, self.wire, self.supervision = io.StringIO(), io.StringIO(), io.StringIO()
        self.session = app_server.Session(self.process, 'Assigned scope', 'read-only', '/approved',
                                         {'type': 'object'}, self.events, self.wire, self.supervision,
                                         lambda message: app_server._write(self.process.stdin, message))

    def sent(self):
        return [json.loads(line) for line in self.process.stdin.getvalue().splitlines()]

    def log(self):
        return [json.loads(line) for line in self.supervision.getvalue().splitlines()]

    def start(self):
        self.session.start()
        self.session.receive({'id': 1, 'result': {}})
        self.session.receive({'id': 2, 'result': {'thread': {'id': 'thread-one'}}})
        self.session.receive({'id': 3, 'result': {'turn': {'id': 'turn-one', 'status': 'inProgress'}}})

    def notify(self, method, **params):
        self.session.receive({'method': method, 'params': {'threadId': 'thread-one', 'turnId': 'turn-one', **params}})

    def test_handshake_fresh_thread_original_sandbox_schema_and_no_approval(self):
        self.start()
        sent = self.sent()
        self.assertEqual([item['method'] for item in sent], ['initialize', 'initialized', 'thread/start', 'turn/start'])
        self.assertEqual(sent[2]['params'], {'cwd': '/approved', 'sandbox': 'read-only', 'approvalPolicy': 'never'})
        self.assertEqual(sent[3]['params']['sandboxPolicy'], {'type': 'readOnly', 'networkAccess': False})
        self.assertEqual(sent[3]['params']['outputSchema'], {'type': 'object'})
        self.assertEqual(sent[3]['params']['approvalPolicy'], 'never')
        self.assertEqual(sent[3]['params']['input'], [{'type': 'text', 'text': 'Assigned scope'}])

    def test_implementation_keeps_only_existing_workspace_write_policy(self):
        self.session.sandbox = 'workspace-write'
        self.start()
        self.assertEqual(self.sent()[3]['params']['sandboxPolicy'],
                         {'type': 'workspaceWrite', 'writableRoots': [], 'networkAccess': False})

    def test_test_artifact_write_root_does_not_add_the_source_root(self):
        self.session.sandbox = 'workspace-write'
        self.session.writable_roots = ['/approved/artifacts']
        self.start()
        self.assertEqual(self.sent()[3]['params']['sandboxPolicy'],
                         {'type': 'workspaceWrite', 'writableRoots': ['/approved/artifacts'], 'networkAccess': False})

    def test_check_in_after_120_seconds_and_repeat_same_turn_without_new_turn(self):
        self.start()
        self.clock.return_value = 119.9
        self.session.check_in()
        self.assertEqual(len(self.sent()), 4)
        self.clock.return_value = 120
        self.session.check_in()
        request = self.sent()[-1]
        self.assertEqual(request['method'], 'turn/steer')
        self.assertEqual(request['params']['expectedTurnId'], 'turn-one')
        self.assertEqual(request['params']['threadId'], 'thread-one')
        self.assertIn('你正在做什么', request['params']['input'][0]['text'])
        self.assertIn('公开进度摘要', request['params']['input'][0]['text'])
        self.assertIn('不要提供内部推理', request['params']['input'][0]['text'])
        self.session.receive({'id': request['id'], 'result': {'turnId': 'turn-one'}})
        self.clock.return_value = 239
        self.session.check_in()
        self.assertEqual(len(self.sent()), 5)
        self.clock.return_value = 240
        self.session.check_in()
        self.assertEqual(len(self.sent()), 6)
        self.assertEqual(sum(item.get('method') == 'turn/start' for item in self.sent()), 1)

    def test_reasoning_events_do_not_masquerade_as_visible_work_progress(self):
        self.start()
        self.clock.return_value = 100
        self.notify('item/reasoning/summaryTextDelta', delta='opaque reasoning event', itemId='reasoning-1')
        self.clock.return_value = 120
        self.session.check_in()
        self.assertEqual(self.sent()[-1]['method'], 'turn/steer')

    def test_message_progress_delays_check_in(self):
        self.start()
        self.clock.return_value = 100
        self.notify('item/agentMessage/delta', delta='Checking assigned file.', itemId='message-1')
        self.clock.return_value = 120
        self.session.check_in()
        self.assertEqual(len(self.sent()), 4)
        self.clock.return_value = 220
        self.session.check_in()
        self.assertEqual(len(self.sent()), 5)

    def test_running_command_is_not_mistaken_for_stalled_thinking(self):
        self.start()
        self.notify('item/started', item={'id': 'cmd-1', 'type': 'commandExecution', 'command': 'approved-build'})
        self.clock.return_value = 500
        self.session.check_in()
        self.assertEqual(len(self.sent()), 4)
        self.notify('item/completed', item={'id': 'cmd-1', 'type': 'commandExecution', 'command': 'approved-build',
                                          'aggregatedOutput': 'build completed', 'exitCode': 0})
        self.clock.return_value = 619
        self.session.check_in()
        self.assertEqual(len(self.sent()), 4)
        self.clock.return_value = 620
        self.session.check_in()
        self.assertEqual(len(self.sent()), 5)

    def test_acknowledgement_is_not_answer_and_later_public_reply_is_recorded(self):
        self.start()
        self.clock.return_value = 120
        self.session.check_in()
        self.session.receive({'id': 4, 'result': {'turnId': 'turn-one'}})
        self.assertEqual([item['stage'] for item in self.log()], ['ATTEMPT', 'SENT', 'ACKNOWLEDGED'])
        self.assertFalse(self.session.checks[4]['answered'])
        self.notify('item/completed', item={'id': 'other', 'type': 'agentMessage', 'phase': 'commentary',
                                          'text': 'Reading assigned source.'})
        self.assertEqual(self.log()[-1]['stage'], 'MESSAGE_AFTER_CHECK')
        self.assertFalse(self.session.checks[4]['answered'])
        self.notify('item/completed', item={'id': 'answer', 'type': 'agentMessage', 'phase': 'commentary',
                                          'text': '[L2 progress] One file reviewed; next fix the reported condition.'})
        self.assertEqual(self.log()[-1]['stage'], 'ANSWER')
        self.assertTrue(self.session.checks[4]['answered'])
        self.assertIsNone(self.session.final_text)

    def test_no_duplicate_steer_while_acknowledgement_pending(self):
        self.start()
        self.clock.return_value = 120
        self.session.check_in()
        self.clock.return_value = 500
        self.session.check_in()
        self.assertEqual(len(self.sent()), 5)

    def test_rejected_steer_is_not_replayed_or_converted_to_an_answer(self):
        self.start()
        self.clock.return_value = 120
        self.session.check_in()
        self.session.receive({'id': 4, 'error': {'code': -32601, 'message': 'unsupported'}})
        self.clock.return_value = 500
        self.session.check_in()
        self.assertEqual(len(self.sent()), 5)
        self.assertEqual(self.log()[-1]['stage'], 'REJECTED')
        self.assertFalse(self.session.checks[4]['answered'])

    def test_wrong_steer_ack_or_thread_or_turn_is_rejected(self):
        self.start()
        self.clock.return_value = 120
        self.session.check_in()
        with self.assertRaises(app_server.ProtocolError):
            self.session.receive({'id': 4, 'result': {'turnId': 'different'}})
        with self.assertRaises(app_server.ProtocolError):
            self.notify('item/completed', threadId='other', item={'id': 'x', 'type': 'agentMessage', 'text': '{}'})
        with self.assertRaises(app_server.ProtocolError):
            self.notify('item/completed', turnId='other', item={'id': 'x', 'type': 'agentMessage', 'text': '{}'})

    def test_approval_requests_never_grant_permissions_or_execute_tools(self):
        self.start()
        for index, method in enumerate(('item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
                                        'item/permissions/requestApproval', 'item/tool/call'), 40):
            self.session.receive({'id': index, 'method': method, 'params': {'command': 'arbitrary',
                                          'permissions': {'network': {'enabled': True}}}})
        replies = self.sent()[-4:]
        self.assertEqual(replies[0]['result'], {'decision': 'decline'})
        self.assertEqual(replies[1]['result'], {'decision': 'decline'})
        self.assertEqual(replies[2]['result'], {'permissions': {}, 'scope': 'turn'})
        self.assertEqual(replies[3]['error']['code'], -32601)

    def test_authoritative_command_and_final_message_feed_existing_evidence_parser(self):
        self.start()
        command = {'id': 'cmd-1', 'type': 'commandExecution', 'command': 'python -m unittest',
                   'aggregatedOutput': 'Ran 2 tests\nOK', 'exitCode': 0}
        self.notify('item/completed', item=command)
        self.notify('item/completed', item={'id': 'report', 'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"status":"PASS"}'})
        self.notify('turn/completed', turn={'id': 'turn-one', 'status': 'completed', 'items': [command]})
        events = [json.loads(line) for line in self.events.getvalue().splitlines()]
        self.assertEqual(events[1]['item']['aggregated_output'], 'Ran 2 tests\nOK')
        self.assertEqual(events[1]['item']['exit_code'], 0)
        self.assertEqual(sum(item.get('item', {}).get('type') == 'command_execution' for item in events), 1)
        self.assertEqual(self.session.final_text, '{"status":"PASS"}')
        self.assertEqual(events[-1]['type'], 'turn.completed')
        self.clock.return_value = 500
        self.session.check_in()
        self.assertEqual(len(self.sent()), 4)

    def test_failed_turn_never_normalizes_to_success(self):
        self.start()
        self.notify('turn/completed', turn={'id': 'turn-one', 'status': 'failed', 'items': [], 'error': {'message': 'failure'}})
        self.assertEqual(json.loads(self.events.getvalue().splitlines()[-1])['type'], 'turn.failed')

    def test_explicit_turn_start_rejection_without_turn_events_is_clean_rejection(self):
        self.session.start()
        self.session.receive({'id': 1, 'result': {}})
        self.session.receive({'id': 2, 'result': {'thread': {'id': 'thread-one'}}})
        with self.assertRaises(app_server.ProtocolError) as error:
            self.session.receive({'id': 3, 'error': {'code': -32602, 'message': 'invalid schema'}})
        self.assertFalse(error.exception.dispatched)

    def test_turn_start_error_after_turn_activity_remains_uncertain(self):
        self.session.start()
        self.session.receive({'id': 1, 'result': {}})
        self.session.receive({'id': 2, 'result': {'thread': {'id': 'thread-one'}}})
        self.notify('turn/started', turn={'id': 'turn-one', 'status': 'inProgress'})
        with self.assertRaises(app_server.ProtocolError) as error:
            self.session.receive({'id': 3, 'error': {'code': -32602, 'message': 'unexpected error'}})
        self.assertTrue(error.exception.dispatched)

    def test_send_failure_keeps_attempt_separate_from_sent(self):
        self.start()
        self.clock.return_value = 120
        self.process.stdin = Mock()
        self.process.stdin.write.side_effect = BrokenPipeError('closed')
        with self.assertRaises(BrokenPipeError):
            self.session.check_in()
        self.assertEqual([row['stage'] for row in self.log()], ['ATTEMPT', 'SEND_FAILED'])

    def test_foreign_session_and_unmatched_rpc_response_are_rejected(self):
        self.start()
        with self.assertRaises(app_server.ProtocolError):
            self.session.receive({'method': 'thread/started', 'params': {'thread': {'id': 'other-thread'}}})
        with self.assertRaises(app_server.ProtocolError):
            self.session.receive({'id': 999, 'result': {}})


FAKE_SERVER = r'''
import json, sys, time
mode = sys.argv[1]
def send(value):
    print(json.dumps(value), flush=True)
def item(value):
    send({'method': 'item/completed', 'params': {'threadId': 'fixture-thread', 'turnId': 'fixture-turn', 'item': value}})
def finish():
    command = 'python -m unittest'
    output = 'Ran 2 tests in 0.1s\nOK\n'
    item({'id': 'cmd', 'type': 'commandExecution', 'command': command, 'aggregatedOutput': output, 'exitCode': 0})
    report = {'status': 'PASS', 'summary': 'Two required tests passed', 'findings': [], 'root_cause': 'needs_analysis',
              'root_cause_key': '', 'evidence': [{'kind': 'command', 'description': 'Executed fixture tests',
              'command': command, 'output': output, 'exit_code': 0, 'test_count': 2}]}
    item({'id': 'report', 'type': 'agentMessage', 'phase': 'final_answer', 'text': json.dumps(report)})
    send({'method': 'turn/completed', 'params': {'threadId': 'fixture-thread',
          'turn': {'id': 'fixture-turn', 'status': 'completed', 'items': []}}})
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if method == 'initialize':
        if mode == 'unsupported':
            send({'id': request['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
        else:
            send({'id': request['id'], 'result': {}})
    elif method == 'thread/start':
        send({'id': request['id'], 'result': {'thread': {'id': 'fixture-thread'}}})
        if mode == 'blocked-stdin':
            time.sleep(30)
    elif method == 'turn/start':
        if mode == 'reject-turn':
            send({'id': request['id'], 'error': {'code': -32602, 'message': 'invalid schema'}})
            continue
        send({'id': request['id'], 'result': {'turn': {'id': 'fixture-turn', 'status': 'inProgress'}}})
        if mode == 'disconnect':
            break
        if mode == 'stderr':
            sys.stderr.write('fixture diagnostic\n' * 10000)
            sys.stderr.flush()
        if mode not in ('check', 'timeout'):
            finish()
    elif method == 'turn/steer':
        send({'id': request['id'], 'result': {'turnId': 'fixture-turn'}})
        item({'id': 'progress', 'type': 'agentMessage', 'phase': 'commentary',
              'text': '[L2 progress] Reporting fixture progress; completing the assigned check.'})
        finish()
'''


class LocalPipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.output = self.root / 'run'
        self.real_popen = subprocess.Popen

    def run_adapter(self, mode, timeout=8, prompt='Assigned fixture'):
        def local_process(command, **options):
            if command[0] != 'fixture-only':
                return self.real_popen(command, **options)
            self.assertEqual(command[1], 'app-server')
            return self.real_popen([sys.executable, '-u', '-c', FAKE_SERVER, mode], **options)
        with patch.object(adapter.subprocess, 'Popen', side_effect=local_process), \
                patch.object(app_server, 'CHECK_IN_SECONDS', 0.2):
            return adapter.CodexAdapter('fixture-only').run('test', prompt, self.workspace, self.output, timeout)

    def test_actual_local_pipes_check_in_ack_answer_final_and_process_record(self):
        result = self.run_adapter('check')
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['session_id'], 'fixture-thread')
        logs = [json.loads(line) for line in (self.output / 'supervision.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['stage'] for row in logs], ['ATTEMPT', 'SENT', 'ACKNOWLEDGED', 'ANSWER'])
        self.assertEqual(result['runtime_evidence']['transport'], 'app-server')
        self.assertEqual(result['runtime_evidence']['command_events'][0]['exit_code'], 0)
        self.assertTrue((self.output / 'process-record.json').is_file())

    def test_stderr_larger_than_pipe_buffer_cannot_deadlock_protocol(self):
        result = self.run_adapter('stderr')
        self.assertEqual(result['status'], 'PASS', result)
        self.assertGreater((self.output / 'stderr.log').stat().st_size, 65536)

    def test_disconnect_after_dispatch_is_unknown_without_worker_replay(self):
        result = self.run_adapter('disconnect')
        self.assertEqual(result['status'], 'UNKNOWN', result)
        self.assertIn('no worker was replayed', result['summary'])

    def test_unsupported_app_server_rejected_before_dispatch_without_exec_fallback(self):
        result = self.run_adapter('unsupported')
        self.assertEqual(result['status'], 'ERROR', result)
        wire = (self.output / 'app-server.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('turn/start', wire)
        self.assertNotIn('thread/resume', wire)

    def test_clean_turn_start_rejection_does_not_become_unknown(self):
        result = self.run_adapter('reject-turn')
        self.assertEqual(result['status'], 'ERROR', result)

    def test_original_timeout_terminates_only_fixture_process_without_steer_or_retry(self):
        def terminate_owned_fixture(process, output):
            process.kill()
            process.wait(timeout=2)
            return True
        with patch.object(adapter, '_terminate_tree', side_effect=terminate_owned_fixture), \
                patch.object(app_server.Session, 'check_in') as check_in:
            result = self.run_adapter('timeout', timeout=1)
        self.assertEqual(result['status'], 'TIMEOUT', result)
        record = json.loads((self.output / 'process-record.json').read_text(encoding='utf-8'))
        self.assertEqual(record['process_tree_status'], 'STOPPED')
        wire = [json.loads(line) for line in (self.output / 'app-server.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(sum(row.get('direction') == 'sent' and row.get('method') == 'turn/start' for row in wire), 1)
        self.assertFalse(any(row.get('method') == 'turn/steer' for row in wire))

    def test_blocked_large_prompt_write_obeys_original_timeout_and_is_not_logged_as_sent(self):
        def terminate_owned_fixture(process, output):
            process.kill()
            process.wait(timeout=2)
            return True
        started = time.monotonic()
        with patch.object(adapter, '_terminate_tree', side_effect=terminate_owned_fixture):
            result = self.run_adapter('blocked-stdin', timeout=1, prompt='Large assigned prompt. ' * 100000)
        self.assertEqual(result['status'], 'TIMEOUT', result)
        self.assertLess(time.monotonic() - started, 5)
        wire = [json.loads(line) for line in (self.output / 'app-server.jsonl').read_text(encoding='utf-8').splitlines()]
        attempts = [row for row in wire if row.get('direction') == 'send_attempt'
                    and row['message'].get('method') == 'turn/start']
        sent = [row for row in wire if row.get('direction') == 'sent' and row.get('method') == 'turn/start']
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sent, [])
        record = json.loads((self.output / 'process-record.json').read_text(encoding='utf-8'))
        self.assertEqual(record['process_tree_status'], 'STOPPED')


if __name__ == '__main__':
    unittest.main()

"""One fresh Codex App Server turn with bounded, observable L2 check-ins.

The wire fields are checked against Codex 0.162.0-alpha.2's generated schema.
No model call, worker retry, approval, or permission grant is hidden here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


CHECK_IN_SECONDS = 120
CHECK_IN_TEXT = (
    '你正在做什么？请用简短的公开进度摘要说明当前步骤、已完成内容、阻碍和最小下一步，'
    '以 [L2 progress] 开头发送 commentary。不要提供内部推理或思维链。'
    '遵守已批准范围，避免过度设计，不增加功能、抽象或兼容机制；完成验收所需工作后停止。'
    '本次询问不增加权限、预算或时限，不要求中断正在执行的操作。最终结果仍使用规定 JSON。'
)


class ProtocolError(ValueError):
    def __init__(self, message, *, dispatched=False):
        super().__init__(message)
        self.dispatched = dispatched


def _write(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False) + '\n')
    stream.flush()


class PipeWriter:
    """Bound blocking stdin writes by the same deadline as stdout and turn completion."""

    def __init__(self, stream, deadline, timeout_seconds):
        self.stream, self.deadline, self.timeout_seconds = stream, deadline, timeout_seconds
        self.requests = queue.Queue()
        self.thread = threading.Thread(target=self.run, daemon=True, name='reqpilot-codex-input')
        self.thread.start()

    def run(self):
        try:
            while (request := self.requests.get()) is not None:
                message, result = request
                if time.monotonic() >= self.deadline:
                    result.put(subprocess.TimeoutExpired('codex app-server', self.timeout_seconds))
                    continue
                try:
                    _write(self.stream, message)
                except (OSError, ValueError, TypeError) as exc:
                    result.put(exc)
                else:
                    result.put(None)
        finally:
            # Only this thread owns stdin's IO lock, including when a descendant keeps the pipe open.
            try:
                self.stream.close()
            except (OSError, ValueError):
                pass

    def send(self, message):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('codex app-server', self.timeout_seconds)
        result = queue.Queue(maxsize=1)
        self.requests.put((message, result))
        try:
            outcome = result.get(timeout=remaining)
        except queue.Empty:
            raise subprocess.TimeoutExpired('codex app-server', self.timeout_seconds) from None
        if time.monotonic() >= self.deadline:
            raise subprocess.TimeoutExpired('codex app-server', self.timeout_seconds)
        if outcome is not None:
            raise outcome

    def stop(self):
        # If a pipe is blocked, the caller's process-tree termination releases this writer.
        self.requests.put(None)
        self.thread.join(timeout=0.1)


class Session:
    """Track the single thread/turn and keep protocol acknowledgements separate from replies."""

    def __init__(self, process, instructions, sandbox, workspace, schema, events, wire, supervision, send_message, writable_roots=()):
        self.process, self.events, self.wire, self.supervision = process, events, wire, supervision
        self.instructions, self.sandbox, self.workspace, self.schema = instructions, sandbox, str(workspace), schema
        self.send_message = send_message
        self.writable_roots = list(writable_roots)
        self.pending, self.next_id = {}, 1
        self.thread_id = self.turn_id = None
        self.dispatched = self.completed = False
        self.final_text = None
        self.completed_items, self.running_commands = set(), set()
        self.last_progress = self.last_check = time.monotonic()
        self.checks, self.pending_check = {}, None
        self.steering_available = True

    def log(self, stage, **values):
        _write(self.supervision, {'at': datetime.now(timezone.utc).isoformat(), 'stage': stage,
                                 'thread_id': self.thread_id, 'turn_id': self.turn_id, **values})

    def send(self, message):
        _write(self.wire, {'direction': 'send_attempt', 'message': message})
        self.send_message(message)
        _write(self.wire, {'direction': 'sent', 'id': message.get('id'), 'method': message.get('method')})

    def request(self, method, params):
        identifier = self.next_id
        self.next_id += 1
        self.pending[identifier] = method
        self.send({'id': identifier, 'method': method, 'params': params})
        return identifier

    def start(self):
        self.request('initialize', {'clientInfo': {'name': 'reqpilot', 'title': 'ReqPilot', 'version': '1'}})

    def check_in(self):
        now = time.monotonic()
        if (not self.turn_id or self.completed or self.running_commands or self.pending_check is not None
                or not self.steering_available or now - max(self.last_progress, self.last_check) < CHECK_IN_SECONDS):
            return
        identifier = self.next_id
        self.last_check = now
        self.log('ATTEMPT', request_id=identifier, text=CHECK_IN_TEXT)
        self.checks[identifier] = {'acknowledged': False, 'answered': False}
        self.pending_check = identifier
        try:
            self.request('turn/steer', {'threadId': self.thread_id, 'expectedTurnId': self.turn_id,
                                      'input': [{'type': 'text', 'text': CHECK_IN_TEXT}]})
        except (OSError, ValueError) as exc:
            self.log('SEND_FAILED', request_id=identifier, error=str(exc))
            raise
        self.log('SENT', request_id=identifier)

    def bind_thread(self, identifier):
        if not isinstance(identifier, str) or not identifier or (self.thread_id and self.thread_id != identifier):
            raise ProtocolError('App Server returned a missing or different thread identity', dispatched=self.dispatched)
        if self.thread_id is None:
            self.thread_id = identifier
            _write(self.events, {'type': 'thread.started', 'thread_id': identifier})

    def bind_turn(self, identifier):
        if not isinstance(identifier, str) or not identifier or (self.turn_id and self.turn_id != identifier):
            raise ProtocolError('App Server returned a missing or different turn identity', dispatched=self.dispatched)
        if self.turn_id is None:
            self.turn_id = identifier
            self.last_progress = time.monotonic()

    def reply(self, message):
        identifier = message['id']
        method = self.pending.pop(identifier, None)
        if method is None:
            raise ProtocolError('App Server response has no matching request', dispatched=self.dispatched)
        if method == 'turn/steer':
            self.pending_check = None
            if 'error' in message:
                self.steering_available = False
                self.log('REJECTED', request_id=identifier, error=message['error'])
            elif (message.get('result') or {}).get('turnId') == self.turn_id:
                self.checks[identifier]['acknowledged'] = True
                self.log('ACKNOWLEDGED', request_id=identifier, answered=self.checks[identifier]['answered'])
            else:
                raise ProtocolError('Steering acknowledgement has a different turn identity', dispatched=True)
            return
        if 'error' in message:
            dispatched = self.dispatched and (method != 'turn/start' or self.turn_id is not None)
            raise ProtocolError('App Server rejected ' + method + ': ' + json.dumps(message['error'], ensure_ascii=False),
                                dispatched=dispatched)
        result = message.get('result')
        if not isinstance(result, dict):
            raise ProtocolError('App Server response lacks a result object', dispatched=self.dispatched)
        if method == 'initialize':
            self.send({'method': 'initialized', 'params': {}})
            self.request('thread/start', {'cwd': self.workspace, 'sandbox': self.sandbox, 'approvalPolicy': 'never'})
        elif method == 'thread/start':
            self.bind_thread((result.get('thread') or {}).get('id'))
            policy = {'type': 'readOnly', 'networkAccess': False}
            if self.sandbox == 'workspace-write':
                policy = {'type': 'workspaceWrite', 'writableRoots': self.writable_roots, 'networkAccess': False}
            # Mark before writing: a broken pipe cannot prove that the turn was not sent.
            self.dispatched = True
            self.request('turn/start', {'threadId': self.thread_id,
                'input': [{'type': 'text', 'text': self.instructions}], 'cwd': self.workspace,
                'approvalPolicy': 'never', 'sandboxPolicy': policy, 'outputSchema': self.schema})
        elif method == 'turn/start':
            self.bind_turn((result.get('turn') or {}).get('id'))

    def deny_request(self, message):
        method, identifier = message['method'], message['id']
        if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
            response = {'id': identifier, 'result': {'decision': 'decline'}}
        elif method == 'item/permissions/requestApproval':
            response = {'id': identifier, 'result': {'permissions': {}, 'scope': 'turn'}}
        else:
            response = {'id': identifier, 'error': {'code': -32601,
                        'message': 'ReqPilot does not authorize this server request; report the blocker to DOT.'}}
        self.log('REQUEST_DENIED', method=method, request_id=identifier)
        self.send(response)

    def completed_item(self, item):
        identifier = item.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise ProtocolError('App Server item lacks an identity', dispatched=self.dispatched)
        if identifier in self.completed_items:
            return
        self.completed_items.add(identifier)
        kind = item.get('type')
        if kind == 'commandExecution':
            self.running_commands.discard(identifier)
            self.last_progress = time.monotonic()
            _write(self.events, {'type': 'item.completed', 'item': {'type': 'command_execution',
                'command': item.get('command'), 'aggregated_output': item.get('aggregatedOutput'),
                'exit_code': item.get('exitCode')}})
        elif kind == 'agentMessage':
            text = item.get('text')
            if not isinstance(text, str):
                raise ProtocolError('App Server agent message lacks text', dispatched=self.dispatched)
            if text.strip():
                self.last_progress = time.monotonic()
                outstanding = [key for key, value in self.checks.items() if not value['answered']]
                if outstanding:
                    # Only a specifically labelled public reply is an answer to the check-in.
                    answered = text.lstrip().startswith('[L2 progress]')
                    self.log('ANSWER' if answered else 'MESSAGE_AFTER_CHECK', request_ids=outstanding,
                             item_id=identifier, text=text, answered=answered)
                    if answered:
                        for key in outstanding:
                            self.checks[key]['answered'] = True
            if item.get('phase') in (None, 'final_answer'):
                self.final_text = text
            _write(self.events, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text,
                                                                  'phase': item.get('phase')}})
        elif kind in ('fileChange', 'mcpToolCall', 'dynamicToolCall'):
            self.last_progress = time.monotonic()

    def receive(self, message):
        if not isinstance(message, dict):
            raise ProtocolError('App Server message is not an object', dispatched=self.dispatched)
        _write(self.wire, {'direction': 'received', 'message': message})
        if 'id' in message:
            if 'method' in message:
                self.deny_request(message)
            else:
                self.reply(message)
            return
        method, params = message.get('method'), message.get('params', {})
        if not isinstance(params, dict):
            raise ProtocolError('App Server notification parameters are not an object', dispatched=self.dispatched)
        if method == 'thread/started':
            self.bind_thread((params.get('thread') or {}).get('id'))
            return
        if method in ('turn/started', 'turn/completed') or (isinstance(method, str) and method.startswith('item/')):
            if params.get('threadId') != self.thread_id:
                raise ProtocolError('App Server notification belongs to a different thread', dispatched=self.dispatched)
            self.bind_turn((params.get('turn') or {}).get('id') if method.startswith('turn/') else params.get('turnId'))
        if method == 'turn/completed':
            turn = params['turn']
            for item in turn.get('items', []):
                self.completed_item(item)
            self.completed = True
            if turn.get('status') == 'completed':
                _write(self.events, {'type': 'turn.completed'})
            else:
                _write(self.events, {'type': 'turn.failed', 'status': turn.get('status'), 'error': turn.get('error')})
        elif method == 'item/started':
            item = params.get('item', {})
            if item.get('type') == 'commandExecution':
                self.running_commands.add(item.get('id'))
                self.last_progress = time.monotonic()
        elif method == 'item/completed':
            self.completed_item(params.get('item', {}))
        elif method in ('item/agentMessage/delta', 'item/commandExecution/outputDelta') and params.get('delta'):
            self.last_progress = time.monotonic()
        elif method == 'error' and not params.get('willRetry', False):
            _write(self.events, {'type': 'error', 'error': params.get('error')})


def run_session(process, *, instructions, sandbox, workspace, schema, output_dir: Path, timeout_seconds, writable_roots=()):
    """Keep stdin open for steering; the caller owns process-tree termination and records."""
    deadline = time.monotonic() + timeout_seconds
    inbox = queue.Queue()

    def read_stdout():
        try:
            for line in process.stdout:
                inbox.put(line)
        except (OSError, UnicodeError) as exc:
            inbox.put(exc)
        finally:
            try:
                process.stdout.close()
            except (OSError, ValueError) as exc:
                inbox.put(exc)
            inbox.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True, name='reqpilot-codex-events')
    reader.start()
    with (output_dir / 'events.jsonl').open('w', encoding='utf-8') as events, \
            (output_dir / 'app-server.jsonl').open('w', encoding='utf-8') as wire, \
            (output_dir / 'supervision.jsonl').open('w', encoding='utf-8') as supervision:
        writer = PipeWriter(process.stdin, deadline, timeout_seconds)
        session = Session(process, instructions, sandbox, workspace, schema, events, wire, supervision, writer.send, writable_roots)
        try:
            session.start()
            while not session.completed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired('codex app-server', timeout_seconds)
                try:
                    line = inbox.get(timeout=min(1, remaining))
                except queue.Empty:
                    if time.monotonic() < deadline:
                        session.check_in()
                    continue
                if line is None:
                    raise ProtocolError('App Server closed before turn completion; no worker was replayed',
                                        dispatched=session.dispatched)
                if isinstance(line, Exception):
                    raise ProtocolError('App Server output could not be read: ' + str(line), dispatched=session.dispatched)
                if not line.strip():
                    continue
                try:
                    session.receive(json.loads(line))
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    if isinstance(exc, ProtocolError):
                        raise
                    raise ProtocolError('Invalid App Server event: ' + str(exc), dispatched=session.dispatched) from exc
                if time.monotonic() < deadline:
                    session.check_in()
            if session.final_text is not None:
                (output_dir / 'result.json').write_text(session.final_text, encoding='utf-8')
            for identifier, check in session.checks.items():
                if not check['answered']:
                    session.log('NO_ANSWER_BEFORE_COMPLETION', request_id=identifier,
                                acknowledged=check['acknowledged'], answered=False)
            writer.stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('codex app-server', timeout_seconds)
            process.wait(timeout=remaining)
        except (OSError, ValueError) as exc:
            if isinstance(exc, ProtocolError):
                raise
            raise ProtocolError('App Server transport failed: ' + str(exc), dispatched=session.dispatched) from exc
        finally:
            writer.stop()
            reader.join(timeout=0.1)

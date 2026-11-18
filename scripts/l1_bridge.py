#!/usr/bin/env python3
"""Minimal synchronous MCP stdio bridge for an L1-only Harness agent preset.

Long-running calls require a corresponding client tool timeout. No approval,
reconciliation or delivery tool for the human final decision is exposed.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from dot_runtime.bridge import Bridge
from dot_runtime.controller import read_json
from dot_runtime.communication import l1_prompt as communication_prompt


def dispatch(message, bridge):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    if 'id' not in message:  # Notifications do not produce protocol output.
        return None
    response = {'jsonrpc': '2.0', 'id': message['id']}
    method, params = message.get('method'), message.get('params', {})
    try:
        if method == 'initialize':
            result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'l1-dot-bridge', 'version': '3.0.3'},
                      'instructions': 'Read L1.md. L1 approves intermediate baselines; user reviews only the final report. '
                                      'Never fake human approval.\n' + communication_prompt()}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': bridge.tools()}
        elif method == 'tools/call':
            try:
                value = bridge.call(params['name'], params.get('arguments', {}))
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}], 'isError': False}
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
        else:
            response['error'] = {'code': -32601, 'message': 'Method not found'}
            return response
        response['result'] = result
    except (ValueError, TypeError, KeyError) as exc:
        response['error'] = {'code': -32602, 'message': str(exc)}
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    bridge = Bridge(read_json(args.config))
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if isinstance(message, list):
                responses = [dispatch(item, bridge) for item in message]
                response = [item for item in responses if item is not None] or None
            else:
                response = dispatch(message, bridge)
        except ValueError:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

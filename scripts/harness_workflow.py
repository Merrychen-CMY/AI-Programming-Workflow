#!/usr/bin/env python3
"""Private JSON pipe used by the native Harness plugin, not a model approval tool."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from dot_runtime.onboarding import dispatch
from dot_runtime.presentation import compact


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        from dot_runtime.host_tools import pipe_relay
        relay = os.environ.get('REQPILOT_HOST_RELAY') == '1'
        request = json.loads(sys.stdin.readline(1_000_001) if relay else sys.stdin.read(1_000_001))
        result = dispatch(request, host_relay=pipe_relay(sys.stdin, sys.stdout) if relay else None)
        if request.get('action') in ('status', 'bridge', 'report_confirm'):
            result = compact(result)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()

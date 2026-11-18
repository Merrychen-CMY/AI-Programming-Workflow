#!/usr/bin/env python3
"""Private JSON pipe used by the native Harness plugin, not a model approval tool."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from dot_runtime.onboarding import dispatch


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        request = json.loads(sys.stdin.read(1_000_001))
        result = dispatch(request)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()

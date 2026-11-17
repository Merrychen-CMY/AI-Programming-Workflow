#!/usr/bin/env python3
"""Local DOT CLI. State and approvals must live outside the worker project."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from dot_runtime.controller import Controller, initialize
from dot_runtime.adapter import CodexAdapter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Stage a new run without starting workers')
    for key in ('project', 'requirements', 'plan', 'run-dir'):
        init.add_argument('--' + key, required=True, type=Path)
    approval = sub.add_parser('approve', help='Human-only local terminal review of exact document hash')
    approval.add_argument('kind', choices=('requirements', 'plan', 'report'))
    approval.add_argument('--run-dir', required=True, type=Path)
    rejection = sub.add_parser('reject', help='Human rejects a pending artifact with a reason; no execution authority')
    rejection.add_argument('kind', choices=('requirements', 'plan', 'report'))
    rejection.add_argument('--run-dir', required=True, type=Path)
    rejection.add_argument('--reason', required=True)
    run = sub.add_parser('run', help='Run independent Codex sessions within approved scope and persistent budgets')
    run.add_argument('--run-dir', required=True, type=Path)
    run.add_argument('--codex', default='codex', help='Codex executable path; preserves configured model')
    status = sub.add_parser('status')
    status.add_argument('--run-dir', required=True, type=Path)
    reconcile = sub.add_parser('reconcile', help='Human confirms old UNKNOWN process/side effects reconciled')
    reconcile.add_argument('--run-dir', required=True, type=Path)
    revise = sub.add_parser('revise', help='Stage changed baselines; invalidate approvals, preserve counters')
    for key in ('requirements', 'plan', 'run-dir'):
        revise.add_argument('--' + key, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            state = initialize(args.project, args.requirements, args.plan, args.run_dir)
        else:
            controller = Controller(args.run_dir, CodexAdapter(args.codex) if args.command == 'run' else None)
            if args.command == 'approve':
                state = controller.approve(args.kind)
            elif args.command == 'reject':
                state = controller.reject(args.kind, args.reason)
            elif args.command == 'run':
                state = controller.run()
            elif args.command == 'revise':
                state = controller.revise(args.requirements, args.plan)
            elif args.command == 'reconcile':
                state = controller.reconcile()
            else:
                state = controller.state
        print(json.dumps({'state': state['state'], 'run_id': state['run_id'],
                          'implementation_attempts': state['implementation_attempts'],
                          'report': state['report'], 'run_dir': str(args.run_dir.resolve())}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print('DOT stopped: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

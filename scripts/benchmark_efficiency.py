"""Repeatable isolated workflow benchmark; no live model, user task or hardware."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_workflow_efficiency import CompleteWorkflow


def main():
    case = CompleteWorkflow('test_recover_missing_input_reuse_evidence_and_finish_existing_workflow')
    result = unittest.TestResult()
    case.run(result)
    if not result.wasSuccessful():
        for _, detail in result.errors + result.failures:
            print(detail, file=sys.stderr)
        return 1
    print(json.dumps(case.metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

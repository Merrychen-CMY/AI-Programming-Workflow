"""回归验证契约校验入口；所有人工输入仅位于临时目录，不操作产品。"""

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run_suite():
    ROOT = Path(__file__).resolve().parents[1]
    TOOL = ROOT / "tools" / "validate_contracts.py"
    ENV = os.environ.copy()
    ENV["PYTHONIOENCODING"] = "utf-8"
    checks = []


    def run(name, arguments, expected, require_text=None):
        command = [sys.executable, str(TOOL)] + [str(arg) for arg in arguments]
        completed = subprocess.run(command, env=ENV, text=True, encoding="utf-8",
                                   capture_output=True, timeout=15)
        output = completed.stdout + completed.stderr
        passed = completed.returncode == expected and (require_text is None or require_text in output)
        checks.append({"check": name, "expected_exit": expected, "actual_exit": completed.returncode,
                       "result": "PASS" if passed else "FAIL", "output": output})
        if not passed:
            raise AssertionError(name + ": " + output)


    draft = json.loads((ROOT / "dev-task.template.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="contract-cli-") as temporary:
        work = Path(temporary)

        def write(name, value):
            path = work / name
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return path

        run("all-draft-templates", [ROOT / f"{name}.template.json" for name in
                                   ("project-profile", "dev-task", "test-plan", "delivery-report")],
            0, "不是执行授权")
        run("draft-require-ready", ["--require-ready", ROOT / "dev-task.template.json"], 2, "DRAFT_BLOCKED")
        run("no-input", [], 3, "TOOL_ERROR")
        run("unknown-option", ["--not-a-real-option"], 3, "TOOL_ERROR")
        run("unreadable-instance", [work / "does-not-exist.json"], 3, "TOOL_ERROR")
        run("missing-schema-directory", ["--schema-dir", work / "missing", ROOT / "dev-task.template.json"], 3, "TOOL_ERROR")

        missing = copy.deepcopy(draft)
        del missing["project_id"]
        invalid_path = write("missing-field.json", missing)
        run("missing-required", [invalid_path], 1, "INVALID")
        unknown = copy.deepcopy(draft)
        unknown["unexpected"] = True
        run("unknown-field", [write("unknown.json", unknown)], 1, "INVALID")
        promoted = copy.deepcopy(draft)
        promoted["status"] = "READY"
        run("empty-draft-promoted-ready", [write("empty-ready.json", promoted)], 1, "INVALID")
        for number, reference in enumerate(("../dev-task.schema.json", str((work.parent / "outside-schema.json").resolve()),
                                            "https://example.invalid/schema.json", "common.schema.json")):
            outside = copy.deepcopy(draft)
            outside["schema_ref"] = reference
            run("schema-ref-refused-" + str(number), [write(f"outside-{number}.json", outside)], 1, "schema_ref")

        invalid_json = work / "invalid.json"
        invalid_json.write_text('{"schema_ref":', encoding="utf-8")
        run("malformed-json", [invalid_json], 1, "JSON")
        duplicate = work / "duplicate.json"
        duplicate.write_text('{"schema_ref":"dev-task.schema.json","schema_ref":"project-profile.schema.json"}', encoding="utf-8")
        run("duplicate-json-key", [duplicate], 1, "重复对象键")
        nonfinite = work / "nonfinite.json"
        nonfinite.write_text('{"schema_ref":"dev-task.schema.json","value":NaN}', encoding="utf-8")
        run("nonfinite-json", [nonfinite], 1, "非有限")
        for label, value in (("positive", "1e309"), ("negative", "-1e309")):
            overflow = work / ("overflow-" + label + ".json")
            overflow.write_text('{"schema_ref":"dev-task.schema.json","value":' + value + '}', encoding="utf-8")
            run("overflow-float-" + label, [overflow], 1, "有限浮点表示范围")
        finite = copy.deepcopy(draft)
        finite["budgets"]["task_seconds"] = 100.0
        finite_path = write("finite-float.json", finite)
        finite_path.write_text(finite_path.read_text(encoding="utf-8").replace('100.0', '1e2'), encoding="utf-8")
        run("finite-float-exponent", [finite_path], 0, "STRUCTURE_OK")
        run("invalid-outranks-draft", ["--require-ready", invalid_path, ROOT / "dev-task.template.json"], 1, "DRAFT_BLOCKED")
        run("tool-error-outranks-invalid", [invalid_path, work / "missing.json"], 3, "INVALID")

        report = json.loads((ROOT / "delivery-report.template.json").read_text(encoding="utf-8"))
        report.update(document_id="DOC-cli-fixture", project_id="PROJECT-cli-fixture", status="FINAL",
                      description="synthetic schema fixture, no test execution", task_id="TASK-cli-fixture",
                      acceptance_reason="no tests run; fixture only", generated_at="2026-11-16T00:00:00Z",
                      recovery_or_rollback="not applicable; fixture only", acceptance_evidence="fixture only")
        run("final-incomplete-is-not-accepted", ["--require-ready", write("final-incomplete-fixture.json", report)],
            0, "acceptance_status=INCOMPLETE")
        run("explicit-local-schema-dir", ["--schema-dir", ROOT, ROOT / "dev-task.template.json"], 0, "STRUCTURE_OK")

    result = {"scope": "Delivered contract-validation CLI only; no product or hardware tests",
              "python": sys.version, "checks": checks, "passed": len(checks), "failed": 0}
    print(f"CLI QA: {len(checks)} checks passed; no product or hardware tests executed.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="可选 JSON 报告路径；默认不写文件")
    args = parser.parse_args()
    exit_code = 0
    try:
        result = run_suite()
    except Exception as error:
        result = {"scope": "Contract-tool tests only; no product or hardware tests",
                  "result": "FAIL", "error": type(error).__name__ + ": " + str(error)}
        print("FAIL: " + result["error"], file=sys.stderr)
        exit_code = 1
    if args.report is not None:
        try:
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, UnicodeError) as error:
            print("REPORT_ERROR: " + str(error), file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

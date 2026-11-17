"""验证本包五份 Schema 与人工结构样例；这些样例不是产品验收证据。"""

import argparse
import copy
import json
from pathlib import Path
import sys


def run_suite():
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    ROOT = Path(__file__).resolve().parents[1]


    def read(name):
        return json.loads((ROOT / name).read_text(encoding="utf-8"))


    schemas = {path.name: read(path.name) for path in ROOT.glob("*.schema.json")}
    registry = Registry().with_resources([(value["$id"], Resource.from_contents(value)) for value in schemas.values()])
    checks = []


    def check(name, validator, document, expected):
        errors = list(validator.iter_errors(document))
        accepted = not errors
        checks.append({"check": name, "expected_schema_acceptance": expected,
                       "observed_schema_acceptance": accepted,
                       "result": "PASS" if expected == accepted else "FAIL",
                       "first_error": errors[0].message if errors else None})
        if accepted != expected:
            raise AssertionError(name + ": " + (errors[0].message if errors else "unexpectedly accepted"))


    validators = {}
    templates = {}
    for filename, value in schemas.items():
        Draft202012Validator.check_schema(value)
        checks.append({"check": "metaschema:" + filename, "result": "PASS"})
    for stem in ("project-profile", "dev-task", "test-plan", "delivery-report"):
        validator = Draft202012Validator(schemas[stem + ".schema.json"], registry=registry, format_checker=FormatChecker())
        validators[stem] = validator
        template = read(stem + ".template.json")
        templates[stem] = template
        check(stem + ":draft", validator, template, True)
        missing = copy.deepcopy(template)
        del missing["project_id"]
        check(stem + ":missing-required-field", validator, missing, False)
        unknown = copy.deepcopy(template)
        unknown["unknown_field"] = "invalid"
        check(stem + ":unknown-field", validator, unknown, False)
        invalid = copy.deepcopy(template)
        invalid["status"] = "PASSED"
        check(stem + ":invalid-enum", validator, invalid, False)
        promoted = copy.deepcopy(template)
        promoted["status"] = "FINAL" if stem == "delivery-report" else "READY"
        check(stem + ":empty-promotion", validator, promoted, False)

    # These are structural positive fixtures held in memory. Their zero hashes and
    # fabricated identifiers do not identify real files and are never deliverables.
    artifact = {"artifact_id": "ART-synthetic", "path": "synthetic-fixture.bin", "sha256": "0" * 64,
                "size_bytes": 1, "role": "OTHER"}
    source = {"repository": "fixture", "revision": "synthetic", "worktree_clean": True,
              "dirty_patch": None, "untracked_artifacts": [], "source_manifest": artifact}
    command = {"argv": ["synthetic-tool"], "working_directory": ".", "environment_profile": "synthetic",
               "expected_tool_version": "synthetic"}
    oracle = {"kind": "APPROVED_REQUIREMENT", "reference": "synthetic-requirement", "version": "1", "rationale": "schema QA only"}
    test_case = {
        "test_id": "TEST-synthetic", "title": "schema QA only", "requirement_ids": ["REQ-synthetic"],
        "criterion_ids": ["AC-synthetic"], "profiles": ["quick"], "required": True,
        "applicability": "APPLICABLE", "applicability_reason": None, "applicability_decision_reference": None,
        "test_kind": "HOST_BEHAVIOR", "execution_environment": "HOST", "execution_mode": "AUTOMATED", "entrypoint": command,
        "subject": {"source_paths": ["synthetic.c"], "production_c_implementation": True,
                    "build_target_or_binary": "synthetic-target", "test_substitutes": [], "implementation_evidence": "synthetic"},
        "preconditions": [], "stimulus_and_steps": ["schema QA only"],
        "assertions": [{"assertion_id": "ASSERT-synthetic", "condition": "synthetic", "units_and_tolerance": "synthetic", "oracle": oracle}],
        "timeout_seconds": 1, "retry_policy": {"maximum_retries": 0, "allowed_reasons": [], "retain_every_attempt": True, "test_failure_may_retry_as_infrastructure": False},
        "skip_policy": {"allowed_environment_reasons": [], "framework_supports_exit_77": False, "required_skip_blocks_acceptance": True, "failure_timeout_or_compile_error_can_skip": False},
        "device_ids": [], "authorization_grant_ids": [], "manual_steps": [], "expected_evidence": ["synthetic"], "cleanup_and_safe_state": "synthetic",
    }


    def active(stem):
        result = copy.deepcopy(templates[stem])
        result.update(document_id="DOC-synthetic", project_id="PROJECT-synthetic", status="READY", description="schema QA only")
        return result


    project = active("project-profile")
    project.update(standard_reference="synthetic", repository_root=".", platforms=["synthetic"], activation_review="synthetic")
    project["source_scope"]["owned_paths"] = ["src"]
    project["toolchains"] = [{"id": "TOOL-synthetic", "name": "synthetic", "version": "1", "configuration_artifact": artifact}]
    for profile in project["check_profiles"]:
        profile.update(configuration_state="CONFIGURED", entrypoint=command, timeout_seconds=1)
    project["default_budgets"]["task_seconds"] = 1
    project["artifact_retention"] = {"archive_root": ".", "retention_policy": "synthetic", "access_policy": "synthetic"}
    check("project-profile:ready-fixture", validators["project-profile"], project, True)
    project_bad = copy.deepcopy(project)
    project_bad["check_profiles"][3]["configuration_state"] = "UNCONFIGURED"
    check("project-profile:unconfigured-profile", validators["project-profile"], project_bad, False)

    task = active("dev-task")
    task.update(project_profile_reference="synthetic", objective="synthetic", change_mode="FUNCTIONAL", risk="R2", risk_rationale="synthetic", starting_source=source,
                test_plan_reference="synthetic", activation_review="synthetic")
    task["scope"].update(allowed_paths=["src"], allowed_modules=["synthetic"], unchanged_behaviors=["synthetic"])
    task["requirements"] = [{"requirement_id": "REQ-synthetic", "statement": "synthetic", "source": "synthetic", "source_version": "1", "verification_method": "AUTOMATED_TEST"}]
    task["acceptance_criteria"] = [{"criterion_id": "AC-synthetic", "requirement_ids": ["REQ-synthetic"], "observable_behavior": "synthetic", "pass_condition": "synthetic", "oracle": oracle, "units_and_tolerance": "synthetic", "required_test_ids": ["TEST-synthetic"]}]
    task["required_test_profiles"] = ["quick"]
    task["authorization_grant_ids"] = ["AUTH-synthetic"]
    task["budgets"]["task_seconds"] = 1
    check("dev-task:ready-fixture", validators["dev-task"], task, True)
    task_bad = copy.deepcopy(task)
    task_bad["change_mode"] = "COMMENT_ONLY"
    task_bad["change_manifest"] = [{"path": "src/x.c", "category": "SOURCE", "purpose": "synthetic", "behavior_effect": "synthetic", "requirement_ids": []}]
    check("dev-task:comment-only-source-change", validators["dev-task"], task_bad, False)

    plan = active("test-plan")
    plan.update(task_reference="synthetic", project_profile_reference="synthetic", planned_source=source,
                requirement_ids=["REQ-synthetic"], criterion_ids=["AC-synthetic"], selected_profiles=["quick"],
                selection_rationale="synthetic", cases=[test_case], activation_review="synthetic")
    plan["review"].update(oracle_and_coverage_review="synthetic", hardware_scope_review="not applicable; schema fixture")
    check("test-plan:ready-fixture", validators["test-plan"], plan, True)
    plan_bad = copy.deepcopy(plan)
    plan_bad["cases"] = []
    check("test-plan:zero-cases", validators["test-plan"], plan_bad, False)
    plan_bad = copy.deepcopy(plan)
    plan_bad["cases"][0]["subject"]["production_c_implementation"] = False
    check("test-plan:parallel-model-only", validators["test-plan"], plan_bad, False)
    plan_bad = copy.deepcopy(plan)
    plan_bad["cases"][0]["execution_environment"] = "DEVICE"
    check("test-plan:device-without-identity-or-grant", validators["test-plan"], plan_bad, False)

    report = copy.deepcopy(templates["delivery-report"])
    report.update(document_id="DOC-synthetic", project_id="PROJECT-synthetic", status="FINAL", description="schema QA only",
                  task_id="TASK-synthetic", test_plan_artifact=artifact, acceptance_status="ACCEPTED", acceptance_reason="synthetic",
                  generated_at="2026-11-16T00:00:00Z", recovery_or_rollback="synthetic", independent_review="synthetic", acceptance_evidence="synthetic")
    report["final_identity"].update(source=source, configuration_artifacts=[artifact], toolchain_artifacts=[artifact],
                                    firmware_applicability="NOT_APPLICABLE", firmware_not_applicable_reason="schema fixture", diagnostic_release_differences="none; fixture")
    report["identity_checks"].update(evidence_matches_final_source_and_config=True, checks_after_last_affected_change=True,
                                    hardware_not_used_reason="schema fixture", evidence_reference="synthetic")
    report["execution_summary"].update(discovered=1, executed=1, passed=1)
    report["test_results"] = [{"test_id": "TEST-synthetic", "required": True, "applicability": "APPLICABLE", "applicability_decision_reference": None,
                               "result": "PASS", "reason": None,
                               "attempts": [{"attempt_number": 1, "started_at": "2026-11-16T00:00:00Z", "finished_at": "2026-11-16T00:00:01Z", "result": "PASS", "exit_code": 0, "evidence_artifact_ids": ["ART-synthetic"], "retry_reason": None}],
                               "assertion_results": [{"assertion_id": "ASSERT-synthetic", "result": "PASS", "observed": "synthetic", "evidence_artifact_ids": ["ART-synthetic"]}], "evidence_artifact_ids": ["ART-synthetic"]}]
    report["evidence_artifacts"] = [artifact]
    check("delivery-report:accepted-fixture", validators["delivery-report"], report, True)
    for field in ("discovered", "executed"):
        bad = copy.deepcopy(report)
        bad["execution_summary"][field] = 0
        check("delivery-report:zero-" + field, validators["delivery-report"], bad, False)
    for result in ("FAIL", "ERROR", "BLOCKED", "SKIP", "NOT_RUN"):
        bad = copy.deepcopy(report)
        bad["test_results"][0]["result"] = result
        bad["test_results"][0]["reason"] = "schema fixture"
        check("delivery-report:required-" + result, validators["delivery-report"], bad, False)
    bad = copy.deepcopy(report)
    bad["identity_checks"]["checks_after_last_affected_change"] = False
    check("delivery-report:old-test-evidence", validators["delivery-report"], bad, False)
    bad = copy.deepcopy(report)
    bad["test_results"][0]["evidence_artifact_ids"] = []
    check("delivery-report:no-pass-evidence", validators["delivery-report"], bad, False)
    bad = copy.deepcopy(templates["delivery-report"])
    bad["acceptance_status"] = "ACCEPTED"
    check("delivery-report:draft-accepted", validators["delivery-report"], bad, False)

    result = {"scope": "JSON Schema and artificial structural fixtures only; no product code, hardware or test execution",
              "validator": "jsonschema 4.23.0 / Draft202012Validator / FormatChecker",
              "checks": checks, "passed": len(checks), "failed": 0}
    print(f"Schema QA: {len(checks)} checks passed; no product or hardware tests executed.")
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

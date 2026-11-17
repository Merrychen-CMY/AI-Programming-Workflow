"""检查 V1.2 可读性说明与既有契约兼容；不执行产品或硬件测试。"""

import argparse
import copy
import json
from pathlib import Path
import sys


def run_suite():
    """返回实际执行的补充检查记录；模板始终保留 DRAFT 状态。"""
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    root = Path(__file__).resolve().parents[1]
    stems = ("project-profile", "dev-task", "test-plan", "delivery-report")
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in root.glob("*.schema.json")
    }
    templates = {
        stem: json.loads((root / f"{stem}.template.json").read_text(encoding="utf-8"))
        for stem in stems
    }
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema))
        for schema in schemas.values()
    )
    validator = Draft202012Validator(
        schemas["project-profile.schema.json"], registry=registry,
        format_checker=FormatChecker(),
    )
    checks = []

    def check(name, condition):
        checks.append({"check": name, "result": "PASS" if condition else "FAIL"})
        if not condition:
            raise AssertionError(name)

    check("five-local-schema-identities-remain-v1.1",
          len(schemas) == 5 and all("/v1.1/" in item["$id"] for item in schemas.values()))
    check("all-template-structure-versions-remain-v1.1",
          all(item["schema_version"] == "1.1" for item in templates.values()))
    check("all-templates-remain-draft",
          all(item["status"] == "DRAFT" for item in templates.values()))
    report = templates["delivery-report"]
    check("no-fabricated-delivery-execution",
          report["acceptance_status"] == "INCOMPLETE" and
          report["test_results"] == [] and report["evidence_artifacts"] == [] and
          report["execution_summary"]["executed"] == 0)
    project = templates["project-profile"]
    check("new-template-targets-standard-v1.2", project["standard_version"] == "1.2")

    for version, expected in (("1.1", True), ("1.2", True), ("1.3", False)):
        candidate = copy.deepcopy(project)
        candidate["standard_version"] = version
        accepted = not list(validator.iter_errors(candidate))
        check(f"standard-version-{version}-acceptance-{expected}", accepted == expected)
    candidate = copy.deepcopy(project)
    candidate["schema_version"] = "1.2"
    check("body-version-is-not-structure-version",
          bool(list(validator.iter_errors(candidate))))

    readme = (root / "README.txt").read_text(encoding="utf-8")
    expected_rules = {
        "READ-001": "抽象层次与业务主线",
        "READ-002": "条件表达与业务分组",
        "READ-003": "提前返回与清理",
        "READ-004": "查询与动作分离",
        "READ-005": "命名及操作/结果语义",
        "READ-006": "有意义辅助函数及冗余分支",
        "READ-007": "复杂度触发评审",
        "READ-008": "重构语义与时序保持",
    }
    check("readme-eight-rule-mappings",
          all(f"{rule}：{title}" in readme for rule, title in expected_rules.items()))
    task = templates["dev-task"]
    check("development-contract-has-existing-readability-fields",
          all(field in task for field in (
              "requirements", "acceptance_criteria", "interface_state_and_data_contracts",
              "open_decisions", "change_manifest")) and
          "unchanged_behaviors" in task["scope"])
    plan = templates["test-plan"]
    check("test-contract-has-existing-case-and-review-fields",
          all(field in plan for field in ("cases", "criterion_ids", "review")) and
          "oracle_and_coverage_review" in plan["review"])
    check("delivery-contract-has-existing-evidence-and-limits-fields",
          all(field in report for field in (
              "change_manifest", "test_results", "evidence_artifacts",
              "independent_review", "acceptance_evidence", "limitations_and_unverified_items")))
    check("readme-states-tool-and-verification-boundaries",
          "本包没有安装、配置或运行项目级 clang-tidy 可读性检查" in readme and
          "有限用例通过不意味着对所有输入形式化等价" in readme and
          "不是机械硬限" in readme)

    result = {
        "scope": "V1.2 contract compatibility and documentation checks only; no product or hardware tests",
        "standard_version": "1.2",
        "schema_version": "1.1",
        "checks": checks,
        "passed": len(checks),
        "failed": 0,
    }
    print(f"Readability contract QA: {len(checks)} checks passed; no product or hardware tests executed.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="可选 JSON 报告路径；父目录需已存在")
    args = parser.parse_args()
    exit_code = 0
    try:
        result = run_suite()
    except Exception as error:
        result = {
            "scope": "Contract compatibility checks only; no product or hardware tests",
            "result": "FAIL",
            "error": type(error).__name__ + ": " + str(error),
        }
        print("FAIL: " + result["error"], file=sys.stderr)
        exit_code = 1
    if args.report is not None:
        try:
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
        except (OSError, UnicodeError) as error:
            print("REPORT_ERROR: " + str(error), file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

"""Exercise the real pinned Doxygen gate with teaching fixtures and refusals.

This is documentation-tool integration verification, not a C behaviour test or
proof of product compliance. No fake executable, mock report or skip is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from check_doxygen import check_doxygen


FIXTURES = (Path(__file__).resolve().parents[1] / "standards" / "embedded-c" /
            "v1.3" / "doxygen" / "examples")
FILES = ("commented_module.h", "commented_module.c")


class VerificationFailure(RuntimeError):
    """A real integration scenario did not meet its expected contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_fixture(work: Path, name: str) -> tuple[Path, Path]:
    source, output = work / name / "source", work / name / "evidence"
    source.mkdir(parents=True)
    for filename in FILES:
        shutil.copyfile(FIXTURES / filename, source / filename)
    return source, output


def replace_once(source: Path, old: str, new: str) -> None:
    """Refuse stale fixture anchors rather than silently testing unchanged code."""
    header = source / "commented_module.h"
    text = header.read_text(encoding="utf-8")
    require(text.count(old) == 1,
            "Fixture mutation must match exactly once: " + repr(old[:90]))
    require(old != new, "Fixture mutation must change its input")
    header.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def verify_command(command: dict, executable: str, phase: str) -> None:
    require(command.get("phase") == phase, "Missing real " + phase + " command")
    require(command.get("argv", [None])[0] == executable,
            phase + " did not invoke the requested Doxygen executable")
    require(command.get("status") == "PASS" and command.get("exit_code") == 0,
            phase + " did not actually exit successfully")
    require(type(command.get("process_id")) is int and command["process_id"] > 0,
            phase + " has no real process ID")
    for stream in ("stdout", "stderr"):
        log = Path(command[stream + "_path"])
        require(log.is_file(), phase + " is missing its raw " + stream + " log")
        require(log.read_bytes().decode("utf-8", errors="replace") == command[stream],
                phase + " report differs from its raw " + stream + " log")


def verify_generated(report: dict, source: Path, output: Path,
                     executable: str, version: str, files=FILES) -> None:
    """Check actual execution and independently read/hash the emitted evidence."""
    require(report.get("checker") == "doxygen-owned-c", "Unexpected checker identity")
    require(report.get("actual_version") == version, "Actual Doxygen version differs")
    commands = report.get("commands", [])
    require(len(commands) == 2, "Expected actual version and generation processes")
    verify_command(commands[0], executable, "version")
    require(commands[0]["argv"] == [executable, "--version"], "Wrong version command")
    require(bool(commands[0]["stdout"].strip()), "Version output is empty")
    verify_command(commands[1], executable, "generate")
    config = output / "Doxyfile"
    require(commands[1]["argv"] == [executable, str(config)], "Wrong generation config")
    require(config.is_file() and config.stat().st_size > 0, "Controlled config is empty")
    require(digest(config) == report.get("config_sha256"), "Config hash differs")
    require(report.get("source_files") == {name: digest(source / name) for name in files},
            "Source evidence does not describe the actual fixture files")
    xml_files = report.get("xml_files", {})
    require("generated/xml/index.xml" in xml_files and len(xml_files) > 1,
            "Real generation did not produce nonempty indexed XML evidence")
    for relative, expected_digest in xml_files.items():
        xml_path = output / relative
        require(xml_path.resolve().is_relative_to(output.resolve()),
                "XML evidence path escapes its output directory")
        require(xml_path.is_file() and xml_path.stat().st_size > 0,
                "Generated XML is missing or empty: " + relative)
        require(digest(xml_path) == expected_digest, "Generated XML hash differs: " + relative)
    counts = report.get("checks", {})
    require(counts.get("source_file_count") == len(files) and counts.get("compound_count", 0) >= 3
            and counts.get("member_count", 0) > 0 and counts.get("parameter_count", 0) > 0,
            "Generated XML did not inspect the fixture files, structure and parameters")
    saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
    require(saved == report, "Saved report does not match the returned evidence")


def tree_hashes(folder: Path) -> dict[str, str]:
    return {path.relative_to(folder).as_posix(): digest(path)
            for path in folder.rglob("*") if path.is_file()}


def diagnostic(report: dict) -> str:
    """Keep failures useful after temporary evidence cleanup without dumping JSON."""
    parts = report.get("findings", [])[:3]
    for text in [report.get("warnings", ""),
                 *(command.get("stderr", "") for command in report.get("commands", []))]:
        if text.strip():
            parts.append(" ".join(text.split())[:500])
    return "; ".join(parts)[:1200]


def run_scenarios(work: Path, executable: str, version: str) -> int:
    completed = 0

    def completed_case(name: str) -> None:
        nonlocal completed
        completed += 1
        print("PASS " + name)

    source, output = copy_fixture(work, "original")
    report = check_doxygen(source, output, version, doxygen=executable)
    require(report["status"] == "PASS", "Original fixture must PASS: " + diagnostic(report))
    verify_generated(report, source, output, executable, version)
    completed_case("original example: real Doxygen generation and XML inspection")

    mutations = [
        ("missing_parameter", " * @param[in] value_count 输入数组的有效元素数量，单位：个；必须大于零，\n"
         " * 且不超过 SAMPLE_MAX_COUNT 减去累加器当前 count 后的剩余容量。\n", "",
         "sample_accumulator_add", "parameter"),
        ("wrong_parameter_name", "@param[in] value_count 输入数组", "@param[in] missing_value_count 输入数组",
         "sample_accumulator_add", "parameter"),
        ("missing_struct_member", " /**< 所有已接收采样值之和，无量纲，范围为 0 到 count * 1000。 */", "",
         "total", "documentation"),
        ("missing_nonvoid_result", " * @return 本次同步操作的完成状态。\n"
         " * @retval SAMPLE_STATUS_OK 整批校验完成，累加器已更新。\n"
         " * @retval SAMPLE_STATUS_INVALID_ARGUMENT 指针为空、数量为零，或累加器字段越界。\n"
         " * @retval SAMPLE_STATUS_CAPACITY_EXCEEDED 批次数量超过剩余容量。\n"
         " * @retval SAMPLE_STATUS_VALUE_OUT_OF_RANGE 存在超过 SAMPLE_MAX_VALUE 的采样值。\n", "",
         "sample_accumulator_add", "return"),
    ]
    for name, old, new, member, category in mutations:
        changed_source, changed_output = copy_fixture(work, name)
        replace_once(changed_source, old, new)
        changed = check_doxygen(changed_source, changed_output, version, doxygen=executable)
        require(changed["status"] == "FAIL", name + " must be refused, got " +
                changed["status"] + ": " + diagnostic(changed))
        verify_generated(changed, changed_source, changed_output, executable, version)
        require(any(member in finding and category in finding.lower()
                    for finding in changed.get("findings", [])),
                name + " failed without identifying the intended documentation defect")
        completed_case(name + ": real Doxygen/XML gate refused the mutated documentation")

    # The same named parameter at both declaration and definition caused the LED task failure.
    source, duplicate_output = copy_fixture(work, "duplicate-definition")
    implementation = source / "commented_module.c"
    original = implementation.read_text(encoding="utf-8")
    # Select by the actual definition, without changing the private helper's valid tags.
    definition = original.index("void sample_accumulator_reset(")
    start = original.rfind("/**", 0, definition)
    duplicate = original[:start] + original[start:].replace(
        "/**", "/**\n * @param[in,out] accumulator 重复声明处契约的错误示例。", 1)
    implementation.write_text(duplicate, encoding="utf-8", newline="\n")
    rejected = check_doxygen(source, duplicate_output, version, doxygen=executable)
    require(rejected['status'] == 'FAIL' and any(
        'sample_accumulator_reset' in item and 'parameter' in item for item in rejected['findings']),
        'Declaration/definition duplicate parameter must be rejected: ' + diagnostic(rejected))
    verify_generated(rejected, source, duplicate_output, executable, version)
    completed_case('duplicate declaration/definition parameter: refused without suppressing warnings')
    implementation.write_text(original, encoding="utf-8", newline="\n")
    repaired_output = work / 'repaired-definition' / 'evidence'
    repaired = check_doxygen(source, repaired_output, version, doxygen=executable)
    require(repaired['status'] == 'PASS', 'Restored implementation paragraphs must pass: ' + diagnostic(repaired))
    verify_generated(repaired, source, repaired_output, executable, version)
    completed_case('repair duplicate: header contract and implementation explanation both retained')

    source, duplicate_output = copy_fixture(work, "duplicate-header")
    replace_once(source, " * @param[in] value_count 输入数组",
                 " * @param[in] value_count 重复参数标签。\n * @param[in] value_count 输入数组")
    rejected = check_doxygen(source, duplicate_output, version, doxygen=executable)
    require(rejected['status'] == 'FAIL' and any(
        'sample_accumulator_add' in item and 'parameter' in item for item in rejected['findings']),
        'Duplicate within the header must still fail: ' + diagnostic(rejected))
    verify_generated(rejected, source, duplicate_output, executable, version)
    completed_case('duplicate in one header: still refused')

    led_source, led_output = work / 'led-module-source', work / 'led-module-evidence'
    shutil.copytree(FIXTURES.parent / 'module-example', led_source)
    led_files = tuple(sorted(p.name for p in led_source.iterdir() if p.suffix in ('.c', '.h')))
    led = check_doxygen(led_source, led_output, version, doxygen=executable)
    require(led['status'] == 'PASS', 'LED entry/pattern/driver documentation must pass: ' + diagnostic(led))
    verify_generated(led, led_source, led_output, executable, version, led_files)
    completed_case('LED module example: entry, pattern and simulated driver documented together')

    empty_source, empty_output = work / "empty-source", work / "empty-evidence"
    empty_source.mkdir()
    empty = check_doxygen(empty_source, empty_output, version, doxygen=executable)
    require(empty["status"] == "ERROR" and not empty["commands"] and not empty["xml_files"],
            "Empty source selection must ERROR before tool execution")
    require(any("No owned .c/.h" in finding for finding in empty["findings"]),
            "Empty source rejection has no source-selection diagnostic")
    completed_case("empty source: ERROR before execution; no fabricated XML")

    mismatch_source, mismatch_output = copy_fixture(work, "version-mismatch")
    wrong_version = "0.0.0" if version != "0.0.0" else "999.0.0"
    mismatch = check_doxygen(mismatch_source, mismatch_output, wrong_version, doxygen=executable)
    require(mismatch["status"] == "FAIL" and mismatch["actual_version"] == version,
            "Version mismatch must FAIL against the actual executable version")
    require(len(mismatch["commands"]) == 1 and not mismatch["xml_files"],
            "Unapproved tool version must not generate documentation")
    verify_command(mismatch["commands"][0], executable, "version")
    completed_case("version mismatch: actual version process ran; generation refused")

    before = tree_hashes(output)
    try:
        check_doxygen(source, output, version, doxygen=executable)
    except ValueError as exc:
        require("Evidence directory" in str(exc), "Output reuse failed for an unrelated reason")
    else:
        raise VerificationFailure("Reusing an existing evidence directory was accepted")
    require(tree_hashes(output) == before, "Refused output reuse modified existing evidence")
    completed_case("output reuse: refused before execution; original evidence unchanged")

    require(completed == 12, "The intended twelve scenarios did not all execute")
    return completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doxygen", required=True, help="Real, approved Doxygen executable")
    parser.add_argument("--expected-version", required=True, help="Exact approved release, e.g. 1.18.0")
    args = parser.parse_args(argv)
    executable = shutil.which(args.doxygen)
    if executable is None:
        print("FAIL: Doxygen executable is unavailable; integration verification cannot be skipped.",
              file=sys.stderr)
        return 1
    executable = str(Path(executable).resolve())
    try:
        with tempfile.TemporaryDirectory(prefix="doxygen-example-check-") as allocated:
            work = Path(allocated).resolve()
            require(work.parent == Path(tempfile.gettempdir()).resolve()
                    and work.name.startswith("doxygen-example-check-"),
                    "Temporary workspace is outside the allocated cleanup scope")
            count = run_scenarios(work, executable, args.expected_version)
    except (OSError, ValueError, VerificationFailure) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        return 1
    print(f"PASS: {count} real-tool integration scenarios; 9 XML generations and 10 version probes. "
          "These check documentation structure and refusals, not C behaviour or product compliance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

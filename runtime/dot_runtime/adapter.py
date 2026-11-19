"""A fresh, sandboxed Codex CLI session for each delegated responsibility.

The adapter checks transport, session identity and structured reports. It does
not certify arbitrary test-count claims or application correctness. The
controller must bind evidence to its candidate, approved plan and test reports.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess

from .scope_constraints import prompt as scope_prompt
from .app_server import ProtocolError, run_session


STATUSES = ("PASS", "FAIL", "ERROR", "TIMEOUT", "UNKNOWN")
ROOT_CAUSES = (
    "implementation", "product_requirement", "task_plan_or_design",
    "verification_plan_or_test", "test_infrastructure", "evidence_collection",
    "delivery_target_or_authorization", "security", "needs_analysis", "mixed",
)
ROLES = {"implementation": "workspace-write", "test": "workspace-write",
         "review": "read-only", "triage": "read-only"}
EVIDENCE_PROPERTIES = {
    "kind": {"type": "string", "enum": ["command", "review"]},
    "description": {"type": "string"}, "command": {"type": "string"},
    "output": {"type": "string"}, "exit_code": {"type": "integer"},
    "test_count": {"type": "integer", "minimum": 0},
}
RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": EVIDENCE_PROPERTIES, "required": list(EVIDENCE_PROPERTIES),
        }},
        "findings": {"type": "array", "items": {"type": "string"}},
        "root_cause": {"type": "string", "enum": list(ROOT_CAUSES)},
        "root_cause_key": {"type": "string"},
    },
    "required": ["status", "summary", "evidence", "findings", "root_cause", "root_cause_key"],
}

# L2 has a stronger contract than implementation/test reports. An inconclusive
# diagnosis still needs a bounded next investigation, not an empty verdict.
TRIAGE_ACTIONS = ('repair', 'reverify', 'revise_baseline', 'continue_investigation',
                  'request_authorization', 'recommend_stop')
SOLUTION_PROPERTIES = {
    'description': {'type': 'string'}, 'expected_benefit': {'type': 'string'},
    'verification_steps': {'type': 'array', 'items': {'type': 'string'}},
    'required_resources': {'type': 'array', 'items': {'type': 'string'}},
    'permission_gaps': {'type': 'array', 'items': {'type': 'string'}},
}
TRIAGE_PROPERTIES = {
    'solutions': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'properties': SOLUTION_PROPERTIES, 'required': list(SOLUTION_PROPERTIES)}},
    'investigation_steps': {'type': 'array', 'items': {'type': 'string'}},
    'missing_information': {'type': 'array', 'items': {'type': 'string'}},
    'permission_gaps': {'type': 'array', 'items': {'type': 'string'}},
    'facility_actions': {'type': 'array', 'items': {'type': 'string'}},
    'recommended_action': {'type': 'string', 'enum': list(TRIAGE_ACTIONS)},
}
TRIAGE_SCHEMA = {**RESULT_SCHEMA, 'properties': {**RESULT_SCHEMA['properties'], **TRIAGE_PROPERTIES},
                 'required': RESULT_SCHEMA['required'] + list(TRIAGE_PROPERTIES)}


def _nonempty_strings(value, *, required=False):
    return (isinstance(value, list) and (not required or bool(value))
            and all(isinstance(item, str) and item.strip() for item in value))


def _valid_triage(report):
    if not isinstance(report, dict) or set(report) != set(TRIAGE_SCHEMA['required']):
        return False
    if not _valid_report({key: report[key] for key in RESULT_SCHEMA['required']}):
        return False
    if report['recommended_action'] not in TRIAGE_ACTIONS:
        return False
    for key in ('investigation_steps', 'missing_information', 'permission_gaps', 'facility_actions'):
        if not _nonempty_strings(report[key]):
            return False
    solutions = report['solutions']
    if not isinstance(solutions, list) or not (solutions or report['investigation_steps']):
        return False
    for solution in solutions:
        if not isinstance(solution, dict) or set(solution) != set(SOLUTION_PROPERTIES):
            return False
        if any(not isinstance(solution[key], str) or not solution[key].strip()
               for key in ('description', 'expected_benefit')):
            return False
        if not _nonempty_strings(solution['verification_steps'], required=True):
            return False
        if any(not _nonempty_strings(solution[key]) for key in ('required_resources', 'permission_gaps')):
            return False
    if report['root_cause'] == 'needs_analysis' and not report['investigation_steps']:
        return False
    action = report['recommended_action']
    if action != 'reverify' and report['facility_actions']:
        return False
    if action in ('repair', 'reverify'):
        if (not solutions or report['root_cause'] == 'needs_analysis' or report['permission_gaps']
                or report['missing_information'] or any(s['permission_gaps'] for s in solutions)):
            return False
    return True


def _valid_report(report: object) -> bool:
    if not isinstance(report, dict) or set(report) != set(RESULT_SCHEMA["required"]):
        return False
    if report["status"] not in STATUSES or report["root_cause"] not in ROOT_CAUSES:
        return False
    if not isinstance(report["root_cause_key"], str):
        return False
    if not isinstance(report["summary"], str) or not report["summary"].strip():
        return False
    if not isinstance(report["findings"], list) or not all(
        isinstance(item, str) and item.strip() for item in report["findings"]
    ):
        return False
    if not isinstance(report["evidence"], list):
        return False
    for item in report["evidence"]:
        if not isinstance(item, dict) or set(item) != set(EVIDENCE_PROPERTIES):
            return False
        if item["kind"] not in ("command", "review"):
            return False
        if not all(isinstance(item[k], str) for k in ("description", "command", "output")):
            return False
        if type(item["exit_code"]) is not int or type(item["test_count"]) is not int:
            return False
        if item["test_count"] < 0 or not item["description"].strip():
            return False
        if item["kind"] == "command" and not item["command"].strip():
            return False
        if item["kind"] == "review" and (
            item["command"] or item["exit_code"] != 0 or item["test_count"] != 0
        ):
            return False
    return True


def _events(path: Path) -> tuple[str | None, list[dict], bool, str | None]:
    sessions, commands, completed = set(), [], False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("JSONL event is not an object")
            if event.get("type") == "thread.started":
                identifier = event.get("thread_id")
                if not isinstance(identifier, str) or not identifier.strip():
                    raise ValueError("Missing thread.started thread_id")
                sessions.add(identifier)
            if event.get("type") == "turn.completed":
                completed = True
            if event.get("type") in ("error", "turn.failed"):
                raise ValueError("Codex reported a failed turn or runtime error")
            item = event.get("item", {})
            if event.get("type") == "item.completed" and isinstance(item, dict):
                if item.get("type") == "command_execution":
                    if (isinstance(item.get("command"), str)
                            and type(item.get("exit_code")) is int
                            and isinstance(item.get("aggregated_output"), str)):
                        commands.append({k: item[k] for k in (
                            "command", "exit_code", "aggregated_output")})
        if len(sessions) != 1:
            raise ValueError("Exactly one fresh thread.started identity is required")
    except (OSError, UnicodeError, ValueError) as exc:
        return next(iter(sessions)) if len(sessions) == 1 else None, commands, completed, str(exc)
    return next(iter(sessions)), commands, completed, None


def _terminate_tree(process: subprocess.Popen, output_dir: Path) -> bool:
    """Confirm tree termination or leave the operation UNKNOWN, never retry it."""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            (output_dir / "termination.log").write_text(
                result.stdout + result.stderr, encoding="utf-8")
            process.wait(timeout=10)
            return result.returncode == 0
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        return False  # A surviving group requires reconciliation, not a new writer.
    except (OSError, subprocess.SubprocessError):
        return False


class CodexAdapter:
    def __init__(self, executable: str = "codex", environment=None):
        self.executable = executable
        self.environment = environment

    def run(self, role: str, prompt: str, workspace: Path, output_dir: Path,
            timeout_seconds: int) -> dict:
        if role not in ROLES:
            raise ValueError(f"Unsupported responsibility: {role}")
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        workspace = Path(workspace).resolve(strict=True)
        output_dir = Path(output_dir).resolve()
        if not workspace.is_dir() or output_dir.is_relative_to(workspace):
            raise ValueError("Run evidence must be outside the product/candidate workspace")
        output_dir.mkdir(parents=True, exist_ok=True)
        reserved = ("events.jsonl", "stderr.log", "result.json", "output-schema.json", "prompt.txt")
        if any((output_dir / name).exists() for name in (*reserved, 'app-server.jsonl', 'supervision.jsonl')):
            raise ValueError("Run output must be fresh; existing evidence must not be overwritten")
        stdout_path, stderr_path, result_path, schema_path, prompt_path = (
            output_dir / name for name in reserved)
        schema = TRIAGE_SCHEMA if role == 'triage' else RESULT_SCHEMA
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        instructions = (
            f"Your delegated responsibility is {role}. Work only on the assigned task below. "
            "Return the required JSON as your final report; brief public progress may precede it. "
            "Never invent execution, evidence or approval. "
            "PASS requires concrete evidence; unresolved findings require FAIL. "
            "For command evidence copy the exact executed command and its complete output, "
            "actual exit_code and actual test_count (zero for non-test commands). "
            "For review evidence identify the reviewed files and observations; use an empty "
            "command, exit_code=0 and test_count=0. Test PASS requires executed tests, not "
            "a plan or a reported prior result. Do not alter acceptance requirements or "
            "dispatch repairs: report findings to DOT. For triage, classify root_cause and "
            "recommend action without modifying product or starting workers. Also supply "
            "root_cause_key: a stable concrete component/violated-condition identifier such "
            "as bounded.invalid-range, using ASCII letters, digits, dot, dash, underscore, "
            "colon or slash (3-128 characters). Reuse the same key for the same underlying "
            "cause and distinguish unrelated causes, even when their root_cause categories "
            "match. A category such as implementation is not a concrete cause key. Outside "
            "triage root_cause_key may be an empty string. For triage supply solutions with benefit, "
            "verification steps, resources and permission gaps, plus investigation_steps, missing_information, "
            "permission_gaps and recommended_action. An unresolved cause requires concrete investigation steps.\n"
            + scope_prompt(role) + "\n" + prompt
        )
        prompt_path.write_text(instructions, encoding="utf-8")
        command = [
            self.executable, "app-server", "--listen", "stdio://",
            "--config", 'approval_policy="never"',
            "--config", "sandbox_workspace_write.writable_roots=[]",
            "--config", "sandbox_workspace_write.network_access=false",
        ]
        metadata = {"stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
                    "result_path": str(result_path), "command_events": [],
                    "process_exit_code": None, "transport": "app-server",
                    "protocol_path": str(output_dir / 'app-server.jsonl'),
                    "supervision_path": str(output_dir / 'supervision.jsonl')}
        result = {"status": "ERROR", "session_id": None, "summary": "Codex did not complete",
                  "evidence": [], "findings": [], "root_cause": "test_infrastructure",
                  "root_cause_key": "",
                  "runtime_evidence": metadata}
        options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
        process = None
        protocol_failed = False
        try:
            with stderr_path.open("w", encoding="utf-8") as stderr:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                           text=True, encoding="utf-8", cwd=workspace, env=self.environment, **options)
                from .operation_recovery import record_process_start, record_process_end
                try:
                    record_process_start(output_dir, process.pid)
                except (OSError, ValueError, TypeError) as exc:
                    _terminate_tree(process, output_dir)
                    result.update(status='UNKNOWN', summary='Started process could not be recorded: ' + str(exc))
                    return result
                tree_stopped = False
                try:
                    writable = ([str(workspace.parent / 'artifacts')] if role == 'test'
                                and (workspace.parent / 'request.json').is_file() else [])
                    run_session(process, instructions=instructions, sandbox=ROLES[role], workspace=workspace,
                                schema=schema, output_dir=output_dir, timeout_seconds=timeout_seconds,
                                writable_roots=writable)
                except subprocess.TimeoutExpired:
                    confirmed = _terminate_tree(process, output_dir)
                    tree_stopped = confirmed
                    result.update(status="TIMEOUT" if confirmed else "UNKNOWN",
                                  summary="Codex timed out; process tree terminated" if confirmed else
                                  "Codex timed out; process tree termination unconfirmed, reconcile before retry")
                except ProtocolError as exc:
                    protocol_failed = True
                    # No model turn exists before dispatch; allow the stdio server to close cleanly.
                    if not exc.dispatched:
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            pass
                    tree_stopped = _terminate_tree(process, output_dir) if process.poll() is None else False
                    uncertain = exc.dispatched or (process.poll() is None and not tree_stopped)
                    result.update(status='UNKNOWN' if uncertain else 'ERROR', summary=str(exc))
                    if not uncertain:
                        result['summary'] += '; require a compatible Codex App Server, with no exec fallback'
                try:
                    record_process_end(output_dir, process.pid, tree_stopped=tree_stopped)
                except (OSError, ValueError, TypeError) as exc:
                    result.update(status='UNKNOWN', summary='Process outcome record could not be saved: ' + str(exc))
                    return result
            metadata["process_exit_code"] = process.returncode
            # IO threads close their own pipes; the main process exiting does not prove that
            # descendants released those pipes or that the threads released their IO locks.
        except (OSError, subprocess.SubprocessError) as exc:
            result["summary"] = f"Codex execution failed: {exc}"
            if process is not None and process.poll() is None:
                if not _terminate_tree(process, output_dir):
                    result["status"] = "UNKNOWN"
            return result
        session_id, commands, completed, event_error = _events(stdout_path)
        result["session_id"], metadata["command_events"] = session_id, commands
        if protocol_failed or result["status"] in ("TIMEOUT", "UNKNOWN"):
            return result
        if process.returncode != 0 or event_error or not completed:
            result["summary"] = event_error or f"Codex incomplete/nonzero run (exit={process.returncode}, completed={completed})"
            return result
        try:
            report = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            result["summary"] = f"Missing or invalid structured report: {exc}"
            return result
        if not (_valid_triage(report) if role == 'triage' else _valid_report(report)):
            result["summary"] = "Structured report does not match the required schema"
            return result
        if role == "triage" and report["root_cause"] != "needs_analysis" and report["status"] in ("PASS", "FAIL"):
            key = report["root_cause_key"]
            if key in ROOT_CAUSES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}", key):
                result["summary"] = "Triage requires a stable concrete root_cause_key, not a broad category"
                result["root_cause"] = "needs_analysis"
                return result
        if report["status"] == "PASS":
            evidence = report["evidence"]
            if not evidence or (role != "triage" and report["findings"]):
                result["summary"] = "PASS requires evidence and no unresolved findings"
                return result
            if role == "test" and not any(
                item["kind"] == "command" and item["exit_code"] == 0 and item["test_count"] > 0
                and any(item["command"] == event["command"] and event["exit_code"] == 0
                        and item["output"] == event["aggregated_output"] for event in commands)
                for item in evidence
            ):
                result["summary"] = "Test PASS lacks matching successful command_execution evidence with nonzero tests"
                return result
            if role == "review" and not any(
                item["kind"] == "review" and item["output"].strip() for item in evidence
            ):
                result["summary"] = "Review PASS lacks concrete independent review evidence"
                return result
        result.update(report)
        return result

"""Run a baseline-approved test entry point and retain its actual output.

This runner is deliberately not an OS sandbox. The authorized reviewer must inspect the
exact command and product/test code before approving it; hardware operations,
publishing and deployment do not belong in the test command. No shell command
string is constructed here. A zero exit status describes process completion,
not product acceptance or a nonzero executed-test count.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .adapter import _terminate_tree
from .snapshot import _root


def run_check(command: list[str], workspace: Path, output_dir: Path,
              timeout_seconds: int, environment=None) -> dict:
    """Execute exactly one reviewed argv; never overwrite an earlier run.

    Invalid arguments/paths raise ValueError before dispatch. Operational
    failures return an evidence-bearing result. UNKNOWN means an old process
    may still be active and must be reconciled before another attempt.
    """
    if (not isinstance(command, list) or not command
            or any(not isinstance(arg, str) or "\x00" in arg for arg in command)
            or not command[0].strip()):
        raise ValueError("Test command must be a nonempty executable/argument list")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    workspace = _root(Path(workspace), exists=True)
    output_dir = _root(Path(output_dir), exists=False)
    if output_dir.is_relative_to(workspace) or workspace.is_relative_to(output_dir):
        raise ValueError("Test evidence and workspace must be disjoint")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("Test output directory must be new or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "check.log"
    result = {"status": "ERROR", "command": list(command), "exit_code": None,
              "output": "", "log_path": str(log_path)}
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    process = None
    with log_path.open("xb") as log:
        try:
            process = subprocess.Popen(
                command, shell=False, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, cwd=workspace, env=environment, **options)
            result["process_id"] = process.pid
            try:
                process.wait(timeout=timeout_seconds)
                result["status"] = "PASS" if process.returncode == 0 else "FAIL"
            except subprocess.TimeoutExpired:
                result["status"] = "TIMEOUT" if _terminate_tree(process, output_dir) else "UNKNOWN"
            result["exit_code"] = process.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            log.write(("Test process error: " + str(exc) + "\n").encode("utf-8", errors="replace"))
            if process is not None:
                if process.poll() is None:
                    result["status"] = "ERROR" if _terminate_tree(process, output_dir) else "UNKNOWN"
                result["exit_code"] = process.returncode
    result["output"] = log_path.read_bytes().decode("utf-8", errors="replace")
    return result

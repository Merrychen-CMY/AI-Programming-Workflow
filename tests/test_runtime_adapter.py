"""Mocked App Server adapter checks, not evidence of a real Codex/product run."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from dot_runtime import adapter


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.output = self.root / "runs" / "one"
        self.command = "python -m unittest discover -v"
        self.command_output = "Ran 2 tests in 0.1s\nOK\n"
        self.report = {
            "status": "PASS", "summary": "Two required tests passed", "findings": [],
            "root_cause": "needs_analysis", "root_cause_key": "", "evidence": [{
                "kind": "command", "description": "Required tests executed",
                "command": self.command, "output": self.command_output,
                "exit_code": 0, "test_count": 2,
            }],
        }
        self.events = [
            {"type": "thread.started", "thread_id": "actual-cli-session-one"},
            {"type": "item.completed", "item": {"type": "command_execution",
                "command": self.command, "aggregated_output": self.command_output, "exit_code": 0}},
            {"type": "turn.completed", "usage": {}},
        ]
        self.exit_code, self.timeout = 0, False
        self.invocations = []
        self.inputs = []
        self.sessions = []

    def fake_popen(self, command, **options):
        self.invocations.append((command, options))
        owner = self
        class Process:
            pid = 43210
            returncode = owner.exit_code
            stdin = io.StringIO()
            stdout = io.StringIO()
            def wait(self, timeout):
                self.returncode = -1
                return -1
            def poll(self):
                return self.returncode
        return Process()

    def fake_session(self, process, **options):
        self.inputs.append(options['instructions'])
        self.sessions.append(options)
        output = options['output_dir']
        (output / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in self.events) + '\n', encoding='utf-8')
        if self.report is not None:
            (output / 'result.json').write_text(json.dumps(self.report), encoding='utf-8')
        if self.timeout:
            process.returncode = None
            raise subprocess.TimeoutExpired('codex app-server', options['timeout_seconds'])

    def run_adapter(self, role="test"):
        with patch.object(adapter.subprocess, "Popen", side_effect=self.fake_popen), \
                patch.object(adapter, 'run_session', side_effect=self.fake_session), \
                patch('dot_runtime.operation_recovery.boot_identity', return_value='fixture-boot'):
            return adapter.CodexAdapter("codex-test-path").run(role, "Assigned scope only", self.workspace, self.output, 10)

    def test_fresh_session_identity_and_matching_command_evidence(self):
        result = self.run_adapter()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["session_id"], "actual-cli-session-one")
        self.assertTrue(Path(result["runtime_evidence"]["result_path"]).is_file())
        self.assertEqual(result["runtime_evidence"]["command_events"][0]["aggregated_output"], self.command_output)
        command, options = self.invocations[0]
        self.assertEqual(command[:2], ["codex-test-path", "app-server"])
        self.assertNotIn("resume", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertEqual(self.sessions[0]['sandbox'], 'read-only')
        self.assertEqual(command[command.index('--listen') + 1], 'stdio://')
        self.assertIn("Assigned scope only", self.inputs[0])
        self.assertNotIn("Assigned scope only", command)
        self.assertIs(options["stdin"], subprocess.PIPE)

    def test_implementation_is_only_role_with_workspace_write(self):
        self.assertEqual(self.run_adapter("implementation")["status"], "PASS")
        self.assertEqual(self.sessions[0]['sandbox'], 'workspace-write')

    def test_zero_exit_without_session_is_not_pass(self):
        self.events = self.events[1:]
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_multiple_session_ids_are_rejected(self):
        self.events.insert(1, {"type": "thread.started", "thread_id": "other-session"})
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_nonzero_exit_cannot_be_hidden_by_pass_report(self):
        self.exit_code = 1
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_missing_report_is_not_pass(self):
        self.report = None
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_incomplete_turn_is_not_pass(self):
        self.events.pop()
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_cli_error_event_cannot_be_hidden_by_success_report(self):
        self.events.insert(1, {"type": "error", "message": "Transport failed"})
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_self_reported_command_not_in_real_events_is_rejected(self):
        self.events.pop(1)
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_modified_output_is_not_matching_execution_evidence(self):
        self.report["evidence"][0]["output"] = "Everything passed"
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_zero_tests_and_empty_evidence_do_not_pass(self):
        self.report["evidence"][0]["test_count"] = 0
        self.assertEqual(self.run_adapter()["status"], "ERROR")
        self.output = self.root / "runs" / "two"
        self.report["evidence"] = []
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_schema_rejects_unknown_properties_and_bool_exit_code(self):
        self.report["session_id"] = "fabricated"
        self.assertEqual(self.run_adapter()["status"], "ERROR")
        del self.report["session_id"]
        self.output = self.root / "runs" / "two"
        self.report["evidence"][0]["exit_code"] = False
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_reported_failure_is_preserved_for_dot_triage(self):
        self.report.update(status="FAIL", findings=["AC-2 mismatch"], root_cause="implementation")
        result = self.run_adapter()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"], ["AC-2 mismatch"])

    def test_triage_requires_concrete_key_and_preserves_it(self):
        from test_runtime_controller import triage_fields
        self.report.update(triage_fields('implementation'))
        self.report.update(root_cause="implementation", root_cause_key="")
        self.assertEqual(self.run_adapter("triage")["status"], "ERROR")
        self.output = self.root / "runs" / "two"
        self.report["root_cause_key"] = "implementation"
        self.assertEqual(self.run_adapter("triage")["status"], "ERROR")
        self.output = self.root / "runs" / "three"
        self.report["root_cause_key"] = "bounded.invalid-range"
        result = self.run_adapter("triage")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["root_cause_key"], "bounded.invalid-range")
        self.assertIn("Reuse the same key", self.inputs[-1])

    def test_missing_root_cause_key_fails_strict_schema(self):
        del self.report["root_cause_key"]
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_unresolved_findings_in_pass_are_rejected(self):
        self.report["findings"] = ["Required AC failed"]
        self.assertEqual(self.run_adapter()["status"], "ERROR")

    def test_review_pass_needs_concrete_review_evidence(self):
        self.assertEqual(self.run_adapter("review")["status"], "ERROR")
        self.output = self.root / "runs" / "two"
        self.report["evidence"] = [{"kind": "review", "description": "Reviewed main.c input checks",
                                    "command": "", "output": "main.c checks null before access",
                                    "exit_code": 0, "test_count": 0}]
        self.assertEqual(self.run_adapter("review")["status"], "PASS")

    def test_confirmed_timeout_returns_timeout(self):
        self.timeout = True
        with patch.object(adapter, "_terminate_tree", return_value=True):
            self.assertEqual(self.run_adapter()["status"], "TIMEOUT")

    def test_unconfirmed_timeout_returns_unknown(self):
        self.timeout = True
        with patch.object(adapter, "_terminate_tree", return_value=False):
            self.assertEqual(self.run_adapter()["status"], "UNKNOWN")

    def test_exited_main_process_does_not_close_pipes_still_owned_by_blocked_io_threads(self):
        original_session = self.fake_session
        pipes = []

        def interrupted_session(process, **options):
            original_session(process, **options)
            process.returncode = 0  # A descendant may still hold both pipe handles.
            process.stdin = Mock()
            process.stdout = Mock()
            for pipe in (process.stdin, process.stdout):
                pipe.close.side_effect = AssertionError('Main-thread close would wait for the IO lock')
                pipes.append(pipe)
            raise subprocess.TimeoutExpired('codex app-server', options['timeout_seconds'])

        with patch.object(self, 'fake_session', side_effect=interrupted_session), \
                patch.object(adapter, '_terminate_tree', return_value=False):
            result = self.run_adapter()
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('termination unconfirmed', result['summary'])
        for pipe in pipes:
            pipe.close.assert_not_called()
        record = json.loads((self.output / 'process-record.json').read_text(encoding='utf-8'))
        self.assertEqual(record['process_tree_status'], 'UNKNOWN')

    def test_existing_run_evidence_cannot_be_overwritten(self):
        self.run_adapter()
        with self.assertRaises(ValueError):
            self.run_adapter()

    def test_product_cannot_contain_its_own_run_evidence(self):
        self.output = self.workspace / "evidence"
        with self.assertRaises(ValueError):
            self.run_adapter()

    @unittest.skipUnless(adapter.os.name == "nt", "Windows process-tree termination branch")
    def test_windows_timeout_uses_hidden_tree_kill_and_requires_confirmation(self):
        self.output.mkdir(parents=True)
        process = Mock(pid=43210)
        killer = Mock(returncode=0, stdout="terminated", stderr="")
        with patch.object(adapter.subprocess, "run", return_value=killer) as terminate:
            self.assertTrue(adapter._terminate_tree(process, self.output))
        self.assertEqual(terminate.call_args.args[0], ["taskkill", "/PID", "43210", "/T", "/F"])
        self.assertIn("creationflags", terminate.call_args.kwargs)
        process.wait.assert_called_once_with(timeout=10)
        killer.returncode = 1
        with patch.object(adapter.subprocess, "run", return_value=killer):
            self.assertFalse(adapter._terminate_tree(process, self.output))


if __name__ == "__main__":
    unittest.main()

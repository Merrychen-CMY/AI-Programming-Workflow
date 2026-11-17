"""Real local test-process transport and mocked timeout lifecycle checks."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from dot_runtime import checks


class CheckRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "product"
        self.workspace.mkdir()
        self.output_dir = self.root / "checks" / "one"

    def run_check(self, command):
        return checks.run_check(command, self.workspace, self.output_dir, 10)

    def test_actual_process_output_and_exit_are_retained(self):
        command = [sys.executable, "-c", "import sys; print('stdout result'); print('stderr result', file=sys.stderr)"]
        result = self.run_check(command)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["command"], command)
        self.assertIn("stdout result", result["output"])
        self.assertIn("stderr result", result["output"])
        self.assertEqual(Path(result["log_path"]).read_bytes().decode("utf-8"), result["output"])
        self.assertGreater(result["process_id"], 0)

    def test_actual_failure_is_not_hidden_by_success_looking_output(self):
        result = self.run_check([sys.executable, "-c", "print('PASS'); raise SystemExit(3)"])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("PASS", result["output"])

    def test_argument_metacharacters_are_passed_literally_without_shell(self):
        value = "literal; & echo unwanted | > output.txt $(exit)"
        result = self.run_check([sys.executable, "-c", "import sys; print(sys.argv[1])", value])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["output"].strip(), value)
        self.assertFalse((self.workspace / "output.txt").exists())

    def test_process_starts_in_exact_approved_workspace(self):
        result = self.run_check([sys.executable, "-c", "import os; print(os.getcwd())"])
        self.assertEqual(Path(result["output"].strip()).resolve(), self.workspace.resolve())

    def test_stdin_is_closed_instead_of_inheriting_operator_input(self):
        result = self.run_check([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"])
        self.assertEqual(result["output"].strip(), "''")

    def test_missing_executable_returns_error_with_log(self):
        result = self.run_check([str(self.root / "no-such-executable")])
        self.assertEqual(result["status"], "ERROR")
        self.assertIsNone(result["exit_code"])
        self.assertTrue(Path(result["log_path"]).exists())
        self.assertIn("Test process error", result["output"])

    def test_output_cannot_be_under_or_above_project(self):
        self.output_dir = self.workspace / "logs"
        with self.assertRaises(ValueError):
            self.run_check([sys.executable, "--version"])
        self.output_dir = self.root
        with self.assertRaises(ValueError):
            self.run_check([sys.executable, "--version"])

    def test_existing_output_cannot_be_replaced(self):
        result = self.run_check([sys.executable, "--version"])
        before = Path(result["log_path"]).read_bytes()
        with self.assertRaises(ValueError):
            self.run_check([sys.executable, "--version"])
        self.assertEqual(Path(result["log_path"]).read_bytes(), before)

    def test_shell_strings_empty_arguments_and_invalid_timeout_rejected(self):
        for invalid in ("python --version", [], [""], [None], ["python\x00"]):
            with self.assertRaises(ValueError):
                self.run_check(invalid)
        for timeout in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                checks.run_check([sys.executable], self.workspace, self.output_dir, timeout)

    def test_timeout_requires_confirmed_tree_termination(self):
        for confirmed, expected in ((True, "TIMEOUT"), (False, "UNKNOWN")):
            self.output_dir = self.root / "checks" / expected
            process = Mock(pid=123, returncode=None)
            process.wait.side_effect = subprocess.TimeoutExpired("test", 10)
            with patch.object(checks.subprocess, "Popen", return_value=process) as launch:
                with patch.object(checks, "_terminate_tree", return_value=confirmed) as terminate:
                    result = self.run_check([sys.executable, "--version"])
            self.assertEqual(result["status"], expected)
            self.assertIsNone(result["exit_code"])
            self.assertFalse(launch.call_args.kwargs["shell"])
            terminate.assert_called_once_with(process, self.output_dir)

    def test_binary_test_output_does_not_destroy_evidence(self):
        result = self.run_check([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(Path(result["log_path"]).read_bytes(), b"\xff\xfe")
        self.assertEqual(result["output"], "\ufffd\ufffd")


if __name__ == "__main__":
    unittest.main()

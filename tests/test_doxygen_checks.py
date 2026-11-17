"""Synthetic tool/XML fault tests, not real Doxygen or product acceptance.

The fake executable is an actual harmless Python subprocess that emits a
deliberately bounded XML fixture. Real pinned-Doxygen tests are separately
provided by scripts/verify_doxygen_examples.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_doxygen as checker


FAKE_TOOL = r'''
import json
from pathlib import Path
import shlex
import sys
import xml.etree.ElementTree as ET
mode, argv = sys.argv[1], sys.argv[2:]
if argv == ["--version"]:
    if mode == "version_exit":
        print("cannot initialize", file=sys.stderr)
        raise SystemExit(2)
    print("1.17.0" if mode == "version_mismatch" else "1.18.0 (abcdef123456)")
    raise SystemExit(0)
config = Path(argv[0])
values = dict(line.split(" = ", 1) for line in config.read_text().splitlines() if " = " in line)
inputs = shlex.split(values["INPUT"])
output = Path(shlex.split(values["OUTPUT_DIRECTORY"])[0]) / "xml"
output.mkdir(parents=True)
warnings = Path(shlex.split(values["WARN_LOGFILE"])[0])
warnings.write_text("synthetic warning" if mode == "warning" else "")
if mode == "stderr": print("synthetic diagnostic", file=sys.stderr)
if mode == "stdout_warning": print("warning: synthetic stdout warning")
if mode == "exit": raise SystemExit(3)
if mode == "missing_xml": raise SystemExit(0)
if mode == "empty_xml":
    (output / "index.xml").write_text("")
    raise SystemExit(0)
if mode == "doctype":
    (output / "index.xml").write_text('<!DOCTYPE x [<!ENTITY x "secret">]><doxygenindex/>')
    raise SystemExit(0)
source = inputs[0]
index = ET.Element("doxygenindex")
ET.SubElement(index, "compound", refid="module_8h", kind="file")
ET.ElementTree(index).write(output / "index.xml", encoding="utf-8")
tree = ET.Element("doxygen")
file = ET.SubElement(tree, "compounddef", id="module_8h", kind="file")
ET.SubElement(file, "compoundname").text = "module.h"
ET.SubElement(file, "location", file=source)
def doc(parent, text="Synthetic documentation"):
    brief = ET.SubElement(parent, "briefdescription")
    ET.SubElement(brief, "para").text = text
doc(file, "" if mode == "missing_file_doc" else "Synthetic file documentation")
section = ET.SubElement(file, "sectiondef", kind="func")
member = ET.SubElement(section, "memberdef", kind="function", id="add")
ET.SubElement(member, "location", file=source)
ET.SubElement(member, "name").text = "add"
ET.SubElement(member, "type").text = "int"
doc(member)
param = ET.SubElement(member, "param")
ET.SubElement(param, "type").text = "int"
ET.SubElement(param, "declname").text = "x"
details = ET.SubElement(member, "detaileddescription")
plist = ET.SubElement(details, "parameterlist", kind="param")
item = ET.SubElement(plist, "parameteritem")
names = ET.SubElement(item, "parameternamelist")
pname = ET.SubElement(names, "parametername", direction="in")
pname.text = "x"
desc = ET.SubElement(item, "parameterdescription")
ET.SubElement(desc, "para").text = "Synthetic input"
returns = ET.SubElement(details, "simplesect", kind="return")
ET.SubElement(returns, "para").text = "Synthetic result"
if mode == "file_only": file.remove(section)
if mode == "missing_direction": pname.attrib.clear()
if mode == "mismatch_param": pname.text = "wrong"
if mode == "missing_return": details.remove(returns)
if mode == "void_return": member.find("type").text = "void"
if mode == "void_valid":
    member.find("type").text = "static inline void"
    details.remove(returns)
if mode == "retval":
    details.remove(returns)
    values = ET.SubElement(details, "parameterlist", kind="retval")
    value = ET.SubElement(values, "parameteritem")
    names = ET.SubElement(value, "parameternamelist")
    ET.SubElement(names, "parametername").text = "0"
    desc = ET.SubElement(value, "parameterdescription")
    ET.SubElement(desc, "para").text = "Synthetic success"
if mode in ("define", "enum", "typedef", "variable"):
    other = ET.SubElement(section, "memberdef", kind=mode, id="undocumented")
    ET.SubElement(other, "location", file=source)
    ET.SubElement(other, "name").text = "undocumented"
if mode == "enum_value":
    other = ET.SubElement(section, "memberdef", kind="enum", id="state")
    ET.SubElement(other, "location", file=source)
    ET.SubElement(other, "name").text = "state"
    doc(other)
    value = ET.SubElement(other, "enumvalue", id="ready")
    ET.SubElement(value, "name").text = "READY"
if mode == "mutate_source": Path(source).write_text("changed while running")
ET.ElementTree(tree).write(output / "module_8h.xml", encoding="utf-8")
'''


class DoxygenGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "owned source"
        self.root.mkdir()
        self.source = self.root / "module.h"
        self.source.write_text("/* Synthetic source: fake tool does not parse this. */\nint add(int x);\n")
        self.fake = self.base / "fake_doxygen.py"
        self.fake.write_text(FAKE_TOOL, encoding="utf-8")
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def run_fake(self, mode="valid", **kwargs):
        self.sequence += 1
        output = self.base / f"evidence {self.sequence}"
        actual_run = checker._run

        def dispatch(argv, phase, directory, timeout):
            return actual_run([sys.executable, str(self.fake), mode, *argv[1:]],
                              phase, directory, timeout)

        with patch.object(checker, "_run", side_effect=dispatch):
            report = checker.check_doxygen(self.root, output, "1.18.0", **kwargs)
        self.assertEqual(json.loads((output / "report.json").read_text(encoding="utf-8")), report)
        return report, output

    def test_synthetic_success_binds_actual_logs_config_and_source(self):
        report, output = self.run_fake()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["actual_version"], "1.18.0")
        self.assertEqual(report["version_banner"], "1.18.0 (abcdef123456)")
        self.assertEqual(report["checks"]["member_count"], 1)
        self.assertEqual(report["source_files"], {"module.h": hashlib.sha256(self.source.read_bytes()).hexdigest()})
        self.assertEqual(report["source_digest"], checker.manifest_digest(report["source_files"]))
        self.assertEqual(report["config_sha256"], hashlib.sha256((output / "Doxyfile").read_bytes()).hexdigest())
        self.assertEqual(report["commands"][0]["argv"][0], sys.executable)
        self.assertTrue(Path(report["commands"][0]["stdout_path"]).read_text().startswith("1.18.0"))

    def test_all_tool_failure_modes_refuse_pass(self):
        for mode in ("version_exit", "version_mismatch", "exit", "warning", "stderr", "stdout_warning",
                     "missing_xml", "empty_xml", "doctype"):
            with self.subTest(mode=mode):
                report, _ = self.run_fake(mode)
                self.assertNotEqual(report["status"], "PASS")
                self.assertTrue(report["findings"])

    def test_structural_omissions_refuse_pass(self):
        for mode in ("missing_file_doc", "missing_direction", "mismatch_param", "missing_return",
                     "void_return", "file_only", "define", "enum", "typedef", "variable", "enum_value"):
            with self.subTest(mode=mode):
                report, _ = self.run_fake(mode)
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(report["findings"])

    def test_valid_void_and_retval_documentation(self):
        for mode in ("void_valid", "retval"):
            with self.subTest(mode=mode):
                report, _ = self.run_fake(mode)
                self.assertEqual(report["status"], "PASS", report["findings"])

    def test_source_mutation_is_rejected(self):
        report, _ = self.run_fake("mutate_source")
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("changed" in item for item in report["findings"]))

    def test_no_sources_missing_tool_and_explicit_missing_tool(self):
        empty = self.base / "empty"
        empty.mkdir()
        report = checker.check_doxygen(empty, self.base / "empty-output", "1.18.0")
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["commands"], [])
        for index, executable in enumerate(("doxygen-obviously-missing-123456", str(self.base / "absent.exe"))):
            report = checker.check_doxygen(self.root, self.base / f"missing-{index}", "1.18.0", executable)
            self.assertEqual(report["status"], "ERROR")

    def test_evidence_cannot_overlap_or_overwrite(self):
        with self.assertRaises(ValueError):
            checker.check_doxygen(self.root, self.root / "logs", "1.18.0")
        with self.assertRaises(ValueError):
            checker.check_doxygen(self.root, self.base, "1.18.0")
        _, output = self.run_fake()
        with self.assertRaises(ValueError):
            checker.check_doxygen(self.root, output, "1.18.0")

    def test_selection_is_explicit_and_cannot_escape(self):
        other = self.root / "other"
        other.mkdir()
        (other / "extra.c").write_text("synthetic source")
        self.assertEqual(set(checker.source_manifest(self.root, ["module.h"])), {"module.h"})
        self.assertEqual(set(checker.source_manifest(self.root)), {"module.h", "other/extra.c"})
        for include in ("../outside", str(self.source), "missing"):
            with self.subTest(include=include), self.assertRaises(ValueError):
                checker.source_manifest(self.root, [include])

    def test_config_is_controlled_and_quotes_spaces(self):
        config = checker.build_config(self.root, checker.source_manifest(self.root), self.base / "result folder")
        self.assertIn('INPUT = "', config)
        for line in ("EXTRACT_ALL = NO", "EXTRACT_STATIC = YES", "WARN_NO_PARAMDOC = YES",
                     "FILTER_PATTERNS = ", "INPUT_FILTER = ", "SEARCH_INCLUDES = NO"):
            self.assertIn(line + "\n", config)
        self.assertNotIn("@INCLUDE", config)
        self.assertNotIn("CLASS_DIAGRAMS", config)
        self.assertRaises(ValueError, checker._quote, Path("$(INJECT)/module.h"))

    def test_fixed_version_is_required(self):
        for version in ("latest", "1.18", "1.18.*", ""):
            with self.subTest(version=version), self.assertRaises(ValueError):
                checker.check_doxygen(self.root, self.base / "bad-version", version)

    def test_timeout_has_no_pass_and_preserves_logs(self):
        for terminated, expected in ((True, "TIMEOUT"), (False, "UNKNOWN")):
            process = Mock(pid=123, returncode=None)
            process.wait.side_effect = subprocess.TimeoutExpired("fake", 1)
            output = self.base / expected
            output.mkdir()
            with patch.object(checker.subprocess, "Popen", return_value=process), \
                    patch.object(checker, "_terminate_tree", return_value=terminated):
                result = checker._run(["synthetic-missing-process"], "generate", output, 1)
            self.assertEqual(result["status"], expected)
            self.assertTrue(Path(result["stdout_path"]).exists())

    def test_xml_entities_utf16_and_path_traversal_are_rejected(self):
        xml = self.base / "bad.xml"
        for content in (b'<!DOCTYPE x><x/>', b'<!ENTITY x "value"><x/>', '<!DOCTYPE x><x/>'.encode("utf-16")):
            xml.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                checker._read_xml(xml)
        _, output = self.run_fake()
        index = output / "generated/xml/index.xml"
        index.write_text('<doxygenindex><compound refid="../outside"/></doxygenindex>')
        with self.assertRaises(ValueError):
            checker._check_xml(output, self.root, checker.source_manifest(self.root))

    def test_undocumented_struct_and_field_are_rejected(self):
        _, output = self.run_fake()
        folder = output / "generated/xml"
        index = ET.parse(folder / "index.xml")
        ET.SubElement(index.getroot(), "compound", refid="struct_state", kind="struct")
        index.write(folder / "index.xml")
        xml = ('<doxygen><compounddef id="struct_state" kind="struct"><compoundname>state</compoundname>'
               f'<location file="{self.source.as_posix()}"/><sectiondef><memberdef kind="variable" id="field">'
               f'<name>count</name><location file="{self.source.as_posix()}"/></memberdef></sectiondef></compounddef></doxygen>')
        (folder / "struct_state.xml").write_text(xml)
        _, _, findings = checker._check_xml(output, self.root, checker.source_manifest(self.root))
        self.assertTrue(any("undocumented struct" in item for item in findings))
        self.assertTrue(any("variable count" in item for item in findings))

    def test_cli_reports_failures_without_test_count(self):
        output = self.base / "cli-report"
        process = subprocess.run([sys.executable, checker.__file__, "--source-root", str(self.root),
                                  "--output-dir", str(output), "--expected-version", "1.18.0",
                                  "--doxygen", "doxygen-obviously-missing-123456"],
                                 capture_output=True, text=True, timeout=15)
        self.assertNotEqual(process.returncode, 0)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "ERROR")
        self.assertNotIn("test_count", report)


if __name__ == "__main__":
    unittest.main()

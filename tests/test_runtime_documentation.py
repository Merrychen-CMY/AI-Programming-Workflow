"""Controller provenance/flow tests using synthetic documentation output, not Doxygen acceptance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from dot_runtime.controller import Controller, atomic_json, initialize, file_hash, read_json, validate_plan
from dot_runtime.documentation import validate_settings, validate_report
from check_doxygen import source_manifest, manifest_digest, build_config
from test_runtime_controller import fixture_plan, fixture_verification, FakeAdapter, result


SETTINGS = {'executable': 'doxygen', 'expected_version': '1.18.0', 'include': ['.'], 'timeout_seconds': 5}


class DocumentationControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'value.h').write_text('int value(void);\n', encoding='utf-8')
        self.requirements = self.root / 'requirements.md'
        self.requirements.write_text('Synthetic controller transport fixture.', encoding='utf-8')
        self.plan = self.root / 'plan.json'
        plan = fixture_plan()
        plan['coding_standard'] = 'huawei-c'
        plan['verification']['doxygen'] = copy.deepcopy(SETTINGS)
        atomic_json(self.plan, plan)
        self.run_dir = self.root / 'run'
        initialize(self.project, self.requirements, self.plan, self.run_dir)
        self.mutate = None

    def fake_check(self, command, workspace, output, timeout):
        output.mkdir(parents=True)
        log = output / 'check.log'
        log.write_text('Synthetic process output\n', encoding='utf-8')
        process = {'status': 'PASS', 'exit_code': 0, 'command': command,
                   'output': 'Ran 3 tests in 0.001s\nOK\n', 'log_path': str(log)}
        if '--source-root' not in command:
            if '--request' in command:
                _, commitment = fixture_verification(Path(command[command.index('--request') + 1]))
                process['output'] += commitment
                log.write_text(process['output'], encoding='utf-8')
            return process
        artifacts = Path(command[command.index('--output-dir') + 1])
        artifacts.mkdir(parents=True)
        files = source_manifest(workspace, SETTINGS['include'])
        config = artifacts / 'Doxyfile'
        config.write_text(build_config(workspace, files, artifacts), encoding='utf-8', newline='\n')
        xml = artifacts / 'generated/xml/index.xml'
        xml.parent.mkdir(parents=True)
        xml.write_text('<synthetic-controller-test/>', encoding='utf-8')
        record = {'schema_version': 1, 'checker': 'doxygen-owned-c', 'status': 'PASS',
                  'source_root': str(workspace), 'source_files': files, 'source_digest': manifest_digest(files),
                  'expected_version': '1.18.0', 'actual_version': '1.18.0',
                  'config_path': str(config), 'config_sha256': file_hash(config),
                  'commands': [{'phase': p, 'exit_code': 0} for p in ('version', 'generate')],
                  'xml_files': {'generated/xml/index.xml': file_hash(xml)},
                  'checks': {'source_file_count': len(files)}, 'findings': []}
        if self.mutate:
            self.mutate(record, process, artifacts)
        atomic_json(artifacts / 'report.json', record)
        return process

    def controller(self, adapter=None):
        c = Controller(self.run_dir, adapter or FakeAdapter(), check_runner=self.fake_check)
        c.approve('requirements', lambda *_: True)
        c.approve('plan', lambda *_: True)
        return c

    def check(self):
        c = self.controller()
        c.make_candidate()
        return c, c.approved_documentation(Path(c.state['candidate']['path']))

    def test_c_plan_cannot_omit_or_disable_documentation(self):
        plan = read_json(self.plan)
        del plan['verification']['doxygen']
        with self.assertRaises(ValueError):
            validate_plan(plan)
        for changes in ({'include': []}, {'include': ['../outside']}, {'include': [[]]},
                        {'expected_version': 'latest'}, {'timeout_seconds': True}, {'enabled': False},
                        {'executable': 'tools/doxygen.exe'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_settings(dict(SETTINGS, **changes))

    def test_documentation_report_binds_current_candidate_and_records_artifacts(self):
        c, checked = self.check()
        self.assertEqual(checked['status'], 'PASS')
        entry = c.state['documentation_checks'][-1]
        self.assertEqual(entry['candidate_id'], c.state['candidate']['id'])
        self.assertTrue(entry['evidence_files'])
        self.assertNotIn('observed_test_count', entry)
        self.assertIsNone(c.state['active_run'])
        c.check_evidence()

    def test_self_reported_pass_cannot_hide_wrong_source_or_version(self):
        self.mutate = lambda r, p, a: r.update(source_digest='0' * 64, actual_version='0.0.0')
        c, checked = self.check()
        self.assertEqual(checked['status'], 'ERROR')

    def test_zero_process_failure_cannot_be_reported_as_documentation_pass(self):
        self.mutate = lambda r, p, a: p.update(status='FAIL', exit_code=3)
        c, checked = self.check()
        self.assertNotEqual(checked['status'], 'PASS')

    def test_report_without_xml_cannot_pass(self):
        self.mutate = lambda r, p, a: r.update(xml_files={})
        c, checked = self.check()
        self.assertNotEqual(checked['status'], 'PASS')

    def test_malformed_report_stops_without_uncaught_error(self):
        self.mutate = lambda r, p, a: r.update(checks=[], xml_files=['untrusted'])
        c, checked = self.check()
        self.assertEqual(checked['status'], 'ERROR')
        self.assertIsNone(c.state['active_run'])

    def test_unknown_process_remains_unknown_even_with_malformed_report(self):
        def mutate(record, process, artifacts):
            record.update(checks=[])
            process.update(status='UNKNOWN', exit_code=None)
        self.mutate = mutate
        c, checked = self.check()
        self.assertEqual(checked['status'], 'UNKNOWN')
        self.assertIsNotNone(c.state['active_run'])

    def test_outer_helper_timeout_cannot_claim_its_nested_process_stopped(self):
        self.mutate = lambda r, p, a: p.update(status='TIMEOUT', exit_code=-9)
        c, checked = self.check()
        self.assertEqual(checked['status'], 'UNKNOWN')
        self.assertIsNotNone(c.state['active_run'])

    def test_failed_helper_without_terminal_report_needs_reconciliation(self):
        c = self.controller()
        c.make_candidate()
        def interrupted(command, workspace, output, timeout):
            process = self.fake_check(command, workspace, output, timeout)
            artifacts = Path(command[command.index('--output-dir') + 1])
            (artifacts / 'report.json').unlink()
            return dict(process, status='FAIL', exit_code=1)
        c.check_runner = interrupted
        checked = c.approved_documentation(Path(c.state['candidate']['path']))
        self.assertEqual(checked['status'], 'UNKNOWN')
        self.assertIsNotNone(c.state['active_run'])

    def test_config_tampering_and_candidate_modification_are_rejected(self):
        def mutate(record, process, artifacts):
            (artifacts / 'Doxyfile').write_text('EXTRACT_ALL = YES\n', encoding='utf-8')
            record['config_sha256'] = file_hash(artifacts / 'Doxyfile')
        self.mutate = mutate
        c, checked = self.check()
        self.assertNotEqual(checked['status'], 'PASS')

    def test_unknown_documentation_process_keeps_reconciliation_required(self):
        self.mutate = lambda r, p, a: r.update(status='UNKNOWN')
        c, checked = self.check()
        self.assertEqual(checked['status'], 'UNKNOWN')
        self.assertIsNotNone(c.state['active_run'])

    def test_recorded_documentation_evidence_cannot_be_changed(self):
        c, checked = self.check()
        item = c.state['documentation_checks'][-1]['evidence_files'][0]
        (self.run_dir / item['path']).write_text('Changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Doxygen evidence'):
            c.check_evidence()

    def test_failed_documentation_flows_to_dot_before_implementation_repair(self):
        attempts = []
        def first_failure(record, process, artifacts):
            attempts.append(1)
            if len(attempts) == 1:
                record.update(status='FAIL', findings=['Synthetic missing parameter description'])
                process.update(status='FAIL', exit_code=1)
        self.mutate = first_failure
        c = self.controller(FakeAdapter([('triage', result('PASS', 'implementation'))]))
        c.c_compliance = lambda workspace: []
        self.assertEqual(c.run()['report']['outcome'], 'SUCCESS')
        self.assertEqual(c.adapter.calls[:3], ['implementation', 'triage', 'implementation'])
        self.assertEqual(len(c.state['documentation_checks']), 2)
        report = read_json(self.run_dir / 'final-report.json')
        self.assertEqual(len(report['documentation_checks']), 2)
        self.assertEqual(c.state['state'], 'REPORT_PENDING')

    def test_doc_baseline_change_requires_new_plan_approval(self):
        c = self.controller()
        changed_checker = self.root / 'changed-checker.py'
        changed_checker.write_text('# changed pinned checker', encoding='utf-8')
        with patch('dot_runtime.controller.DOXYGEN_CHECKER', changed_checker):
            with self.assertRaisesRegex(ValueError, 'baseline changed'):
                c.check_approvals()


if __name__ == '__main__':
    unittest.main()

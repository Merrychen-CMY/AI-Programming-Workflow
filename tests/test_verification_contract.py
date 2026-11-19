"""Actual isolated Python/CMake test runs; no model or product task dispatch."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from dot_runtime import verification as v
from dot_runtime.controller import atomic_json, file_hash, read_json


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'candidate'; self.project.mkdir()
        (self.project / 'value.py').write_text('def value():\n    return 7\n', encoding='utf-8')
        (self.project / 'test_value.py').write_text('import unittest\nfrom value import value\n'
            'class Test(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 7)\n', encoding='utf-8')
        self.settings = {'test_report_format': 'unittest', 'test_command': [sys.executable, '-B', '-m', 'unittest'],
            'test_contract': {'protocol': 'python-unittest', 'source_root': '.', 'product_files': ['value.py'],
                              'required_tests': ['test_value.Test.test_value'], 'not_applicable_tests': []}}

    def run_verification(self, name='one'):
        context = v.prepare(self.project, self.root / 'run/test-workspaces' / name, self.settings)
        process = subprocess.run(context['command'], cwd=context['source'], capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=90)
        context['execution_output'] = process.stdout
        return context, process

    def assess(self, context):
        return v.assess(context, context['execution_output'])

    def test_real_python_records_current_loaded_product_and_exact_tests(self):
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        record = self.assess(context)
        self.assertEqual(record['tests'], [{'id': 'test_value.Test.test_value', 'status': 'PASS'}])
        self.assertEqual(record['product_sources']['value.py']['sha256'], file_hash(self.project / 'value.py'))
        self.assertTrue(Path(record['product_sources']['value.py']['path']).is_relative_to(Path(context['source'])))
        v.check_record(record)

    def test_required_partial_skip_cannot_pass(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('    @unittest.skip("hardware absent")\n    def test_hil(self):\n        pass\n')
        self.settings['test_contract']['required_tests'].append('test_value.Test.test_hil')
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, 'Required test did not pass: test_value.Test.test_hil'):
            self.assess(context)

    def test_only_explicitly_waived_optional_skip_is_accepted(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('    @unittest.skip("other platform")\n    def test_optional(self):\n        pass\n')
        context, _ = self.run_verification()
        with self.assertRaisesRegex(ValueError, 'unapproved skipped'):
            self.assess(context)
        self.settings['test_contract']['not_applicable_tests'] = [{'id': 'test_value.Test.test_optional', 'reason': 'Approved Linux-only obligation on Windows'}]
        context, _ = self.run_verification('approved')
        self.assertEqual(self.assess(context)['observed_test_count'], 1)

    def test_declared_required_test_cannot_disappear(self):
        self.settings['test_contract']['required_tests'].append('test_value.Test.test_missing')
        context, _ = self.run_verification()
        with self.assertRaisesRegex(ValueError, 'MISSING'):
            self.assess(context)

    def test_unlisted_subtest_failure_still_blocks_success(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('    def test_extra(self):\n        with self.subTest(case=1):\n            self.assertEqual(1, 2)\n')
        context, process = self.run_verification()
        self.assertNotEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, 'actual test framework failed'):
            self.assess(context)

    def test_pytest_collection_error_exit_is_retained_even_with_a_passed_required_item(self):
        from dot_runtime import verification_runner as runner
        context = v.prepare(self.project, self.root / 'run/test-workspaces/pytest', self.settings)
        request = read_json(context['request'])
        request['contract']['protocol'] = 'python-pytest'
        def fake_pytest(arguments, plugins):
            import importlib.util
            spec = importlib.util.spec_from_file_location('_pytest_product_fixture', Path(context['source']) / 'value.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            sys.modules['_pytest_product_fixture'] = module
            plugins[0].pytest_runtest_logreport(SimpleNamespace(nodeid='test_value.py::test_value',
                skipped=False, failed=False, when='call'))
            return 2  # Actual pytest collection/internal errors must not become success.
        previous_cwd, previous_path = Path.cwd(), list(sys.path)
        try:
            with patch.dict(sys.modules, {'pytest': SimpleNamespace(main=fake_pytest)}):
                report = runner.python_tests(request)
        finally:
            os.chdir(previous_cwd); sys.path[:] = previous_path
            sys.modules.pop('_pytest_product_fixture', None)
        self.assertEqual(report['tests'], [{'id': 'test_value.py::test_value', 'status': 'PASS'}])
        self.assertEqual(report['framework_exit_code'], 2)
        report['request_sha256'] = file_hash(context['request'])
        atomic_json(context['result'], report)
        output = 'REQPILOT_VERIFICATION_RESULT_SHA256=' + file_hash(context['result']) + '\n'
        with self.assertRaisesRegex(ValueError, 'actual test framework failed'):
            v.assess(context, output)

    def test_post_execution_report_rewrite_is_rejected_by_stdout_commitment(self):
        context, _ = self.run_verification()
        report = read_json(context['result']); report['tests'][0]['status'] = 'SKIP'
        atomic_json(context['result'], report)
        with self.assertRaisesRegex(ValueError, 'committed in actual command output'):
            self.assess(context)

    def test_malformed_result_structures_are_clean_validation_rejections(self):
        context, _ = self.run_verification()
        valid = read_json(context['result'])
        invalid_reports = [[], dict(valid, error=[]), dict(valid, product_sources=[]),
            dict(valid, product_sources={'value.py': []}), dict(valid, tested_artifacts={}),
            dict(valid, tested_artifacts=[{'path': [], 'sha256': 'invalid'}]),
            dict(valid, tests={}), dict(valid, tests=[{'id': [], 'status': 'PASS'}]),
            dict(valid, tests=[{'id': 'example', 'status': []}]),
            dict(valid, tests=[{'id': 'example', 'status': 'UNKNOWN'}]),
            dict(valid, framework_exit_code=False)]
        for index, report in enumerate(invalid_reports):
            with self.subTest(index=index):
                atomic_json(context['result'], report)
                output = 'REQPILOT_VERIFICATION_RESULT_SHA256=' + file_hash(context['result']) + '\n'
                with self.assertRaises(ValueError):
                    v.assess(context, output)

    def test_unittest_same_method_name_in_distinct_classes_has_unique_identity(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('class Other(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 7)\n')
        self.settings['test_contract']['required_tests'].append('test_value.Other.test_value')
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.assess(context)['observed_test_count'], 2)

    def test_old_external_module_cannot_establish_candidate_provenance(self):
        old = self.root / 'old'; old.mkdir()
        (old / 'value.py').write_text('def value():\n    return 7\n')
        (self.project / 'test_value.py').write_text('import sys\nsys.path.insert(0, ' + repr(str(old)) + ')\n'
            'import unittest\nfrom value import value\nclass Test(unittest.TestCase):\n'
            '    def test_value(self):\n        self.assertEqual(value(), 7)\n')
        context, _ = self.run_verification()
        with self.assertRaisesRegex(ValueError, 'not loaded/built'):
            self.assess(context)

    def test_source_mutation_is_rejected_and_artifact_writes_are_permitted(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('    def test_artifact(self):\n        import os\n        from pathlib import Path\n'
                    '        Path(os.environ["REQPILOT_TEST_ARTIFACTS"], "result.txt").write_text("measured")\n')
        context, _ = self.run_verification()
        self.assertEqual(self.assess(context)['status'], 'PASS')
        self.assertTrue((Path(context['artifacts']) / 'result.txt').is_file())
        (Path(context['source']) / 'value.py').write_text('def value(): return 99\n')
        with self.assertRaisesRegex(ValueError, 'changed candidate source'):
            self.assess(context)

    def test_missing_contract_wrapper_and_external_test_paths_require_replanning(self):
        invalid = copy.deepcopy(self.settings); invalid.pop('test_contract')
        with self.assertRaisesRegex(ValueError, 'legacy plan'): v.validate(invalid)
        for command in ([sys.executable, 'wrapper.py'], [sys.executable, '-B', '-m', 'unittest', '/old/tests']):
            invalid = copy.deepcopy(self.settings); invalid['test_command'] = command
            with self.assertRaisesRegex(ValueError, 'wrapper'): v.validate(invalid)

    def test_malformed_plan_types_are_clean_validation_rejections(self):
        for field, value in (('protocol', []), ('protocol', {}), ('product_files', [{}])):
            with self.subTest(field=field, value=value):
                invalid = copy.deepcopy(self.settings)
                invalid['test_contract'][field] = value
                with self.assertRaises(ValueError):
                    v.validate(invalid)

    def test_report_request_and_exact_command_are_bound(self):
        context, _ = self.run_verification()
        event = {'command': '& ' + ' '.join('"' + word + '"' for word in context['command']), 'exit_code': 0}
        v.require_command_event(context, {'runtime_evidence': {'command_events': [event]}})
        event['command'] += ' && echo forged'
        with self.assertRaisesRegex(ValueError, 'exact pinned'):
            v.require_command_event(context, {'runtime_evidence': {'command_events': [event]}})
        request = read_json(context['request']); request['contract']['required_tests'] = ['other']
        atomic_json(context['request'], request)
        with self.assertRaisesRegex(ValueError, 'different execution request'):
            self.assess(context)

@unittest.skipUnless(importlib.util.find_spec('pytest'), 'Real pytest unavailable')
class PytestVerificationTests(unittest.TestCase):
    run_verification = VerificationTests.run_verification
    assess = VerificationTests.assess

    def setUp(self):
        VerificationTests.setUp(self)
        self.settings['test_report_format'] = 'pytest'
        self.settings['test_command'] = [sys.executable, '-B', '-m', 'pytest']
        self.settings['test_contract']['protocol'] = 'python-pytest'
        self.settings['test_contract']['required_tests'] = ['test_value.py::Test::test_value']

    def test_actual_pytest_passes_with_full_nodeid_and_same_method_in_another_class(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('class TestOther:\n    def test_value(self):\n        assert value() == 7\n')
        self.settings['test_contract']['required_tests'].append('test_value.py::TestOther::test_value')
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.assess(context)['observed_test_count'], 2)

    def test_actual_pytest_partial_required_skip_is_rejected(self):
        with (self.project / 'test_value.py').open('a') as f:
            f.write('    @unittest.skip("hardware absent")\n    def test_hil(self):\n        pass\n')
        self.settings['test_contract']['required_tests'].append('test_value.py::Test::test_hil')
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        with self.assertRaisesRegex(ValueError, 'Required test did not pass: test_value.py::Test::test_hil'):
            self.assess(context)

    def test_actual_pytest_collection_error_blocks_success(self):
        (self.project / 'test_bad.py').write_text('raise RuntimeError("collection error fixture")\n')
        context, process = self.run_verification()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(read_json(context['result'])['framework_exit_code'], 2)
        with self.assertRaisesRegex(ValueError, 'actual test framework failed'):
            self.assess(context)


@unittest.skipUnless(shutil.which('cmake') and shutil.which('ctest') and (shutil.which('cc') or shutil.which('cl')), 'Real CMake/CTest compiler toolchain unavailable')
class CMakeVerificationTests(unittest.TestCase):
    run_verification = VerificationTests.run_verification
    assess = VerificationTests.assess
    def setUp(self):
        VerificationTests.setUp(self)
        (self.project / 'main.c').write_text('int main(void) { return 0; }\n')
        (self.project / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.21)\nproject(candidate C)\n'
            'enable_testing()\nadd_executable(current main.c)\nadd_test(NAME required COMMAND current)\n')
        self.settings = {'test_report_format': 'ctest', 'test_command': [shutil.which('ctest'), '--output-on-failure'],
            'test_contract': {'protocol': 'cmake-ctest', 'source_root': '.', 'product_files': ['main.c'],
                'required_tests': ['required'], 'not_applicable_tests': [],
                'cmake': {'executable': shutil.which('cmake'), 'configure_args': ['-G', 'Ninja'] if shutil.which('ninja') else [], 'build_args': []}}}

    # This class has its own CMake cases, not the Python protocol cases above.
    def test_current_cmake_build_passes_and_old_external_test_command_is_rejected(self):
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.assess(context)['tests'], [{'id': 'required', 'status': 'PASS'}])
        with (self.project / 'CMakeLists.txt').open('a') as f:
            f.write('add_test(NAME other COMMAND current)\nset_tests_properties(required PROPERTIES DISABLED TRUE)\n')
        context, _ = self.run_verification('disabled')
        with self.assertRaisesRegex(ValueError, 'Required test did not pass'):
            self.assess(context)

    def test_ctest_wrapper_or_old_build_cannot_be_claimed_as_current_binary(self):
        with (self.project / 'CMakeLists.txt').open('a') as f:
            f.write('add_test(NAME external COMMAND "' + sys.executable.replace('\\', '/') + '" -c "print(42)")\n')
        context, process = self.run_verification()
        self.assertNotEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, 'external binary/wrapper'):
            self.assess(context)

    def test_cmake_order_only_dependency_cannot_prove_tested_product(self):
        (self.project / 'product.c').write_text('int product(void) { return 7; }\n')
        self.settings['test_contract']['product_files'] = ['product.c']
        with (self.project / 'CMakeLists.txt').open('a') as f:
            f.write('add_library(product STATIC product.c)\nadd_dependencies(current product)\n')
        context, process = self.run_verification()
        self.assertNotEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, 'not loaded/built'):
            self.assess(context)

    def test_current_linked_library_contributes_product_provenance(self):
        (self.project / 'product.c').write_text('int product(void) { return 7; }\n')
        (self.project / 'main.c').write_text('int product(void); int main(void) { return product() == 7 ? 0 : 1; }\n')
        self.settings['test_contract']['product_files'] = ['product.c']
        with (self.project / 'CMakeLists.txt').open('a') as f:
            f.write('add_library(product STATIC product.c)\ntarget_link_libraries(current PRIVATE product)\n')
        context, process = self.run_verification()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.assess(context)['status'], 'PASS')

    def test_waived_disabled_executable_cannot_prove_executed_product(self):
        (self.project / 'unused.c').write_text('int main(void) { return 0; }\n')
        self.settings['test_contract']['product_files'] = ['unused.c']
        self.settings['test_contract']['not_applicable_tests'] = [{'id': 'optional', 'reason': 'Explicitly waived platform test'}]
        with (self.project / 'CMakeLists.txt').open('a') as f:
            f.write('add_executable(unused unused.c)\nadd_test(NAME optional COMMAND unused)\n'
                    'set_tests_properties(optional PROPERTIES DISABLED TRUE)\n')
        context, process = self.run_verification()
        self.assertNotEqual(process.returncode, 0)
        with self.assertRaisesRegex(ValueError, 'not loaded/built'):
            self.assess(context)

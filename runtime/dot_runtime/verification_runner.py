"""Pinned subprocess entry for approved verification protocols; no model evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def product_sources(request, observed):
    source = Path(request['source']).resolve()
    result = {}
    for name in request['contract']['product_files']:
        path = (source / name).resolve()
        if path not in observed:
            raise ValueError('Product source was not loaded/built by this verification: ' + name)
        result[name] = {'path': str(path), 'sha256': sha(path)}
    return result


def python_tests(request):
    source = Path(request['source']).resolve()
    root = (source / request['contract']['source_root']).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    rows = []
    if request['contract']['protocol'] == 'python-unittest':
        class Result(unittest.TextTestResult):
            def addSuccess(self, test):
                rows.append({'id': test.id(), 'status': 'PASS'}); super().addSuccess(test)
            def addSkip(self, test, reason):
                rows.append({'id': test.id(), 'status': 'SKIP'}); super().addSkip(test, reason)
            def addFailure(self, test, err):
                rows.append({'id': test.id(), 'status': 'FAIL'}); super().addFailure(test, err)
            def addError(self, test, err):
                rows.append({'id': test.id(), 'status': 'ERROR'}); super().addError(test, err)
            def addExpectedFailure(self, test, err):
                rows.append({'id': test.id(), 'status': 'SKIP'}); super().addExpectedFailure(test, err)
            def addUnexpectedSuccess(self, test):
                rows.append({'id': test.id(), 'status': 'FAIL'}); super().addUnexpectedSuccess(test)
            def addSubTest(self, test, subtest, err):
                if err is not None:
                    rows.append({'id': subtest.id(), 'status': 'FAIL'})
                super().addSubTest(test, subtest, err)
        suite = unittest.defaultTestLoader.discover(str(root), top_level_dir=str(root))
        outcome = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=Result).run(suite)
        framework_exit = 0 if outcome.wasSuccessful() else 1
    else:
        import pytest
        class Plugin:
            def pytest_runtest_logreport(self, report):
                name = report.nodeid
                if report.skipped:
                    rows.append({'id': name, 'status': 'SKIP'})
                elif report.failed:
                    rows.append({'id': name, 'status': 'FAIL'})
                elif report.when == 'call':
                    rows.append({'id': name, 'status': 'PASS'})
        framework_exit = int(pytest.main(['-q', '-p', 'no:cacheprovider', str(root)], plugins=[Plugin()]))
    loaded = {Path(module.__file__).resolve() for module in tuple(sys.modules.values())
              if getattr(module, '__file__', None) and str(module.__file__).endswith('.py')}
    return {'tests': rows, 'product_sources': product_sources(request, loaded), 'tested_artifacts': [],
            'framework_exit_code': framework_exit}


def cmake_tests(request):
    source = Path(request['source']).resolve()
    root = (source / request['contract']['source_root']).resolve()
    artifacts = Path(request['artifacts']).resolve()
    build = artifacts / 'build'
    build.mkdir()  # Never reuse an imported build tree.
    query = build / '.cmake/api/v1/query'
    query.mkdir(parents=True)
    (query / 'codemodel-v2').write_text('')
    options = request['contract']['cmake']
    cmake, ctest = options['executable'], request['test_command'][0]
    def command(argv, capture=False):
        print('VERIFICATION COMMAND: ' + json.dumps(argv), flush=True)
        run = subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL, check=True,
                             capture_output=capture, text=True, encoding='utf-8', errors='replace')
        return run.stdout if capture else None
    command([cmake, '-S', str(root), '-B', str(build), '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', *options['configure_args']])
    cache = (build / 'CMakeCache.txt').read_text(encoding='utf-8')
    if 'CMAKE_HOME_DIRECTORY:INTERNAL=' + str(root).replace('\\', '/') not in cache.replace('\\', '/'):
        raise ValueError('CMake build does not refer to the current candidate source')
    compilation = json.loads((build / 'compile_commands.json').read_text(encoding='utf-8'))
    compiled = set()
    for row in compilation:
        path = Path(row['file'])
        path = (Path(row['directory']) / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(source):
            raise ValueError('External/generated source provenance is unsupported: ' + str(path))
        if not Path(row['directory']).resolve().is_relative_to(build):
            raise ValueError('Compilation escaped the fresh build directory')
        compiled.add(path)
    command([cmake, '--build', str(build), *options['build_args']])
    reply = build / '.cmake/api/v1/reply'
    model = json.loads(next(reply.glob('codemodel-v2-*.json')).read_text(encoding='utf-8'))
    targets, built = {}, {}
    for config in model['configurations']:
        for reference in config['targets']:
            target = json.loads((reply / reference['jsonFile']).read_text(encoding='utf-8'))
            targets[target['id']] = target
            if target['type'] == 'EXECUTABLE' and target.get('compileGroups'):
                for artifact in target.get('artifacts', []):
                    path = (build / artifact['path']).resolve()
                    if not path.is_relative_to(build):
                        raise ValueError('CMake executable artifact escaped fresh build')
                    built[path] = target['id']
    config = options['build_args'][1:] if options['build_args'] else []
    config_args = ['-C', config[0]] if config else []
    listing = json.loads(command([ctest, '--test-dir', str(build), *config_args, '--show-only=json-v1'], True))
    tested, target_by_test = [], {}
    for test in listing['tests']:
        argv = test.get('command', [])
        if not argv or Path(argv[0]).resolve() not in built:
            raise ValueError('CTest must execute a current CMake-built executable, not an external binary/wrapper: ' + test['name'])
        path = Path(argv[0]).resolve()
        target_by_test[test['name']] = built[path]
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked CTest executable')
        tested.append({'path': str(path), 'sha256': sha(path)})
    observed, visited = set(), set()
    def dependencies(identifier):
        if identifier in visited:
            return
        visited.add(identifier)
        target = targets[identifier]
        for entry in target.get('sources', []):
            if 'compileGroupIndex' in entry:
                path = (root / entry['path']).resolve()
                if path not in compiled:
                    raise ValueError('Tested target source lacks a current compilation record')
                observed.add(path)
        for dependency in target.get('dependencies', []):
            # File-api dependencies include add_dependencies ordering edges.
            # Such a separately built target does not establish that its code
            # belongs to the executable under test. Only explicit link edges
            # contribute product source provenance in this protocol.
            graph = target.get('backtraceGraph', {})
            index = dependency.get('backtrace')
            nodes, commands = graph.get('nodes', []), graph.get('commands', [])
            node = nodes[index] if isinstance(index, int) and index < len(nodes) else {}
            command_index = node.get('command')
            command_name = commands[command_index] if isinstance(command_index, int) and command_index < len(commands) else None
            linked = targets[dependency['id']]
            fragments = target.get('link', {}).get('commandFragments', [])
            link_inputs = set()
            for fragment in fragments:
                if fragment.get('role') == 'libraries':
                    for argument in shlex.split(fragment['fragment']):
                        link_inputs.add((build / argument).resolve())
            artifacts_for_target = {(build / item['path']).resolve() for item in linked.get('artifacts', [])}
            if (command_name == 'target_link_libraries' and linked.get('type') in ('STATIC_LIBRARY', 'SHARED_LIBRARY')
                    and artifacts_for_target & link_inputs):
                dependencies(dependency['id'])
    junit = artifacts / 'ctest-results.xml'
    # Nonzero is retained as structured FAIL below; configuration/build failures
    # have no result and are rejected rather than reusing old evidence.
    framework_exit = 0
    try:
        command([ctest, '--test-dir', str(build), *config_args, '--output-on-failure', '--output-junit', str(junit)])
    except subprocess.CalledProcessError as exc:
        framework_exit = exc.returncode
    rows = []
    for case in ET.parse(junit).getroot().iter('testcase'):
        status = 'SKIP' if case.find('skipped') is not None or case.get('status') in ('notrun', 'disabled', 'skipped') else ('FAIL' if case.find('failure') is not None or case.find('error') is not None else 'PASS')
        rows.append({'id': case.attrib['name'], 'status': status})
        if status == 'PASS':
            dependencies(target_by_test[case.attrib['name']])
    return {'tests': rows, 'product_sources': product_sources(request, observed), 'tested_artifacts': tested,
            'framework_exit_code': framework_exit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding='utf-8'))
    os.environ['REQPILOT_TEST_ARTIFACTS'] = request['artifacts']
    os.environ['REQPILOT_FACILITY_DIR'] = request['facility']
    report = {'request_sha256': sha(args.request)}
    result = Path(request['artifacts']) / 'verification-result.json'
    if result.exists():
        raise ValueError('Verification result already exists; do not replay this attempt')
    try:
        report.update(cmake_tests(request) if request['contract']['protocol'] == 'cmake-ctest' else python_tests(request))
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    result.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('REQPILOT_VERIFICATION_RESULT_SHA256=' + sha(result), flush=True)
    return 1 if report.get('error') or report.get('framework_exit_code') != 0 or any(row['status'] in ('FAIL', 'ERROR') for row in report.get('tests', [])) else 0


if __name__ == '__main__':
    raise SystemExit(main())

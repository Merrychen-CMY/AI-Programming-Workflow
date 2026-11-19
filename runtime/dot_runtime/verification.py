"""Candidate-bound verification contracts and disposable execution workspaces.

This is an evidence protocol, not a hostile-code sandbox. Approved test code and
toolchains remain subject to independent review. Arbitrary wrapper commands are
not a supported way of establishing product provenance.
"""
import json
import re
from pathlib import Path, PurePosixPath
import sys

from .snapshot import snapshot_project, verify_snapshot


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts):
        raise ValueError('Verification product paths must stay inside the candidate')
    return value


def validate(settings):
    contract = settings.get('test_contract')
    if not isinstance(contract, dict):
        raise ValueError('Verification needs test_contract; revise and independently review the legacy plan')
    required = {'protocol', 'source_root', 'product_files', 'required_tests', 'not_applicable_tests'}
    if not required <= set(contract) or set(contract) - required - {'cmake'}:
        raise ValueError('Incomplete or unsupported test_contract fields')
    protocol = contract['protocol']
    formats = {'python-unittest': 'unittest', 'python-pytest': 'pytest', 'cmake-ctest': 'ctest'}
    if not isinstance(protocol, str) or formats.get(protocol) != settings.get('test_report_format'):
        raise ValueError('Verification protocol and report format must match')
    relative(contract['source_root'])
    files = contract['product_files']
    if not isinstance(files, list) or not files or any(not isinstance(v, str) for v in files) or len(set(files)) != len(files):
        raise ValueError('List the exact product source files that verification must load or build')
    for path in files:
        relative(path)
    names = contract['required_tests']
    if not isinstance(names, list) or not names or any(not isinstance(v, str) or not v.strip() for v in names) or len(set(names)) != len(names):
        raise ValueError('required_tests must contain unique exact test identifiers')
    waived = contract['not_applicable_tests']
    if not isinstance(waived, list) or any(not isinstance(v, dict) or set(v) != {'id', 'reason'}
            or not all(isinstance(v[k], str) and v[k].strip() for k in ('id', 'reason')) for v in waived):
        raise ValueError('Every approved non-applicable test needs an exact id and reason')
    ids = [v['id'] for v in waived]
    if len(set(ids)) != len(ids) or set(ids) & set(names):
        raise ValueError('Required tests cannot also be waived')
    command = settings.get('test_command', [])
    if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
        raise ValueError('Verification needs an executable and supported runner arguments')
    if protocol.startswith('python-'):
        module = protocol.removeprefix('python-')
        allowed = (['-B', '-m', module], ['-B', '-m', module, '-v'], ['-B', '-m', module, '-q'])
        if command[1:] not in allowed or 'cmake' in contract:
            raise ValueError('Python verification uses the direct module protocol, without wrapper scripts or external test paths')
    else:
        if command[1:] != ['--output-on-failure']:
            raise ValueError('CTest build directory is supplied by the controller; external --test-dir and wrappers are forbidden')
        cmake = contract.get('cmake')
        if not isinstance(cmake, dict) or set(cmake) != {'executable', 'configure_args', 'build_args'} or not isinstance(cmake['executable'], str) or not cmake['executable']:
            raise ValueError('cmake-ctest needs the approved CMake executable and configuration/build arguments')
        # Root/build paths and file-api requests are controller-owned. Keep the
        # first protocol deliberately narrow instead of accepting shell wrappers.
        args = cmake['configure_args']
        if not isinstance(args, list) or any(not isinstance(v, str) or not v for v in args):
            raise ValueError('Invalid CMake configure arguments')
        index = 0
        while index < len(args):
            value = args[index]
            if value in ('-G', '-A', '-T') and index + 1 < len(args):
                index += 2
            elif value.startswith(('-DCMAKE_BUILD_TYPE=', '-DCMAKE_C_COMPILER=', '-DCMAKE_CXX_COMPILER=', '-DCMAKE_MAKE_PROGRAM=')):
                index += 1
            else:
                raise ValueError('Unsupported CMake argument; source/build paths, imports and arbitrary wrappers cannot establish candidate provenance')
        if cmake['build_args'] not in ([], ['--config', 'Debug'], ['--config', 'Release']):
            raise ValueError('Only default, Debug or Release CMake builds are supported')
    return contract


def prepare(candidate, destination, settings):
    from .controller import atomic_json
    contract = validate(settings)
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Verification workspace must be fresh')
    source = destination / 'source'
    manifest = snapshot_project(Path(candidate), source)
    for name in contract['product_files']:
        if name not in manifest['files']:
            raise ValueError('Required verification product source is absent from candidate: ' + name)
    artifacts = destination / 'artifacts'
    artifacts.mkdir()
    request = {'schema_version': 1, 'source': str(source), 'artifacts': str(artifacts),
        'facility': str(destination.parent.parent / 'facility-workspace'),
        'contract': contract, 'test_command': settings['test_command'], 'source_snapshot': manifest}
    request_path = destination / 'request.json'
    atomic_json(request_path, request)
    executable = settings['test_command'][0] if contract['protocol'].startswith('python-') else sys.executable
    command = [executable, '-B', str(Path(__file__).with_name('verification_runner.py')), '--request', str(request_path)]
    from .controller import file_hash
    return {**request, 'request': str(request_path), 'request_sha256': file_hash(request_path),
            'result': str(artifacts / 'verification-result.json'), 'command': command}


def assess(context, execution_output):
    """Validate runtime-owned provenance and every approved obligation, not a count."""
    from .controller import file_hash
    source = Path(context['source'])
    if not verify_snapshot(source, context['source_snapshot']):
        raise ValueError('Independent verification changed candidate source inputs')
    path = Path(context['result'])
    if not path.is_file() or path.is_symlink():
        raise ValueError('Controlled verification produced no provenance report')
    commitments = re.findall(r'(?m)^REQPILOT_VERIFICATION_RESULT_SHA256=([0-9a-f]{64})\r?$', execution_output)
    if commitments != [file_hash(path)]:
        raise ValueError('Verification report differs from the result committed in actual command output')
    report = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(report, dict):
        raise ValueError('Verification report must be an object')
    if file_hash(context['request']) != context['request_sha256'] or report.get('request_sha256') != context['request_sha256']:
        raise ValueError('Verification report belongs to a different execution request')
    if 'error' in report:
        raise ValueError('Verification provenance rejected: ' + str(report['error']))
    if type(report.get('framework_exit_code')) is not int or report['framework_exit_code'] != 0:
        raise ValueError('The actual test framework failed or did not finish: ' + str(report.get('framework_exit_code')))
    sources = report.get('product_sources')
    if not isinstance(sources, dict):
        raise ValueError('Verification product_sources must be an object')
    for name in context['contract']['product_files']:
        item = sources.get(name)
        if not isinstance(item, dict):
            raise ValueError('Missing structured product source evidence: ' + name)
        expected = (source / name).resolve()
        if item.get('path') != str(expected) or item.get('sha256') != context['source_snapshot']['files'][name]:
            raise ValueError('Tested product source is not the current candidate: ' + name)
    artifacts = report.get('tested_artifacts')
    if not isinstance(artifacts, list) or any(not isinstance(item, dict)
            or not all(isinstance(item.get(key), str) and item[key].strip() for key in ('path', 'sha256'))
            for item in artifacts):
        raise ValueError('Verification tested_artifacts must contain paths and hashes')
    for item in artifacts:
        artifact = Path(item['path'])
        if (not artifact.resolve().is_relative_to(Path(context['artifacts']).resolve()) or artifact.is_symlink()
                or not artifact.is_file() or file_hash(artifact) != item['sha256']):
            raise ValueError('Tested build artifact changed or escaped the fresh build')
    rows = report.get('tests', [])
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or set(row) != {'id', 'status'}
            or not isinstance(row['id'], str) or not row['id'].strip()
            or not isinstance(row['status'], str) or row['status'] not in ('PASS', 'SKIP', 'FAIL', 'ERROR')
            for row in rows):
        raise ValueError('Missing structured executed-test identities')
    by_id = {row['id']: row['status'] for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('Ambiguous duplicate full test identifiers')
    for name in context['contract']['required_tests']:
        if by_id.get(name) != 'PASS':
            raise ValueError('Required test did not pass: ' + name + ' (' + by_id.get(name, 'MISSING') + ')')
    waived = {row['id'] for row in context['contract']['not_applicable_tests']}
    for name, status in by_id.items():
        if status != 'PASS' and not (status == 'SKIP' and name in waived):
            raise ValueError('Test failure or unapproved skipped obligation: ' + name + ' (' + status + ')')
    return {'status': 'PASS', 'tests': rows, 'product_sources': report['product_sources'],
        'source': str(source), 'source_snapshot': context['source_snapshot'],
        'tested_artifacts': report.get('tested_artifacts', []), 'report_path': str(path),
        'report_sha256': file_hash(path), 'observed_test_count': sum(row['status'] == 'PASS' for row in rows)}


def check_record(record):
    from .controller import file_hash
    if not verify_snapshot(Path(record['source']), record['source_snapshot']):
        raise ValueError('Verified source copy changed after testing')
    if file_hash(record['report_path']) != record['report_sha256']:
        raise ValueError('Verification provenance report changed after testing')
    for item in record['tested_artifacts']:
        if file_hash(item['path']) != item['sha256']:
            raise ValueError('Tested artifact changed after testing')


def require_command_event(context, result):
    """Accept a plain exact argv invocation (including PowerShell's call operator).

    Shell wrappers, pipelines, environment overrides and added commands cannot
    establish that the pinned verification runner produced these artifacts.
    """
    for event in result.get('runtime_evidence', {}).get('command_events', []):
        command = event.get('command')
        if not isinstance(command, str) or event.get('exit_code') != 0:
            continue
        parts = re.findall(r"'([^']*)'|\"([^\"]*)\"|([^\s]+)", command)
        tokens = [next((part for part in row if part), '') for row in parts]
        if tokens[:1] == ['&']:
            tokens.pop(0)
        if tokens == context['command']:
            return event.get('aggregated_output', '')
    raise ValueError('Independent test lacks a successful exact pinned verification command event')

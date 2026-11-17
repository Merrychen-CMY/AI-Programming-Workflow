"""Bind a real Doxygen invocation to the approved candidate and source scope."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / 'standards/embedded-c/v1.3/doxygen'
CHECKER = ROOT / 'scripts/check_doxygen.py'


def checker_helpers():
    spec = importlib.util.spec_from_file_location('workflow_doxygen_checker', CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != {
            'executable', 'expected_version', 'include', 'timeout_seconds'}:
        raise ValueError('C plans require verification.doxygen executable/version/include/timeout')
    for name in ('executable', 'expected_version'):
        if (not isinstance(settings[name], str) or not settings[name].strip()
                or 'REPLACE_WITH' in settings[name] or '\x00' in settings[name]):
            raise ValueError('Pin the actual Doxygen executable and version before approval')
    import re
    if not re.fullmatch(r'\d+\.\d+\.\d+', settings['expected_version']):
        raise ValueError('Pin an exact Doxygen major.minor.patch version')
    executable = settings['executable']
    if ('/' in executable or '\\' in executable) and not Path(executable).is_absolute():
        raise ValueError('Use an absolute Doxygen executable path or a name available on PATH')
    if type(settings['timeout_seconds']) is not int or settings['timeout_seconds'] < 1:
        raise ValueError('Doxygen timeout must be a positive integer')
    includes = settings['include']
    if (not isinstance(includes, list) or not includes
            or any(not isinstance(v, str) for v in includes) or len(set(includes)) != len(includes)):
        raise ValueError('Doxygen needs a nonempty unique approved owned-source scope')
    for name in includes:
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or name.startswith('/') or '..' in name.split('/') or '\x00' in name):
            raise ValueError('Doxygen include scope must stay inside the candidate')


def validate_report(report, workspace, output, settings, process, candidate_files):
    """Do not count documentation entities as executed behavioral tests."""
    helpers = checker_helpers()
    files = helpers.source_manifest(workspace, settings['include'])
    if not files or any(candidate_files.get(name) != sha for name, sha in files.items()):
        raise ValueError('Doxygen selected inputs do not match the candidate')
    if process.get('status') != 'PASS' or process.get('exit_code') != 0:
        raise ValueError('Doxygen command did not complete successfully')
    if not isinstance(report, dict):
        raise ValueError('Doxygen report must be an object')
    if report.get('checker') != 'doxygen-owned-c' or report.get('status') != 'PASS':
        raise ValueError('Doxygen actual report is not PASS')
    if (report.get('source_files') != files
            or report.get('source_digest') != helpers.manifest_digest(files)
            or Path(report.get('source_root', '')).resolve() != workspace.resolve()):
        raise ValueError('Doxygen report source identity differs from this candidate copy')
    if (report.get('expected_version') != settings['expected_version']
            or report.get('actual_version') != settings['expected_version']):
        raise ValueError('Doxygen report tool version differs from the approved plan')
    import hashlib
    expected_config = helpers.build_config(workspace, files, output)
    config_path = output / 'Doxyfile'
    if (Path(report.get('config_path', '')).resolve() != config_path.resolve()
            or config_path.read_text(encoding='utf-8') != expected_config
            or report.get('config_sha256') != hashlib.sha256(config_path.read_bytes()).hexdigest()):
        raise ValueError('Doxygen configuration is missing, altered or unbound')
    commands = report.get('commands', [])
    if (not isinstance(commands, list) or any(not isinstance(r, dict) for r in commands)
            or [r.get('phase') for r in commands] != ['version', 'generate']
            or any(r.get('exit_code') != 0 for r in commands)):
        raise ValueError('Doxygen report lacks successful actual version and generation commands')
    if (report.get('findings') != [] or not isinstance(report.get('xml_files'), dict)
            or not report['xml_files']):
        raise ValueError('Doxygen report contains findings or lacks XML artifacts')
    if (not isinstance(report.get('checks'), dict)
            or report['checks'].get('source_file_count') != len(files)):
        raise ValueError('Doxygen report source coverage count differs')
    for relative, sha in report['xml_files'].items():
        if not isinstance(relative, str) or not isinstance(sha, str):
            raise ValueError('Doxygen XML artifact identities must be strings')
        path = (output / relative).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            raise ValueError('Doxygen XML artifact escaped or disappeared')
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('Doxygen XML artifact digest differs')
    return files

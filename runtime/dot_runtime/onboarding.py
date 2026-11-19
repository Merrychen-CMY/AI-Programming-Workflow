"""Onboarding integrity checks. Human identity comes from the native host UI.

This local pipe is not an authentication boundary against same-account code.
The public model tools never accept a confirmation flag or receipt.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .bridge import Bridge
from .controller import Controller, atomic_json, digest, file_hash, read_json, run_lock
from .delegation import validate_delegation
from .snapshot import _root, _inventory, _modes, _manifest
from .clarifications import UNRESOLVED
from . import resources

TOPICS = ('requirements', 'baseline', 'scope', 'functions', 'acceptance',
          'test_environment', 'test_plan', 'delivery', 'safety')
DRAFT_KEYS = {'project', 'raw_input', 'topics', 'hard_constraints', 'allowed_paths',
              'limits', 'expires_at', 'environment_checks'}


def delegation(draft):
    return {'schema_version': 1, 'authority': 'delegated_l1',
            **({'adaptive_limits': draft['adaptive_limits']} if 'adaptive_limits' in draft else {}),
            'source_message': 'Native Harness startup confirmation; see confirmation.json',
            'raw_input_sha256': hashlib.sha256(draft['raw_input'].encode('utf-8')).hexdigest(),
            'expires_at': draft['expires_at'], 'hard_constraints': draft['hard_constraints'],
            'allowed_paths': draft['allowed_paths'], 'external_delivery': False,
            'limits': draft['limits']}


def validate_draft(draft, task):
    if not isinstance(draft, dict) or set(draft) not in (DRAFT_KEYS, DRAFT_KEYS | {'adaptive_limits'}):
        raise ValueError('Use every field in templates/harness-start.example.json; no extra fields')
    if not isinstance(draft['raw_input'], str) or not draft['raw_input'].strip():
        raise ValueError('Original user input is required')
    if set(draft['topics']) != set(TOPICS) or any(
            not isinstance(draft['topics'][key], str) or not draft['topics'][key].strip()
            or UNRESOLVED.fullmatch(draft['topics'][key].strip())
            for key in TOPICS):
        raise ValueError('All nine discovery topics require explicit answers; unresolved facts cannot pass')
    text = json.dumps(draft, ensure_ascii=False)
    if len(text) > 60_000 or 'REPLACE_WITH_' in text or 'sk-proj-' in text:
        raise ValueError('Draft is too large, contains placeholders or resembles an API secret')
    validate_delegation(delegation(draft))
    if not Path(draft['project']).is_absolute():
        raise ValueError('Project path must be absolute')
    project = _root(Path(draft['project']), exists=True)
    task = _root(task, exists=False)
    if project.is_relative_to(task) or task.is_relative_to(project):
        raise ValueError('Plugin task records and product workspace must be disjoint')
    checks = draft['environment_checks']
    if not isinstance(checks, list) or not checks:
        raise ValueError('Declare required toolchain, test entries and hardware checks')
    for check in checks:
        if not isinstance(check, dict) or set(check) != {'name', 'kind', 'path', 'required', 'evidence'}:
            raise ValueError('Invalid environment check fields')
        if type(check['required']) is not bool or check['kind'] not in ('file', 'directory', 'manual'):
            raise ValueError('Environment checks allow only file/directory existence or explicit manual evidence')
        if not check['name'].strip() or not isinstance(check['evidence'], str):
            raise ValueError('Environment check needs a name and evidence description')
        if check['kind'] != 'manual' and not Path(check['path']).is_absolute():
            raise ValueError('Environment paths must be absolute')
    return project


def fingerprint(project):
    root = _root(Path(project), exists=True)
    first = _manifest(_inventory(root), _modes(root))
    if not first['files']:
        raise ValueError('Project must contain at least one real input file before startup')
    if first != _manifest(_inventory(root), _modes(root)):
        raise ValueError('Project changed while inspecting its baseline')
    return {'id': first['id'], 'file_count': len(first['files'])}


def tool_check(executable, args):
    try:
        result = subprocess.run([executable, *args], capture_output=True, timeout=15,
                                encoding='utf-8', errors='replace')
        # Never return login output, environment values or raw exception text.
        return {'status': 'PASS' if result.returncode == 0 else 'FAIL', 'exit_code': result.returncode}
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'FAIL', 'reason': 'Executable unavailable or probe timed out'}


def check_environment(settings):
    """Check base tools before discovery; no project, requirements or API needed here."""
    checks = [
        {'name': 'Python >= 3.11', 'status': 'PASS' if sys.version_info >= (3, 11) else 'FAIL', 'required': True},
        {'name': 'Git', 'required': True, **tool_check('git', ['--version'])},
        {'name': 'Codex CLI', 'required': True, **tool_check(settings['codex'], ['--version'])},
        {'name': 'Codex login status', 'required': True, **tool_check(settings['codex'], ['login', 'status'])},
    ]
    return {'checks': checks, 'ready': all(c['status'] == 'PASS' for c in checks),
            'limits': 'Base tools only; project-specific toolchain, tests and hardware are checked after requirements are known.'}


def require_environment(task, settings):
    path = task / 'environment.json'
    if not path.is_file():
        raise ValueError('Complete the basic environment check before requirements discovery')
    record = read_json(path)
    if record.get('ready') is not True or record.get('settings') != settings:
        raise ValueError('Basic environment failed, is unknown, or configuration changed; check again before discovery')


def inspect(draft, task, settings):
    require_environment(task, settings)
    project = validate_draft(draft, task)
    checks = check_environment(settings)['checks']
    for item in draft['environment_checks']:
        if item['kind'] == 'manual':
            status = 'USER_ATTESTED' if item['evidence'].strip() and not UNRESOLVED.search(item['evidence']) else 'UNKNOWN'
        else:
            p = Path(item['path'])
            status = 'PASS' if (p.is_file() if item['kind'] == 'file' else p.is_dir()) else 'FAIL'
        checks.append({**item, 'status': status})
    return {'baseline': fingerprint(project), 'checks': checks,
            'ready': all(not c['required'] or c['status'] in ('PASS', 'USER_ATTESTED') for c in checks),
            'limits': 'Existence and login probes do not prove compilation, device access or HIL results; manual evidence is shown to the user.'}


def load_task(request):
    task = _root(Path(request['task']), exists=False)
    if not task.is_absolute():
        raise ValueError('Absolute task directory required')
    task.mkdir(parents=True, exist_ok=True)
    return task


def dispatch(request):
    action = request['action']
    task = load_task(request)
    with run_lock(task):
        confirmed = (task / 'confirmation.json').exists()
        if action == 'environment':
            if confirmed:
                raise ValueError('Existing task is pinned; use status without resetting onboarding')
            return check_environment(request['settings'])
        if action == 'inspect':
            if confirmed:
                raise ValueError('Existing task is pinned; new drafts cannot reset its records or budgets')
            inspection = inspect(request['draft'], task, request['settings'])
            return {'inspection': inspection, 'digest': digest({'draft': request['draft'],
                    'settings': request['settings'], 'inspection': inspection})}
        if action == 'confirm':
            if confirmed:
                raise ValueError('Startup was already confirmed')
            draft, settings = request['draft'], request['settings']
            current = inspect(draft, task, settings)
            if not current['ready'] or digest({'draft': draft, 'settings': settings, 'inspection': current}) != request['digest']:
                raise ValueError('Inputs or environment changed during human confirmation; review the new summary')
            (task / 'raw-input.md').write_text(draft['raw_input'], encoding='utf-8', newline='\n')
            atomic_json(task / 'delegation.json', delegation(draft))
            atomic_json(task / 'draft.json', draft)
            bridge_settings = {'project': str(Path(draft['project']).resolve()),
                'control_dir': str(task / 'control'), 'raw_input': str(task / 'raw-input.md'),
                'delegation': str(task / 'delegation.json'), 'model': settings['model'], 'codex': settings['codex'],
                'environment_context': json.dumps({'topics': draft['topics'], 'inspection': current}, ensure_ascii=False)}
            atomic_json(task / 'bridge.json', bridge_settings)
            atomic_json(task / 'confirmation.json', {'authority': 'local_harness_user', 'session': request['session'],
                'at': datetime.now(timezone.utc).isoformat(), 'digest': request['digest'],
                'baseline': current['baseline'], 'settings': settings,
                'bridge_sha256': digest(bridge_settings), 'draft_sha256': digest(draft),
                'delegation_sha256': digest(delegation(draft)), 'external_delivery': False})
            return {'state': 'START_CONFIRMED', 'next':
                    ('l1_adjust_limits within user ceilings; ' if draft.get('adaptive_limits') else '')
                    + 'dot_plan; dot_initialize; L1 independent approvals; dot_run'}
        if action == 'status' and not confirmed:
            return {'state': 'DISCOVERY', 'start_confirmed': False}
        if not confirmed:
            raise ValueError('Native user startup confirmation required before DOT tools')
        receipt = read_json(task / 'confirmation.json')
        if action in ('initialization_preview', 'initialization_commit', 'initialization_finish'):
            from .initialization_recovery import dispatch as recover_initialization
            return recover_initialization(task, receipt, request)
        if (task / 'initialization-recovery-pending.json').exists():
            raise ValueError('INITIALIZATION_RECOVERY_PENDING: finish the approved local recovery before task tools')
        if action in ('upgrade_preview', 'upgrade_commit', 'upgrade_recover'):
            from .upgrades import dispatch as upgrade
            return upgrade(task, receipt, request)
        if (task / 'runtime-upgrade-pending.json').exists():
            raise ValueError('UPGRADE_PENDING: finish the recorded local migration before task tools')
        if (task / 'runtime-upgrades.json').exists():
            from .upgrades import journal
            _, current = journal(task, receipt)
            if current != file_hash(Path(__file__).resolve().parents[2] / 'MANIFEST.json'):
                raise ValueError('Task must use its upgraded runtime')
        settings = read_json(task / 'bridge.json')
        if receipt['bridge_sha256'] != digest(settings) or receipt['draft_sha256'] != digest(read_json(task / 'draft.json')):
            raise ValueError('Confirmed configuration or draft changed')
        bridge = Bridge(settings)
        if action in ('api_retry_preview', 'api_retry_decide'):
            from . import api_recovery
            with run_lock(bridge.root):
                original = read_json(settings['delegation'])
                if digest(original) != receipt['delegation_sha256'] or (task / 'interrupted.json').exists():
                    raise ValueError('API review cannot rewrite the delegation or clear an interrupted host operation')
                api_recovery.check_task(bridge.root)
                pending = api_recovery.load(bridge.root)['pending']
                if action == 'api_retry_preview':
                    if not pending:
                        raise ValueError('No pending API retry decision')
                    return pending
                return api_recovery.decide(bridge.root, original, bridge.project,
                    request['sha256'], request['approved'], request['session'])
        if action in ('authorization_preview', 'authorization_decide'):
            with run_lock(bridge.root):
                original = read_json(settings['delegation'])
                if digest(original) != receipt['delegation_sha256']:
                    raise ValueError('Original delegation changed')
                state = read_json(bridge.run_dir / 'state.json') if (bridge.run_dir / 'state.json').exists() else None
                if (task / 'interrupted.json').exists() or (state and (state.get('active_run') or state.get('report'))) or (bridge.root / 'preflight-report.json').exists():
                    raise ValueError('Unknown operation or final report requires verified recovery, not an authorization rewrite')
                pending = resources.read(bridge.root, original)['pending']
                if action == 'authorization_preview':
                    if not pending:
                        raise ValueError('No pending authorization request')
                    return pending
                before_scope = resources.scope_digest(original, bridge.root)
                result = resources.decide_authorization(bridge.root, original, request['sha256'],
                                                        request['approved'], request['session'], state)
                if result['accepted'] and before_scope != resources.scope_digest(original, bridge.root):
                    atomic_json(bridge.root / 'planning-status.json', {'status': 'NEEDS_INFO',
                        'reason': 'User changed authorized scope; replan and independently review the new baseline', 'gaps': []})
                    if state:
                        controller = Controller(bridge.run_dir)
                        controller.state['pending_authorization_revision'] = True
                        controller.state['approvals'] = {}
                        if controller.state['state'] == 'DECISION_PENDING':
                            controller.save()
                        else:
                            controller.transition('DECISION_PENDING', 'User authorized new scope; revise before independent approval')
                return result
        if action == 'status':
            return bridge.call('dot_status', {})
        if action == 'report_preview':
            status = bridge.call('dot_status', {})
            if status.get('state') != 'REPORT_PENDING' or not (status.get('report') or {}).get('l1_submitted'):
                preflight = status.get('preflight_report')
                if preflight:
                    return {'preflight': True, 'report': preflight, 'sha256': digest(preflight)}
                raise ValueError('L1 must submit the final report first')
            return {'preflight': False, 'report': status['final_report'], 'sha256': status['report']['sha256']}
        if action == 'report_confirm':
            preflight = bridge.root / 'preflight-report.json'
            if not (bridge.run_dir / 'state.json').exists() and preflight.exists():
                if digest(read_json(preflight)) != request['sha256']:
                    raise ValueError('Report changed during review')
                atomic_json(task / 'preflight-review.json', {'authority': 'local_harness_user',
                    'sha256': request['sha256'], 'execution_authorized': False})
                return {'state': 'BLOCKED', 'reviewed': True, 'execution_authorized': False}
            controller = Controller(bridge.run_dir)
            controller.approve('report', confirm=lambda kind, path, sha: sha == request['sha256'],
                               authority='local_harness_user')
            return bridge.status()
        if action != 'bridge':
            raise ValueError('Unknown private operation')
        name, arguments = request['name'], request['arguments']
        from .initialization_recovery import effective_baseline
        current_baseline = effective_baseline(task, receipt)
        if name in ('l1_approve', 'l1_submit_report', 'l1_add_facts', 'l1_adjust_limits', 'l1_request_authorization', 'l1_review_api'):
            if 'l1_session_id' in arguments:
                raise ValueError('L1 session identity comes from the host, not model arguments')
            arguments = {**arguments, 'l1_session_id': request['session']}
        if name not in ('dot_status', 'l1_stop', 'l1_submit_report'):
            if digest(read_json(task / 'delegation.json')) != receipt['delegation_sha256']:
                raise ValueError('Confirmed delegation changed')
            if (name not in ('l1_adjust_limits', 'l1_request_authorization', 'l1_review_api')
                    and not (bridge.run_dir / 'state.json').exists() and fingerprint(settings['project']) != current_baseline):
                raise ValueError('Original project changed after startup confirmation; do not plan or initialize')
        result = bridge.call(name, arguments)
        if name == 'dot_initialize' and result.get('accepted') is not False:
            # Close the check/copy race: initialization cannot authorize any worker.
            state = read_json(bridge.run_dir / 'state.json')
            if state['initial_snapshot']['id'] != current_baseline['id']:
                Controller(bridge.run_dir).stop_with_report('Initial snapshot differs from startup baseline')
                raise ValueError('Initial snapshot differs from confirmed baseline')
            from .upgrades import records_for_control
            upgrades = records_for_control(bridge.root)
            if upgrades and not state.get('runtime_upgrade'):
                state['runtime_upgrade'] = upgrades[-1]
                state['history'].extend({'action': 'runtime_upgrade', 'at': item['at'], 'upgrade': item,
                                         'before_initialization': True} for item in upgrades)
                atomic_json(bridge.run_dir / 'state.json', state)
        return result

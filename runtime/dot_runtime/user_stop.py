"""Native stop intent pauses dispatch; it never clears operations or finalizes."""
from contextlib import ExitStack
from pathlib import Path

from .controller import atomic_json, digest, file_hash, now, read_json, run_lock
from .initialization_recovery import _identity
from .snapshot import _root, _reject_link

PENDING = 'user-stop-pending.json'
RECEIPT = 'user-stop.json'


def status(control):
    root = Path(control)
    for name, state in ((RECEIPT, 'USER_STOP_RECORDED'), (PENDING, 'USER_STOP_PENDING')):
        path = root / name
        if path.exists():
            _reject_link(path)
            return {'state': state, 'record': name,
                    'next': 'L1 reviews closure using user-stop.json' if name == RECEIPT
                    else 'workflow_confirm_stop: obtain the real user decision',
                    'dispatch_paused': True}
    return None


def require_not_paused(control):
    if status(control):
        raise ValueError('USER_STOP: dispatch is paused; only read records, native stop decision and L1 closure are allowed')


def _binding(task, receipt, session):
    _root(task, exists=True)
    settings, _ = _identity(task, receipt, session)
    return {'task': str(Path(task).resolve()), 'project': str(Path(settings['project']).resolve()),
            'control': str((Path(task) / 'control').resolve()), 'session': session,
            'confirmation_sha256': digest(receipt)}


def validate_receipt(control, record):
    """Validate the canonical native receipt and its original task/session binding."""
    root = _root(Path(control), exists=True)
    path = root / RECEIPT
    _reject_link(path)
    if not path.is_file() or read_json(path) != record:
        raise ValueError('User stop must reference the canonical native task receipt')
    receipt = read_json(root.parent / 'confirmation.json')
    binding = _binding(root.parent, receipt, receipt.get('session'))
    expected = {'schema_version', 'authority', 'decision', 'session', 'at', 'task_binding',
                'task_binding_sha256', 'preview_sha256'}
    if (not isinstance(record, dict) or set(record) != expected
            or record['authority'] != 'local_harness_user' or record['decision'] != 'user_requested_stop'
            or record['session'] != binding['session'] or record['task_binding'] != binding
            or record['task_binding_sha256'] != digest(binding) or not record['at']):
        raise ValueError('User stop receipt is not bound to this confirmed native task')
    return record


def _snapshot(task, binding):
    root = task / 'control'
    state = read_json(root / 'run/state.json') if (root / 'run/state.json').exists() else None
    if (state and state.get('report')) or (root / 'preflight-report.json').exists():
        raise ValueError('A final report already exists; stop cannot revoke or reopen it')
    records = {}
    for name in ('interrupted.json', 'control/run/state.json', 'control/resources.json',
                 'control/api-review.json', 'control/api/ledger.json', 'control/requirements-review/state.json',
                 'operation-recovery-pending.json', 'initialization-recovery-pending.json',
                 'runtime-upgrade-pending.json'):
        path = task / name
        if path.exists():
            _reject_link(path)
            records[name] = file_hash(path)
    return {'task_binding': binding, 'records': records,
            'runtime_state': state.get('state') if state else 'PREFLIGHT',
            'active_run': state.get('active_run') if state else None,
            'analysis_activity': state.get('analysis_activity') if state else None,
            'implementation_attempts': state.get('implementation_attempts', 0) if state else 0,
            'revision_count': state.get('revision_count', 0) if state else 0}


def dispatch(task, receipt, request):
    task = Path(task)
    action = request['action']
    allowed = {'action', 'task', 'session'} | ({'sha256', 'decision'} if action == 'stop_confirm' else set())
    if action not in ('stop_preview', 'stop_confirm') or set(request) != allowed:
        raise ValueError('Invalid private native stop request')
    binding = _binding(task, receipt, request['session'])
    if any((task / name).exists() for name in ('operation-recovery-pending.json',
            'initialization-recovery-pending.json', 'runtime-upgrade-pending.json')):
        raise ValueError('An approved local transaction already pauses dispatch; finish that exact local transaction before native stop confirmation')
    root = _root(task / 'control', exists=False)
    root.mkdir(exist_ok=True)
    with ExitStack() as locks:
        locks.enter_context(run_lock(root))
        if (root / 'run').exists():
            locks.enter_context(run_lock(root / 'run'))
        if (root / RECEIPT).exists():
            record = validate_receipt(root, read_json(root / RECEIPT))
            return {'status': 'USER_STOP_RECORDED', 'record': record, 'already_confirmed': True,
                    'dispatch_paused': True, 'finalized': False, 'next': 'l1_stop with structured assessment referencing user-stop.json'}
        snapshot = _snapshot(task, binding)
        snapshot_sha = digest(snapshot)
        if action == 'stop_preview':
            previous = read_json(root / PENDING) if (root / PENDING).exists() else None
            pending = {'schema_version': 1, 'at': now(), 'sha256': snapshot_sha, 'snapshot': snapshot}
            # Refreshing the display cannot remove the pause or authorize anything.
            if previous:
                pending['at'] = previous['at']
            atomic_json(root / PENDING, pending)
            from . import resources
            original = read_json(task / 'delegation.json')
            authority = resources.authority(original, root)
            allocation = resources.read(root, original)['current'] if resources.enabled(original, root) else original['limits']
            return {'status': 'USER_STOP_PENDING', 'sha256': snapshot_sha,
                    'project': binding['project'], 'snapshot': snapshot, 'dispatch_paused': True,
                    'resources': {'total_limits': authority['limits'], 'current_limits': allocation,
                                  'final_deadline': authority['expires_at'],
                                  'usage': resources.usage(root, read_json(root / 'run/state.json')
                                                          if (root / 'run/state.json').exists() else None)},
                    'finalized': False, 'host_interrupted': (task / 'interrupted.json').exists()}
        if not (root / PENDING).exists():
            raise ValueError('Native stop preview is required')
        pending = read_json(root / PENDING)
        if request['sha256'] != pending['sha256'] or snapshot_sha != pending['sha256']:
            raise ValueError('Task changed during native stop confirmation; remain paused and display again')
        if request['decision'] not in ('stop', 'continue'):
            raise ValueError('A native explicit stop or continue choice is required')
        if request['decision'] == 'stop':
            record = {'schema_version': 1, 'authority': 'local_harness_user',
                      'decision': 'user_requested_stop', 'session': request['session'], 'at': now(),
                      'task_binding': binding, 'task_binding_sha256': digest(binding),
                      'preview_sha256': snapshot_sha}
            atomic_json(root / RECEIPT, record)  # Durable receipt itself keeps the dispatch guard.
            return {'status': 'USER_STOP_RECORDED', 'record': record, 'dispatch_paused': True,
                    'finalized': False, 'next': 'L1 l1_stop assessment referencing user-stop.json; then l1_submit_report and user review'}
        archive = root / 'user-stop-decisions'
        archive.mkdir(exist_ok=True)
        atomic_json(archive / (snapshot_sha + '.json'), {**pending, 'decision': 'continue',
                    'authority': 'local_harness_user', 'session': request['session'], 'decided_at': now()})
        (root / PENDING).unlink()
        return {'status': 'USER_STOP_NOT_REQUESTED', 'dispatch_paused': False,
                'other_guards_preserved': True, 'finalized': False, 'product_execution_started': False}

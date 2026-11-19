"""Reviewed recovery of an uncommitted, local-only initialization.

This is not a general UNKNOWN override. A native user approves an exact snapshot;
all original records and partial copies are retained, and no product/API action runs.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import stat

from .controller import ROOT, atomic_json, digest, file_hash, now, read_json, run_lock
from .snapshot import _root, _reject_link, _filesystem_path

JOURNAL = 'initialization-recoveries.json'
PENDING = 'initialization-recovery-pending.json'


def journal(task, receipt):
    value = read_json(task / JOURNAL) if (task / JOURNAL).exists() else {'schema_version': 1, 'records': []}
    if value.get('schema_version') != 1 or not isinstance(value.get('records'), list):
        raise ValueError('Invalid initialization recovery history')
    previous = None
    for number, entry in enumerate(value['records'], 1):
        body = {k: v for k, v in entry.items() if k != 'sha256'}
        if (entry['sha256'] != digest(body) or entry['sequence'] != number or entry['previous_sha256'] != previous
                or entry['original_confirmation_sha256'] != file_hash(task / 'confirmation.json')
                or entry['session'] != receipt['session'] or entry['authority'] != 'local_harness_user'):
            raise ValueError('Initialization recovery history changed')
        previous = entry['sha256']
    return value


def records_for_control(root):
    task = Path(root).parent
    return journal(task, read_json(task / 'confirmation.json'))['records'] if (task / JOURNAL).exists() else []


def effective_baseline(task, receipt):
    entries = journal(task, receipt)['records']
    baseline = entries[-1]['baseline_after'] if entries else receipt['baseline']
    initialization_head = entries[-1]['sha256'] if entries else None
    # Operation reconciliation may explicitly adopt a changed pre-init project.
    # Bind it to the initialization journal head instead of comparing wall-clock
    # timestamps: a later initialization recovery supersedes prior adoptions.
    from .operation_recovery import records_for_control
    for entry in records_for_control(task / 'control'):
        adoption = entry.get('baseline_adoption', {})
        if (adoption.get('applies_before_initialization') is True
                and adoption.get('initialization_recovery_sha256') == initialization_head):
            baseline = adoption['after']
    return baseline


def _files(task):
    """Hash every record, including partial evidence; never follow reparse points."""
    _root(task, exists=True)
    result = {}
    def walk(directory):
        for item in sorted(_filesystem_path(directory).iterdir()):
            p = directory / item.name
            mode = _reject_link(p).st_mode
            relative = p.relative_to(task).as_posix()
            if relative == PENDING or p.name == '.lock':
                continue
            if stat.S_ISDIR(mode):
                walk(p)
            elif stat.S_ISREG(mode):
                result[relative] = file_hash(p)
            else:
                raise ValueError('Recovery requires regular local records')
    walk(task)
    return result


def _identity(task, receipt, session):
    if receipt.get('authority') != 'local_harness_user' or receipt['session'] != session:
        raise ValueError('Recovery requires the original native session')
    settings = read_json(task / 'bridge.json')
    for key, name in [('bridge_sha256', 'bridge.json'), ('draft_sha256', 'draft.json'),
                      ('delegation_sha256', 'delegation.json')]:
        if receipt[key] != digest(read_json(task / name)):
            raise ValueError('Original confirmed inputs changed')
    for key, path in [('control_dir', task / 'control'), ('delegation', task / 'delegation.json'),
                      ('raw_input', task / 'raw-input.md')]:
        if Path(settings[key]).resolve() != path.resolve():
            raise ValueError('Recovery requires the native task layout')
    original = read_json(task / 'delegation.json')
    if file_hash(task / 'raw-input.md') != original['raw_input_sha256']:
        raise ValueError('Original raw input changed')
    from . import resources
    if resources.enabled(original, task / 'control'):
        resources.read(task / 'control', original)
    return settings, original


def _guard(task, receipt, session, *, finishing=False):
    settings, original = _identity(task, receipt, session)
    run = task / 'control/run'
    if ((run / 'state.json').exists() or (task / 'control/preflight-report.json').exists()
            or (task / 'preflight-review.json').exists() or (task / 'runtime-upgrade-pending.json').exists()):
        raise ValueError('Recovery only supports uncommitted initialization, never an initialized task or final report')
    if run.exists():
        for p in run.iterdir():
            if p.name not in ('.lock', 'initial-project', 'initialization.json'):
                raise ValueError('Unexpected run records require separate reconciliation: ' + p.name)
    marker = read_json(task / 'interrupted.json') if (task / 'interrupted.json').exists() else None
    operation = read_json(run / 'initialization.json') if (run / 'initialization.json').exists() else None
    if marker and (marker.get('name') != 'dot_initialize' or marker.get('status') != 'UNKNOWN'):
        raise ValueError('Only a dot_initialize interruption can be recovered here')
    if not finishing and not marker and not (operation and operation.get('operation') == 'dot_initialize'
                                            and operation.get('status') == 'FAILED'):
        raise ValueError('No recoverable initialization failure')
    ledger = task / 'control/api/ledger.json'
    from . import api_recovery
    if (not ledger.exists() or api_recovery.unresolved(task / 'control')
            or api_recovery.load(task / 'control')['pending']):
        raise ValueError('Missing or unresolved API ledger requires separate reconciliation')
    return settings, original, operation


def _target():
    return {'version': (ROOT / 'VERSION').read_text().strip(), 'manifest_sha256': file_hash(ROOT / 'MANIFEST.json')}


def preview(task, receipt, session, source):
    if (task / PENDING).exists():
        raise ValueError('INITIALIZATION_RECOVERY_PENDING: finish the approved local recovery first')
    settings, original, operation = _guard(task, receipt, session)
    from .upgrades import journal as runtime_journal
    from .onboarding import fingerprint
    from . import resources
    runtimes, current = runtime_journal(task, receipt)
    target = _target()
    policy = read_json(ROOT / 'policy/initialization-recovery.json')
    if (source.get('manifest_sha256') != current or source.get('version') not in policy['supported_source_versions']
            or tuple(map(int, source['version'].split('.'))) > tuple(map(int, target['version'].split('.')))):
        raise ValueError('Unsupported initialization recovery source')
    before, after = effective_baseline(task, receipt), fingerprint(settings['project'])
    files = _files(task)
    # Inspect twice to reject changes while building a reviewable snapshot.
    if after != fingerprint(settings['project']) or files != _files(task):
        raise ValueError('Records changed during recovery preview')
    value = {'source': source, 'target': target, 'baseline_before': before, 'baseline_after': after,
             'baseline_changed': before != after, 'project': settings['project'],
             'original_confirmation_sha256': file_hash(task / 'confirmation.json'),
             'records_sha256': digest(files), 'failure': operation,
             'usage': resources.report(task / 'control', original)['dot_api_reported_usage'],
             'api_calls': len(read_json(task / 'control/api/ledger.json')['attempts']),
             'action': 'Archive partial initialization and marker; preserve original authority, usage and history. '
                       'Adopt exactly the displayed baseline; replan if changed, independently review before execution. '
                       'No API, worker or product write.',
             'baseline_detail_limit': 'Legacy startup stored only a digest and file count; a complete historical file diff may be unavailable.'}
    return {**value, 'sha256': digest(value)}


def _encoded_hash(value):
    import hashlib
    return hashlib.sha256((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')).hexdigest()


def _path(task, name):
    # Intents contain only task-relative paths, checked before every move/write.
    if not isinstance(name, str) or '\\' in name or ':' in name or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('Invalid recovery path')
    p = task / name
    if not p.resolve().is_relative_to(task.resolve()):
        raise ValueError('Recovery path escapes task')
    for component in (p, *p.parents):
        if component.exists():
            _reject_link(component)
        if component == task:
            break
    return p


def _finish(task, receipt, session):
    intent = read_json(task / PENDING)
    if intent.get('sha256') != digest({k: v for k, v in intent.items() if k != 'sha256'}):
        raise ValueError('Recovery intent damaged; reconcile separately')
    settings, _, _ = _guard(task, receipt, session, finishing=True)
    from .onboarding import fingerprint
    entry = intent['entry']
    if (entry['target'] != _target() or entry['session'] != session
            or entry['original_confirmation_sha256'] != file_hash(task / 'confirmation.json')
            or entry['baseline_after'] != fingerprint(settings['project'])):
        raise ValueError('Approved recovery snapshot changed; no automatic continuation')
    actual = _files(task)
    expected = dict(intent['before_files'])
    # Each atomic archive rename is either entirely before or entirely after.
    for old, new in intent['moves']:
        old_path, new_path = _path(task, old), _path(task, new)
        rewritten = old in intent['writes'] and actual.get(old) == _encoded_hash(intent['writes'][old])
        if (not old_path.exists() and not new_path.exists()) or (old_path.exists() and new_path.exists() and not rewritten):
            raise ValueError('Recovery archive locations are inconsistent')
        if new_path.exists():
            for p in list(expected):
                if p == old or p.startswith(old + '/'):
                    expected[new + p[len(old):]] = expected.pop(p)
    for path, value in intent['writes'].items():
        _path(task, path)
        if actual.get(path) == _encoded_hash(value):
            expected[path] = actual[path]
    if actual != expected:
        raise ValueError('Recovery records changed; no files overwritten')
    for old, new in intent['moves']:
        source, dest = _path(task, old), _path(task, new)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            _filesystem_path(source).replace(_filesystem_path(dest))
    for path, value in intent['writes'].items():
        destination = _path(task, path)
        if not destination.exists() or file_hash(destination) != _encoded_hash(value):
            atomic_json(destination, value)
    journal(task, receipt)
    (task / PENDING).unlink()
    return {'status': 'INITIALIZATION_RECOVERED', 'record': entry,
            'next': 'Review changed baseline and replan' if entry['baseline_changed'] else 'dot_initialize, then independent L1 approvals',
            'product_execution_started': False}


def dispatch(task, receipt, request):
    # Native caller holds the task lock; old Python initialization holds these same locks.
    with ExitStack() as locks:
        for directory in (task / 'control', task / 'control/run'):
            if directory.exists():
                _root(directory, exists=True)
                locks.enter_context(run_lock(directory))
        if request['action'] == 'initialization_finish':
            if not (task / PENDING).exists():
                return {'status': 'NO_PENDING_INITIALIZATION_RECOVERY'}
            return _finish(task, receipt, request['session'])
        plan = preview(task, receipt, request['session'], request['source'])
        if request['action'] == 'initialization_preview':
            return plan
        if request['action'] != 'initialization_commit' or request.get('sha256') != plan['sha256']:
            raise ValueError('Recovery snapshot changed during native confirmation; review again')
        log = journal(task, receipt)
        entry = {**plan, 'sequence': len(log['records']) + 1, 'at': now(),
                 'session': request['session'], 'authority': 'local_harness_user',
                 'previous_sha256': log['records'][-1]['sha256'] if log['records'] else None}
        entry.pop('sha256')
        entry['approved_preview_sha256'] = plan['sha256']
        entry['sha256'] = digest(entry)
        archive = 'initialization-archives/' + entry['sha256']
        if (task / archive).exists():
            raise ValueError('Recovery archive already exists')
        moves = []
        for name in ('interrupted.json', 'control/run/initial-project', 'control/run/initialization.json'):
            if (task / name).exists():
                moves.append([name, archive + '/' + name])
        writes = {JOURNAL: {'schema_version': 1, 'records': [*log['records'], entry]}}
        if plan['baseline_changed']:
            old = 'control/planning-status.json'
            if (task / old).exists():
                moves.append([old, archive + '/' + old])
            writes[old] = {'status': 'NEEDS_INFO', 'reason': 'User accepted a changed project baseline during initialization recovery; review requirements and replan',
                           'gaps': [], 'recovery_sha256': entry['sha256']}
        from .upgrades import journal as runtime_journal, JOURNAL as RUNTIME_JOURNAL
        runtimes, current = runtime_journal(task, receipt)
        if current != plan['target']['manifest_sha256']:
            upgrade = {k: plan[k] for k in ('source', 'target', 'original_confirmation_sha256')}
            upgrade.update(sequence=len(runtimes['records']) + 1, at=entry['at'], authority='local_harness_user',
                           session=request['session'], previous_sha256=runtimes['records'][-1]['sha256'] if runtimes['records'] else None,
                           task_snapshot_sha256=plan['records_sha256'], initialization_recovery_sha256=entry['sha256'],
                           effect='Reviewed uncommitted initialization recovery; no execution or usage reset')
            upgrade['sha256'] = digest(upgrade)
            writes[RUNTIME_JOURNAL] = {'schema_version': 1, 'records': [*runtimes['records'], upgrade]}
        intent = {'entry': entry, 'before_files': _files(task), 'moves': moves, 'writes': writes}
        intent['sha256'] = digest(intent)
        atomic_json(task / PENDING, intent)
        return _finish(task, receipt, request['session'])

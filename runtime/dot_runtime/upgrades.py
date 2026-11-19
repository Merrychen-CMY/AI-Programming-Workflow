"""Native, quiescent task upgrades. No worker dispatch, authority change or UNKNOWN reset.

Task/control/run OS locks protect the exact reviewed snapshot. A durable intent permits
completion of interrupted local file writes only; product operations are never replayed.
"""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import re

from .controller import ROOT, Controller, atomic_json, digest, file_hash, now, read_json, run_lock
from .snapshot import verify_snapshot

JOURNAL = 'runtime-upgrades.json'
PENDING = 'runtime-upgrade-pending.json'
STATE = 'control/run/state.json'


def journal(task, receipt):
    value = read_json(task / JOURNAL) if (task / JOURNAL).exists() else {'schema_version': 1, 'records': []}
    if value.get('schema_version') != 1 or not isinstance(value.get('records'), list):
        raise ValueError('Invalid runtime upgrade journal')
    current, previous = receipt['settings']['workflow_sha256'], None
    for number, record in enumerate(value['records'], 1):
        body = {k: v for k, v in record.items() if k != 'sha256'}
        if (digest(body) != record['sha256'] or record['sequence'] != number
                or record['previous_sha256'] != previous or record['source']['manifest_sha256'] != current
                or record['original_confirmation_sha256'] != file_hash(task / 'confirmation.json')
                or record['session'] != receipt['session'] or record['authority'] != 'local_harness_user'):
            raise ValueError('Invalid runtime upgrade journal chain')
        current, previous = record['target']['manifest_sha256'], record['sha256']
    return value, current


def _snapshot(task):
    # Hash all historical evidence as well as the live project. Never follow links.
    files = {}
    for path in sorted(task.rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Task upgrade does not accept redirected records')
        name = path.relative_to(task).as_posix()
        if name in (JOURNAL, PENDING, STATE) or path.name == '.lock':
            continue
        if path.is_file():
            files[name] = file_hash(path)
    from .onboarding import fingerprint
    settings = read_json(task / 'bridge.json')
    return digest({'files': files, 'project': fingerprint(settings['project'])})


def _quiescent(task, receipt, session):
    if receipt['session'] != session:
        raise ValueError('Upgrade requires the original native session identity')
    settings = read_json(task / 'bridge.json')
    if (receipt['bridge_sha256'] != digest(settings)
            or receipt['draft_sha256'] != digest(read_json(task / 'draft.json'))
            or receipt['delegation_sha256'] != digest(read_json(task / 'delegation.json'))):
        raise ValueError('Original confirmation inputs changed')
    if (Path(settings['control_dir']).resolve() != (task / 'control').resolve()
            or Path(settings['delegation']).resolve() != (task / 'delegation.json').resolve()
            or Path(settings['raw_input']).resolve() != (task / 'raw-input.md').resolve()):
        raise ValueError('Upgrade requires the native task layout')
    from . import resources
    original = read_json(task / 'delegation.json')
    if file_hash(task / 'raw-input.md') != original['raw_input_sha256']:
        raise ValueError('Original raw input changed')
    if resources.enabled(original, task / 'control'):
        resources.read(task / 'control', original)
    if (task / 'interrupted.json').exists() or (task / 'control/preflight-report.json').exists():
        raise ValueError('Upgrade cannot clear UNKNOWN or reopen a final report')
    ledger = task / 'control/api/ledger.json'
    if ledger.exists() and any(a['status'] != 'COMPLETED' for a in read_json(ledger)['attempts']):
        raise ValueError('Unresolved API attempt must be reconciled before upgrade')
    state = read_json(task / STATE) if (task / STATE).exists() else None
    if state:
        if (state.get('schema_version') != 1 or state.get('active_run') or state.get('report')
                or state['state'] not in ('REVIEW_PENDING', 'PLAN_PENDING', 'DECISION_PENDING', 'EXECUTING', 'VERIFYING')):
            raise ValueError('Upgrade requires a quiescent, nonterminal task with no unresolved operation')
        c = Controller(task / 'control/run')
        c.check_evidence()
        if state.get('completed_tasks') and any(state.get('approved_baseline', {}).get(kind) != file_hash(state[kind])
                                                for kind in ('requirements', 'plan')):
            raise ValueError('Completed tasks require unchanged recorded requirements and plan before upgrade')
        if not verify_snapshot(Path(state['project']), state.get('checkpoint', state['initial_snapshot'])):
            raise ValueError('Unrecorded project changes require reconciliation before upgrade')
    return state


def _target():
    return {'version': (ROOT / 'VERSION').read_text().strip(), 'manifest_sha256': file_hash(ROOT / 'MANIFEST.json')}


def records_for_control(root):
    task = Path(root).parent
    if not (task / JOURNAL).exists():
        return []
    return journal(task, read_json(task / 'confirmation.json'))[0]['records']


def preview(task, receipt, session, source):
    if (task / PENDING).exists():
        raise ValueError('UPGRADE_PENDING: recover the existing local migration before another upgrade')
    state = _quiescent(task, receipt, session)
    log, current = journal(task, receipt)
    target = _target()
    policy = read_json(ROOT / 'policy/task-upgrades.json')
    if (source.get('manifest_sha256') != current or source.get('version') not in policy['supported_source_versions']
            or not re.fullmatch(r'\d+\.\d+\.\d+', source.get('version', ''))
            or tuple(map(int, target['version'].split('.'))) <= tuple(map(int, source['version'].split('.')))):
        raise ValueError('No reviewed forward migration exists for this source and target')
    value = {'source': source, 'target': target, 'task_snapshot_sha256': _snapshot(task),
             'state_sha256': digest(state), 'journal_sha256': digest(log),
             'original_confirmation_sha256': file_hash(task / 'confirmation.json'),
             'state': state['state'] if state else 'START_CONFIRMED',
             'effect': 'Invalidate approvals/candidate; preserve completed tasks, all usage and evidence. '
                       'L1 independent reapproval and full candidate reverification required. No product dispatch.'}
    return {**value, 'sha256': digest(value)}


def _finish(task, receipt, session):
    intent = read_json(task / PENDING)
    if intent.get('sha256') != digest({k: v for k, v in intent.items() if k != 'sha256'}):
        raise ValueError('Upgrade intent is damaged; manual reconciliation required')
    _quiescent(task, receipt, session)
    entry = intent['after_journal']['records'][-1]
    if (entry['target'] != _target() or entry['session'] != session
            or entry['original_confirmation_sha256'] != file_hash(task / 'confirmation.json')
            or entry['task_snapshot_sha256'] != _snapshot(task)):
        raise ValueError('Upgrade snapshot changed; manual reconciliation required')
    state = read_json(task / STATE) if (task / STATE).exists() else None
    log, _ = journal(task, receipt)
    if state not in (intent['before_state'], intent['after_state']) or log not in (intent['before_journal'], intent['after_journal']):
        raise ValueError('Upgrade records changed; manual reconciliation required')
    # Idempotent only for these exact local writes, never an external operation.
    if intent['after_state'] is not None and state != intent['after_state']:
        atomic_json(task / STATE, intent['after_state'])
    if log != intent['after_journal']:
        atomic_json(task / JOURNAL, intent['after_journal'])
    journal(task, receipt)
    (task / PENDING).unlink()
    return {'status': 'UPGRADED', 'record': entry, 'next': 'Read current rules; L1 independently reapproves requirements and plan, then DOT reruns required verification. No new startup or budget reset.'}


def dispatch(task, receipt, request):
    # Caller holds the task lock. Also exclude Bridge and direct controller operations.
    with ExitStack() as locks:
        for directory in (task / 'control', task / 'control/run'):
            if directory.is_dir():
                locks.enter_context(run_lock(directory))
        if request['action'] == 'upgrade_recover':
            if not (task / PENDING).exists():
                return {'status': 'NO_PENDING_UPGRADE'}
            return _finish(task, receipt, request['session'])
        plan = preview(task, receipt, request['session'], request['source'])
        if request['action'] == 'upgrade_preview':
            return plan
        if request['action'] != 'upgrade_commit' or request.get('sha256') != plan['sha256']:
            raise ValueError('Upgrade snapshot changed during native confirmation; review again')
        before = read_json(task / STATE) if (task / STATE).exists() else None
        after = deepcopy(before)
        log, _ = journal(task, receipt)
        entry = {k: plan[k] for k in ('source', 'target', 'task_snapshot_sha256', 'original_confirmation_sha256', 'effect')}
        entry.update(sequence=len(log['records']) + 1, at=now(), authority='local_harness_user', session=request['session'],
                     previous_sha256=log['records'][-1]['sha256'] if log['records'] else None)
        entry['sha256'] = digest(entry)
        if after:
            next_state = 'DECISION_PENDING' if (after['state'] == 'DECISION_PENDING'
                or after.get('pending_authorization_revision') or after.get('pending_clarification_revision')) else 'REVIEW_PENDING'
            # Keep old evidence and decisions in history; do not replay completed implementation.
            after['history'].append({'action': 'runtime_upgrade', 'at': entry['at'], 'from': after['state'],
                'to': next_state, 'upgrade': entry, 'prior_approvals': after['approvals'],
                'prior_candidate': after['candidate'], 'prior_compliance': after.get('compliance')})
            after.update(state=next_state, approvals={}, candidate=None)
            after.pop('compliance', None)
            after['runtime_upgrade'] = entry
        intent = {'before_state': before, 'after_state': after, 'before_journal': log,
                  'after_journal': {'schema_version': 1, 'records': [*log['records'], entry]}}
        intent['sha256'] = digest(intent)
        atomic_json(task / PENDING, intent)
        return _finish(task, receipt, request['session'])

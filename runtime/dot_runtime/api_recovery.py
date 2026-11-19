"""API-only review and bounded next-request authorization; never erase an unknown attempt."""
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
import time

from .controller import atomic_json, digest, file_hash, read_json, run_lock
from . import resources


class ApiReview(Exception):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic['code'] + ': API request needs L1 review; no automatic retry')


def load(root):
    path = Path(root) / 'api-review.json'
    if not path.exists():
        return {'events': [], 'timeout_seconds': 120, 'pending': None, 'grants': []}
    value = read_json(path)
    material = {k: value[k] for k in ('events', 'timeout_seconds', 'pending', 'grants')}
    if value.get('sha256') != digest(material):
        raise ValueError('API review journal changed')
    previous = None
    for entry in value['events']:
        if entry['record']['previous'] != previous or entry['sha256'] != digest(entry['record']):
            raise ValueError('API review history changed')
        previous = entry['sha256']
    return value


def save(root, journal, kind, details):
    record = {'at': time.time(), 'kind': kind, 'details': details,
              'previous': journal['events'][-1]['sha256'] if journal['events'] else None}
    journal['events'].append({'record': record, 'sha256': digest(record)})
    journal.pop('sha256', None)
    journal['sha256'] = digest(journal)
    atomic_json(Path(root) / 'api-review.json', journal)


def ledger(root):
    path = Path(root) / 'api/ledger.json'
    return read_json(path) if path.exists() else {'attempts': []}


def unresolved(root, value=None):
    journal = load(root)
    grants = {g['attempt_sha256'] for g in journal['grants']}
    return [a for a in (value or ledger(root))['attempts']
            if a['status'] != 'COMPLETED' and digest(a) not in grants]


def report(root):
    value, journal = ledger(root), load(root)
    waiting = unresolved(root, value)
    return {'state': 'API_AUTHORIZATION_PENDING' if journal['pending'] else
            ('API_REVIEW_PENDING' if waiting else 'READY'),
            'timeout_seconds': journal['timeout_seconds'], 'attempts': value['attempts'],
            'historically_unknown_requests': [a['id'] for a in value['attempts']
                if a['status'] != 'COMPLETED' and a.get('diagnostic', {}).get('outcome', 'UNKNOWN') == 'UNKNOWN'],
            'unresolved': waiting, 'pending': journal['pending'], 'history': journal['events'],
            'usage_note': 'Unreported usage remains unknown, including user-authorized replacement requests.'}


def check_task(root):
    root = Path(root)
    state_path = root / 'run/state.json'
    state = read_json(state_path) if state_path.exists() else None
    if ((root / 'preflight-report.json').exists() or (root / 'run/final-report.json').exists()
            or (state and (state.get('report') or state.get('active_run')
                           or state.get('state') in ('BLOCKED', 'DELIVERY_READY', 'REPORT_PENDING')))):
        raise ValueError('API review cannot reopen a final report or reconcile a product worker')
    return state


def snapshot(root, original, project):
    from .onboarding import fingerprint
    root = Path(root)
    files = {}
    for name in ('api/ledger.json', 'resources.json', 'planning-status.json', 'run/state.json',
                 'requirements.md', 'plan.json'):
        path = root / name
        files[name] = file_hash(path) if path.exists() else None
    for attempt in ledger(root)['attempts']:
        identity = attempt.get('id', '')
        if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid API attempt identity')
        for suffix in ('request', 'response'):
            path = root / 'api' / (identity + '-' + suffix + '.json')
            files[path.name] = file_hash(path) if path.exists() else None
    return digest({'files': files, 'original': original, 'project': fingerprint(project)})


def check_budget(root, original, timeout, *, retry):
    current = resources.effective(original, root)
    if type(timeout) is not int or not 1 <= timeout <= current['limits']['timeout_seconds']:
        raise ValueError('API wait must fit the current authorized per-operation timeout_seconds')
    value = ledger(root)
    elapsed = max(0, time.time() - value.get('started_at', time.time()))
    remaining = min(current['limits']['wall_time_seconds'] - elapsed,
                    datetime.fromisoformat(current['expires_at']).timestamp() - time.time())
    if remaining <= 0:
        raise resources.BudgetReview('wall_time_seconds', elapsed, 1,
            current['limits']['wall_time_seconds'], resources.authority(original, root)['limits']['wall_time_seconds'])
    if retry:
        resources.require(original, root, 'api_calls', len(value['attempts']))
        if len(value['attempts']) >= current['limits']['api_calls']:
            raise ValueError('API request budget exhausted; no retry authorized')
    return current


def review(root, original, proposal, actor, project):
    root = Path(root)
    with run_lock(root / 'run') if (root / 'run').exists() else nullcontext():
        check_task(root)
        if (not isinstance(proposal, dict) or set(proposal) != {
                'action', 'timeout_seconds', 'reason', 'evidence', 'remaining_work', 'expected_benefit'}
                or proposal['action'] not in ('configure', 'retry')
                or any(not isinstance(proposal[k], str) or not proposal[k].strip()
                       for k in ('reason', 'evidence', 'remaining_work', 'expected_benefit'))):
            raise ValueError('API review requires action, timeout_seconds, reason, evidence, remaining_work and expected_benefit')
        if not resources.enabled(original, root):
            raise ValueError('API adjustments require an explicitly adaptive task')
        journal = load(root)
        superseded = journal['pending']['sha256'] if journal['pending'] else None
        retry = proposal['action'] == 'retry'
        check_budget(root, original, proposal['timeout_seconds'], retry=retry)
        request = {'actor': actor, 'proposal': proposal, 'before_timeout_seconds': journal['timeout_seconds'],
                   'supersedes': superseded}
        journal['pending'] = None
        if not retry:
            journal['timeout_seconds'] = proposal['timeout_seconds']
            save(root, journal, 'l1_api_wait_adjusted', request)
            return {'accepted': True, 'next': 'Review any unresolved request before invoking DOT'}
        waiting = unresolved(root)
        if len(waiting) != 1:
            raise ValueError('Exactly one unresolved API attempt is required')
        attempt = waiting[0]
        error = attempt.get('diagnostic', {})
        if attempt['status'] != 'FAILED_OR_UNKNOWN' or error.get('version') != 1:
            raise ValueError('Legacy or in-flight UNKNOWN lacks verified API-only diagnostics; no automatic recovery')
        request.update(attempt_sha256=digest(attempt), attempt_id=attempt['id'], diagnostic=error,
                       snapshot=snapshot(root, original, project), usage=resources.usage(root, check_task(root)))
        request['sha256'] = digest(request)
        if error.get('outcome') in ('NOT_SENT', 'REJECTED', 'RESPONSE_RECEIVED'):
            journal['timeout_seconds'] = proposal['timeout_seconds']
            journal['grants'].append({'attempt_sha256': digest(attempt), 'authority': 'delegated_l1',
                                      'request_sha256': request['sha256']})
            save(root, journal, 'l1_api_retry_authorized', request)
            return {'accepted': True, 'next': 'Next request remains budgeted; no API call was made by this review'}
        journal['pending'] = request
        save(root, journal, 'api_retry_user_decision_requested', request)
        return {'accepted': False, 'state': 'API_AUTHORIZATION_PENDING',
                'next': 'workflow_review_api_retry: old result and charges remain unknown; native approval required'}


def decide(root, original, project, sha256, approved, session):
    root = Path(root)
    with run_lock(root / 'run') if (root / 'run').exists() else nullcontext():
        check_task(root)
        journal = load(root)
        request = journal['pending']
        if (type(approved) is not bool or not session or not request or request['sha256'] != sha256
                or request['sha256'] != digest({k: v for k, v in request.items() if k != 'sha256'})
                or (approved and request['snapshot'] != snapshot(root, original, project))):
            raise ValueError('API decision is missing, stale or changed; review a fresh snapshot')
        if approved:
            check_budget(root, original, request['proposal']['timeout_seconds'], retry=True)
            journal['timeout_seconds'] = request['proposal']['timeout_seconds']
            journal['grants'].append({'attempt_sha256': request['attempt_sha256'],
                'authority': 'local_harness_user', 'session': session, 'request_sha256': sha256})
        journal['pending'] = None
        save(root, journal, 'user_api_retry_approved' if approved else 'user_api_retry_declined',
             {'request': request, 'session': session, 'old_outcome_unchanged': True})
        return {'accepted': approved, 'next': 'L1 may invoke the next bounded request' if approved else
                'No retry allowed; L1 may revise its assessment or submit the final report'}

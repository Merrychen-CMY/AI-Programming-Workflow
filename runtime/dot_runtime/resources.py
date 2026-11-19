"""Versioned allocations and native-user amendments; never reset observed usage.

This is a local audit journal, not authentication against same-account code.
Only the private host confirmation path calls decide_authorization.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

LIMITS = ('implementation_attempts', 'same_root_cause_attempts', 'infrastructure_retries',
          'timeout_seconds', 'l1_reviews', 'revisions', 'api_calls',
          'api_max_output_tokens', 'wall_time_seconds')
AMENDABLE = {'limits', 'expires_at', 'allowed_paths', 'hard_constraints', 'authorized_context'}


class BudgetReview(Exception):
    def __init__(self, key, used, requested, current, ceiling, reason='Current allocation needs L1 evaluation'):
        super().__init__(reason)
        self.details = dict(key=key, used=used, requested=requested, current=current, ceiling=ceiling, reason=reason)


def enabled(original, root):
    return isinstance(original, dict) and original.get('adaptive_limits') is True and root is not None


def l1_managed(original, root):
    """New-task delegation or an explicitly reviewed runtime migration, never a model assertion."""
    if not enabled(original, root):
        return False
    if original.get('resource_authority') == 'delegated_l1':
        return True
    from .upgrades import records_for_control
    return any(item.get('resource_authority') == 'delegated_l1' for item in records_for_control(root))


def read(root, original):
    from .controller import digest, read_json
    path = Path(root) / 'resources.json'
    if not path.exists():
        return {'original_sha256': digest(original), 'events': [], 'current': None,
                'amendments': {}, 'pending': None, 'pause': None}
    value = read_json(path)
    if value['original_sha256'] != digest(original):
        raise ValueError('Original authorization changed; resource journal cannot be rebound')
    previous = None
    for entry in value['events']:
        record = entry['record']
        if record['previous'] != previous or entry['sha256'] != digest(record):
            raise ValueError('Resource history changed')
        previous = entry['sha256']
    material = {k: value[k] for k in ('original_sha256', 'events', 'current', 'amendments', 'pending', 'pause')}
    if value.get('sha256') != digest(material):
        raise ValueError('Resource state changed')
    return value


def save(root, value, kind, payload):
    from .controller import atomic_json, digest, now
    record = {'at': now(), 'kind': kind, 'payload': payload,
              'previous': value['events'][-1]['sha256'] if value['events'] else None}
    value['events'].append({'record': record, 'sha256': digest(record)})
    value.pop('sha256', None)
    value['sha256'] = digest(value)
    atomic_json(Path(root) / 'resources.json', value)


def authority(original, root):
    if not enabled(original, root):
        return original
    value = copy.deepcopy(original)
    value.update(read(root, original)['amendments'])
    return value


def scope_digest(original, root):
    from .controller import digest
    value = authority(original, root)
    return digest({key: value.get(key) for key in ('allowed_paths', 'hard_constraints', 'authorized_context')})


def effective(original, root, *, require_allocation=True, enforce_time=True):
    if not enabled(original, root):
        return original
    value = authority(original, root)
    journal = read(root, original)
    ceiling = value['limits']
    if enforce_time and datetime.fromisoformat(value['expires_at']).timestamp() <= time.time():
        raise BudgetReview('expires_at', value['expires_at'], 'extend', value['expires_at'], value['expires_at'],
                           'Authorization deadline reached; request explicit user extension')
    if journal['current'] is None:
        if require_allocation:
            raise BudgetReview('allocation', 0, 1, 0, ceiling, 'L1 must allocate finite operating limits under the recorded resource authority')
    else:
        value['limits'] = journal['current']
    return value


def usage(root, state=None):
    from .controller import read_json
    state = state or {}
    ledger = Path(root) / 'api/ledger.json'
    api = read_json(ledger) if ledger.exists() else {'attempts': []}
    from .requirements_review import review_count, revision_count
    return {'implementation_attempts': state.get('implementation_attempts', 0),
            'infrastructure_retries': state.get('infrastructure_retries', 0),
            'same_root_cause_attempts': max(state.get('root_cause_counts', {}).values(), default=0),
            'l1_reviews': len(state['l1_reviews']) if 'l1_reviews' in state else review_count(root),
            'revisions': state.get('revision_count', revision_count(root)),
            'api_calls': len(api['attempts']),
            'wall_time_seconds': max(0, time.time() - api['started_at']) if 'started_at' in api else 0}


def require(original, root, key, used, requested=1):
    value = effective(original, root)
    if enabled(original, root) and used + requested > value['limits'][key]:
        raise BudgetReview(key, used, requested, value['limits'][key], authority(original, root)['limits'][key])
    return value['limits'][key]


def pause(root, original, exc, state=None):
    journal = read(root, original)
    request = {**exc.details, 'usage': usage(root, state),
               'remaining_tasks': [t['id'] for t in state.get('approved_plan', {}).get('tasks', [])
                                   if t['id'] not in state.get('completed_tasks', [])] if state else [],
               'failure_evidence': state.get('issues', [])[-1:] if state else [],
               'next': 'L1 evaluates evidence and adjusts allocation, requests user authorization, or submits final report'}
    if exc.details['key'] in LIMITS:
        request['ceiling'] = authority(original, root)['limits'][exc.details['key']]
    journal['pause'] = request
    save(root, journal, 'budget_review', request)
    return request


def resolve_planning_pause(root, original, requirements, plan, state=None):
    """A validated smaller plan can resolve dispatch demand without raising caps."""
    from .controller import digest, pending_dispatch_count
    journal = read(root, original)
    checkpoint = journal['pause']
    if not checkpoint or checkpoint['key'] != 'implementation_attempts' or journal['pending']:
        return False
    used = state.get('implementation_attempts', 0) if state else 0
    remaining = pending_dispatch_count(state, requirements, plan)
    require(original, root, 'implementation_attempts', used, remaining)
    journal['pause'] = None
    save(root, journal, 'l1_replanned_within_allocation', {
        'checkpoint': checkpoint, 'plan_sha256': digest(plan),
        'requirements_sha256': digest(requirements), 'remaining_dispatches': remaining,
        'observed_usage': usage(root, state), 'limits': journal['current']})
    return True


def _numbers(value):
    if not isinstance(value, dict) or set(value) != set(LIMITS):
        raise ValueError('Supply exactly the nine operating limit fields')
    for key, number in value.items():
        minimum = 0 if key in ('revisions', 'infrastructure_retries') else 1
        if type(number) is not int or number < minimum:
            raise ValueError('Invalid resource limit: ' + key)


def _text(value, keys):
    for key in keys:
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError('Required explanation: ' + key)


def adjust(root, original, proposal, actor, state=None):
    if not enabled(original, root):
        raise ValueError('This task did not authorize adaptive limits')
    if set(proposal) != {'limits', 'reason', 'evidence', 'remaining_work', 'expected_benefit', 'strategy'}:
        raise ValueError('Allocation requires limits, reason, evidence, remaining_work, expected_benefit and strategy')
    _text(proposal, ('reason', 'evidence', 'remaining_work', 'expected_benefit', 'strategy'))
    _numbers(proposal['limits'])
    journal = read(root, original)
    previous_request = journal['pending']
    if previous_request and not (l1_managed(original, root)
                                 and set(previous_request['proposal']['changes']) == {'limits'}):
        raise ValueError('Resolve the pending human authorization before allocating resources')
    maximum = authority(original, root)
    if datetime.fromisoformat(maximum['expires_at']).timestamp() <= time.time():
        raise ValueError('User deadline extension is required before allocating resources')
    observed = usage(root, state)
    for key, amount in proposal['limits'].items():
        if amount > maximum['limits'][key] and not l1_managed(original, root):
            raise ValueError('Allocation exceeds user ceiling: ' + key)
        if amount < observed.get(key, 0):
            raise ValueError('Allocation is below actual cumulative usage: ' + key)
    paused = journal['pause']
    if paused and paused['key'] in LIMITS and proposal['limits'][paused['key']] < paused['used'] + paused['requested']:
        raise ValueError('Proposed allocation does not resolve the recorded budget checkpoint')
    before = journal['current']
    if l1_managed(original, root):
        # Limits remain finite, but L1 owns their approval. Original inputs and actual usage are immutable.
        journal['amendments']['limits'] = {key: max(maximum['limits'][key], amount)
                                          for key, amount in proposal['limits'].items()}
    journal['current'], journal['pause'], journal['pending'] = copy.deepcopy(proposal['limits']), None, None
    save(root, journal, 'l1_allocation', {'actor': actor, 'authority': 'delegated_l1',
                                       'before': before, 'before_ceiling': maximum['limits'],
                                       'after_ceiling': journal['amendments'].get('limits', original['limits']), 'after': proposal,
                                       'superseded_resource_request': previous_request,
                                       'observed_usage': observed})
    return {'accepted': True, 'limits': journal['current'], 'usage': observed}


def propose_authorization(root, original, proposal, actor, state=None):
    from .controller import digest
    from .delegation import validate_delegation
    if not enabled(original, root):
        raise ValueError('This task did not authorize versioned amendments')
    if set(proposal) != {'changes', 'reason', 'evidence', 'remaining_work', 'expected_benefit', 'risk', 'resume_plan'}:
        raise ValueError('Authorization requires changes, reason, evidence, remaining_work, expected_benefit, risk and resume_plan')
    _text(proposal, ('reason', 'evidence', 'remaining_work', 'expected_benefit', 'risk', 'resume_plan'))
    changes = proposal['changes']
    if not isinstance(changes, dict) or not changes or not set(changes) <= AMENDABLE:
        raise ValueError('Cannot rewrite history, clear UNKNOWN, change credentials or invent unsupported execution permissions')
    if l1_managed(original, root) and 'limits' in changes:
        if set(changes) != {'limits'}:
            raise ValueError('Separate resource adjustment from scope/deadline authorization; use l1_adjust_limits for resources')
        return adjust(root, original, {'limits': changes['limits'], 'reason': proposal['reason'],
            'evidence': proposal['evidence'], 'remaining_work': proposal['remaining_work'],
            'expected_benefit': proposal['expected_benefit'], 'strategy': proposal['resume_plan']}, actor, state)
    current = authority(original, root)
    new = copy.deepcopy(current)
    new.update(changes)
    validate_delegation(new)
    if 'limits' in changes:
        _numbers(changes['limits'])
        for key, used in usage(root, state).items():
            if changes['limits'][key] < used:
                raise ValueError('New ceiling cannot erase cumulative usage: ' + key)
    if 'authorized_context' in changes and (not isinstance(changes['authorized_context'], str) or not changes['authorized_context'].strip()):
        raise ValueError('Record the exact new goals/acceptance requirements as authorized_context')
    journal = read(root, original)
    if journal['pending']:
        raise ValueError('An authorization request is already awaiting a user decision')
    request = {'id': uuid.uuid4().hex, 'actor': actor, 'proposal': proposal,
               'before': {k: current.get(k) for k in changes}, 'usage': usage(root, state),
               'state_sha256': digest(state), 'base_authority_sha256': digest(current)}
    request['sha256'] = digest(request)
    journal['pending'] = request
    save(root, journal, 'authorization_requested', request)
    return request


def decide_authorization(root, original, request_sha, approved, session, state=None):
    """Private host-only call. No public model tool accepts approved or user identity."""
    from .controller import digest
    journal = read(root, original)
    request = journal['pending']
    if (not request or request['sha256'] != request_sha or request['state_sha256'] != digest(state)
            or request['base_authority_sha256'] != digest(authority(original, root))):
        raise ValueError('Authorization request or task state changed during human review')
    if type(approved) is not bool or not session:
        raise ValueError('Native user decision and host session are required')
    if approved:
        from .delegation import validate_delegation
        candidate = copy.deepcopy(authority(original, root))
        candidate.update(request['proposal']['changes'])
        validate_delegation(candidate)
        journal['amendments'].update(copy.deepcopy(request['proposal']['changes']))
        if journal['current'] is not None:
            journal['current'] = {k: min(v, candidate['limits'][k]) for k, v in journal['current'].items()}
    journal['pending'] = None
    save(root, journal, 'user_authorization_approved' if approved else 'user_authorization_declined',
         {'request': request, 'authority': 'local_harness_user', 'session': session})
    return {'accepted': approved, 'request_sha256': request_sha,
            'next': 'L1 allocates resources and reviews affected baselines' if approved else 'L1 submits final report or a different proposal'}


def controller_context(state):
    from .controller import read_json
    if not state.get('delegation'):
        return None, None
    original = read_json(state['delegation']['path'])
    return original, state.get('clarification_root')


def controller_limits(state):
    original, root = controller_context(state)
    if original and enabled(original, root):
        return effective(original, root, enforce_time=False)['limits']
    return state['approved_plan']['budgets']


def check_controller(state, key, used, requested=1):
    original, root = controller_context(state)
    if original and enabled(original, root):
        require(original, root, key, used, requested)


def report(root, original, state=None):
    from .controller import read_json
    journal = read(root, original)
    ledger = Path(root) / 'api/ledger.json'
    attempts = read_json(ledger)['attempts'] if ledger.exists() else []
    token_usage = {'reported_requests': 0, 'unreported_requests': 0,
                   'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
    for attempt in attempts:
        supplied = attempt.get('usage')
        if isinstance(supplied, dict) and all(type(supplied.get(k)) is int for k in ('input_tokens', 'output_tokens', 'total_tokens')):
            token_usage['reported_requests'] += 1
            for key in ('input_tokens', 'output_tokens', 'total_tokens'):
                token_usage[key] += supplied[key]
        else:
            token_usage['unreported_requests'] += 1
    return {'resource_authority': 'delegated_l1' if l1_managed(original, root) else 'legacy_user_ceiling',
            'user_authority': authority(original, root), 'operating_limits': journal['current'],
            'actual_usage': usage(root, state), 'history': journal['events'],
            'dot_api_reported_usage': token_usage,
            'pending_authorization': journal['pending'], 'budget_review': journal['pause'],
            'cost_accounting': 'Counts, elapsed time and reported DOT API tokens; unreported usage is unknown. '
                               'No unified L1/Codex/DOT token or monetary hard cap.'}

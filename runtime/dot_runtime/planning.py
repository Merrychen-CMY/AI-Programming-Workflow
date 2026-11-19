"""Known, completed plan refusals; never classifies transport or worker failures."""
from pathlib import Path
import re
import uuid


def handoff(root, original, approval=None, state=None):
    """Controller-validated current receipts, not L1-transcribed new facts."""
    from . import resources
    from .controller import digest
    journal = resources.read(root, original)
    approved = []
    for event in journal['events']:
        record = event['record']
        if record['kind'] == 'user_authorization_approved':
            payload = record['payload']
            request = payload['request']
            approved.append({'at': record['at'], 'event_sha256': event['sha256'],
                'request_sha256': request['sha256'], 'authority': payload['authority'],
                'session': payload['session'], 'changes': request['proposal']['changes']})
    material = {'requirements_approval': approval,
        'original_authorization_sha256': digest(original),
        'current_user_authority': resources.authority(original, root),
        'current_operating_limits': journal['current'],
        'approved_authorization_amendments': approved}
    return {**material, 'handoff_sha256': digest(material),
        'actual_usage': resources.usage(root, state),
        'records': {'approval': 'requirements-review/state.json', 'resources': 'resources.json'},
        'interpretation': (
            'Controller-validated receipts. The current requirements approval binds the current facts; '
            'historical pending-review labels are not the current approval status. '
            'Approved native amendments supersede original values only for their explicit fields; '
            'old numeric limits in original prose remain history for those amended limits. '
            'Unamended goals, AC and permissions remain binding. Proposed or declined changes grant nothing. '
            'Do not request transcription of these existing receipts as new facts or a new review. '
            'Actual new facts or changed scope still require the normal review path.')}


class PlanRejected(ValueError):
    def __init__(self, reason, *, code='PLAN_VALIDATION', details=None, proposal=None, response_id=None):
        super().__init__(reason)
        self.code, self.details = code, details or {}
        self.proposal, self.response_id = proposal, response_id


def validate_proposal(plan, delegation, implementation_used=0, *, pending_dispatches=None):
    from .controller import validate_plan
    from .delegation import check_plan_bounds, validate_delegation
    # Authority expiry is not a repairable draft error.
    validate_delegation(delegation)
    try:
        tasks = validate_plan(plan)
        check_plan_bounds(plan, delegation)
        pending = len(tasks) if pending_dispatches is None else pending_dispatches
        if type(pending) is not int or not 0 <= pending <= len(tasks):
            raise ValueError('Pending dispatch count must be derived from the controller completed-task comparison')
        if implementation_used + pending > plan['budgets']['implementation_attempts']:
            raise PlanRejected('Remaining implementation budget cannot cover the revised initial dispatches',
                code='IMPLEMENTATION_DISPATCH_BUDGET', details={
                    'implementation_used': implementation_used, 'initial_task_dispatches': pending,
                    'retained_completed_tasks': len(tasks) - pending,
                    'implementation_limit': plan['budgets']['implementation_attempts']})
    except PlanRejected as exc:
        exc.proposal = plan
        if exc.code == 'IMPLEMENTATION_DISPATCH_BUDGET':
            exc.details['implementation_used'] = implementation_used
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        # Only pure validation is enclosed here, never writes, transport or dispatch.
        raise PlanRejected(str(exc), proposal=plan) from exc
    return plan


def record_rejection(root, operation, rejection, request_sha=None, base_sha=None):
    from .controller import atomic_json, digest, now
    root = Path(root)
    directory = root / 'planning-rejections'
    directory.mkdir(exist_ok=True)
    record = {'at': now(), 'operation': operation, 'code': rejection.code,
              'reason': str(rejection), 'details': rejection.details,
              'proposal': rejection.proposal, 'response_id': rejection.response_id,
              'product_dispatch_started': False}
    reference = uuid.uuid4().hex + '.json'
    atomic_json(directory / reference, {'record': record, 'sha256': digest(record)})
    atomic_json(root / 'planning-status.json', {'status': 'PLAN_REJECTED',
        'code': rejection.code, 'reason': str(rejection), 'details': rejection.details,
        'rejection_record': reference, 'request_sha256': request_sha, 'base_request_sha256': base_sha})


def read_rejection(root, name):
    from .controller import digest, read_json
    if not isinstance(name, str) or not re.fullmatch(r'[0-9a-f]{32}\.json', name):
        raise ValueError('Invalid planning rejection reference')
    value = read_json(Path(root) / 'planning-rejections' / name)
    if digest(value['record']) != value['sha256']:
        raise ValueError('Planning rejection history changed')
    return value['record']


def feedback(root, status):
    if status.get('status') != 'PLAN_REJECTED':
        return None
    record = read_rejection(root, status['rejection_record'])
    # Stable across repeated identical model output; identifiers/timestamps cannot enable retries.
    return {key: record[key] for key in ('code', 'reason', 'details', 'proposal')}


def history(root):
    directory = Path(root) / 'planning-rejections'
    return sorted((read_rejection(root, p.name) for p in directory.glob('*.json')), key=lambda r: r['at'])

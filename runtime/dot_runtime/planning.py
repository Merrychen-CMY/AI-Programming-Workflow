"""Known, completed plan refusals; never classifies transport or worker failures."""
from pathlib import Path
import re
import uuid


class PlanRejected(ValueError):
    def __init__(self, reason, *, code='PLAN_VALIDATION', details=None, proposal=None, response_id=None):
        super().__init__(reason)
        self.code, self.details = code, details or {}
        self.proposal, self.response_id = proposal, response_id


def validate_proposal(plan, delegation, implementation_used=0):
    from .controller import validate_plan
    from .delegation import check_plan_bounds, validate_delegation
    # Authority expiry is not a repairable draft error.
    validate_delegation(delegation)
    try:
        tasks = validate_plan(plan)
        check_plan_bounds(plan, delegation)
        if implementation_used + len(tasks) > plan['budgets']['implementation_attempts']:
            raise PlanRejected('Remaining implementation budget cannot cover the revised initial dispatches',
                code='IMPLEMENTATION_DISPATCH_BUDGET', details={
                    'implementation_used': implementation_used, 'initial_task_dispatches': len(tasks),
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

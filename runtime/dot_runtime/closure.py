"""Explicit L1 closure assessment; no model text is a human authorization."""
import json
from pathlib import Path, PurePosixPath

BASES = ('no_feasible_solution', 'user_requested_stop', 'unadjustable_total_limit')

def validate_assessment(value, root=None, *, state=None, original=None):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError('L1 closure requires a JSON assessment in reason; use templates/l1-closure.example.json') from exc
    fields = {'summary', 'solutions_considered', 'authority_review', 'resource_review', 'termination_basis', 'evidence'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('L1 closure assessment fields are incomplete')
    if value['termination_basis'] not in BASES:
        raise ValueError('L1 must establish no feasible solution, user stop, or an unadjustable total limit')
    for key in ('summary', 'authority_review', 'resource_review'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError('L1 assessment requires ' + key)
    for key in ('solutions_considered', 'evidence'):
        if not isinstance(value[key], list) or not value[key] or any(not isinstance(x, str) or not x.strip() for x in value[key]):
            raise ValueError('L1 assessment requires nonempty ' + key)
    if root is not None:
        records = evidence_records(value, root)
        if value['termination_basis'] == 'user_requested_stop':
            confirmed = False
            for record in records:
                if record['path'] == 'user-stop.json':
                    try:
                        from .controller import read_json
                        from .user_stop import validate_receipt
                        validate_receipt(root, read_json(Path(root) / record['path']))
                        confirmed = True
                    except (ValueError, OSError):
                        pass
            if not confirmed:
                raise ValueError('User stop requires an actual native user stop record; model assertions are not authorization')
        if value['termination_basis'] == 'unadjustable_total_limit':
            _require_total_limit(root, state, original)
    return value


def _require_total_limit(root, state, original):
    """Check objective exhaustion; L1 still owns the judgement about alternatives."""
    from datetime import datetime
    import time
    from . import api_recovery, resources
    from .controller import read_json
    state = state or {}
    if original is None and state.get('delegation'):
        original = read_json(state['delegation']['path'])
    journal = resources.read(root, original) if original is not None else None
    if (journal and journal['pending']) or api_recovery.load(root)['pending']:
        raise ValueError('Resolve pending user authorization before concluding the total limit is unadjustable')
    maximum = resources.authority(original, root) if original is not None else None
    if maximum and datetime.fromisoformat(maximum['expires_at']).timestamp() <= time.time():
        return
    ceilings = maximum['limits'] if maximum else state.get('approved_plan', {}).get('budgets', {})
    observed = resources.usage(root, state)
    pause = (journal or {}).get('pause') or {}
    for key, used in observed.items():
        ceiling = ceilings.get(key)
        # A configured zero retry allowance alone is not evidence of exhaustion.
        # It counts only at an actual recorded request for that unavailable retry.
        requested_zero = (ceiling == 0 and pause.get('key') == key
                          and pause.get('requested', 0) > 0)
        if ceiling is not None and used >= ceiling and (used > 0 or requested_zero):
            return
    raise ValueError('Unadjustable total limit requires an actually reached user ceiling or deadline; current allocation exhaustion is insufficient')


def evidence_records(value, root):
    """Bind a closure to real task records, without claiming their conclusions are true."""
    from .controller import file_hash
    from .snapshot import _filesystem_path
    base = Path(root).absolute()
    disk_base = _filesystem_path(base).resolve(strict=True)
    result = []
    for name in value['evidence']:
        relative = PurePosixPath(name)
        if ('\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts
                or not relative.parts or relative.suffix not in ('.json', '.md', '.txt', '.log', '.jsonl')):
            raise ValueError('Closure evidence must name real task-relative records')
        path = base
        for part in relative.parts:
            path /= part
            disk = _filesystem_path(path)
            if disk.is_symlink() or (hasattr(disk, 'is_junction') and disk.is_junction()):
                raise ValueError('Closure evidence links are forbidden')
        disk = _filesystem_path(path)
        if not disk.is_file() or not disk.resolve().is_relative_to(disk_base):
            raise ValueError('Closure evidence record does not exist: ' + name)
        result.append({'path': name, 'sha256': file_hash(path)})
    return result

L2_GUIDANCE = (
    'First reason about feasible solutions within existing authority from the available evidence. '
    'State observed facts, plausible causes, options considered, expected benefit, verification steps, '
    'required resources and exact permission gaps in structured solutions, investigation_steps, '
    'missing_information, permission_gaps and recommended_action fields. Implementations remain assigned to L3, '
    'facilities to L4; do not edit products during triage. If evidence is insufficient, propose bounded '
    'investigation rather than inventing a diagnosis. Escalate the proposal to L1 when authority or baseline '
    'changes are needed. You may pause or recommend termination, but may never finalize failure. '
    'L1 must explicitly assess every non-success closure; lack of diagnosis alone is not a termination basis. '
)

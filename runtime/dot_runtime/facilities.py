"""L4 executes only L2-selected, baseline-approved local facility actions.

Commands are reviewed argv, not a shell or a general device/network executor.
Their working directory is a control-owned facility area, never the product.
Like the approved check runner, this is not an OS security boundary against
malicious authorized code; reviewers must inspect the command and its inputs.
"""
from pathlib import Path
import re

from .snapshot import _root


def validate_settings(settings):
    if settings is None:
        return
    if not isinstance(settings, dict) or set(settings) != {'actions'}:
        raise ValueError('facility_recovery requires an actions list')
    actions = settings['actions']
    if not isinstance(actions, list) or not actions or len(actions) > 10:
        raise ValueError('facility_recovery needs one to ten bounded actions')
    ids = set()
    for action in actions:
        fields = {'id', 'kind', 'description'}
        if isinstance(action, dict) and action.get('kind') == 'command':
            fields.add('command')
        if not isinstance(action, dict) or set(action) != fields:
            raise ValueError('Malformed facility action')
        if (not isinstance(action['id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', action['id'])
                or action['id'] in ids):
            raise ValueError('Facility action IDs must be unique identifiers')
        ids.add(action['id'])
        if (action['kind'] not in ('retry', 'command') or not isinstance(action['description'], str)
                or not action['description'].strip()):
            raise ValueError('Facility actions require a concrete purpose and supported kind')
        if action['kind'] == 'command':
            argv = action['command']
            if (not isinstance(argv, list) or not argv
                    or any(not isinstance(arg, str) or not arg.strip() or '\x00' in arg for arg in argv)):
                raise ValueError('Facility command requires a nonempty reviewed argv')


def selected_actions(plan, analysis):
    settings = plan.get('verification', {}).get('facility_recovery')
    validate_settings(settings)
    selected = analysis.get('facility_actions', [])
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(name, str) for name in selected) or len(selected) != len(set(selected))):
        raise ValueError('L2 must select explicit approved facility actions; a bare retry is insufficient')
    available = {action['id']: action for action in (settings or {}).get('actions', [])}
    if any(name not in available for name in selected):
        raise ValueError('Facility proposal needs L1 baseline review: action is not in the approved plan')
    return [available[name] for name in selected]


def command_for(action, candidate, facility):
    """Resolve only controller-owned roots; neither accepts model-supplied paths."""
    candidate = _root(Path(candidate), exists=True)
    facility = _root(Path(facility), exists=True)
    if candidate.is_relative_to(facility) or facility.is_relative_to(candidate):
        raise ValueError('Facility work area must be disjoint from candidate source')
    return [arg.replace('{candidate}', str(candidate)).replace('{facility}', str(facility))
            for arg in action['command']]

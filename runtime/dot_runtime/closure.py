"""Explicit L1 closure assessment; no model text is a human authorization."""
import json

BASES = ('no_feasible_solution', 'user_requested_stop', 'unadjustable_total_limit')

def validate_assessment(value):
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
    return value

L2_GUIDANCE = (
    'First reason about feasible solutions within existing authority from the available evidence. '
    'State observed facts, plausible causes, options considered, expected benefit, verification steps, '
    'required resources and exact permission gaps in summary/findings. Implementations remain assigned to L3, '
    'facilities to L4; do not edit products during triage. If evidence is insufficient, propose bounded '
    'investigation rather than inventing a diagnosis. Escalate the proposal to L1 when authority or baseline '
    'changes are needed. You may pause or recommend termination, but may never finalize failure. '
    'L1 must explicitly assess every non-success closure; lack of diagnosis alone is not a termination basis. '
)

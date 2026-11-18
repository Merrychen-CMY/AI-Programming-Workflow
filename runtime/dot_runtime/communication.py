"""Grice-inspired communication guidance; not a truth checker or approval channel."""
import hashlib
import json
from pathlib import Path

POLICY = Path(__file__).resolve().parents[2] / 'policy/communication.json'
MAXIMS = ['quantity', 'quality', 'relation', 'manner']
BOUNDARIES = {
    'evidence_and_hard_constraints_before_brevity': True,
    'preserve_all_failures_and_attempts': True,
    'silence_or_acknowledgment_is_approval': False,
    'implicit_intent_grants_authority': False,
    'cooperation_means_agreement': False,
    'delta_requires_accessible_matching_baseline': True,
    'reference_alone_proves_recipient_access': False,
    'stateless_api_may_assume_prior_context': False,
    'critical_missing_facts_block_dependent_work': True,
    'unrelated_authorized_work_may_continue': True,
    'extra_user_forms_or_checkpoints': False,
    'extra_confirmation_round_required': False,
    'changes_to_tool_schema_or_state_machine': False,
    'all_message_semantics_automatically_verified': False,
}


def validate_communication_policy(value):
    """Validate policy structure and boundaries, never natural-language message truth."""
    if not isinstance(value, dict):
        return ['Communication policy must be an object']
    errors = []
    for name, expected in [('schema_version', 1), ('revision', 1),
                           ('scope', 'L1_DOT_communication_and_supporting_worker_reports')]:
        if type(value.get(name)) is not type(expected) or value.get(name) != expected:
            errors.append('Communication policy identity mismatch: ' + name)
    rows = value.get('maxims')
    if not isinstance(rows, list) or len(rows) != 4 or any(not isinstance(r, dict) for r in rows):
        return errors + ['Communication policy requires four distinct maxims']
    if [r.get('name') for r in rows] != MAXIMS:
        errors.append('Communication maxims must cover quantity, quality, relation and manner')
    for number, row in enumerate(rows, 1):
        if row.get('id') != f'COMM-{number:02}' or row.get('level') != 'MUST':
            errors.append('Communication maxim identity or strength changed')
        for field in ('title', 'instruction'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append('Communication maxim lacks ' + field)
        if not _text_list(row.get('checks')):
            errors.append('Communication maxim lacks review criteria')
    boundaries = value.get('boundaries')
    if not isinstance(boundaries, dict):
        errors.append('Communication boundaries must be explicit')
    else:
        for key, expected in BOUNDARIES.items():
            if boundaries.get(key) is not expected:
                errors.append('Communication boundary changed: ' + key)
    guide = value.get('message_guide')
    if not isinstance(guide, dict):
        errors.append('Communication message guide is missing')
    else:
        for key, expected in {'guide_is_new_wire_schema': False, 'use_existing_tool_fields': True,
                              'purpose_label_grants_authority': False,
                              'decision_requires_existing_approval_receipt': True}.items():
            if guide.get(key) is not expected:
                errors.append('Communication guide boundary changed: ' + key)
        if 'fixed_word_limit' not in guide or guide['fixed_word_limit'] is not None:
            errors.append('Brevity must not impose a fixed length that drops required content')
        for key in ('always', 'when_applicable', 'purposes'):
            if not _text_list(guide.get(key)):
                errors.append('Communication guide is missing ' + key)
    if not _text_list(value.get('operating_rules')):
        errors.append('Communication operating rules are missing')
    source = value.get('source', {})
    if not isinstance(source, dict) or source.get('measured_efficiency_gain_claimed') is not False:
        errors.append('Communication design must not claim measured efficiency gains')
    evaluation = value.get('evaluation', {})
    if (not isinstance(evaluation, dict) or evaluation.get('status') != 'NOT_MEASURED'
            or evaluation.get('speed_or_tokens_alone_define_success') is not False
            or evaluation.get('new_dashboard_or_user_form_required') is not False):
        errors.append('Communication efficiency needs evidence without extra user forms')
    return errors


def _text_list(value):
    return isinstance(value, list) and bool(value) and all(isinstance(s, str) and s.strip() for s in value)


def load_policy():
    value = json.loads(POLICY.read_text(encoding='utf-8'))
    errors = validate_communication_policy(value)
    if errors:
        raise ValueError('; '.join(errors))
    return value


def baseline_digest():
    return hashlib.sha256(POLICY.read_bytes()).hexdigest()


def prompt():
    """Only operative guidance is sent, not source metadata or review checklists."""
    value = load_policy()
    lines = ['L1/DOT COMMUNICATION — Grice-inspired project rules; preserve existing tool schemas.']
    lines.extend(row['id'] + ' ' + row['instruction'] for row in value['maxims'])
    lines += value['operating_rules']
    return '\n'.join(lines)

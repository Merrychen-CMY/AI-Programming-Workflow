"""Communication and scoped L1 expression guidance, never a truth or approval service."""
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
L1_SOURCE = {
    'repository': 'https://github.com/Adkid-Zephyr/anti-defensive-writing-Skill',
    'commit': 'e65bd9d8bf8ed8b9af809e5aff8237d76370876d',
    'skill_path': 'skills/anti-defensive-writing/SKILL.md',
    'skill_sha256': '6f072e66f6adf813bc1b4291754f2bc07ed4e65a398359844e97bfa215a1c28e',
    'quick_prompt_sha256': '8a52d3a5d5def26d22acf9024ab31169f74cc4d944e1b6a7c6280c3886a01e86',
    'license': 'MIT',
    'copyright': 'Copyright (c) 2026 Adkid-Zephyr',
    'license_path': 'docs/licenses/anti-defensive-writing-MIT.txt',
    'license_sha256': 'fdf440e5214588d9edcdc1ec315cddbd8525fe509242c24989a8472d283325f4',
}
L1_BOUNDARIES = {
    'existing_requirements_evidence_authority_and_c_standards_take_precedence': True,
    'full_upstream_skill_enabled': False,
    'automatic_upstream_updates': False,
    'style_may_change_acceptance_or_outcome': False,
    'style_may_hide_failures_risks_or_unknowns': False,
    'style_may_rewrite_structured_receipts_or_evidence': False,
    'blanket_negative_word_ban': False,
    'extra_user_approval_or_model_call_required': False,
    'measured_effectiveness_claimed': False,
}
PROGRESS_FORMAT = {
    'scope': 'L1_user_facing_progress_only',
    'headings': ['【类型】', '【时间】', '【进度】', '【需要你】', '【不回复会怎样】', '【详情】'],
    'type_value': '进度',
    'headings_on_separate_lines': True,
    'all_fields_required': True,
    'timestamp_format': 'YYYYMMDDHHmm',
    'timestamp_source': 'actual_report_time_in_user_timezone',
    'default_timezone': 'Asia/Shanghai',
    'silence_is_approval': False,
    'next_step_field': '进度_or_详情',
    'new_execution_gate': False,
}


def validate_communication_policy(value):
    """Validate policy structure and boundaries, never natural-language message truth."""
    if not isinstance(value, dict):
        return ['Communication policy must be an object']
    errors = []
    for name, expected in [('schema_version', 1), ('revision', 3),
                           ('scope', 'L1_user_and_L1_DOT_communication_and_supporting_worker_reports')]:
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
    errors.extend(_validate_l1_expression(value.get('l1_user_expression')))
    return errors


def _validate_l1_expression(profile):
    if not isinstance(profile, dict):
        return ['Scoped L1 expression policy is missing']
    errors = []
    if (profile.get('scope') != 'L1_user_facing_requirements_and_reports'
            or profile.get('adoption') != 'selective_project_adaptation'):
        errors.append('L1 expression must remain a selective user-facing adaptation')
    if profile.get('source') != L1_SOURCE:
        errors.append('L1 writing source must retain the reviewed commit, hashes and attribution')
    boundaries = profile.get('boundaries')
    if not isinstance(boundaries, dict):
        errors.append('L1 expression boundaries must be explicit')
    else:
        for key, expected in L1_BOUNDARIES.items():
            if boundaries.get(key) is not expected:
                errors.append('L1 expression boundary changed: ' + key)
    rows = profile.get('rules')
    if not isinstance(rows, list) or len(rows) != 5 or any(not isinstance(r, dict) for r in rows):
        return errors + ['L1 expression requires five scoped rules']
    for number, row in enumerate(rows, 1):
        if row.get('id') != f'L1-EXPR-{number:02}' or row.get('level') != 'MUST':
            errors.append('L1 expression rule identity or strength changed')
        if not isinstance(row.get('instruction'), str) or not row['instruction'].strip():
            errors.append('L1 expression rule lacks its instruction')
    progress = profile.get('progress_report')
    if not isinstance(progress, dict):
        errors.append('L1 progress report format is missing')
    else:
        for key, expected in PROGRESS_FORMAT.items():
            if type(progress.get(key)) is not type(expected) or progress.get(key) != expected:
                errors.append('L1 progress report format changed: ' + key)
        if not _text_list(progress.get('instructions')):
            errors.append('L1 progress report instructions are missing')
    return errors


def validate_l1_source_notice(root):
    """Check the redistributed license locally; never fetch or activate upstream code."""
    try:
        actual = hashlib.sha256((root / L1_SOURCE['license_path']).read_bytes()).hexdigest()
    except OSError:
        return ['L1 writing source license is missing']
    return [] if actual == L1_SOURCE['license_sha256'] else ['L1 writing source license changed']


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
    return _shared_prompt(load_policy())


def _shared_prompt(value):
    lines = ['L1/DOT COMMUNICATION — Grice-inspired project rules; preserve existing tool schemas.']
    lines.extend(row['id'] + ' ' + row['instruction'] for row in value['maxims'])
    lines += value['operating_rules']
    return '\n'.join(lines)


def l1_prompt():
    """L1-only natural-language guidance; DOT receives only the shared rules."""
    value = load_policy()
    lines = [_shared_prompt(value),
             'L1 USER EXPRESSION — 以下仅约束L1向用户沟通需求和汇报的自然语言表达；'
             '独立审查据此核对完整性，不为作者辩护。原始需求、授权、验收、证据和华为/Doxygen规范优先。'
             '不改变工具字段、原始回执、状态机或批准，不增加模型调用和用户审批。']
    lines.extend(row['id'] + ' ' + row['instruction'] for row in value['l1_user_expression']['rules'])
    progress = value['l1_user_expression']['progress_report']
    lines.append('L1 PROGRESS REPORT — 每次向用户汇报进度，必须按以下顺序输出六个独立行抬头，内容另起一行；不得埋在正文或省略字段。')
    lines.append('\n'.join(progress['headings']))
    lines.extend(progress['instructions'])
    return '\n'.join(lines)

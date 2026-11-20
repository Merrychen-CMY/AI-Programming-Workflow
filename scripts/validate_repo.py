#!/usr/bin/env python3
"""Validate this workflow and local-runtime repository; never grants approval or delivery authority.

Python 3.10+, standard library only. MANIFEST is a drift detector, not a signature.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from huawei_coverage import check_repository_coverage
from doxygen_reference import check_doxygen_reference
from project_constraints import check_project_constraints

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from dot_runtime.communication import validate_communication_policy, validate_l1_source_notice
IGNORED = {'.git', '__pycache__', '.venv', 'local-project', 'private-evidence'}
ROUTES = {
    'implementation': 'L3', 'product_requirement': 'L1',
    'task_plan_or_design': 'L2', 'verification_plan_or_test': 'L1-V',
    'test_infrastructure': 'L4', 'evidence_collection': 'L4',
    'delivery_target_or_authorization': 'L5',
    'security': 'authorized_security_owner', 'needs_analysis': 'L2', 'mixed': 'L2',
}


def repository_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and not any(part in IGNORED for part in p.relative_to(root).parts)
                  and p.name not in {'MANIFEST.json', '.env'}
                  and p.suffix != '.pyc')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_manifest(root):
    return {
        'schema_version': 1,
        'workflow_version': (root / 'VERSION').read_text(encoding='utf-8').strip(),
        'purpose': 'Content drift detection only; not a signature or approval.',
        'files': {p.relative_to(root).as_posix(): digest(p) for p in repository_files(root)},
    }


def validate_manifest(root, manifest):
    errors = []
    actual = make_manifest(root)
    if manifest.get('workflow_version') != actual['workflow_version']:
        errors.append('MANIFEST version mismatch')
    expected = manifest.get('files', {})
    for path in sorted(set(expected) | set(actual['files'])):
        if expected.get(path) != actual['files'].get(path):
            errors.append(f'MANIFEST missing, added or changed file: {path}')
    return errors


def validate_task_upgrade_policy(value):
    expected = {'schema_version': 1, 'supported_source_versions': ['3.4.0', '3.4.1', '3.5.0', '3.6.0', '3.6.1', '3.7.0', '3.8.0', '3.9.1', '3.10.0', '3.11.0', '3.12.1', '3.13.0', '3.13.3', '3.14.0', '3.14.1', '3.15.0', '3.16.0', '3.17.0', '3.17.1', '3.18.0'],
                'task_schema_version': 1, 'native_user_confirmation_required': True,
                'preserve_original_authority_and_cumulative_usage': True,
                'invalidate_approvals_and_candidate': True, 'replay_completed_implementation': False,
                'clear_unknown_or_reopen_final_report': False,
                'crash_recovery': 'finish_exact_authorized_local_writes_only',
                'guide': 'docs/guides/task-upgrades.md'}
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Task upgrade policy fields differ from implemented scope']
    return ['Task upgrade boundary drift: ' + k for k, wanted in expected.items()
            if type(value[k]) is not type(wanted) or value[k] != wanted]


def validate_review_handoff_policy(value):
    expected = {'schema_version': 1, 'guide': 'docs/guides/normal-start.md',
        'planning_includes_current_approval_and_native_amendments': True,
        'approval_reply_aliases_compacted': True, 'review_markdown_embeds_raw_runtime_output': False,
        'source_packet_owner': 'controller', 'source_packet_digest_bound': True,
        'existing_record_copy_consumes_revision': False,
        'new_facts_and_baseline_changes_retain_existing_limits': True,
        'approval_order': ['requirements', 'plan', 'execution'],
        'initial_native_order': ['prepare_requirements', 'independent_requirements_review', 'l1_requirements_approval',
            'dot_plan', 'dot_initialize', 'independent_plan_review', 'l1_plan_approval', 'execution'],
        'preplan_reviews_counted_and_imported': True,
        'wrong_phase_result': 'COMPLETED_REJECTED', 'wrong_phase_dispatches_worker_or_api': False,
        'wrong_phase_changes_authority_or_usage': False, 'latest_review_before_history': True,
        'record_tool': 'dot_read_record', 'record_page_characters': 6000,
        'record_read_clears_unknown': False, 'reopen_final_report': False,
        'retain_full_report_and_original_hash': True,
        'markdown_reports': {
            'stages': ['requirements_review', 'execution_plan_review', 'final_success_or_failure_including_preflight'],
            'format': 'markdown', 'metadata_field': 'report_files', 'original_json_retained': True,
            'immutable_content_addressed_files': True, 'new_report_returned_once_per_operation': True,
            'chat_delivery': 'host_deliverables_presented_event', 'host_bootstrap_abi': 12,
            'final_confirmation_checks_markdown_digest': True, 'adds_user_approval_checkpoint': False,
            'delivery_failure_replays_review_or_execution': False}}
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Review handoff policy differs from implemented scope']
    return ['Review handoff boundary drift: ' + key for key, wanted in expected.items()
            if type(value[key]) is not type(wanted) or value[key] != wanted]


def validate_closure_policy(value):
    expected = {'schema_version': 1, 'non_success_requires_explicit_l1_assessment': True,
        'l2_may_finalize_failure': False, 'l2_first_considers_in_scope_solutions': True,
        'insufficient_evidence_is_terminal': False, 'out_of_scope_proposal_owner': 'L1',
        'automatic_non_success_route': 'L1_assessment',
        'assessment_template': 'templates/l1-closure.example.json', 'guide': 'docs/guides/l1-closure.md'}
    return [] if value == expected else ['Invalid L1 closure governance']


def validate_flow_recovery(value):
    expected = {'schema_version': 1, 'guide': 'docs/guides/flow-recovery.md', 'bootstrap_abi': 12,
        'completed_preflight_review_recovery_requires_native_attestation': True,
        'completed_review_recovery_preserves_valid_approval': True, 'maintenance_recovery_changes_pinned_runtime': False,
        'new_native_adaptive_limits_required': True, 'requirements_review_before_initial_planning': True,
        'initialization_adopts_review_without_replay': True, 'preflight_review_usage_preserved': True,
        'read_only_investigation_tool': 'dot_investigate', 'l1_decision_tool': 'l1_resume_decision',
        'l2_requires_solutions_or_investigation_steps': True, 'l2_may_finalize_failure': False,
        'closure_evidence_must_exist': True, 'unaffected_completed_work_preserved': True,
        'changed_candidate_reuses_old_pass': False, 'native_recovery_requires_process_and_evidence_checks': True,
        'recovery_preserves_original_unknown_and_usage': True, 'recovery_reopens_final_report': False,
        'preflight_report_requires_l1_submission': True, 'legacy_receipts_rewritten_as_requirements_first': False}
    return [] if value == expected else ['Flow recovery policy differs from implemented boundaries']


def validate_execution_scope(value):
    expected = {'schema_version': 1, 'guide': 'docs/guides/execution-scope.md',
        'minimal_change_instruction': 'Make the smallest change that fully solves the task. Do not add abstractions, fallbacks, defensive guards, refactors, or features unless they are strictly required.',
        'scope_before_action': ['expected_outcome', 'allowed_objects', 'preserved_constraints', 'acceptance_conditions'],
        'unclear_path_search': 'smallest_relevant_scope',
        'read_only_requests': ['explanation', 'diagnosis', 'review'],
        'read_only_may_edit_product': False, 'read_only_may_run_tests': False,
        'explicitly_assigned_verification_may_run_authorized_tests': True,
        'unrequired_compatibility_layers_switches_dependencies_abstractions': False,
        'unrelated_findings_authorize_repairs': False, 'unrelated_findings_route': 'report_to_L1_for_user_decision',
        'stop_when_acceptance_work_complete': True,
        'progress_check': {'owner': 'L2_DOT', 'transport': 'same_active_codex_thread_turn_steer',
            'no_visible_progress_seconds': 120, 'reasoning_or_heartbeat_resets_timer': False,
            'running_explicit_command_is_thinking': False,
            'repeat_after_ack_and_another_full_interval': True, 'pending_ack_allows_duplicate': False,
            'rejected_steer_retried_automatically': False, 'acknowledgement_is_answer': False,
            'request': '你正在做什么？请说明当前步骤、已完成内容、阻碍和最小下一步；避免过度设计。',
            'requests_private_chain_of_thought': False, 'starts_new_turn_or_replays_work': False,
            'extends_permissions_budget_or_deadline': False,
            'scope': 'active_codex_workers_only_L5_summary_remains_DOT'}}
    return [] if value == expected else ['Execution scope or progress check policy differs from implemented boundaries']


def validate_input_material_policy(value):
    expected = {'schema_version': 1, 'api_input_limit_bytes': 250000,
        'api_wire_limit_bytes': 32000000, 'image_bytes_count_as_text': False,
        'triage_view_target_bytes': 60000, 'local_rejection_outcome': 'NOT_SENT',
        'local_rejection_operation_status': 'COMPLETED_REJECTED', 'investigation_owner': 'L2',
        'escalation_owner': 'L1', 'unclassified_route': 'needs_analysis',
        'auto_finalize_undiagnosed_cause': False, 'preserve_original_evidence': True,
        'mark_excerpts_and_source_hash': True, 'local_rejection_consumes_api_attempt': False,
        'clear_unresolved_product_operation': False, 'reopen_old_final_report': False,
        'guide': 'docs/guides/investigation.md'}
    expected.update({'planning_research_view_bytes': 32000, 'tool_output_view_bytes': 16000, 'tool_history_compaction_threshold_bytes': 80000, 'l2_evidence_read_tool': 'read_evidence', 'residual_input_recovery_coordinator': 'L1', 'approved_baseline_silently_truncated': False})
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Input material policy fields differ from implemented scope']
    return ['Input investigation policy drift: ' + k for k, v in expected.items()
            if type(value[k]) is not type(v) or value[k] != v]


def validate_api_recovery_policy(value):
    expected = {'schema_version': 1, 'scope': 'structured_api_failures_only',
        'review_state': 'API_REVIEW_PENDING', 'native_review_state': 'API_AUTHORIZATION_PENDING',
        'default_wait_seconds': 120, 'wait_ceiling': 'current_limits.timeout_seconds_and_remaining_total_time',
        'l1_tool': 'l1_review_api', 'native_tool': 'workflow_review_api_retry',
        'safe_l1_retry_outcomes': ['NOT_SENT', 'REJECTED', 'RESPONSE_RECEIVED'],
        'ambiguous_retry_requires_native_approval': True, 'automatic_retry': False,
        'clear_old_unknown': False, 'reset_usage': False, 'reopen_final_report': False,
        'legacy_interruption_recovery': False, 'live_response_retrieval': False,
        'guide': 'docs/guides/api-recovery.md'}
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['API recovery policy fields differ from implemented scope']
    return ['API recovery boundary drift: ' + k for k, v in expected.items()
            if type(value[k]) is not type(v) or value[k] != v]


def validate_initialization_recovery_policy(value):
    expected = {'schema_version': 1, 'supported_source_versions': ['3.5.0', '3.6.0', '3.6.1', '3.7.0', '3.8.0', '3.9.1', '3.10.0', '3.11.0', '3.12.1', '3.13.0', '3.13.3', '3.14.0', '3.14.1', '3.15.0', '3.16.0', '3.17.0', '3.17.1', '3.18.0', '3.19.0'],
                'scope': 'uncommitted_or_unbound_local_initialization_only', 'native_confirmation_required': True,
                'changed_baseline_requires_explicit_adoption_and_replan': True,
                'archive_partial_files_and_marker': True, 'original_confirmation_immutable': True,
                'reset_usage_or_deadline': False, 'product_write_or_api_replay': False,
                'initialized_task_or_final_report_recovery': False,
                'recovery_interruption': 'finish_exact_approved_local_writes_only',
                'guide': 'docs/guides/initialization-recovery.md'}
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Initialization recovery policy fields differ from implemented scope']
    return ['Initialization recovery boundary drift: ' + k for k, v in expected.items()
            if type(value[k]) is not type(v) or value[k] != v]


def validate_update_policy(value):
    expected = {
        'schema_version': 1, 'repository': 'Merrychen-CMY/ReqPilot',
        'channel': 'github_stable_releases', 'bootstrap_abi': 12,
        'default_enabled': True, 'check_interval_seconds': 21600,
        'checks_on_tool_activity_only': True, 'network_sends_project_or_gpt_credentials': False,
        'private_repository_auth': 'existing_local_git_credential_no_interactive_prompt',
        'github_credential_in_logs_or_model': False,
        'paid_api_calls': False, 'confirmed_tasks_pin_manifest': True,
        'task_history_mutation': False, 'automatic_task_migration': False,
        'unknown_clear_or_replay': False, 'install_scripts': False,
        'automatic_host_plugin_replacement': False,
        'new_tool_or_major_or_abi_change': 'host_install_required',
        'guide': 'docs/guides/automatic-updates.md',
        'reviewed_task_migration': 'policy/task-upgrades.json',
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Update policy fields differ from implemented scope']
    return ['Update boundary drift: ' + key for key, wanted in expected.items()
            if type(value[key]) is not type(wanted) or value[key] != wanted]


def validate_policy(policy):
    errors = []
    expected = {
        'execution_authority': False,
        'reflow.sources': ['L4', 'L5'],
        'reflow.intake_owner': 'L2', 'reflow.triage_owner': 'L2',
        'reflow.require_issue_id': True,
        'reflow.require_evidence_before_classification': True,
        'reflow.allow_direct_l4_l5_to_l3': False,
        'candidate_rules.immutable': True,
        'candidate_rules.reuse_pass_across_candidates': False,
        'candidate_rules.plan_or_verifier_semantic_change': 'new_approval_and_rerun_all_required_checks',
        'candidate_verification.require_test_contract': True,
        'candidate_verification.require_actual_product_provenance': True,
        'candidate_verification.required_test_skip_passes': False,
        'candidate_verification.facility_actions': 'L2_selects_baseline_approved_actions_or_requests_L1_review',
        'approval_rules.L5_can_approve_delivery': False,
        'approval_rules.merge_implies_release': False,
        'approval_rules.example_is_authorization': False,
        'closure.G3_requires_issue_CLOSED': False,
        'closure.preserve_all_attempts': True,
        'closure.implementation_or_verification': 'independent_L4_reverification_and_trusted_verdict_then_L2_close',
        'closure.delivery_only': 'approval_service_and_delivery_guard_validation_L5_review_then_L2_close',
        'budgets.reset_on_restart': False,
    }
    for path, wanted in expected.items():
        value = policy
        for key in path.split('.'):
            value = value.get(key) if isinstance(value, dict) else None
        if value != wanted or type(value) is not type(wanted):
            errors.append(f'Invariant {path}: expected {wanted!r}, got {value!r}')
    if policy.get('roles') != ['L1', 'L1-V', 'L2', 'L3', 'L4', 'L5']:
        errors.append('Six distinct roles including both L1 roles are required')
    routes = policy.get('reflow', {}).get('routes', {})
    for kind, owner in ROUTES.items():
        if kind not in routes or routes[kind].get('owner') != owner:
            errors.append(f'Reflow route {kind} must be owned by {owner!r} after L2 triage')
    required_inputs = {'product_code', 'dependencies', 'toolchain', 'build_configuration', 'artifacts', 'integration_base'}
    if not required_inputs.issubset(policy.get('candidate_rules', {}).get('new_id_on', [])):
        errors.append('Changed candidate inputs must create a new candidate ID')
    for key, value in policy.get('budgets', {}).items():
        if key != 'reset_on_restart' and (type(value) is not int or value < 1):
            errors.append(f'Budget {key} must be a positive integer')
    return errors


SIMPLE_FLOW_TRANSITIONS = {
    'DISCOVERY': {'REVIEW_PENDING'},
    'REVIEW_PENDING': {'DISCOVERY', 'PLAN_PENDING', 'DECISION_PENDING', 'REPORT_PENDING', 'BLOCKED'},
    'PLAN_PENDING': {'REVIEW_PENDING', 'EXECUTING', 'DECISION_PENDING', 'REPORT_PENDING', 'BLOCKED'},
    'EXECUTING': {'VERIFYING', 'DECISION_PENDING', 'REPORT_PENDING', 'BLOCKED'},
    'VERIFYING': {'EXECUTING', 'DECISION_PENDING', 'REPORT_PENDING', 'BLOCKED'},
    'DECISION_PENDING': {'DECISION_PENDING', 'REVIEW_PENDING', 'PLAN_PENDING', 'EXECUTING', 'VERIFYING', 'REPORT_PENDING', 'BLOCKED'},
    'BLOCKED': {'REVIEW_PENDING', 'PLAN_PENDING', 'REPORT_PENDING'},
    'REPORT_PENDING': {'DELIVERY_READY', 'BLOCKED'},
    'DELIVERY_READY': {'DELIVERED', 'DECISION_PENDING', 'BLOCKED'},
    'DELIVERED': {'DISCOVERY'},
}


def validate_clarification_policy(value):
    expected = {'state': 'NEEDS_INFO', 'append_tool': 'l1_add_facts',
                'original_confirmation_immutable': True, 'same_task_supplements_allowed': True,
                'budget_reset_allowed': False, 'expiry_extension_allowed': False,
                'requires_independent_baseline_review': True, 'unknown_or_final_report_auto_resume': False,
                'guide': 'docs/guides/clarifications.md'}
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Clarification policy fields differ from implemented scope']
    return ['Clarification boundary drift: ' + key for key, wanted in expected.items()
            if type(value[key]) is not type(wanted) or value[key] != wanted]


def validate_planning_policy(value):
    expected = {
        'schema_version': 1, 'rejected_state': 'PLAN_REJECTED',
        'completed_rejection_clears_only_current_inflight': True,
        'unknown_replay_allowed': False,
        'implementation_budget_unit': 'global_L3_dispatches_including_initial_and_repairs',
        'verification_phases_are_implementation_tasks': False,
        'rejection_resets_counters': False,
        'replanning_consumes': ['api_calls', 'wall_time'],
        'independent_review_required': True, 'legacy_locked_task_auto_migration': False,
        'initial_native_plan_requires_approved_requirements': True,
        'guide': 'docs/guides/plan-rejections.md'}
    expected.update({'l2_draft_corrections_per_call': 1, 'resource_shortfall_auto_correction': False})
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Planning policy fields differ from implemented scope']
    return ['Planning boundary drift: ' + key for key, wanted in expected.items()
            if type(value[key]) is not type(wanted) or value[key] != wanted]


def validate_simple_flow(flow):
    """Validate policy invariants and exact allowed edges; not a runtime approval."""
    errors = []
    expected = {
        'default_mode': 'simple',
        'default_approval_mode': 'delegated_l1',
        'requirements_require_l1_approval': True,
        'execution_plan_requires_l1_approval': True,
        'product_or_ac_change_requires_l1_decision': True,
        'approval_owners': {'requirements': 'L1', 'execution_plan': 'L1', 'final_report': 'user'},
        'execution_authority': False,
        'user_facing_roles': ['L1'],
        'user_approval_artifact': 'l1_final_success_or_failure_report',
        'implementation_requires_user_approval': False,
        'requirements_approval_allows_implementation': False,
        'execution_plan_requires_user_approval': False,
        'final_report_requires_user_approval': True,
        'routine_in_scope_work_requires_new_user_approval': False,
        'internal_verification_plan_requires_separate_user_document': False,
        'independent_verification_required': True,
        'candidate_verification.require_test_contract': True,
        'candidate_verification.require_actual_product_provenance': True,
        'candidate_verification.required_test_skip_passes': False,
        'independent_review_required': True,
        'distinct_worker_sessions': ['developer', 'tester', 'reviewer'],
        'implementation_worker_can_issue_final_acceptance': False,
        'issue_intake': 'DOT_as_L2',
        'direct_verifier_to_developer_dispatch': False,
        'product_or_ac_change_requires_user_decision': True,
        'in_scope_requirement_clarification_requires_user_decision': False,
        'authority_change_requires_user_decision': True,
        'plan_maintenance.may_lower_acceptance_standard': False,
        'plan_maintenance.semantic_change': 'L1_reapproves_within_current_user_authority_and_reruns_required_checks',
        'delivery.execute_when_existing_authorization_covers_actual_action': True,
        'delivery.check_actual_candidate_evidence_action_target': True,
        'delivery.require_new_approval_when_scope_not_covered': True,
        'delivery.requires_approved_success_report': True,
        'delivery.preauthorization_may_bypass_report_review': False,
        'delivery.local_runtime_executes_delivery': False,
        'delivery.delivered_requires_external_receipt': True,
        'delivery.DOT_may_self_authorize': False,
        'internal_role_mapping.L2': 'DOT_orchestration',
        'internal_role_mapping.L5': 'DOT_acceptance_and_delivery_request',
        'existing_project_protections_remain_required': True,
        'runtime_implemented_by_this_repository': True,
        'runtime_approval_trust_model': 'local_delegated_l1_and_final_human_no_authenticated_service',
        'agent_may_enter_user_approval': False,
        'failed_report_approval_resumes_execution': False,
        'budgets_reset_on_restart_or_replan': False,
        'three_checkpoints': ['approved_requirements', 'approved_execution_plan', 'approved_final_report'],
    }
    for path, wanted in expected.items():
        value = flow
        for key in path.split('.'):
            value = value.get(key) if isinstance(value, dict) else None
        if type(value) is not type(wanted) or value != wanted:
            errors.append(f'Simple flow boundary {path}: expected {wanted!r}, got {value!r}')
    required_bindings = {'actions', 'targets', 'candidate_conditions', 'evidence_conditions', 'expiry', 'use_limit'}
    if not required_bindings.issubset(flow.get('delivery', {}).get('preauthorization_must_bind', [])):
        errors.append('Delivery preauthorization must be bounded and bind actual candidate/evidence conditions')
    required_outputs = {'initial_requirements_questions', 'success_or_failure_final_report_for_review'}
    if not required_outputs.issubset(flow.get('user_visible_outputs', [])):
        errors.append('Only initial questions and final report require user interaction')
    states = flow.get('states', [])
    if not isinstance(states, list) or not all(isinstance(state, dict) for state in states):
        return errors + ['Simple flow states must be objects in a list']
    ids = [state.get('id') for state in states]
    if not all(isinstance(state_id, str) for state_id in ids):
        return errors + ['Simple flow state IDs must be strings']
    if set(ids) != set(SIMPLE_FLOW_TRANSITIONS) or len(set(ids)) != len(ids):
        errors.append('Simple flow states are incomplete or duplicated')
    for state in states:
        state_id = state.get('id')
        targets = state.get('next')
        valid_targets = isinstance(targets, list) and all(isinstance(target, str) for target in targets)
        if not valid_targets or len(targets) != len(set(targets)) or set(targets) != SIMPLE_FLOW_TRANSITIONS.get(state_id):
            errors.append(f'Invalid simple flow allowed edges: {state_id}')
        if not isinstance(state.get('guard'), str) or not state['guard'].strip():
            errors.append(f'Missing simple flow guard: {state_id}')
    return errors


def validate_resource_policy(value):
    expected = {
        'schema_version': 1, 'opt_in_field': 'adaptive_limits', 'new_task_default': True,
        'legacy_auto_migration': False,
        'new_native_task_required': True, 'legacy_mode': 'preserve_existing_confirmations_and_receipts_only',
        'user_confirms': ['goals_constraints_acceptance', 'permissions', 'final_deadline'],
        'resource_ceiling_owner': 'L1', 'resource_increase_requires_user_approval': False,
        'capability_policy': 'policy/agent-capabilities.json',
        'resource_ceiling_owner': 'L1', 'resource_increase_requires_user_approval': False,
        'capability_policy': 'policy/agent-capabilities.json',
        'allocation_owner': 'L1', 'exhaustion_state': 'BUDGET_REVIEW_PENDING',
        'human_request_state': 'AUTHORIZATION_PENDING', 'status_layer': 'bridge_overlay_preserves_runtime_state',
        'allocation_tool': 'l1_adjust_limits', 'request_tool': 'l1_request_authorization',
        'human_review_tool': 'workflow_review_authorization', 'human_confirmation_provider': 'native_ctx.userQuestions',
        'model_supplied_human_approval': False, 'original_confirmation_immutable': True,
        'usage_reset_allowed': False, 'history_deletion_allowed': False, 'unknown_auto_clear': False,
        'final_report_auto_reopen': False, 'independent_verification_required': True,
        'scope_change_requires_replan_and_independent_review': True,
        'resource_only_change_invalidates_product_baseline': False,
        'amendable_fields': ['limits', 'expires_at', 'allowed_paths', 'hard_constraints', 'authorized_context'],
        'final_report_includes': ['user_authority', 'operating_limits', 'actual_usage', 'history', 'dot_api_reported_usage'],
        'unified_monetary_hard_cap': False, 'guide': 'docs/guides/adaptive-resources.md',
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        return ['Resource policy fields differ from implemented scope']
    return ['Resource boundary drift: ' + key for key, wanted in expected.items()
            if type(value[key]) is not type(wanted) or value[key] != wanted]


def validate_capabilities(index):
    """Only the implemented local runtime may be advertised, not external services."""
    expected = {
        'static_repo_validation': True,
        'runtime_controller': True,
        'real_approval_service': False,
        'product_tests_executed': False,
        'hil_tests_executed': False,
        'delivery_executor': False,
        'l1_delegated_approval': True, 'gpt_responses_adapter': True,
        'l1_mcp_bridge': True, 'harness_end_to_end_verified': False,
        'harness_native_plugin_bundle': True,
    }
    errors = []
    capabilities = index.get('capabilities', {})
    if set(capabilities) != set(expected):
        errors.append('Capability inventory differs from supported local runtime scope')
    for name, wanted in expected.items():
        if type(capabilities.get(name)) is not bool or capabilities.get(name) is not wanted:
            errors.append(f'Capability {name} must be {wanted}')
    runtime = index.get('runtime_scope', {})
    if runtime.get('approval_trust_model') != 'local_delegated_l1_and_final_human_no_authenticated_service':
        errors.append('Local runtime must declare its limited approval trust model')
    if runtime.get('external_delivery_implemented') is not False:
        errors.append('Local runtime must not claim external delivery')
    if runtime.get('entrypoint') != 'scripts/dot.py' or runtime.get('implementation') != 'runtime/dot_runtime':
        errors.append('Runtime must identify the actual local entrypoint and implementation')
    return errors


def validate_embedded_c_policy(policy, source_rules):
    """Check integration boundaries and faithful mapping, not real C compliance."""
    errors = []
    expected = {
        'standard_version': '1.3', 'contract_schema_version': '1.1',
        'coding_reference.document_id': 'DKBA 2826-2011.5',
        'clause_coverage.catalog': 'standards/embedded-c/v1.3/huawei/coverage.json',
        'clause_coverage.numbered_clauses': 139,
        'clause_coverage.governance_supplements': 6,
        'clause_coverage.coverage_revision': 3,
        'clause_coverage.reviewed_subrequirements': 50,
        'clause_coverage.all_catalog_subrequirements_accounted_for': True,
        'clause_coverage.parent_pass_does_not_imply_child_pass': True,
        'documentation_reference.huawei_takes_precedence': True,
        'documentation_reference.pinned_commit': 'd0c0531cf3802f399540387c74641eaa45d033ac',
        'documentation_reference.required_for_huawei_c_plans': True,
        'documentation_reference.tool_success_proves_semantic_truth': False,
        'documentation_reference.extra_user_forms_required': False,
        'clause_coverage.product_compliance_claimed': False,
        'default_for_applicable_projects': True, 'extra_user_documents_required': False,
        'AI_written_source_is_generated_code_exception': False,
        'source_ESP32_comment_only_restriction_is_global': False,
        'rule_checkers_all_implemented': False,
        'coding.goto_allowed': False, 'coding.control_statement_braces_required': True,
        'coding.indentation': 'spaces', 'coding.indent_width': 4,
        'coding.tab_indentation_allowed': False,
        'coding.goto_prohibition_origin': 'retained_project_requirement_stricter_than_Huawei_suggestion_6.3',
        'coding.column_target_origin': 'project_choice_allowed_by_Huawei_rule_9.3',
        'coding.column_target': 80, 'coding.column_target_is_hard_limit': False,
        'coding.brace_placement': 'allman_project_choice_allowed_by_Huawei_9.5',
        'coding.if_function_call_requires_named_result': True,
        'coding.boolean_return_constants': ['FALSE', 'TRUE'],
        'coding.definition_documentation': 'complete_Doxygen_above_definition_with_header_contract_preserved',
        'project_constraints.revision': 1,
        'project_constraints.rules': 26,
        'project_constraints.huawei_takes_precedence': True,
        'project_constraints.legacy_file_scope_inherited': False,
        'project_constraints.current_task_scope_required': True,
        'project_constraints.all_rules_automatically_checked': False,
        'readability.review_triggers_are_hard_limits': False,
        'readability.trigger_requires_recorded_review': True,
        'readability.mechanical_split_counts_as_fix': False,
        'readability.preserve_short_circuit_MMIO_atomicity_cleanup': True,
        'readability.review_triggers.function_nonblank_noncomment_lines_greater_than': 50,
        'readability.new_function_constraints.max_nonblank_noncomment_lines': 50,
        'readability.new_function_constraints.max_nesting_depth': 4,
        'readability.new_function_constraints.exception_requires_record_and_authorized_review': True,
        'readability.new_function_constraints.routine_review_note_is_exception_approval': False,
        'readability.new_function_constraints.automatically_rewrite_legacy_code': False,
        'risk.behavior_preserving_implementation_refactor_may_be_R0': False,
        'risk.all_profiles_required_for_every_task': False,
        'verification.host_behavior_links_real_C': True,
        'verification.oracle_independent_of_current_output': True,
        'verification.zero_tests_or_exit_zero_alone_means_pass': False,
        'verification.schema_validation_means_product_acceptance': False,
        'verification.source_schema_cross_file_runtime_validation_implemented': False,
        'acceptance.report_starts': 'INCOMPLETE',
        'acceptance.FINAL_or_CLOSED_means_accepted': False,
        'acceptance.ACCEPTED_means_delivered_or_authorized': False,
        'acceptance.required_unexecuted_checks_block_acceptance': True,
        'acceptance.optional_failure_affecting_requirement_may_be_ignored': False,
    }
    for path, wanted in expected.items():
        value = policy
        for key in path.split('.'):
            value = value.get(key) if isinstance(value, dict) else None
        if type(value) is not type(wanted) or value != wanted:
            errors.append(f'Embedded C boundary {path}: expected {wanted!r}, got {value!r}')
    original = {rule['id']: rule for rule in source_rules}
    mapped = policy.get('rules', [])
    if len(original) != 33 or len(mapped) != 33 or {r.get('id') for r in mapped} != set(original):
        errors.append('All 33 current embedded C rules must be mapped exactly once')
    for rule in mapped:
        source = original.get(rule.get('id'), {})
        if any(rule.get(key) != source.get(key) for key in ('level', 'title', 'scope', 'sections', 'verification', 'failure_handling')):
            errors.append(f'Embedded C rule differs from source: {rule.get("id")}')
        if rule.get('finding_intake') != 'DOT_as_L2' or rule.get('dispatch_requires_root_cause_analysis') is not True:
            errors.append(f'Embedded C finding must enter DOT triage: {rule.get("id")}')
        if rule.get('default_responsibility') not in ('L1', 'L1-V', 'L2', 'L3', 'L4', 'L5') or not rule.get('references'):
            errors.append(f'Embedded C mapping missing owner/reference: {rule.get("id")}')
    return errors


def check_local_link(root, source, target):
    target = target.strip().strip('<>')
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return []
    destination = (source.parent / unquote(parsed.path)).resolve()
    if not destination.is_relative_to(root.resolve()):
        return [f'Link escapes repository: {source.relative_to(root)} -> {target}']
    if not destination.exists():
        return [f'Missing link: {source.relative_to(root)} -> {target}']
    return []


def validate_repository(root):
    errors, documents = [], {}
    for file in repository_files(root):
        if file.suffix == '.json':
            try:
                documents[file.relative_to(root).as_posix()] = json.loads(file.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError) as exc:
                errors.append(f'Invalid JSON: {file.relative_to(root)}: {exc}')
        if file.suffix == '.md':
            content = file.read_text(encoding='utf-8')
            content = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', content, flags=re.M | re.S)
            for target in re.findall(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', content):
                errors.extend(check_local_link(root, file, target))
    required = ['AGENTS.md', 'L1.md', 'DOT.md', 'templates/requirements.md',
                'templates/execution-plan.md', 'templates/final-report.md', 'docs/guides/local-runtime.md', 'scripts/dot.py',
                'policy/simple-flow.json', 'policy/communication.json', 'policy/embedded-c.json', 'START_HERE.md', 'README.md', 'INDEX.json', 'VERSION',
                'docs/core-rules.md', '.github/workflows/validate.yml',
                'policy/workflow.json', 'policy/roles.json', 'policy/rejection-tests.json',
                'policy/l4-failures.json', 'policy/l5-delivery.json']
    errors.extend(f'Missing required file: {name}' for name in required if not (root / name).is_file())
    if errors:
        return errors
    policy, index = documents['policy/workflow.json'], documents['INDEX.json']
    errors.extend(validate_policy(policy))
    simple = documents['policy/simple-flow.json']
    errors.extend(validate_simple_flow(simple))
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    readme_versions = re.findall(r'当前版本\s*\*\*([^*]+)\*\*', (root / 'README.md').read_text(encoding='utf-8'))
    if readme_versions != [version]:
        errors.append('README current version must appear once and match VERSION')
    if not (root / 'docs/releases' / (version + '.md')).is_file():
        errors.append('Current version release notes are missing: docs/releases/' + version + '.md')
    if version != policy.get('policy_version') or version != index.get('workflow_version'):
        errors.append('VERSION, INDEX and policy versions differ')
    if version != simple.get('workflow_version') or version != documents['templates/project-context.json']['workflow']['version']:
        errors.append('Simple flow or context template version differs from VERSION')
    primary = ['AGENTS.md', 'L1.md', 'DOT.md', 'templates/requirements.md']
    if index.get('primary_documents') != primary or simple.get('entry_documents') != primary:
        errors.append('Default entry documents must be the compact L1/DOT workflow')
    if index.get('always_read') != ['AGENTS.md'] or index.get('default_mode') != 'simple':
        errors.append('Default startup must not require full technical reference loading')
    paths = [index['entrypoint'], *index['always_read'], *index['original_examples']]
    paths.extend(primary)
    paths.extend([index['reference_scope'], simple['advanced_reference'], policy['default_user_flow']])
    embedded = documents['policy/embedded-c.json']
    paths.extend([embedded['entrypoint'], embedded['source_index']])
    errors.extend(validate_embedded_c_policy(embedded, documents[embedded['source_index']]['rules']))
    if embedded.get('workflow_version') != version:
        errors.append('Embedded C integration workflow version mismatch')
    for standard in index.get('coding_standards', []):
        paths.extend([standard['index'], standard['policy']])
    for rule in embedded['rules']:
        paths.extend(rule['references'])
    embedded_root = embedded['reference_root']
    paths.append(embedded['coding_reference']['adoption'])
    paths.extend(embedded['clause_coverage'][key] for key in ('entrypoint', 'catalog', 'inventory'))
    errors.extend(check_repository_coverage(root, {r['id'] for r in embedded['rules']}))
    source_record = documents[embedded_root + 'SOURCE.json']
    records = source_record['derived_files'][:]
    for snapshot in embedded['historical_source_snapshots']:
        paths.append(snapshot)
        records.extend(documents[snapshot]['imported_files'])
    for record in records:
        path = root / record['path']
        if not path.is_file() or digest(path) != record['repository_sha256']:
            errors.append(f'Source or derived-file drift: {record["path"]}')
    source_text = (root / embedded_root / 'source/standard.txt').read_text(encoding='utf-8').splitlines()
    sections = documents[embedded_root + 'INDEX.json']['sections']
    if len(sections) != 18:
        errors.append('Embedded C reference must retain 18 sections/appendices')
    for section in sections:
        paths.append(section['path'])
        page = root / section['path']
        if not page.is_file():
            continue
        rendered = page.read_text(encoding='utf-8').replace('\\|', '|')
        normalize = lambda value: re.sub(r'[\s|]', '', value)
        for line in source_text[section['source_start_line'] - 1:section['source_end_line']]:
            if line.strip() and normalize(line) not in normalize(rendered):
                errors.append(f'Embedded C section lost source text: {section["path"]}')
                break
    for group in ('role_reading', 'event_reading'):
        for entries in index[group].values():
            paths.extend(entries)
    paths.extend(row['path'] for row in index['full_reference'])
    if [row['chapter'] for row in index['full_reference']] != list(range(1, 22)):
        errors.append('Full reference must preserve all 21 chapters in order')
    roles = documents['policy/roles.json']['roles']
    if sorted(row['id'] for row in roles) != sorted(policy['roles']):
        errors.append('Role documents and policy role IDs differ')
    paths.extend(row['path'] for row in roles)
    for path in paths:
        errors.extend(check_local_link(root, root / 'INDEX.json', path))
    for name, layer, count in [('l4-failures', 'L4', 16), ('l5-delivery', 'L5', 12)]:
        matrix = documents[f'policy/{name}.json']
        if matrix.get('source_layer') != layer or any(matrix.get(k) != 'L2' for k in ('intake_owner', 'triage_owner')):
            errors.append(f'{name}: all findings must enter L2 triage')
        rows = matrix['scenarios']
        if len(rows) != count or len({r['id'] for r in rows}) != count:
            errors.append(f'{name}: expected {count} unique scenarios')
        for row in rows:
            if not all(row.get(key) for key in ('observation', 'l2_analysis_and_blocking', 'handler_after_l2_triage', 'resume_or_close_conditions')):
                errors.append(f'{name}: incomplete scenario {row.get("id")}')
    cases = documents['policy/rejection-tests.json']['cases']
    if len(cases) != 48 or len({c['id'] for c in cases}) != 48:
        errors.append('Expected 48 unique acceptance/rejection scenarios')
    if any(c.get('execution_status') != 'NOT_RUN' or c.get('evidence_ref') is not None for c in cases):
        errors.append('Reference scenarios must not claim real execution; store actual results in the product project')
    errors.extend(validate_capabilities(index))
    errors.extend(check_doxygen_reference(root))
    errors.extend(check_project_constraints(root))
    errors.extend(validate_communication_policy(documents['policy/communication.json']))
    errors.extend(validate_l1_source_notice(root))
    onboarding = documents.get('policy/onboarding.json', {})
    expected_onboarding = {
        'workflow_version': version,
        'human_start_confirmation': 'once_after_discovery_before_DOT_calls',
        'confirmation_provider': 'native_ctx.userQuestions',
        'model_can_supply_human_confirmation': False,
        'routine_intermediate_approval_owner': 'L1_after_independent_review',
        'final_report_review_owner': 'user',
        'credential_in_model_arguments': False,
        'credential_in_worker_environment': False,
        'external_delivery_executor': False,
        'interruption': 'persist_UNKNOWN_before_dispatch_no_automatic_replay',
        'base_environment_before_requirements': True,
        'setup_diagnostics': 'allowlisted_code_stage_http_status_no_raw_errors',
    }
    for key, expected in expected_onboarding.items():
        if onboarding.get(key) != expected or type(onboarding.get(key)) is not type(expected):
            errors.append('Onboarding invariant drift: ' + key)
    topics = ['requirements', 'baseline', 'scope', 'functions', 'acceptance', 'test_environment', 'test_plan', 'delivery', 'safety']
    if onboarding.get('ordered_stages') != ['configure_model_and_key', 'check_environment', 'discuss_raw_requirements',
                                          'confirm_start', 'l1_autonomous_execution', 'user_final_report_review']:
        errors.append('Onboarding stage order must be configuration, environment, requirements, start, execution, final review')
    if onboarding.get('required_topics') != topics or set(documents.get('templates/harness-start.example.json', {}).get('topics', {})) != set(topics):
        errors.append('Onboarding must preserve all nine discovery topics')
    package = documents.get('package.json', {})
    errors.extend(validate_update_policy(documents.get('policy/updates.json')))
    errors.extend(validate_task_upgrade_policy(documents.get('policy/task-upgrades.json')))
    errors.extend(validate_initialization_recovery_policy(documents.get('policy/initialization-recovery.json')))
    errors.extend(validate_api_recovery_policy(documents.get('policy/api-recovery.json')))
    errors.extend(validate_review_handoff_policy(documents.get('policy/review-handoff.json')))
    errors.extend(validate_flow_recovery(documents.get('policy/flow-recovery.json')))
    errors.extend(validate_execution_scope(documents.get('policy/execution-scope.json')))
    file_policy = documents.get('policy/file-environment.json', {})
    file_expected = {'schema_version': 1, 'guide': 'docs/guides/encrypted-environment.md', 'bootstrap_abi': 12,
        'scope': 'all_workflow_file_content_not_two_example_paths',
        'read_provider': 'native_then_same_account_configured_python', 'expected_hashes_must_match': True,
        'disable_or_bypass_encryption_policy': False, 'consumer_access_requires_separate_evidence': True,
        'replace_attempts': 6, 'retry_only_same_local_commit': True, 'retain_failed_commit_staging': True,
        'automatic_operation_replay': False, 'automatic_non_success_closure': False,
        'recovery_requires_native_confirmation': True, 'unknown_record_is_not_promoted_to_pass': True}
    if file_policy != file_expected:
        errors.append('File environment compatibility must retain integrity, evidence and recovery boundaries')
    for key in ('policy/simple-flow.json', 'policy/workflow.json', 'policy/onboarding.json', 'INDEX.json'):
        if documents[key].get('file_environment_policy') != 'policy/file-environment.json':
            errors.append('Workflow must reference file environment policy: ' + key)
    errors.extend(validate_input_material_policy(documents.get('policy/input-material.json')))
    errors.extend(validate_closure_policy(documents.get('policy/closure.json')))
    for key in ('policy/simple-flow.json', 'policy/workflow.json'):
        errors.extend(validate_closure_policy(documents[key].get('non_success_closure')))
    if package.get('reqpilot', {}).get('bootstrap_abi') != 12:
        errors.append('Native plugin requires the reviewed bootstrap ABI')
    for key in ('policy/simple-flow.json', 'policy/workflow.json', 'policy/onboarding.json', 'INDEX.json'):
        if documents[key].get('execution_scope_policy') != 'policy/execution-scope.json':
            errors.append('Workflow must reference execution scope and progress supervision: ' + key)
        if documents[key].get('update_policy') != 'policy/updates.json':
            errors.append('Workflow must reference automatic update policy: ' + key)
        if documents[key].get('task_upgrade_policy') != 'policy/task-upgrades.json':
            errors.append('Workflow must reference task upgrade policy: ' + key)
        if documents[key].get('initialization_recovery_policy') != 'policy/initialization-recovery.json':
            errors.append('Workflow must reference initialization recovery policy: ' + key)
    if package.get('version') != version or package.get('dsh', {}).get('bundle', {}).get('patch') != './harness/cordis.patch.yml':
        errors.append('Native plugin package/version mismatch')
    for key in ('policy/simple-flow.json', 'policy/workflow.json'):
        if documents[key].get('onboarding_policy') != 'policy/onboarding.json':
            errors.append('Workflow must reference onboarding policy')
    for key in ('policy/simple-flow.json', 'policy/workflow.json', 'policy/onboarding.json'):
        errors.extend(validate_clarification_policy(documents[key].get('clarification_policy')))
        if documents[key].get('planning_policy') != 'policy/planning.json':
            errors.append('Workflow must reference planning policy')
    errors.extend(validate_planning_policy(documents.get('policy/planning.json')))
    errors.extend(validate_resource_policy(documents.get('policy/resources.json')))
    for key in ('policy/simple-flow.json', 'policy/workflow.json', 'policy/onboarding.json', 'INDEX.json'):
        if documents[key].get('resource_policy') != 'policy/resources.json':
            errors.append('Workflow must reference resource governance: ' + key)
    for key in ('templates/harness-start.example.json', 'templates/l1-delegation.example.json'):
        if documents[key].get('adaptive_limits') is not True:
            errors.append('New task templates must enable adaptive resource governance: ' + key)
    if onboarding.get('states_before_runtime') != ['DISCOVERY', 'PREPARED', 'START_CONFIRMED', 'NEEDS_INFO', 'PLAN_REJECTED', 'REQUIREMENTS_PENDING', 'REQUIREMENTS_APPROVED', 'DECISION_PENDING']:
        errors.append('Onboarding must distinguish recoverable planning results from UNKNOWN/final reports')
    if simple.get('startup_confirmation_required') is not True:
        errors.append('Consolidated startup confirmation is required')
    if simple.get('communication_policy') != 'policy/communication.json' or policy.get('communication_policy') != 'policy/communication.json':
        errors.append('Default and detailed workflows must reference the same communication policy')
    if not (root / 'runtime/dot_runtime').is_dir():
        errors.append('Advertised local runtime implementation is missing')
    for name in ('project-context', 'task-draft'):
        template = documents[f'templates/{name}.json']
        if template.get('status') != 'DRAFT':
            errors.append(f'{name}: template must remain DRAFT')
    try:
        manifest = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
        errors.extend(validate_manifest(root, manifest))
    except (OSError, ValueError) as exc:
        errors.append(f'MANIFEST unavailable: {exc}; maintainers may use --refresh-manifest after review')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-manifest', action='store_true', help='Record reviewed file hashes; does not approve content')
    args = parser.parse_args()
    if args.refresh_manifest:
        (ROOT / 'MANIFEST.json').write_text(json.dumps(make_manifest(ROOT), ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    errors = validate_repository(ROOT)
    if errors:
        for error in errors:
            print(f'ERROR: {error}')
        return 1
    print(f'STATIC_SPEC_CHECKS_PASS: {len(repository_files(ROOT))} files; 21 chapters; 16 L4 + 12 L5 scenarios.')
    print('Embedded C V1.3 / Huawei reference: 33 mapped rules, 18 sections and provenance verified; not product C compliance.')
    print('Huawei catalog: 139 numbered + 6 governance + 50 reviewed subitems; no product acceptance claim.')
    print('Doxygen reference: 12 adoption decisions + 33 subrequirements; Huawei precedence and pinned provenance verified.')
    print('48 product/HIL acceptance scenarios remain NOT_RUN. No execution, approval or delivery authority is granted.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

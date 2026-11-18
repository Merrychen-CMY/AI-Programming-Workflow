"""Static regression checks for the simplified interface and authority boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_repo import validate_simple_flow, validate_capabilities


class SimpleFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = json.loads((ROOT / 'policy/simple-flow.json').read_text(encoding='utf-8'))

    def reject(self, path, value):
        flow = copy.deepcopy(self.flow)
        keys = path.split('.')
        parent = flow
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
        self.assertTrue(validate_simple_flow(flow), path)

    def test_default_simple_flow_valid(self):
        self.assertEqual(validate_simple_flow(self.flow), [])

    def test_implementation_requires_l1_approval_without_extra_human_checkpoint(self):
        self.reject('implementation_requires_user_approval', True)
        self.reject('requirements_require_l1_approval', False)

    def test_routine_work_does_not_add_user_approval(self):
        self.reject('routine_in_scope_work_requires_new_user_approval', True)
        self.reject('internal_verification_plan_requires_separate_user_document', True)

    def test_simplification_cannot_remove_independent_verification(self):
        self.reject('independent_verification_required', False)
        self.reject('implementation_worker_can_issue_final_acceptance', True)

    def test_dot_cannot_self_authorize_or_bypass_existing_protection(self):
        self.reject('delivery.DOT_may_self_authorize', True)
        self.reject('existing_project_protections_remain_required', False)

    def test_preauthorization_is_not_blanket_delivery_permission(self):
        self.reject('delivery.check_actual_candidate_evidence_action_target', False)
        self.reject('delivery.preauthorization_must_bind', ['actions'])
        self.reject('delivery.require_new_approval_when_scope_not_covered', False)

    def test_ac_changes_and_lowered_standards_are_not_internal_repairs(self):
        self.reject('product_or_ac_change_requires_user_decision', True)
        self.reject('product_or_ac_change_requires_l1_decision', False)
        self.reject('plan_maintenance.may_lower_acceptance_standard', True)

    def test_verifier_still_returns_to_dot_before_repair_dispatch(self):
        self.reject('issue_intake', 'L3')
        self.reject('direct_verifier_to_developer_dispatch', True)

    def test_dangling_state_transition_is_rejected(self):
        states = copy.deepcopy(self.flow['states'])
        states[0]['next'] = ['MISSING_STATE']
        self.reject('states', states)

    def test_requirements_cannot_replace_execution_plan_review(self):
        self.reject('requirements_approval_allows_implementation', True)
        self.reject('execution_plan_requires_user_approval', True)
        self.reject('execution_plan_requires_l1_approval', False)

    def test_final_report_review_is_required_on_success_and_failure(self):
        self.reject('final_report_requires_user_approval', False)
        self.reject('user_visible_outputs', ['requirements_for_review', 'execution_plan_for_review'])
        self.reject('failed_report_approval_resumes_execution', True)

    def test_developer_test_and_review_have_distinct_sessions(self):
        self.reject('independent_review_required', False)
        self.reject('distinct_worker_sessions', ['developer', 'tester'])

    def test_local_runtime_cannot_claim_authenticated_or_automatic_approval(self):
        self.reject('agent_may_enter_user_approval', True)
        self.reject('runtime_approval_trust_model', 'authenticated_approval_service')

    def test_delivery_requires_report_review_and_actual_receipt(self):
        self.reject('delivery.requires_approved_success_report', False)
        self.reject('delivery.preauthorization_may_bypass_report_review', True)
        self.reject('delivery.delivered_requires_external_receipt', False)
        self.reject('delivery.local_runtime_executes_delivery', True)

    def test_replan_does_not_reset_cumulative_budget(self):
        self.reject('budgets_reset_on_restart_or_replan', True)

    def test_material_plan_change_requires_bounded_l1_review(self):
        self.reject('plan_maintenance.semantic_change', 'internal_owner_approval_only')

    def reject_edge(self, source, target):
        states = copy.deepcopy(self.flow['states'])
        next(state for state in states if state['id'] == source)['next'].append(target)
        self.reject('states', states)

    def test_known_state_bypass_edges_are_rejected(self):
        for source, target in [
            ('DISCOVERY', 'EXECUTING'),
            ('REVIEW_PENDING', 'EXECUTING'),
            ('PLAN_PENDING', 'VERIFYING'),
            ('EXECUTING', 'DELIVERED'),
            ('VERIFYING', 'DELIVERY_READY'),
            ('REPORT_PENDING', 'DELIVERED'),
            ('REPORT_PENDING', 'EXECUTING'),
            ('BLOCKED', 'EXECUTING'),
        ]:
            with self.subTest(source=source, target=target):
                self.reject_edge(source, target)

    def test_missing_duplicate_and_malformed_edges_are_rejected(self):
        for targets in [[], ['REVIEW_PENDING', 'REVIEW_PENDING'], 'REVIEW_PENDING', [None]]:
            states = copy.deepcopy(self.flow['states'])
            states[0]['next'] = targets
            self.reject('states', states)

    def test_review_states_and_guard_cannot_be_removed(self):
        for name in ['PLAN_PENDING', 'REPORT_PENDING']:
            states = [s for s in self.flow['states'] if s['id'] != name]
            self.reject('states', states)
        states = copy.deepcopy(self.flow['states'])
        states[0]['guard'] = ' '
        self.reject('states', states)

    def test_capability_claims_match_local_runtime_limits(self):
        index = json.loads((ROOT / 'INDEX.json').read_text(encoding='utf-8'))
        self.assertEqual(validate_capabilities(index), [])
        for unsupported in ['real_approval_service', 'product_tests_executed', 'hil_tests_executed', 'delivery_executor']:
            changed = copy.deepcopy(index)
            changed['capabilities'][unsupported] = True
            self.assertTrue(validate_capabilities(changed), unsupported)
        changed = copy.deepcopy(index)
        changed['runtime_scope']['approval_trust_model'] = 'authenticated'
        self.assertTrue(validate_capabilities(changed))


if __name__ == '__main__':
    unittest.main()

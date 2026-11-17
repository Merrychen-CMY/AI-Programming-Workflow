"""Static regression checks for the simplified interface and authority boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_repo import validate_simple_flow


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

    def test_implementation_cannot_start_before_user_approval(self):
        self.reject('implementation_requires_user_approval', False)

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
        self.reject('product_or_ac_change_requires_user_decision', False)
        self.reject('plan_maintenance.may_lower_acceptance_standard', True)

    def test_verifier_still_returns_to_dot_before_repair_dispatch(self):
        self.reject('issue_intake', 'L3')
        self.reject('direct_verifier_to_developer_dispatch', True)

    def test_dangling_state_transition_is_rejected(self):
        states = copy.deepcopy(self.flow['states'])
        states[0]['next'] = ['MISSING_STATE']
        self.reject('states', states)


if __name__ == '__main__':
    unittest.main()

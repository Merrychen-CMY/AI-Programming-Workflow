"""Regression checks for embedded C adoption, not product code tests."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_repo import validate_embedded_c_policy


class EmbeddedCIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / 'policy/embedded-c.json').read_text(encoding='utf-8'))
        cls.rules = json.loads((ROOT / cls.policy['source_index']).read_text(encoding='utf-8'))['rules']

    def reject(self, path, value):
        policy = copy.deepcopy(self.policy)
        keys = path.split('.')
        parent = policy
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
        self.assertTrue(validate_embedded_c_policy(policy, self.rules), path)

    def test_complete_source_mapping(self):
        self.assertEqual(validate_embedded_c_policy(self.policy, self.rules), [])

    def test_rule_omission_or_downgrade_is_rejected(self):
        self.reject('rules', self.policy['rules'][:-1])
        rules = copy.deepcopy(self.policy['rules'])
        rules[0]['level'] = 'MAY'
        self.reject('rules', rules)

    def test_readability_is_review_not_mechanical_rewrite(self):
        self.reject('readability.thresholds_are_hard_limits', True)
        self.reject('readability.trigger_requires_recorded_review', False)
        self.reject('readability.mechanical_split_counts_as_fix', True)

    def test_original_project_phase_is_not_global(self):
        self.reject('source_ESP32_comment_only_restriction_is_global', True)

    def test_ai_source_is_not_exempt_generated_code(self):
        self.reject('AI_written_source_is_generated_code_exception', True)
        self.reject('coding.goto_allowed', True)

    def test_real_c_and_independent_oracle_remain_required(self):
        self.reject('verification.host_behavior_links_real_C', False)
        self.reject('verification.oracle_independent_of_current_output', False)

    def test_structural_success_is_not_acceptance(self):
        self.reject('verification.schema_validation_means_product_acceptance', True)
        self.reject('acceptance.FINAL_or_CLOSED_means_accepted', True)
        self.reject('acceptance.ACCEPTED_means_delivered_or_authorized', True)

    def test_risk_cannot_be_downgraded_to_avoid_tests(self):
        self.reject('risk.behavior_preserving_implementation_refactor_may_be_R0', True)
        self.reject('acceptance.required_unexecuted_checks_block_acceptance', False)

    def test_findings_still_enter_dot_triage(self):
        rules = copy.deepcopy(self.policy['rules'])
        rules[0]['finding_intake'] = 'L3'
        self.reject('rules', rules)

    def test_user_does_not_get_additional_forms(self):
        self.reject('extra_user_documents_required', True)


if __name__ == '__main__':
    unittest.main()

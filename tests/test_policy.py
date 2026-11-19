"""Negative tests for the static validator, not real product acceptance tests."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_repo import check_local_link, make_manifest, validate_manifest, validate_policy


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / 'policy/workflow.json').read_text(encoding='utf-8'))

    def test_valid_reference_policy(self):
        self.assertEqual(validate_policy(self.policy), [])

    def reject(self, path, value):
        policy = copy.deepcopy(self.policy)
        parent = policy
        keys = path.split('.')
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
        self.assertTrue(validate_policy(policy), path)

    def test_direct_l4_dispatch_is_rejected(self):
        self.reject('reflow.allow_direct_l4_l5_to_l3', True)

    def test_non_l2_intake_is_rejected(self):
        self.reject('reflow.intake_owner', 'L3')

    def test_non_l2_triage_is_rejected(self):
        self.reject('reflow.triage_owner', 'L4')

    def test_test_contract_and_required_obligations_cannot_be_relaxed(self):
        self.reject('candidate_verification.require_test_contract', False)
        self.reject('candidate_verification.require_actual_product_provenance', False)
        self.reject('candidate_verification.required_test_skip_passes', True)

    def test_wrong_root_cause_routes_are_rejected(self):
        for kind in ('implementation', 'product_requirement', 'task_plan_or_design',
                     'verification_plan_or_test', 'test_infrastructure', 'delivery_target_or_authorization'):
            with self.subTest(kind=kind):
                self.reject(f'reflow.routes.{kind}.owner', 'WRONG_OWNER')

    def test_old_candidate_pass_reuse_is_rejected(self):
        self.reject('candidate_rules.reuse_pass_across_candidates', True)

    def test_mutable_candidate_is_rejected(self):
        self.reject('candidate_rules.immutable', False)

    def test_missing_changed_input_is_rejected(self):
        self.reject('candidate_rules.new_id_on', ['product_code'])

    def test_g3_closure_cycle_is_rejected(self):
        self.reject('closure.G3_requires_issue_CLOSED', True)

    def test_delivery_self_approval_is_rejected(self):
        self.reject('approval_rules.L5_can_approve_delivery', True)

    def test_merge_is_not_release_approval(self):
        self.reject('approval_rules.merge_implies_release', True)

    def test_examples_cannot_authorize(self):
        self.reject('approval_rules.example_is_authorization', True)

    def test_restart_does_not_reset_budget(self):
        self.reject('budgets.reset_on_restart', True)

    def test_missing_dual_l1_is_rejected(self):
        self.reject('roles', ['L1', 'L2', 'L3', 'L4', 'L5'])

    def test_false_is_not_numeric_zero(self):
        self.reject('execution_authority', 0)


class RepositoryTests(unittest.TestCase):
    def test_missing_link_and_traversal_rejected_external_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'README.md'
            source.write_text('test', encoding='utf-8')
            self.assertTrue(check_local_link(root, source, 'missing.md'))
            self.assertTrue(check_local_link(root, source, '../outside.md'))
            self.assertEqual(check_local_link(root, source, 'README.md#section'), [])
            self.assertEqual(check_local_link(root, source, 'https://example.com/doc'), [])

    def test_manifest_detects_modified_added_and_removed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'VERSION').write_text('1.0.0\n', encoding='utf-8')
            file = root / 'README.md'
            file.write_text('original', encoding='utf-8')
            manifest = make_manifest(root)
            self.assertEqual(validate_manifest(root, manifest), [])
            file.write_text('changed', encoding='utf-8')
            self.assertTrue(validate_manifest(root, manifest))
            file.unlink()
            self.assertTrue(validate_manifest(root, manifest))
            file.write_text('original', encoding='utf-8')
            (root / 'extra.md').write_text('added', encoding='utf-8')
            self.assertTrue(validate_manifest(root, manifest))


if __name__ == '__main__':
    unittest.main()

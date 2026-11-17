"""Negative cases prevent catalog completeness being mistaken for product acceptance."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from huawei_coverage import BASE, validate_coverage, check_repository_coverage


class HuaweiCoverageTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / BASE / 'coverage.json').read_text(encoding='utf-8'))
        self.inventory = json.loads((ROOT / BASE / 'source-inventory.json').read_text(encoding='utf-8'))
        self.rules = {r['id'] for r in json.loads((ROOT / 'policy/embedded-c.json').read_text(encoding='utf-8'))['rules']}

    def row(self, identity):
        return next(r for r in self.data['clauses'] if r['id'] == identity)

    def rejected(self):
        self.assertTrue(validate_coverage(self.data, self.inventory, self.rules))

    def test_complete_catalog_and_generated_views(self):
        self.assertEqual(check_repository_coverage(ROOT, self.rules), [])

    def test_missing_suggestion_is_not_full_coverage(self):
        self.data['clauses'].remove(self.row('HW-S-2.5'))
        self.rejected()

    def test_duplicate_cannot_replace_missing_clause(self):
        self.data['clauses'][-1] = copy.deepcopy(self.data['clauses'][-2])
        self.rejected()

    def test_fabricated_number_is_rejected(self):
        self.row('HW-R-1.1')['id'] = 'HW-R-1.9'
        self.rejected()

    def test_editing_inventory_together_cannot_hide_missing_clause(self):
        identity = 'HW-S-4.4'
        self.data['clauses'] = [r for r in self.data['clauses'] if r['id'] != identity]
        self.inventory['entries'] = [r for r in self.inventory['entries'] if r['id'] != identity]
        self.rejected()

    def test_governance_is_not_omitted(self):
        self.data['clauses'].remove(self.row('HW-G-0.3-exceptions'))
        self.rejected()

    def test_rule_cannot_be_downgraded(self):
        self.row('HW-R-13.1')['project_level'] = 'SHOULD'
        self.rejected()

    def test_suggestion_cannot_be_silently_strengthened(self):
        self.row('HW-S-2.5')['project_level'] = 'MUST'
        self.rejected()

    def test_existing_goto_override_keeps_project_basis(self):
        self.row('HW-S-6.3')['adoption'] = 'ADOPTED'
        self.rejected()

    def test_source_kind_and_page_are_traceable(self):
        for field, value in [('source_kind','建议'),('source_page',61)]:
            with self.subTest(field=field):
                row = self.row('HW-R-1.1')
                previous = row[field]
                row[field] = value
                self.rejected()
                row[field] = previous

    def test_wrong_pdf_is_rejected(self):
        self.data['source_sha256'] = '0' * 64
        self.rejected()

    def test_empty_requirement_applicability_check_or_delta_rejected(self):
        for field in ('project_requirement','applicability','verification','difference'):
            with self.subTest(field=field):
                row = self.row('HW-S-10.2')
                previous = row[field]
                row[field] = ' '
                self.rejected()
                row[field] = previous

    def test_unknown_project_rule_rejected(self):
        self.row('HW-S-2.5')['project_rule_ids'] = ['NOT-A-RULE']
        self.rejected()

    def test_adaptation_requires_actual_difference(self):
        self.row('HW-S-6.3')['difference'] = '保留原条款意图与强度；按本清单实施。'
        self.rejected()

    def test_catalog_adoption_is_not_pass(self):
        self.row('HW-R-1.1')['adoption'] = 'PASS'
        self.rejected()

    def test_catalog_cannot_claim_product_execution(self):
        for key in ('product_compliance_claimed','product_checks_executed'):
            with self.subTest(key=key):
                self.data[key] = True
                self.rejected()
                self.data[key] = False

    def test_na_and_omission_need_explicit_handling(self):
        for key in ('scope_resolution_required','not_applicable_requires_reason','omission_is_not_pass'):
            with self.subTest(key=key):
                self.data[key] = False
                self.rejected()
                self.data[key] = True

    def test_l4_cannot_dispatch_directly_to_l3(self):
        self.row('HW-R-1.1')['finding_intake'] = 'L3'
        self.rejected()

    def test_independent_verification_cannot_be_self_test(self):
        self.row('HW-R-1.1')['independent_verification_owner'] = 'L3'
        self.rejected()

    def test_normative_pointer_cannot_drift(self):
        self.row('HW-R-1.1')['requirement_ref'] = 'standards/embedded-c/v1.2/source/standard.txt'
        self.rejected()

    def test_stale_markdown_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / BASE, root / BASE)
            (root / BASE / '02.md').write_text('Stale view\n', encoding='utf-8')
            self.assertTrue(check_repository_coverage(root, self.rules))


if __name__ == '__main__':
    unittest.main()

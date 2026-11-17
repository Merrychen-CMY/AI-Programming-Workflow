"""Guard provenance, optional strengths and generated reference consistency."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from doxygen_reference import BASE, HUAWEI, check_doxygen_reference, render_readme, validate_reference


class DoxygenReferenceTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / BASE / 'adoption.json').read_text(encoding='utf-8'))
        self.source = json.loads((ROOT / BASE / 'source.json').read_text(encoding='utf-8'))
        self.huawei = {r['id'] for r in json.loads((ROOT / HUAWEI).read_text(encoding='utf-8'))['clauses']}

    def errors(self):
        return validate_reference(self.data, self.source, self.huawei)

    def test_reviewed_catalog_and_rendered_view_pass(self):
        self.assertEqual(check_doxygen_reference(ROOT), [])

    def test_pin_cannot_float_to_master(self):
        self.source['pinned_commit'] = 'master'
        self.assertTrue(self.errors())

    def test_all_three_source_hashes_are_fixed(self):
        for record in self.source['read_files']:
            with self.subTest(file=record['path']):
                old = record['sha256']
                record['sha256'] = '0' * 64
                self.assertTrue(self.errors())
                record['sha256'] = old

    def test_duplicate_file_cannot_replace_license(self):
        self.source['read_files'][1] = copy.deepcopy(self.source['read_files'][0])
        self.assertTrue(self.errors())

    def test_mit_notice_cannot_be_silently_trimmed(self):
        self.source['license']['notice'] = 'MIT License\nCopyright (c) 2025 viys\n'
        self.assertTrue(self.errors())

    def test_source_url_must_identify_reviewed_revision(self):
        self.source['read_files'][0]['url'] = 'https://github.com/viys/doxygen_stu/blob/master/readme.md'
        self.assertTrue(self.errors())

    def test_twelve_rules_cannot_hide_an_omission_with_duplicate(self):
        self.data['rules'][-1] = copy.deepcopy(self.data['rules'][-2])
        self.assertTrue(self.errors())

    def test_thirty_three_children_cannot_hide_an_omission_with_duplicate(self):
        children = self.data['rules'][2]['requirements']
        children[-1] = copy.deepcopy(children[-2])
        self.assertTrue(self.errors())

    def test_must_cannot_be_downgraded(self):
        self.data['rules'][0]['requirements'][0]['project_level'] = 'SHOULD'
        self.assertTrue(self.errors())

    def test_optional_parent_and_every_may_child_cannot_be_upgraded(self):
        optional = [r for r in self.data['rules'] if r['project_level'] == 'MAY']
        optional += [c for r in self.data['rules'] for c in r['requirements'] if c['project_level'] == 'MAY']
        self.assertEqual(len(optional), 5)
        for row in optional:
            for level in ('MUST', 'SHOULD'):
                with self.subTest(id=row['id'], level=level):
                    row['project_level'] = level
                    self.assertTrue(self.errors())
                    row['project_level'] = 'MAY'

    def test_should_cannot_be_silently_upgraded(self):
        child = self.data['rules'][3]['requirements'][1]
        self.assertEqual(child['project_level'], 'SHOULD')
        child['project_level'] = 'MUST'
        self.assertTrue(self.errors())

    def test_child_applicability_and_verification_are_required(self):
        child = self.data['rules'][10]['requirements'][2]
        for key in ('requirement', 'applicability', 'verification'):
            old = child[key]
            child[key] = ' '
            with self.subTest(field=key):
                self.assertTrue(self.errors())
            child[key] = old

    def test_unknown_huawei_reference_is_rejected(self):
        self.data['rules'][0]['huawei_refs'].append('HW-R-99.1')
        self.assertTrue(self.errors())

    def test_huawei_priority_cannot_gain_an_adaptation_override(self):
        self.data['precedence'] += ' Existing project adaptations take priority.'
        self.assertTrue(self.errors())

    def test_source_priority_cannot_be_lost(self):
        self.source['huawei_precedence']['decision'] = '两套规范冲突时由实现者选择。'
        self.assertTrue(self.errors())

    def test_source_line_and_tag_inventory_are_checked(self):
        self.data['rules'][0]['source_lines']['start'] = 1
        self.assertTrue(self.errors())
        self.data['rules'][0]['source_lines']['start'] = 9
        self.data['tags'] = [tag for tag in self.data['tags'] if tag['tag'] != '@endcode']
        self.assertTrue(self.errors())

    def test_product_pass_and_execution_claims_are_rejected(self):
        for field in ('product_compliance_claimed', 'product_checks_executed'):
            with self.subTest(field=field):
                self.data[field] = True
                self.assertTrue(self.errors())
                self.data[field] = False
        self.data['rules'][0]['result'] = 'PASS'
        self.assertTrue(self.errors())

    def test_no_additional_form_or_automatic_semantic_proof(self):
        self.data['workflow']['extra_user_form_required'] = True
        self.assertTrue(self.errors())
        self.data['workflow']['extra_user_form_required'] = False
        self.data['verification_boundary']['all_rules_automatically_checked'] = True
        self.assertTrue(self.errors())

    def test_malformed_shapes_fail_with_errors_instead_of_crashing(self):
        for target, key, value in ((self.data, 'rules', None),
                                   (self.source['read_files'][0], 'path', []),
                                   (self.source, 'official_documentation', [{}])):
            with self.subTest(key=key):
                old = target[key]
                target[key] = value
                self.assertTrue(self.errors())
                target[key] = old

    def fixture_root(self, directory):
        root = Path(directory)
        for relative in (BASE / 'adoption.json', BASE / 'source.json', BASE / 'README.md', HUAWEI):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        return root

    def test_render_is_deterministic_and_manual_readme_drift_is_rejected(self):
        first = render_readme(self.data, self.source)
        self.assertEqual(first, render_readme(copy.deepcopy(self.data), copy.deepcopy(self.source)))
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture_root(directory)
            (root / BASE / 'README.md').write_text(first + '\nUnreviewed override\n', encoding='utf-8')
            self.assertTrue(any('stale' in error for error in check_doxygen_reference(root)))

    def test_json_requirement_change_requires_readme_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture_root(directory)
            self.data['rules'][0]['requirements'][0]['requirement'] += ' 补充说明。'
            (root / BASE / 'adoption.json').write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            self.assertTrue(any('stale' in error for error in check_doxygen_reference(root)))
            (root / BASE / 'README.md').write_text(render_readme(self.data, self.source), encoding='utf-8')
            self.assertEqual(check_doxygen_reference(root), [])


if __name__ == '__main__':
    unittest.main()

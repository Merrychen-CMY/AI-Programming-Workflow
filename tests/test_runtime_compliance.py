"""Exercise real clause evidence gates using explicit synthetic review artifacts."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from runtime.dot_runtime.compliance import catalog_digest, validate_compliance


ROOT = Path(__file__).resolve().parents[1]


class ComplianceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = json.loads((ROOT / 'standards/embedded-c/v1.3/huawei/coverage.json').read_text(encoding='utf-8'))
        content = b'SYNTHETIC protocol-test evidence; not a product acceptance report.\n'
        (self.root / 'review.txt').write_bytes(content)
        self.evidence = {'path': 'review.txt', 'sha256': hashlib.sha256(content).hexdigest()}
        self.record = {
            'standard_version': self.catalog['standard_version'],
            'coverage_revision': self.catalog['coverage_revision'],
            'catalog_sha256': catalog_digest(self.catalog),
            'candidate_id': 'candidate-1', 'implementation_session_id': 'implementer-1',
            'clauses': [self.fixture_row(c) for c in self.catalog['clauses']],
        }

    def fixture_row(self, clause):
        row = {
            'id': clause['id'], 'applicability': 'APPLICABLE',
            'applicability_reason': 'Synthetic fixture scope includes this requirement.',
            'result': 'PASS', 'verification_method': 'Independent synthetic review',
            'verifier_session_id': 'reviewer-1', 'evidence': [dict(self.evidence)],
            'reason': None, 'exception_approval_id': None,
        }
        if clause.get('subrequirements'):
            row['subrequirements'] = [self.fixture_row(child) for child in clause['subrequirements']]
        return row

    def add_fixture_subrequirements(self):
        # Independent of the real catalog revision or its selected parent IDs.
        self.catalog = copy.deepcopy(self.catalog)
        parent = next(c for c in self.catalog['clauses'] if c['id'] == 'HW-R-13.9')
        parent['subrequirements'] = [{
            'id': parent['id'] + suffix, 'source_page': 59,
            'source_basis': 'exception', 'project_level': level,
            'requirement': 'Synthetic child requirement used only to exercise the gate.',
            'applicability': 'Synthetic scope.', 'verification': 'Synthetic review.',
            'source_note': 'Synthetic protocol fixture, not a source interpretation.',
        } for suffix, level in (('.SUB-91', 'MUST'), ('.SUB-92', 'SHOULD'))]
        self.row(parent['id'])['subrequirements'] = [self.fixture_row(c) for c in parent['subrequirements']]
        self.record['catalog_sha256'] = catalog_digest(self.catalog)
        return self.row(parent['id'])['subrequirements']

    def validate(self, **kwargs):
        return validate_compliance(self.record, self.catalog, self.root,
                                   'candidate-1', 'implementer-1',
                                   trusted_verifier_sessions=kwargs.pop('trusted_verifier_sessions', {'reviewer-1'}),
                                   **kwargs)

    def row(self, identity):
        return next(r for r in self.record['clauses'] if r['id'] == identity)

    def test_complete_bound_independent_evidence_passes(self):
        self.assertEqual(self.validate(), [])

    def test_complete_parent_and_child_results_pass(self):
        self.add_fixture_subrequirements()
        self.assertEqual(self.validate(), [])

    def test_parent_pass_does_not_imply_children_pass(self):
        self.add_fixture_subrequirements()
        del self.row('HW-R-13.9')['subrequirements']
        self.assertTrue(any('missing subrequirement' in e for e in self.validate()))

    def test_duplicate_child_cannot_replace_missing_child(self):
        children = self.add_fixture_subrequirements()
        children[-1] = copy.deepcopy(children[0])
        errors = self.validate()
        self.assertTrue(any('duplicate' in e for e in errors))
        self.assertTrue(any('missing subrequirement' in e for e in errors))

    def test_child_cannot_be_invented_or_moved_to_another_parent(self):
        children = self.add_fixture_subrequirements()
        original = children[0]['id']
        children[0]['id'] = 'HW-R-13.9.SUB-99'
        self.assertTrue(any('unknown or misplaced' in e for e in self.validate()))
        children[0]['id'] = original
        self.row('HW-R-9.1').setdefault('subrequirements', []).append(children.pop(0))
        errors = self.validate()
        self.assertTrue(any('unknown or misplaced' in e for e in errors))
        self.assertTrue(any('missing subrequirement' in e for e in errors))

    def test_subrequirement_cannot_be_reported_as_a_top_level_clause(self):
        children = self.add_fixture_subrequirements()
        self.record['clauses'].append(children.pop())
        self.assertTrue(any('parent clause' in e for e in self.validate()))

    def test_child_unexecuted_results_block_even_with_parent_pass(self):
        children = self.add_fixture_subrequirements()
        for outcome in ('NOT_RUN', 'FAIL', 'BLOCKED', 'ERROR', 'SKIP', 'APPROVED'):
            with self.subTest(outcome=outcome):
                children[0]['result'] = outcome
                self.assertTrue(any(children[0]['id'] in e for e in self.validate()))

    def test_child_recommendation_deviation_requires_reason(self):
        children = self.add_fixture_subrequirements()
        children[1].update(result='DEVIATION', reason='Synthetic justified choice within the recommendation.')
        self.assertEqual(self.validate(), [])
        children[1]['reason'] = ''
        self.assertTrue(self.validate())
        children[1]['reason'] = 'Reason restored.'
        children[0].update(result='DEVIATION', reason='Cannot downgrade a mandatory child.')
        self.assertTrue(self.validate())

    def test_each_child_needs_independent_identity_and_actual_evidence(self):
        children = self.add_fixture_subrequirements()
        for field, value in (('verifier_session_id', 'implementer-1'),
                             ('verifier_session_id', 'invented-reviewer'),
                             ('evidence', []), ('applicability', 'UNASSESSED'),
                             ('applicability_reason', ''), ('verification_method', '')):
            with self.subTest(field=field, value=value):
                previous = children[0][field]
                children[0][field] = value
                self.assertTrue(self.validate())
                children[0][field] = previous

    def test_child_nonapplicability_is_separately_assessed(self):
        children = self.add_fixture_subrequirements()
        children[0].update(applicability='NOT_APPLICABLE', result='NOT_APPLICABLE',
                           applicability_reason='Synthetic target lacks this child condition.')
        self.assertEqual(self.validate(), [])
        children[0]['result'] = 'PASS'
        self.assertTrue(self.validate())

    def test_nonapplicable_parent_cannot_hide_applicable_child(self):
        self.add_fixture_subrequirements()
        self.row('HW-R-13.9').update(applicability='NOT_APPLICABLE', result='NOT_APPLICABLE')
        self.assertTrue(any('contradicts' in e for e in self.validate()))

    def test_nonapplicable_parent_does_not_waive_missing_children(self):
        self.add_fixture_subrequirements()
        parent = self.row('HW-R-13.9')
        parent.update(applicability='NOT_APPLICABLE', result='NOT_APPLICABLE')
        del parent['subrequirements']
        self.assertTrue(any('missing subrequirement' in e for e in self.validate()))

    def test_parent_and_children_can_each_be_independently_nonapplicable(self):
        children = self.add_fixture_subrequirements()
        parent = self.row('HW-R-13.9')
        parent.update(applicability='NOT_APPLICABLE', result='NOT_APPLICABLE')
        for child in children:
            child.update(applicability='NOT_APPLICABLE', result='NOT_APPLICABLE',
                         applicability_reason='Reviewed target lacks this specific child condition.')
        self.assertEqual(self.validate(), [])
        children[0]['evidence'] = []
        self.assertTrue(self.validate())

    def test_mandatory_child_exception_requires_its_own_bound_receipt(self):
        child = self.add_fixture_subrequirements()[0]
        child.update(result='EXCEPTION', reason='Synthetic independently approved deviation.',
                     exception_approval_id='child-approval', approved_by='user', decision='APPROVED')
        self.assertTrue(self.validate())
        receipt = {'decision': 'APPROVED', 'approval_id': 'child-approval',
                   'approved_by': 'authenticated-user', 'clause_id': child['id'],
                   'candidate_id': self.record['candidate_id'],
                   'standard_version': self.record['standard_version'],
                   'coverage_revision': self.record['coverage_revision'],
                   'catalog_sha256': self.record['catalog_sha256']}
        self.assertEqual(self.validate(approved_exceptions={'child-approval': receipt}), [])
        for field, value in (('clause_id', 'HW-R-13.9'), ('candidate_id', 'old-candidate'),
                             ('catalog_sha256', '0' * 64)):
            with self.subTest(field=field):
                wrong = dict(receipt, **{field: value})
                self.assertTrue(self.validate(approved_exceptions={'child-approval': wrong}))

    def test_parent_without_catalog_children_allows_omitted_or_empty_array(self):
        parent = next(c for c in self.catalog['clauses'] if not c.get('subrequirements'))
        row = self.row(parent['id'])
        row.pop('subrequirements', None)
        self.assertEqual(self.validate(), [])
        row['subrequirements'] = []
        self.assertEqual(self.validate(), [])
        row['subrequirements'] = None
        self.assertTrue(self.validate())

    def test_malformed_catalog_child_blocks_instead_of_trusting_report(self):
        self.add_fixture_subrequirements()
        parent = next(c for c in self.catalog['clauses'] if c['id'] == 'HW-R-13.9')
        parent['subrequirements'][0]['project_level'] = 'OPTIONAL'
        self.record['catalog_sha256'] = catalog_digest(self.catalog)
        self.assertTrue(any('invalid subrequirement' in e for e in self.validate()))

    def test_omitted_clause_blocks(self):
        self.record['clauses'].pop()
        self.assertTrue(any('missing clause' in e for e in self.validate()))

    def test_duplicate_cannot_replace_omitted_clause(self):
        self.record['clauses'][-1] = copy.deepcopy(self.record['clauses'][0])
        errors = self.validate()
        self.assertTrue(any('duplicate' in e for e in errors))
        self.assertTrue(any('missing clause' in e for e in errors))

    def test_unknown_clause_blocks(self):
        self.record['clauses'][0]['id'] = 'HW-R-99.1'
        self.assertTrue(self.validate())

    def test_actual_indent_doxygen_header_rules_reject_unexecuted_results(self):
        for identity in ('HW-R-9.1', 'HW-S-8.3', 'HW-R-1.1'):
            for result in ('NOT_RUN', 'FAIL', 'BLOCKED', 'ERROR', 'SKIP', 'APPROVED'):
                with self.subTest(identity=identity, result=result):
                    self.row(identity)['result'] = result
                    self.assertTrue(self.validate())
            self.row(identity)['result'] = 'PASS'

    def test_unassessed_applicability_blocks(self):
        self.row('HW-R-9.1')['applicability'] = 'UNASSESSED'
        self.assertTrue(self.validate())

    def test_nonapplicable_requires_reason_and_review_evidence(self):
        row = self.row('HW-R-13.10')
        row.update(applicability='NOT_APPLICABLE', result='NOT_APPLICABLE',
                   applicability_reason='The reviewed firmware has no command execution interface.')
        self.assertEqual(self.validate(), [])
        row['applicability_reason'] = ''
        self.assertTrue(self.validate())
        row['applicability_reason'] = 'No command execution interface.'
        row['evidence'] = []
        self.assertTrue(self.validate())

    def test_nonapplicable_is_not_pass(self):
        self.row('HW-R-13.10')['applicability'] = 'NOT_APPLICABLE'
        self.assertTrue(self.validate())

    def test_recommendation_deviation_is_assessed_not_ignored(self):
        row = self.row('HW-S-2.5')
        row.update(result='DEVIATION', reason='Reviewed compatibility requirement retains a typed wrapper around the existing varargs ABI.')
        self.assertEqual(self.validate(), [])
        row['reason'] = ''
        self.assertTrue(self.validate())

    def test_strengthened_doxygen_suggestion_cannot_use_should_deviation(self):
        self.row('HW-S-8.3').update(result='DEVIATION', reason='Developer prefers no documentation.')
        self.assertTrue(self.validate())

    def test_stale_candidate_or_catalog_blocks(self):
        for key, value in [('candidate_id', 'old-candidate'), ('catalog_sha256', '0' * 64),
                           ('coverage_revision', 0), ('standard_version', '1.2')]:
            with self.subTest(key=key):
                old = self.record[key]
                self.record[key] = value
                self.assertTrue(self.validate())
                self.record[key] = old

    def test_missing_modified_and_forged_evidence_blocks(self):
        row = self.row('HW-R-9.1')
        row['evidence'][0]['sha256'] = '0' * 64
        self.assertTrue(self.validate())
        row['evidence'][0] = dict(self.evidence)
        (self.root / 'review.txt').write_text('changed', encoding='utf-8')
        self.assertTrue(self.validate())
        (self.root / 'review.txt').unlink()
        self.assertTrue(self.validate())

    def test_path_escape_absolute_and_windows_drive_rejected(self):
        for name in ('../review.txt', '/review.txt', 'C:/review.txt', 'C:review.txt', '..\\review.txt'):
            with self.subTest(path=name):
                self.row('HW-R-9.1')['evidence'][0]['path'] = name
                self.assertTrue(self.validate())

    def test_evidence_symlink_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'outside.txt'
            target.write_text('outside', encoding='utf-8')
            try:
                (self.root / 'escape.txt').symlink_to(target)
            except OSError:
                self.skipTest('Host does not permit creation of symbolic links')
            self.row('HW-R-9.1')['evidence'] = [{'path': 'escape.txt', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}]
            self.assertTrue(self.validate())

    def test_claimed_independence_needs_registered_session(self):
        self.row('HW-R-9.1')['verifier_session_id'] = 'made-up-reviewer'
        self.assertTrue(self.validate())
        self.row('HW-R-9.1')['verifier_session_id'] = 'implementer-1'
        self.assertTrue(self.validate())

    def test_no_trusted_registry_fails_closed(self):
        self.assertTrue(self.validate(trusted_verifier_sessions=None))

    def test_report_cannot_approve_own_exception(self):
        self.row('HW-R-1.1').update(result='EXCEPTION', reason='Private translation unit.',
                                   exception_approval_id='approval-1', decision='APPROVED', approved_by='user')
        self.assertTrue(self.validate())

    def test_exception_receipt_binds_clause_candidate_and_catalog(self):
        self.row('HW-R-1.1').update(result='EXCEPTION', reason='Approved exceptional private translation unit.',
                                   exception_approval_id='approval-1')
        receipt = {'decision': 'APPROVED', 'approval_id': 'approval-1', 'approved_by': 'authenticated-user',
                   'clause_id': 'HW-R-1.1', 'candidate_id': 'candidate-1',
                   'standard_version': self.record['standard_version'],
                   'coverage_revision': self.record['coverage_revision'],
                   'catalog_sha256': self.record['catalog_sha256']}
        self.assertEqual(self.validate(approved_exceptions={'approval-1': receipt}), [])
        for key, value in [('candidate_id', 'old-candidate'), ('clause_id', 'HW-R-8.2'),
                           ('coverage_revision', 0), ('approved_by', 'implementer-1')]:
            with self.subTest(key=key):
                stale = dict(receipt, **{key: value})
                self.assertTrue(self.validate(approved_exceptions={'approval-1': stale}))

    def test_waived_violation_cannot_be_labeled_pass(self):
        self.row('HW-R-1.1')['exception_approval_id'] = 'approval-1'
        self.assertTrue(self.validate())

    def test_draft_template_is_not_acceptance(self):
        self.record = json.loads((ROOT / 'templates/huawei-compliance.example.json').read_text(encoding='utf-8'))
        self.assertEqual(len(self.record['clauses']), 145)
        self.assertTrue(self.validate())

    def test_malformed_evidence_and_record_fail_without_crash(self):
        for evidence in (None, 'fake', [None], [{'path': None, 'sha256': None}]):
            with self.subTest(evidence=evidence):
                self.row('HW-R-9.1')['evidence'] = evidence
                self.assertTrue(self.validate())


if __name__ == '__main__':
    unittest.main()

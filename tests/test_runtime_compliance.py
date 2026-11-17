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
            'clauses': [{
                'id': c['id'], 'applicability': 'APPLICABLE',
                'applicability_reason': 'Synthetic fixture scope includes this clause.',
                'result': 'PASS', 'verification_method': 'Independent synthetic review',
                'verifier_session_id': 'reviewer-1', 'evidence': [dict(self.evidence)],
                'reason': None, 'exception_approval_id': None,
            } for c in self.catalog['clauses']],
        }

    def validate(self, **kwargs):
        return validate_compliance(self.record, self.catalog, self.root,
                                   'candidate-1', 'implementer-1',
                                   trusted_verifier_sessions=kwargs.pop('trusted_verifier_sessions', {'reviewer-1'}),
                                   **kwargs)

    def row(self, identity):
        return next(r for r in self.record['clauses'] if r['id'] == identity)

    def test_complete_bound_independent_evidence_passes(self):
        self.assertEqual(self.validate(), [])

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

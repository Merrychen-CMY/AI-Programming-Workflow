"""Portable source adoption and runtime propagation; no live model or product attestation."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'runtime'))
from project_constraints import BASE, check_project_constraints, validate_adoption
from dot_runtime import project_constraints as runtime_constraints
from dot_runtime.delegation import attach_delegation, approve_l1
from dot_runtime.gpt_dot import propose_plan
import test_runtime_controller as fixtures
from test_delegated_l1 import delegation, call_response


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / BASE / 'adoption.json').read_text(encoding='utf-8'))
        self.source = json.loads((ROOT / BASE / 'source.json').read_text(encoding='utf-8'))

    def errors(self):
        return validate_adoption(self.data, self.source)

    def test_current_adoption_and_generated_view(self):
        self.assertEqual(check_project_constraints(ROOT), [])

    def test_omission_and_duplicate_source_id_rejected(self):
        self.data['requirements'].pop()
        self.assertTrue(self.errors())
        self.data['requirements'].append(copy.deepcopy(self.data['requirements'][-1]))
        self.assertTrue(self.errors())

    def test_source_hash_and_unread_material_claim_rejected(self):
        self.source['sha256'] = '0' * 64
        self.assertTrue(self.errors())
        self.setUp()
        self.source['not_inspected_material_does_not_add_rules'] = False
        self.assertTrue(self.errors())

    def test_huawei_cannot_be_demoted(self):
        self.data['precedence'] = 'newest_attachment_always_wins'
        self.assertTrue(self.errors())

    def test_legacy_file_allowlist_cannot_survive(self):
        for key, value in [('new_project_legacy_file_allowlist', ['old-module.c']),
                           ('legacy_scope_is_global', True)]:
            with self.subTest(key=key):
                old = self.data[key]
                self.data[key] = value
                self.assertTrue(self.errors())
                self.data[key] = old

    def test_generalization_does_not_remove_current_authorization(self):
        self.data['current_task_authorization_required'] = False
        self.assertTrue(self.errors())

    def test_task_specific_cleanup_and_delivery_do_not_become_always_required(self):
        for row in self.data['requirements']:
            if row['source_id'] in {'U04', 'U21', 'U23', 'U24', 'U25'}:
                with self.subTest(id=row['id']):
                    row['disposition'] = 'generalized'
                    self.assertTrue(self.errors())
                    row['disposition'] = 'conditional'

    def test_no_false_product_pass_or_automatic_semantic_claim(self):
        self.data['requirements'][6]['product_status'] = 'PASS'
        self.assertTrue(self.errors())
        self.setUp()
        self.data['all_rules_automatically_checked'] = True
        self.assertTrue(self.errors())

    def test_suggestions_cannot_silently_become_mandatory(self):
        self.data['recommendations'][0]['level'] = 'MUST'
        self.assertTrue(self.errors())

    def test_constraints_cannot_be_downgraded_or_made_user_forms(self):
        self.data['requirements'][6]['level'] = 'MAY'
        self.assertTrue(self.errors())
        self.setUp()
        self.data['extra_user_forms_required'] = True
        self.assertTrue(self.errors())

    def test_findings_must_enter_dot_not_implementation(self):
        self.data['finding_intake'] = 'L3'
        self.assertTrue(self.errors())

    def test_changed_machine_content_requires_readable_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / BASE, root / BASE)
            self.data['requirements'][0]['requirements'][0] += ' 新的审查说明。'
            (root / BASE / 'adoption.json').write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            self.assertTrue(check_project_constraints(root))
            self.assertEqual(check_project_constraints(root, write=True), [])


class RuntimePropagationTests(unittest.TestCase):
    setUp = fixtures.ControllerTests.setUp
    controller = fixtures.ControllerTests.controller
    approved = fixtures.ControllerTests.approved

    def test_each_c_worker_role_receives_all_rules_and_scope(self):
        c = self.approved()
        c.state['approved_plan']['coding_standard'] = 'huawei-c'
        for role in ('implementation', 'test', 'review', 'triage'):
            prompt = c.prompt(role, 'Inspect the assigned candidate')
            for index in range(1, 27):
                self.assertIn(f'PC-U{index:02}', prompt)
            self.assertIn('Huawei takes precedence', prompt)
            self.assertIn('does not expand approved paths', prompt)

    def test_non_c_worker_does_not_receive_c_format_rules(self):
        self.assertNotIn('PC-U07', self.approved().prompt('implementation', 'Python only'))

    def test_changed_rule_file_invalidates_approval_before_dispatch(self):
        directory = self.root / 'constraints'
        shutil.copytree(runtime_constraints.DIRECTORY, directory)
        with patch.object(runtime_constraints, 'DIRECTORY', directory):
            c = self.approved()
            p = directory / 'adoption.json'
            data = json.loads(p.read_text(encoding='utf-8'))
            data['requirements'][6]['requirements'][0] += ' 修改后的规范。'
            p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            state = c.run()
            self.assertEqual(state['state'], 'DECISION_PENDING')
            self.assertIn('baseline changed', state['history'][-1]['reason'])
            self.assertEqual(c.adapter.calls, [])

    def test_missing_constraints_fail_closed(self):
        with patch.object(runtime_constraints, 'DIRECTORY', self.root / 'absent'):
            with self.assertRaises(OSError):
                self.controller().baseline()

    def test_changed_policy_invalidates_frozen_candidate(self):
        policy = self.root / 'policy.json'
        shutil.copyfile(runtime_constraints.POLICY, policy)
        with patch.object(runtime_constraints, 'POLICY', policy):
            c = self.approved()
            c.make_candidate()
            policy.write_text(policy.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Candidate.*stale'):
                c.check_candidate()

    def test_l1_independent_approval_reviewer_gets_constraints(self):
        raw = self.root / 'raw.md'
        raw.write_text('Implement fixture value', encoding='utf-8')
        policy = self.root / 'delegation.json'
        fixtures.atomic_json(policy, delegation(raw))
        c = self.controller()
        attach_delegation(c, policy, raw)
        seen = []
        original = c.adapter.run
        def capture(role, prompt, workspace, output, timeout):
            seen.append(prompt)
            return original(role, prompt, workspace, output, timeout)
        with patch.object(c.adapter, 'run', side_effect=capture):
            approve_l1(c, 'requirements', 'author', c.adapter)
        self.assertIn('PC-U07', seen[0])
        self.assertIn('PC-U26', seen[0])

    def test_gpt_plan_receives_rules_without_local_file_access(self):
        raw = self.root / 'raw.md'
        raw.write_text('Implement fixture value', encoding='utf-8')
        client = Mock()
        client.delegation = delegation(raw)
        client.request.return_value = call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})
        propose_plan(client, 'Known requirements', fixtures.fixture_plan(), 'Known environment')
        self.assertIn('PC-U01', client.request.call_args.args[0])
        self.assertIn('PC-U26', client.request.call_args.args[0])


if __name__ == '__main__':
    unittest.main()

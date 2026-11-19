import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from dot_runtime import onboarding as o
from dot_runtime.controller import Controller, atomic_json, digest, read_json


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / 'product'
        self.project.mkdir()
        (self.project / 'input.txt').write_text('original', encoding='utf-8')
        self.task = self.base / 'task'
        self.settings = {'model': 'fixture-model', 'python': sys.executable, 'codex': 'fixture-codex'}
        self.draft = json.loads((ROOT / 'templates/harness-start.example.json').read_text(encoding='utf-8'))
        self.draft.pop('adaptive_limits', None)  # These fixtures retain the legacy fixed-budget contract.
        self.draft.update(project=str(self.project), raw_input='原始要求：完成输入解析，不发布。',
                          topics={key: '明确测试条件' for key in o.TOPICS}, hard_constraints=['不得发布'], allowed_paths=['.'],
                          expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
                          environment_checks=[{'name':'input','kind':'file','path':str(self.project/'input.txt'),
                                               'required':True,'evidence':'Input fixture exists'}])
        self.probe = patch.object(o, 'tool_check', return_value={'status':'PASS','exit_code':0})
        self.probe.start(); self.addCleanup(self.probe.stop)

    def request(self, action, **kwargs):
        return o.dispatch({'task':str(self.task),'session':'native-session','action':action, **kwargs})

    def prepare(self):
        environment = self.request('environment',settings=self.settings)
        atomic_json(self.task/'environment.json', {'settings':self.settings, **environment})
        return self.request('inspect',draft=self.draft,settings=self.settings)

    def confirm(self, prepared=None):
        prepared = prepared or self.prepare()
        return self.request('confirm',draft=self.draft,settings=self.settings,digest=prepared['digest'])

    def test_inspection_is_read_only_and_not_confirmation(self):
        result = self.prepare()
        self.assertTrue(result['inspection']['ready'])
        self.assertFalse((self.task/'confirmation.json').exists())
        self.assertEqual((self.project/'input.txt').read_text(),'original')

    def test_basic_environment_needs_no_project_or_requirements(self):
        with patch.object(o, 'fingerprint', side_effect=AssertionError('Must not read a project')):
            result = self.request('environment',settings=self.settings)
        self.assertTrue(result['ready'])
        self.assertEqual(len(result['checks']),4)
        self.assertFalse((self.task/'raw-input.md').exists())
        self.assertFalse((self.task/'confirmation.json').exists())

    def test_missing_basic_check_blocks_preparing_requirements(self):
        with self.assertRaisesRegex(ValueError,'before requirements discovery'):
            self.request('inspect',draft=self.draft,settings=self.settings)

    def test_failed_basic_check_blocks_preparing_requirements(self):
        self.task.mkdir()
        atomic_json(self.task/'environment.json', {'settings':self.settings,'ready':False})
        with self.assertRaisesRegex(ValueError,'Basic environment failed'):
            self.request('inspect',draft=self.draft,settings=self.settings)

    def test_bridge_refuses_before_confirmation(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError,'confirmation required'):
            self.request('bridge',name='dot_plan',arguments={'requirements':'x'})

    def test_missing_topic_rejected(self):
        self.draft['topics'].pop('safety')
        with self.assertRaises(ValueError): self.prepare()

    def test_unknown_required_manual_check_blocks(self):
        self.draft['environment_checks']=[{'name':'device','kind':'manual','path':'','required':True,'evidence':''}]
        prepared = self.prepare()
        self.assertFalse(prepared['inspection']['ready'])
        with self.assertRaises(ValueError): self.confirm(prepared)

    def test_unknown_text_is_not_manual_evidence(self):
        for evidence in ['UNKNOWN', 'ESP-IDF version: UNKNOWN', '串口待确认', 'Doxygen 未提供']:
            self.draft['environment_checks'] = [{'name': 'toolchain', 'kind': 'manual', 'path': '',
                                                 'required': True, 'evidence': evidence}]
            self.assertFalse(self.prepare()['inspection']['ready'])

    def test_unknown_only_discovery_topic_cannot_pass(self):
        self.draft['topics']['test_environment'] = 'UNKNOWN'
        with self.assertRaisesRegex(ValueError, 'unresolved facts'):
            self.prepare()

    def test_facts_tool_uses_host_identity_and_preserves_start_confirmation(self):
        from dot_runtime.bridge import Bridge
        from dot_runtime.gpt_dot import ResponsesClient
        from test_delegated_l1 import call_response
        from test_clarifications import supplement, needs_info
        from test_runtime_controller import fixture_plan, FakeAdapter
        self.confirm()
        original = {name: (self.task / name).read_bytes() for name in
                    ('raw-input.md', 'draft.json', 'bridge.json', 'delegation.json', 'confirmation.json')}
        replies = [needs_info(), call_response('propose_plan', {'plan_json': json.dumps(fixture_plan())})]
        def bridge_factory(settings):
            return Bridge(settings, workers=FakeAdapter(), client_factory=lambda m, p, d:
                          ResponsesClient(m, p, d, send=lambda *_: replies.pop(0)))
        with patch.object(o, 'Bridge', side_effect=bridge_factory):
            result = self.request('bridge', name='dot_plan', arguments={'requirements': 'input parsing'})
            self.assertEqual(result['state'], 'NEEDS_INFO')
            with self.assertRaisesRegex(ValueError, 'identity comes from the host'):
                self.request('bridge', name='l1_add_facts', arguments={
                    'facts_json': json.dumps(supplement()), 'l1_session_id': 'forged'})
            self.request('bridge', name='l1_add_facts', arguments={'facts_json': json.dumps(supplement())})
            result = self.request('bridge', name='dot_plan', arguments={'requirements': 'input parsing'})
            self.assertTrue(result['plan'])
        from dot_runtime.clarifications import records
        self.assertEqual(records(self.task / 'control')[0]['l1_session_id'], 'native-session')
        self.assertEqual(original, {name: (self.task / name).read_bytes() for name in original})

    def test_manual_evidence_never_becomes_tool_pass(self):
        self.draft['environment_checks']=[{'name':'device','kind':'manual','path':'','required':True,'evidence':'User says device is connected'}]
        self.assertEqual(self.prepare()['inspection']['checks'][-1]['status'],'USER_ATTESTED')

    def test_rejected_plan_then_initialize_returns_known_refusal_without_missing_state_error(self):
        from dot_runtime.bridge import Bridge
        from dot_runtime.gpt_dot import ResponsesClient
        from test_plan_rejections import proposed, phase_tasks
        from test_runtime_controller import FakeAdapter
        self.confirm()
        def factory(settings):
            return Bridge(settings, workers=FakeAdapter(), client_factory=lambda m, p, d:
                          ResponsesClient(m, p, d, send=lambda *_: proposed(phase_tasks())))
        with patch.object(o, 'Bridge', side_effect=factory):
            result = self.request('bridge', name='dot_plan', arguments={'requirements': 'all work required'})
            self.assertEqual(result['operation_status'], 'COMPLETED_REJECTED')
            result = self.request('bridge', name='dot_initialize', arguments={})
            self.assertFalse(result['accepted'])
            self.assertFalse((self.task/'control/run/state.json').exists())

    def test_tool_failure_blocks(self):
        with patch.object(o,'tool_check',return_value={'status':'FAIL'}):
            self.assertFalse(self.request('environment',settings=self.settings)['ready'])
            with self.assertRaisesRegex(ValueError,'Basic environment failed'): self.prepare()

    def test_changed_project_during_confirmation_rejected(self):
        prepared = self.prepare()
        (self.project/'input.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'changed'): self.confirm(prepared)

    def test_changed_draft_during_confirmation_rejected(self):
        prepared = self.prepare()
        self.draft['limits']['api_calls'] += 10
        with self.assertRaisesRegex(ValueError,'changed'): self.confirm(prepared)

    def test_changed_model_during_confirmation_rejected(self):
        prepared = self.prepare()
        self.settings['model']='different-model'
        with self.assertRaisesRegex(ValueError,'changed'): self.confirm(prepared)

    def test_confirmation_pins_raw_input_delegation_and_no_execution(self):
        self.confirm()
        bridge = read_json(self.task/'bridge.json')
        receipt = read_json(self.task/'confirmation.json')
        self.assertEqual(receipt['authority'],'local_harness_user')
        self.assertEqual(receipt['bridge_sha256'],digest(bridge))
        self.assertFalse((self.task/'control/run/state.json').exists())
        self.assertEqual((self.task/'raw-input.md').read_text(encoding='utf-8'),self.draft['raw_input'])

    def test_cannot_reset_confirmed_task(self):
        self.confirm()
        with self.assertRaises(ValueError): self.prepare()
        with self.assertRaises(ValueError): self.confirm({'digest':'x'})

    def test_tampered_delegation_cannot_expand_authority(self):
        self.confirm()
        policy = read_json(self.task/'delegation.json'); policy['limits']['api_calls']=999
        (self.task/'delegation.json').write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError,'delegation changed'):
            self.request('bridge',name='dot_plan',arguments={'requirements':'x'})

    def test_changed_project_after_confirmation_cannot_start(self):
        self.confirm(); (self.project/'input.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'project changed'):
            self.request('bridge',name='dot_plan',arguments={'requirements':'x'})

    def test_model_cannot_choose_l1_identity(self):
        self.confirm()
        with self.assertRaisesRegex(ValueError,'identity comes from the host'):
            self.request('bridge',name='l1_approve',arguments={'kind':'plan','l1_session_id':'forged'})

    def test_product_cannot_include_control_records(self):
        self.task = self.project/'private'
        with self.assertRaisesRegex(ValueError,'disjoint'): self.prepare()

    def test_path_traversal_in_delegation_rejected(self):
        self.draft['allowed_paths']=['../outside']
        with self.assertRaises(ValueError): self.prepare()

    def test_expired_authority_rejected(self):
        self.draft['expires_at']='2001-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError,'expired'): self.prepare()

    def test_harness_cannot_approve_intermediate_as_human(self):
        with self.assertRaisesRegex(ValueError,'limited to the final report'):
            Controller.approve(None,'requirements',authority='local_harness_user')

    def test_failure_report_review_never_resumes(self):
        self.confirm()
        self.request('bridge',name='l1_stop',arguments={'reason':'No feasible plan'})
        preview=self.request('report_preview')
        with self.assertRaises(ValueError): self.request('report_confirm',sha256='forged')
        result=self.request('report_confirm',sha256=preview['sha256'])
        self.assertEqual(result['state'],'BLOCKED')
        self.assertFalse(result['execution_authorized'])
        with self.assertRaisesRegex(ValueError,'no automatic resumption'):
            self.request('bridge',name='dot_plan',arguments={'requirements':'try again'})


if __name__ == '__main__': unittest.main()

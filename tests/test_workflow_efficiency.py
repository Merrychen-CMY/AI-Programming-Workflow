"""Deterministic efficiency/recovery checks; simulated models, no paid API or hardware."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import test_l2_direct_tools as direct
import test_delegated_l1 as delegated
import test_requirements_first as workflow
from dot_runtime import clarifications, research
from dot_runtime.controller import atomic_json, digest, read_json
from dot_runtime.gpt_dot import researched_request
from dot_runtime.recovery_advice import for_status


class FactViews(unittest.TestCase):
    def test_duplicate_facts_compact_but_conflicts_sources_and_stages_survive(self):
        facts = [{'revision': i + 1, 'supplement': {'reason': 'Previous discussion ' * 2000,
            'facts': [{'name': 'SDK', 'value': '5.0', 'source': 'tool log', 'stage': 'build'}]}}
            for i in range(12)]
        facts[-1]['supplement']['facts'].extend([
            {'name': 'SDK', 'value': '5.1', 'source': 'different tool log', 'stage': 'build'},
            {'name': 'Serial port', 'value': 'COM7', 'source': 'device listing', 'stage': 'test'}])
        original = copy.deepcopy(facts)
        with tempfile.TemporaryDirectory() as root, patch.object(clarifications, 'records', return_value=facts):
            view = json.loads(clarifications.planning_context(root))
            self.assertEqual(len(view['facts_by_stage']['build']), 2)
            self.assertEqual(view['facts_by_stage']['test'][0]['value'], 'COM7')
            self.assertLess(len(json.dumps(view)), len(json.dumps(facts)) // 5)
            self.assertEqual(read_json(Path(root) / view['full_history']['source_record']), original)
            self.assertEqual(facts, original)


class ToolEfficiency(unittest.TestCase):
    setUp = direct.DirectToolTests.setUp
    controller = direct.DirectToolTests.controller
    bridge = direct.DirectToolTests.bridge
    tools = direct.DirectToolTests.tools

    def test_large_supplementary_environment_is_archived_without_rewriting_requirements(self):
        import test_runtime_controller as fixtures
        b = self.bridge([delegated.call_response('propose_plan', {'plan_json': json.dumps(fixtures.fixture_plan())})])
        b.settings['environment_context'] = 'Recorded compiler output ' * 20000
        original = self.delegation_path.read_bytes()
        result = b.call('dot_plan', {'requirements': 'Keep the exact LED acceptance and authorized paths.'})
        self.assertIn('plan', result)
        self.assertEqual(len(self.requests), 1)
        self.assertLess(len(json.dumps(self.requests[0]).encode()), 250000)
        self.assertTrue(any(read_json(p) == b.settings['environment_context'] for p in (b.root / 'api/materials').glob('*.json')))
        self.assertEqual(self.delegation_path.read_bytes(), original)
        self.assertFalse(b.workers.calls)

    def test_read_reuse_rechecks_bytes_and_authority_and_missing_file(self):
        tools = self.tools()
        args = {'path': 'value.py', 'offset': 0, 'limit': 300}
        before = tools.run('local_read', args)
        with patch.object(tools, 'perform', wraps=tools.perform) as perform:
            reused = tools.run('local_read', args)
            self.assertEqual(perform.call_count, 0)
            self.assertEqual(reused['reused_record'], before['record'])
            self.assertEqual(reused['text'], before['text'])
            path = self.project / 'value.py'
            path.write_text('changed', encoding='utf-8')
            fresh = tools.run('local_read', args)
            self.assertEqual(perform.call_count, 1)
            self.assertNotIn('reused_record', fresh)
            self.assertEqual(fresh['text'], 'changed')
            path.unlink()
            missing = tools.run('local_read', args)
            self.assertEqual(missing['status'], 'FAIL')
            self.assertEqual(missing['recovery_advice']['code'], 'LOCATE_INPUT')
        self.assertEqual(len(research.records(self.b.root)), 4)

    def test_fresh_authority_is_not_replaced_by_cached_page(self):
        tools = self.tools()
        args = {'path': 'value.py', 'offset': 0, 'limit': 300}
        tools.run('local_read', args)
        self.assertIsNone(tools.reuse_read('local_read', args, 'changed-authority'))
        for name in ('local_command', 'web_fetch', 'web_search', 'local_list', 'local_write'):
            self.assertIsNone(tools.reuse_read(name, {}, 'unused'))

    def test_same_investigation_reuses_prior_observation_then_allows_progress(self):
        b = self.bridge([])
        client = b.client()
        client.research = Mock(return_value={'status': 'PASS', 'summary': 'SDK located', 'path': 'research/source/record.json'})
        call = delegated.call_response('request_investigation', {'question': 'Locate SDK'})
        self.responses.extend([copy.deepcopy(call), copy.deepcopy(call),
                               delegated.call_response('stop', {'reason': 'Use recorded SDK'})])
        researched_request(client, 'Use existing facts')
        self.assertEqual(client.research.call_count, 1)
        self.assertEqual(len(self.requests), 3)
        outputs = [i for i in self.requests[-1]['input'] if i.get('type') == 'function_call_output']
        self.assertIn('reused_investigation', outputs[-1]['output'])
        self.assertFalse(b.workers.calls)

    def test_failed_tool_is_not_blindly_replayed_and_different_probe_can_continue(self):
        b = self.bridge([])
        call = delegated.call_response('local_read', {'path': 'absent.txt', 'offset': 0, 'limit': 200})
        self.responses.extend([copy.deepcopy(call), copy.deepcopy(call),
            delegated.call_response('local_list', {'path': '.', 'offset': 0, 'limit': 20}),
            delegated.call_response('stop', {'reason': 'Correct input located'})])
        researched_request(b.client(), 'Locate input')
        self.assertEqual(len(research.records(b.root)), 2)
        self.assertEqual(len(self.requests), 4)
        self.assertIn('NOT_EXECUTED', json.dumps(self.requests[-1]))
        self.assertFalse((b.root / 'preflight-report.json').exists())

    def test_third_unchanged_failure_pauses_with_evidence_not_final_report(self):
        b = self.bridge([])
        call = delegated.call_response('local_read', {'path': 'absent.txt', 'offset': 0, 'limit': 200})
        self.responses.extend([copy.deepcopy(call) for _ in range(3)])
        with self.assertRaisesRegex(clarifications.PlanningNeedsInfo, 'different solution'):
            researched_request(b.client(), 'Locate input')
        self.assertEqual(len(research.records(b.root)), 1)
        self.assertFalse((b.root / 'preflight-report.json').exists())


class AdviceTests(unittest.TestCase):
    def test_stage_advice_is_readonly_and_does_not_clear_any_gap(self):
        status = {'state': 'NEEDS_INFO', 'planning': {'gaps': [
            {'name': 'port', 'stage': 'test', 'reason': 'not connected'},
            {'name': 'board', 'stage': 'planning', 'reason': 'not identified'}]}}
        before = copy.deepcopy(status)
        advice = for_status(status)
        self.assertEqual(set(advice['gaps_by_stage']), {'planning', 'test'})
        self.assertTrue(advice['advisory_only'])
        self.assertEqual(status, before)
        self.assertIsNone(for_status({**status, 'report': {'outcome': 'FAILED'}}))

    def test_uncertain_api_and_product_operations_do_not_get_retry_advice(self):
        self.assertEqual(for_status({'state': 'API_REVIEW_PENDING'})['code'], 'API_REVIEW')
        self.assertEqual(for_status({'state': 'DECISION_PENDING', 'run_id': 'test',
            'decision': {'unresolved_product_operation': {'role': 'implementation'}}})['code'], 'RECONCILE_OPERATION')
        self.assertEqual(for_status({'state': 'BUDGET_REVIEW_PENDING'})['owner'], 'L1')


class CompleteWorkflow(unittest.TestCase):
    setUp = workflow.NativeRequirementsFlowTests.setUp
    request = workflow.NativeRequirementsFlowTests.request
    tool = workflow.NativeRequirementsFlowTests.tool
    confirmed = workflow.NativeRequirementsFlowTests.confirmed

    def test_recover_missing_input_reuse_evidence_and_finish_existing_workflow(self):
        started = time.monotonic()
        self.confirmed()
        bridge = self.o.Bridge(read_json(self.task / 'bridge.json'))
        tools = bridge.client().local_tools
        missing = tools.run('local_read', {'path': 'not-the-source.py', 'offset': 0, 'limit': 200})
        self.assertEqual(missing['recovery_advice']['owner'], 'L2')
        tools.run('local_list', {'path': '.', 'offset': 0, 'limit': 20})
        args = {'path': 'value.py', 'offset': 0, 'limit': 200}
        first = tools.run('local_read', args)
        second = tools.run('local_read', args)
        self.assertEqual(second['reused_record'], first['record'])
        self.tool('l1_prepare_requirements', requirements=self.text)
        self.assertEqual(self.tool('l1_approve', kind='requirements')['state'], 'REQUIREMENTS_APPROVED')
        self.assertEqual(len(self.responses), 2)  # Requirements precede every DOT API call.
        self.tool('dot_plan', requirements=self.text)
        self.assertEqual(self.tool('dot_initialize')['state'], 'PLAN_PENDING')
        self.assertEqual(self.tool('l1_approve', kind='plan')['state'], 'EXECUTING')
        result = self.tool('dot_run')
        self.assertEqual(result['report']['outcome'], 'SUCCESS')
        self.assertEqual(result['final_report']['approved_test_commands'][0]['observed_test_count'], 1)
        self.assertEqual(result['implementation_attempts'], 1)
        self.assertEqual(result['revision_count'], 0)
        self.metrics = {'fixture_outcome': 'SUCCESS', 'elapsed_seconds': round(time.monotonic() - started, 3),
            'implementation_dispatches': 1, 'fact_revisions': 0, 'reused_file_pages': 1,
            'requirements_approved_before_dot': True,
            'preserved_failed_tool_records': sum(r['record']['result']['status'] == 'FAIL' for r in research.records(bridge.root)),
            'api_calls': len(read_json(bridge.root / 'api/ledger.json')['attempts']),
            'scope': 'Simulated models/approvals; real isolated test subprocess. Not live Harness or product/HIL acceptance.'}


if __name__ == '__main__':
    unittest.main()

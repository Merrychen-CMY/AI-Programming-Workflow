"""Autonomous technical research and L1 resource ownership; no paid API or hardware."""
import copy
import json
import unittest

import test_clarifications as fixture
import test_delegated_l1 as delegated
import test_resources as budget
from dot_runtime import research, resources
from dot_runtime.controller import atomic_json, read_json


class CapabilityTests(unittest.TestCase):
    controller = fixture.ClarificationTests.controller
    setUp = delegated.DelegatedTests.setUp
    bridge = fixture.ClarificationTests.bridge

    def enable(self):
        original = read_json(self.delegation_path)
        original.update(adaptive_limits=True, resource_authority='delegated_l1')
        atomic_json(self.delegation_path, original)
        b = self.bridge([])
        b.call('l1_adjust_limits', {'proposal_json': json.dumps(budget.allocation(original['limits'])),
                                  'l1_session_id': 'L1'})
        return b, original

    def test_research_before_requirements_or_initialization_preserves_product_and_revisions(self):
        b, original = self.enable()
        before = {p.name: p.read_bytes() for p in self.project.iterdir() if p.is_file()}
        result = b.call('dot_research', {'question': 'Find installed CMake path and version; read official LED datasheet'})
        self.assertTrue(result['accepted'])
        self.assertEqual(b.workers.calls, ['research'])
        self.assertFalse(b.run_dir.exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.project.iterdir() if p.is_file()})
        used = resources.usage(b.root)
        self.assertEqual((used['implementation_attempts'], used['revisions'], used['api_calls']), (0, 0, 0))
        page = b.call('dot_read_record', {'path': result['investigation']['path'], 'offset': '0'})
        self.assertIn('Find installed CMake', json.dumps(page))
        self.assertEqual(len(research.records(b.root)), 1)

    def test_dot_requests_research_and_continues_plan_without_human_material_collection(self):
        from test_runtime_controller import fixture_plan
        b, original = self.enable()
        self.responses.extend([
            delegated.call_response('request_investigation', {'question': 'Locate CMake and the LED timing source'}),
            delegated.call_response('propose_plan', {'plan_json': json.dumps(fixture_plan())})])
        result = b.call('dot_plan', {'requirements': 'Light the LED using SPI'})
        self.assertNotEqual(result.get('state'), 'NEEDS_INFO')
        self.assertEqual(read_json(b.root / 'planning-status.json')['status'], 'PLAN_READY')
        self.assertEqual(b.workers.calls, ['research'])
        self.assertEqual(resources.usage(b.root)['api_calls'], 2)
        self.assertIn('INVESTIGATION RESULT', json.dumps(self.requests[-1]))
        self.assertIn('PINNED TEST PROTOCOL', json.dumps(self.requests[-1]))
        self.assertEqual(resources.usage(b.root)['revisions'], 0)

    def test_l1_increases_initial_ceiling_without_user_pending_or_usage_reset(self):
        b, original = self.enable()
        ledger = {'delegation_sha256': 'fixture', 'model': 'fixture', 'started_at': 1,
                  'attempts': [{'id': 'spent', 'status': 'COMPLETED'}]}
        (b.root / 'api').mkdir()
        atomic_json(b.root / 'api/ledger.json', ledger)
        limits = copy.deepcopy(original['limits'])
        limits.update(api_calls=limits['api_calls'] + 20, wall_time_seconds=3000000000)
        result = b.call('l1_adjust_limits', {'proposal_json': json.dumps(budget.allocation(limits)), 'l1_session_id': 'L1'})
        self.assertTrue(result['accepted'])
        self.assertEqual(resources.authority(original, b.root)['limits'], limits)
        self.assertIsNone(resources.read(b.root, original)['pending'])
        self.assertEqual(read_json(b.root / 'api/ledger.json'), ledger)
        self.assertEqual(read_json(self.delegation_path), original)
        event = resources.read(b.root, original)['events'][-1]['record']['payload']
        self.assertEqual(event['before_ceiling'], original['limits'])
        self.assertEqual(event['observed_usage']['api_calls'], 1)

    def test_dot_research_proposal_reaches_l1_and_approved_baseline_without_self_approval(self):
        from dot_runtime.controller import Controller
        from test_runtime_controller import fixture_plan
        b, _ = self.enable()
        proposal = ('Observed fixture SDK; recommend recording the SDK version from the research evidence. '
                    'Rationale: reproducible build; acceptance target unchanged. L1 reviews the proposed baseline.')
        self.responses.extend([
            delegated.call_response('request_investigation', {'question': 'Inspect SDK and propose missing requirement details'}),
            delegated.call_response('request_clarification', {'reason': proposal, 'gaps': [
                {'name': 'SDK baseline', 'reason': 'L1 reviews the sourced requirement addition', 'stage': 'planning'}]}),
            delegated.call_response('propose_plan', {'plan_json': json.dumps(fixture_plan())})])
        b.call('dot_plan', {'requirements': 'Existing product goal'})
        self.assertEqual(b.status()['planning']['reason'], proposal)
        self.assertFalse(b.run_dir.exists())
        observation = research.context(b.root)[0]
        facts = fixture.supplement()
        facts['facts'][0]['source'] = observation['path'] + '; synthetic SDK observation for this test'
        added = b.call('l1_add_facts', {'facts_json': json.dumps(facts), 'l1_session_id': 'L1'})
        self.assertTrue(added['accepted'])
        b.call('dot_plan', {'requirements': 'Existing product goal; sourced SDK baseline'})
        b.call('dot_initialize', {})
        self.assertFalse(Controller(b.run_dir).state['approvals'])
        b.call('l1_approve', {'kind': 'requirements', 'l1_session_id': 'L1'})
        state = Controller(b.run_dir).state
        self.assertIn('requirements', state['approvals'])
        self.assertEqual(state['revision_count'], 1)
        self.assertEqual(state['implementation_attempts'], 0)
        self.assertEqual(b.workers.calls[0], 'research')
        self.assertIn('review', b.workers.calls)

    def test_resource_authorization_route_also_returns_l1_approval_without_user_question(self):
        b, original = self.enable()
        limits = {**original['limits'], 'api_calls': original['limits']['api_calls'] + 7}
        result = b.call('l1_request_authorization', {'proposal_json': json.dumps(budget.amendment({'limits': limits})),
                                                   'l1_session_id': 'L1'})
        self.assertTrue(result['accepted'])
        self.assertIsNone(resources.read(b.root, original)['pending'])
        self.assertEqual(resources.effective(original, b.root)['limits']['api_calls'], limits['api_calls'])

    def test_resource_tool_cannot_expand_paths_or_erase_usage(self):
        b, original = self.enable()
        proposal = budget.allocation(original['limits'])
        proposal['allowed_paths'] = ['.']
        self.assertFalse(b.call('l1_adjust_limits', {'proposal_json': json.dumps(proposal), 'l1_session_id': 'L1'})['accepted'])
        proposal = budget.amendment({'limits': original['limits'], 'allowed_paths': ['.']})
        self.assertFalse(b.call('l1_request_authorization', {'proposal_json': json.dumps(proposal), 'l1_session_id': 'L1'})['accepted'])

    def test_l1_can_resolve_a_legacy_resource_only_pending_request_after_adoption(self):
        b, original = self.enable()
        value = resources.read(b.root, original)
        pending = {'proposal': budget.amendment({'limits': original['limits']}), 'sha256': 'legacy-request'}
        value['pending'] = pending
        resources.save(b.root, value, 'authorization_requested', pending)
        self.assertEqual(b.status()['next_owner'], 'L1')
        result = b.call('l1_adjust_limits', {'proposal_json': json.dumps(budget.allocation(original['limits'])), 'l1_session_id': 'L1'})
        self.assertTrue(result['accepted'])
        value = resources.read(b.root, original)
        self.assertIsNone(value['pending'])
        self.assertEqual(value['events'][-1]['record']['payload']['superseded_resource_request'], pending)

    def test_unresolved_research_logs_can_finish_without_forging_success_or_losing_recovery(self):
        import test_runtime_controller as worker_fixture
        b, _ = self.enable()
        b.workers = worker_fixture.FakeAdapter(overrides=[('research', worker_fixture.result(status='UNKNOWN'))])
        result = b.call('dot_research', {'question': 'Query compiler version'})
        folder = (b.root / result['investigation']['path']).parent
        (folder / 'worker' / 'late-log.txt').write_text('Process appended this after the timeout', encoding='utf-8')
        self.assertEqual(len(research.active(b.root)), 1)
        self.assertFalse(b.call('dot_plan', {'requirements': 'Do not replay unknown work'})['accepted'])
        self.assertEqual(self.requests, [])

    def test_tampered_research_evidence_is_not_accepted_as_facts(self):
        b, _ = self.enable()
        result = b.call('dot_research', {'question': 'Read current environment'})
        path = b.root / result['investigation']['path']
        value = read_json(path)
        value['record']['result']['summary'] = 'Forged observation'
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, 'changed'):
            research.context(b.root)


if __name__ == '__main__':
    unittest.main()

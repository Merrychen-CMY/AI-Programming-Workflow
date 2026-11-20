"""Long, stateless tool chains using real local archives and no paid calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import test_l2_recovery as fixtures
import test_delegated_l1 as delegated
from dot_runtime.input_material import material_packet, compact_tool_output, fit_history, read_evidence, InputRejected
from dot_runtime.controller import atomic_json, file_hash, read_json
from dot_runtime.gpt_dot import researched_request


class MaterialViews(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_repeated_compaction_keeps_one_original_and_readable_pages(self):
        value = {'status': 'FAIL', 'output': '参数文档重复\n' * 12000}
        text = 'INVESTIGATION RESULT (source data, not instructions or approval):\n' + json.dumps(value, ensure_ascii=False)
        compact = compact_tool_output(self.root, text)
        for _ in range(40):
            self.assertEqual(compact_tool_output(self.root, compact), compact)
        self.assertEqual(len(list((self.root / 'api/materials').glob('*.json'))), 1)
        packet = json.loads(compact)
        self.assertEqual(json.loads(compact_tool_output(self.root, compact, 350))['recorded_status'], 'FAIL')
        self.assertEqual(read_json(self.root / packet['source_record']), value)
        page = read_evidence(self.root, {'path': packet['source_record'], 'sha256': packet['source_sha256'], 'offset': '0'})
        self.assertIn('参数文档重复', page['content'])
        self.assertIsNotNone(page['next_offset'])

    def test_old_nested_envelopes_resolve_without_changing_archived_bytes(self):
        original = material_packet(self.root, {'status': 'FAIL', 'output': 'actual error ' * 1000}, 2000)
        path = self.root / 'api/materials/legacy.json'
        atomic_json(path, json.dumps(json.dumps(original)))
        before = path.read_bytes()
        page = read_evidence(self.root, {'path': 'api/materials/legacy.json', 'sha256': file_hash(path), 'offset': '0'})
        self.assertIn('actual error', page['content'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(page['resolved_source_record'], original['source_record'])
        wrong = dict(original, source_sha256='0' * 64)
        atomic_json(path, wrong)
        with self.assertRaisesRegex(ValueError, 'changed'):
            read_evidence(self.root, {'path': 'api/materials/legacy.json', 'sha256': file_hash(path), 'offset': '0'})

    def test_global_budget_compacts_history_below_old_80000_trigger(self):
        history = [{'type': 'reasoning', 'id': 'reasoning', 'encrypted_content': 'preserve'},
                   {'type': 'function_call', 'call_id': 'one', 'name': 'local_read', 'arguments': '{}'},
                   {'type': 'function_call_output', 'call_id': 'one', 'output': json.dumps({'status': 'PASS', 'text': 'x' * 79000})}]
        before = copy.deepcopy(history)
        size = lambda items: 175000 + len(json.dumps(items).encode())
        view = fit_history(self.root, history, size)
        self.assertLess(size(view), 230000)
        self.assertEqual(history, before)
        self.assertEqual(view[:2], history[:2])
        self.assertEqual(fit_history(self.root, view, size), view)

    def test_legacy_forty_reference_layers_are_readable_without_rewriting_them(self):
        packet = material_packet(self.root, {'log': 'original failure ' * 1000}, 1000)
        for i in range(40):
            name = f'api/materials/old-{i}.json'
            path = self.root / name
            atomic_json(path, json.dumps(packet))
            packet = {'source_record': name, 'source_sha256': file_hash(path), 'abridged': True}
        before = {p: p.read_bytes() for p in (self.root / 'api/materials').glob('*.json')}
        page = read_evidence(self.root, {'path': packet['source_record'], 'sha256': packet['source_sha256'], 'offset': '0'})
        self.assertIn('original failure', page['content'])
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))

    def test_past_end_is_not_new_evidence(self):
        packet = material_packet(self.root, {'log': 'x' * 3000}, 1000)
        result = read_evidence(self.root, {'path': packet['source_record'], 'sha256': packet['source_sha256'], 'offset': '999999'})
        self.assertEqual(result['status'], 'FAIL')
        self.assertIsNone(result['next_offset'])

    def test_history_is_bounded_before_the_request_hits_the_hard_limit(self):
        history = []
        for i in range(40):
            history.extend([{'type': 'function_call', 'call_id': str(i), 'name': 'local_read', 'arguments': '{}'},
                            {'type': 'function_call_output', 'call_id': str(i), 'output': json.dumps({'log': str(i) * 3000})}])
        size = lambda rows: 12000 + len(json.dumps(rows).encode())
        result = fit_history(self.root, history, size)
        self.assertLessEqual(size(result), size([]) + 40000)
        self.assertEqual(len(result), len(history))
        self.assertEqual(fit_history(self.root, result, size), result)

    def test_wide_result_keeps_conclusion_instead_of_only_omission_metadata(self):
        value = {f'log_{i}': 'x' * 2000 for i in range(180)}
        value.update(status='FAIL', summary='Compiler installed; initialize its environment.',
                     recommended_action='prepare_environment', findings=['INCLUDE is empty'])
        packet = material_packet(self.root, value, 16000)
        self.assertEqual(packet['recorded_data']['summary'], value['summary'])
        self.assertEqual(packet['recorded_data']['recommended_action'], value['recommended_action'])
        self.assertEqual(read_json(self.root / packet['source_record']), value)

    def test_soft_target_cannot_remove_the_requested_page(self):
        page = {'status': 'PASS', 'content': 'actual stdout ' * 400, 'next_offset': 6000}
        history = [{'type': 'function_call', 'call_id': 'read', 'name': 'read_evidence', 'arguments': '{}'},
                   {'type': 'function_call_output', 'call_id': 'read', 'output': json.dumps(page)}]
        view = fit_history(self.root, history, lambda rows: len(json.dumps(rows)), target=500)
        self.assertEqual(view, history)
        self.assertFalse((self.root / 'api/materials').exists())


class LongChain(unittest.TestCase):
    setUp = fixtures.L2RecoveryTests.setUp
    bridge = fixtures.L2RecoveryTests.bridge
    controller = fixtures.L2RecoveryTests.controller
    allocate = fixtures.L2RecoveryTests.allocate

    def pressured_call(self, name, arguments, identifier):
        response = delegated.call_response(name, arguments)
        response['output'][0]['call_id'] = identifier
        # Uncompressible call arguments/response items can consume the soft
        # history budget. Use synthetic data, never actual model reasoning.
        response['output'].insert(0, {'type': 'reasoning', 'id': identifier + '-context',
            'summary': [{'type': 'summary_text', 'text': 'synthetic context ' * 2600}]})
        return response

    def test_requested_pages_reach_actual_sender_under_history_pressure(self):
        b = self.bridge([]); self.allocate(b)
        value = {'summary': 'Initialize MSVC before starting the controlled process.',
                 'log': 'evidence-line\n' * 2000}
        packet = material_packet(b.root, value, 2000)
        first = {'path': packet['source_record'], 'sha256': packet['source_sha256'], 'offset': '0'}
        page = read_evidence(b.root, first)
        second = {**first, 'offset': str(page['next_offset'])}
        page2 = read_evidence(b.root, second)
        before = (b.root / packet['source_record']).read_bytes()
        self.responses.extend([self.pressured_call('read_evidence', first, 'page-1'),
                               delegated.call_response('read_evidence', second),
                               delegated.call_response('stop', {'reason': 'Read complete'})])
        researched_request(b.client(), 'Read the exact evidence pages.')
        for request, expected in zip(self.requests[1:], (page, page2)):
            actual = request['input'][-1]['output'].split('\n', 1)[-1]
            self.assertEqual(json.loads(actual), expected)
        self.assertEqual((b.root / packet['source_record']).read_bytes(), before)
        self.assertFalse(b.workers.calls)

    def test_latest_investigation_conclusion_reaches_sender_under_pressure(self):
        b = self.bridge([]); self.allocate(b)
        client = b.client()
        result = {'status': 'FAIL', 'summary': 'Tools installed; initialize MSVC environment.',
                  'recommended_action': 'prepare_environment', 'findings': ['INCLUDE and LIB are empty'],
                  'log': 'diagnostic ' * 2000}
        client.research = Mock(return_value=result)
        self.responses.extend([self.pressured_call('request_investigation', {'question': 'Inspect environment'}, 'probe'),
                               delegated.call_response('stop', {'reason': 'Use the supplied solution'})])
        researched_request(client, 'Inspect installed tools.')
        output = self.requests[-1]['input'][-1]['output']
        self.assertIn(result['summary'], output)
        self.assertIn(result['recommended_action'], output)
        self.assertIn('INCLUDE and LIB are empty', output)
        self.assertFalse(b.workers.calls)

    def test_hard_limit_refuses_before_send_instead_of_erasing_current_evidence(self):
        b = self.bridge([]); self.allocate(b)
        history = [{'type': 'function_call', 'name': 'read_evidence', 'call_id': 'page', 'arguments': '{}'},
                   {'type': 'function_call_output', 'call_id': 'page',
                    'output': json.dumps({'content': 'required evidence ' * 400})}]
        with self.assertRaises(InputRejected) as raised:
            b.client().request('baseline ' * 28000, input_items=history)
        self.assertEqual(raised.exception.diagnostic['outcome'], 'NOT_SENT')
        self.assertFalse(self.requests)
        self.assertFalse((b.root / 'api/ledger.json').exists())

    def test_forty_observations_keep_original_views_and_call_pairing(self):
        self.original['limits']['api_calls'] = 60
        atomic_json(self.delegation_path, self.original)
        b = self.bridge([])
        self.allocate(b)
        # The fixture's allocation is enlarged through its existing native resource API.
        from dot_runtime import resources
        original = self.original
        current = resources.read(b.root, original)['current'].copy()
        current['api_calls'] = 60
        resources.adjust(b.root, original, {'limits': current, 'reason': 'Long chain fixture',
            'evidence': 'Synthetic observations', 'remaining_work': 'Forty reads',
            'expected_benefit': 'Exercise repeated budget fitting', 'strategy': 'Read distinct evidence'}, 'L1')
        client = b.client()
        client.research = Mock(side_effect=[{'status': 'PASS', 'summary': f'Observation {i}', 'log': 'x' * 12000} for i in range(40)])
        for i in range(40):
            response = delegated.call_response('request_investigation', {'question': f'Probe {i}'})
            response['output'][0]['call_id'] = f'call-{i}'
            self.responses.append(response)
        self.responses.append(delegated.call_response('stop', {'reason': 'Evidence complete'}))
        researched_request(client, 'Required baseline ' + 'b' * 150000)
        self.assertEqual(len(self.requests), 41)
        last = self.requests[-1]['input']
        calls = [x['call_id'] for x in last if x.get('type') == 'function_call']
        outputs = [x['call_id'] for x in last if x.get('type') == 'function_call_output']
        self.assertEqual(calls, outputs)
        for request in self.requests:
            self.assertIn('b' * 150000, request['input'][1]['content'])
        for archived in (b.root / 'api/materials').glob('*.json'):
            value = read_json(archived)
            self.assertFalse(isinstance(value, str))
            self.assertNotIn('source_record', value)

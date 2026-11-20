"""Long, stateless tool chains using real local archives and no paid calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import test_l2_recovery as fixtures
import test_delegated_l1 as delegated
from dot_runtime.input_material import material_packet, compact_tool_output, fit_history, read_evidence
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


class LongChain(unittest.TestCase):
    setUp = fixtures.L2RecoveryTests.setUp
    bridge = fixtures.L2RecoveryTests.bridge
    controller = fixtures.L2RecoveryTests.controller
    allocate = fixtures.L2RecoveryTests.allocate

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

"""TEST ONLY: real host/Python pipe and controller, simulated AI/native decisions.

No external API, credential lookup, Codex process or existing product is used.
"""
import json
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'tests')]
import test_runtime_controller as fixtures
import test_delegated_l1 as delegated
from dot_runtime import onboarding
from dot_runtime.bridge import Bridge
from dot_runtime.gpt_dot import ResponsesClient
from dot_runtime.presentation import compact
from dot_runtime.controller import atomic_json


class Workers(fixtures.FakeAdapter):
    def run(self, role, prompt, workspace, output_dir, timeout_seconds):
        report = super().run(role, prompt, workspace, output_dir, timeout_seconds)
        if role == 'implementation':
            (workspace / 'value.py').write_text('def value():\n    return 3\n')
        report['session_id'] = uuid.uuid4().hex
        if 'source-index.json' in prompt:
            report['summary'] = 'Checked controller originals and approved fixture requirements'
        atomic_json(output_dir / 'result.json', report)
        return report


def bridge(settings):
    plan = fixtures.fixture_plan()
    plan['verification']['test_command'] = [sys.executable, '-B', '-m', 'unittest', '-v']
    def factory(model, path, envelope):
        def send(payload, timeout):
            # Both paths are intentionally deterministic fixture model responses.
            marker = path / 'fixture-stop-returned'
            if 'Fixture request L2 assessment' in json.dumps(payload.get('input', [])) and not marker.exists():
                marker.write_text('fixture-only')
                return delegated.call_response('stop', {'reason': 'Fixture: consider authorized alternative, submit permission gap to L1'})
            if any(t.get('name') == 'propose_plan' for t in payload.get('tools', [])):
                return delegated.call_response('propose_plan', {'plan_json': json.dumps(plan)})
            return delegated.call_response('run_approved_workflow')
        class FixtureClient(ResponsesClient):
            def request(self, prompt, **options):
                if 'Fixture oversized material' in prompt:
                    # Simulate attached diagnostic material AFTER the exact
                    # requirements approval, exercising the real unsent guard.
                    prompt += '\nFIXTURE DIAGNOSTIC MATERIAL:\n' + 'fixture-log\n' * 30000
                return super().request(prompt, **options)
        return FixtureClient(model, path, envelope, send=send)
    return Bridge(settings, client_factory=factory, workers=Workers())


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8')
    request = json.load(sys.stdin)
    try:
        from tempfile import gettempdir
        task = Path(request['task']).resolve()
        base = task.parents[1]
        if base.parent != Path(gettempdir()).resolve() or not base.name.startswith('reqpilot-handoff-'):
            raise ValueError('Fixture driver only accepts its isolated temporary test directory')
        with patch.object(onboarding, 'Bridge', side_effect=bridge), patch.object(onboarding, 'tool_check',
                return_value={'status': 'PASS', 'exit_code': 0}):
            result = onboarding.dispatch(request)
        if request['action'] in ('bridge', 'status', 'report_confirm'):
            result = compact(result)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)})); sys.exit(1)

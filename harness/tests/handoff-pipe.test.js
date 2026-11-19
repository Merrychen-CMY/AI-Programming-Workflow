// Real JS tool -> Python pipe -> onboarding -> controller -> local unittest process.
// AI responses, environment probes and native user answers are simulated, not a live Harness UI claim.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createWorkflow } from '../workflow.js';
import { pythonCall } from '../process.js';
import { saveConfig, keyDigest } from '../setup.js';

test('normal host lifecycle survives a wrong-phase request and produces a real test report', async t => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-handoff-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const project = resolve(directory, 'product'); await mkdir(project);
  await writeFile(resolve(project, 'test_value.py'), 'import unittest\nfrom value import value\nclass Test(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 3)\n');
  const key = 'fixture-not-an-api-key', python = process.env.PYTHON || 'python';
  await saveConfig(directory, {model: 'fixture-model', python, codex: 'fixture-codex',
    probe: {status: 'PASS', model: 'fixture-model', credential_sha256: keyDigest(key)}});
  const questions = [], calls = [];
  const ctx = {credentials: {resolve: async () => ({value: key})}, userQuestions: {ask: async q => {
    questions.push(q); return {answers: [{id: q.questions[0].id, selected: [q.questions[0].options[0].label]}]};
  }}};
  const flow = createWorkflow({ctx, root, directory, keyRef: 'fixture', runner: (exe, _script, request, opts) => {
    calls.push(request); return pythonCall(exe, resolve(root, 'tests/harness_handoff_fixture.py'), request, opts);
  }});
  t.after(() => flow.dispose());
  const exec = {agent: {id: 'normal-path-fixture'}, signal: new AbortController().signal};
  const invoke = (name, args = {}) => flow.invoke(name, args, exec);
  assert.equal((await invoke('workflow_check_environment')).ready, true);
  const draft = JSON.parse(await readFile(resolve(root, 'templates/harness-start.example.json'), 'utf8'));
  draft.project = project; draft.raw_input = 'Return 3 from value(). Python-only. No external actions.';
  draft.hard_constraints = ['Only modify value.py; tests must independently pass'];
  draft.allowed_paths = ['value.py']; draft.environment_checks = [{name: 'independent test entry', kind: 'file',
    path: resolve(project, 'test_value.py'), required: true, evidence: 'Fixture test entry exists; execution remains pending'}];
  draft.expires_at = new Date(Date.now() + 3600000).toISOString();
  draft.topics = Object.fromEntries(Object.keys(draft.topics).map(k => [k, 'Local Python fixture; return 3; no network or hardware']));
  await invoke('workflow_inspect', {draft_json: JSON.stringify(draft)});
  await invoke('workflow_confirm_start');
  await invoke('l1_adjust_limits', {proposal_json: JSON.stringify({limits: draft.limits,
    reason: 'Allocate bounded resources for the complete isolated fixture', evidence: 'Confirmed fixture scope',
    remaining_work: 'Review, implement and independently verify return 3', expected_benefit: 'Complete the fixture',
    strategy: 'Use one implementation and independent test/review'})});
  const unapproved = await invoke('dot_plan', {requirements: draft.raw_input});
  assert.equal(unapproved.operation_status, 'COMPLETED_REJECTED', JSON.stringify(unapproved));
  assert.match(unapproved.reason, /requirements approval/);
  assert.equal(unapproved.input_rejection, undefined);
  await invoke('l1_prepare_requirements', {requirements: 'Fixture oversized material: ' + draft.raw_input});
  await invoke('l1_approve', {kind: 'requirements'});
  const mismatch = await invoke('dot_plan', {requirements: '中'.repeat(100000)});
  assert.equal(mismatch.operation_status, 'COMPLETED_REJECTED');
  assert.match(mismatch.reason, /exactly the independently approved requirements/);
  assert.equal(mismatch.input_rejection, undefined, 'mismatched baseline must be refused before API material sizing');
  const oversized = await invoke('dot_plan', {requirements: 'Fixture oversized material: ' + draft.raw_input});
  assert.equal(oversized.operation_status, 'COMPLETED_REJECTED');
  assert.equal(oversized.input_rejection.outcome, 'NOT_SENT');
  const earlyTask = resolve(directory, 'tasks', createHash('sha256').update(exec.agent.id).digest('hex'));
  await assert.rejects(readFile(resolve(earlyTask, 'interrupted.json')), /ENOENT/);
  assert.equal(JSON.parse(await readFile(resolve(earlyTask, 'control/api/ledger.json'))).attempts.length, 0,
    'independent requirements review starts the clock; unsent material does not reserve an API call');
  await invoke('l1_prepare_requirements', {requirements: 'Fixture request L2 assessment'});
  await invoke('l1_approve', {kind: 'requirements'});
  const l2Pause = await invoke('dot_plan', {requirements: 'Fixture request L2 assessment'});
  assert.equal(l2Pause.operation_status, 'COMPLETED_REJECTED');
  assert.equal(l2Pause.preflight_report, null);
  await assert.rejects(readFile(resolve(earlyTask, 'interrupted.json')), /ENOENT/);
  await invoke('l1_prepare_requirements', {requirements: draft.raw_input});
  await invoke('l1_approve', {kind: 'requirements'});
  await invoke('dot_plan', {requirements: draft.raw_input});
  assert.equal((await invoke('dot_initialize')).state, 'PLAN_PENDING');
  const task = resolve(directory, 'tasks', createHash('sha256').update(exec.agent.id).digest('hex'));
  const rejected = await invoke('l1_approve', {kind: 'requirements'});
  assert.equal(rejected.operation_status, 'COMPLETED_REJECTED');
  await assert.rejects(readFile(resolve(task, 'interrupted.json')), /ENOENT/);
  const refusedStop = await invoke('l1_stop', {reason: 'Unknown; stop now'});
  assert.equal(refusedStop.operation_status, 'COMPLETED_REJECTED');
  await assert.rejects(readFile(resolve(task, 'control/run/final-report.json')), /ENOENT/);
  await assert.rejects(readFile(resolve(task, 'interrupted.json')), /ENOENT/);
  assert.equal((await invoke('l1_approve', {kind: 'plan'})).state, 'EXECUTING');
  const result = await invoke('dot_run');
  const executionState = JSON.parse(await readFile(resolve(task, 'control/run/state.json')));
  assert.equal(result.report?.outcome, 'SUCCESS', JSON.stringify(executionState.history.slice(-3)));
  assert.equal(result.revision_count, 2, 'both changed requirement drafts retain cumulative revisions');
  assert.ok(JSON.stringify(result).length < 20000);
  let offset = '0', full = '';
  do {
    const page = await invoke('dot_read_record', {path: 'run/final-report.json', offset});
    full += page.content; offset = page.next_offset === null ? null : String(page.next_offset);
  } while (offset !== null);
  assert.equal(JSON.parse(full).approved_test_commands[0].observed_test_count, 1);
  assert.equal(JSON.parse(full).l1_reviews.length, 4, 'three pre-plan requirements reviews plus one plan review; no duplicate requirement review after initialization');
  await invoke('l1_submit_report', {summary: 'Local fixture passed. AI simulated; no live Harness/product acceptance.'});
  assert.equal((await invoke('workflow_review_report')).state, 'DELIVERY_READY');
  assert.equal(questions.length, 2, 'startup and final review only');
  assert.equal(calls.filter(x => x.name === 'dot_run').length, 1);
  await assert.rejects(readFile(resolve(task, 'interrupted.json')), /ENOENT/);
});

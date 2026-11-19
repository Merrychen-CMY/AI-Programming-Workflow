import test from 'node:test';
import assert from 'node:assert/strict';
import { recoverOperation } from '../operation-recovery.js';
import { execFileSync } from 'node:child_process';
import { readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pythonCall } from '../process.js';
import { createBootstrap } from '../bootstrap.js';
import { toolFields } from '../workflow.js';
import { createHash } from 'node:crypto';

function fixture(choice = 'approve', ready = true) {
  const calls = [], questions = [];
  const preview = {sha256: 'fixture-exact-snapshot', ready, project: 'fixture product',
    proposal: {analysis: 'Sourced investigation', evidence: [{path: 'run/actual-evidence.json', sha256: 'f'.repeat(64)}]},
    processes: {status: ready ? 'REQUIRES_NATIVE_ATTESTATION' : 'UNKNOWN'},
    binding: {host_interruption: {name: 'dot_run', status: 'UNKNOWN'}}, usage: {implementation_attempts: 1}};
  const call = async (action, args) => {calls.push({action, args});return action === 'operation_preview' ? preview : {status: 'OPERATION_RECONCILED'};};
  const ctx = {userQuestions: {ask: async ({questions: q}) => {
    questions.push(...q);
    return {answers: choice === 'skip' ? [] : [{id: q[0].id, custom: choice === 'custom',
      selected: [choice === 'approve' ? q[0].options[0].label : q[0].options[1].label]}]};
  }}};
  return {call, ctx, exec: {agent: {id: 'native-session'}}, calls, questions, preview};
}

test('exact native choice commits only the inspected hash with explicit evidence limitations', async () => {
  const f = fixture();
  const result = await recoverOperation(f);
  assert.equal(result.status, 'OPERATION_RECONCILED');
  assert.deepEqual(f.calls[1], {action: 'operation_commit', args: {sha256: f.preview.sha256}});
  assert.match(f.questions[0].question, /模型陈述不等于真实核实/);
  assert.match(f.questions[0].question, /不重放旧操作/);
  assert.match(f.questions[0].question, /累计消耗/);
});

for (const choice of ['decline', 'skip', 'custom']) test(`${choice} never clears interruption`, async () => {
  const f = fixture(choice);
  const result = await recoverOperation(f);
  assert.equal(result.status, 'OPERATION_RECOVERY_NOT_APPROVED');
  assert.equal(f.calls.length, 1);
});

test('unknown or running process blocks before asking for approval', async () => {
  const f = fixture('approve', false);
  const result = await recoverOperation(f);
  assert.equal(result.status, 'OPERATION_RECOVERY_BLOCKED');
  assert.equal(f.questions.length, 0);
  assert.equal(f.calls.length, 1);
});

test('bootstrap uses verified maintenance for a completed pre-plan review without changing the task runtime', async t => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const python = process.env.PYTHON || 'python';
  const created = JSON.parse(execFileSync(python, ['-B', '-c', `import sys,json,hashlib
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'runtime'),str(Path.cwd()/'tests')]
from test_completed_review_handoff import CompletedReviewTests
from dot_runtime.controller import file_hash
x=CompletedReviewTests();x.setUp()
x.task=x.base/'tasks'/hashlib.sha256(b'native-l1-session').hexdigest()
x.stopped_review()
x.tool('l1_prepare_recovery',proposal_json=json.dumps({'reason':'Completed review reply overflow','analysis':'Completed native fixture approval and persisted evidence','resume_plan':'Continue planning after native confirmation','side_effects':'Read-only fixture, no product work','evidence':[{'path':'requirements-review/state.json','sha256':file_hash(x.root/'requirements-review/state.json'),'description':'Persisted fixture approval'}]}))
x.temp._finalizer.detach()
print(json.dumps({'directory':str(x.base),'task':str(x.task)}))
`], { cwd: root, windowsHide: true, encoding: 'utf8' }));
  const relativePath = relative(tmpdir(), created.directory);
  assert.ok(relativePath && !relativePath.startsWith('..') && !isAbsolute(relativePath));
  t.after(() => rm(created.directory, { recursive: true, force: true }));
  const manifest = createHash('sha256').update(await readFile(resolve(root, 'MANIFEST.json'))).digest('hex');
  const version = (await readFile(resolve(root, 'VERSION'), 'utf8')).trim();
  const before = await readFile(resolve(created.task, 'confirmation.json'));
  const review = await readFile(resolve(created.task, 'control/requirements-review/state.json'));
  const calls = [], questions = [];
  const updates = {forTask: async () => ({root, version, manifest_sha256: manifest, pinned: true}),
    operationRecoveryTarget: async () => ({root, version, manifest_sha256: manifest}), dispose() {}};
  const bootstrap = createBootstrap({root, directory: created.directory, fields: toolFields, updates,
    ctx: {credentials: {resolve() {throw Error('Recovery must not read credentials');}}, userQuestions: {ask: async ({questions: q}) => {
      questions.push(...q); return {answers: [{id: q[0].id, selected: [q[0].options[0].label]}]};
    }}}, workflowOptions: {runner: (exe, script, request, options) => {
      calls.push(request.action); assert.equal(options.key, undefined); return pythonCall(exe, script, request, options);
    }}});
  t.after(() => bootstrap.dispose());
  const result = await bootstrap.invoke('workflow_recover_operation', {}, {agent: {id: 'native-l1-session'}});
  assert.equal(result.status, 'OPERATION_RECONCILED', JSON.stringify(result));
  assert.equal(result.task_runtime_changed, false);
  assert.deepEqual(calls, ['operation_preview', 'operation_commit']);
  assert.equal(questions.length, 1);
  assert.match(questions[0].question, /不重复审查/);
  assert.deepEqual(await readFile(resolve(created.task, 'confirmation.json')), before);
  assert.deepEqual(await readFile(resolve(created.task, 'control/requirements-review/state.json')), review);
  await assert.rejects(readFile(resolve(created.task, 'interrupted.json')), /ENOENT/);
});

test('cancelled native question cannot commit', async () => {
  const f = fixture();
  f.exec.signal = {aborted: true};
  assert.equal((await recoverOperation(f)).status, 'OPERATION_RECOVERY_NOT_APPROVED');
  assert.equal(f.calls.length, 1);
});

test('a changed task is rejected by the private commit, never retried', async () => {
  const f = fixture();
  const original = f.call;
  f.call = async (action, args) => {
    if (action === 'operation_commit') throw Error('Recovery snapshot changed during native confirmation');
    return original(action, args);
  };
  await assert.rejects(recoverOperation(f), /snapshot changed/);
});

test('an interrupted already-approved local transaction finishes through the same native entry', async () => {
  const f = fixture();
  f.preview.finish_required = true;
  const result = await recoverOperation(f);
  assert.equal(result.status, 'OPERATION_RECONCILED');
  assert.equal(f.questions.length, 0);
  assert.equal(f.calls[1].action, 'operation_finish');
  assert.equal(f.calls.length, 2);
});

test('changed pre-init baseline requires an explicit native adoption label with before and after', async () => {
  const f = fixture();
  f.preview.baseline_adoption = {applies_before_initialization: true, changed: true,
    before: {id: 'old-baseline-hash', file_count: 3}, after: {id: 'new-baseline-hash', file_count: 4}};
  await recoverOperation(f);
  assert.match(f.questions[0].options[0].label, /采用所示新基线/);
  assert.match(f.questions[0].question, /old-baseline-hash/);
  assert.match(f.questions[0].question, /new-baseline-hash/);
  assert.match(f.questions[0].question, /重新整理需求、独立审查及规划/);
  assert.match(f.questions[0].question, /原确认完整保留/);
  assert.equal(f.calls[1].action, 'operation_commit');
});

test('native entry reaches real Python recovery and preserves actual fixture files (human/process history simulated)', async t => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const python = process.env.PYTHON || 'python';
  const created = JSON.parse(execFileSync(python, ['-B', '-c', `
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'runtime'));sys.path.insert(0,str(Path.cwd()/'tests'))
from test_operation_recovery import OperationRecoveryTests
from dot_runtime.controller import atomic_json,read_json
from dot_runtime.operation_recovery import boot_identity
f=OperationRecoveryTests();f.setUp()
marker=read_json(f.task/'interrupted.json')
marker['process']={'pid':123456,'boot_id':'fixture-recorded-previous-boot','status':'UNKNOWN'}
atomic_json(f.task/'interrupted.json',marker)
state=read_json(f.run/'state.json');state['active_run']['process']=marker['process'];atomic_json(f.run/'state.json',state)
f.prepare();f.probe.stop();f.temp._finalizer.detach()
print(json.dumps({'task':str(f.task),'directory':str(f.base),'session':f.session,'boot_available':bool(boot_identity())}))
`], {cwd: root, encoding: 'utf8', windowsHide: true}));
  t.after(async () => {
    const path = resolve(created.directory), part = relative(resolve(tmpdir()), path);
    if (!part || part.startsWith('..') || isAbsolute(part)) throw Error('Unexpected fixture cleanup target');
    await rm(path, {recursive: true, force: true});
  });
  if (!created.boot_available) { t.skip('OS boot identity unavailable; production keeps scan/attestation fallback'); return; }
  const statePath = resolve(created.task, 'control/run/state.json');
  const originalState = JSON.parse(await readFile(statePath, 'utf8'));
  const originalConfirmation = await readFile(resolve(created.task, 'confirmation.json'));
  const calls = [], questions = [];
  const call = (action, payload = {}) => {
    calls.push(action);
    return pythonCall(python, resolve(root, 'scripts/harness_workflow.py'),
      {action, task: created.task, session: created.session, ...payload});
  };
  const ctx = {userQuestions: {ask: async ({questions: q}) => {
    questions.push(...q);
    return {answers: [{id: q[0].id, selected: [q[0].options[0].label]}]};
  }}};
  const result = await recoverOperation({call, ctx, exec: {agent: {id: created.session}}});
  assert.equal(result.status, 'OPERATION_RECONCILED');
  assert.deepEqual(calls, ['operation_preview', 'operation_commit']);
  assert.equal(questions.length, 1);
  const after = JSON.parse(await readFile(statePath, 'utf8'));
  assert.equal(after.state, 'DECISION_PENDING');
  assert.equal(after.active_run, null);
  assert.equal(after.implementation_attempts, originalState.implementation_attempts);
  assert.deepEqual(after.completed_tasks, originalState.completed_tasks);
  assert.deepEqual(await readFile(resolve(created.task, 'confirmation.json')), originalConfirmation);
  assert.equal(result.product_execution_started, false);
});

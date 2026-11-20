import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { reportDelivery } from '../report-delivery.js';
import {withFileAccess} from '../file-access.js';

const sha = value => createHash('sha256').update(value).digest('hex');
async function fixture(t) {
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-report-chat-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const task = resolve(directory, 'tasks', sha('session-1'));
  await mkdir(task, { recursive: true });
  const path = resolve(task, 'audit.md'), text = '# 需求审核\n\nFAIL：缺少硬件验收证据。\n';
  await writeFile(path, text);
  const events = [], handlers = {};
  const exec = { agent: { id: 'session-1', session: { append: (name, payload) => events.push({ name, payload }) } },
    signal: new AbortController().signal, callId: 'call-1' };
  const ctx = { on: (name, fn) => { handlers[name] = fn; },
    agents: { turnBoundaryOf: () => ({ openTurnStartSeq: 3, lastTurn: 2 }) },
    fs: { resolve: async path => path, stat: async () => ({ type: 'file' }), processPath: path => path } };
  const value = { accepted: false, state: 'REVIEW_PENDING', operation_status: 'COMPLETED_REJECTED',
    report_files: [{ path, title: '需求审核', mime_type: 'text/markdown', sha256: sha(text) }] };
  return { directory, task, path, text, exec, ctx, events, handlers, value, deliver: reportDelivery(ctx, directory) };
}

test('successful tool result presents the verified Markdown in its owning chat', async t => {
  const f = await fixture(t), value = await f.deliver(f.value, f.exec);
  assert.equal(value.report_delivery.status, 'READY');
  assert.equal(value.accepted, false); // An audit rejection still has a report to deliver.
  assert.deepEqual(f.events, []);
  f.handlers['tools/result'](f.exec, { isError: false });
  assert.deepEqual(f.events, [{ name: 'deliverables/presented', payload: {
    turn: 2, callId: 'call-1', files: [{ path: f.path, description: '需求审核' }],
  } }]);
  f.handlers['tools/result'](f.exec, { isError: false });
  assert.equal(f.events.length, 1);
});

test('report alternate view must match its original hash before registering the chat card',async t=>{
  const f=await fixture(t);
  await withFileAccess({nativeRead:async()=>Buffer.from('vendor envelope'),
    bridge:async request=>request.paths.map(()=>Buffer.from(f.text).toString('base64'))},async()=>{
      const result=await f.deliver(f.value,f.exec);assert.equal(result.report_delivery.status,'READY');
    });
  f.handlers['tools/result'](f.exec,{isError:false});assert.equal(f.events.length,1);
});

test('status without new reports does not resend history, failed tools do not declare delivery', async t => {
  const f = await fixture(t), status = { state: 'REPORT_PENDING' };
  assert.equal(await f.deliver(status, f.exec), status);
  await f.deliver(f.value, f.exec);
  f.handlers['tools/result'](f.exec, { isError: true });
  assert.deepEqual(f.events, []);
});

for (const fault of ['changed', 'other-session', 'host-unreadable', 'no-open-turn'])
  test(`delivery ${fault} preserves completed audit and reports display failure`, async t => {
    const f = await fixture(t);
    if (fault === 'changed') await writeFile(f.path, '# changed');
    if (fault === 'other-session') {
      const outside = resolve(f.directory, 'other.md');
      await writeFile(outside, f.text); f.value.report_files[0].path = outside;
    }
    if (fault === 'host-unreadable') f.ctx.fs.stat = async () => undefined;
    if (fault === 'no-open-turn') f.ctx.agents.turnBoundaryOf = () => ({ openTurnStartSeq: null });
    const value = await f.deliver(f.value, f.exec);
    assert.equal(value.report_delivery.status, 'FAILED');
    assert.equal(value.operation_status, 'COMPLETED_REJECTED');
    assert.deepEqual(value.report_files, f.value.report_files);
    f.handlers['tools/result'](f.exec, { isError: false });
    assert.deepEqual(f.events, []);
  });

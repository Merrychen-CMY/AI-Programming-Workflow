import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { hostTools } from '../host-tools.js';
import { pythonCall } from '../process.js';

function fixture(dispatch) {
  const exec = { agent: { id: 'session' }, toolSet: { admitted: true }, token: {}, rootCallId: 'outer',
    signal: new AbortController().signal, deferContext: c => contexts.push(c), concludeTurn: () => { concluded = true; } };
  const contexts = []; let concluded = false;
  let names = ['read_image', 'pwsh', 'jobs', 'subagent', 'workflow_run', 'l1_approve', 'dot_plan'];
  const ctx = { tools: { schemas(agent, toolSet) {
    assert.equal(agent, exec.agent); assert.equal(toolSet, exec.toolSet);
    return names.map(name => ({ name, description: name, parameters: { type: 'object' } }));
  } }, get: () => ({ readImage: async () => ({ data: Buffer.from('image-fixture') }) }) };
  const relay = hostTools(ctx, exec, { dispatch, controlTools: ['l1_approve', 'dot_plan'] });
  return { relay, exec, ctx, contexts, concluded: () => concluded, remove: name => { names = names.filter(n => n !== name); } };
}
const call = (name, args = {}) => ({ operation: 'call', arguments: { name, arguments_json: JSON.stringify(args) } });

test('discover scoped tools dynamically; governance is excluded but general workflows remain', async () => {
  let invoked = false;
  const f = fixture(async () => { invoked = true; });
  const result = await f.relay({ operation: 'list', arguments: { query: '', offset: 0 } });
  assert.deepEqual(result.tools.map(x => x.name), ['read_image', 'pwsh', 'jobs', 'subagent', 'workflow_run']);
  f.remove('jobs');
  for (const name of ['jobs', 'hidden-tool', 'l1_approve', 'dot_plan'])
    assert.equal((await f.relay(call(name))).status, 'FAIL');
  assert.equal(invoked, false);
});

test('dispatch retains host scope, admission, parent identity, signal and authoritative result projection', async () => {
  let received;
  const f = fixture(async (ctx, input) => {
    received = input;
    return { isError: false, value: { secret: 'never expose raw tool value' },
      content: [{ type: 'text', text: 'redacted by host' }], additionalContexts: [{ content: 'skill guidance' }], concludesTurn: true };
  });
  const result = await f.relay(call('pwsh', { command: 'version' }));
  assert.equal(received.agent, f.exec.agent); assert.equal(received.toolSet, f.exec.toolSet);
  assert.equal(received.parent, f.exec.token); assert.equal(received.signal, f.exec.signal);
  assert.deepEqual(received.arguments, { command: 'version' });
  assert.equal(result.status, 'PASS'); assert.equal(result.concludes_turn, true);
  assert.equal(f.concluded(), true); assert.equal(f.contexts.length, 1);
  assert.equal(JSON.stringify(result).includes('never expose'), false);
});

test('host denial is a known failure; cancellation after dispatch remains unresolved', async () => {
  for (const [code, expected] of [['APPROVAL_REJECTED', 'FAIL'], ['ABORTED_BEFORE_DISPATCH', 'FAIL'], ['ABORTED', 'UNKNOWN']]) {
    const f = fixture(async () => ({ isError: true, content: [], error: { message: code, info: { code } } }));
    assert.equal((await f.relay(call('pwsh'))).status, expected);
  }
});

test('image content is loaded through attachment verification and passed as actual input_image bytes', async () => {
  const f = fixture(async () => ({ isError: false, content: [
    { type: 'image', attachment: { mediaType: 'image/png', attachmentId: 'fixture' } }] }));
  const result = await f.relay(call('read_image'));
  assert.equal(result.images[0].type, 'input_image');
  assert.equal(result.images[0].image_url, 'data:image/png;base64,' + Buffer.from('image-fixture').toString('base64'));
});

test('real bidirectional Python pipe invokes host twice and returns final result without EOF deadlock', async t => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const dir = await mkdtemp(resolve(tmpdir(), 'reqpilot-relay-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const script = resolve(dir, 'relay.py');
  await writeFile(script, `import json,sys
sys.path.insert(0,${JSON.stringify(resolve(root, 'runtime'))})
from dot_runtime.host_tools import pipe_relay
request=json.loads(sys.stdin.readline())
relay=pipe_relay(sys.stdin,sys.stdout)
a=relay({'operation':'list','arguments':{},'timeout_seconds':10})
b=relay({'operation':'call','arguments':{},'timeout_seconds':10})
print(json.dumps({'ok':True,'result':[a,b]}))
`);
  const operations = [];
  const result = await pythonCall(process.env.PYTHON || 'python', script, {task: dir, session: 'fixture'}, { key: 'fixture-secret',
    hostRelay: async request => { operations.push(request.operation); return { status: 'PASS', summary: 'fixture-secret' }; } });
  assert.deepEqual(operations, ['list', 'call']);
  assert.equal(result[0].summary, '[REDACTED]'); assert.equal(result.length, 2);
  const receipts = await readdir(resolve(dir, 'host-tool-calls'));
  assert.equal(receipts.length, 2);
  for (const name of receipts) {
    const receipt = JSON.parse(await readFile(resolve(dir, 'host-tool-calls', name), 'utf8'));
    assert.equal(receipt.status, 'RETURNED'); assert.equal(receipt.result.summary, '[REDACTED]');
  }
});

test('cancellation drains native operation and retains its receipt before returning unresolved status', async t => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const dir = await mkdtemp(resolve(tmpdir(), 'reqpilot-relay-cancel-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const script = resolve(dir, 'relay.py');
  await writeFile(script, `import json,sys
sys.path.insert(0,${JSON.stringify(resolve(root, 'runtime'))})
from dot_runtime.host_tools import pipe_relay
json.loads(sys.stdin.readline())
result=pipe_relay(sys.stdin,sys.stdout)({'operation':'call','arguments':{},'timeout_seconds':10})
print(json.dumps({'ok':True,'result':result}))
`);
  const controller = new AbortController(); let drained = false;
  await assert.rejects(pythonCall(process.env.PYTHON || 'python', script, {task: dir}, {
    signal: controller.signal, hostRelay: async (_request, signal) => {
      setTimeout(() => controller.abort(), 5);
      await new Promise(resolve => signal.addEventListener('abort', () => setTimeout(resolve, 20), {once: true}));
      drained = true;
      return {status: 'UNKNOWN', summary: 'Interrupted after starting; inspect host evidence'};
    }}), /UNKNOWN/);
  assert.equal(drained, true);
  const [name] = await readdir(resolve(dir, 'host-tool-calls'));
  assert.equal(JSON.parse(await readFile(resolve(dir, 'host-tool-calls', name), 'utf8')).status, 'RETURNED');
});

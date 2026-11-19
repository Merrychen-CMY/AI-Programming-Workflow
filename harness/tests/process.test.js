import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pythonCall } from '../process.js';

function fakeChild() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stdout.setEncoding = () => {};
  child.stderr = new EventEmitter(); child.stdin = new EventEmitter(); child.stdin.end = () => {};
  child.kill = () => { child.killed = true; };
  return child;
}

test('output overflow is distinguishable from caller cancellation and never reported as success', async () => {
  const child = fakeChild();
  const request = pythonCall('fixture', 'fixture', {}, { spawnImpl: () => child });
  child.stdout.emit('data', 'x'.repeat(8_000_001));
  await assert.rejects(request, /OUTPUT_LIMIT:.*may already be recorded/);
  assert.equal(child.killed, true);
  const other = fakeChild(), controller = new AbortController();
  const cancelled = pythonCall('fixture', 'fixture', {}, { spawnImpl: () => other, signal: controller.signal });
  controller.abort();
  await assert.rejects(cancelled, /UNKNOWN: caller interrupted/);
});

test('real Python pipe returns separate oversized approval journals as bounded actionable summaries', async t => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-reply-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const script = resolve(directory, 'reply.py');
  await writeFile(script, `import json,sys,copy
sys.path.insert(0,${JSON.stringify(resolve(root, 'runtime'))})
from dot_runtime.presentation import compact
json.load(sys.stdin)
review={'kind':'requirements','status':'PASS','result':{'summary':'Approval saved','findings':[], 'runtime_evidence': {'output':'log'*3000000}},'evidence_files':[]}
journal={'state':'REQUIREMENTS_APPROVED','reviews':[review],'approval':{'review_number':1}}
value={'state':'REQUIREMENTS_APPROVED','requirements_review':copy.deepcopy(journal),'requirements_approval':copy.deepcopy(journal)}
print(json.dumps({'ok':True,'result':compact(value)}))
`);
  const result = await pythonCall(process.env.PYTHON || 'python', script, {});
  assert.ok(Buffer.byteLength(JSON.stringify(result)) < 40000);
  assert.equal(result.requirements_approval.latest_review.status, 'PASS');
  assert.equal(result.requirements_approval.review_count, 1);
  assert.match(result.next, /dot_plan/);
});

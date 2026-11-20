import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { standardStatus } from '../standard-status.js';

test('standards comparison reports actual added/changed clauses without adopting them', async t => {
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-standard-status-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  async function version(name, revision, requirements, supplemental_requirements = []) {
    const root = resolve(directory, name);
    const path = resolve(root, 'standards/embedded-c/v1.3/project-constraints/adoption.json');
    await mkdir(resolve(path, '..'), { recursive: true });
    await writeFile(path, JSON.stringify({revision, requirements, supplemental_requirements}));
    return {root, version: name, path};
  }
  const old = await version('3.22.2', 4, [{id: 'PC-U16', requirements: ['old']}], [{id: 'PC-M01'}]);
  const current = await version('3.23.0', 5, [{id: 'PC-U16', requirements: ['new']}], [{id: 'PC-M01'}, {id: 'PC-M02'}]);
  const before = await readFile(old.path);
  const evidence = {independent_review_status: 'NOT_RUN'};
  const result = await standardStatus(old, current, evidence);
  assert.deepEqual(result.added_rules, ['PC-M02']);
  assert.deepEqual(result.changed_rules, ['PC-U16']);
  assert.deepEqual(result.removed_rules, []);
  assert.equal(result.task.project_constraints_revision, 4);
  assert.equal(result.available_verified.project_constraints_revision, 5);
  assert.equal(result.candidate_review.independent_review_status, 'NOT_RUN');
  assert.deepEqual(await readFile(old.path), before);
  const unavailable = await standardStatus(old, null);
  assert.equal(unavailable.comparison, 'UNAVAILABLE');
  assert.equal(unavailable.added_rules, null);
  const legacy = await standardStatus({root: directory, version: 'legacy'}, current);
  assert.equal(legacy.task.project_constraints_revision, null);
  assert.equal(legacy.candidate_review, null);
});

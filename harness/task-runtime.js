import { readFile } from './file-access.js';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';

const sha = value => createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(value && typeof value === 'object'
  ? Array.isArray(value) ? value.map(v => JSON.parse(canonical(v)))
    : Object.fromEntries(Object.keys(value).sort().map(k => [k, JSON.parse(canonical(value[k]))])) : value);

/** Original startup receipt stays immutable; a reviewed, chained upgrade selects the effective runtime. */
export async function taskRuntime(task, receipt) {
  let journal;
  try { journal = JSON.parse(await readFile(resolve(task, 'runtime-upgrades.json'), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return receipt.settings.workflow_sha256; throw error; }
  const original = sha(await readFile(resolve(task, 'confirmation.json')));
  let current = receipt.settings.workflow_sha256, previous = null, sequence = 0;
  if (journal.schema_version !== 1 || !Array.isArray(journal.records) || !journal.records.length)
    throw new Error('TASK_UPGRADE_JOURNAL_INVALID');
  for (const record of journal.records) {
    const { sha256, ...body } = record;
    if (sha(canonical(body)) !== sha256 || record.previous_sha256 !== previous || record.sequence !== ++sequence
        || record.source?.manifest_sha256 !== current || !/^[a-f0-9]{64}$/.test(record.target?.manifest_sha256)
        || record.authority !== 'local_harness_user' || record.session !== receipt.session
        || record.original_confirmation_sha256 !== original) throw new Error('TASK_UPGRADE_JOURNAL_INVALID');
    current = record.target.manifest_sha256; previous = sha256;
  }
  return current;
}

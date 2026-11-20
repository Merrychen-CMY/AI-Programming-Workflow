import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { composeGuidance } from '../guidance.js';

test('only exact cross-file version notes are deduplicated; scope and code survive', () => {
  const repeated = '3.19.2 ' + 'Keep approval and evidence. '.repeat(30);
  const different = repeated + ' Extra constraint.';
  const first = `# Role one\n\n${repeated}\n\n${repeated}`;
  const second = `# Role two\n\n${repeated}\n\n${different}\n\n\`\`\`text\n\n${repeated}\n\n\`\`\``;
  const result = composeGuidance([first, second]);
  assert.ok(result.startsWith(first)); // Never remove repeated wording within its original document.
  assert.ok(result.includes('# Role two'));
  assert.ok(result.includes(different));
  assert.ok(result.includes(`\`\`\`text\n\n${repeated}\n\n\`\`\``));
  assert.equal(result.length, `${first}\n\n${second}`.length - repeated.length - 2);
});

test('current L1 entry material is smaller and keeps each source version note', async () => {
  const documents = await Promise.all(['harness/prompt.md', 'AGENTS.md', 'L1.md'].map(path =>
    readFile(new URL(`../../${path}`, import.meta.url), 'utf8')));
  const result = composeGuidance(documents);
  assert.ok(Buffer.byteLength(result) < Buffer.byteLength(documents.join('\n\n')));
  for (const paragraph of documents.flatMap(text => text.replaceAll('\r\n', '\n').split('\n\n')))
    if (/^\d+\.\d+\.\d+ /.test(paragraph) && !paragraph.includes('\n')) assert.ok(result.includes(paragraph));
});

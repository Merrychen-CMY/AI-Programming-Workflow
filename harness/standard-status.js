import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const catalogPath = 'standards/embedded-c/v1.3/project-constraints/adoption.json';

// Call only with roots already verified by the runtime selector. Never fetch or adopt here.
async function catalog(runtime) {
  try {
    const data = JSON.parse(await readFile(resolve(runtime.root, catalogPath), 'utf8'));
    return { version: runtime.version, revision: data.revision,
      clauses: [...data.requirements, ...(data.supplemental_requirements || [])] };
  } catch (error) {
    if (error.code === 'ENOENT') return { version: runtime.version, revision: null, clauses: null };
    throw error;
  }
}

export async function standardStatus(runtime, available, evidence = null) {
  const current = await catalog(runtime);
  const target = available ? await catalog(available) : null;
  const known = current.clauses !== null && target?.clauses != null;
  const old = new Map((current.clauses || []).map(row => [row.id, row]));
  const newer = new Map((target?.clauses || []).map(row => [row.id, row]));
  return {
    task: { version: current.version, project_constraints_revision: current.revision },
    available_verified: target ? { version: target.version, project_constraints_revision: target.revision } : null,
    comparison: known ? 'KNOWN' : 'UNAVAILABLE',
    added_rules: known ? [...newer.keys()].filter(id => !old.has(id)) : null,
    changed_rules: known ? [...newer.keys()].filter(id => old.has(id) &&
      JSON.stringify(newer.get(id)) !== JSON.stringify(old.get(id))) : null,
    removed_rules: known ? [...old.keys()].filter(id => !newer.has(id)) : null,
    candidate_review: evidence,
    scope: 'C/H project requirements; available means verified local release, not a fresh remote check',
    instruction: 'Difference is informational, not a task failure. L1 assesses applicability; use existing upgrade approval and affected-baseline review. Never rewrite history or apply new rules to an old approval silently.'
  };
}

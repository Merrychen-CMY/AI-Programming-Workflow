/** Validate preset schemas against an installed/extracted Harness node_modules tree.
 * Requires Node 24. Does not start agents, execute tools, read credentials or call APIs.
 * Usage: node scripts/verify_harness_preset.mjs <host-node-modules-directory>
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, registerHooks } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
if (!process.argv[2]) throw new Error('Supply the Harness node_modules directory');
const hostRequire = createRequire(resolve(process.argv[2], '../reqpilot-schema-check.cjs'));
const hostImport = name => import(pathToFileURL(hostRequire.resolve(name)).href);
const yaml = (await hostImport('js-yaml')).default;
// Do not evaluate arbitrary YAML JavaScript. Recognize only the preset's existing expressions.
const schema = yaml.DEFAULT_SCHEMA.extend([new yaml.Type('tag:yaml.org,2002:js', {
  kind: 'scalar', construct(value) {
    if (value === "process.platform !== 'win32'") return process.platform !== 'win32';
    if (value === "process.platform === 'win32'") return process.platform === 'win32';
    if (value === "!ctx.get('profileContext')") return false;
    if (value === "process.getBuiltinModule('node:path').join(process.getBuiltinModule('node:path').dirname(process.getBuiltinModule('node:module').createRequire(baseUrl).resolve('@deepseek-ai/dsh-agent-preset/package.json')), 'skills')")
      return resolve(dirname(hostRequire.resolve('@deepseek-ai/dsh-agent-preset/package.json')), 'skills');
    throw new Error(`Unrecognized preset expression: ${value}`);
  },
})]);
const preset = yaml.load(readFileSync(resolve(root, 'harness/cordis.patch.yml'), 'utf8'), { schema })[0].insert[0];
const checked = [];
async function check(entry) {
  if (entry.name.startsWith('@deepseek-ai/')) {
    const module = await hostImport(entry.name);
    if (module.Config) module.Config(structuredClone(entry.config || {}));
    checked.push(entry.name);
  }
  for (const child of (entry.group ? entry.config : entry.config?.plugins) || []) await check(child);
}
await check(preset);
const search = preset.config.plugins.find(entry => entry.id === 'tool-fs-search');
const { Config } = await hostImport(search.name);
assert.equal(Config(structuredClone(search.config)).sampleOverCapGlobResults, false);
const missing = structuredClone(search.config);
delete missing.sampleOverCapGlobResults;
assert.throws(() => Config(missing), /sampleOverCapGlobResults/);
assert.throws(() => Config({ ...search.config, sampleOverCapGlobResults: 'false' }), /sampleOverCapGlobResults/);

// Resolve shared host packages as Harness does; no physical node_modules is added to the repo.
const hook = registerHooks({ resolve(specifier, context, next) {
  if (specifier.startsWith('@deepseek-ai/') && context.parentURL?.startsWith(pathToFileURL(root).href))
    return next(pathToFileURL(hostRequire.resolve(specifier)).href, context);
  return next(specifier, context);
} });
const registered = [], effects = [];
try {
  const plugin = await import(new URL('../harness/index.js', import.meta.url));
  await plugin.apply({ tools: { register: definition => registered.push(definition) },
    on: () => {},
    systemPrompt: { section: section => {
      assert.ok(section.text.includes('L1'));
      assert.ok(section.text.includes('各层主动排障：遇到流程阻塞'));
      assert.ok(section.text.includes('你作为L1收到阻塞后，先读状态、证据和具体恢复入口'));
    } },
    effect: setup => effects.push(setup()) });
  assert.ok(registered.some(tool => tool.name === 'workflow_updates'));
  assert.ok(registered.some(tool => tool.name === 'dot_plan'));
} finally {
  for (const dispose of effects.reverse()) dispose?.();
  hook.deregister();
}
console.log(JSON.stringify({ status: 'PASS', host_plugins_checked: checked.length,
  registered_workflow_tools: registered.length, required_field_rejections: 2,
  scope: 'Real host schemas and entry imports; fixture services for plugin activation. No live Harness UI or product task.' }));

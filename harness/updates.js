import { readFile, writeFile, mkdir, mkdtemp, rename, rm, open, lstat } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { githubCredential } from './github-auth.js';
import { taskRuntime } from './task-runtime.js';

export const REPOSITORY = 'Merrychen-CMY/ReqPilot';
export const BOOTSTRAP_ABI = 8;
export const CHECK_INTERVAL = 6 * 60 * 60 * 1000;
const MAX_ARCHIVE = 8_000_000, MAX_EXPANDED = 32_000_000;
const sha = data => createHash('sha256').update(data).digest('hex');
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const version = value => typeof value === 'string' && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value);
export const newer = (a, b) => {
  if (!version(a) || !version(b)) return false;
  const x = a.split('.').map(Number), y = b.split('.').map(Number);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
  return false;
};
const fail = code => { throw new Error(code); };
const json = async path => JSON.parse(await readFile(path, 'utf8'));
async function optional(path) {
  try { return await json(path); } catch (e) { if (e.code === 'ENOENT') return null; throw e; }
}
async function atomic(path, value) {
  const temp = `${path}.${randomUUID()}.tmp`;
  try { await writeFile(temp, JSON.stringify(value, null, 2), { mode: 0o600, flag: 'wx' }); await rename(temp, path); }
  finally { await rm(temp, { force: true }); }
}
function safePath(path) {
  if (typeof path !== 'string' || path.length > 240 || /[\\:\x00-\x1f<>"|?*]/.test(path)) return false;
  return path.split('/').every(part => part && !['.', '..', '.git', 'node_modules', '__pycache__'].includes(part)
    && !part.startsWith('.env') && !/[. ]$/.test(part) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part));
}
function inventory(manifest, manifestHash) {
  if (!manifest || manifest.schema_version !== 1 || !version(manifest.workflow_version)
      || !manifest.files || Array.isArray(manifest.files) || typeof manifest.files !== 'object'
      || !digest(manifestHash)) fail('UPDATE_MANIFEST');
  const names = Object.keys(manifest.files), folded = new Set(['manifest.json']);
  if (names.length < 1 || names.length > 2000 || Object.hasOwn(manifest.files, 'MANIFEST.json')) fail('UPDATE_MANIFEST');
  for (const path of names) {
    if (!safePath(path) || !digest(manifest.files[path]) || folded.has(path.toLowerCase())) fail('UPDATE_PATH');
    folded.add(path.toLowerCase());
  }
  for (const path of ['package.json', 'harness/workflow.js', 'harness/prompt.md', 'AGENTS.md', 'L1.md', 'scripts/harness_workflow.py'])
    if (!names.includes(path)) fail('UPDATE_MANIFEST');
  return names;
}
function packageIdentity(data, expectedVersion, allowedAbis = [BOOTSTRAP_ABI]) {
  const pkg = JSON.parse(data.toString('utf8'));
  if (pkg.name !== 'ai-programming-workflow' || pkg.version !== expectedVersion
      || !allowedAbis.includes(pkg.reqpilot?.bootstrap_abi)) fail('HOST_UPDATE_REQUIRED');
}
/** Manifest is an integrity check. Publisher trust comes from the fixed GitHub HTTPS origin. */
export function decodeRuntime(archive, metadata) {
  if (archive.length > MAX_ARCHIVE || !digest(metadata.sha256) || sha(archive) !== metadata.sha256) fail('UPDATE_DIGEST');
  const payload = JSON.parse(gunzipSync(archive, { maxOutputLength: MAX_EXPANDED }).toString('utf8'));
  if (payload.format !== 1 || !payload.files || typeof payload.files !== 'object' || Array.isArray(payload.files)) fail('UPDATE_ARCHIVE');
  const files = new Map(); let size = 0;
  for (const [path, encoded] of Object.entries(payload.files)) {
    if (!safePath(path) || typeof encoded !== 'string') fail('UPDATE_PATH');
    const data = Buffer.from(encoded, 'base64');
    if (data.toString('base64') !== encoded || (size += data.length) > MAX_EXPANDED) fail('UPDATE_ARCHIVE');
    files.set(path, data);
  }
  const raw = files.get('MANIFEST.json');
  if (!raw || sha(raw) !== metadata.manifest_sha256) fail('UPDATE_DIGEST');
  const manifest = JSON.parse(raw.toString('utf8')), names = inventory(manifest, metadata.manifest_sha256);
  if (manifest.workflow_version !== metadata.version || files.size !== names.length + 1) fail('UPDATE_MANIFEST');
  for (const path of names) if (!files.has(path) || sha(files.get(path)) !== manifest.files[path]) fail('UPDATE_DIGEST');
  packageIdentity(files.get('package.json'), metadata.version);
  return files;
}
export async function verifyRuntime(root, expected, { pinned = false } = {}) {
  if ((await lstat(root)).isSymbolicLink()) fail('RUNTIME_INTEGRITY');
  const raw = await readFile(resolve(root, 'MANIFEST.json')), hash = sha(raw);
  if (expected && hash !== expected) fail('RUNTIME_INTEGRITY');
  const manifest = JSON.parse(raw.toString('utf8')), names = inventory(manifest, hash);
  let size = 0;
  // Parent components must not redirect a manifest path outside the cached version.
  for (const path of ['MANIFEST.json', ...names]) {
    let current = root;
    for (const part of path.split('/')) {
      current = resolve(current, part);
      if ((await lstat(current)).isSymbolicLink()) fail('RUNTIME_INTEGRITY');
    }
    const stat = await lstat(current);
    if (!stat.isFile() || (size += stat.size) > MAX_EXPANDED) fail('RUNTIME_INTEGRITY');
    if (path !== 'MANIFEST.json' && sha(await readFile(current)) !== manifest.files[path]) fail('RUNTIME_INTEGRITY');
  }
  // Retain old engines until a separately reviewed migration through the stable tool contract.
  packageIdentity(await readFile(resolve(root, 'package.json')), manifest.workflow_version, pinned ? [1, 2, 3, 4, 5, 6, 7, 8] : [BOOTSTRAP_ABI]);
  return { version: manifest.workflow_version, manifest_sha256: hash, root };
}
/** A GitHub credential is restricted to exact repository API URLs, never redirects/CDN. */
export async function download(url, limit, fetchImpl = fetch, signal, credential) {
  const allowed = new Set(['api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com']);
  const timeout = AbortSignal.timeout(15_000);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  for (let hop = 0; hop < 5; hop++) {
    const target = new URL(url);
    if (target.protocol !== 'https:' || target.username || target.password || target.port
        || !allowed.has(target.hostname)) fail('UPDATE_ORIGIN');
    const headers = { 'User-Agent': 'ReqPilot-updater', 'Accept': 'application/octet-stream' };
    if (target.hostname === 'api.github.com') {
      if (hop !== 0 || !new RegExp(`^/repos/${REPOSITORY}/releases/(latest|assets/[0-9]+)$`).test(target.pathname)) fail('UPDATE_ORIGIN');
      if (target.pathname.endsWith('/latest')) headers.Accept = 'application/vnd.github+json';
      if (credential) headers.Authorization = `Bearer ${credential}`;
    }
    const result = await fetchImpl(target.href, { redirect: 'manual', signal: combined, headers });
    if ([301, 302, 303, 307, 308].includes(result.status)) {
      const location = result.headers.get('location'); await result.body?.cancel();
      if (!location) fail('UPDATE_HTTP'); url = new URL(location, target).href; continue;
    }
    if (result.status !== 200) { await result.body?.cancel(); fail('UPDATE_HTTP'); }
    if (Number(result.headers.get('content-length')) > limit) { await result.body?.cancel(); fail('UPDATE_SIZE'); }
    const chunks = []; let size = 0;
    for await (const chunk of result.body) {
      if ((size += chunk.length) > limit) fail('UPDATE_SIZE'); chunks.push(chunk);
    }
    return Buffer.concat(chunks);
  }
  fail('UPDATE_REDIRECT');
}
function contract(fields) {
  return JSON.stringify(Object.keys(fields).sort().map(name => [name, Object.keys(fields[name]).sort()]));
}
export async function loadEngine(root, fields) {
  const engine = await import(pathToFileURL(resolve(root, 'harness/workflow.js')).href);
  const pkg = JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8'));
  const version6Fields = Object.fromEntries(Object.entries(fields).filter(([name])=>
    !['l1_prepare_requirements','dot_investigate','l1_resume_decision','l1_prepare_recovery','workflow_recover_operation','workflow_confirm_stop'].includes(name)));
  const version5Fields = Object.fromEntries(Object.entries(version6Fields).filter(([name])=>name !== 'dot_read_record'));
  const legacyFields = Object.fromEntries(Object.entries(version5Fields).filter(([name])=>
    !['l1_review_api','workflow_review_api_retry'].includes(name)));
  const same = engine.toolFields && (contract(engine.toolFields) === contract(fields) ||
    (pkg.reqpilot?.bootstrap_abi === 6 && contract(engine.toolFields) === contract(version6Fields)) ||
    (pkg.reqpilot?.bootstrap_abi === 5 && contract(engine.toolFields) === contract(version5Fields)) ||
    (pkg.reqpilot?.bootstrap_abi <= 4 && contract(engine.toolFields) === contract(legacyFields)));
  if (typeof engine.createWorkflow !== 'function' || !same)
    fail('HOST_UPDATE_REQUIRED');
  return engine;
}

export function createUpdates({ root, directory, fields, fetchImpl = fetch, now = Date.now, credentialProvider = githubCredential }) {
  const base = resolve(directory, 'updates'), cache = resolve(base, 'runtimes');
  const statePath = resolve(base, 'state.json'), settingsPath = resolve(base, 'settings.json');
  let bundled, pending, stopped = false;
  const abort = new AbortController();
  async function status() {
    return { enabled: (await optional(settingsPath))?.enabled !== false,
      ...(await optional(statePath) || { status: 'NOT_CHECKED' }), bootstrap_abi: BOOTSTRAP_ABI };
  }
  async function locked(action) {
    await mkdir(cache, { recursive: true, mode: 0o700 });
    const path = resolve(base, 'install.lock'); let handle;
    try { handle = await open(path, 'wx', 0o600); }
    catch (e) { if (e.code === 'EEXIST') fail('UPDATE_BUSY'); throw e; }
    try { await handle.writeFile(JSON.stringify({ pid: process.pid, at: now() })); return await action(); }
    finally { await handle.close(); await rm(path, { force: true }); }
  }
  async function store(files, hash) {
    const target = resolve(cache, hash);
    try { await lstat(target); return await verifyRuntime(target, hash); }
    catch (e) { if (e.code !== 'ENOENT') throw e; }
    const stage = await mkdtemp(resolve(cache, '.stage-'));
    try {
      for (const [path, data] of files) {
        const destination = resolve(stage, path);
        await mkdir(dirname(destination), { recursive: true });
        await writeFile(destination, data, { flag: 'wx', mode: 0o600 });
      }
      await verifyRuntime(stage, hash);
      await rename(stage, target);
      return await verifyRuntime(target, hash);
    } finally { await rm(stage, { recursive: true, force: true }); }
  }
  async function ensureBundled() {
    if (!bundled) bundled = (async () => {
      const original = await verifyRuntime(root);
      try { return await verifyRuntime(resolve(cache, original.manifest_sha256), original.manifest_sha256); }
      catch (e) { if (e.code !== 'ENOENT') throw e; }
      return await locked(async () => {
        const raw = await readFile(resolve(root, 'MANIFEST.json'));
        const manifest = JSON.parse(raw), files = new Map([['MANIFEST.json', raw]]);
        for (const path of Object.keys(manifest.files)) files.set(path, await readFile(resolve(root, path)));
        return await store(files, original.manifest_sha256);
      });
    })().catch(error => { bundled = undefined; throw error; });
    return await bundled;
  }
  async function selected() {
    const installed = await ensureBundled(), state = await optional(statePath);
    if (state?.active && digest(state.active.manifest_sha256) && newer(state.active.version, installed.version))
      return await verifyRuntime(resolve(cache, state.active.manifest_sha256), state.active.manifest_sha256);
    return await verifyRuntime(installed.root, installed.manifest_sha256);
  }
  async function check() {
    if (pending) return pending;
    pending = (async () => {
      const installed = await ensureBundled();
      if (stopped || !(await status()).enabled) return status();
      return await locked(async () => {
        const old = await optional(statePath) || {};
        const state = { ...old, checked_at: now() };
        try {
          // Public assets need no credentials. Private repos reuse an existing local Git login.
          let metadata, privateRelease, credential;
          try {
            metadata = JSON.parse((await download(`https://github.com/${REPOSITORY}/releases/latest/download/reqpilot-update.json`, 16_000, fetchImpl, abort.signal)).toString('utf8'));
          } catch {
            credential = await credentialProvider();
            if (!credential) fail('UPDATE_GITHUB_LOGIN_REQUIRED');
            const api = `https://api.github.com/repos/${REPOSITORY}/releases/`;
            privateRelease = JSON.parse((await download(api + 'latest', 1_000_000, fetchImpl, abort.signal, credential)).toString('utf8'));
            if (privateRelease.draft !== false || privateRelease.prerelease !== false || !Array.isArray(privateRelease.assets)) fail('UPDATE_RELEASE');
            const asset = privateRelease.assets.find(item => item.name === 'reqpilot-update.json');
            if (!Number.isSafeInteger(asset?.id) || asset.id < 1) fail('UPDATE_RELEASE');
            metadata = JSON.parse((await download(api + 'assets/' + asset.id, 16_000, fetchImpl, abort.signal, credential)).toString('utf8'));
            if (privateRelease.tag_name !== `v${metadata.version}`) fail('UPDATE_RELEASE');
          }
          const target = metadata.version;
          if (metadata.format !== 1 || metadata.channel !== 'stable' || !version(target)) fail('UPDATE_RELEASE');
          const current = await selected();
          if (!newer(target, current.version)) state.status = 'UP_TO_DATE';
          else if (target.split('.')[0] !== installed.version.split('.')[0]) {
            state.status = 'HOST_UPDATE_REQUIRED'; state.available_version = target;
          } else {
            state.available_version = target;
            const prefix = `https://github.com/${REPOSITORY}/releases/download/v${target}/`;
            if (metadata.bootstrap_abi !== BOOTSTRAP_ABI) fail('HOST_UPDATE_REQUIRED');
            let url = prefix + 'reqpilot-runtime.json.gz';
            if (privateRelease) {
              const asset = privateRelease.assets.find(item => item.name === 'reqpilot-runtime.json.gz');
              if (!Number.isSafeInteger(asset?.id) || asset.id < 1) fail('UPDATE_RELEASE');
              url = `https://api.github.com/repos/${REPOSITORY}/releases/assets/${asset.id}`;
            }
            const data = await download(url, MAX_ARCHIVE, fetchImpl, abort.signal, credential);
            const files = decodeRuntime(data, metadata);
            const candidate = await store(files, metadata.manifest_sha256);
            await loadEngine(candidate.root, fields);
            if (stopped || !(await status()).enabled) return status();
            state.previous = old.active || { version: installed.version, manifest_sha256: installed.manifest_sha256 };
            state.active = { version: candidate.version, manifest_sha256: candidate.manifest_sha256 };
            state.status = 'READY'; state.available_version = target;
          }
          delete state.error;
        } catch (error) {
          // No raw network/body/host error messages, and no changes to task files or the last-good pointer.
          state.status = error.message === 'HOST_UPDATE_REQUIRED' ? 'HOST_UPDATE_REQUIRED' : 'CHECK_FAILED';
          state.error = /^(UPDATE_[A-Z_]+|HOST_UPDATE_REQUIRED|RUNTIME_INTEGRITY)$/.test(error.message) ? error.message : 'UPDATE_UNAVAILABLE';
        }
        await atomic(statePath, state);
        return status();
      });
    })().catch(() => ({ enabled: true, status: 'CHECK_FAILED', error: 'UPDATE_BUSY_OR_UNAVAILABLE', bootstrap_abi: BOOTSTRAP_ABI }))
      .finally(() => { pending = undefined; });
    return pending;
  }
  async function schedule() {
    const info = await status();
    if (!stopped && info.enabled && (!info.checked_at || now() - info.checked_at >= CHECK_INTERVAL)) void check();
  }
  async function setEnabled(enabled) {
    await mkdir(base, { recursive: true, mode: 0o700 }); await atomic(settingsPath, { enabled });
    return status();
  }
  async function forTask(task) {
    await ensureBundled();
    if (await optional(resolve(task, 'initialization-recovery-pending.json'))) fail('INITIALIZATION_RECOVERY_PENDING_FINISH_REQUIRED');
    if (await optional(resolve(task, 'runtime-upgrade-pending.json'))) fail('TASK_UPGRADE_PENDING_RECOVER_REQUIRED');
    const receipt = await optional(resolve(task, 'confirmation.json'));
    if (receipt) {
      const hash = await taskRuntime(task, receipt);
      if (!digest(hash)) fail('TASK_RUNTIME_MISSING');
      try { return { ...await verifyRuntime(resolve(cache, hash), hash, { pinned: true }), pinned: true }; }
      catch { fail('TASK_RUNTIME_MISSING_OR_DAMAGED'); }
    }
    // Legacy interrupted tasks without a confirmation must never be switched implicitly.
    if (await optional(resolve(task, 'interrupted.json'))) fail('TASK_RUNTIME_REQUIRES_RECONCILIATION');
    return { ...await selected(), pinned: false };
  }
  async function upgradeTarget(task, recover = false) {
    if (await optional(resolve(task, 'initialization-recovery-pending.json'))) fail('INITIALIZATION_RECOVERY_PENDING_FINISH_REQUIRED');
    if (!recover) return selected();
    const pending = await optional(resolve(task, 'runtime-upgrade-pending.json'));
    if (!pending) fail('TASK_NO_PENDING_UPGRADE');
    const hash = pending.after_journal?.records?.at(-1)?.target?.manifest_sha256;
    if (!digest(hash)) fail('TASK_UPGRADE_INVALID');
    return verifyRuntime(resolve(cache, hash), hash);
  }
  async function recoveryTarget(task, finish = false) {
    if (!finish) return selected();
    const pending = await optional(resolve(task, 'initialization-recovery-pending.json'));
    const hash = pending?.entry?.target?.manifest_sha256;
    if (!digest(hash)) fail('INITIALIZATION_NO_VALID_PENDING_RECOVERY');
    return verifyRuntime(resolve(cache, hash), hash);
  }
  return { status, check, schedule, setEnabled, forTask, ensureBundled, upgradeTarget, recoveryTarget,
    dispose: () => { stopped = true; abort.abort(); }, settled: () => pending };
}

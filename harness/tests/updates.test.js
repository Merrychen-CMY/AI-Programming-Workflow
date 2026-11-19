import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, lstat, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { createUpdates, decodeRuntime, download, verifyRuntime, CHECK_INTERVAL } from '../updates.js';
import { createBootstrap, updateFields } from '../bootstrap.js';
import { toolFields as actualFields } from '../workflow.js';
import { saveConfig, keyDigest } from '../setup.js';
import { githubCredential } from '../github-auth.js';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';

const sha = data => createHash('sha256').update(data).digest('hex');
const fields = { workflow_status: {}, workflow_confirm_start: {}, dot_run: {} };
function release(v = '3.4.1', modify = files => files, abi = 7) {
  let files = {
    'package.json': JSON.stringify({ name: 'ai-programming-workflow', version: v, type: 'module', reqpilot: { bootstrap_abi: abi } }),
    'harness/workflow.js': `import {writeFile,mkdir,readFile} from 'node:fs/promises';
      import {createHash} from 'node:crypto'; import {resolve} from 'node:path';
      export const toolFields=${JSON.stringify(fields)};
      export function createWorkflow({root,directory}){return {dispose(){},async invoke(name,args,exec){
        const task=resolve(directory,'tasks',createHash('sha256').update(exec.agent.id).digest('hex'));
        if(name==='workflow_confirm_start') {await mkdir(task,{recursive:true}); await writeFile(resolve(task,'confirmation.json'),JSON.stringify({settings:{workflow_sha256:createHash('sha256').update(await readFile(resolve(root,'MANIFEST.json'))).digest('hex')}}));}
        return {fixture_version:'${v}',root};}};}`,
    'harness/prompt.md': `Guidance ${v}`, 'AGENTS.md': 'Rules', 'L1.md': 'L1 rules', 'scripts/harness_workflow.py': '# fixture only',
  };
  files = modify(files);
  files = Object.fromEntries(Object.entries(files).map(([p, text]) => [p, Buffer.from(text)]));
  files['MANIFEST.json'] = Buffer.from(JSON.stringify({ schema_version: 1, workflow_version: v,
    files: Object.fromEntries(Object.entries(files).map(([p, data]) => [p, sha(data)])) }));
  const payload = { format: 1, files: Object.fromEntries(Object.entries(files).map(([p, data]) => [p, data.toString('base64')])) };
  const archive = gzipSync(JSON.stringify(payload));
  return { files, archive, payload, metadata: { format: 1, channel: 'stable', version: v, bootstrap_abi: abi,
    sha256: sha(archive), manifest_sha256: sha(files['MANIFEST.json']) } };
}
async function put(root, files) {
  for (const [p, data] of Object.entries(files)) { await mkdir(resolve(root,p,'..'),{recursive:true}); await writeFile(resolve(root,p),data); }
}
async function fixture(t, target = release()) {
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-update-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = resolve(directory, 'installed'), data = resolve(directory, 'data'), calls = [], original = release('3.4.0');
  await put(root, original.files);
  let clock = 1000000000;
  const control = { target, offline: false, prerelease: false };
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    if (control.offline) throw new Error('fixture private error NEVER OUTPUT');
    if (url.endsWith('reqpilot-update.json')) return Response.json({...control.target.metadata,channel:control.prerelease?'preview':'stable'});
    return new Response(control.target.archive);
  };
  const options = { root, directory: data, fields, fetchImpl, now: () => clock, credentialProvider: async()=>null };
  const updates = createUpdates(options); t.after(() => updates.dispose());
  const session = 'fixture-session', exec = {agent: {id: session}}, task = resolve(data,'tasks',sha(session));
  const bootstrap = createBootstrap({ctx:{}, root, directory:data, fields, updates}); t.after(() => bootstrap.dispose());
  return { directory:data, root, original, control, calls, options, updates, exec, task, bootstrap,
    advance: () => { clock += CHECK_INTERVAL + 1; } };
}

test('same conversation adopts a verified compatible update without installing host plugins', async t => {
  const f = await fixture(t);
  const first = await f.bootstrap.invoke('workflow_status',{},f.exec);
  assert.equal(first.fixture_version,'3.4.0'); assert.match(first.workflow_runtime.guidance,/3.4.0/);
  await f.updates.settled();
  const second = await f.bootstrap.invoke('workflow_status',{},f.exec);
  assert.equal(second.fixture_version,'3.4.1'); assert.match(second.workflow_runtime.guidance,/3.4.1/);
  assert.equal(second.workflow_runtime.task_pinned,false);
  assert.equal((await f.updates.status()).status,'READY');
  assert.equal(f.calls.length,2);
  assert.ok(f.calls.every(c => c.options.redirect==='manual' && !JSON.stringify(c.options.headers).includes('Authorization')));
  assert.equal((await readFile(resolve(f.root,'package.json'),'utf8')),f.original.files['package.json'].toString());
});

test('confirmation pins original code across update and host restart without altering records', async t => {
  const f = await fixture(t); await f.updates.setEnabled(false);
  await f.bootstrap.invoke('workflow_confirm_start',{},f.exec);
  const before = await readFile(resolve(f.task,'confirmation.json'));
  await writeFile(resolve(f.task,'ledger.json'),'historical usage'); await writeFile(resolve(f.task,'interrupted.json'),'{}');
  await writeFile(resolve(f.task,'final-report.json'),'terminal result');
  await f.updates.setEnabled(true); await f.updates.check();
  const restarted = createUpdates(f.options); t.after(()=>restarted.dispose());
  const bootstrap = createBootstrap({ctx:{},root:f.root,directory:f.directory,fields,updates:restarted}); t.after(()=>bootstrap.dispose());
  const result = await bootstrap.invoke('workflow_status',{},f.exec);
  assert.equal(result.fixture_version,'3.4.0'); assert.equal(result.workflow_runtime.task_pinned,true);
  assert.deepEqual(await readFile(resolve(f.task,'confirmation.json')),before);
  for (const [name, expected] of [['ledger.json','historical usage'],['interrupted.json','{}'],['final-report.json','terminal result']])
    assert.equal(await readFile(resolve(f.task,name),'utf8'),expected);
});

test('unknown legacy task cannot silently use installed or newest code', async t => {
  const f = await fixture(t); await mkdir(f.task,{recursive:true});
  await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings:{workflow_sha256:'a'.repeat(64)}}));
  await assert.rejects(f.updates.forTask(f.task),/TASK_RUNTIME_MISSING/); assert.equal(f.calls.length,0);
});

test('ABI2 bootstrap retains an already-confirmed ABI1 cache without migration',async t=>{
  const f=await fixture(t);await f.updates.setEnabled(false);
  const old=release('3.4.0',files=>files,1), hash=old.metadata.manifest_sha256;
  await put(resolve(f.directory,'updates/runtimes',hash),old.files);
  await mkdir(f.task,{recursive:true});const receipt=JSON.stringify({settings:{workflow_sha256:hash}});
  await writeFile(resolve(f.task,'confirmation.json'),receipt);
  const result=await f.bootstrap.invoke('workflow_status',{},f.exec);
  assert.equal(result.workflow_runtime.task_pinned,true);assert.equal(result.workflow_runtime.manifest_sha256,hash);
  assert.equal(await readFile(resolve(f.task,'confirmation.json'),'utf8'),receipt);
});

test('cached code corruption blocks a pinned task and never falls back to newer code', async t => {
  const f = await fixture(t); await f.updates.setEnabled(false);
  const info = await f.bootstrap.invoke('workflow_confirm_start',{},f.exec);
  await writeFile(resolve(info.root,'harness/workflow.js'),'corrupted');
  await assert.rejects(f.bootstrap.invoke('dot_run',{},f.exec),/TASK_RUNTIME_MISSING_OR_DAMAGED/);
});

test('unconfirmed bundled cache is verified again on each invocation',async t=>{
  const f=await fixture(t);await f.updates.setEnabled(false);
  const info=await f.bootstrap.invoke('workflow_status',{},f.exec);
  await writeFile(resolve(info.root,'L1.md'),'corrupted rules');
  await assert.rejects(f.bootstrap.invoke('workflow_status',{},f.exec),/RUNTIME_INTEGRITY/);
});

test('offline checks preserve selected code and redact raw diagnostics', async t => {
  const f = await fixture(t); await f.updates.check(); f.control.offline=true; f.advance();
  const info = await f.updates.check(); assert.equal(info.status,'CHECK_FAILED');
  assert.ok(!JSON.stringify(info).includes('NEVER OUTPUT'));
  assert.equal((await f.updates.forTask(f.task)).version,'3.4.1');
});

for (const [label, target] of [
  ['major',release('4.0.0')], ['ABI',release('3.4.1', x=>x,8)],
  ['tool contract',release('3.4.1',x=>({...x,'harness/workflow.js':x['harness/workflow.js'].replace('workflow_status','new_tool')}))],
  ['import error',release('3.4.1',x=>({...x,'harness/workflow.js':'throw new Error("bad import");'}))],
]) test(`${label} update never becomes selected`,async t=>{
  const f=await fixture(t,target),result=await f.updates.check();
  assert.ok(['HOST_UPDATE_REQUIRED','CHECK_FAILED'].includes(result.status));
  assert.equal((await f.updates.forTask(f.task)).version,'3.4.0');
});

test('a rejected second update preserves last good release',async t=>{
  const f=await fixture(t);await f.updates.check();
  f.control.target=release('3.4.2');f.control.target.metadata.sha256='f'.repeat(64);
  assert.equal((await f.updates.check()).status,'CHECK_FAILED');
  assert.equal((await f.updates.forTask(f.task)).version,'3.4.1');
});

test('checks are coalesced and automatic schedule is persisted and throttled',async t=>{
  const f=await fixture(t);await Promise.all([f.updates.check(),f.updates.check(),f.updates.check()]);
  assert.equal(f.calls.length,2);
  const restarted=createUpdates(f.options);t.after(()=>restarted.dispose());
  await restarted.schedule();assert.equal(f.calls.length,2);
  f.advance();await restarted.schedule();await restarted.settled();assert.equal(f.calls.length,3);
});

test('disabled updater performs no network requests, including explicit check',async t=>{
  const f=await fixture(t);await f.updates.setEnabled(false);await f.updates.schedule();await f.updates.check();
  assert.equal(f.calls.length,0);assert.equal((await f.updates.status()).enabled,false);
  await f.bootstrap.invoke('workflow_updates',{action:'enable'},f.exec);assert.equal((await f.updates.status()).enabled,true);
});

test('update controls accept neither arbitrary sources nor supplied approval',async t=>{
  const f=await fixture(t);assert.deepEqual(Object.keys(updateFields.workflow_updates),['action']);
  for(const args of [{action:'check',url:'https://evil.test'},{action:'install'},{action:'check',approved:true}])
    await assert.rejects(f.bootstrap.invoke('workflow_updates',args,f.exec),/Update action/);
});

test('busy installer cannot change active pointer or erase lock',async t=>{
  const f=await fixture(t);await f.updates.ensureBundled();
  const path=resolve(f.directory,'updates/install.lock');await writeFile(path,'existing installer');
  assert.equal((await f.updates.check()).status,'CHECK_FAILED');
  assert.equal(await readFile(path,'utf8'),'existing installer');assert.equal(f.calls.length,0);
  assert.equal((await f.updates.forTask(f.task)).version,'3.4.0');
});

test('prereleases and version downgrade are never installed',async t=>{
  const f=await fixture(t);f.control.prerelease=true;
  assert.equal((await f.updates.check()).status,'CHECK_FAILED');
  f.control.prerelease=false;f.control.target=release('3.3.9');
  assert.equal((await f.updates.check()).status,'UP_TO_DATE');assert.equal(f.calls.length,2);
});

for(const path of ['../outside','/absolute','C:/escape','a\\escape','CON.txt','a/../outside','a.','a:stream','node_modules/bad','MANIFEST.JSON'])
  test(`archive rejects unsafe/aliased path ${path}`,()=>{
    const value=release('3.4.1',files=>({...files,[path]:'untrusted'}));
    assert.throws(()=>decodeRuntime(value.archive,value.metadata));
  });

test('archive rejects extra files and changed manifest contents',()=>{
  const value=release();value.payload.files.extra=Buffer.from('untracked').toString('base64');
  const archive=gzipSync(JSON.stringify(value.payload));
  assert.throws(()=>decodeRuntime(archive,{...value.metadata,sha256:sha(archive)}),/UPDATE_MANIFEST/);
  assert.throws(()=>decodeRuntime(value.archive,{...value.metadata,manifest_sha256:'0'.repeat(64)}),/UPDATE_DIGEST/);
});

test('redirects reject foreign origins, credentials and non-HTTPS before making requests',async()=>{
  for(const location of ['https://evil.test/file','http://github.com/file','https://user:secret@github.com/file','https://github.com:444/file']){
    const calls=[];await assert.rejects(download('https://github.com/start',100,async(url)=>{
      calls.push(url);return new Response(null,{status:302,headers:{location}});
    }),/UPDATE_ORIGIN/);assert.equal(calls.length,1);
  }
});

test('stream limit also applies without content-length',async()=>{
  await assert.rejects(download('https://github.com/start',5,async()=>new Response('too large')),/UPDATE_SIZE/);
});

test('symlinked cached entry is rejected',async t=>{
  const f=await fixture(t), path=resolve(f.root,'L1.md'),outside=resolve(f.directory,'outside');
  await mkdir(f.directory,{recursive:true});await writeFile(outside,'L1 rules');await rm(path);
  try {await symlink(outside,path);} catch(e) {if(e.code==='EPERM'){t.skip('Windows symlink privilege unavailable');return;}throw e;}
  await assert.rejects(verifyRuntime(f.root),/RUNTIME_INTEGRITY/);
});

test('snapshot loader executes the actual Python status pipe without network or product dispatch',async t=>{
  const directory=await mkdtemp(resolve(tmpdir(),'reqpilot-real-pipe-'));t.after(()=>rm(directory,{recursive:true,force:true}));
  const root=fileURLToPath(new URL('../../',import.meta.url));
  const updates=createUpdates({root,directory,fields:actualFields,fetchImpl:()=>{throw new Error('No network permitted');}});
  await updates.setEnabled(false);
  const bootstrap=createBootstrap({root,directory,fields:actualFields,ctx:{},updates});t.after(()=>bootstrap.dispose());
  const result=await bootstrap.invoke('workflow_status',{}, {agent:{id:'真实管道只读会话'}});
  assert.equal(result.state,'DISCOVERY');assert.equal(result.workflow_runtime.version,'3.11.0');
  assert.equal(result.workflow_runtime.task_pinned,false);assert.equal(result.workflow_runtime.updates.enabled,false);
});

test('compatible update invalidates prepared environment and prevents approval of old summary',async t=>{
  const f=await fixture(t);const actualRoot=fileURLToPath(new URL('../../',import.meta.url));
  const target=release('3.4.1',files=>files); // synthetic release uses the actual engine below
  const content={...target.files};delete content['MANIFEST.json'];
  for(const path of ['harness/workflow.js','harness/setup.js','harness/diagnostics.js','harness/process.js','harness/task-runtime.js','harness/operation-recovery.js','harness/user-stop.js'])
    content[path]=await readFile(resolve(actualRoot,path));
  content['MANIFEST.json']=Buffer.from(JSON.stringify({schema_version:1,workflow_version:'3.4.1',
    files:Object.fromEntries(Object.entries(content).map(([p,data])=>[p,sha(data)]))}));
  const archive=gzipSync(JSON.stringify({format:1,files:Object.fromEntries(Object.entries(content).map(([p,data])=>[p,data.toString('base64')]))}));
  f.control.target={archive,metadata:{...target.metadata,sha256:sha(archive),manifest_sha256:sha(content['MANIFEST.json'])}};
  const updates=createUpdates({...f.options,fields:actualFields});
  await updates.check();assert.equal((await updates.status()).status,'READY');
  const key='fixture-secret',settings={model:'fixture-model',python:'python',codex:'codex',workflow_sha256:f.original.metadata.manifest_sha256};
  await saveConfig(f.directory,{...settings,probe:{status:'PASS',model:settings.model,credential_sha256:keyDigest(key)}});
  await mkdir(f.task,{recursive:true});await writeFile(resolve(f.task,'environment.json'),JSON.stringify({settings,ready:true}));
  await writeFile(resolve(f.task,'prepared.json'),JSON.stringify({settings,inspection:{ready:true}}));
  let questions=0,runs=0;
  const bootstrap=createBootstrap({root:f.root,directory:f.directory,fields:actualFields,updates,
    ctx:{credentials:{resolve:async()=>({value:key})},userQuestions:{ask:async()=>{questions++;}}},
    workflowOptions:{runner:async()=>{runs++;}}});t.after(()=>bootstrap.dispose());
  const result=await bootstrap.invoke('workflow_confirm_start',{},f.exec);
  assert.equal(result.executed_successfully,false);assert.match(result.error,/基础环境未通过或配置已改变/);
  assert.equal(result.workflow_runtime.version,'3.4.1');assert.equal(questions,0);assert.equal(runs,0);
  await assert.rejects(lstat(resolve(f.task,'confirmation.json')),e=>e.code==='ENOENT');
});

test('private repository updates reuse Git login without leaking it to CDN or status',async t=>{
  const f=await fixture(t), token='fixture-github-secret', target=release();let credentials=0;const requests=[];
  const fetchImpl=async(url,options)=>{
    requests.push({url,authorization:options.headers.Authorization});
    if(url.includes('/releases/latest/download/')) return new Response(null,{status:404});
    if(url==='https://api.github.com/repos/Merrychen-CMY/ReqPilot/releases/latest')
      return Response.json({tag_name:'v3.4.1',draft:false,prerelease:false,assets:[{id:123,name:'reqpilot-update.json'},{id:124,name:'reqpilot-runtime.json.gz'}]});
    if(url.includes('/releases/assets/')) return new Response(null,{status:302,headers:{location:'https://release-assets.githubusercontent.com/'+(url.endsWith('123')?'metadata':'runtime')}});
    return url.endsWith('metadata')?Response.json(target.metadata):new Response(target.archive);
  };
  const updates=createUpdates({...f.options,fetchImpl,credentialProvider:async()=>{credentials++;return token;}});t.after(()=>updates.dispose());
  const result=await updates.check();assert.equal(result.status,'READY');assert.equal(credentials,1);
  assert.ok(!JSON.stringify(result).includes(token));
  for(const r of requests) assert.equal(r.authorization,r.url.startsWith('https://api.github.com/')?'Bearer '+token:undefined);
  assert.equal((await updates.forTask(f.task)).version,'3.4.1');
  assert.ok(!(await readFile(resolve(f.directory,'updates/state.json'),'utf8')).includes(token));
});

test('missing Git login reports actionable update error and keeps bundled task runtime',async t=>{
  const f=await fixture(t);f.control.offline=true;
  assert.equal((await f.updates.check()).error,'UPDATE_GITHUB_LOGIN_REQUIRED');
  assert.equal((await f.updates.forTask(f.task)).version,'3.4.0');
});

test('GitHub token is never sent to another repository API or followed API redirect',async()=>{
  let requests=0;
  await assert.rejects(download('https://api.github.com/repos/other/repo/releases/latest',100,async()=>{requests++;},undefined,'secret'),/UPDATE_ORIGIN/);
  assert.equal(requests,0);
  await assert.rejects(download('https://api.github.com/repos/Merrychen-CMY/ReqPilot/releases/latest',100,async()=>{
    requests++;return new Response(null,{status:302,headers:{location:'https://api.github.com/repos/other/repo/releases/latest'}});
  },undefined,'secret'),/UPDATE_ORIGIN/);assert.equal(requests,1);
});

for(const host of ['github.com','other.example'])test(`local Git credential validates host ${host} and prevents interactive prompts`,async()=>{
  const token='fixture-only-token';let input='',options;
  const value=await githubCredential({spawnImpl:(file,args,opts)=>{
    assert.equal(file,'git');assert.deepEqual(args,['-c','credential.interactive=never','credential','fill']);options=opts;
    const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
    child.stdin.on('data',data=>{input+=data.toString();});
    setImmediate(()=>{child.stdout.write(`protocol=https\nhost=${host}\npassword=${token}\n`);child.emit('close',0);});return child;
  }});
  assert.equal(value,host==='github.com'?token:null);assert.match(input,/path=Merrychen-CMY\/ReqPilot.git/);
  assert.equal(options.env.GIT_TERMINAL_PROMPT,'0');assert.equal(options.env.GCM_INTERACTIVE,'Never');
  assert.equal(options.windowsHide,true);assert.equal(options.shell,false);assert.equal(options.env.OPENAI_API_KEY,undefined);
});

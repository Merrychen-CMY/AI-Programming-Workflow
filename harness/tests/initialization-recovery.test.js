import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createBootstrap } from '../bootstrap.js';
import { createUpdates } from '../updates.js';
import { toolFields } from '../workflow.js';
import { pythonCall } from '../process.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const sha = value => createHash('sha256').update(value).digest('hex');
const python = process.env.PYTHON || 'python';

async function fixture(t, answer = 'approve') {
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-init-recovery-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const session = 'init-recovery-fixture', task = resolve(directory, 'tasks', sha(session));
  const files = {'package.json': JSON.stringify({name:'ai-programming-workflow',version:'3.5.0',type:'module',reqpilot:{bootstrap_abi:3}}),
    'harness/workflow.js':`export const toolFields=${JSON.stringify(toolFields)};export function createWorkflow(){return {invoke:async()=>({old:true}),dispose(){}}}`,
    'harness/prompt.md':'old fixture','AGENTS.md':'fixture','L1.md':'fixture','scripts/harness_workflow.py':'# fixture'};
  const manifest=JSON.stringify({schema_version:1,workflow_version:'3.5.0',files:Object.fromEntries(Object.entries(files).map(([p,v])=>[p,sha(v)]))});
  const hash=sha(manifest); files['MANIFEST.json']=manifest;
  for(const [p,v] of Object.entries(files)) {const dest=resolve(directory,'updates/runtimes',hash,p);await mkdir(resolve(dest,'..'),{recursive:true});await writeFile(dest,v);}
  execFileSync(python,['-B','-c',`import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'runtime')); sys.path.insert(0,str(Path.cwd()/'tests'))
from dot_runtime.controller import atomic_json,digest
from dot_runtime.onboarding import fingerprint
from test_delegated_l1 import delegation as make_delegation
task=Path(sys.argv[1]); task.mkdir(parents=True)
project=task.parents[1]/'product'; project.mkdir(); (project/'input.txt').write_text('original')
(task/'raw-input.md').write_text('fixture request'); draft={'fixture':True}; delegation=make_delegation(task/'raw-input.md')
settings={'project':str(project),'control_dir':str(task/'control'),'raw_input':str(task/'raw-input.md'),'delegation':str(task/'delegation.json'),'model':'fixture','codex':'codex','environment_context':'fixture'}
for name,value in [('draft',draft),('delegation',delegation),('bridge',settings)]: atomic_json(task/(name+'.json'),value)
atomic_json(task/'confirmation.json',{'authority':'local_harness_user','session':sys.argv[2],'settings':{'model':'fixture','python':sys.executable,'codex':'codex','workflow_sha256':sys.argv[3]},'bridge_sha256':digest(settings),'draft_sha256':digest(draft),'delegation_sha256':digest(delegation),'baseline':fingerprint(project)})
partial=task/'control/run/initial-project'; partial.mkdir(parents=True); (partial/'part.txt').write_text('partial')
api=task/'control/api'; api.mkdir(); atomic_json(api/'ledger.json',{'started_at':1234,'attempts':[{'status':'COMPLETED','usage':{'input_tokens':100,'output_tokens':23,'total_tokens':123}}]})
atomic_json(task/'interrupted.json',{'name':'dot_initialize','status':'UNKNOWN'})
`,task,session,hash],{cwd:root,windowsHide:true,encoding:'utf8'});
  const updates=createUpdates({root,directory,fields:toolFields,fetchImpl:async()=>{throw Error('No network')},credentialProvider:async()=>null});
  await updates.setEnabled(false); t.after(()=>updates.dispose());
  const controls={answer,calls:[],questions:[]};
  const ctx={credentials:{resolve:async()=>{throw Error('Recovery must not read a key')}},userQuestions:{ask:async({questions})=>{
    controls.questions.push(...questions); const q=questions[0];
    if(controls.answer==='stale')await writeFile(resolve(directory,'product/input.txt'),'changed during confirmation');
    return {answers:controls.answer==='skip'?[]:[{id:q.id,custom:controls.answer==='custom',selected:[controls.answer==='decline'?q.options[1].label:q.options[0].label]}]};
  }}};
  const runner=(exe,script,request,options)=>{controls.calls.push(request.action);assert.equal(options.key,undefined);return pythonCall(exe,script,request,options)};
  const bootstrap=createBootstrap({ctx,root,directory,updates,fields:toolFields,workflowOptions:{runner}});t.after(()=>bootstrap.dispose());
  return {directory,task,hash,bootstrap,updates,controls,exec:{agent:{id:session}}};
}

test('native initialization recovery retains task identity, API usage and history using real Python',async t=>{
  const f=await fixture(t);const original=await readFile(resolve(f.task,'confirmation.json'));const ledger=await readFile(resolve(f.task,'control/api/ledger.json'));
  const preview=await f.bootstrap.invoke('workflow_updates',{action:'initialization_preview'},f.exec);
  assert.equal(preview.api_calls,1);assert.equal(preview.usage.total_tokens,123);assert.equal(f.controls.questions.length,0);
  const result=await f.bootstrap.invoke('workflow_updates',{action:'initialization_recover'},f.exec);
  assert.equal(result.status,'INITIALIZATION_RECOVERED');assert.equal(result.product_execution_started,false);
  assert.deepEqual(await readFile(resolve(f.task,'confirmation.json')),original);assert.deepEqual(await readFile(resolve(f.task,'control/api/ledger.json')),ledger);
  assert.equal((await f.updates.forTask(f.task)).version,'3.7.0');
  assert.deepEqual(f.controls.calls,['initialization_preview','initialization_preview','initialization_commit']);
  assert.match(f.controls.questions[0].question,/不修改产品文件/);
  const status=await f.bootstrap.invoke('workflow_status',{},f.exec);
  assert.equal(status.interrupted,false);assert.equal(status.initialization_recoveries.length,1);
});

for(const answer of ['decline','skip','custom'])test(`native ${answer} never clears the initialization marker`,async t=>{
  const f=await fixture(t,answer);const marker=await readFile(resolve(f.task,'interrupted.json'));
  const result=await f.bootstrap.invoke('workflow_updates',{action:'initialization_recover'},f.exec);
  assert.equal(result.status,'INITIALIZATION_RECOVERY_NOT_APPROVED');assert.deepEqual(await readFile(resolve(f.task,'interrupted.json')),marker);
  assert.deepEqual(f.controls.calls,['initialization_preview']);assert.equal((await f.updates.forTask(f.task)).manifest_sha256,f.hash);
});

test('changed project is explicitly shown and approved, then requires replanning',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.directory,'product/extra.txt'),'new baseline');
  const result=await f.bootstrap.invoke('workflow_updates',{action:'initialization_recover'},f.exec);
  assert.equal(result.record.baseline_changed,true);assert.match(f.controls.questions[0].options[0].label,/新基线/);
  assert.equal(JSON.parse(await readFile(resolve(f.task,'control/planning-status.json'),'utf8')).status,'NEEDS_INFO');
});

test('change during native approval rejects stale recovery without clearing UNKNOWN',async t=>{
  const f=await fixture(t,'stale');await assert.rejects(f.bootstrap.invoke('workflow_updates',{action:'initialization_recover'},f.exec),/changed during native/);
  assert.ok(await readFile(resolve(f.task,'interrupted.json')));
});

test('model cannot inject an approval, baseline or task identity',async t=>{
  const f=await fixture(t);await assert.rejects(f.bootstrap.invoke('workflow_updates',{action:'initialization_recover',approved:true},f.exec),/Update action/);
  assert.equal(f.controls.questions.length,0);assert.equal(f.controls.calls.length,0);
});

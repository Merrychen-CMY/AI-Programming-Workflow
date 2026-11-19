import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createUpdates } from '../updates.js';
import { createBootstrap } from '../bootstrap.js';
import { toolFields } from '../workflow.js';
import { pythonCall } from '../process.js';
import { saveConfig, keyDigest } from '../setup.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const sha = value => createHash('sha256').update(value).digest('hex');
const python = process.env.PYTHON || 'python';

async function fixture(t, answer = 'approve') {
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-task-upgrade-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const session = 'native-upgrade-fixture', task = resolve(directory, 'tasks', sha(session));
  const files = {'package.json': JSON.stringify({ name: 'ai-programming-workflow', version: '3.4.1', type: 'module', reqpilot: { bootstrap_abi: 2 } }),
    'harness/workflow.js': `export const toolFields=${JSON.stringify(toolFields)};export function createWorkflow(){return {invoke:async()=>({old:true}),dispose(){}}}`,
    'harness/prompt.md': 'fixture old rules', 'AGENTS.md': 'fixture rules', 'L1.md': 'fixture L1', 'scripts/harness_workflow.py': '# unused old fixture'};
  const manifest = JSON.stringify({schema_version:1,workflow_version:'3.4.1',files:Object.fromEntries(Object.entries(files).map(([p,v])=>[p,sha(v)]))});
  const sourceHash = sha(manifest); files['MANIFEST.json'] = manifest;
  for(const [p,value] of Object.entries(files)) {
    const dest=resolve(directory,'updates/runtimes',sourceHash,p);await mkdir(resolve(dest,'..'),{recursive:true});await writeFile(dest,value);
  }
  execFileSync(python, ['-c', `import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'runtime'))
from dot_runtime.controller import atomic_json,digest
from dot_runtime.onboarding import fingerprint
task=Path(sys.argv[1]); task.mkdir(parents=True)
project=task.parents[1]/'product'; project.mkdir(); (project/'input.txt').write_text('original')
(task/'raw-input.md').write_text('fixture original request')
sys.path.insert(0, str(Path.cwd()/'tests'))
from test_delegated_l1 import delegation as make_delegation
draft={'fixture':True}; delegation=make_delegation(task/'raw-input.md')
settings={'project':str(project),'control_dir':str(task/'control'),'raw_input':str(task/'raw-input.md'),'delegation':str(task/'delegation.json'),'model':'fixture','codex':'codex','environment_context':'fixture'}
atomic_json(task/'draft.json',draft); atomic_json(task/'delegation.json',delegation); atomic_json(task/'bridge.json',settings)
atomic_json(task/'confirmation.json',{'session':sys.argv[2],'authority':'local_harness_user','settings':{'model':'fixture','python':sys.executable,'codex':'codex','workflow_sha256':sys.argv[3]},'bridge_sha256':digest(settings),'draft_sha256':digest(draft),'delegation_sha256':digest(delegation),'baseline':fingerprint(project)})
`, task, session, sourceHash], {cwd:root,windowsHide:true,encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  const updates=createUpdates({root,directory,fields:toolFields,fetchImpl:async()=>{throw Error('No network in migration tests')},credentialProvider:async()=>null});
  await updates.setEnabled(false);t.after(()=>updates.dispose());
  const control={answer,questions:0,calls:[]};
  const ctx={credentials:{resolve:async()=>({value:'isolated-fixture-key'})},userQuestions:{ask:async({questions})=>{
    control.questions++;
    if(control.answer==='stale') await writeFile(resolve(directory,'product/input.txt'),'changed while reviewing');
    return {answers:control.answer==='skip'?[]:[{id:questions[0].id,custom:control.answer==='custom',selected:[control.answer==='decline'?'暂不升级':'批准本次任务升级']}]};
  }}};
  const runner=(executable,script,request,options)=>{control.calls.push(request.action);assert.equal(options.key,undefined);return pythonCall(executable,script,request,options)};
  const bootstrap=createBootstrap({root,directory,fields:toolFields,updates,ctx,workflowOptions:{runner}});t.after(()=>bootstrap.dispose());
  return {directory,task,session,updates,control,bootstrap,exec:{agent:{id:session}},sourceHash,ctx,runner};
}

test('native upgrade keeps the conversation, receipt and effective version after restart using real Python',async t=>{
  const f=await fixture(t); const receipt=await readFile(resolve(f.task,'confirmation.json'));
  const preview=await f.bootstrap.invoke('workflow_updates',{action:'upgrade_preview'},f.exec);
  assert.equal(preview.source.version,'3.4.1');assert.equal(f.control.questions,0);
  const result=await f.bootstrap.invoke('workflow_updates',{action:'upgrade'},f.exec);
  assert.equal(result.status,'UPGRADED');assert.equal(f.control.questions,1);
  assert.deepEqual(await readFile(resolve(f.task,'confirmation.json')),receipt);
  assert.equal((await f.updates.forTask(f.task)).manifest_sha256,result.record.target.manifest_sha256);
  const restarted=createBootstrap({root,directory:f.directory,fields:toolFields,ctx:f.ctx,updates:f.updates,workflowOptions:{runner:f.runner}});
  t.after(()=>restarted.dispose());
  const status=await restarted.invoke('workflow_status',{},f.exec);
  assert.equal(status.workflow_runtime.version,'3.5.0');assert.equal(status.runtime_upgrades.length,1);
  assert.equal(status.workflow_runtime.task_pinned,true);assert.ok(status.workflow_runtime.guidance);
  assert.equal(status.interrupted,false);assert.deepEqual(f.control.calls,['upgrade_preview','upgrade_preview','upgrade_commit','status']);
  const original=JSON.parse(receipt);
  await saveConfig(f.directory,{...original.settings,probe:{status:'PASS',model:'fixture',credential_sha256:keyDigest('isolated-fixture-key')}});
  // Invalid allocation is a known refusal, but must reach the new controller through the preserved model/executor binding.
  const refusal=await restarted.invoke('l1_adjust_limits',{proposal_json:'{}'},f.exec);
  assert.equal(refusal.accepted,false);assert.equal(refusal.error,undefined);
  assert.equal(f.control.calls.at(-1),'bridge');
});

for(const answer of ['decline','skip','custom']) test(`native ${answer} leaves task version unchanged`,async t=>{
  const f=await fixture(t,answer);const result=await f.bootstrap.invoke('workflow_updates',{action:'upgrade'},f.exec);
  assert.equal(result.status,'UPGRADE_NOT_APPROVED');assert.deepEqual(f.control.calls,['upgrade_preview']);
  assert.equal((await f.updates.forTask(f.task)).manifest_sha256,f.sourceHash);
});

test('task changes during native approval cannot commit stale migration',async t=>{
  const f=await fixture(t,'stale');await assert.rejects(f.bootstrap.invoke('workflow_updates',{action:'upgrade'},f.exec),/changed during native confirmation/);
  assert.equal((await f.updates.forTask(f.task)).manifest_sha256,f.sourceHash);
});

test('model cannot inject approval, target paths or version arguments',async t=>{
  const f=await fixture(t);await assert.rejects(f.bootstrap.invoke('workflow_updates',{action:'upgrade',approved:'true'},f.exec),/Update action/);
  assert.equal(f.control.questions,0);assert.deepEqual(f.control.calls,[]);
});

test('incomplete migration blocks normal engine selection and uses only recorded target for recovery',async t=>{
  const f=await fixture(t);const target=await f.updates.upgradeTarget(f.task);
  await writeFile(resolve(f.task,'runtime-upgrade-pending.json'),JSON.stringify({after_journal:{records:[{target}]}}));
  await assert.rejects(f.updates.forTask(f.task),/TASK_UPGRADE_PENDING/);
  assert.equal((await f.updates.upgradeTarget(f.task,true)).manifest_sha256,target.manifest_sha256);
  await assert.rejects(f.bootstrap.invoke('workflow_updates',{action:'upgrade_recover'},f.exec),/intent is damaged/);
});

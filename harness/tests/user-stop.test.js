import test from 'node:test';
import assert from 'node:assert/strict';
import { confirmStop } from '../user-stop.js';
import { toolFields } from '../workflow.js';
import { execFileSync } from 'node:child_process';
import { readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pythonCall } from '../process.js';

function fixture(choice = 'stop') {
  const calls = [], questions = [];
  const preview = {sha256:'exact-task-hash',project:'isolated fixture',snapshot:{runtime_state:'DECISION_PENDING',
    implementation_attempts:1,revision_count:2,active_run:{role:'implementation'}},host_interrupted:true};
  return {calls,questions,preview,exec:{agent:{id:'native-session'}},
    call:async(action,args)=>{calls.push({action,args});return action==='stop_preview'?preview:{status:args.decision==='stop'?'USER_STOP_RECORDED':'USER_STOP_NOT_REQUESTED'};},
    ctx:{userQuestions:{ask:async({questions:q})=>{questions.push(...q);return {answers:choice==='skip'?[]:[{
      id:q[0].id,custom:choice==='custom',selected:choice==='multi'?q[0].options.map(x=>x.label):[
        q[0].options[choice==='continue'?1:0].label]}]};}}}};
}

test('native stop tool accepts no model approval arguments',()=>assert.deepEqual(toolFields.workflow_confirm_stop,{}));
test('pause occurs before native question and only exact user choice writes receipt',async()=>{
  const f=fixture();const result=await confirmStop(f);
  assert.equal(result.status,'USER_STOP_RECORDED');
  assert.deepEqual(f.calls,[{action:'stop_preview',args:undefined},{action:'stop_confirm',args:{sha256:'exact-task-hash',decision:'stop'}}]);
  assert.match(f.questions[0].question,/当前已暂停新的派工和重试/);
  assert.match(f.questions[0].question,/不清除 active\/UNKNOWN/);
  assert.match(f.questions[0].question,/不会直接判定失败/);
});
for(const choice of ['skip','custom','multi']) test(`${choice} retains pause and never records user stop`,async()=>{
  const f=fixture(choice);const result=await confirmStop(f);
  assert.equal(result.status,'USER_STOP_PENDING');assert.equal(f.calls.length,1);
});
test('explicit continue cancels only the unconfirmed stop pause',async()=>{
  const f=fixture('continue');await confirmStop(f);
  assert.equal(f.calls[1].args.decision,'continue');
});
test('aborted native dialog cannot write authorization',async()=>{
  const f=fixture();f.exec.signal={aborted:true};await confirmStop(f);assert.equal(f.calls.length,1);
});
test('recorded stop is idempotent and cannot reopen the task',async()=>{
  const f=fixture();f.preview.already_confirmed=true;await confirmStop(f);
  assert.equal(f.calls.length,1);assert.equal(f.questions.length,0);
});

test('native choice reaches Python receipt and L1 closure without clearing host interruption',async t=>{
  const root=fileURLToPath(new URL('../../',import.meta.url)),python=process.env.PYTHON||'python';
  const created=JSON.parse(execFileSync(python,['-B','-c',`
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'runtime'));sys.path.insert(0,str(Path.cwd()/'tests'))
from test_user_stop import UserStopTests
f=UserStopTests();f.setUp();f.f.probe.stop();f.f.temp._finalizer.detach()
print(json.dumps({'task':str(f.task),'directory':str(f.f.base),'session':f.session,'assessment':f.assessment()}))
`],{cwd:root,encoding:'utf8',windowsHide:true}));
  t.after(async()=>{
    const path=resolve(created.directory),part=relative(resolve(tmpdir()),path);
    if(!part||part.startsWith('..')||isAbsolute(part))throw Error('Unexpected fixture cleanup target');
    await rm(path,{recursive:true,force:true});
  });
  const call=(action,payload={})=>pythonCall(python,resolve(root,'scripts/harness_workflow.py'),
    {action,task:created.task,session:created.session,...payload});
  const original=await readFile(resolve(created.task,'control/run/state.json'));
  const marker=await readFile(resolve(created.task,'interrupted.json'));
  const ctx={userQuestions:{ask:async({questions:q})=>{
    assert.equal(JSON.parse(await readFile(resolve(created.task,'control/user-stop-pending.json'),'utf8')).sha256.length,64);
    return {answers:[{id:q[0].id,selected:[q[0].options[0].label]}]};
  }}};
  const result=await confirmStop({call,ctx,exec:{agent:{id:created.session}}});
  assert.equal(result.status,'USER_STOP_RECORDED');
  assert.deepEqual(await readFile(resolve(created.task,'control/run/state.json')),original);
  const final=await call('bridge',{name:'l1_stop',arguments:{reason:JSON.stringify(created.assessment)}});
  assert.equal(final.state,'REPORT_PENDING');
  assert.deepEqual(await readFile(resolve(created.task,'interrupted.json')),marker);
  assert.equal(JSON.parse(await readFile(resolve(created.task,'control/run/state.json'),'utf8')).implementation_attempts,1);
});

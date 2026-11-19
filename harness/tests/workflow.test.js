import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createWorkflow, toolFields } from '../workflow.js';
import { loadConfig, saveConfig, keyDigest, probeModel, startSetup } from '../setup.js';
import { pythonCall } from '../process.js';

const root = fileURLToPath(new URL('../../',import.meta.url));
const secret = 'fixture-secret-never-output';
const settings = {model:'fixture-model',python:process.env.PYTHON || 'python',codex:'fixture-codex',
  workflow_sha256:createHash('sha256').update(await readFile(resolve(root,'MANIFEST.json'))).digest('hex')};
const session='fixture-session', sessionHash=createHash('sha256').update(session).digest('hex');
async function fixture(t, answer='approve') {
  const directory=await mkdtemp(resolve(tmpdir(),'l1-plugin-')); t.after(()=>rm(directory,{recursive:true,force:true}));
  await saveConfig(directory,{...settings,probe:{status:'PASS',model:settings.model,credential_sha256:keyDigest(secret)}});
  const calls=[],questions=[];
  const ctx={credentials:{resolve:async()=>({value:secret})},userQuestions:{ask:async value=>{
    questions.push(value); const q=value.questions[0]; return {answers:answer==='skip'?[]:[{id:q.id,selected:[answer==='approve'?q.options[0].label:q.options[1].label]}]};}}};
  const task=resolve(directory,'tasks',sessionHash);
  await mkdir(task,{recursive:true});
  await writeFile(resolve(task,'environment.json'),JSON.stringify({settings,ready:true}));
  const controls={environment:'PASS'};
  const runner=async(exe,script,request,options)=>{
    calls.push({request,options});
    if(request.action==='environment') {
      if(controls.environment==='throw')throw new Error('fixture runner unavailable');
      return{ready:controls.environment==='PASS',checks:[{name:'Python/Git/Codex fixture',required:true,status:controls.environment}],limits:'base tools only'};
    }
    if(request.action==='inspect')return{inspection:{ready:true,baseline:{id:'baseline'},checks:[],limits:'fixture only'},digest:'a'.repeat(64)};
    if(request.action==='confirm')await writeFile(resolve(task,'confirmation.json'),JSON.stringify({settings}));
    if(request.action==='report_preview')return{sha256:'b'.repeat(64),report:{outcome:'FAILED',evidence:['original']}};
    if(request.action==='authorization_preview')return{sha256:'c'.repeat(64),proposal:{changes:{limits:{api_calls:20}},reason:'Remaining diagnosed work'}};
    if(request.action==='bridge' && controls.bridgeError)throw new Error(controls.bridgeError);
    if(request.action==='bridge' && controls.bridgeReply)return controls.bridgeReply;
    return {state:request.action==='confirm'?'START_CONFIRMED':'FIXTURE'};
  };
  const flow=createWorkflow({ctx,root,directory,keyRef:'FIXTURE_KEY',runner}); t.after(()=>flow.dispose());
  const exec={agent:{id:session},signal:new AbortController().signal};
  const draft=JSON.parse(await readFile(resolve(root,'templates/harness-start.example.json'),'utf8'));
  draft.project=resolve(directory,'product'); draft.raw_input='用户原始需求';
  return{directory,task,flow,exec,draft,calls,questions,ctx,controls};
}

test('tool schema offers no human receipt, key or forged L1 session parameter',()=>{
  assert.deepEqual(toolFields.workflow_confirm_start,{});assert.deepEqual(toolFields.workflow_review_report,{});
  for(const fields of Object.values(toolFields))for(const k of Object.keys(fields))assert.ok(!['key','confirmed','receipt','l1_session_id'].includes(k));
});
for(const answer of ['approve','decline'])test(`native authorization ${answer} binds exact request without model approval`,async t=>{
  const f=await fixture(t,answer);
  await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await f.flow.invoke('workflow_review_authorization',{},f.exec);
  assert.match(f.questions[0].questions[0].question,/Remaining diagnosed work/);
  assert.equal(f.calls.at(-1).request.sha256,'c'.repeat(64));
  assert.equal(f.calls.at(-1).request.approved,answer==='approve');
  assert.equal(f.calls.at(-1).options.key,undefined);
  assert.deepEqual(toolFields.workflow_review_authorization,{});
});
test('skipped authorization remains pending and model cannot approve it',async t=>{
  const f=await fixture(t,'skip');
  await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await assert.rejects(f.flow.invoke('workflow_review_authorization',{approved:true},f.exec),/no model arguments/);
  await assert.rejects(f.flow.invoke('workflow_review_authorization',{},f.exec),/继续等待/);
  assert.ok(!f.calls.some(c=>c.request.action==='authorization_decide'));
});
test('UNKNOWN blocks resource adjustments and human amendment without clearing the marker',async t=>{
  const f=await fixture(t); await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await writeFile(resolve(f.task,'interrupted.json'),'{}');
  for(const [name,args] of [['l1_adjust_limits',{proposal_json:'{}'}],['l1_request_authorization',{proposal_json:'{}'}],['workflow_review_authorization',{}]])
    await assert.rejects(f.flow.invoke(name,args,f.exec),/UNKNOWN/);
  assert.equal(f.calls.length,0);
  assert.equal(await readFile(resolve(f.task,'interrupted.json'),'utf8'),'{}');
});
test('completed budget review clears only the current in-flight marker and can resume',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  f.controls.bridgeReply={accepted:false,state:'BUDGET_REVIEW_PENDING',operation_status:'COMPLETED_BUDGET_REVIEW'};
  const result=await f.flow.invoke('dot_run',{},f.exec);
  assert.equal(result.state,'BUDGET_REVIEW_PENDING');
  await assert.rejects(readFile(resolve(f.task,'interrupted.json')),/ENOENT/);
  f.controls.bridgeReply={accepted:true};
  await f.flow.invoke('l1_adjust_limits',{proposal_json:'{}'},f.exec);
  assert.equal(f.calls.at(-1).options.key,undefined);
});
test('no native startup approval means no bridge calls',async t=>{
  const f=await fixture(t);await assert.rejects(f.flow.invoke('dot_run',{},f.exec),/真实用户开工确认/);assert.equal(f.calls.length,0);
});
test('environment check waits for model/key setup',async t=>{
  const f=await fixture(t);await saveConfig(f.directory,{...settings,model:'',probe:null});
  await assert.rejects(f.flow.invoke('workflow_check_environment',{},f.exec),/配置页/);
  assert.equal(f.calls.length,0);
});
test('environment check needs neither project nor raw requirements and forwards no key',async t=>{
  const f=await fixture(t);const result=await f.flow.invoke('workflow_check_environment',{},f.exec);
  assert.equal(result.ready,true);assert.match(result.next,/沟通原始需求/);
  assert.equal(f.calls[0].request.action,'environment');
  assert.ok(!Object.hasOwn(f.calls[0].request,'draft'));assert.equal(f.calls[0].options.key,undefined);
  assert.ok(!JSON.stringify(f.calls).includes(secret));
});
test('cannot prepare requirements without a current environment check',async t=>{
  const f=await fixture(t);await rm(resolve(f.task,'environment.json'));
  await assert.rejects(f.flow.invoke('workflow_inspect',{draft_json:JSON.stringify(f.draft)},f.exec),/先调用 workflow_check_environment/);
  assert.equal(f.calls.length,0);
});
for(const failure of ['FAIL','throw'])test(`${failure} invalidates old environment PASS and blocks discovery`,async t=>{
  const f=await fixture(t);f.controls.environment=failure;
  const result=await f.flow.invoke('workflow_check_environment',{},f.exec);
  assert.equal(result.ready,false);
  assert.equal(JSON.parse(await readFile(resolve(f.task,'environment.json'),'utf8')).ready,false);
  await assert.rejects(f.flow.invoke('workflow_inspect',{draft_json:JSON.stringify(f.draft)},f.exec),/基础环境未通过/);
});
test('environment tool cannot reset a confirmed task',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await assert.rejects(f.flow.invoke('workflow_check_environment',{},f.exec),/任务已确认/);
  assert.equal(f.calls.length,0);
});
for(const answer of ['decline','skip'])test(`${answer} cannot authorize startup`,async t=>{
  const f=await fixture(t,answer);await f.flow.invoke('workflow_inspect',{draft_json:JSON.stringify(f.draft)},f.exec);
  await assert.rejects(f.flow.invoke('workflow_confirm_start',{},f.exec),/未明确确认/);
  assert.ok(!f.calls.some(c=>c.request.action==='confirm'));
});
test('real UI approval binds prepared summary without exposing credential',async t=>{
  const f=await fixture(t);await f.flow.invoke('workflow_inspect',{draft_json:JSON.stringify(f.draft)},f.exec);
  const result=await f.flow.invoke('workflow_confirm_start',{},f.exec);assert.equal(result.state,'START_CONFIRMED');
  assert.match(f.questions[0].questions[0].question,/用户原始需求/);assert.ok(!JSON.stringify(f.questions).includes(secret));
  assert.ok(!JSON.stringify(f.calls).includes(secret));
});
test('configuration change invalidates prepared summary',async t=>{
  const f=await fixture(t);await f.flow.invoke('workflow_inspect',{draft_json:JSON.stringify(f.draft)},f.exec);
  const config=await loadConfig(f.directory);config.codex='different';await saveConfig(f.directory,config);
  await assert.rejects(f.flow.invoke('workflow_confirm_start',{},f.exec),/配置已改变/);assert.equal(f.questions.length,0);
});
test('model-supplied confirmation is rejected before asking user',async t=>{
  const f=await fixture(t);await assert.rejects(f.flow.invoke('workflow_confirm_start',{confirmed:true},f.exec),/no model arguments/);
});
test('restart preserves UNKNOWN and blocks repeated worker dispatch',async t=>{
  const f=await fixture(t);await mkdir(f.task,{recursive:true});await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await writeFile(resolve(f.task,'interrupted.json'),'{}');await assert.rejects(f.flow.invoke('dot_run',{},f.exec),/UNKNOWN/);
  await f.flow.invoke('workflow_status',{},f.exec);assert.equal(f.calls.at(-1).request.action,'status');
});
test('final report UI preserves FAILED evidence and waits for human',async t=>{
  const f=await fixture(t,'decline');await mkdir(f.task,{recursive:true});await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await assert.rejects(f.flow.invoke('workflow_review_report',{},f.exec),/未明确确认/);
  assert.match(f.questions[0].questions[0].question,/FAILED/);assert.match(f.questions[0].questions[0].question,/original/);
  assert.ok(!f.calls.some(c=>c.request.action==='report_confirm'));
});
test('completed stop has no spurious UNKNOWN, prior interrupted stop keeps UNKNOWN',async t=>{
  const f=await fixture(t);await mkdir(f.task,{recursive:true});await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await f.flow.invoke('l1_stop',{reason:'No feasible solution'},f.exec);
  await assert.rejects(readFile(resolve(f.task,'interrupted.json')),/ENOENT/);
  await writeFile(resolve(f.task,'interrupted.json'),'{}');
  await f.flow.invoke('l1_stop',{reason:'Cannot reconcile old process'},f.exec);
  assert.equal(await readFile(resolve(f.task,'interrupted.json'),'utf8'),'{}');
});

test('completed NEEDS_INFO clears in-flight marker and allows sourced facts without new user approval',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  f.controls.bridgeReply={state:'NEEDS_INFO',accepted:false,planning:{gaps:[{name:'compiler',stage:'planning'}]}};
  const result=await f.flow.invoke('dot_plan',{requirements:'requirements'},f.exec);
  assert.equal(result.state,'NEEDS_INFO');
  await assert.rejects(readFile(resolve(f.task,'interrupted.json')),/ENOENT/);
  f.controls.bridgeReply={state:'NEEDS_INFO',accepted:true};
  await f.flow.invoke('l1_add_facts',{facts_json:'{"reason":"supplement","facts":[]}'},f.exec);
  assert.equal(f.calls.at(-1).options.key,undefined);
  assert.equal(f.calls.at(-1).request.name,'l1_add_facts');
  assert.equal(f.questions.length,0);
});

test('unknown transport failure still blocks facts and repeated planning',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  f.controls.bridgeError='UNKNOWN: lost result';
  await assert.rejects(f.flow.invoke('dot_plan',{requirements:'requirements'},f.exec),/UNKNOWN/);
  assert.ok(await readFile(resolve(f.task,'interrupted.json')));
  const count=f.calls.length;
  await assert.rejects(f.flow.invoke('l1_add_facts',{facts_json:'{}'},f.exec),/UNKNOWN/);
  await assert.rejects(f.flow.invoke('dot_plan',{requirements:'new requirements'},f.exec),/UNKNOWN/);
  assert.equal(f.calls.length,count);
});
test('known plan rejection clears only this call marker and permits correction',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  f.controls.bridgeReply={state:'PLAN_REJECTED',accepted:false,operation_status:'COMPLETED_REJECTED'};
  for(const [name,args] of [['dot_plan',{requirements:'all phases'}],['l1_revise',{requirements:'all phases',plan_json:'{}'}]]) {
    const result=await f.flow.invoke(name,args,f.exec);
    assert.equal(result.operation_status,'COMPLETED_REJECTED');
    await assert.rejects(readFile(resolve(f.task,'interrupted.json')),/ENOENT/);
  }
  f.controls.bridgeReply={plan:{fixture:true}};
  await f.flow.invoke('dot_plan',{requirements:'all phases retained; correct allocation'},f.exec);
  assert.equal(f.calls.length,3);assert.equal(f.questions.length,0);
});
test('known rejection cannot clear a marker left by an earlier unknown operation',async t=>{
  const f=await fixture(t);await writeFile(resolve(f.task,'confirmation.json'),JSON.stringify({settings}));
  await writeFile(resolve(f.task,'interrupted.json'),'prior unknown');
  f.controls.bridgeReply={accepted:false,operation_status:'COMPLETED_REJECTED'};
  await assert.rejects(f.flow.invoke('dot_plan',{requirements:'x'},f.exec),/UNKNOWN/);
  assert.equal(await readFile(resolve(f.task,'interrupted.json'),'utf8'),'prior unknown');
  assert.equal(f.calls.length,0);
});
test('credential provider errors do not expose provider details',async t=>{
  const f=await fixture(t);f.ctx.credentials.resolve=async()=>{throw new Error(secret);};
  await assert.rejects(f.flow.invoke('workflow_inspect',{draft_json:JSON.stringify(f.draft)},f.exec),error=>!error.message.includes(secret));
});
test('reference cannot read outside plugin directory',async t=>{
  const f=await fixture(t);await assert.rejects(f.flow.invoke('workflow_reference',{path:resolve(f.directory,'settings.json'),offset:'0'},f.exec),/escapes/);
});
test('API probe validates two actual response shapes; refuses incomplete result',async()=>{
  const requests=[];
  const fetchImpl=async(url,options)=>{requests.push({url,options});return{ok:true,text:async()=>JSON.stringify({status:'completed',output:requests.length===1?
    [{type:'function_call',name:'workflow_probe',arguments:'{"ok":true}'}]:[{content:[{type:'output_text',text:'{"ok":true}'}]}]})};};
  const result=await probeModel(secret,'fixture-model',fetchImpl);assert.equal(result.api_calls,2);
  assert.ok(requests.every(r=>r.options.redirect==='error' && JSON.parse(r.options.body).store===false));
  assert.ok(!JSON.stringify(result).includes(secret));
  await assert.rejects(probeModel(secret,'fixture-model',async()=>({ok:true,text:async()=>'{"status":"incomplete"}'})),error=>error.code==='API_INCOMPLETE');
});
test('local setup rejects missing token/origin; never echoes saved key',async t=>{
  const directory=await mkdtemp(resolve(tmpdir(),'l1-setup-'));t.after(()=>rm(directory,{recursive:true,force:true}));
  let value;const credentials={resolve:async()=>value?{value}:undefined,set:async(ref,key)=>{value=key;}};
  const page=await startSetup({directory,credentials,keyRef:'fixture',html:'<p>fixture</p>'});t.after(()=>page.close());
  const u=new URL(page.url),origin=u.origin,token=u.hash.slice(1);
  const post=(path,body,headers={})=>fetch(origin+path,{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});
  assert.equal((await post('/status',{})).status,403);
  const { workflow_sha256, ...editable } = settings;
  assert.equal((await post('/save',{...editable,key:secret},{Origin:'https://evil.test','X-Workflow-Token':token})).status,403);
  const headers={Origin:origin,'X-Workflow-Token':token};assert.equal((await post('/save',{...editable,key:secret},headers)).status,200);
  const result=await(await post('/status',{},headers)).text();assert.ok(!result.includes(secret));assert.ok(! (await readFile(resolve(directory,'settings.json'),'utf8')).includes(secret));
});
test('real Python pipe handles Chinese JSON, no model key in read-only process',async t=>{
  const directory=await mkdtemp(resolve(tmpdir(),'l1-pipe-'));t.after(()=>rm(directory,{recursive:true,force:true}));
  const result=await pythonCall(settings.python,resolve(root,'scripts/harness_workflow.py'),{action:'status',task:directory,session:'中文会话'});
  assert.equal(result.state,'DISCOVERY');
});

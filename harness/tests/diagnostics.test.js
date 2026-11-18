import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { probeModel, saveConfig, loadConfig, startSetup } from '../setup.js';
import { SetupError, setupDiagnostic } from '../diagnostics.js';

const secret = 'synthetic-secret-do-not-echo';
const config = {model:'fixture-model',python:'python',codex:'codex',probe:{status:'PASS'}};
const functionResult = {status:'completed',output:[{type:'function_call',name:'workflow_probe',arguments:'{"ok":true}'}]};
const response = (body, status=200) => new Response(typeof body === 'string' ? body : JSON.stringify(body),{status});

for (const [status,code,expected] of [
  [401,'invalid_api_key','API_AUTH'],[403,'permission_denied','API_ACCESS'],
  [403,'unsupported_country_region_territory','API_REGION'],[404,'model_not_found','API_MODEL'],
  [429,'insufficient_quota','API_QUOTA'],[429,'credit_balance_exhausted','API_QUOTA'],
  [429,'project_spend_limit_exceeded','API_QUOTA'],[429,'rate_limit_exceeded','API_RATE'],
  [429,'slow_down','API_RATE'],[429,'unknown','API_LIMIT'],[400,'invalid_parameter','API_REQUEST'],
  [503,'server_error','API_SERVER'],
]) test(`HTTP ${status} ${code} is classified without exposing the response`,async()=>{
  let count=0;
  await assert.rejects(probeModel(secret,'fixture-model',async()=>{
    count++;return response({error:{code,message:secret,request_id:secret}},status);
  }),error=>{
    const diagnostic=setupDiagnostic(error);
    assert.equal(diagnostic.code,expected);assert.equal(diagnostic.http_status,status);
    assert.equal(diagnostic.stage,'function_call');assert.ok(!JSON.stringify(diagnostic).includes(secret));return true;
  });
  assert.equal(count,1);
});

test('non-JSON access-denied page retains HTTP status and hides its body',async()=>{
  await assert.rejects(probeModel(secret,'fixture-model',async()=>response(`<html>${secret}</html>`,403)),error=>{
    assert.equal(error.code,'API_ACCESS');assert.ok(!JSON.stringify(setupDiagnostic(error)).includes(secret));return true;
  });
});
for (const [name,code,expected] of [
  ['TimeoutError',undefined,'NETWORK_TIMEOUT'],['TypeError','ENOTFOUND','NETWORK_DNS'],
  ['TypeError','CERT_HAS_EXPIRED','NETWORK_TLS'],['TypeError','ECONNRESET','NETWORK_CONNECT'],
]) test(`${expected} hides raw network exceptions`,async()=>{
  await assert.rejects(probeModel(secret,'fixture-model',async()=>{throw Object.assign(new Error(secret),{name,cause:{code,message:secret}});}),error=>{
    assert.equal(error.code,expected);assert.ok(!JSON.stringify(setupDiagnostic(error)).includes(secret));return true;
  });
});

test('second probe output limit identifies its phase, retains budget and does not retry',async()=>{
  let count=0;
  await assert.rejects(probeModel(secret,'fixture-model',async(url,options)=>{
    const request=JSON.parse(options.body);assert.equal(request.model,'fixture-model');
    assert.equal(request.max_output_tokens,2048);assert.equal(request.store,false);
    return response(++count===1 ? functionResult : {status:'incomplete',incomplete_details:{reason:'max_output_tokens'},output:[{text:secret}]});
  }),error=>error.code==='API_OUTPUT_LIMIT' && error.stage==='structured_output');
  assert.equal(count,2);
});
for (const [body,expected] of [
  ['invalid '+secret,'API_RESPONSE'],[{status:'completed',output:null},'API_RESPONSE'],
  [{status:'completed',output:[{content:[{type:'refusal',refusal:secret}]}]},'API_REFUSAL'],
  [{status:'completed',output:[{type:'function_call',name:'workflow_probe',arguments:secret}]},'API_TOOL_FORMAT'],
  [{status:'incomplete',incomplete_details:{reason:secret}},'API_INCOMPLETE'],
]) test(`${expected} is safe for malformed or refused model output`,async()=>{
  await assert.rejects(probeModel(secret,'fixture-model',async()=>response(body)),error=>{
    assert.equal(error.code,expected);assert.ok(!JSON.stringify(setupDiagnostic(error)).includes(secret));return true;
  });
});
test('oversized streamed response stops at the response bound',async()=>{
  await assert.rejects(probeModel(secret,'fixture-model',async()=>response('x'.repeat(500001))),error=>error.code==='API_RESPONSE_SIZE');
});
test('untrusted exception fields and mutated message cannot enter public diagnostics',()=>{
  assert.ok(!JSON.stringify(setupDiagnostic({code:secret,stage:secret,message:secret})).includes(secret));
  const error=new SetupError('API_AUTH','function_call',401);error.message=secret;
  assert.ok(!JSON.stringify(setupDiagnostic(error)).includes(secret));
});

async function setupFixture(t, overrides={}) {
  const directory=await mkdtemp(resolve(tmpdir(),'l1-diagnostic-'));t.after(()=>rm(directory,{recursive:true,force:true}));
  await saveConfig(directory,config);
  const credentials={resolve:async()=>({value:secret}),set:async()=>{},...overrides.credentials};
  const page=await startSetup({directory,credentials,keyRef:'fixture',html:'fixture',...overrides,credentials});t.after(()=>page.close());
  const url=new URL(page.url);
  const post=async(path,body={})=>{
    const result=await fetch(url.origin+path,{method:'POST',headers:{Origin:url.origin,'X-Workflow-Token':url.hash.slice(1),'Content-Type':'application/json'},body:JSON.stringify(body)});
    return{status:result.status,value:await result.json()};
  };
  return{directory,post};
}
test('local HTTP probe exposes safe diagnosis and invalidates an old PASS',async t=>{
  const f=await setupFixture(t,{probe:async()=>{throw new SetupError('API_QUOTA','structured_output',429);}});
  const result=await f.post('/probe');assert.equal(result.status,400);
  assert.equal(result.value.code,'API_QUOTA');assert.equal(result.value.stage,'structured_output');
  assert.equal(result.value.http_status,429);assert.ok(!JSON.stringify(result).includes(secret));
  assert.equal((await loadConfig(f.directory)).probe,null);
});
for (const [method,operation,expected] of [
  ['resolve','/status','CREDENTIAL_READ'],['set','/save','CREDENTIAL_WRITE'],
]) test(`${expected} local HTTP error does not expose credential provider text`,async t=>{
  const f=await setupFixture(t,{credentials:{[method]:async()=>{throw new Error(secret);}}});
  const result=await f.post(operation,{...config,key:secret,probe:undefined});
  assert.equal(result.value.code,expected);assert.ok(!JSON.stringify(result).includes(secret));
});
test('local HTTP setup distinguishes malformed fields from unavailable credentials',async t=>{
  const f=await setupFixture(t,{credentials:{resolve:async()=>undefined}});
  assert.equal((await f.post('/save',{model:'',python:'python',codex:'codex'})).value.code,'CONFIG_INVALID');
  assert.equal((await f.post('/probe')).value.code,'SETUP_REQUIRED');
  assert.ok(!(await readFile(resolve(f.directory,'settings.json'),'utf8')).includes(secret));
});

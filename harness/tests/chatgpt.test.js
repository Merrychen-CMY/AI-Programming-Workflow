import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { generateKeyPair, exportJWK, SignJWT } from '../vendor/jose/dist/webapi/index.js';
import { createChatGPTAuth } from '../chatgpt-auth.js';
import { setupDiagnostic } from '../diagnostics.js';
import { chatgptPayload, chatgptRequest, chatgptRelay, streamResponse } from '../chatgpt-response.js';
import { probeModel, saveConfig, startSetup } from '../setup.js';
import { createWorkflow } from '../workflow.js';
import { pythonCall } from '../process.js';

const issuer = 'https://auth.openai.com', client = 'oaiapp_fixture', secret = 'fixture-oauth-access-secret';
const pair = await generateKeyPair('RS256');
const jwk = { ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256', use: 'sig' };
const root = fileURLToPath(new URL('../../', import.meta.url));
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
async function fixture(t) {
  const directory = await mkdtemp(resolve(tmpdir(), 'reqpilot-siwc-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const vault = new Map(), requests = [], control = { nonce: '', refreshes: 0, scope: 'openid resource.invoke chatgpt.tokens.use.direct' };
  const credentials = { resolve: async key => vault.has(key) ? { value: vault.get(key) } : undefined,
    set: async (key, value) => vault.set(key, value) };
  const fetchImpl = async (url, options = {}) => {
    requests.push({ url, options });
    if (control.reply && url.endsWith(control.reply.path)) return control.reply.response();
    if (url.endsWith('openid-configuration')) return json({ issuer, jwks_uri: `${issuer}/jwks`, revocation_endpoint: `${issuer}/revoke` });
    if (url.endsWith('/jwks')) return json({ keys: [jwk] });
    if (url.endsWith('/revoke')) return new Response(null, { status: 200 });
    if (url.endsWith('/models')) return json({ models: [{ slug: 'fixture-model', display_name: 'Fixture', visibility: 'list' },
      { slug: 'hidden', visibility: 'hidden' }] });
    assert.equal(url, `${issuer}/api/accounts/oauth/token`);
    assert.equal(options.redirect, 'error');
    const fields = options.body;
    assert.equal(fields.get('client_id'), client);
    assert.equal(fields.get('resource'), 'https://api.openai.com/v1');
    if (fields.get('grant_type') === 'refresh_token') {
      control.refreshes++;
      return json({ access_token: secret + '-renewed', refresh_token: 'rotated-refresh', token_type: 'Bearer', expires_in: 3600 });
    }
    const token = await new SignJWT({ nonce: control.nonce, email: 'fixture@example.invalid' })
      .setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).setIssuer(control.issuer || issuer)
      .setAudience(control.audience || client).setSubject(control.subject || 'subject').setIssuedAt().setExpirationTime(control.expiry || '5m').sign(pair.privateKey);
    return json({ access_token: secret, refresh_token: 'fixture-refresh', id_token: control.badSignature ? token.slice(0, -4) + 'AAAA' : token,
      token_type: 'Bearer', expires_in: 3600, scope: control.scope });
  };
  const auth = createChatGPTAuth({ directory, credentials, keyRef: 'FIXTURE', fetchImpl });
  async function login(selected) {
    const url = new URL(await auth.begin('http://127.0.0.1:12345/auth/callback', selected));
    control.nonce = url.searchParams.get('nonce');
    const callback = new URL(`http://127.0.0.1:12345/auth/callback?code=fixture&state=${url.searchParams.get('state')}&client_id=${client}`);
    return { url, callback };
  }
  return { directory, auth, credentials, vault, requests, control, login, fetchImpl };
}

test('OAuth validates a real signed identity, reuses host/client and never exposes tokens in status', async t => {
  const f = await fixture(t), first = await f.login();
  assert.equal(first.url.searchParams.get('client_id'), 'dynamic_agent_client');
  assert.equal(first.url.searchParams.get('code_challenge_method'), 'S256');
  await f.auth.callback(first.callback);
  const status = await f.auth.status(); assert.equal(status.active, client);
  assert.ok(!JSON.stringify(status).includes(secret));
  assert.equal((await f.auth.models(client)).length, 1);
  const second = await f.login(client);
  assert.equal(second.url.searchParams.get('client_id'), client);
  assert.equal(first.url.searchParams.get('ext_agent_host_id'), second.url.searchParams.get('ext_agent_host_id'));
  assert.equal(second.url.searchParams.has('agent_name_hint'), false);
  assert.notEqual(first.url.searchParams.get('state'), second.url.searchParams.get('state'));
  assert.notEqual(first.url.searchParams.get('code_challenge'), second.url.searchParams.get('code_challenge'));
  await f.auth.callback(second.callback);
  await assert.rejects(f.auth.callback(second.callback), { code: 'CHATGPT_STATE' });
});

for (const failure of ['state', 'issuer', 'audience', 'badSignature', 'nonce', 'consent', 'expiry'])
  test(`OAuth rejects ${failure} without activating credentials`, async t => {
    const f = await fixture(t), flow = await f.login();
    if (failure === 'state') flow.callback.searchParams.set('state', 'wrong');
    else if (failure === 'nonce') f.control.nonce = 'wrong';
    else if (failure === 'consent') f.control.scope = 'openid';
    else if (failure === 'expiry') f.control.expiry = '10s ago';
    else f.control[failure] = failure === 'badSignature' ? true : 'wrong';
    await assert.rejects(f.auth.callback(flow.callback));
    assert.equal((await f.auth.status()).active, null);
    assert.ok(![...f.vault.values()].join().includes(secret));
    if (failure === 'state') assert.equal(f.requests.length, 0);
  });

test('returning registration cannot silently replace the selected account identity', async t => {
  const f = await fixture(t), first = await f.login(); await f.auth.callback(first.callback);
  const before = f.vault.get('FIXTURE_CHATGPT'), next = await f.login(client); f.control.subject = 'different-account';
  await assert.rejects(f.auth.callback(next.callback), { code: 'CHATGPT_IDENTITY' });
  assert.equal(f.vault.get('FIXTURE_CHATGPT'), before);
});

test('refresh rotates atomically and preserves probe identity; logout clears tokens but retains registration', async t => {
  const f = await fixture(t), flow = await f.login(); await f.auth.callback(flow.callback);
  const identity = await f.auth.identity(client);
  const state = JSON.parse(f.vault.get('FIXTURE_CHATGPT')); state.profiles[0].expires_at = 0;
  f.vault.set('FIXTURE_CHATGPT', JSON.stringify(state));
  const results = await Promise.allSettled([f.auth.access(client), f.auth.access(client)]);
  assert.equal(f.control.refreshes, 1);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  assert.equal((await f.auth.access(client)).identity, identity);
  assert.equal(JSON.parse(f.vault.get('FIXTURE_CHATGPT')).profiles[0].refresh_token, 'rotated-refresh');
  assert.equal((await f.auth.logout(client)).revoked, true);
  assert.ok(!f.vault.get('FIXTURE_CHATGPT').includes('refresh_token'));
  await assert.rejects(f.auth.access(client), { code: 'CHATGPT_LOGIN_REQUIRED' });
  assert.equal(new URL(await f.auth.begin('http://127.0.0.1:12345/auth/callback', client)).searchParams.get('client_id'), client);
});

const completed = { id: 'resp_fixture', status: 'completed', output: [{ type: 'message', content: [{ type: 'output_text', text: '中文' }] }],
  usage: { input_tokens: 10, output_tokens: 3, total_tokens: 13 } };
function sse(value = completed, type = 'response.completed') {
  const bytes = new TextEncoder().encode(`: heartbeat\r\n\r\ndata: ${JSON.stringify({ type, response: value })}\r\n\r\n`);
  return new Response(new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3)); controller.close(); } }));
}
test('SIWC adapts tools and parses split UTF-8 SSE only through a terminal event', async () => {
  const payload = { model: 'fixture-model', max_output_tokens: 200, input: 'hello', tools: [{ type: 'function', name: 'read' }] };
  const wire = chatgptPayload(payload);
  assert.equal(wire.max_output_tokens, undefined); assert.equal(wire.stream, true); assert.equal(wire.store, false);
  assert.equal(wire.tools[0].type, 'namespace'); assert.equal(payload.max_output_tokens, 200);
  const response = await chatgptRequest(secret, payload, 10, { fetchImpl: async (url, opts) => {
    assert.equal(url, 'https://api.openai.com/v1/responses'); assert.equal(opts.redirect, 'error');
    assert.deepEqual(JSON.parse(opts.body), wire); return sse();
  } });
  assert.deepEqual(response, completed);
  await assert.rejects(streamResponse(new Response('data: {"type":"response.created"}\n\n')), { code: 'API_INCOMPLETE' });
  await assert.rejects(streamResponse(new Response('data: broken\n\n')), { code: 'API_RESPONSE' });
});

test('quota and interrupted streams never retry or fall back to the API key', async () => {
  for (const status of [401, 429, 500]) {
    let calls = 0;
    const relay = chatgptRelay({ access: async () => ({ token: secret }) }, client,
      async () => { calls++; return new Response('private upstream body', { status }); });
    const result = await relay({ arguments: { payload: { input: [] } }, timeout_seconds: 5 });
    assert.equal(calls, 1); assert.equal(result.status, 'FAIL');
    assert.equal(result.diagnostic.outcome, status === 500 ? 'UNKNOWN' : 'REJECTED');
    assert.ok(!JSON.stringify(result).includes('private'));
    if (status === 429) assert.equal(result.diagnostic.code, 'CHATGPT_LIMIT');
  }
  let sends = 0;
  const missing = chatgptRelay({ access: async () => { throw new Error('secret'); } }, client, async () => { sends++; });
  assert.equal((await missing({})).diagnostic.outcome, 'NOT_SENT'); assert.equal(sends, 0);
});

test('membership probe exercises namespaced function call and structured output via stream', async () => {
  const payloads = [];
  const result = await probeModel(secret, 'fixture-model', async (_, opts) => {
    const payload = JSON.parse(opts.body); payloads.push(payload);
    return sse({ ...completed, output: payload.tools ? [{ type: 'function_call', namespace: 'reqpilot', name: 'workflow_probe', arguments: '{"ok":true}' }]
      : [{ type: 'message', content: [{ type: 'output_text', text: '{"ok":true}' }] }] });
  }, { chatgpt: true });
  assert.equal(result.api_calls, 2); assert.ok(payloads.every(p => p.stream && !('max_output_tokens' in p)));
});

test('real Python-to-host membership relay preserves terminal usage and forwards no credentials', async t => {
  const f = await fixture(t), script = resolve(f.directory, 'pipe.py');
  await writeFile(script, `import sys,json,os\nsys.path.insert(0,${JSON.stringify(resolve(root, 'runtime'))})\nfrom dot_runtime.gpt_dot import transport\njson.loads(sys.stdin.readline())\nassert 'OPENAI_API_KEY' not in os.environ\nassert 'AI_WORKFLOW_OPENAI_API_KEY_CHATGPT' not in os.environ\nresult=transport({'model':'fixture-model','input':[]},5)\nprint(json.dumps({'ok':True,'result':result}))\n`);
  const result = await pythonCall(process.env.PYTHON || 'python', script, {}, { authMode: 'chatgpt', key: 'unused-paid-key',
    hostRelay: chatgptRelay({ access: async () => ({ token: secret }) }, client, async () => sse()) });
  assert.deepEqual(result, completed);
});

test('workflow pins membership identity and supplies only privileged transport to DOT', async t => {
  const f = await fixture(t), flow = await f.login(); await f.auth.callback(flow.callback);
  const identity = await f.auth.identity(client), manifest = createHash('sha256').update(await readFile(resolve(root, 'MANIFEST.json'))).digest('hex');
  const config = { model: 'fixture-model', python: 'python', codex: 'codex', auth_mode: 'chatgpt', chatgpt_profile: client,
    probe: { status: 'PASS', model: 'fixture-model', auth_mode: 'chatgpt', credential_sha256: identity } };
  await saveConfig(f.directory, config);
  const session = 'membership-session', task = resolve(f.directory, 'tasks', createHash('sha256').update(session).digest('hex'));
  const { mkdir } = await import('node:fs/promises'); await mkdir(task, { recursive: true });
  const settings = { model: config.model, python: config.python, codex: config.codex, workflow_sha256: manifest,
    auth_mode: 'chatgpt', chatgpt_profile: client, chatgpt_identity: identity };
  await writeFile(resolve(task, 'confirmation.json'), JSON.stringify({ settings }));
  const calls = [];
  const workflow = createWorkflow({ directory: f.directory, root, keyRef: 'FIXTURE', ctx: { credentials: f.credentials }, chatgptAuth: f.auth,
    runner: async (...args) => { calls.push(args); return { state: 'FIXTURE' }; } }); t.after(() => workflow.dispose());
  await workflow.invoke('dot_plan', { requirements: 'fixture' }, { agent: { id: session } });
  assert.equal(calls[0][3].key, undefined); assert.equal(calls[0][3].authMode, 'chatgpt');
  assert.equal(typeof calls[0][3].hostRelay, 'function');
  assert.ok(!JSON.stringify(calls).includes(secret));
  await saveConfig(f.directory, { ...config, model: 'changed-model', probe: { ...config.probe, model: 'changed-model' } });
  await assert.rejects(workflow.invoke('dot_plan', { requirements: 'fixture' }, { agent: { id: session } }), /已固定/);
  assert.equal(calls.length, 1);
});

test('setup returns authorization URL only to the local UI and saves identity-bound probe without token hash', async t => {
  const f = await fixture(t);
  const page = await startSetup({ directory: f.directory, credentials: f.credentials, keyRef: 'FIXTURE', html: '<p>setup</p>', auth: f.auth,
    probe: async (token, model, _, options) => { assert.equal(token, secret); assert.equal(options.chatgpt, true);
      return { status: 'PASS', model, api_calls: 2, credential_sha256: 'transient-token-hash' }; } });
  t.after(() => page.close());
  const u = new URL(page.url);
  const post = async (path, body) => {
    const response = await fetch(u.origin + path, { method: 'POST', headers: { Origin: u.origin, 'X-Workflow-Token': u.hash.slice(1), 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    assert.equal(response.status, 200); return response.json();
  };
  const login = new URL((await post('/chatgpt/login', {})).url); f.control.nonce = login.searchParams.get('nonce');
  const callback = new URL(login.searchParams.get('redirect_uri')); callback.search = new URLSearchParams({ state: login.searchParams.get('state'), code: 'fixture', client_id: client });
  assert.equal((await fetch(callback)).status, 200);
  await post('/save', { auth_mode: 'chatgpt', chatgpt_profile: client, model: 'fixture-model', python: 'python', codex: 'codex' });
  await post('/probe', {});
  const saved = JSON.parse(await readFile(resolve(f.directory, 'settings.json'), 'utf8'));
  assert.equal(saved.probe.credential_sha256, await f.auth.identity(client));
  assert.ok(!JSON.stringify(await post('/status', {})).includes(secret));
  assert.ok(!JSON.stringify(saved).includes(secret));
});

test('unsaved membership selection cannot accidentally run a paid API probe', async t => {
  const f = await fixture(t); let calls = 0;
  await saveConfig(f.directory, { model: 'fixture-model', python: 'python', codex: 'codex', auth_mode: 'api_key' });
  const page = await startSetup({ directory: f.directory, credentials: f.credentials, keyRef: 'FIXTURE', html: '', auth: f.auth,
    probe: async () => { calls++; } }); t.after(() => page.close());
  const u = new URL(page.url);
  const response = await fetch(u.origin + '/probe', { method: 'POST', headers: { Origin: u.origin,
    'X-Workflow-Token': u.hash.slice(1), 'Content-Type': 'application/json' },
    body: JSON.stringify({ auth_mode: 'chatgpt', model: 'fixture-model', chatgpt_profile: client }) });
  assert.equal(response.status, 400); assert.equal((await response.json()).code, 'CONFIG_UNSAVED'); assert.equal(calls, 0);
});

for (const [status, body, expected] of [
  [403, '<html>blocked fixture-oauth-access-secret</html>', 'CHATGPT_ACCESS_HTML'],
  [403, {error: {code:'unsupported_country_region_territory'}}, 'CHATGPT_REGION'],
  [403, {error: {code:'subscription_sharing_user_not_eligible'}}, 'CHATGPT_INELIGIBLE'],
  [403, {error: {code:'chatpass_v2_scope_not_authorized'}}, 'CHATGPT_PERMISSION'],
  [403, {detail:secret}, 'CHATGPT_ACCESS'],
  [403, {error: {code:secret, message:secret}}, 'CHATGPT_ACCESS'],
  [400, {error:'invalid_grant', error_description:secret}, 'CHATGPT_CODE_EXPIRED'],
  [401, {error:'invalid_client', error_description:secret}, 'CHATGPT_CLIENT'],
  [400, {error:'invalid_scope'}, 'CHATGPT_CLIENT'],
  [401, {}, 'CHATGPT_AUTH'],
  [429, {}, 'CHATGPT_RATE'],
  [503, {}, 'CHATGPT_SERVICE'],
  [200, '<html>unexpected</html>', 'CHATGPT_RESPONSE'],
]) test(`login exchange distinguishes ${status}/${expected}, retains registration and never replays code`, async t => {
  const f = await fixture(t), flow = await f.login();
  f.control.reply = {path:'/oauth/token', response:()=>new Response(typeof body === 'string' ? body : JSON.stringify(body),
    {status, headers:{'x-request-id':secret}})};
  await assert.rejects(f.auth.callback(flow.callback), error => {
    const diagnostic=setupDiagnostic(error);
    assert.equal(diagnostic.code,expected); assert.equal(diagnostic.stage,'auth_exchange');
    assert.equal(diagnostic.http_status,status);
    assert.equal(diagnostic.endpoint,`${issuer}/api/accounts/oauth/token`);
    assert.ok(!JSON.stringify(diagnostic).includes(secret));
    assert.match(diagnostic.error,/浏览器已返回本机/);
    return true;
  });
  assert.equal(f.requests.length,1);
  assert.equal((await f.auth.status()).active,null);
  assert.equal(JSON.parse(f.vault.get('FIXTURE_CHATGPT')).profiles[0].client_id,client);
  await assert.rejects(f.auth.callback(flow.callback),{code:'CHATGPT_STATE'});
  assert.equal(f.requests.length,1);
  assert.equal(new URL(await f.auth.begin('http://127.0.0.1:12345/auth/callback',client)).searchParams.get('client_id'),client);
});

for (const [path,stage] of [['openid-configuration','auth_discovery'],['/jwks','auth_jwks']])
  test(`identity verification preserves ${stage} HTTP failure instead of reporting signature failure`,async t=>{
    const f=await fixture(t),flow=await f.login();
    f.control.reply={path,response:()=>new Response('<html>fixture</html>',{status:403})};
    await assert.rejects(f.auth.callback(flow.callback),error=>error.code==='CHATGPT_ACCESS_HTML' && error.stage===stage && error.httpStatus===403);
    assert.equal((await f.auth.status()).active,null);
    assert.ok(!f.vault.get('FIXTURE_CHATGPT').includes(secret));
  });

test('models permission failure retains connected credentials and reports the model endpoint',async t=>{
  const f=await fixture(t),flow=await f.login(); await f.auth.callback(flow.callback);
  const before=f.vault.get('FIXTURE_CHATGPT');
  f.control.reply={path:'/models',response:()=>new Response(JSON.stringify({error:{code:'chatpass_v2_invalid_authorization_context'}}),{status:403})};
  await assert.rejects(f.auth.models(client),error=>{
    const d=setupDiagnostic(error);assert.equal(d.stage,'auth_models');assert.equal(d.code,'CHATGPT_PERMISSION');
    assert.equal(d.response_kind,'json');assert.equal(d.endpoint,'https://api.openai.com/v1/models');return true;
  });
  assert.equal(f.vault.get('FIXTURE_CHATGPT'),before);
});

for (const terminal of [false,true]) test(`refresh ${terminal?'terminal grant failure clears only unusable tokens':'temporary 403 preserves credentials'}`,async t=>{
  const f=await fixture(t),flow=await f.login(); await f.auth.callback(flow.callback);
  const state=JSON.parse(f.vault.get('FIXTURE_CHATGPT')); state.profiles[0].expires_at=0;
  f.vault.set('FIXTURE_CHATGPT',JSON.stringify(state)); const before=f.vault.get('FIXTURE_CHATGPT');
  f.control.reply={path:'/oauth/token',response:()=>new Response(terminal ? JSON.stringify({error:'invalid_grant'}) : '<html>blocked</html>',{status:terminal?400:403})};
  const start=f.requests.length;
  await assert.rejects(f.auth.access(client),error=>error.stage==='auth_refresh' && error.code===(terminal?'CHATGPT_REFRESH_INVALID':'CHATGPT_ACCESS_HTML'));
  assert.equal(f.requests.length,start+1);
  if (!terminal) assert.equal(f.vault.get('FIXTURE_CHATGPT'),before);
  else {
    const after=JSON.parse(f.vault.get('FIXTURE_CHATGPT'));
    assert.equal(after.host_id,state.host_id);assert.equal(after.active,state.active);
    assert.equal(after.profiles[0].client_id,client);assert.equal(after.profiles[0].subject,state.profiles[0].subject);
    assert.equal(after.profiles[0].refresh_token,undefined);assert.equal(after.profiles[0].access_token,undefined);
    await assert.rejects(f.auth.access(client),{code:'CHATGPT_LOGIN_REQUIRED'});assert.equal(f.requests.length,start+1);
  }
});

test('challenge and known request ID are safe; raw header, body and errors are never echoed',async t=>{
  const f=await fixture(t),flow=await f.login(),requestId='req_'+'a'.repeat(32);
  f.control.reply={path:'/oauth/token',response:()=>new Response(secret,{status:403,headers:{'cf-mitigated':'challenge','x-request-id':requestId}})};
  await assert.rejects(f.auth.callback(flow.callback),error=>{
    const d=setupDiagnostic(error);assert.equal(d.code,'CHATGPT_CHALLENGE');assert.equal(d.request_id,requestId);
    error.details={request_id:secret,provider_code:secret,response_kind:secret};error.message=secret;
    assert.ok(!JSON.stringify(setupDiagnostic(error)).includes(secret));return true;
  });
});

test('exchange response reader is bounded, cancelled and a broken error body still retains HTTP status',async t=>{
  const f=await fixture(t); let cancelled=0;
  f.control.reply={path:'/oauth/token',response:()=>new Response(new ReadableStream({
    start(c){c.enqueue(new Uint8Array(500001));},cancel(){cancelled++;}
  }),{status:403})};
  await assert.rejects(f.auth.callback((await f.login()).callback),error=>{
    const d=setupDiagnostic(error);return d.code==='CHATGPT_ACCESS' && d.http_status===403 && d.response_kind==='unreadable';
  });
  assert.equal(cancelled,1);
});

test('DNS error during exchange reports the exchange stage without secret exception content',async t=>{
  const f=await fixture(t),flow=await f.login();
  f.control.reply={path:'/oauth/token',response:()=>{throw Object.assign(new Error(secret),{cause:{code:'ENOTFOUND'}});}};
  await assert.rejects(f.auth.callback(flow.callback),error=>{
    const d=setupDiagnostic(error);assert.equal(d.code,'NETWORK_DNS');assert.equal(d.stage,'auth_exchange');
    assert.ok(!JSON.stringify(d).includes(secret));return true;
  });
});

test('real callback page shows safe exchange diagnosis and does not invoke inference',async t=>{
  const f=await fixture(t);let inference=0;
  f.control.reply={path:'/oauth/token',response:()=>new Response(`<html>${secret}</html>`,{status:403})};
  const page=await startSetup({directory:f.directory,credentials:f.credentials,keyRef:'FIXTURE',html:'',auth:f.auth,
    probe:async()=>{inference++;}});t.after(()=>page.close());
  const u=new URL(page.url), login=new URL(await f.auth.begin(u.origin+'/auth/callback'));
  const cb=new URL(login.searchParams.get('redirect_uri'));
  cb.search=new URLSearchParams({state:login.searchParams.get('state'),code:'private-authorization-code',client_id:client});
  const response=await fetch(cb),text=await response.text();
  assert.equal(response.status,400);assert.match(text,/CHATGPT_ACCESS_HTML/);assert.match(text,/后台兑换登录凭据/);
  assert.match(text,/响应类型：html/);assert.ok(!text.includes(secret));assert.ok(!text.includes('private-authorization-code'));
  assert.equal(inference,0);assert.equal(f.requests.length,1);
});

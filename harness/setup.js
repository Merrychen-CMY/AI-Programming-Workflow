import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual, createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { SetupError, setupDiagnostic, httpFailure, networkFailure } from './diagnostics.js';

export const keyDigest = key => createHash('sha256').update(key).digest('hex');

async function responseText(response, stage) {
  const reader = response.body?.getReader();
  try {
    if (!reader) {
      const text = await response.text();
      if (Buffer.byteLength(text) > 500_000) throw new SetupError('API_RESPONSE_SIZE', stage);
      return text;
    }
    const chunks = []; let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 500_000) { await reader.cancel(); throw new SetupError('API_RESPONSE_SIZE', stage); }
      chunks.push(Buffer.from(value));
    }
    return Buffer.concat(chunks).toString('utf8');
  } catch (error) { throw error instanceof SetupError ? error : networkFailure(error, stage); }
  finally { reader?.releaseLock(); }
}

function expectedBoolean(text) {
  try { const value = JSON.parse(text); return value?.ok === true && Object.keys(value).length === 1; }
  catch { return false; }
}

export async function probeModel(key, model, fetchImpl = fetch) {
  const request = async (payload, stage) => {
    let response;
    try {
      response = await fetchImpl('https://api.openai.com/v1/responses', {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(45000),
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, store: false, max_output_tokens: 2048, ...payload }),
      });
    } catch (error) { throw networkFailure(error, stage); }
    const body = await responseText(response, stage);
    let result;
    try { result = JSON.parse(body); } catch { /* HTTP status remains usable even for a non-JSON error page. */ }
    if (!response.ok) throw httpFailure(response.status, result, stage);
    if (!result || typeof result !== 'object') throw new SetupError('API_RESPONSE', stage);
    if (result.status !== 'completed') throw new SetupError(result.incomplete_details?.reason === 'max_output_tokens'
      ? 'API_OUTPUT_LIMIT' : 'API_INCOMPLETE', stage);
    if (!Array.isArray(result.output)) throw new SetupError('API_RESPONSE', stage);
    if (result.output.some(o => Array.isArray(o?.content) && o.content.some(c => c?.type === 'refusal')))
      throw new SetupError('API_REFUSAL', stage);
    return result;
  };
  const tool = await request({ input: 'Call workflow_probe with ok=true. This is a synthetic connectivity test.',
    tools: [{ type: 'function', name: 'workflow_probe', description: 'Connectivity fixture', strict: true,
      parameters: { type: 'object', additionalProperties: false, properties: { ok: { type: 'boolean' } }, required: ['ok'] } }],
    tool_choice: 'required', parallel_tool_calls: false }, 'function_call');
  const calls = tool.output.filter(o => o?.type === 'function_call');
  if (calls.length !== 1 || calls[0].name !== 'workflow_probe' || !expectedBoolean(calls[0].arguments))
    throw new SetupError('API_TOOL_FORMAT', 'function_call');
  const structured = await request({ input: 'Return ok=true. Synthetic schema fixture.',
    text: { format: { type: 'json_schema', name: 'workflow_probe', strict: true,
      schema: { type: 'object', additionalProperties: false, properties: { ok: { type: 'boolean' } }, required: ['ok'] } } } }, 'structured_output');
  const output = structured.output.flatMap(o => Array.isArray(o?.content) ? o.content : []).filter(o => o?.type === 'output_text').map(o => o.text).join('');
  if (!expectedBoolean(output)) throw new SetupError('API_JSON_FORMAT', 'structured_output');
  return { status: 'PASS', model, credential_sha256: keyDigest(key), at: new Date().toISOString(),
    api_calls: 2, scope: 'Synthetic API compatibility only; not a product or Codex execution test' };
}

export async function loadConfig(directory) {
  try { return JSON.parse(await readFile(`${directory}/settings.json`, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw new SetupError('CONFIG_READ'); }
  return { model: '', python: 'python', codex: 'codex', probe: null };
}

export async function saveConfig(directory, config) {
  try {
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const temporary = `${directory}/settings.${randomBytes(12).toString('hex')}.tmp`;
    await writeFile(temporary, JSON.stringify(config, null, 2), { mode: 0o600 });
    await rename(temporary, `${directory}/settings.json`);
  } catch { throw new SetupError('CONFIG_WRITE'); }
}

async function credentialOperation(code, operation) {
  try { return await operation(); }
  catch { throw new SetupError(code, 'credentials'); }
}

export async function startSetup({ directory, credentials, keyRef, html, probe = probeModel }) {
  const token = randomBytes(32).toString('hex');
  let origin, busy = false;
  const server = createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'");
    const respond = (code, value) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(value)); };
    if (req.headers.host !== new URL(origin).host) return respond(403, { error: 'Invalid host' });
    if (req.method === 'GET' && req.url === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html); return;
    }
    const supplied = req.headers['x-workflow-token'];
    if (req.headers.origin !== origin || typeof supplied !== 'string' || supplied.length !== token.length ||
        !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) return respond(403, { error: 'Invalid setup session' });
    if (req.method !== 'POST' || req.headers['content-type'] !== 'application/json') return respond(405, { error: 'POST JSON required' });
    if (busy) return respond(409, { error: 'A configuration operation is already running' });
    busy = true;
    try {
      let body = '';
      for await (const chunk of req) { body += chunk.toString(); if (body.length > 16_384) throw new Error('Input too large'); }
      const input = JSON.parse(body);
      const config = await loadConfig(directory);
      if (req.url === '/status') {
        const configured = Boolean(await credentialOperation('CREDENTIAL_READ', () => credentials.resolve(keyRef)));
        return respond(200, { model:config.model, python:config.python, codex:config.codex,
          probe: config.probe ? { status: config.probe.status, at: config.probe.at } : null, configured });
      }
      if (req.url === '/save') {
        if (Object.keys(input).some(k => !['model', 'python', 'codex', 'key'].includes(k)) ||
            ['model', 'python', 'codex'].some(k => typeof input[k] !== 'string' || !input[k].trim() || /[\r\n\0]/.test(input[k])) ||
            !/^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/.test(input.model.trim()) || input.model.trim().startsWith('sk-'))
          throw new SetupError('CONFIG_INVALID');
        if (input.key) {
          if (typeof input.key !== 'string' || /\s/.test(input.key) || input.key.length > 1024) throw new SetupError('KEY_FORMAT');
          await credentialOperation('CREDENTIAL_WRITE', () => credentials.set(keyRef, input.key));
        }
        await saveConfig(directory, { model: input.model.trim(), python: input.python.trim(), codex: input.codex.trim(), probe: null });
        return respond(200, { saved: true, next: 'Run the explicit API compatibility test' });
      }
      if (req.url === '/probe') {
        const resolved = await credentialOperation('CREDENTIAL_READ', () => credentials.resolve(keyRef));
        if (!resolved || !config.model) throw new SetupError('SETUP_REQUIRED');
        await saveConfig(directory, { ...config, probe: null });
        const result = await probe(resolved.value, config.model);
        await saveConfig(directory, { ...config, probe: result });
        return respond(200, { status: result.status, api_calls: result.api_calls, scope: result.scope });
      }
      respond(404, { error: 'Unknown setup operation' });
    } catch (error) {
      respond(400, setupDiagnostic(error));
    } finally { busy = false; }
  });
  server.requestTimeout = 120000;
  server.headersTimeout = 15000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;
  return { url: `${origin}/#${token}`, close: () => server.close(), server };
}

import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual, createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';

export const keyDigest = key => createHash('sha256').update(key).digest('hex');

export async function probeModel(key, model, fetchImpl = fetch) {
  const request = async payload => {
    const response = await fetchImpl('https://api.openai.com/v1/responses', {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(45000),
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, store: false, max_output_tokens: 2048, ...payload }),
    });
    if (!response.ok) throw new Error(`API probe failed: HTTP ${response.status}`);
    const body = await response.text();
    if (body.length > 500_000) throw new Error('API probe response exceeds limit');
    const result = JSON.parse(body);
    if (result.status !== 'completed') throw new Error('API probe did not complete');
    return result;
  };
  const tool = await request({ input: 'Call workflow_probe with ok=true. This is a synthetic connectivity test.',
    tools: [{ type: 'function', name: 'workflow_probe', description: 'Connectivity fixture', strict: true,
      parameters: { type: 'object', additionalProperties: false, properties: { ok: { type: 'boolean' } }, required: ['ok'] } }],
    tool_choice: 'required', parallel_tool_calls: false });
  const calls = (tool.output || []).filter(o => o.type === 'function_call');
  if (calls.length !== 1 || calls[0].name !== 'workflow_probe' || JSON.stringify(JSON.parse(calls[0].arguments)) !== '{"ok":true}')
    throw new Error('Model failed the function-call compatibility check');
  const structured = await request({ input: 'Return ok=true. Synthetic schema fixture.',
    text: { format: { type: 'json_schema', name: 'workflow_probe', strict: true,
      schema: { type: 'object', additionalProperties: false, properties: { ok: { type: 'boolean' } }, required: ['ok'] } } } });
  const output = (structured.output || []).flatMap(o => o.content || []).filter(o => o.type === 'output_text').map(o => o.text).join('');
  if (JSON.stringify(JSON.parse(output)) !== '{"ok":true}') throw new Error('Model failed the structured-output check');
  return { status: 'PASS', model, credential_sha256: keyDigest(key), at: new Date().toISOString(),
    api_calls: 2, scope: 'Synthetic API compatibility only; not a product or Codex execution test' };
}

export async function loadConfig(directory) {
  try { return JSON.parse(await readFile(`${directory}/settings.json`, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw new Error('Invalid plugin configuration'); }
  return { model: '', python: 'python', codex: 'codex', probe: null };
}

export async function saveConfig(directory, config) {
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temporary = `${directory}/settings.${randomBytes(12).toString('hex')}.tmp`;
  await writeFile(temporary, JSON.stringify(config, null, 2), { mode: 0o600 });
  await rename(temporary, `${directory}/settings.json`);
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
        const configured = Boolean(await credentials.resolve(keyRef));
        return respond(200, { ...config, probe: config.probe ? { status: config.probe.status, at: config.probe.at } : null, configured });
      }
      if (req.url === '/save') {
        if (Object.keys(input).some(k => !['model', 'python', 'codex', 'key'].includes(k)) ||
            ['model', 'python', 'codex'].some(k => typeof input[k] !== 'string' || !input[k].trim() || /[\r\n\0]/.test(input[k])) ||
            !/^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/.test(input.model.trim()) || input.model.trim().startsWith('sk-'))
          throw new Error('Model and executable paths are required; do not enter command-line arguments');
        if (input.key) {
          if (typeof input.key !== 'string' || /\s/.test(input.key) || input.key.length > 1024) throw new Error('Invalid key field');
          await credentials.set(keyRef, input.key);
        }
        await saveConfig(directory, { model: input.model.trim(), python: input.python.trim(), codex: input.codex.trim(), probe: null });
        return respond(200, { saved: true, next: 'Run the explicit API compatibility test' });
      }
      if (req.url === '/probe') {
        const resolved = await credentials.resolve(keyRef);
        if (!resolved || !config.model) throw new Error('Configure model and API key first');
        await saveConfig(directory, { ...config, probe: null });
        const result = await probe(resolved.value, config.model);
        await saveConfig(directory, { ...config, probe: result });
        return respond(200, { status: result.status, api_calls: result.api_calls, scope: result.scope });
      }
      respond(404, { error: 'Unknown setup operation' });
    } catch {
      // Underlying provider/fetch exceptions may include secrets; never echo them.
      respond(400, { error: '配置或连接检查失败。请核对模型、密钥权限、账户额度、可执行文件路径和网络；未启动产品任务。' });
    } finally { busy = false; }
  });
  server.requestTimeout = 120000;
  server.headersTimeout = 15000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  origin = `http://127.0.0.1:${server.address().port}`;
  return { url: `${origin}/#${token}`, close: () => server.close(), server };
}

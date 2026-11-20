import { SetupError, httpFailure, networkFailure } from './diagnostics.js';

/** Match the public SIWC Responses contract; no backend-api or API-key fallback. */
export function chatgptPayload(payload) {
  const next = { ...payload, store: false, stream: true };
  for (const key of ['max_output_tokens', 'background', 'conversation', 'max_tool_calls', 'metadata',
    'moderation', 'multi_agent', 'prompt', 'prompt_cache_retention', 'safety_identifier', 'temperature',
    'top_logprobs', 'top_p', 'truncation', 'user', 'previous_response_id']) delete next[key];
  if (!Array.isArray(next.input)) next.input = [{ role: 'user', content: next.input }];
  if (next.tools?.length && !next.tools.every(t => t.type === 'namespace')) {
    if (next.tools.some(t => t.type !== 'function')) throw new SetupError('CHATGPT_TOOLS');
    next.tools = [{ type: 'namespace', name: 'reqpilot', description: 'ReqPilot workflow tools', tools: next.tools }];
  }
  return next;
}

export async function streamResponse(response, signal, limit = 8 * 1024 * 1024) {
  const reader = response.body?.getReader();
  if (!reader) throw new SetupError('API_RESPONSE');
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '', size = 0;
  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new SetupError('API_RESPONSE_SIZE');
      buffer += decoder.decode(value, { stream: true });
      let match;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, match.index); buffer = buffer.slice(match.index + match[0].length);
        const data = block.split(/\r?\n/).filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n');
        if (!data || data === '[DONE]') continue;
        let event;
        try { event = JSON.parse(data); } catch { throw new SetupError('API_RESPONSE'); }
        if (['response.completed', 'response.failed', 'response.incomplete'].includes(event.type)) {
          if (!event.response || typeof event.response !== 'object') throw new SetupError('API_RESPONSE');
          return event.response;
        }
        if (event.type === 'error') throw new SetupError('API_INCOMPLETE');
      }
    }
    throw new SetupError('API_INCOMPLETE'); // EOF is not evidence of completion; never replay here.
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export async function chatgptRequest(token, payload, timeoutSeconds, { fetchImpl = fetch, signal } = {}) {
  const timeout = AbortSignal.timeout(Math.max(1, Math.min(2147483647, Math.ceil(timeoutSeconds * 1000))));
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const body = JSON.stringify(chatgptPayload(payload));
  let response;
  try {
    response = await fetchImpl('https://api.openai.com/v1/responses', { method: 'POST', redirect: 'error', signal: combined,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body });
    if (!response.ok) {
      // Only status is needed for subscription failures; never echo a provider error or secret.
      await response.body?.cancel();
      if (response.status === 429) throw new SetupError('CHATGPT_LIMIT', 'function_call', 429);
      throw httpFailure(response.status, {}, 'function_call');
    }
    return await streamResponse(response, combined);
  } catch (error) { throw error instanceof SetupError ? error : networkFailure(error, 'function_call'); }
}

export function chatgptRelay(auth, profile, fetchImpl = fetch) {
  return async (frame, signal) => {
    let sent = false;
    try {
      const payload = chatgptPayload(frame.arguments.payload);
      const { token } = await auth.access(profile);
      sent = true;
      const response = await chatgptRequest(token, payload, frame.timeout_seconds, { fetchImpl, signal });
      return { status: 'PASS', response };
    } catch (error) {
      const status = error.httpStatus;
      const outcome = !sent || ['NETWORK_DNS', 'NETWORK_TLS'].includes(error.code) ? 'NOT_SENT'
        : [400, 401, 403, 404, 413, 422, 429].includes(status) ? 'REJECTED' : 'UNKNOWN';
      return { status: 'FAIL', diagnostic: { version: 1, code: error instanceof SetupError ? error.code : 'CHATGPT_CONNECTION',
        outcome, auth_mode: 'chatgpt', ...(status ? { http_status: status } : {}) } };
    }
  };
}

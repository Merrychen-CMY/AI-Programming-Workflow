import { randomUUID } from 'node:crypto';

// These names select workflow identities/approvals, not general agent capabilities.

export async function dispatchHostTool(ctx, input, ToolExecutionPipeline) {
  const pipeline = new ToolExecutionPipeline(ctx);
  const prepared = await pipeline.prepare(input);
  const outcome = prepared.kind === 'dispatch' ? await pipeline.dispatch(prepared.exec) : prepared;
  return pipeline.complete(prepared.exec, outcome);
}

/** Dynamic scoped capabilities through the host's shared policy/approval pipeline. */
export function hostTools(ctx, exec, { dispatch, controlTools = [] } = {}) {
  const governance = new Set(controlTools);
  return async (request, signal = exec.signal) => {
    const fail = summary => ({ status: 'FAIL', summary });
    if (!exec.agent || !ctx.tools?.schemas || typeof dispatch !== 'function')
      return fail('HOST_TOOLS_UNAVAILABLE: install/reload the current host plugin; no host call dispatched');
    const schemas = () => ctx.tools.schemas(exec.agent, exec.toolSet).filter(tool => !governance.has(tool.name));
    if (request.operation === 'list') {
      const { query, offset } = request.arguments;
      if (typeof query !== 'string' || !Number.isSafeInteger(offset) || offset < 0) return fail('Invalid tool discovery arguments');
      const found = schemas().filter(tool => `${tool.name} ${tool.description}`.toLowerCase().includes(query.toLowerCase()));
      return { status: 'PASS', summary: `${found.length} matching host tools`, tools: found.slice(offset, offset + 8),
        next_offset: offset + 8 < found.length ? offset + 8 : null, total: found.length };
    }
    if (request.operation !== 'call') return fail('Invalid host relay operation');
    const { name, arguments_json } = request.arguments;
    if (typeof name !== 'string' || !schemas().some(tool => tool.name === name))
      return fail('TOOL_NOT_AVAILABLE: tool is not admitted in this session or belongs to L1/workflow control');
    let args;
    try { args = JSON.parse(arguments_json); }
    catch { return fail('Invalid tool arguments JSON; nothing dispatched'); }
    if (!args || Array.isArray(args) || typeof args !== 'object') return fail('Tool arguments must be an object');
    const result = await dispatch(ctx, { name, arguments: args, callId: `reqpilot-l2-${randomUUID()}`,
      rootCallId: exec.rootCallId || exec.callId, parent: exec.token,
      agent: exec.agent, toolSet: exec.toolSet, signal });
    for (const context of result.additionalContexts || []) exec.deferContext?.(context);
    if (result.concludesTurn) exec.concludeTurn?.();
    const code = result.error?.info?.code;
    const images = [], warnings = [];
    // Use only settled model-visible content: post-execute redaction must not be bypassed by raw value.
    const content = [];
    for (const block of result.content || []) {
      if (block.type === 'image' && block.attachment) {
        try {
          const attachments = ctx.get('attachments');
          if (!attachments) throw new Error('Attachment service unavailable');
          const stored = await attachments.readImage(block.attachment, signal);
          images.push({ type: 'input_image', detail: 'auto',
            image_url: `data:${block.attachment.mediaType};base64,${Buffer.from(stored.data).toString('base64')}` });
          content.push(block);
        } catch { warnings.push('Image bytes unavailable; do not claim to have seen this image'); }
      } else content.push(block);
    }
    return { status: code === 'ABORTED' ? 'UNKNOWN' : result.isError ? 'FAIL' : 'PASS',
      summary: content.filter(block => block.type === 'text').map(block => block.text).join('\n').slice(0, 12000),
      content, images, warnings, host_contexts: result.additionalContexts || [],
      concludes_turn: Boolean(result.concludesTurn),
      ...(code ? { error_code: code } : {}) };
  };
}

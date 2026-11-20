import { readFile, configuredAccess } from './file-access.js';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { ToolExecutionPipeline } from '@deepseek-ai/dsh-tools/pipeline';
import { dispatchHostTool } from './host-tools.js';
import { credentialRef } from '@deepseek-ai/dsh-credentials';
import { toolFields, descriptions } from './workflow.js';
import { createBootstrap, updateFields, updateDescriptions } from './bootstrap.js';
import { reportDelivery } from './report-delivery.js';

export const name = 'ai-programming-workflow';
export const inject = ['tools', 'systemPrompt', 'credentials', 'userQuestions', 'agents', 'fs'];

export async function apply(ctx) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const workflow = createBootstrap({ ctx, root, fields: toolFields, keyRef: credentialRef('AI_WORKFLOW_OPENAI_API_KEY'),
    workflowOptions: { hostDispatch: (host, input) => dispatchHostTool(host, input, ToolExecutionPipeline) } });
  const deliver = reportDelivery(ctx);
  ctx.effect(() => () => workflow.dispose());
  let guidance;
  try { guidance = await configuredAccess(resolve(homedir(),'.ai-programming-workflow'), async () =>
    (await Promise.all(['harness/prompt.md','AGENTS.md','L1.md'].map(path => readFile(new URL(`../${path}`, import.meta.url),'utf8')))).join('\n\n')); }
  catch { guidance = 'ReqPilot reference files are unreadable. Use workflow_updates action=diagnose to inspect file access. Preserve existing tasks. Do not run product work, infer approvals, or finalize failure from this loading problem.'; }
  ctx.systemPrompt.section({ name, order: 150, interpolate: false, text: guidance });
  for (const [toolName, fields] of Object.entries({ ...toolFields, ...updateFields })) {
    ctx.tools.register(defineTool({ name: toolName, description: descriptions[toolName] || updateDescriptions[toolName],
      parameters: Object.fromEntries(Object.entries(fields).map(([key,description])=>
        [key,{ type: 'string', required: true, description }])),
      output: { schema: { type: 'string' }, render: (_args,value)=>[{ type: 'text', text: value }] },
      async execute(args, exec) {
        try { return JSON.stringify(await configuredAccess(resolve(homedir(),'.ai-programming-workflow'), async () =>
          deliver(await workflow.invoke(toolName,args,exec),exec))); }
        catch(error) { return JSON.stringify({ error: error.message, executed_successfully: false }); }
      },
    }));
  }
}

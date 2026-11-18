import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { credentialRef } from '@deepseek-ai/dsh-credentials';
import { createWorkflow, toolFields, descriptions } from './workflow.js';

export const name = 'ai-programming-workflow';
export const inject = ['tools', 'systemPrompt', 'credentials', 'userQuestions'];

export function apply(ctx) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const workflow = createWorkflow({ ctx, root, keyRef: credentialRef('AI_WORKFLOW_OPENAI_API_KEY') });
  ctx.effect(() => () => workflow.dispose());
  ctx.systemPrompt.section({ name, order: 150, interpolate: false,
    text: ['harness/prompt.md','AGENTS.md','L1.md'].map(path => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')).join('\n\n') });
  for (const [toolName, fields] of Object.entries(toolFields)) {
    ctx.tools.register(defineTool({ name: toolName, description: descriptions[toolName],
      parameters: Object.fromEntries(Object.entries(fields).map(([key,description])=>
        [key,{ type: 'string', required: true, description }])),
      output: { schema: { type: 'string' }, render: (_args,value)=>[{ type: 'text', text: value }] },
      async execute(args, exec) {
        try { return JSON.stringify(await workflow.invoke(toolName,args,exec)); }
        catch(error) { return JSON.stringify({ error: error.message, executed_successfully: false }); }
      },
    }));
  }
}

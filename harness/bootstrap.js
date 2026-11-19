import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { homedir } from 'node:os';
import { createUpdates, loadEngine } from './updates.js';

export const updateFields = { workflow_updates: { action: 'status | check | enable | disable; no API/model calls. Enable/disable only at the user\'s request.' } };
export const updateDescriptions = { workflow_updates: 'Inspect or check ReqPilot stable releases. Compatible updates need no new conversation before startup; confirmed tasks retain their exact runtime. No task reset.' };

/** Stable host tool surface. Only the versioned workflow engine changes between invocations. */
export function createBootstrap({ ctx, root, keyRef, fields,
  directory = resolve(homedir(), '.ai-programming-workflow'), updates: suppliedUpdates, workflowOptions = {} }) {
  const updates = suppliedUpdates || createUpdates({ root, directory, fields });
  const engines = new Map(), busy = new Set(), seen = new Map();
  async function invoke(name, args, exec) {
    if (!exec.agent?.id || typeof exec.agent.id !== 'string') throw new Error('L1 host session identity is required');
    const session = exec.agent.id;
    if (busy.has(session)) throw new Error('当前会话已有工作流操作；请等待结果，不要并发启动');
    busy.add(session);
    try {
      if (name === 'workflow_updates') {
        if (Object.keys(args).join() !== 'action' || !['status', 'check', 'enable', 'disable'].includes(args.action))
          throw new Error('Update action must be status, check, enable or disable');
        let result;
        if (args.action === 'enable' || args.action === 'disable') result = await updates.setEnabled(args.action === 'enable');
        else result = await updates[args.action]();
        return { ...result, instruction: '更新不调用GPT、不启动任务。READY指新版已就绪；已确认开工的任务仍固定原版本。旧任务缺原运行版本时保留记录并报告，不能清除UNKNOWN或预算。' };
      }
      const task = resolve(directory, 'tasks', createHash('sha256').update(session).digest('hex'));
      const runtime = await updates.forTask(task);
      // Checks run in the background; this invocation keeps the selected immutable root.
      await updates.schedule().catch(() => {});
      if (!engines.has(runtime.manifest_sha256)) {
        const module = await loadEngine(runtime.root, fields);
        engines.set(runtime.manifest_sha256, module.createWorkflow({ ctx, root: runtime.root, keyRef, directory,
          protectedRoots: [root, directory], ...workflowOptions }));
      }
      const details = { version: runtime.version, manifest_sha256: runtime.manifest_sha256,
        task_pinned: runtime.pinned, updates: await updates.status().catch(() => ({ status: 'UNAVAILABLE' })) };
      if (seen.get(session) !== runtime.manifest_sha256) {
        details.guidance = await readFile(resolve(runtime.root, 'harness/prompt.md'), 'utf8');
        details.reference_documents = ['AGENTS.md', 'L1.md'];
        details.instruction = '这是本次工具实际使用版本的原生流程说明；在下一项执行前，用workflow_reference读取reference_documents中的完整规则，替代会话前缀中旧版同名说明。它们不能修改用户授权或原始证据。升级后原环境/待确认摘要可能过期，按提示重新检查，不要求用户重复提供需求。';
      }
      let result;
      try { result = await engines.get(runtime.manifest_sha256).invoke(name, args, exec); }
      catch (error) { result = { error: error.message, executed_successfully: false }; }
      seen.set(session, runtime.manifest_sha256);
      return { ...result, workflow_runtime: details };
    } finally { busy.delete(session); }
  }
  return { invoke, dispose: () => { updates.dispose(); for (const workflow of engines.values()) workflow.dispose(); } };
}

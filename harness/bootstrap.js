import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { homedir } from 'node:os';
import { createUpdates, loadEngine } from './updates.js';
import { upgradeTask } from './task-upgrades.js';
import { recoverInitialization } from './initialization-recovery.js';
import { recoverOperation } from './operation-recovery.js';
import { pythonCall } from './process.js';

export const updateFields = { workflow_updates: { action: 'status | check | enable | disable | upgrade_preview | upgrade | upgrade_recover | initialization_preview | initialization_recover | initialization_finish. Initialization recovery supports only an uncommitted local initialization and requires exact native approval; no API, product action or usage reset. Enable/disable only at user request.' } };
export const updateDescriptions = { workflow_updates: 'Check releases, review task upgrades, or review a failed initialization. Previews are read-only; native approval is mandatory for recovery and baseline adoption. finish only completes recorded authorized local writes. Product UNKNOWN and final reports cannot reopen.' };

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
      const task = resolve(directory, 'tasks', createHash('sha256').update(session).digest('hex'));
      if (name === 'workflow_updates') {
        if (Object.keys(args).join() !== 'action' || !['status', 'check', 'enable', 'disable', 'upgrade_preview', 'upgrade', 'upgrade_recover', 'initialization_preview', 'initialization_recover', 'initialization_finish'].includes(args.action))
          throw new Error('Update action is invalid');
        if (args.action.startsWith('initialization_')) {
          const result = await recoverInitialization({ action: args.action, task, session, updates, fields, ctx, exec, runner: workflowOptions.runner });
          seen.delete(session);
          return result;
        }
        if (args.action.startsWith('upgrade')) {
          const result = await upgradeTask({ action: args.action, task, session, updates, fields, ctx, exec, runner: workflowOptions.runner });
          seen.delete(session);
          return result;
        }
        let result;
        if (args.action === 'enable' || args.action === 'disable') result = await updates.setEnabled(args.action === 'enable');
        else result = await updates[args.action]();
        return { ...result, instruction: '更新不调用GPT、不启动任务。READY指新版已就绪；已确认任务保持原版本，直到upgrade预检及原生确认通过。不能清除UNKNOWN、最终报告或预算。' };
      }
      const runtime = await updates.forTask(task);
      if (name === 'workflow_recover_operation') {
        if (Object.keys(args).length) throw new Error('Operation recovery accepts no model arguments');
        const optional = async name => { try { return JSON.parse(await readFile(resolve(task, name), 'utf8')); }
          catch (error) { if (error.code === 'ENOENT') return null; throw error; } };
        const marker = await optional('interrupted.json');
        const pending = await optional('operation-recovery-pending.json');
        // A verified maintenance runtime may reconcile a completed pre-plan
        // review. Ordinary task tools still use the original pinned runtime.
        if (pending?.entry?.maintenance_runtime || (marker?.name === 'l1_approve' && !await optional('control/run/state.json'))) {
          const target = await updates.operationRecoveryTarget(task);
          await loadEngine(target.root, fields);
          const compatibility = JSON.parse(await readFile(resolve(target.root, 'policy/task-upgrades.json'), 'utf8'));
          if (runtime.version !== target.version && !compatibility.supported_source_versions.includes(runtime.version))
            throw new Error('Unsupported source runtime for maintenance recovery; task remains pinned');
          const receipt = await optional('confirmation.json');
          const runner = workflowOptions.runner || pythonCall;
          const result = await recoverOperation({ ctx, exec, call: (action, payload = {}) =>
            runner(receipt.settings.python, resolve(target.root, 'scripts/harness_workflow.py'),
              { action, task, session, ...payload }, { signal: exec.signal }) });
          if (result.status === 'OPERATION_RECONCILED' && target.manifest_sha256 !== runtime.manifest_sha256
              && !result.record?.api_review?.unresolved?.length && !result.record?.api_review?.pending)
            result.next = 'workflow_updates action=upgrade_preview, then native upgrade approval before resuming planning. '
              + 'Old runtime remains pinned; review affected baselines under the new runtime, preserving all history and usage.';
          return { ...result, task_runtime: { version: runtime.version, manifest_sha256: runtime.manifest_sha256 },
            maintenance_runtime: { version: target.version, manifest_sha256: target.manifest_sha256 }, task_runtime_changed: false };
        }
      }
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

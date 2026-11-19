import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pythonCall } from './process.js';
import { loadEngine } from './updates.js';

/** Explicit native approval, separate from upgrades and ordinary L1 adjustments. */
export async function recoverInitialization({ action, task, session, updates, fields, ctx, exec, runner = pythonCall }) {
  const finish = action === 'initialization_finish';
  const target = await updates.recoveryTarget(task, finish);
  await loadEngine(target.root, fields);
  const receipt = JSON.parse(await readFile(resolve(task, 'confirmation.json'), 'utf8'));
  const call = (name, payload = {}) => runner(receipt.settings.python, resolve(target.root, 'scripts/harness_workflow.py'),
    { action: name, task, session, ...payload }, { signal: exec.signal });
  if (finish) return call('initialization_finish');
  const source = await updates.forTask(task);
  const identity = { version: source.version, manifest_sha256: source.manifest_sha256 };
  const preview = await call('initialization_preview', { source: identity });
  if (action === 'initialization_preview') return preview;
  const id = `initialization-${preview.sha256}`;
  const label = preview.baseline_changed ? '批准恢复并采用所示新基线' : '批准本次初始化恢复';
  const question = `恢复未完成的初始化\n项目：${preview.project}\n运行版本：${preview.source.version} → ${preview.target.version}\n` +
    `之前基线：${preview.baseline_before.id}（${preview.baseline_before.file_count} 个文件）\n` +
    `当前基线：${preview.baseline_after.id}（${preview.baseline_after.file_count} 个文件）\n` +
    `基线变化：${preview.baseline_changed ? '是；批准即接受所示新基线，随后必须重新规划及独立审核' : '否'}\n` +
    `旧版仅保存基线摘要和数量，可能无法提供完整历史差异。请先核实文件变化符合预期。\n` +
    `已有 API 请求：${preview.api_calls} 次；已报告 tokens：${preview.usage.total_tokens}；未报告用量请求：${preview.usage.unreported_requests}。\n` +
    `失败信息：${preview.failure?.error || '旧版未保存完整错误；部分快照将保留供诊断'}\n` +
    `核对编号：${preview.sha256}\n\n仅归档部分初始化和中断标记，保留原确认、目标、权限、额度、截止时间、累计消耗及全部历史。` +
    `此次恢复不调用 API、不派工、不修改产品文件、不处理产品执行 UNKNOWN、不撤销最终报告。后续工作继续计入原预算。`;
  const reply = await ctx.userQuestions.ask({ agent: exec.agent, signal: exec.signal, questions: [{
    id, question, header: '初始化恢复', multiSelect: false, options: [
      { label, description: '批准上述准确方案，保留原授权与全部历史消耗。' },
      { label: '暂不恢复', description: '保留现状，先核对失败原因及项目变化。' }] }] });
  const answer = reply.answers?.find(a => a.id === id);
  if (exec.signal?.aborted || !answer || answer.custom || answer.selected?.length !== 1 || answer.selected[0] !== label)
    return { status: 'INITIALIZATION_RECOVERY_NOT_APPROVED', task_changed: false };
  const checked = await updates.recoveryTarget(task);
  if (checked.manifest_sha256 !== target.manifest_sha256) throw new Error('恢复运行版本已改变，请重新预览');
  return call('initialization_commit', { source: identity, sha256: preview.sha256 });
}

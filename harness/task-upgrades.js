import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pythonCall } from './process.js';
import { loadEngine } from './updates.js';

/** Native approval bridge: the model can request a review, never supply its decision. */
export async function upgradeTask({ action, task, session, updates, fields, ctx, exec, runner = pythonCall }) {
  const recover = action === 'upgrade_recover';
  const target = await updates.upgradeTarget(task, recover);
  const receipt = JSON.parse(await readFile(resolve(task, 'confirmation.json'), 'utf8'));
  const source = recover ? null : await updates.forTask(task);
  await loadEngine(target.root, fields);
  const call = (name, payload = {}) => runner(receipt.settings.python, resolve(target.root, 'scripts/harness_workflow.py'),
    { action: name, task, session, ...payload }, { signal: exec.signal });
  if (recover) return call('upgrade_recover');
  const identity = { version: source.version, manifest_sha256: source.manifest_sha256 };
  const preview = await call('upgrade_preview', { source: identity });
  if (action === 'upgrade_preview') return preview;
  const id = `upgrade-${preview.sha256}`;
  const question = `升级当前已开工任务\n${preview.source.version} → ${preview.target.version}\n原版本摘要：${preview.source.manifest_sha256}\n目标版本摘要：${preview.target.manifest_sha256}\n任务状态：${preview.state}\n核对编号：${preview.sha256}\n\n保留原始开工确认、目标、权限、明确截止时间、累计消耗、已完成任务和全部历史证据。采用新版资源委托：后续资源追加由L1评估并自行批准，无需用户审批；初始数值保留为历史。旧审批与候选版本失效，由L1重新组织独立审核及完整验证；这些后续工作继续累计计费和计数，所需额度由L1评估调整。原有待处理问题仍须解决。此操作只切换运行版本，不执行产品任务，不清除UNKNOWN或撤销最终报告。`;
  const reply = await ctx.userQuestions.ask({ agent: exec.agent, signal: exec.signal, questions: [{
    id, question, header: '任务版本升级', multiSelect: false, options: [
      { label: '批准本次任务升级', description: '批准此版本迁移及L1资源管理委托，保留目标、权限及历史消耗。' },
      { label: '暂不升级', description: '继续保留当前版本。' }] }] });
  const answer = reply.answers?.find(a => a.id === id);
  if (exec.signal?.aborted || !answer || answer.custom || answer.selected?.length !== 1 || answer.selected[0] !== '批准本次任务升级')
    return { status: 'UPGRADE_NOT_APPROVED', task_changed: false };
  // The target and all task evidence are checked again after the human wait.
  const checked = await updates.upgradeTarget(task);
  if (checked.manifest_sha256 !== target.manifest_sha256) throw new Error('升级目标已改变，请重新预览');
  return call('upgrade_commit', { source: identity, sha256: preview.sha256 });
}

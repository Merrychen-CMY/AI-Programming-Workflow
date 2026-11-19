/** A model can propose recovery; only this native UI may approve its exact snapshot. */
export async function recoverOperation({ call, ctx, exec }) {
  const preview = await call('operation_preview');
  if (preview.finish_required) return call('operation_finish');
  if (!preview.ready) return { status: 'OPERATION_RECOVERY_BLOCKED', preview,
    next: '先核实旧进程与副作用；仍在运行或无法核实的操作不会解除，也不会自动结案。' };
  const id = `operation-recovery-${preview.sha256}`;
  const adoption = preview.baseline_adoption;
  const label = adoption?.changed ? '批准恢复并采用所示新基线' : '已核实证据并批准本次恢复';
  const usage = preview.usage ?? {};
  const displayedUsage = {
    user_resource_ceilings: usage.user_authority?.limits,
    final_deadline: usage.user_authority?.expires_at,
    operating_limits: usage.operating_limits, actual_usage: usage.actual_usage,
    dot_api_reported_usage: usage.dot_api_reported_usage,
    adjustment_records: usage.history?.length ?? 0,
    pending_authorization: Boolean(usage.pending_authorization),
    note: '历史明细完整保留；此处只显示当前额度、累计消耗和记录数量。',
  };
  const api = preview.api_review ?? {};
  const displayedApi = {state: api.state, unresolved: api.unresolved, pending: Boolean(api.pending),
    history_records: api.history?.length ?? 0, usage_note: api.usage_note};
  const question = `操作核实与恢复\n项目：${preview.project}\n核对编号：${preview.sha256}\n\n`
    + (adoption?.applies_before_initialization
      ? `之前基线：${adoption.before.id}（${adoption.before.file_count} 个文件）\n`
        + `当前基线：${adoption.after.id}（${adoption.after.file_count} 个文件）\n`
        + `基线变化：${adoption.changed ? '是；批准明确接受所示新基线，随后必须重新整理需求、独立审查及规划' : '否'}\n`
        + '原确认完整保留；基线指纹和数量不等于完整历史文件差异，请核对所列真实变化证据。\n\n'
      : '')
    + `L1 分析及恢复方案：\n${JSON.stringify(preview.proposal, null, 2)}\n\n`
    + `真实进程检查：\n${JSON.stringify(preview.processes, null, 2)}\n\n`
    + `原操作：\n${JSON.stringify(preview.binding, null, 2)}\n\n`
    + `累计消耗：\n${JSON.stringify(displayedUsage, null, 2)}\n\n`
    + `API 状态：\n${JSON.stringify(displayedApi, null, 2)}\n\n`
    + '请先核对所列实际证据，确认旧操作的进程、工作区和设备/网络副作用均已核实。'
    + '模型陈述不等于真实核实。批准仅解除这份快照中已核实的中断保护，归档原记录，保留全部累计消耗、失败和已完成工作。'
    + '恢复后进入 L2 调查与 L1 独立重审；若仍有未决 API，必须先走独立 API 评估流程，旧结果和未知费用原样保留。'
    + '不重放旧操作、不立即派工、不修改产品、不增加权限、不撤销最终报告。';
  if (question.length > 65_000) throw new Error('恢复材料超过原生界面上限；缩小方案后重新提交，未解除中断');
  const reply = await ctx.userQuestions.ask({ agent: exec.agent, signal: exec.signal, questions: [{
    id, question, header: '操作恢复', multiSelect: false, options: [
      { label, description: adoption?.changed ? '确认核实证据并采用显示的新基线，随后重新审查和规划。' : '确认所示核实证据，仅恢复到调查和独立重审阶段。' },
      { label: '暂不恢复', description: '保留中断保护，继续核对真实进程与副作用。' },
    ],
  }] });
  const answer = reply.answers?.find(item => item.id === id);
  if (exec.signal?.aborted || !answer || answer.custom || answer.selected?.length !== 1 || answer.selected[0] !== label)
    return { status: 'OPERATION_RECOVERY_NOT_APPROVED', task_changed: false };
  return call('operation_commit', { sha256: preview.sha256 });
}

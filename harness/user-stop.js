/** Native-only user choice; entering the dialog pauses new dispatch first. */
export async function confirmStop({ call, ctx, exec }) {
  const preview = await call('stop_preview');
  if (preview.already_confirmed) return preview;
  const id = `user-stop-${preview.sha256}`;
  const snapshot = preview.snapshot ?? {};
  const question = `停止任务确认\n项目：${preview.project}\n状态：${snapshot.runtime_state}\n确认编号：${preview.sha256}\n\n`
    + `已用实现次数：${snapshot.implementation_attempts}\n已用修订次数：${snapshot.revision_count}\n`
    + `额度与累计消耗：${JSON.stringify(preview.resources ?? {})}\n`
    + `待核实操作：${JSON.stringify({active_run:snapshot.active_run,analysis_activity:snapshot.analysis_activity,host_interrupted:preview.host_interrupted})}\n\n`
    + '当前已暂停新的派工和重试。确认停止只记录你的停止意愿，随后由 L1 明确评估并提交最终报告；不会直接判定失败。'
    + '所有额度、已用次数、失败、证据和中断记录原样保留，不清除 active/UNKNOWN，不重放旧操作，不执行产品动作。'
    + '这不会声称旧进程已经终止；已有未决操作仍需核实。未回答或关闭此窗口将保持暂停。';
  const reply = await ctx.userQuestions.ask({agent:exec.agent,signal:exec.signal,questions:[{
    id,question,header:'停止任务',multiSelect:false,options:[
      {label:'确认停止，交由 L1 总结',description:'记录本任务停止意愿，保留未决操作和累计消耗。'},
      {label:'不停止，回到原流程',description:'只取消尚未确认的停止暂停，其余审批和中断保护仍有效。'},
    ],
  }]});
  const answer = reply.answers?.find(item => item.id === id);
  if (exec.signal?.aborted || !answer || answer.custom || answer.selected?.length !== 1
      || !['确认停止，交由 L1 总结','不停止，回到原流程'].includes(answer.selected[0]))
    return {status:'USER_STOP_PENDING',dispatch_paused:true,finalized:false};
  return call('stop_confirm',{sha256:preview.sha256,
    decision:answer.selected[0] === '确认停止，交由 L1 总结' ? 'stop' : 'continue'});
}

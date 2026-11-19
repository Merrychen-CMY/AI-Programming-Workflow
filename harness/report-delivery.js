import { readFile, realpath, lstat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve, relative, isAbsolute, extname } from 'node:path';
import { homedir } from 'node:os';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');

/** Use the host's existing present-file event in the owning chat, after tool success. */
export function reportDelivery(ctx, directory = resolve(homedir(), '.ai-programming-workflow')) {
  const pending = new WeakMap();
  ctx.on('tools/result', (exec, result) => {
    const delivery = pending.get(exec);
    pending.delete(exec);
    if (!delivery || result.isError) return;
    delivery.session.append('deliverables/presented', {
      turn: delivery.turn, callId: exec.callId, files: delivery.files,
    });
  });
  return async (value, exec) => {
    if (!value?.report_files?.length) return value;
    try {
      const task = await realpath(resolve(directory, 'tasks', sha(exec.agent.id)));
      const session = exec.agent.session;
      const boundary = ctx.agents.turnBoundaryOf(session);
      if (boundary.openTurnStartSeq === null) throw new Error('当前没有可接收报告的聊天轮次');
      const files = [];
      for (const report of value.report_files) {
        const path = await realpath(report.path);
        const rel = relative(task, path);
        if (!rel || rel.split(/[\\/]/)[0] === '..' || isAbsolute(rel) || extname(path) !== '.md'
            || report.mime_type !== 'text/markdown' || (await lstat(report.path)).isSymbolicLink())
          throw new Error('报告文件不在本任务内或不是 Markdown 文件');
        if (sha(await readFile(path)) !== report.sha256) throw new Error('报告文件已改变，不能交付旧摘要');
        const target = await ctx.fs.resolve(path, { cwd: task, signal: exec.signal });
        const entry = await ctx.fs.stat(target, exec.signal);
        if (entry?.type !== 'file') throw new Error('聊天文件服务无法读取该报告');
        files.push({ path: ctx.fs.processPath(target), description: report.title });
      }
      exec.signal?.throwIfAborted();
      pending.set(exec, { session, turn: boundary.lastTurn, files });
      return { ...value, report_delivery: { status: 'READY', files,
        instruction: '本工具成功后将报告登记到当前聊天，文件卡在本轮结束时显示。L1现在应发送简要结论及Markdown文件链接，再请求最终人工审核。' } };
    } catch (error) {
      // Report generation/approval already completed; a display failure must not replay it.
      return { ...value, report_delivery: { status: 'FAILED', error: error.message,
        instruction: '报告已保存，但聊天文件交付失败。告知用户并提供report_files中的文件链接；不要重放审核或产品操作。' } };
    }
  };
}

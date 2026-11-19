import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { resolve as resolvePath } from 'node:path';

/** No shell, no credential arguments, bounded capture; cancellation is UNKNOWN. */
export function pythonCall(executable, script, request, { key, signal, spawnImpl = spawn } = {}) {
  return new Promise((resolve, reject) => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) =>
      !['OPENAI_API_KEY', 'AI_WORKFLOW_OPENAI_API_KEY'].includes(k.toUpperCase())));
    env.PYTHONIOENCODING = 'utf-8';
    env.PYTHONDONTWRITEBYTECODE = '1';
    if (key) env.OPENAI_API_KEY = key;
    const child = spawnImpl(executable, [script], { env, shell: false, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'] });
    const tracked = request.action === 'bridge' && !['dot_status', 'dot_read_record',
      'l1_prepare_recovery', 'l1_stop', 'l1_submit_report'].includes(request.name) && Number.isInteger(child.pid);
    const processPath = tracked ? resolvePath(request.task, 'host-process.json') : null;
    const record = { schema_version: 1, pid: child.pid, boot_id: null, started_at: new Date().toISOString(),
      operation: request.name, status: 'RUNNING', process_tree_status: 'UNKNOWN' };
    const saveProcess = value => {
      const temporary = `${processPath}.${child.pid}.tmp`;
      writeFileSync(temporary, JSON.stringify(value), { mode: 0o600 });
      renameSync(temporary, processPath);
    };
    if (tracked) {
      try { saveProcess(record); }
      catch { child.kill(); reject(new Error('Could not preserve process identity; operation was not sent')); return; }
    }
    let stdout = '', size = 0, settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener('abort', abort);
      clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const terminate = reason => { child.kill(); finish(new Error(reason)); };
    const abort = () => terminate('UNKNOWN: caller interrupted; reconcile old processes before retry');
    const overflow = () => terminate('OUTPUT_LIMIT: runner reply exceeded 8000000 bytes; operation may already be recorded. Reconcile recorded completion; do not replay');
    const timer = setTimeout(() => terminate('UNKNOWN: runner exceeded host wait limit; reconcile old processes before retry'), 4 * 60 * 60 * 1000);
    child.on('error', () => finish(new Error('Python runner could not start; check the configured executable')));
    child.stdin.on('error', () => {});
    child.stdout.setEncoding('utf8');
    child.stderr.on('data', chunk => { size += chunk.length; if (size > 8_000_000) overflow(); });
    child.stdout.on('data', chunk => { size += Buffer.byteLength(chunk); if (size > 8_000_000) overflow(); else stdout += chunk; });
    child.on('close', code => {
      try {
        if (tracked && JSON.parse(readFileSync(processPath, 'utf8')).pid === child.pid)
          saveProcess({ ...record, status: 'EXITED', exit_code: code, ended_at: new Date().toISOString() });
        const value = JSON.parse(stdout);
        if (value.ok && code === 0) finish(null, value.result);
        else finish(new Error(key ? String(value.error).replaceAll(key, '[REDACTED]') : value.error || 'Runner failed'));
      } catch { finish(new Error('UNKNOWN: runner ended without a valid result; do not replay')); }
    });
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    else child.stdin.end(JSON.stringify(request));
  });
}

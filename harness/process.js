import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { readFileSync, writeFileSync, renameSync } from './file-access.js';
import { resolve as resolvePath } from 'node:path';

/** No shell, no credential arguments, bounded capture; cancellation is UNKNOWN. */
export function pythonCall(executable, script, request, { key, signal, hostRelay, spawnImpl = spawn } = {}) {
  return new Promise((resolve, reject) => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) =>
      !['OPENAI_API_KEY', 'AI_WORKFLOW_OPENAI_API_KEY'].includes(k.toUpperCase())));
    env.PYTHONIOENCODING = 'utf-8';
    env.PYTHONDONTWRITEBYTECODE = '1';
    delete env.REQPILOT_HOST_RELAY;
    if (hostRelay) env.REQPILOT_HOST_RELAY = '1';
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
    let stdout = '', size = 0, settled = false, finalReply, pending = Promise.resolve(), inHostCall = false;
    const hostAbort = new AbortController();
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener('abort', abort);
      clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const terminate = reason => {
      hostAbort.abort(); child.kill();
      // Drain native work before reporting cancellation; its side effects may need reconciliation.
      pending.finally(() => finish(new Error(reason)));
    };
    const abort = () => terminate('UNKNOWN: caller interrupted; reconcile old processes before retry');
    const overflow = () => terminate('OUTPUT_LIMIT: runner reply exceeded 8000000 bytes; operation may already be recorded. Reconcile recorded completion; do not replay');
    const timer = setTimeout(() => terminate('UNKNOWN: runner exceeded host wait limit; reconcile old processes before retry'), 4 * 60 * 60 * 1000);
    child.on('error', () => finish(new Error('Python runner could not start; check the configured executable')));
    child.stdin.on('error', () => {});
    child.stdout.setEncoding('utf8');
    child.stderr.on('data', chunk => { size += chunk.length; if (size > 8_000_000) overflow(); });
    child.stdout.on('data', chunk => {
      size += Buffer.byteLength(chunk);
      if (size > 8_000_000) { overflow(); return; }
      stdout += chunk;
      if (!hostRelay) return;
      let newline;
      while ((newline = stdout.indexOf('\n')) >= 0) {
        const line = stdout.slice(0, newline); stdout = stdout.slice(newline + 1); size = Buffer.byteLength(stdout);
        let frame;
        try { frame = JSON.parse(line); }
        catch { terminate('UNKNOWN: malformed runner frame; reconcile before retry'); return; }
        if (!frame.host_request) { finalReply = frame; continue; }
        if (!/^[a-f0-9]{32}$/.test(frame.host_request)) { terminate('UNKNOWN: invalid host request identity'); return; }
        if (inHostCall || finalReply) { terminate('UNKNOWN: overlapping host requests'); return; }
        inHostCall = true;
        pending = (async () => {
          const timeout = new AbortController();
          const abortHost = () => timeout.abort();
          hostAbort.signal.addEventListener('abort', abortHost, { once: true });
          if (hostAbort.signal.aborted) timeout.abort();
          const wait = setTimeout(abortHost, Math.max(1, frame.timeout_seconds || 1) * 1000);
          let result;
          const recordPath = request.task ? resolvePath(request.task, 'host-tool-calls', frame.host_request + '.json') : null;
          const saveHost = value => {
            if (!recordPath) return;
            mkdirSync(resolvePath(request.task, 'host-tool-calls'), { recursive: true });
            const text = JSON.stringify(value);
            writeFileSync(recordPath + '.tmp', key ? text.replaceAll(key, '[REDACTED]') : text, { mode: 0o600 });
            renameSync(recordPath + '.tmp', recordPath);
          };
          const hostRecord = { request: frame, session: request.session, started_at: new Date().toISOString(), status: 'RUNNING' };
          try {
            saveHost(hostRecord);
            result = await hostRelay(frame, timeout.signal);
            saveHost({ ...hostRecord, status: 'RETURNED', result, ended_at: new Date().toISOString() });
          }
          catch(error) { result = { status: 'UNKNOWN',
            ...(error.code?.startsWith('FILE_') ? {error_code:error.code} : {}),
            summary: error.code?.startsWith('FILE_')
              ? `${error.code}: host receipt commit failed; retain staged evidence and reconcile, do not replay the host call`
              : 'Host call did not return a confirmed result; reconcile before retry' }; }
          finally { clearTimeout(wait); hostAbort.signal.removeEventListener('abort', abortHost); }
          result = { ...result, host_receipt: `host-tool-calls/${frame.host_request}.json` };
          let reply = JSON.stringify({ host_response: frame.host_request, result });
          if (key) reply = reply.replaceAll(key, '[REDACTED]');
          if (Buffer.byteLength(reply) > 32_000_000)
            reply = JSON.stringify({ host_response: frame.host_request, result: {
              status: 'UNKNOWN', summary: 'Host result exceeds pipe capacity; inspect host evidence, do not replay' } });
          inHostCall = false;
          if (!hostAbort.signal.aborted) child.stdin.write(reply + '\n');
        })();
      }
    });
    child.on('close', async code => {
      if (inHostCall) hostAbort.abort();
      await pending;
      try {
        if (tracked && JSON.parse(readFileSync(processPath, 'utf8')).pid === child.pid)
          saveProcess({ ...record, status: 'EXITED', exit_code: code, ended_at: new Date().toISOString() });
        const value = hostRelay ? finalReply : JSON.parse(stdout);
        if (value.ok && code === 0) finish(null, value.result);
        else finish(Object.assign(new Error(key ? String(value.error).replaceAll(key, '[REDACTED]') : value.error || 'Runner failed'),
          value.error_code?.startsWith('FILE_') ? {code:value.error_code} : {}));
      } catch(error) { finish(error.code?.startsWith('FILE_') ? error : new Error('UNKNOWN: runner ended without a valid result; do not replay')); }
    });
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    else if (hostRelay) child.stdin.write(JSON.stringify(request) + '\n');
    else child.stdin.end(JSON.stringify(request));
  });
}

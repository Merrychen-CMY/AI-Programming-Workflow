import { spawn } from 'node:child_process';

/** No shell, no credential arguments, bounded capture; cancellation is UNKNOWN. */
export function pythonCall(executable, script, request, { key, signal, spawnImpl = spawn } = {}) {
  return new Promise((resolve, reject) => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) =>
      !['OPENAI_API_KEY', 'AI_WORKFLOW_OPENAI_API_KEY'].includes(k.toUpperCase())));
    env.PYTHONIOENCODING = 'utf-8';
    if (key) env.OPENAI_API_KEY = key;
    const child = spawnImpl(executable, [script], { env, shell: false, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', size = 0, settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener('abort', abort);
      clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const abort = () => { child.kill(); finish(new Error('UNKNOWN: caller interrupted; reconcile old processes before retry')); };
    const timer = setTimeout(abort, 4 * 60 * 60 * 1000);
    child.on('error', () => finish(new Error('Python runner could not start; check the configured executable')));
    child.stdin.on('error', () => {});
    child.stdout.setEncoding('utf8');
    child.stderr.on('data', chunk => { size += chunk.length; if (size > 8_000_000) abort(); });
    child.stdout.on('data', chunk => { size += Buffer.byteLength(chunk); stdout += chunk; if (size > 8_000_000) abort(); });
    child.on('close', code => {
      try {
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

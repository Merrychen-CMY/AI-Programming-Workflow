import { spawn } from 'node:child_process';
import { homedir } from 'node:os';

/** Reuse an existing local Git login; never prompt, log, persist, or return it to a model. */
export function githubCredential({ spawnImpl = spawn } = {}) {
  return new Promise(resolve => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !['OPENAI_API_KEY', 'AI_WORKFLOW_OPENAI_API_KEY', 'GIT_ASKPASS', 'SSH_ASKPASS'].includes(key.toUpperCase())));
    Object.assign(env, { GIT_TERMINAL_PROMPT: '0', GCM_INTERACTIVE: 'Never', GIT_ASKPASS: '', SSH_ASKPASS: '' });
    const child = spawnImpl('git', ['-c', 'credential.interactive=never', 'credential', 'fill'],
      { shell: false, windowsHide: true, cwd: homedir(), env, stdio: ['pipe', 'pipe', 'pipe'] });
    let output = '', settled = false;
    const finish = value => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
    const timer = setTimeout(() => { child.kill(); finish(null); }, 8000);
    child.on('error', () => finish(null)); child.stdin.on('error', () => {});
    child.stderr.on('data', () => {});
    child.stdout.on('data', chunk => {
      output += chunk.toString('utf8');
      if (output.length > 16_000) { child.kill(); finish(null); }
    });
    child.on('close', code => {
      const fields = Object.fromEntries(output.split(/\r?\n/).filter(line => line.includes('='))
        .map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));
      const value = fields.password;
      finish(code === 0 && fields.protocol === 'https' && fields.host === 'github.com'
        && typeof value === 'string' && value.length > 0 && value.length < 4096 && !/[\s\x00-\x1f]/.test(value) ? value : null);
    });
    child.stdin.end('protocol=https\nhost=github.com\npath=Merrychen-CMY/ReqPilot.git\n\n');
  });
}

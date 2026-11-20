/** Same-account file-view compatibility. Never decrypts, changes DLP policy, or skips hashes. */
import * as fs from 'node:fs/promises';
import * as syncFs from 'node:fs';
import { execFile, execFileSync } from 'node:child_process';
import { AsyncLocalStorage } from 'node:async_hooks';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';

// Installed bootstrap and immutable cached engines share invocation scope, not a global mode switch.
const slot = Symbol.for('reqpilot.file-access.v1');
const storage = globalThis[slot] ||= new AsyncLocalStorage();
const LIMIT = 32_000_000;
const sha = data => createHash('sha256').update(data).digest('hex');
const filename = path => path instanceof URL ? fileURLToPath(path) : String(path);
export const encrypted = data => Buffer.from(data).subarray(0, 14).toString() === '%ONEDLP_HEADER';
export function fileError(code, path, extra = {}) {
  return Object.assign(new Error(`${code}: ${path}`), { code, path: filename(path), ...extra });
}
export function accessReport() {
  const scope = storage.getStore();
  return { provider: scope?.active ? 'configured_python' : 'node',
    view_differences: scope?.differences || [],
    limits: 'Same-account permitted reads only; no decryption, security policy changes or consumer/toolchain attestation.' };
}
export const withFileAccess = (options, action) => storage.run({ python: 'python', active: false, differences: [], ...options }, action);

// Fixed, installation-owned standard-library program; no project script/import, shell or model code.
const PROGRAM = String.raw`
import sys,os,json,base64,time,errno,stat
def checked(path):
 p=os.path.abspath(path)
 while True:
  s=os.lstat(p)
  if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400: raise ValueError('FILE_PATH_UNSAFE')
  parent=os.path.dirname(p)
  if parent==p: break
  p=parent
def read(path):
 checked(path)
 with open(path,'rb') as f:
  before=os.fstat(f.fileno());data=f.read(32000001)
  f.seek(0);again=f.read(32000001);after=os.fstat(f.fileno())
  if data!=again or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino): raise ValueError('FILE_CONTENT_CHANGED')
 if len(data)>32000000: raise ValueError('FILE_SIZE_LIMIT')
 if data.startswith(b'%ONEDLP_HEADER%'): raise ValueError('FILE_ENCRYPTED_VIEW')
 return data
def move(a,b):
 for delay in (.05,.1,.2,.4,.8,None):
  try: os.replace(a,b); return
  except OSError as e:
   retry=e.errno in (errno.EACCES,errno.EPERM,errno.EBUSY) or getattr(e,'winerror',None) in (5,32,33)
   if not retry: raise
   if delay is None: raise ValueError('FILE_REPLACE_BLOCKED')
   time.sleep(delay)
try:
 q=json.loads(sys.stdin.buffer.read(48000001)); op=q['operation']
 if op=='read':
  values=[]; size=0
  for p in q['paths']:
   data=read(p);size+=len(data)
   if size>32000000: raise ValueError('FILE_SIZE_LIMIT')
   values.append(base64.b64encode(data).decode('ascii'))
  result=values
 elif op=='write':
  p=q['path'];checked(os.path.dirname(os.path.abspath(p)))
  if os.path.lexists(p): checked(p)
  data=base64.b64decode(q['data'],validate=True)
  flags={'w':os.O_CREAT|os.O_TRUNC,'wx':os.O_CREAT|os.O_EXCL,'a':os.O_CREAT|os.O_APPEND,'ax':os.O_CREAT|os.O_EXCL|os.O_APPEND}
  descriptor=os.open(p,os.O_WRONLY|getattr(os,'O_BINARY',0)|flags[q['flag']],q.get('mode',0o600))
  with os.fdopen(descriptor,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
  if q['flag']!='a' and read(p)!=data: raise ValueError('FILE_WRITE_VERIFY_FAILED')
  result=None
 elif op=='rename':
  checked(q['source']);checked(os.path.dirname(os.path.abspath(q['target'])))
  if os.path.lexists(q['target']): checked(q['target'])
  move(q['source'],q['target']);result=None
 else: raise ValueError('FILE_OPERATION_INVALID')
 print(json.dumps({'ok':True,'result':result}))
except Exception as e:
 code=('ENOENT' if isinstance(e,FileNotFoundError) else 'EEXIST' if isinstance(e,FileExistsError) else 'FILE_ACCESS_DENIED' if isinstance(e,PermissionError) else str(e) if isinstance(e,ValueError) and str(e).startswith('FILE_') else 'FILE_IO_FAILED')
 print(json.dumps({'ok':False,'code':code}))
`;
function options(request) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/(?:API_KEY|TOKEN|SECRET|PASSWORD)$/i.test(name)));
  return { encoding: 'utf8', windowsHide: true, shell: false, timeout: 30_000,
    maxBuffer: 48_000_000, env: { ...env, PYTHONIOENCODING: 'utf-8' }, input: JSON.stringify(request) };
}
function decode(output, request) {
  let reply;
  try { reply = JSON.parse(output); } catch { throw fileError('FILE_PROVIDER_PROTOCOL', request.path || request.paths?.[0] || request.target); }
  if (!reply.ok) throw fileError(reply.code, request.path || request.paths?.[0] || request.target);
  return reply.result;
}
export async function pythonFiles(request) {
  const scope = storage.getStore();
  if (!scope?.python) throw fileError('FILE_PROVIDER_UNAVAILABLE', request.path || request.paths?.[0]);
  if (scope.bridge) return scope.bridge(request);
  return new Promise((accept, reject) => {
    const settings = options(request);
    const child = execFile(scope.python, ['-I', '-c', PROGRAM], settings, (error, stdout) => {
      if (error) { reject(fileError('FILE_PROVIDER_UNAVAILABLE', request.path || request.paths?.[0] || request.target)); return; }
      try { accept(decode(stdout, request)); } catch (error) { reject(error); }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(settings.input);
  });
}
function pythonSync(request) {
  const scope = storage.getStore();
  try { return decode(execFileSync(scope.python, ['-I', '-c', PROGRAM], options(request)), request); }
  catch (error) { if (error.code?.startsWith('FILE_') || ['ENOENT','EEXIST'].includes(error.code)) throw error;
    throw fileError('FILE_PROVIDER_UNAVAILABLE', request.path || request.paths?.[0] || request.target); }
}
const nativeRead = (path, option) => (storage.getStore()?.nativeRead || fs.readFile)(path, option);
function usePython(path, reason) {
  const scope = storage.getStore();
  scope.active = true;
  if (!scope.differences.some(item => item.path === filename(path))) scope.differences.push({ path: filename(path), reason });
}
export async function readFiles(paths, { alternate = false } = {}) {
  const scope = storage.getStore();
  if (scope && (scope.active || alternate)) {
    const values = await pythonFiles({ operation: 'read', paths: paths.map(filename) });
    return values.map(data => Buffer.from(data, 'base64'));
  }
  return Promise.all(paths.map(path => readFile(path)));
}
export async function readFile(path, option) {
  const scope = storage.getStore();
  let data;
  if (scope?.active) data = (await readFiles([path]))[0];
  else {
    try { data = await nativeRead(path); }
    catch(error) {
      if (!scope || !['EACCES','EPERM'].includes(error.code)) throw error;
      data = (await readFiles([path], {alternate:true}))[0];
      usePython(path, 'Native reader was denied; configured Python provided a permitted view');
    }
    if (encrypted(data)) {
      if (!scope) throw fileError('FILE_ENCRYPTED_VIEW', path);
      data = (await readFiles([path], { alternate: true }))[0];
      if (encrypted(data)) throw fileError('FILE_ENCRYPTED_VIEW', path);
      usePython(path, 'Native reader received an encrypted envelope; configured Python provided a readable view');
    }
  }
  const encoding = typeof option === 'string' ? option : option?.encoding;
  return encoding ? data.toString(encoding) : data;
}
export async function verifiedFiles(paths, expected) {
  let contents = await readFiles(paths);
  const matches = values => values.every((data, i) => sha(data) === expected[i]);
  if (!matches(contents) && storage.getStore() && !storage.getStore().active) {
    const alternate = await readFiles(paths, { alternate: true });
    if (!matches(alternate)) throw fileError('FILE_CONTENT_MISMATCH', paths[contents.findIndex((data,i) => sha(data) !== expected[i])]);
    usePython(paths[contents.findIndex((data,i) => sha(data) !== expected[i])], 'Only configured Python matched the recorded content hashes');
    contents = alternate;
  }
  if (!matches(contents)) throw fileError('FILE_CONTENT_MISMATCH', paths[contents.findIndex((data,i) => sha(data) !== expected[i])]);
  if (contents.reduce((size, data) => size + data.length, 0) > LIMIT) throw fileError('FILE_SIZE_LIMIT', paths[0]);
  return contents;
}
export async function writeFile(path, data, option = {}) {
  const scope = storage.getStore();
  const settings = typeof option === 'string' ? { encoding: option } : option;
  const buffer = Buffer.isBuffer(data) ? data : Buffer.from(data, settings.encoding || 'utf8');
  if (!scope?.active) {
    await fs.writeFile(path, data, option);
    if (!(settings.flag || 'w').startsWith('a') && !(await readFile(path)).equals(buffer))
      throw fileError('FILE_WRITE_VERIFY_FAILED', path);
    return;
  }
  return pythonFiles({ operation: 'write', path: filename(path), data: buffer.toString('base64'), flag: settings.flag || 'w', mode: settings.mode ?? 0o600 });
}
export async function rename(source, target) {
  if (storage.getStore()?.active) return pythonFiles({ operation: 'rename', source: filename(source), target: filename(target) });
  for (const delay of [50, 100, 200, 400, 800, null]) {
    try { return await fs.rename(source, target); }
    catch (error) { if (!['EACCES','EPERM','EBUSY'].includes(error.code)) throw error;
      if (delay === null) throw fileError('FILE_REPLACE_BLOCKED', target, { temporary: filename(source) });
      await new Promise(done => setTimeout(done, delay)); }
  }
}
export function readFileSync(path, option) {
  if (!storage.getStore()?.active) {
    const data = syncFs.readFileSync(path);
    if (!encrypted(data)) return typeof option === 'string' || option?.encoding ? data.toString(typeof option === 'string' ? option : option.encoding) : data;
    if (!storage.getStore()) throw fileError('FILE_ENCRYPTED_VIEW', path);
    const alternate = Buffer.from(pythonSync({operation:'read',paths:[filename(path)]})[0],'base64');
    if (encrypted(alternate)) throw fileError('FILE_ENCRYPTED_VIEW', path);
    usePython(path, 'Native synchronous reader received an encrypted envelope');
    return typeof option === 'string' || option?.encoding ? alternate.toString(typeof option === 'string' ? option : option.encoding) : alternate;
  }
  const data = Buffer.from(pythonSync({ operation: 'read', paths: [filename(path)] })[0], 'base64');
  return option ? data.toString(typeof option === 'string' ? option : option.encoding) : data;
}
export function writeFileSync(path, data, option = {}) {
  if (!storage.getStore()?.active) {
    syncFs.writeFileSync(path, data, option);
    if (!(option.flag || 'w').startsWith('a') && !readFileSync(path).equals(Buffer.from(data)))
      throw fileError('FILE_WRITE_VERIFY_FAILED',path);
    return;
  }
  return pythonSync({ operation: 'write', path: filename(path), data: Buffer.from(data).toString('base64'), flag: option.flag || 'w', mode: option.mode ?? 0o600 });
}
export function renameSync(source, target) {
  if (storage.getStore()?.active) return pythonSync({ operation: 'rename', source: filename(source), target: filename(target) });
  for (const delay of [50,100,200,400,800,null]) {
    try { return syncFs.renameSync(source,target); }
    catch(error) { if (!['EACCES','EPERM','EBUSY'].includes(error.code)) throw error;
      if(delay===null) throw fileError('FILE_REPLACE_BLOCKED',target,{temporary:filename(source)});
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,delay); }
  }
}
export async function configuredAccess(directory, action) {
  return withFileAccess({}, async () => {
    const scope = storage.getStore(), path = resolve(directory, 'settings.json');
    let settings;
    try { settings = JSON.parse(await readFile(path, 'utf8')); }
    catch(error) {
      if(error.code==='ENOENT') return action();
      if(!(error instanceof SyntaxError)) throw error;
      settings = JSON.parse((await readFiles([path],{alternate:true}))[0].toString('utf8'));
      usePython(path,'Settings readable through default Python only');
    }
    if(typeof settings.python==='string' && settings.python && !/[\0\r\n]/.test(settings.python)) scope.python=settings.python;
    return action();
  });
}
export async function probeFiles(directory) {
  const folder = await fs.mkdtemp(resolve(directory, '.file-probe-'));
  const results = [];
  try {
    for(const extension of ['.json','.md','.c','.h','.py','.js','.exe','.bat','.cmd']) {
      const a=resolve(folder,`input${extension}`), b=resolve(folder,`copy${extension}`), staging=resolve(folder,`replacement${extension}`);
      const data=Buffer.from(`ReqPilot inert file-view probe ${randomUUID()}\n`);
      try {
        await writeFile(a,data); const actual=await verifiedFiles([a],[sha(data)]);
        await writeFile(b,actual[0]); await writeFile(staging,data); await rename(staging,b);
        await verifiedFiles([b],[sha(data)]);
        results.push({extension,status:'PASS',read_copy_replace:true});
      } catch(error) { results.push({extension,status:'FAIL',code:error.code || 'FILE_IO_FAILED'}); }
    }
  } finally { await fs.rm(folder,{recursive:true,force:true}); }
  return {ready:results.every(x=>x.status==='PASS'),checks:results,...accessReport(),
    scope:'Inert scratch files only. No executable probe is run. Does not establish compiler/Codex access to product files.'};
}

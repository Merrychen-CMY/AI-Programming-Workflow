import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile as nativeRead,writeFile as nativeWrite,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {withFileAccess,readFile,writeFile,verifiedFiles,accessReport,probeFiles,rename} from '../file-access.js';
import {createBootstrap} from '../bootstrap.js';
const sha = data => createHash('sha256').update(data).digest('hex');
async function folder(t) {
  const root=await mkdtemp(resolve(tmpdir(),'reqpilot-files-'));
  t.after(()=>rm(root,{recursive:true,force:true}));return root;
}
const bridge = value => async request => request.paths.map(()=>Buffer.from(value).toString('base64'));
test('encrypted native view uses permitted reader and still requires exact recorded hash',async()=>{
  await withFileAccess({nativeRead:async()=>Buffer.from('%ONEDLP_HEADER%cipher'),bridge:bridge('plain')},async()=>{
    assert.equal((await verifiedFiles(['file.c'],[sha('plain')]))[0].toString(),'plain');
    assert.equal(accessReport().provider,'configured_python');
    await assert.rejects(verifiedFiles(['file.c'],[sha('modified')]),{code:'FILE_CONTENT_MISMATCH'});
  });
  assert.equal(accessReport().provider,'node');
});
test('unrecognized envelope is resolved only by matching all expected hashes',async()=>{
  await withFileAccess({nativeRead:async()=>Buffer.from('other vendor envelope'),bridge:bridge('plain')},async()=>{
    assert.equal((await verifiedFiles(['file.h'],[sha('plain')]))[0].toString(),'plain');
  });
  await withFileAccess({nativeRead:async()=>Buffer.from('different'),bridge:bridge('tampered')},async()=>{
    await assert.rejects(verifiedFiles(['file.h'],[sha('plain')]),{code:'FILE_CONTENT_MISMATCH'});
  });
});
test('both readers encrypted or denied stays blocked without content leakage',async()=>{
  await withFileAccess({nativeRead:async()=>Buffer.from('%ONEDLP_HEADER%secret'),bridge:bridge('%ONEDLP_HEADER%secret')},async()=>{
    await assert.rejects(readFile('file.c'),error=>error.code==='FILE_ENCRYPTED_VIEW'&&!error.message.includes('secret'));
  });
  await withFileAccess({python:'definitely-nonexistent-reqpilot-python',nativeRead:async()=>Buffer.from('%ONEDLP_HEADER%secret')},async()=>{
    await assert.rejects(readFile('file.c'),{code:'FILE_PROVIDER_UNAVAILABLE'});
  });
});
test('real Python provider reads writes replaces and preserves exclusive creation',async t=>{
  const root=await folder(t),path=resolve(root,'record.json'),temp=resolve(root,'staged.tmp');
  await withFileAccess({active:true,python:process.env.PYTHON || 'python'},async()=>{
    await writeFile(path,'original',{flag:'wx',mode:0o600});
    await assert.rejects(writeFile(path,'overwrite',{flag:'wx'}),{code:'EEXIST'});
    assert.equal(await readFile(path,'utf8'),'original');
    await writeFile(temp,'replacement',{flag:'wx'});await rename(temp,path);
    assert.equal(await readFile(path,'utf8'),'replacement');
    const probe=await probeFiles(root);assert.equal(probe.ready,true);assert.equal(probe.checks.length,9);
  });
  assert.equal(await nativeRead(path,'utf8'),'replacement');
  assert.deepEqual(await readdir(root),['record.json']);
});
test('native compatibility detects write-view differences without repeating writes',async t=>{
  const path=resolve(await folder(t),'file.c');let reads=0;
  await withFileAccess({nativeRead:async()=>{reads++;return Buffer.from('%ONEDLP_HEADER%cipher');},bridge:bridge('contents')},async()=>{
    await writeFile(path,'contents');assert.equal(reads,1);assert.equal(accessReport().provider,'configured_python');
  });
  assert.equal(await nativeRead(path,'utf8'),'contents');
});
test('diagnose remains available without runtime manifest or product/API calls',async t=>{
  const root=await folder(t);
  const bootstrap=createBootstrap({ctx:{},root,directory:root,fields:{},updates:{dispose(){},forTask(){throw Error('must not load');}}});
  t.after(()=>bootstrap.dispose());
  const result=await bootstrap.invoke('workflow_updates',{action:'diagnose'},{agent:{id:'fixture'}});
  assert.equal(result.status,'FILE_ENVIRONMENT_DIAGNOSIS');assert.equal(result.task_changed,false);
  assert.equal(result.file_environment.ready,true);
});

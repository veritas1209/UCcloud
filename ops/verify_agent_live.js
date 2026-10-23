/* Actual public portal/guest test. All files are confined to a fresh QA directory. */
const fs=require('node:fs');
const crypto=require('node:crypto');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const auth=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const base='https://comsv-03.tail5819db.ts.net';
const directory='uccloud-agent-qa-'+crypto.randomUUID();
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 let api,token,connectionId,page;
 try {
  const context=await browser.newContext({viewport:{width:1360,height:900}});
  await context.addCookies([{name:'school_cloud_session',value:auth.session,domain:'comsv-03.tail5819db.ts.net',path:'/',httpOnly:true,secure:true,sameSite:'Lax'}]);
  page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/terminal?vm='+auth.vm_id+'&ai=1');
  await page.locator('#vm-password').waitFor({state:'visible',timeout:60000});
  await page.locator('#vm-password').fill(process.env.UCCLOUD_TEST_VM_PASSWORD);
  await page.locator('#connect').click();
  await page.locator('#agent-dialog').waitFor({state:'visible',timeout:60000});
  await page.waitForFunction(() => !document.getElementById('agent-result').hidden || document.getElementById('agent-status').textContent.includes('사용 중인 연결'), null, {timeout:65000});
  if (await page.locator('#agent-result').isHidden()) {
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#agent-renew').click();
  }
  try { await page.locator('#agent-result').waitFor({state:'visible',timeout:65000}); }
  catch (_) { throw new Error('AI issuance: '+await page.locator('#agent-status').textContent()); }
  const prompt=await page.locator('#agent-prompt').inputValue();
  token=prompt.match(/임시 API 키: (uca_[A-Za-z0-9_-]{43})/)[1];
  const endpoint=prompt.match(/API 기본 주소: (https:\/\/\S+)/)[1];
  // This client knows only the generated prompt and HTTPS. No SSH or cookie.
  const {request}=require('playwright');api=await request.newContext({baseURL:endpoint+'/',extraHTTPHeaders:{Authorization:'Bearer '+token}});
  async function call(method,path,data,status=200){
   const r=await api.fetch(endpoint+path,{method,data,timeout:30000});
   assert.equal(r.status(),status,method+' '+path+' status '+r.status());
   return r.json();
  }
  const info=await call('GET','/context');assert.equal(info.server_id,auth.vm_id);assert.equal(info.vm_username,'student');assert.equal(info.sudo_available,true);
  assert.ok(!prompt.includes(process.env.UCCLOUD_TEST_VM_PASSWORD),'VM password must never enter copied instructions');
  const grants=await context.request.get(base+'/api/instances/'+auth.vm_id+'/agent-connections');connectionId=(await grants.json()).connections[0].id;
  const {spawnSync}=require('node:child_process');
  const native=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',fs.readFileSync('ops/verify_agent_powershell.ps1','utf8')],{input:JSON.stringify({endpoint,key:token,vm_id:auth.vm_id}),encoding:'utf8',timeout:40000});
  assert.equal(native.status,0,'Built-in PowerShell API access: '+(native.stderr||'').replaceAll(token,'[redacted]').slice(0,2000));
  console.log(native.stdout.trim());
  await page.locator('#agent-close').click();await page.close();page=null;
  // Closing the browser SSH must not end the agent capability.
  assert.equal((await call('GET','/context')).server_id,auth.vm_id);
  async function write(name,text){return call('POST','/files',{operation:'write',path:directory+'/'+name,data_base64:Buffer.from(text).toString('base64'),truncate:true,parents:true});}
  async function job(command,timeout=30,requestId=crypto.randomUUID(),sudo=false){
   const body={command,cwd:directory,timeout_seconds:timeout,request_id:requestId,sudo};
   const item=await call('POST','/jobs',body,202);
   for(let n=0;n<60;n++){
    await new Promise(r=>setTimeout(r,2000));const result=await call('GET','/jobs/'+item.id);
    if(!['queued','running'].includes(result.state))return result;
   }
   throw new Error('Job did not finish');
  }
  await write('app.py','print("연결 성공 v1")\n');
  let result=await job('python3 app.py');assert.equal(result.exit_code,0);assert.match(result.stdout,/연결 성공 v1/);
  await write('app.py','print("수정 성공 v2")\n');
  result=await job('python3 app.py');assert.equal(result.exit_code,0);assert.match(result.stdout,/수정 성공 v2/);
  result=await job('id -u; apt-get --version | head -n 1',30,crypto.randomUUID(),true);assert.equal(result.exit_code,0);assert.match(result.stdout,/^0\napt /);
  result=await job('python3 -c "import pathlib,os; p=pathlib.Path(\'sudo-proof.txt\'); p.write_text(str(os.getuid())); p.chmod(0o644)"',30,crypto.randomUUID(),true);assert.equal(result.exit_code,0);
  const rootFile=await call('POST','/files',{operation:'read',path:directory+'/sudo-proof.txt'});assert.equal(Buffer.from(rootFile.data_base64,'base64').toString(),'0');
  const read=await call('POST','/files',{operation:'read',path:directory+'/app.py'});assert.match(Buffer.from(read.data_base64,'base64').toString(),/수정 성공 v2/);
  const listing=await call('POST','/files',{operation:'list',path:directory});assert.ok(listing.entries.some(x=>x.name==='app.py'));
  const body={command:'printf once >> once.txt',cwd:directory,timeout_seconds:10,request_id:crypto.randomUUID()};
  const first=await call('POST','/jobs',body,202);const second=await call('POST','/jobs',body,202);assert.equal(first.id,second.id);
  await new Promise(r=>setTimeout(r,3000));
  const once=await call('POST','/files',{operation:'read',path:directory+'/once.txt'});assert.equal(Buffer.from(once.data_base64,'base64').toString(),'once');
  await call('POST','/jobs',{...body,command:'different'},409);
  result=await job('printf error >&2; exit 7');assert.equal(result.state,'failed');assert.equal(result.exit_code,7);assert.match(result.stderr,/error/);
  result=await job('echo $$ > timeout.pid; sleep 90',1);assert.equal(result.state,'timed_out');
  const cancelled=await call('POST','/jobs',{command:'echo $$ > cancel.pid; sleep 90',cwd:directory,timeout_seconds:120,request_id:crypto.randomUUID()},202);
  await new Promise(r=>setTimeout(r,2000));await call('POST','/jobs/'+cancelled.id+'/cancel');
  let cancelResult;
  for(let n=0;n<15;n++){
   await new Promise(r=>setTimeout(r,2000));cancelResult=await call('GET','/jobs/'+cancelled.id);
   if(!['queued','running'].includes(cancelResult.state))break;
  }
  assert.equal(cancelResult.state,'cancelled');
  result=await job('python3 -c "import pathlib; assert all(not pathlib.Path(\'/proc/\'+p.read_text().strip()).exists() for p in pathlib.Path.cwd().glob(\'*.pid\')); print(\'processes stopped\')"');
  assert.equal(result.exit_code,0,'Timed out/cancelled processes must really stop');
  const privileged=await call('POST','/jobs',{command:'python3 -c "import os,pathlib,time; p=pathlib.Path(\'sudo.pid\'); p.write_text(str(os.getpid())); p.chmod(0o644); time.sleep(90)"',sudo:true,cwd:directory,timeout_seconds:120,request_id:crypto.randomUUID()},202);
  await new Promise(r=>setTimeout(r,2000));await call('POST','/jobs/'+privileged.id+'/cancel');
  for(let n=0;n<15;n++){
   await new Promise(r=>setTimeout(r,2000));cancelResult=await call('GET','/jobs/'+privileged.id);
   if(!['queued','running'].includes(cancelResult.state))break;
  }
  assert.equal(cancelResult.state,'cancelled','API sudo cancellation: '+(cancelResult.stderr||'').slice(0,1000));
  result=await job('python3 -c "import pathlib; assert not pathlib.Path(\'/proc/\'+pathlib.Path(\'sudo.pid\').read_text().strip()).exists(); print(\'sudo child stopped\')"');
  assert.equal(result.exit_code,0,'Privileged child must really stop');
  result=await job('python3 -c "import os,pathlib,time; p=pathlib.Path(\'sudo-timeout.pid\'); p.write_text(str(os.getpid())); p.chmod(0o644); time.sleep(90)"',1,crypto.randomUUID(),true);assert.equal(result.state,'timed_out');
  result=await job('python3 -c "import pathlib; assert not pathlib.Path(\'/proc/\'+pathlib.Path(\'sudo-timeout.pid\').read_text().strip()).exists()"');assert.equal(result.exit_code,0);
  await call('POST','/files',{operation:'write',path:'/etc/uccloud-agent-qa','data_base64':'YQ=='},409);
  await call('POST','/jobs',{...body,server_id:crypto.randomUUID()},422);
  result=await job('python3 -c "import sys; sys.stdout.write(\'x\'*400000)"');assert.equal(result.output_truncated,true);assert.ok(Buffer.byteLength(result.stdout)<=256*1024);
  const homeListing=await call('POST','/files',{operation:'list',path:'.'});
  for(const entry of homeListing.entries){
   if(entry.type!=='directory' || entry.name===directory || !/^uccloud-agent-qa-[a-f0-9-]{36}$/.test(entry.name))continue;
   const content=await call('POST','/files',{operation:'read',path:entry.name+'/app.py'});
   const onceContent=await call('POST','/files',{operation:'read',path:entry.name+'/once.txt'});
   assert.equal(Buffer.from(content.data_base64,'base64').toString(),'print("수정 성공 v2")\n','Only this task\'s earlier failed QA may be cleaned');
   assert.equal(Buffer.from(onceContent.data_base64,'base64').toString(),'once');
   result=await job('python3 -c "import pathlib,shutil; h=pathlib.Path.home(); p=h/\''+entry.name+'\'; assert p.is_dir() and not p.is_symlink() and p.resolve().is_relative_to(h); shutil.rmtree(p)"');assert.equal(result.exit_code,0);
  }
  // Delete only our exact fresh directory from within itself, using Python.
  result=await job('python3 -c "import pathlib,shutil; p=pathlib.Path.cwd(); assert p.name==\''+directory+'\'; shutil.rmtree(p)"');assert.equal(result.exit_code,0);
  assert.deepEqual(errors,[]);
  await call('POST','/disconnect');
  const denied=await api.get(endpoint+'/context');assert.equal(denied.status(),401);
  console.log('Real generated prompt only: HTTPS/files/run, sudo root execution, root file creation, privileged timeout/cancel process exit, duplicate safety, path/scope denial, output cap, browser-independent access and revoke passed.');
 } finally {
  if(connectionId){const {request}=require('playwright');const cleanup=await request.newContext();try{await cleanup.delete(base+'/api/instances/'+auth.vm_id+'/agent-connections/'+connectionId,{headers:{Cookie:'school_cloud_session='+auth.session,'X-CSRF-Token':auth.csrf_token}});}finally{await cleanup.dispose();}}
  await api?.dispose();await browser.close();
 }
})().catch(e=>{console.error(e.name+': '+e.message);process.exitCode=1;});

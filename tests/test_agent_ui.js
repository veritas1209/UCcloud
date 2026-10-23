const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const base='https://comsv-03.tail5819db.ts.net';
const id='b2878008-d330-40dc-86f4-c5d36862a8e9';
const fakeKey='uca_'+'A'.repeat(43);
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  for(const width of [1360,390]) {
   const page=await browser.newPage({viewport:{width,height:900}});const errors=[];let revoked=false;
   page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(({id,key})=>{
    class Socket {
     static OPEN=1;
     constructor(){this.readyState=1;setTimeout(()=>this.onopen?.(),10);}
     send(raw){const v=JSON.parse(raw);if(v.username)setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'ready'})}),10);
      if(v.type==='agent.issue')setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'agent',request_id:v.request_id,state:'ready',key,id:'connection-one',server_id:id,vm_username:'student',expires_at:Math.floor(Date.now()/1000)+14400})}),10);
      if(v.type==='publish.status')setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'publish',request_id:v.request_id,state:'private'})}),10);
     }
     close(){this.readyState=3;this.onclose?.({code:1000});}
    }
    window.WebSocket=Socket;
   },{id,key:fakeKey});
   await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/terminal') return route.fulfill({path:path.resolve('static/terminal.html')});
    if(url.pathname.startsWith('/assets/')) return route.fulfill({path:path.resolve('static',url.pathname.slice(8))});
    if(url.pathname==='/api/me') return route.fulfill({json:{csrf_token:'csrf'}});
    if(url.pathname==='/api/instances') return route.fulfill({json:{instances:[{id,name:'vm-1',vm_username:'student',status:'ACTIVE',addresses:[{address:'10.30.25.186',version:4}]}]}});
    if(url.pathname.endsWith('/agent-connections'))return route.fulfill({json:{enabled:true,connections:[]}});
    if(url.pathname.includes('/agent-connections/')){revoked=true;return route.fulfill({json:{ok:true}});}
    if(url.pathname==='/api/agent/v1/instructions')return route.fulfill({contentType:'text/plain',body:'GET /context; POST /jobs; POST /files. Test-only mock token.'});
    return route.fulfill({json:{}});
   });
   await page.goto(base+'/terminal?vm='+id+'&ai=1');
   await page.locator('#vm-password').fill('test-only-password');await page.locator('#connect').click();
   await page.locator('#agent-result').waitFor({state:'visible'});
   assert.match(await page.locator('#agent-prompt').inputValue(),/POST \/files/);
   assert.equal(await page.locator('#agent-hours').inputValue(),'4');
   assert.equal(await page.evaluate(()=>localStorage.length),0);
   assert.ok(!(await page.locator('#agent-dialog').innerHTML()).includes(fakeKey));
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.equal(await page.locator('#agent-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
   // Screenshots deliberately contain a fake token, never a usable capability.
   await page.locator('#agent-dialog').screenshot({path:'agent-connect-'+width+'.png'});
   await page.locator('#agent-close').click();
   assert.equal(await page.locator('#agent-prompt').inputValue(),'');
   await page.locator('#agent-connect').click();await page.locator('#agent-result').waitFor({state:'visible'});
   page.on('dialog',d=>d.accept());await page.locator('#agent-revoke').click();
   await page.waitForFunction(()=>document.getElementById('agent-status').textContent.includes('해제했습니다'));
   assert.equal(revoked,true);assert.equal(await page.locator('#agent-prompt').inputValue(),'');
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log('PC/mobile AI dialog, one-click issue, expiry display, secret cleanup, revoke and no overflow passed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve('static');
const server = http.createServer((req,res) => {
  const name = new URL(req.url,'http://localhost').pathname;
  if(name.startsWith('/api/')) {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(name==='/api/me'?{csrf_token:'test'}:{instances:[{id:'11111111-1111-4111-8111-111111111111',name:'내 서버',status:'ACTIVE',vm_username:'student',addresses:[{address:'10.30.23.10'}]}]}));return;}
  const file = path.resolve(root,name.startsWith('/assets/')?name.slice(8):'terminal.html');
  if(!file.startsWith(root+path.sep)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');
  try{res.end(fs.readFileSync(file));}catch{res.writeHead(404);res.end();}
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:1100}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{
      window.publishTest={requests:[],public:false};
      class Socket {
        static OPEN=1;static CLOSED=3;
        constructor(){this.readyState=1;setTimeout(()=>this.onopen?.(),0);}
        send(raw){const m=JSON.parse(raw);const answer=x=>setTimeout(()=>this.onmessage?.({data:JSON.stringify(x)}),1);
          if(m.username){answer({type:'ready'});return;}
          if(!m.type.startsWith('publish.'))return;
          window.publishTest.requests.push(m);
          if(m.type==='publish.start'){
            answer({type:'publish',request_id:m.request_id,state:'working',message:'HTTPS 주소를 설정하고 있습니다.'});
            setTimeout(()=>{
              window.publishTest.public=true;
              answer({type:'publish',request_id:m.request_id,state:'public',port:m.port,message:'공개 설정 완료',url:'https://uccloud-test.tail2d25b3.ts.net'});
            },1300);return;
          }
          if(m.type==='publish.stop')window.publishTest.public=false;
          answer({type:'publish',request_id:m.request_id,state:window.publishTest.public?'public':'private',message:'공개 상태 확인 완료',url:'https://uccloud-test.tail2d25b3.ts.net',port:8080});
        }
        close(){this.readyState=3;this.onclose?.({code:1000});}
      }
      window.WebSocket=Socket;
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/terminal?vm=11111111-1111-4111-8111-111111111111`);
    await page.locator('#vm-password').fill('test-password');await page.locator('#connect').click();
    await page.waitForFunction(()=>!document.getElementById('publish-start').disabled);
    await page.locator('#publish-port').fill('8080');await page.locator('#publish-start').click();
    await page.waitForFunction(()=>document.getElementById('publish-elapsed').textContent.includes('1초'));
    assert.equal(await page.locator('#publish-start').isDisabled(),true);
    await page.locator('#publish-result').waitFor({state:'visible'});
    assert.equal(await page.locator('#publish-address').inputValue(),'https://uccloud-test.tail2d25b3.ts.net');
    assert.equal(await page.locator('#publish-link').getAttribute('rel'),'noopener noreferrer');
    await page.screenshot({path:'publish-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'publish-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    page.once('dialog',dialog=>dialog.accept());await page.locator('#publish-stop').click();
    await page.locator('#publish-result').waitFor({state:'hidden'});
    await page.evaluate(()=>{
      window.uccloudPublish.connected(m=>window.uccloudPublish.receive({type:'publish',request_id:m.request_id,state:'public',message:'bad',url:'javascript:alert(1)'}));
    });
    assert.equal(await page.locator('#publish-result').isVisible(),false);
    assert.match(await page.locator('#publish-message').textContent(),/확인하지 못/);
    assert.deepEqual(errors,[]);
    console.log('Public URL, elapsed time, buttons, stop, unsafe link rejection and mobile layout passed');
  } finally {await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});

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
      window.uploadTest={received:[],complete:[]};
      class Socket {
        static OPEN=1;static CLOSED=3;
        constructor(){this.readyState=1;setTimeout(()=>this.onopen?.(),0);}
        send(raw){const m=JSON.parse(raw);const answer=x=>setTimeout(()=>this.onmessage?.({data:JSON.stringify(x)}),1);
          if(m.username){answer({type:'ready'});return;}
          if(!m.type.startsWith('upload.'))return;
          let result={state:'progress',received:0};
          if(m.type==='upload.begin'){
            window.uploadTest.name=m.name;window.uploadTest.received=[];
            result=m.name==='existing.txt'?{state:'error',message:'같은 이름의 파일이 있습니다.'}:{state:'started'};
          }
          if(m.type==='upload.chunk'){window.uploadTest.received.push(...Uint8Array.from(atob(m.data),c=>c.charCodeAt(0)));result.received=window.uploadTest.received.length;}
          if(m.type==='upload.finish'){window.uploadTest.complete.push(window.uploadTest.name);result={state:'complete',path:'~/uploads/'+window.uploadTest.name};}
          if(m.type==='upload.cancel')result={state:'cancelled'};
          answer({type:'upload',request_id:m.request_id,...result});
        }
        close(){this.readyState=3;this.onclose?.({code:1000});}
      }
      window.WebSocket=Socket;
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/terminal?vm=11111111-1111-4111-8111-111111111111`);
    await page.locator('#vm-password').fill('test-password');await page.locator('#connect').click();
    await page.locator('#file-panel').waitFor({state:'visible'});
    await page.evaluate(()=>{
      const bytes=Uint8Array.from({length:70000},(_,i)=>i%256);
      const data=new DataTransfer();data.items.add(new File([bytes],'<img onerror=alert(1)>.bin'));
      document.getElementById('file-drop').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));
    });
    await page.waitForFunction(()=>document.getElementById('file-feedback').textContent.includes('~/uploads/'));
    assert.equal(await page.evaluate(()=>window.uploadTest.received.length),70000);
    assert.equal(await page.evaluate(()=>window.uploadTest.received.every((v,i)=>v===i%256)),true);
    assert.equal(await page.locator('#file-feedback img').count(),0);
    await page.locator('#file-picker').setInputFiles({name:'existing.txt',mimeType:'text/plain',buffer:Buffer.from('x')});
    await page.waitForFunction(()=>document.getElementById('file-feedback').textContent.includes('같은 이름'));
    await page.screenshot({path:'files-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'files-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log('Drag/drop bytes, progress, duplicate feedback, XSS and mobile layout passed');
  } finally {await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});

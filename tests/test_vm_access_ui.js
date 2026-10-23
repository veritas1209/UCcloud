/* All API/SSH traffic is mocked. No live VM or credential is changed. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = 'https://comsv-03.tail5819db.ts.net';
const id = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const instance = {id,name:'완성된 서버',vm_username:'student',status:'ACTIVE',
  created_at:'2026-09-01T00:00:00Z',flavor:'small',addresses:[{address:'192.0.2.10',version:4}],
  software:[{id:'nginx',version:'1.30'}]};
const me = {username:'student',status:'active',role:'member',team_owner:false,
  team_name:'Test',team_slug:'test',csrf_token:'test-csrf'};

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    for (const width of [1360,390]) {
      const page = await browser.newPage({viewport:{width,height:900}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        class Socket {
          static OPEN = 1;
          constructor() { this.readyState=1; window.__ssh=this; setTimeout(()=>this.onopen?.(),10); }
          send(raw) {
            const value=JSON.parse(raw);
            if (value.username) setTimeout(()=> {
              this.onmessage?.({data:JSON.stringify({type:'ready'})});
              this.onmessage?.({data:JSON.stringify({type:'software',state:'ready',message:'설치가 끝났습니다.',started_at:1780000000,updated_at:1780000125})});
            },10);
            if (value.type==='agent.issue') setTimeout(()=>this.onmessage?.({data:JSON.stringify({type:'agent',request_id:value.request_id,state:'ready',key:'uca_'+'A'.repeat(43),id:'fake-connection',server_id:'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205',vm_username:'student',expires_at:Math.floor(Date.now()/1000)+14400})}),10);
          }
          close() { this.readyState=3;this.onclose?.({code:1000}); }
        }
        window.WebSocket=Socket;
      });
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/') return route.fulfill({path:path.resolve('static/index.html')});
        if (url.pathname === '/terminal') return route.fulfill({path:path.resolve('static/terminal.html')});
        if (url.pathname.startsWith('/assets/')) return route.fulfill({path:path.resolve('static',url.pathname.slice(8))});
        if (url.pathname === '/api/me') return route.fulfill({json:me});
        if (url.pathname === '/api/public-config') return route.fulfill({json:{operator_enabled:false}});
        if (url.pathname === '/api/instances') return route.fulfill({json:{instances:[instance]}});
        if (url.pathname === '/api/catalog') return route.fulfill({json:{images:[{id:'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205',name:'ubuntu-24.04'}],flavors:[{id:'small',name:'small',vcpus:1,ram:2048,disk:20}],software:[{id:'node',name:'Node.js',versions:['24'],group:'개발 환경'},{id:'react',name:'React',versions:['19'],port:3000,group:'팀 개발 도구'},{id:'postgres',name:'PostgreSQL',versions:['18'],port:5432,credentials:true,group:'DB'},{id:'redis',name:'Redis',versions:['8.2','7.4'],port:6379,credentials:true,group:'캐시'}]}});
        if (url.pathname === '/api/quota') return route.fulfill({json:{instances:{in_use:1,limit:4},cores:{in_use:1,limit:8},ram:{in_use:2048,limit:16384}}});
        if (url.pathname.endsWith('/agent-connections')) return route.fulfill({json:{enabled:true,connections:[]}});
        if (url.pathname === '/api/agent/v1/instructions') return route.fulfill({body:'GET /context; POST /jobs',contentType:'text/plain'});
        return route.fulfill({json:{enabled:false,backups:[]}});
      });
      await page.goto(base);
      await page.locator('.nav-item[data-section=instances]').click();
      const link = page.locator('.instance-actions a[data-agent]');
      await link.waitFor();
      assert.match(await link.getAttribute('href'), /&ai=1/);
      assert.equal(await link.evaluate(el=>getComputedStyle(el).textDecorationLine),'none');
      assert.ok((await link.boundingBox()).height >= 36);
      await page.locator('.vm-technical summary').click();
      assert.match(await page.locator('.vm-technical').textContent(), /접속 IDstudent/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:'vm-actions-fixed-'+width+'.png',fullPage:true});
      const target = await link.getAttribute('href');
      await page.locator('#open-create').click();
      await page.locator('#software-select').selectOption('preset:web');
      assert.equal(await page.locator('.software-row').count(),3);
      assert.equal(await page.locator('[data-software-username=postgres]').inputValue(),'student');
      assert.equal(await page.locator('[data-software-password=postgres]').getAttribute('required'),'');
      await page.screenshot({path:'vm-presets-'+width+'.png',fullPage:true});
      await page.locator('[data-close]').click();
      await page.goto(base+target);
      await page.locator('#connect-form:not(.hidden)').waitFor();
      assert.equal(await page.locator('#software-time').textContent(),'');
      assert.match(await page.locator('#software-message').textContent(), /접속/);
      await page.waitForTimeout(1100);
      assert.equal(await page.locator('#software-time').textContent(),'');
      await page.locator('#vm-password').fill('mock-password');
      await page.locator('#connect').click();
      await page.locator('#agent-result').waitFor({state:'visible'});
      assert.match(await page.locator('#agent-prompt').inputValue(), /GET \/context/);
      await page.locator('#agent-close').click();
      assert.equal(await page.locator('#paste').count(),0);
      assert.equal(await page.locator('#vnc').evaluate(el=>el.closest('.heading')===null),true);
      assert.equal(await page.locator('#vnc').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(181, 55, 50)');
      assert.equal(await page.locator('#vnc').evaluate(el=>Boolean(document.querySelector('#file-panel').compareDocumentPosition(el)&Node.DOCUMENT_POSITION_FOLLOWING)),true);
      await page.locator('#shortcuts').click();
      assert.equal(await page.locator('[data-shortcut=copy]').textContent(),'Ctrl + C');
      assert.equal(await page.locator('[data-shortcut=paste]').textContent(),'Ctrl + V');
      await page.locator('#shortcut-dialog .shortcut-bottom button[type=submit]').click();
      await page.locator('#software-add').click();
      await page.locator('#software-add-select').selectOption('redis');
      await page.locator('#software-add-list [data-field=password]').fill('mock-db-password');
      assert.equal(await page.locator('#software-add-start').isEnabled(),true);
      assert.equal(await page.locator('#software-add-dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
      await page.locator('#software-add-close').click();
      assert.equal(await page.locator('#software-add-list').textContent(),'');
      assert.equal(await page.locator('#software-time').textContent(),'총 2분 5초');
      await page.waitForTimeout(1100);
      assert.equal(await page.locator('#software-time').textContent(),'총 2분 5초');
      await page.screenshot({path:'vm-ready-fixed-'+width+'.png',fullPage:true});
      await page.evaluate(()=>window.__ssh.onmessage({data:JSON.stringify({type:'software',state:'failed',message:'설치 실패',started_at:1780000000,updated_at:1780000060})}));
      assert.equal(await page.locator('#software-time').textContent(),'경과 1분 0초');
      await page.waitForTimeout(1100);
      assert.equal(await page.locator('#software-time').textContent(),'경과 1분 0초');
      await page.evaluate(()=>window.__ssh.onmessage({data:JSON.stringify({type:'software',state:'unknown',message:'설치 상태를 확인하지 못했습니다.'})}));
      assert.equal(await page.locator('#software-time').textContent(),'');
      instance.software_status={state:'ready',message:'완료',started_at:1780000000,updated_at:1780000125};
      await page.goto(base+`/terminal?vm=${id}`);
      await page.locator('#connect-form:not(.hidden)').waitFor();
      assert.equal(await page.locator('#software-message').textContent(),'프로그램 설치 완료');
      assert.equal(await page.locator('#software-time').textContent(),'');
      delete instance.software_status;
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PC/mobile AI button and auto-open, VM ID, old VM no fake timer, ready/failed/unknown timing passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});

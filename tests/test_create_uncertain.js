/** A mocked 502 after Nova accepts a VM must recover the same creation, not submit twice. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const localStatic = process.env.UI_LOCAL_STATIC_DIR;
const id = '08ad1837-94e3-4c79-a43a-b50ec5189418';
const json = (route, value, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(value),
});

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const context = await browser.newContext();
    if (localStatic) {
      const files = {'/':'index.html', '/terminal':'terminal.html',
        '/assets/app.js':'app.js', '/assets/styles.css':'styles.css',
        '/assets/funnel-guide.css':'funnel-guide.css',
        '/assets/terminal.js':'terminal.js', '/assets/terminal.css':'terminal.css'};
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        const file = files[url.pathname];
        return url.origin === new URL(baseUrl).origin && file
          ? route.fulfill({path:path.join(localStatic, file)}) : route.continue();
      });
    }
    await context.addInitScript(() => {
      window.__sshFrames = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
        send(data) {
          const frame = JSON.parse(data);
          window.__sshFrames.push(frame);
          if (frame.password) setTimeout(() => this.onmessage?.({data:JSON.stringify({type:'ready'})}), 0);
        }
        close() { this.readyState = 3; }
      };
    });
    let requestId = null;
    let createPosts = 0;
    let listReads = 0;
    await context.route('**/api/public-config', route => json(route,
      {operator_enabled:false, registration_code_required:true}));
    await context.route('**/api/me', route => json(route, {
      username:'student', status:'active', role:'member', team_owner:false,
      team_name:'Class Lab', team_slug:'class-lab', csrf_token:'test-csrf',
    }));
    await context.route('**/api/quota', route => json(route, {
      instances:{in_use:0,limit:4}, cores:{in_use:0,limit:8}, ram:{in_use:0,limit:16384},
    }));
    await context.route('**/api/catalog', route => json(route, {
      images:[{id:'d58be315-054e-49fb-bb25-14a5544adf39',name:'Ubuntu 24.04'}],
      flavors:[{id:'small',name:'small',vcpus:1,ram:2048,disk:20}],
    }));
    await context.route('**/api/instances', route => {
      if (route.request().method() === 'POST') {
        const body = JSON.parse(route.request().postData());
        requestId = body.request_id;
        createPosts += 1;
        assert.match(requestId, /^[0-9a-f-]{36}$/);
        return json(route, {detail:'Cloud service is temporarily unavailable'}, 502);
      }
      listReads += 1;
      return json(route, {instances: requestId && listReads > 2 ? [{
        id, name:'vm-1', status:'ACTIVE', flavor:'small', vm_username:'student',
        request_id:requestId, addresses:[{address:'10.30.23.178',version:4}],
      }] : []});
    });
    const page = await context.newPage();
    await page.goto(baseUrl, {waitUntil:'domcontentloaded'});
    await page.locator('#open-create').click();
    await page.locator('#create-dialog[open]').waitFor();
    await page.locator('#create-form input[name=vm_password]').fill('fake-test-password');
    const popupPromise = page.waitForEvent('popup');
    await page.locator('#create-form button[type=submit]').click();
    const popup = await popupPromise;
    await page.getByText('VM 생성 결과를 확인하는 중입니다. 잠시 기다려 주세요.').waitFor();
    await popup.waitForURL(url => url.pathname === '/terminal', {timeout:15000});
    await popup.locator('#paste:not(.hidden)').waitFor({timeout:15000});
    const frames = await popup.evaluate(() => window.__sshFrames);
    assert.equal(frames[0].username, 'student');
    assert.equal(frames[0].password, 'fake-test-password');
    assert.equal(createPosts, 1);
    assert.equal(await page.locator('#create-dialog').evaluate(element => element.open), false);
    console.log('Uncertain VM creation recovered without a duplicate POST');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

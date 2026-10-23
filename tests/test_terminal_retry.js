/** Browser SSH keeps an unsent/rejected VM password only until successful login or page exit. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const vmId = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const json = (route, value) => route.fulfill({contentType:'application/json',body:JSON.stringify(value)});

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.__sockets = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() {
          this.readyState = 0;
          window.__sockets.push(this);
          setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0);
        }
        send(data) { window.__lastSent = JSON.parse(data); }
        close() { this.readyState = 3; this.onclose?.({code:1000}); }
        emit(message) { this.onmessage?.({data:JSON.stringify(message)}); }
      };
    });
    await page.route('**/api/me', route => json(route,{csrf_token:'retry-test'}));
    await page.route('**/api/instances', route => json(route,{instances:[{
      id:vmId,name:'test-vm',status:'ACTIVE',vm_username:'student',
      addresses:[{address:'10.30.23.177',version:4}],
    }]}));
    if (process.env.UI_USE_LIVE_STATIC !== '1') {
      await page.route('**/terminal?*', route => route.fulfill({
        contentType:'text/html',body:fs.readFileSync('static/terminal.html','utf8'),
      }));
      await page.route('**/assets/terminal.js*', route => route.fulfill({
        contentType:'application/javascript',body:fs.readFileSync('static/terminal.js','utf8'),
      }));
    }
    await page.goto(`${baseUrl}/terminal?vm=${vmId}`);
    await page.locator('#connect-form:not(.hidden)').waitFor();
    await page.locator('#vm-password').fill('example-vm-password');
    await page.locator('#connect').click();
    await page.waitForFunction(() => window.__lastSent?.password === 'example-vm-password');
    assert.equal(await page.locator('#vm-password').inputValue(),'example-vm-password');
    await page.evaluate(() => window.__sockets[0].emit({type:'error',message:'VM 접속 ID 또는 비밀번호가 올바르지 않습니다.'}));
    await page.locator('#connect:not([disabled])').waitFor();
    assert.equal(await page.locator('#vm-password').inputValue(),'example-vm-password');
    await page.locator('#vm-password').fill('corrected-vm-password');
    await page.locator('#connect').click();
    await page.waitForFunction(() => window.__sockets.length === 2 && window.__lastSent?.password === 'corrected-vm-password');
    await page.evaluate(() => window.__sockets[1].emit({type:'ready'}));
    await page.locator('#terminal:not(.hidden)').waitFor();
    assert.equal(await page.locator('#vm-password').inputValue(),'');
    await page.evaluate(() => { document.getElementById('vm-password').value='temporary'; window.dispatchEvent(new Event('pagehide')); });
    assert.equal(await page.locator('#vm-password').inputValue(),'');
    assert.deepEqual(errors,[]);
    console.log('Browser SSH retry, successful wipe, and page-exit wipe passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});

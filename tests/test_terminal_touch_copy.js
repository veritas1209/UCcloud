/** Read-only mobile clipboard regression; API and SSH transport are mocked. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const vmId = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const json = (route, value) => route.fulfill({contentType:'application/json', body:JSON.stringify(value)});

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    const page = await context.newPage();
    if (process.env.UI_LOCAL_STATIC_DIR) {
      const files = {'/terminal':'terminal.html','/assets/terminal.js':'terminal.js','/assets/terminal.css':'terminal.css'};
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        const file = files[url.pathname];
        return url.origin === new URL(baseUrl).origin && file
          ? route.fulfill({path:path.join(process.env.UI_LOCAL_STATIC_DIR,file)}) : route.continue();
      });
    }
    await page.addInitScript(() => {
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { window.__sshSocket=this; this.readyState = 0; setTimeout(() => {this.readyState=1; this.onopen?.();}, 0); }
        send(data) {
          if (!JSON.parse(data).password) return;
          setTimeout(() => {
            this.onmessage?.({data:JSON.stringify({type:'ready'})});
            this.onmessage?.({data:new TextEncoder().encode('touch-copy-test-output\r\n').buffer});
          }, 40);
        }
        close() { this.readyState=3; this.onclose?.({code:1000}); }
      };
    });
    await page.route('**/api/me', route => json(route,{csrf_token:'touch-test'}));
    await page.route('**/api/instances', route => json(route,{instances:[{id:vmId,name:'touch-test',status:'ACTIVE',vm_username:'student',addresses:[{address:'10.30.23.177',version:4}]}]}));
    await page.goto(`${baseUrl}/terminal?vm=${vmId}`,{waitUntil:'domcontentloaded'});
    await page.locator('#connect-form:not(.hidden)').waitFor();
    await page.locator('#vm-password').fill('test-password');
    await page.locator('#connect').click();
    await page.locator('#terminal:not(.hidden)').waitFor();
    await page.getByText('연결됐습니다. 이 창에서 바로 입력하세요.').waitFor();
    await page.locator('#read-text:not(.hidden)').waitFor();
    await page.getByText('글자를 복사하려면 화면 글자를 누르세요.').waitFor();
    const screen = await page.locator('.xterm-screen').boundingBox();
    const client = await context.newCDPSession(page);
    const start = {x:Math.round(screen.x+10),y:Math.round(screen.y+12)};
    const end = {x:Math.round(screen.x+160),y:start.y};
    await client.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[start]});
    await page.waitForTimeout(650);
    await client.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[end]});
    await page.waitForTimeout(120);
    await client.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await page.waitForTimeout(300);
    const nativeSelection = await page.evaluate(() => window.getSelection()?.toString() || '');
    await page.locator('#read-text').click();
    await page.locator('#terminal-text-dialog[open]').waitFor();
    assert.match(await page.locator('#terminal-text').textContent(), /touch-copy-test-output/);
    if (process.env.AXE_CORE_MODULE_PATH) {
      const axe = require(process.env.AXE_CORE_MODULE_PATH);
      await page.evaluate(axe.source);
      const violations = await page.evaluate(() => window.axe.run(document, {
        runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']},
      }).then(result => result.violations.map(item => item.id)));
      assert.deepEqual(violations, [], `touch copy dialog accessibility: ${violations.join(', ')}`);
    }
    if (process.env.TOUCH_SCREENSHOT_PATH) await page.screenshot({path:process.env.TOUCH_SCREENSHOT_PATH,fullPage:true});
    const textBox = await page.locator('#terminal-text').boundingBox();
    const textStart = {x:Math.round(textBox.x+18),y:Math.round(textBox.y+22)};
    await client.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[textStart]});
    await page.waitForTimeout(650);
    await client.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:textStart.x+120,y:textStart.y}]});
    await client.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await page.waitForTimeout(200);
    const dialogSelection = await page.evaluate(() => window.getSelection()?.toString() || '');
    await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:new URL(baseUrl).origin});
    await page.locator('#terminal-text-copy').click();
    await page.getByText('복사했습니다.').waitFor();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /touch-copy-test-output/);
    await page.locator('#terminal-text-close').click();
    await page.waitForFunction(() => document.getElementById('terminal-text').textContent === '');
    assert.equal(await page.locator('#terminal-text').textContent(), '');
    await page.locator('#read-text').click();
    await page.evaluate(() => window.__sshSocket.close());
    await page.locator('#terminal-text-dialog').waitFor({state:'hidden'});
    await page.locator('#read-text').waitFor({state:'hidden'});
    await page.waitForFunction(() => document.getElementById('terminal-text').textContent === '');
    assert.equal(await page.locator('#terminal-text').textContent(), '');
    console.log(JSON.stringify({nativeSelection,dialogSelection,fallbackCopied:true}));
    await context.close();
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});

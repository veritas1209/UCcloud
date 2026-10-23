/** A slow or failed cloud check must not block opening the VM form or erase typed input. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const json = (route, value, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
const quota = {instances:{in_use:1,limit:4},cores:{in_use:1,limit:8},ram:{in_use:2048,limit:16384}};

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:375,height:700}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    if (process.env.UI_USE_LIVE_STATIC !== '1') {
      const files = {'/':'index.html','/assets/styles.css':'styles.css',
        '/assets/funnel-guide.css':'funnel-guide.css','/assets/app.js':'app.js'};
      await page.context().route('**/*', route => {
        const url = new URL(route.request().url());
        const file = files[url.pathname];
        return url.origin === new URL(baseUrl).origin && file
          ? route.fulfill({path:path.resolve('static',file)}) : route.continue();
      });
    }
    await page.route('**/api/public-config', route => json(route,{operator_enabled:false,registration_code_required:true}));
    await page.route('**/api/me', route => json(route,{username:'student',status:'active',role:'member',team_owner:false,team_name:'Lab',team_slug:'lab',csrf_token:'create-test'}));
    await page.route('**/api/instances', route => json(route,{instances:[{id:'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205',name:'vm-1',status:'ACTIVE',flavor:'small',addresses:[]}]}));
    await page.route('**/api/quota', route => json(route,quota));
    await page.route('**/api/catalog', route => json(route,{images:[{id:'ubuntu',name:'Ubuntu 24.04'}],flavors:[{id:'small',name:'small',vcpus:1,ram:2048,disk:20}]}));
    await page.goto(baseUrl);
    await page.getByText('VM 1개 / 최대 4개').waitFor();

    let firstRoute;
    let firstCaptured;
    const firstRequested = new Promise(resolve => { firstCaptured = resolve; });
    let quotaReads = 0;
    await page.route('**/api/quota', route => {
      quotaReads += 1;
      if (quotaReads === 1) { firstRoute = route; firstCaptured(); return; }
      return quotaReads <= 3
        ? json(route,{detail:'Cloud service is temporarily unavailable'},503)
        : json(route,quota);
    });
    await page.locator('#open-create').click();
    await firstRequested;
    await page.locator('#create-dialog[open]').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('name')),'vm_password');
    assert.equal(await page.locator('#create-form button[type=submit]').isDisabled(),true);
    await page.locator('#create-form input[name=vm_password]').fill('example-vm-password');
    await firstRoute.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Cloud service is temporarily unavailable'})});
    await page.locator('#retry-create-setup:not(.hidden)').waitFor({timeout:15000});
    assert.equal(quotaReads,3);
    assert.equal(await page.locator('#create-form input[name=vm_password]').inputValue(),'example-vm-password');
    assert.match(await page.locator('#create-status').textContent(),/클라우드 연결에 문제가/);
    await page.locator('#retry-create-setup').click();
    await page.locator('#create-form button[type=submit]:not([disabled])').waitFor();
    assert.equal(quotaReads,4);
    assert.equal(await page.locator('#create-form input[name=vm_password]').inputValue(),'example-vm-password');
    assert.equal(await page.locator('#create-form input[name=name]').inputValue(),'vm-2');
    const width = await page.evaluate(() => [innerWidth,document.documentElement.scrollWidth]);
    assert.ok(width[1] <= width[0] + 1,`mobile overflow: ${width}`);
    await page.locator('#create-dialog [data-close]').click();
    await page.locator('#create-dialog').waitFor({state:'hidden'});
    await page.waitForFunction(() => document.querySelector('#create-form input[name=vm_password]').value === '');
    assert.equal(await page.locator('#create-form input[name=vm_password]').inputValue(),'');
    assert.deepEqual(errors,[]);
    console.log('Immediate VM form, cloud retry, input retention, and mobile width passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});

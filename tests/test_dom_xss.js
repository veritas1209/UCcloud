/** Read-only browser test: hostile API text must never become executable HTML. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const localStatic = process.env.UI_LOCAL_STATIC_DIR;
const payload = '\"><img src=x onerror="window.__uccloudInjected=1">';
const vmId = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const fulfill = (route, value, status=200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(value),
});

(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => { window.__uccloudInjected = 0; });
    if (localStatic) {
      const path = require('node:path');
      const files = {'/':'index.html','/assets/app.js':'app.js','/assets/styles.css':'styles.css',
        '/assets/funnel-guide.css':'funnel-guide.css'};
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        const file = files[url.pathname];
        return url.origin === new URL(baseUrl).origin && file
          ? route.fulfill({path:path.join(localStatic,file)}) : route.continue();
      });
    }
    await page.route('**/api/public-config', route => fulfill(route,
      {operator_enabled:false,registration_code_required:true}));
    await page.route('**/api/me', route => fulfill(route, {
      username:'student',status:'active',role:'member',team_owner:true,
      team_name:payload,team_slug:'test-team',csrf_token:'xss-csrf',
    }));
    await page.route('**/api/instances', route => fulfill(route, {instances:[{
      id:vmId,name:payload,status:'ACTIVE',flavor:payload,vm_username:'student',
      addresses:[{address:payload,version:4}],
    }]}));
    await page.route('**/api/quota', route => fulfill(route, {
      instances:{limit:4,in_use:1},cores:{limit:8,in_use:1},ram:{limit:16384,in_use:2048},
    }));
    await page.route('**/api/catalog', route => fulfill(route, {
      images:[{id:'d58be315-054e-49fb-bb25-14a5544adf39',name:payload}],
      flavors:[{id:'small',name:payload,vcpus:1,ram:2048,disk:20}],
    }));
    await page.route('**/api/team/requests', route => fulfill(route, {requests:[{
      id:42,username:payload,email:payload,created_at:payload,
    }]}));
    await page.goto(baseUrl, {waitUntil:'domcontentloaded'});
    await page.locator('.instance-card').waitFor();
    assert.equal(await page.locator('#team-name').textContent(), payload);
    assert.equal(await page.locator('.instance-card h3').textContent(), payload);
    assert.equal(await page.locator('.instance-card img').count(), 0);
    await page.locator('.instance-card .vm-technical summary').click();
    assert.ok((await page.locator('.instance-card .vm-technical').textContent()).includes(payload));
    await page.locator('#open-create').click();
    await page.locator('#create-dialog[open]').waitFor();
    assert.equal(await page.locator('#create-form select[name=image_id] option').textContent(), payload);
    assert.equal(await page.locator('#create-form select[name=flavor_id] option').textContent(),
      `${payload} · CPU 1 · 메모리 2GB`);
    await page.locator('#create-dialog [data-close]').click();
    await page.locator('.nav-item[data-section=team]').click();
    await page.locator('.approval-card').waitFor();
    assert.equal(await page.locator('.approval-card b').textContent(), payload);
    assert.equal(await page.locator('.approval-card img').count(), 0);
    assert.equal(await page.evaluate(() => window.__uccloudInjected), 0);
    assert.deepEqual(errors, []);
    console.log('Browser DOM XSS checks passed');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});

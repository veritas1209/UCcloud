/** Browser-only account password UX check; APIs are mocked and no account is changed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const axe = require(process.env.AXE_CORE_MODULE_PATH || 'axe-core');

const staticDir = path.resolve(__dirname, '..', 'static');
const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const useLiveStatic = process.env.UI_USE_LIVE_STATIC === '1';
const json = (route, value, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:375,height:812}});
    let requests = 0;
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (!useLiveStatic && url.pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(staticDir,'index.html'))});
      if (!useLiveStatic && url.pathname.startsWith('/assets/')) {
        const name = path.basename(url.pathname);
        const file = path.join(staticDir,name);
        if (fs.existsSync(file)) return route.fulfill({contentType:name.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file)});
      }
      if (url.pathname === '/api/public-config') return json(route,{operator_enabled:false,registration_code_required:true});
      if (url.pathname === '/api/me') return json(route,{username:'student',status:'active',role:'member',team_owner:false,
        team_name:'Class Lab',team_slug:'class-lab',csrf_token:'test-csrf'});
      if (url.pathname === '/api/instances') return json(route,{instances:[]});
      if (url.pathname === '/api/quota') return json(route,{instances:{in_use:0,limit:4},cores:{in_use:0,limit:8},ram:{in_use:0,limit:16384}});
      if (url.pathname === '/api/profile/password') {
        requests += 1;
        assert.equal(route.request().headers()['x-csrf-token'],'test-csrf');
        const data = route.request().postDataJSON();
        assert.equal(data.new_password,'new password');
        return requests === 1 ? json(route,{detail:'Invalid current password'},401) : json(route,{ok:true});
      }
      return useLiveStatic ? route.continue() : route.abort();
    });
    await page.goto(baseUrl);
    await page.locator('#dashboard-view:not(.hidden)').waitFor();
    await page.locator('#change-password').click();
    await page.locator('#password-dialog[open]').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.name),'current_password');
    await page.evaluate(axe.source);
    const accessibility = await page.evaluate(() => window.axe.run(document, {
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']},
    }));
    assert.deepEqual(accessibility.violations.map(item => item.id),[]);
    await page.locator('#password-form [name=current_password]').fill('wrong password');
    await page.locator('#password-form [name=new_password]').fill('new password');
    await page.locator('#password-form button[type=submit]').click();
    await page.getByText('현재 비밀번호가 올바르지 않습니다.').waitFor();
    assert.equal(await page.locator('#password-form button[type=submit]').isEnabled(),true);
    await page.locator('#password-form [name=current_password]').fill('old password');
    await page.locator('#password-form button[type=submit]').click();
    await page.getByText('비밀번호를 바꿨습니다.').waitFor();
    await page.locator('#password-dialog').waitFor({state:'hidden'});
    await page.locator('#change-password').click();
    assert.equal(await page.locator('#password-form [name=current_password]').inputValue(),'');
    assert.equal(await page.locator('#password-form [name=new_password]').inputValue(),'');
    const width = await page.evaluate(() => ({viewport:innerWidth,content:document.documentElement.scrollWidth}));
    assert.ok(width.content <= width.viewport + 1, JSON.stringify(width));
    assert.equal(requests,2);
    console.log('Account password dialog, feedback, and mobile width checks passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });

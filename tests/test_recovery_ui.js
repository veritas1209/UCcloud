const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = 'https://comsv-03.tail5819db.ts.net';
const staticDir = path.resolve('static');
const output = process.argv[2];
const json = (route, body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:320,height:700}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route(base + '/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/public-config') return json(route,{operator_enabled:false,registration_code_required:true});
      if (url.pathname === '/api/me') return json(route,{detail:'Login required'},401);
      if (url.pathname === '/api/auth/find-id') return json(route,{username:'student'});
      if (url.pathname === '/api/auth/forgot-password') return json(route,{detail:'메일 발송을 준비 중입니다. 운영자에게 문의해 주세요.'},503);
      if (url.pathname === '/api/auth/reset-password') {
        const body = route.request().postDataJSON();
        assert.equal(body.token, 'a'.repeat(43)); assert.equal(body.password, 'newpassword');
        return json(route,{message:'비밀번호를 바꿨습니다. 새 비밀번호로 로그인하세요.'});
      }
      const file = url.pathname === '/' ? 'index.html' : url.pathname === '/reset-password' ? 'reset-password.html' : url.pathname.startsWith('/assets/') ? url.pathname.slice(8) : null;
      if (file && !file.includes('..')) return process.env.UI_LIVE_STATIC === 'true' ? route.continue() : route.fulfill({path:path.join(staticDir,file)});
      return json(route,{},404);
    });
    await page.goto(base);
    await page.locator('#find-id').click();
    await page.locator('#recovery-form input').fill('student@example.com');
    await page.locator('#recovery-form button[type=submit]').click();
    await page.getByText('아이디: student',{exact:true}).waitFor();
    await page.locator('#recovery-close').click();
    await page.locator('#find-password').click();
    await page.locator('#recovery-form input').fill('student@example.com');
    await page.locator('#recovery-form button[type=submit]').click();
    await page.getByText('메일 발송을 준비 중입니다. 운영자에게 문의해 주세요.',{exact:true}).waitFor();
    assert.equal(await page.locator('#recovery-form button[type=submit]').isEnabled(),true);
    if(output) await page.screenshot({path:path.join(output,'recovery-mobile.png'),fullPage:true});
    await page.goto(base + '/reset-password#token=' + 'a'.repeat(43));
    assert.equal(page.url(),base + '/reset-password');
    await page.locator('#reset-password').fill('newpassword');
    await page.locator('#reset-confirm').fill('differentpassword');
    await page.locator('#reset-form button').click();
    await page.getByText('비밀번호가 서로 다릅니다. 다시 확인해 주세요.',{exact:true}).waitFor();
    await page.locator('#reset-confirm').fill('newpassword');
    if(output) await page.screenshot({path:path.join(output,'reset-mobile.png'),fullPage:true});
    await page.locator('#reset-form button').click();
    await page.getByText('비밀번호를 바꿨습니다. 새 비밀번호로 로그인하세요.',{exact:true}).waitFor();
    assert.equal(await page.locator('#reset-form').isVisible(),false);
    await page.reload();
    await page.getByText('메일에 있는 비밀번호 변경 주소를 열어 주세요.',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    console.log('Recovery mobile UI, error feedback, token removal and password reset checks passed');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});

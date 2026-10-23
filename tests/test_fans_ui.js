const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const path = require('node:path');
const root = path.resolve('static');
const source = fs.readFileSync(path.join(root,'fans.js'),'utf8');
const html = fs.readFileSync(path.join(root,'index.html'),'utf8');
const url = 'http://127.0.0.1:8088';
let changes = [], minimum = 0;
const fixture = {id:'compute-1',name:'comsv-01',role:'컴퓨트',state:'ok',supported:true,minimum:0,cpu_c:30,fans:[{name:'Fan 1',percent:6}],warnings:[]};
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage();
    const errors = []; page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      const reply = value => route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      if (pathname === '/') return route.fulfill({contentType:'text/html',body:html});
      if (pathname.startsWith('/assets/')) return route.fulfill({path:path.join(root,pathname.slice(8))});
      if (pathname === '/api/public-config') return reply({operator_enabled:true});
      if (pathname === '/api/me') return reply({username:'admin',role:'admin',status:'active',csrf_token:'fan-csrf'});
      if (pathname === '/api/admin/fans') return reply({nodes:[{...fixture,minimum}]});
      if (pathname.endsWith('/minimum')) {
        changes.push({body:request.postDataJSON(),csrf:request.headers()['x-csrf-token']});
        minimum = request.postDataJSON().minimum;
        return reply({node:{...fixture,minimum},changed:true});
      }
      if (pathname === '/api/admin/events') return reply({events:[]});
      if (pathname === '/api/admin/registration-code') return reply({registration_code:''});
      if (pathname === '/api/admin/capacity') return reply({total:4,online:4});
      if (pathname === '/api/instances') return reply({instances:[]});
      if (pathname === '/api/catalog') return reply({images:[],flavors:[],software:[]});
      if (pathname === '/api/quota') return reply({});
      return reply({});
    });
    await page.goto(url);
    await page.locator('[data-section=admin]').click();
    await page.locator('[data-fan-setting]').waitFor();
    assert.match(await page.locator('.fan-panel').textContent(), /自動|자동/);
    page.on('dialog',dialog=>dialog.accept());
    await page.locator('[data-fan-setting]').selectOption('20');
    await page.locator('[data-fan-apply]').click();
    await page.waitForFunction(()=>document.querySelector('[data-fan-setting]')?.value==='20' && !document.querySelector('[data-fan-apply]')?.disabled);
    assert.deepEqual(changes[0],{body:{minimum:20,expected:0},csrf:'fan-csrf'});
    await page.locator('[data-fan-setting]').selectOption('0'); await page.locator('[data-fan-apply]').click();
    await page.waitForFunction(()=>document.querySelector('[data-fan-setting]')?.value==='0' && !document.querySelector('[data-fan-apply]')?.disabled);
    assert.deepEqual(changes[1].body,{minimum:0,expected:20});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), 'mobile page must not overflow horizontally');
    await page.screenshot({path:'fan-admin-mobile.png',fullPage:true});
    await page.setViewportSize({width:1440,height:1100});
    await page.screenshot({path:'fan-admin-desktop.png',fullPage:true});
    assert.deepEqual(errors,[]);
    fixture.name = '<img src=x onerror=alert(1)>'; fixture.fans[0].name = '<svg onload=alert(1)>';
    await page.locator('#fan-refresh').click(); await page.waitForFunction(()=>document.querySelector('.fan-table')?.textContent.includes('<img'));
    assert.equal(await page.locator('.fan-panel img,.fan-panel svg').count(),0);
    console.log('Fan UI apply/reset, CSRF, feedback, layout and XSS checks passed');
  } finally { await browser.close(); }
})();

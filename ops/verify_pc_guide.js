const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = 'https://comsv-03.tail5819db.ts.net';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const [name, width] of [['pc', 1360], ['mobile', 390]]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const response = await page.goto(base + '/guide/funnel', { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      const photos = page.locator('.guide-photo img');
      assert.equal(await photos.count(), 4);
      for (let index = 0; index < 4; index++) {
        const photo = photos.nth(index);
        await photo.scrollIntoViewIfNeeded();
        await photo.evaluate(image => image.decode());
        assert.ok(await photo.evaluate(image => image.complete && image.naturalWidth > 0));
        const link = photo.locator('..');
        assert.equal(await link.getAttribute('target'), '_blank');
        assert.equal((await page.request.get(base + await link.getAttribute('href'))).status(), 200);
      }
      assert.equal(await page.locator('.photo-slot').count(), 0);
      assert.equal(await page.locator('a[href*="tailscale.com"]').count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#keep-running-help').evaluate(element => element.open = true);
      await page.locator('#site-start').fill('npm start');
      assert.equal(await page.locator('#copy-keep-running').isDisabled(), false);
      assert.match(await page.locator('#keep-running-command').textContent(), /npm start/);
      await page.locator('#keep-running-help').evaluate(element => element.open = false);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({ path: path.resolve('guide-' + name + '-final.png'), fullPage: true });
      assert.deepEqual(errors, []);
      console.log(name + ': live guide, four full-size photos, command generator and layout verified.');
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.name + ': ' + error.message); process.exitCode = 1; });

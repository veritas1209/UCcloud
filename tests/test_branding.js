/* Anonymous browser/crawler checks. No login, VM or data mutation. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = 'https://comsv-03.tail5819db.ts.net';

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on('console', message => {
      if (message.type() === 'error' && /content security|refused|manifest|favicon/i.test(message.text())) errors.push(message.text());
    });
    await page.goto(base, {waitUntil: 'networkidle'});
    assert.equal(await page.title(), 'UCcloud');
    assert.equal(await page.locator('meta[property="og:description"]').getAttribute('content'), '팀 서버를 만들고 사용하세요.');
    const imageUrl = await page.locator('meta[property="og:image"]').getAttribute('content');
    assert.equal(imageUrl, base + '/assets/brand/share-v1.png');
    const png = await context.request.get(imageUrl);
    assert.equal(png.status(), 200);
    assert.equal(png.headers()['content-type'], 'image/png');
    const body = await png.body();
    assert.equal(body.readUInt32BE(16), 1200);
    assert.equal(body.readUInt32BE(20), 630);
    for (const relation of ['icon', 'apple-touch-icon', 'manifest']) {
      for (const href of await page.locator(`link[rel="${relation}"]`).evaluateAll(links => links.map(link => link.href))) {
        assert.equal((await context.request.get(href)).status(), 200, href);
      }
    }
    const manifest = await (await context.request.get(base + '/assets/brand/site.webmanifest')).json();
    for (const icon of manifest.icons) assert.equal((await context.request.get(base + icon.src)).status(), 200);
    for (const url of ['/favicon.ico', '/robots.txt', '/sitemap.xml', '/api/health']) {
      assert.equal((await context.request.get(base + url)).status(), 200, url);
    }
    assert.equal((await context.request.get(base + '/api/admin/registration-code')).status(), 404);
    assert.equal((await context.request.get(base + '/api/me')).status(), 401);
    for (const url of ['/terminal', '/reset-password']) {
      const response = await context.request.get(base + url);
      assert.equal(response.headers()['x-robots-tag'], 'noindex, nofollow');
    }
    // Server-rendered metadata must be available to crawlers without JavaScript.
    const crawler = await browser.newContext({javaScriptEnabled: false});
    const crawlerPage = await crawler.newPage();
    await crawlerPage.goto(base);
    assert.equal(await crawlerPage.locator('meta[property="og:image"]').count(), 1);
    assert.equal(await crawlerPage.locator('meta[name="twitter:card"]').getAttribute('content'), 'summary_large_image');
    assert.deepEqual(errors, []);
    console.log('Public branding checks passed: icons, PNG, metadata without JS, manifest, search hints and authentication boundary.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});

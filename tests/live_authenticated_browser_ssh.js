/** Live, disposable QA account and VM only. No API or WebSocket mocks. */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = 'https://comsv-03.tail5819db.ts.net';
const {QA_USERNAME: username, QA_PASSWORD: password, QA_VM_ID: vmId} = process.env;
assert.ok(username?.startsWith('ucqa') && password && /^[0-9a-f-]{36}$/i.test(vmId || ''),
  'Disposable QA credentials and VM ID are required');

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}});
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await page.locator('#login-form input[name=username]').fill(username);
    await page.locator('#login-form input[name=password]').fill(password);
    await page.locator('#login-form button[type=submit]').click();
    await page.locator('#dashboard-view:not(.hidden)').waitFor({timeout: 30000});
    const link = page.locator(`a[data-terminal][href="/terminal?vm=${vmId}"]`);
    await link.waitFor({timeout: 30000});
    const popup = page.waitForEvent('popup');
    await link.click();
    const terminal = await popup;
    terminal.on('pageerror', error => pageErrors.push(error.message));
    await terminal.locator('#connect-form:not(.hidden)').waitFor({timeout: 30000});
    assert.equal(await terminal.locator('#vm-user').inputValue(), 'student');
    const prefix = 'UCQA-BROWSER-';
    const suffix = crypto.randomBytes(8).toString('hex');
    const marker = prefix + suffix;
    let received = '';
    const socketEvents = [];
    let finish;
    const output = new Promise(resolve => {finish = resolve;});
    terminal.on('websocket', socket => {
      socketEvents.push('opened');
      socket.on('close', () => socketEvents.push('closed'));
      socket.on('socketerror', error => socketEvents.push(`error:${error}`));
      socket.on('framereceived', frame => {
        const chunk = Buffer.isBuffer(frame.payload) ? frame.payload.toString('utf8') : String(frame.payload);
        received += chunk;
        if (received.includes(marker)) finish();
      });
    });
    await terminal.locator('#vm-password').fill(password);
    await terminal.locator('#connect').click();
    try {
      await terminal.locator('#terminal:not(.hidden)').waitFor({timeout: 30000});
    } catch (error) {
      const status = await terminal.locator('#status').textContent();
      throw new Error(`Browser SSH did not connect: ${status}; websocket=${socketEvents.join(',')}; ${error.message}`);
    }
    await terminal.getByText('연결됐습니다.', {exact: false}).waitFor();
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.keyboard.type(`printf '%s%s\\n' ${prefix} ${suffix}`);
    await terminal.keyboard.press('Enter');
    await Promise.race([
      output,
      new Promise((_, reject) => setTimeout(() => reject(new Error('VM shell output timed out')), 30000)),
    ]);
    assert.deepEqual(pageErrors, []);
    console.log('Live authenticated browser → UCcloud WebSocket → VM shell passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});

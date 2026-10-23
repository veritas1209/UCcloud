/** Create and connect to one disposable VM through the real public portal. */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = 'https://comsv-03.tail5819db.ts.net';
const {QA_USERNAME: username, QA_PASSWORD: password, QA_VM_NAME: vmName} = process.env;
assert.ok(username?.startsWith('ucqa') && password && /^portal-qa-ui-[0-9a-f]{12}$/.test(vmName || ''),
  'Disposable QA credentials and VM name are required');

function waitForOutput(read, marker, timeoutMs) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (read().includes(marker)) { clearInterval(timer); resolve(); }
      else if (Date.now() - started >= timeoutMs) {
        clearInterval(timer);
        reject(new Error(`VM output timed out waiting for ${marker}`));
      }
    }, 50);
  });
}

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await page.locator('#login-form input[name=username]').fill(username);
    await page.locator('#login-form input[name=password]').fill(password);
    await page.locator('#login-form button[type=submit]').click();
    try {
      await page.locator('#dashboard-view:not(.hidden)').waitFor({timeout: 30000});
    } catch (error) {
      throw new Error(`QA login did not reach dashboard: ${await page.locator('#auth-message').textContent()}; ${error.message}`);
    }
    await page.locator('#open-create').click();
    await page.locator('#create-dialog[open]').waitFor();
    assert.equal(await page.locator('#create-form input[name=vm_username]').inputValue(), 'student');
    await page.locator('#create-form input[name=name]').fill(vmName);
    await page.locator('#create-form input[name=vm_password]').fill(password);
    const creation = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/instances' && response.request().method() === 'POST',
    );
    const createdPopup = page.waitForEvent('popup');
    await page.locator('#create-form button[type=submit]').click();
    const terminal = await createdPopup;
    terminal.on('pageerror', error => errors.push(error.message));
    const response = await creation;
    if (response.status() !== 202) throw new Error(`VM creation failed: ${await response.text()}`);
    await terminal.waitForURL(url => url.pathname === '/terminal' && url.searchParams.get('wait') === '1'
      && url.searchParams.get('handoff') === '1', {timeout: 30000});
    const created = {id: new URL(terminal.url()).searchParams.get('vm'), name: vmName};
    assert.match(created.id, /^[0-9a-f-]{36}$/i);
    assert.equal(created.name, vmName);
    console.log('Real portal create response → browser SSH tab passed');
    const createSuffix = crypto.randomBytes(8).toString('hex');
    const marker = 'UCQA-CREATE-' + createSuffix;
    let received = '';
    let sawWaiting = false;
    terminal.on('websocket', socket => socket.on('framereceived', frame => {
      const chunk = Buffer.isBuffer(frame.payload) ? frame.payload.toString('utf8') : String(frame.payload);
      if (chunk === '{"type":"waiting"}') sawWaiting = true;
      received += chunk;
    }));
    await terminal.locator('#terminal:not(.hidden)').waitFor({timeout: 300000});
    assert.equal(await terminal.locator('#vm-user').inputValue(), 'student');
    assert.equal(await terminal.locator('#vm-password').inputValue(), '');
    assert.equal(await terminal.evaluate(() => window.opener), null);
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.keyboard.type(`printf '%s%s\\n' UCQA-CREATE- ${createSuffix}`);
    await terminal.keyboard.press('Enter');
    await waitForOutput(() => received, marker, 30000);
    assert.deepEqual(errors, []);
    console.log(`Real portal create → OpenStack VM → browser SSH shell passed; first-boot wait shown: ${sawWaiting}`);

    const initSuffix = crypto.randomBytes(8).toString('hex');
    await terminal.keyboard.type(`cloud-init status --wait && printf '%s%s\\n' UCQA-INIT- ${initSuffix}`);
    await terminal.keyboard.press('Enter');
    await waitForOutput(() => received, 'UCQA-INIT-' + initSuffix, 420000);
    const promptOffset = received.length;
    await terminal.keyboard.type('sudo tailscale up');
    await terminal.keyboard.press('Enter');
    await waitForOutput(() => received.slice(promptOffset), '[sudo] password for student:', 30000);
    await terminal.keyboard.type(password);
    await terminal.keyboard.press('Enter');
    await terminal.locator('#tailscale-login:not(.hidden)').waitFor({timeout: 60000});
    const loginUrl = new URL(await terminal.locator('#tailscale-login').getAttribute('href'));
    assert.equal(loginUrl.hostname, 'login.tailscale.com');
    assert.equal(loginUrl.protocol, 'https:');
    console.log('Fresh VM Tailscale CLI → browser login button passed');
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.keyboard.press('Control+C');

    await page.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    const card = page.locator('.instance-card').filter({has: page.locator('h3', {hasText: vmName})});
    await card.waitFor({timeout: 30000});
    const actionResponse = action => page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/instances/${created.id}/action`
      && response.request().method() === 'POST'
      && JSON.parse(response.request().postData() || '{}').action === action,
    );
    const stopping = actionResponse('stop');
    await card.locator('details[data-vm-manage] > summary').click();
    await card.locator('[data-action=stop]').click();
    assert.equal((await stopping).status(), 202, 'VM stop request failed');
    const startLink = card.locator(`a[data-terminal][href="/terminal?vm=${created.id}&start=1"]`);
    await startLink.waitFor({timeout: 180000});
    assert.equal(await startLink.textContent(), '켜고 접속');
    console.log('VM reached SHUTOFF and one-click connect appeared');
    const startResponses = [];
    page.context().on('response', response => {
      if (new URL(response.url()).pathname === `/api/instances/${created.id}/action`
          && response.request().method() === 'POST'
          && JSON.parse(response.request().postData() || '{}').action === 'start') {
        startResponses.push(response.status());
      }
    });
    const reopened = page.waitForEvent('popup');
    await startLink.click();
    const terminalAgain = await reopened;
    terminalAgain.on('pageerror', error => errors.push(error.message));
    await terminalAgain.locator('#connect-form:not(.hidden)').waitFor({timeout: 30000});
    assert.deepEqual(startResponses, [202], 'one-click connect should send one successful start request');
    console.log('One click started the stopped VM and opened browser SSH');
    const restartSuffix = crypto.randomBytes(8).toString('hex');
    const markerAgain = 'UCQA-RESTART-' + restartSuffix;
    let receivedAgain = '';
    terminalAgain.on('websocket', socket => socket.on('framereceived', frame => {
      receivedAgain += Buffer.isBuffer(frame.payload) ? frame.payload.toString('utf8') : String(frame.payload);
    }));
    await terminalAgain.locator('#vm-password').fill(password);
    await terminalAgain.locator('#connect').click();
    await terminalAgain.locator('#terminal:not(.hidden)').waitFor({timeout: 180000});
    await terminalAgain.locator('.xterm-helper-textarea').focus();
    await terminalAgain.keyboard.type(`printf '%s%s\\n' UCQA-RESTART- ${restartSuffix}`);
    await terminalAgain.keyboard.press('Enter');
    await waitForOutput(() => receivedAgain, markerAgain, 30000);
    assert.deepEqual(errors, []);
    console.log('VM stop → one-click start and connect → browser SSH shell passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});

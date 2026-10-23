/** Read-only public UI check. Student APIs and SSH transport are mocked; no VM is contacted. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const localStatic = process.env.UI_LOCAL_STATIC_DIR;
const outputDir = path.resolve(process.argv[2] || '.');
fs.mkdirSync(outputDir, {recursive: true});

const json = (route, value, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(value),
});
const publicConfig = {operator_enabled: false, registration_code_required: true};
const vm = {
  id: 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205', name: 'my-server',
  status: 'ACTIVE', flavor: 'small', flavor_details: {vcpus: 1, ram: 2048, disk: 20}, vm_username: 'student', created_at: new Date().toISOString(),
  addresses: [{address: '10.30.23.177', version: 4}],
};
const quota = {
  instances: {in_use: 1, limit: 4},
  cores: {in_use: 1, limit: 8},
  ram: {in_use: 2048, limit: 16384},
};

async function checkWidth(page, label) {
  const width = await page.evaluate(() => ({viewport: innerWidth, content: document.documentElement.scrollWidth}));
  assert.ok(width.content <= width.viewport + 1, `${label} overflows horizontally: ${JSON.stringify(width)}`);
}

async function useLocalStatic(page) {
  if (!localStatic) return;
  const files = {
    '/': 'index.html',
    '/guide/funnel': 'funnel-guide.html',
    '/terminal': 'terminal.html',
    '/reset-password': 'reset-password.html',
    '/assets/reset-password.js': 'reset-password.js',
    '/assets/app.js': 'app.js',
    '/assets/terminal.js': 'terminal.js',
    '/assets/terminal-software.js': 'terminal-software.js',
    '/assets/terminal.css': 'terminal.css',
    '/assets/styles.css': 'styles.css',
    '/assets/funnel-guide.css': 'funnel-guide.css',
    '/assets/funnel-guide.js': 'funnel-guide.js',
  };
  await page.context().route('**/*', route => {
    const url = new URL(route.request().url());
    const file = files[url.pathname];
    if (url.origin === new URL(baseUrl).origin && file) {
      return route.fulfill({path: path.join(localStatic, file)});
    }
    return route.continue();
  });
}

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  const pageErrors = [];
  try {
    const login = await browser.newPage({viewport: {width: 1280, height: 800}});
    login.on('pageerror', error => pageErrors.push(`login: ${error.message}`));
    await useLocalStatic(login);
    await login.route('**/api/public-config', route => json(route, publicConfig));
    await login.route('**/api/me', route => json(route, {detail: 'Login required'}, 401));
    await login.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await login.locator('#auth-view:not(.hidden)').waitFor();
    await login.waitForFunction(() => document.activeElement === document.querySelector('#login-form input[name=username]'));
    assert.equal(await login.evaluate(() => document.activeElement === document.querySelector('#login-form input[name=username]')), true);
    assert.equal(await login.locator('.tab[data-tab=login]').getAttribute('aria-pressed'), 'true');
    await checkWidth(login, 'login desktop');
    await login.screenshot({path: path.join(outputDir, 'login-desktop.png'), fullPage: true});
    await login.setViewportSize({width: 390, height: 844});
    await checkWidth(login, 'login mobile');
    await login.screenshot({path: path.join(outputDir, 'login-mobile.png'), fullPage: true});
    await login.setViewportSize({width: 390, height: 667});
    const loginButton = await login.locator('#login-form button[type=submit]').boundingBox();
    assert.ok(loginButton.y + loginButton.height <= 667, 'login button is below a short mobile viewport');
    await login.locator('#login-form [data-password-toggle]').click();
    assert.equal(await login.locator('#login-password').getAttribute('type'), 'text');
    assert.equal(await login.locator('#login-form [data-password-toggle]').getAttribute('aria-pressed'), 'true');
    await login.setViewportSize({width: 390, height: 844});
    await login.route('**/api/auth/login', route => json(route, {detail:'Invalid username or password'}, 401));
    assert.match(await login.locator('#login-form label').first().textContent(), /아이디 또는 이메일/);
    await login.locator('#login-form input[name=username]').fill('student@example.com');
    await login.locator('#login-password').fill('wrong-password');
    await login.locator('#login-form button[type=submit]').click();
    await login.getByText('아이디/이메일 또는 비밀번호가 올바르지 않습니다.').waitFor();
    assert.equal(await login.locator('#login-form button[type=submit]').isEnabled(), true);

    const touchContext = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    const touchLogin = await touchContext.newPage();
    touchLogin.on('pageerror', error => pageErrors.push(`touch login: ${error.message}`));
    await useLocalStatic(touchLogin);
    await touchLogin.route('**/api/public-config', route => json(route, publicConfig));
    await touchLogin.route('**/api/me', route => json(route, {detail:'Login required'}, 401));
    await touchLogin.goto(baseUrl, {waitUntil:'domcontentloaded'});
    await touchLogin.locator('#auth-view:not(.hidden)').waitFor();
    assert.equal(await touchLogin.evaluate(() => matchMedia('(pointer: fine)').matches), false);
    assert.equal(await touchLogin.evaluate(() => document.activeElement === document.querySelector('#login-form input[name=username]')), false);
    await touchLogin.locator('.tab[data-tab=register]').tap();
    assert.equal(await touchLogin.evaluate(() => document.activeElement === document.querySelector('#register-form input[name=email]')), true);
    await touchContext.close();

    await login.locator('.tab[data-tab=register]').click();
    await login.locator('#register-form:not(.hidden)').waitFor();
    assert.equal(await login.evaluate(() => document.activeElement === document.querySelector('#register-form input[name=email]')), true);
    await login.locator('#register-form input[name=email]').fill('student.name+lab@example.invalid');
    assert.equal(await login.locator('#register-form input[name=username]').inputValue(), 'student.namelab');
    assert.equal(await login.locator('#register-form input[name=team_name]').inputValue(), 'student.namelab의 팀');
    await login.locator('#register-form input[name=username]').fill('my-own-id');
    assert.equal(await login.locator('#register-form input[name=team_name]').inputValue(), 'my-own-id의 팀');
    await login.locator('#register-form input[name=team_name]').fill('Study Club');
    await login.locator('#register-form input[name=email]').fill('other@example.invalid');
    assert.equal(await login.locator('#register-form input[name=username]').inputValue(), 'my-own-id');
    assert.equal(await login.locator('#register-form input[name=team_name]').inputValue(), 'Study Club');
    await login.locator('#register-form').evaluate(form => form.reset());
    await login.locator('#register-form input[name=email]').fill('newstudent@example.invalid');
    assert.equal(await login.locator('#register-form input[name=username]').inputValue(), 'newstudent');
    assert.equal(await login.locator('#register-form input[name=team_name]').inputValue(), 'newstudent의 팀');
    await login.locator('#register-form').evaluate(form => form.reset());
    assert.equal(await login.locator('.tab[data-tab=login]').getAttribute('aria-pressed'), 'false');
    assert.equal(await login.locator('.tab[data-tab=register]').getAttribute('aria-pressed'), 'true');
    assert.equal(await login.locator('#login-password').getAttribute('type'), 'password');
    await login.locator('#register-form [data-password-toggle]').click();
    assert.equal(await login.locator('#register-password').getAttribute('type'), 'text');
    await login.locator('#register-form [data-password-toggle]').click();
    assert.equal(await login.locator('#register-password').getAttribute('type'), 'password');
    await checkWidth(login, 'register mobile');
    await login.screenshot({path: path.join(outputDir, 'register-mobile.png'), fullPage: true});
    await login.setViewportSize({width: 390, height: 667});
    const registerButton = await login.locator('#register-form button[type=submit]').boundingBox();
    assert.ok(registerButton.y + registerButton.height <= 667, 'register button is below a short mobile viewport');
    await login.screenshot({path: path.join(outputDir, 'register-short-mobile.png'), fullPage: true});
    await login.setViewportSize({width: 320, height: 700});
    await checkWidth(login, 'register narrow mobile');
    await login.setViewportSize({width: 390, height: 844});

    const invited = await browser.newPage({viewport: {width: 390, height: 844}});
    invited.on('pageerror', error => pageErrors.push(`invited: ${error.message}`));
    await useLocalStatic(invited);
    await invited.route('**/api/public-config', route => json(route, publicConfig));
    let joined = false;
    await invited.route('**/api/me', route => json(route, joined ? {
      username: 'invited-student', status: 'pending', role: 'member', team_owner: false,
      team_name: 'Class Lab', csrf_token: 'invite-csrf',
    } : {detail: 'Login required'}, joined ? 200 : 401));
    const joinRequests = [];
    await invited.route('**/api/auth/register', route => {
      const body = JSON.parse(route.request().postData());
      joinRequests.push(body);
      return body.join_existing
        ? json(route, {ready: false, message: '팀장에게 가입 요청을 보냈습니다.'}, 201)
        : json(route, {detail: {code: 'team_exists', message: '이미 있는 팀입니다.'}}, 409);
    });
    await invited.route('**/api/auth/login', route => {
      joined = true;
      return json(route, {csrf_token: 'invite-csrf'});
    });
    let joinConfirmed = false;
    invited.on('dialog', async dialog => {
      assert.match(dialog.message(), /팀장에게 가입 요청/);
      joinConfirmed = true;
      await dialog.accept();
    });
    await invited.goto(`${baseUrl}/?team=Class%20Lab`, {waitUntil: 'domcontentloaded'});
    await invited.locator('#register-form:not(.hidden)').waitFor();
    assert.equal(await invited.locator('#register-form input[name=team_name]').inputValue(), 'Class Lab');
    await checkWidth(invited, 'invited registration mobile');
    await invited.screenshot({path: path.join(outputDir, 'invite-registration-mobile.png'), fullPage: true});
    await invited.locator('#register-form input[name=username]').fill('invited-student');
    await invited.locator('#register-form input[name=email]').fill('invited@example.invalid');
    assert.equal(await invited.locator('#register-form input[name=team_name]').inputValue(), 'Class Lab');
    await invited.locator('#registration-code-field:not(.hidden) input').fill('QA-CODE');
    await invited.locator('#register-form input[name=password]').fill('test-password');
    await invited.locator('#register-form button[type=submit]').click();
    await invited.locator('#pending-view:not(.hidden)').waitFor();
    assert.match(await invited.locator('#pending-copy').textContent(), /Class Lab에 신청했습니다/);
    assert.equal(joinConfirmed, true);
    assert.equal(joinRequests.length, 2);
    assert.equal(joinRequests[0].team_name, 'Class Lab');
    assert.equal(joinRequests[0].join_existing, undefined);
    assert.equal(joinRequests[1].join_existing, true);

    async function checkProvisionRetry(manual, failFirst = false) {
      const page = await browser.newPage();
      page.on('pageerror', error => pageErrors.push(`provision retry: ${error.message}`));
      await useLocalStatic(page);
      let loggedIn = false;
      let provisioned = false;
      let retryCalls = 0;
      await page.route('**/api/public-config', route => json(route, publicConfig));
      await page.route('**/api/me', route => json(route, loggedIn ? {
        username: 'new-owner', status: provisioned ? 'active' : 'pending', role: 'member',
        team_owner: true, team_name: 'New Lab', team_slug: provisioned ? 'new-lab' : null,
        csrf_token: 'owner-csrf',
      } : {detail: 'Login required'}, loggedIn ? 200 : 401));
      await page.route('**/api/auth/register', route => json(route, {
        ready: false, message: '계정은 생성됐지만 작업 공간 준비가 지연되고 있습니다.',
      }, 201));
      await page.route('**/api/auth/login', route => { loggedIn = true; return json(route, {csrf_token: 'owner-csrf'}); });
      await page.route('**/api/team/provision', route => {
        retryCalls += 1;
        if (failFirst && retryCalls === 1) return json(route, {detail: 'Cloud service is temporarily unavailable'}, 502);
        provisioned = true;
        return json(route, {ready: true});
      });
      await page.route('**/api/instances', route => json(route, {instances: []}));
      await page.route('**/api/quota', route => json(route, quota));
      await page.route('**/api/team/requests', route => json(route, {requests: []}));
      await page.goto(baseUrl, {waitUntil: 'domcontentloaded'});
      await page.locator('.tab[data-tab=register]').click();
      await page.locator('#register-form input[name=email]').fill('new-owner@example.invalid');
      await page.locator('#register-form input[name=team_name]').fill('New Lab');
      await page.locator('#registration-code-field input').fill('QA-CODE');
      await page.locator('#register-form input[name=password]').fill('test-password');
      await page.locator('#register-form button[type=submit]').click();
      await page.locator('#pending-view:not(.hidden)').waitFor();
      if (manual) await page.locator('#retry-provision').click();
      if (failFirst) {
        await page.getByText('준비가 지연되고 있습니다. 다시 시도해 주세요.').waitFor({timeout: 10000});
        assert.equal(retryCalls, 1, 'failed automatic retry ran more than once');
        assert.equal(await page.locator('#retry-provision').isEnabled(), true);
        await page.locator('#retry-provision').click();
      }
      await page.locator('#dashboard-view:not(.hidden)').waitFor({timeout: 15000});
      if (manual) await page.waitForTimeout(3300);
      assert.equal(retryCalls, failFirst ? 2 : 1,
        failFirst ? 'manual fallback did not recover after one automatic failure'
          : manual ? 'manual retry did not cancel the automatic retry' : 'automatic retry did not run once');
      await page.close();
    }
    await checkProvisionRetry(false);
    await checkProvisionRetry(true);
    await checkProvisionRetry(false, true);

    const owner = await browser.newPage({viewport: {width: 390, height: 844}});
    owner.on('pageerror', error => pageErrors.push(`owner: ${error.message}`));
    await useLocalStatic(owner);
    await owner.context().grantPermissions(['clipboard-read', 'clipboard-write'], {origin: new URL(baseUrl).origin});
    await owner.route('**/api/public-config', route => json(route, publicConfig));
    await owner.route('**/api/me', route => json(route, {
      username: 'owner', status: 'active', role: 'member', team_owner: true,
      team_name: 'Class Lab', team_slug: 'class-lab', csrf_token: 'owner-csrf',
    }));
    await owner.route('**/api/instances', route => json(route, {instances: []}));
    await owner.route('**/api/quota', route => json(route, quota));
    let pendingTeamRequests = [];
    await owner.route('**/api/team/requests', route => json(route, {requests: pendingTeamRequests}));
    await owner.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await owner.getByText('아직 VM이 없습니다.').waitFor();
    assert.equal(await owner.title(), 'UCcloud');
    await owner.locator('.nav-item[data-section=team]:not(.hidden)').click();
    await owner.getByText('새 가입 요청이 없습니다.').waitFor();
    await checkWidth(owner, 'team owner mobile');
    await owner.screenshot({path: path.join(outputDir, 'team-owner-mobile.png'), fullPage: true});
    const emptyRequests = await owner.locator('#request-list .empty').elementHandle();
    await owner.evaluate(() => loadTeamRequests());
    assert.equal(await emptyRequests.evaluate(element => element.isConnected), true, 'unchanged team poll replaced the request list');
    pendingTeamRequests = [{id: 7, username: 'new-student', email: 'new@example.invalid', created_at: '2026-09-24 13:00'}];
    await owner.evaluate(() => loadTeamRequests());
    await owner.getByText('new-student').waitFor();
    assert.equal(await owner.locator('#request-count').textContent(), ' (1)');
    assert.equal(await owner.title(), '가입 요청 1건 · UCcloud');
    const requestCard = await owner.locator('#request-list .approval-card').elementHandle();
    await owner.evaluate(() => loadTeamRequests());
    assert.equal(await requestCard.evaluate(element => element.isConnected), true, 'unchanged team poll replaced a pending request');
    await owner.bringToFront();
    const visibleRequest = owner.waitForRequest('**/api/team/requests');
    await owner.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await visibleRequest;
    await owner.bringToFront();
    await owner.locator('#copy-team-invite').click();
    await owner.getByText('복사됨').waitFor();
    const inviteUrl = new URL(await owner.evaluate(() => navigator.clipboard.readText()));
    assert.equal(inviteUrl.origin, new URL(baseUrl).origin);
    assert.equal(inviteUrl.searchParams.get('team'), 'Class Lab');
    pendingTeamRequests = [];
    await owner.evaluate(() => loadTeamRequests());
    assert.equal(await owner.title(), 'UCcloud');
    pendingTeamRequests = [{id: 8, username: 'background-student', email: 'background@example.invalid', created_at: '2026-09-24 14:00'}];
    await owner.evaluate(() => {
      Object.defineProperty(document, 'hidden', {value: true, configurable: true});
      return pollBackgroundTeamRequests();
    });
    assert.equal(await owner.title(), '가입 요청 1건 · UCcloud');
    pendingTeamRequests = [];
    await owner.evaluate(() => { delete document.hidden; return loadTeamRequests(); });
    assert.equal(await owner.title(), 'UCcloud');

    const operator = await browser.newPage({viewport: {width: 390, height: 844}});
    operator.on('pageerror', error => pageErrors.push(`operator: ${error.message}`));
    await useLocalStatic(operator);
    await operator.route('**/api/public-config', route => json(route, {operator_enabled: true, registration_code_required: true}));
    await operator.route('**/api/me', route => json(route, {
      username: 'admin', status: 'active', role: 'admin', team_owner: false,
      team_name: '운영자', csrf_token: 'admin-csrf',
    }));
    await operator.route('**/api/admin/events', route => json(route, {events: [
      {id:4,action:'auth.register.rate_limited',target:'1000/1000',created_at:'2026-09-24 12:40:00',username:null},
      {id:3,action:'auth.register.rate_warning',target:'800/1000',created_at:'2026-09-24 12:35:00',username:null},
      {id:2,action:'cloud.health.recovered',created_at:'2026-09-24 12:30:00',username:null},
      {id:1,action:'cloud.health.failed',created_at:'2026-09-24 12:20:00',username:null},
    ]}));
    await operator.route('**/api/admin/capacity', route => json(route, {
      online: 4, total: 4, memory_free_mb: 110000,
    }));
    await operator.route('**/api/admin/registration-code', route => json(route, {
      registration_code: '2026UCCS-KKS-TEST',
    }));
    await operator.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await operator.locator('.nav-item[data-section=admin]:not(.hidden)').click();
    await operator.getByText('서버 4/4 정상 · 남은 메모리 107GB').waitFor();
    await operator.getByText('2026UCCS-KKS-TEST').waitFor();
    assert.equal(await operator.locator('#copy-registration-code').isEnabled(), true);
    await operator.getByText('클라우드 연결 문제').waitFor();
    await operator.getByText('클라우드 연결 복구').waitFor();
    await operator.getByText('가입량 증가 · 800/1000').waitFor();
    await operator.getByText('가입 일시 제한 · 1000/1000').waitFor();
    await checkWidth(operator, 'operator mobile');
    await operator.screenshot({path: path.join(outputDir, 'operator-mobile.png'), fullPage: true});
    await operator.close();

    let stoppedState = 'SHUTOFF';
    const stoppedPage = await browser.newPage({viewport: {width: 390, height: 844}});
    stoppedPage.on('pageerror', error => pageErrors.push(`stopped VM: ${error.message}`));
    await useLocalStatic(stoppedPage);
    await stoppedPage.route('**/api/public-config', route => json(route, publicConfig));
    await stoppedPage.route('**/api/me', route => json(route, {
      username: 'student', status: 'active', role: 'member', team_owner: false,
      team_name: 'Class Lab', team_slug: 'class-lab', csrf_token: 'start-csrf',
    }));
    await stoppedPage.route('**/api/instances', route => json(route, {instances: [{...vm, status: stoppedState,
      addresses: stoppedState === 'ACTIVE' ? vm.addresses : []}]}));
    await stoppedPage.route('**/api/quota', route => json(route, quota));
    await stoppedPage.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    const startLink = stoppedPage.locator('.instance-card a[data-terminal]');
    await startLink.getByText('켜고 접속').waitFor();
    const stoppedHref = await startLink.getAttribute('href');
    assert.equal(stoppedHref, `/terminal?vm=${vm.id}&start=1`);
    await checkWidth(stoppedPage, 'stopped VM mobile');
    await stoppedPage.screenshot({path: path.join(outputDir, 'stopped-vm-mobile.png'), fullPage: true});
    assert.equal(await stoppedPage.locator('.instance-actions > a, .instance-actions > button').count(), 2);
    await stoppedPage.locator('details[data-vm-manage] > summary').click();
    await stoppedPage.getByRole('button', {name:'켜기만'}).waitFor();
    let uncertainDashboardStarts = 0;
    await stoppedPage.route(`**/api/instances/${vm.id}/action`, route => {
      uncertainDashboardStarts += 1;
      return json(route, {detail: 'Cloud service is temporarily unavailable'}, 502);
    });
    await stoppedPage.locator('button[data-action=start]').click();
    await stoppedPage.getByText('상태 확인 중…').waitFor();
    await stoppedPage.getByText('요청 결과를 확인하지 못했습니다. VM 상태를 자동으로 확인합니다.').waitFor();
    assert.equal(uncertainDashboardStarts, 1);

    const stoppedTerminal = await browser.newPage({viewport: {width: 390, height: 844}});
    stoppedTerminal.on('pageerror', error => pageErrors.push(`stopped terminal: ${error.message}`));
    await useLocalStatic(stoppedTerminal);
    await stoppedTerminal.route('**/api/me', route => json(route, {csrf_token: 'start-csrf'}));
    await stoppedTerminal.route('**/api/instances', route => json(route, {instances: [{...vm, status: stoppedState,
      addresses: stoppedState === 'ACTIVE' ? vm.addresses : []}]}));
    const startRequests = [];
    await stoppedTerminal.route(`**/api/instances/${vm.id}/action`, route => {
      startRequests.push({csrf: route.request().headers()['x-csrf-token'], body: JSON.parse(route.request().postData())});
      stoppedState = 'ACTIVE';
      return json(route, {ok: true}, 202);
    });
    await stoppedTerminal.goto(new URL(stoppedHref, baseUrl).href, {waitUntil: 'domcontentloaded'});
    await stoppedTerminal.getByText('VM을 켜는 중입니다. 자동으로 다시 확인합니다.').waitFor();
    await stoppedTerminal.screenshot({path: path.join(outputDir, 'stopped-vm-starting-mobile.png'), fullPage: true});
    await stoppedTerminal.locator('#connect-form:not(.hidden)').waitFor({timeout: 15000});
    assert.deepEqual(startRequests, [{csrf: 'start-csrf', body: {action: 'start'}}]);
    assert.equal(await stoppedTerminal.locator('#vm-user').inputValue(), 'student');
    await stoppedTerminal.close();
    await stoppedPage.close();

    const student = await browser.newPage({viewport: {width: 1280, height: 800}});
    student.on('pageerror', error => pageErrors.push(`student: ${error.message}`));
    await useLocalStatic(student);
    await student.route('**/api/public-config', route => json(route, publicConfig));
    await student.route('**/api/me', route => json(route, {
      username: 'student', status: 'active', role: 'member', team_name: 'Class Lab',
      team_slug: 'class-lab', team_owner: false, csrf_token: 'test-csrf',
    }));
    await student.route('**/api/backups', route => json(route, {enabled:true,backups:[]}));
    let creation = null;
    let listCreatedVm = false;
    let cloudUnavailable = false;
    let quotaUnavailable = false;
    let quotaReads = 0;
    let sessionExpired = false;
    const createdVm = {
      id: '08ad1837-94e3-4c79-a43a-b50ec5189418', name: 'vm-1', status: 'ACTIVE',
      flavor: 'small', vm_username: 'student', created_at: new Date().toISOString(),
      addresses: [{address: '10.30.23.178', version: 4}],
    };
    await student.route('**/api/instances', route => {
      if (route.request().method() === 'POST') {
        creation = {body: JSON.parse(route.request().postData()), csrf: route.request().headers()['x-csrf-token']};
        listCreatedVm = true;
        return json(route, {id: createdVm.id, name: 'vm-1', status: 'BUILD', flavor: 'small'}, 202);
      }
      if (sessionExpired) return json(route, {detail: 'Session expired'}, 401);
      if (cloudUnavailable) return json(route, {detail: 'Cloud service is temporarily unavailable'}, 502);
      return json(route, {instances: listCreatedVm ? [createdVm, vm] : [vm]});
    });
    await student.route('**/api/quota', route => {
      quotaReads += 1;
      if (sessionExpired) return json(route, {detail: 'Session expired'}, 401);
      if (cloudUnavailable || quotaUnavailable) return json(route, {detail: 'Cloud service is temporarily unavailable'}, 502);
      return json(route, listCreatedVm ? {
        instances: {in_use: 2, limit: 4}, cores: {in_use: 2, limit: 8},
        ram: {in_use: 4096, limit: 16384},
      } : quota);
    });
    await student.route('**/api/catalog', route => json(route, {
      images: [{id: 'd58be315-054e-49fb-bb25-14a5544adf39', name: 'Ubuntu 24.04'}],
      flavors: [{id: 'small', name: 'small', vcpus: 1, ram: 2048, disk: 20}],
      software: [
        {id:'node',name:'Node.js',group:'개발 환경',versions:['24','22'],note:'npm 포함',min_ram:2048},
        {id:'postgres',name:'PostgreSQL',group:'데이터베이스',versions:['18','17','16'],note:'Docker로 실행',min_ram:2048,port:5432},
        {id:'mongo',name:'MongoDB',group:'데이터베이스',versions:['8.0','7.0'],note:'Docker로 실행',min_ram:4096,port:27017},
      ],
    }));
    await student.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await student.locator('#dashboard-view:not(.hidden)').waitFor();
    await student.getByText('VM 1개 / 최대 4개').waitFor();
    const firstQuotaReads = quotaReads;
    await student.evaluate(() => refreshCloud(true));
    assert.equal(quotaReads, firstQuotaReads, 'routine VM refresh fetched quota again');
    await student.evaluate(() => { quotaUpdatedAt -= quotaRefreshInterval; return refreshCloud(true); });
    assert.equal(quotaReads, firstQuotaReads + 1, 'expired quota was not refreshed');
    listCreatedVm = true;
    await student.evaluate(() => refreshCloud(true));
    assert.equal(quotaReads, firstQuotaReads + 1, 'changed VM list unexpectedly fetched quota in the same poll');
    await student.evaluate(() => refreshCloud(true));
    assert.equal(quotaReads, firstQuotaReads + 2, 'changed VM list did not refresh quota on the next poll');
    listCreatedVm = false;
    await student.evaluate(() => refreshCloud(true));
    await student.evaluate(() => refreshCloud(true));
    await student.getByText('VM 1개 / 최대 4개').waitFor();
    cloudUnavailable = true;
    await student.evaluate(() => refreshCloud(true));
    await student.locator('#cloud-status:not(.hidden)').waitFor();
    await student.getByText('VM 목록이 최신 상태가 아닙니다. 자동으로 다시 확인합니다.').waitFor();
    assert.equal(await student.locator('.instance-card').count(), 1);
    await student.screenshot({path: path.join(outputDir, 'dashboard-cloud-unavailable-desktop.png'), fullPage: true});
    cloudUnavailable = false;
    await student.evaluate(() => refreshCloud(true));
    await student.locator('#cloud-status').waitFor({state: 'hidden'});
    quotaUnavailable = true;
    listCreatedVm = true;
    await student.evaluate(() => refreshCloud(true, true));
    await student.getByText('VM 목록은 볼 수 있지만 사용량을 확인하지 못했습니다. 자동으로 다시 확인합니다.').waitFor();
    assert.equal(await student.locator('.instance-card').count(), 2, 'quota failure hid the refreshed VM list');
    quotaUnavailable = false;
    listCreatedVm = false;
    await student.evaluate(() => refreshCloud(true));
    await student.locator('#cloud-status').waitFor({state: 'hidden'});
    assert.equal(await student.locator('.instance-card').count(), 1);
    const vmDetails = student.locator('.instance-card').filter({hasText: 'my-server'}).locator('details[data-vm-details]');
    assert.equal(await vmDetails.evaluate(element => element.open), false);
    assert.equal(await vmDetails.locator('div').first().isVisible(), false);
    await vmDetails.locator('summary').click();
    assert.equal(await vmDetails.locator('div').first().isVisible(), true);
    assert.equal(await vmDetails.locator('div').filter({hasText:'사양'}).locator('b').textContent(), 'CPU 1 · 메모리 2GB · 저장공간 20GB');
    const unchangedDetails = await vmDetails.elementHandle();
    await student.evaluate(() => refreshCloud(true));
    assert.equal(await unchangedDetails.evaluate(element => element.isConnected), true, 'unchanged polling replaced focused VM card');
    await student.evaluate(() => renderInstances(state.instances));
    assert.equal(await vmDetails.evaluate(element => element.open), true, 'automatic refresh closed VM details');
    await vmDetails.locator('summary').click();
    const vmCard = student.locator('.instance-card').filter({hasText: 'my-server'});
    const vmManage = vmCard.locator('details[data-vm-manage]');
    assert.equal(await vmCard.locator('.instance-actions > a, .instance-actions > button').count(), 2);
    assert.equal(await vmManage.evaluate(element => element.open), false);
    await vmManage.locator('summary').click();
    await vmManage.locator('[data-backup]').waitFor();
    assert.deepEqual(await vmManage.locator('button').allTextContents(), ['사이트 상태', '백업·복구', '끄기', '화면 콘솔', '재부팅', '삭제']);
    const deleteDialog = student.waitForEvent('dialog');
    const deleteClick = vmManage.locator('[data-delete]').click();
    const confirmation = await deleteDialog;
    assert.match(confirmation.message(), /VM 안의 파일도 모두 사라집니다/);
    await confirmation.dismiss();
    await deleteClick;
    assert.equal(await student.locator('.instance-card').count(), 1);
    await student.evaluate(() => renderInstances(state.instances));
    assert.equal(await vmManage.evaluate(element => element.open), true, 'automatic refresh closed VM management');
    await vmManage.locator('summary').click();
    await checkWidth(student, 'dashboard desktop');
    await student.screenshot({path: path.join(outputDir, 'dashboard-desktop.png'), fullPage: true});
    await student.setViewportSize({width: 390, height: 844});
    await checkWidth(student, 'dashboard mobile');
    const sidebarHeight = await student.locator('#dashboard-view aside').evaluate(element => element.getBoundingClientRect().height);
    assert.ok(sidebarHeight < 190, `mobile team menu is too tall: ${sidebarHeight}px`);
    await student.screenshot({path: path.join(outputDir, 'dashboard-mobile.png'), fullPage: true});
    await vmManage.locator('summary').click();
    await checkWidth(student, 'VM management mobile');
    await student.screenshot({path: path.join(outputDir, 'dashboard-manage-mobile.png'), fullPage: true});
    await student.route(`**/api/instances/${vm.id}/site`, route => json(route, {site:null}));
    await student.route(`**/api/instances/${vm.id}/site/check`, route => json(route,
      {url:'https://team.tail123.ts.net',state:'online',http_status:200,message:'접속 가능'}));
    await vmManage.locator('[data-site]').click();
    await student.locator('#site-dialog[open]').waitFor();
    await student.locator('#site-url').fill('https://team.tail123.ts.net');
    await student.locator('#site-form button[type=submit]').click();
    await student.locator('#site-result').getByText('접속 가능').waitFor();
    await checkWidth(student, 'site status mobile');
    await student.screenshot({path: path.join(outputDir, 'site-status-mobile.png'), fullPage: true});
    await student.locator('#close-site').click();
    await vmManage.locator('[data-backup]').click();
    await student.locator('#backup-dialog[open]').waitFor();
    assert.equal(await student.locator('#backup-create').isDisabled(), true);
    await student.screenshot({path: path.join(outputDir, 'backup-mobile.png'), fullPage: true});
    await student.locator('#close-backup').click();
    await vmManage.locator('summary').click();
    await student.setViewportSize({width: 320, height: 700});
    await checkWidth(student, 'dashboard narrow mobile');
    await student.setViewportSize({width: 390, height: 844});
    let createQuotaReads = 0;
    const createQuotaRoute = route => {
      createQuotaReads += 1;
      return createQuotaReads === 1
        ? json(route, {detail: 'Cloud service is temporarily unavailable'}, 502)
        : json(route, quota);
    };
    await student.route('**/api/quota', createQuotaRoute);
    await student.locator('#open-create').click();
    await student.locator('#create-dialog[open]').waitFor();
    await student.locator('#create-form button[type=submit]:not([disabled])').waitFor();
    assert.equal(createQuotaReads, 2, 'one click should recover from a temporary quota failure');
    await student.unroute('**/api/quota', createQuotaRoute);
    await checkWidth(student, 'create mobile');
    assert.equal(await student.locator('#create-form input[name=name]').inputValue(), 'vm-1');
    assert.equal(await student.evaluate(() => document.activeElement?.getAttribute('name')), 'vm_password');
    await student.locator('#vm-password-toggle').click();
    assert.equal(await student.locator('#create-form input[name=vm_password]').getAttribute('type'), 'text');
    assert.equal(await student.locator('#vm-password-toggle').getAttribute('aria-pressed'), 'true');
    await student.locator('#vm-password-toggle').click();
    assert.equal(await student.locator('#create-form input[name=vm_password]').getAttribute('type'), 'password');
    assert.equal(await student.locator('#create-options').getAttribute('open'), null);
    await student.getByText('메모리 2GB · ID student', {exact: false}).waitFor();
    const defaults = await student.locator('#create-form').evaluate(form => Object.fromEntries(new FormData(form)));
    assert.equal(defaults.name, 'vm-1');
    assert.equal(defaults.vm_username, 'student');
    assert.equal(defaults.flavor_id, 'small');
    await student.screenshot({path: path.join(outputDir, 'create-mobile.png'), fullPage: true});
    assert.equal(await student.locator('#create-form select[name=flavor_id] option').count(), 1);
    await student.setViewportSize({width: 320, height: 700});
    await checkWidth(student, 'create narrow mobile');
    await student.screenshot({path: path.join(outputDir, 'create-narrow-mobile.png'), fullPage: true});
    await student.setViewportSize({width: 390, height: 667});
    const createButton = await student.locator('#create-form button[type=submit]').boundingBox();
    assert.ok(createButton.y + createButton.height <= 667, 'create button is below a short mobile viewport');
    await student.locator('#create-options summary').click();
    assert.equal(await student.locator('#create-options').getAttribute('open'), '');
    await student.locator('#flavor-disk').getByText('저장공간 20GB').waitFor();
    await student.locator('#create-form input[name=vm_username]').fill('myuser');
    await student.getByText('ID myuser', {exact: false}).waitFor();
    await student.locator('#create-form button[type=submit]').scrollIntoViewIfNeeded();
    assert.ok(await student.locator('#create-form button[type=submit]').isVisible());
    await student.screenshot({path: path.join(outputDir, 'create-options-mobile.png'), fullPage: true});
    await student.locator('#create-form input[name=vm_username]').fill('student');
    await student.locator('#create-options summary').click();
    await student.locator('#software-select').selectOption('node');
    await student.locator('[data-software-version=node]').selectOption('22');
    await student.locator('#software-select').selectOption('postgres');
    await student.locator('[data-software-version=postgres]').selectOption('17');
    await student.locator('[data-software-port=postgres]').fill('5555');
    await student.locator('[data-software-port=postgres]').blur();
    await student.locator('#software-select').selectOption('mongo');
    assert.match(await student.locator('#software-hint').textContent(), /메모리 4GB/);
    await student.locator('[data-software-remove=mongo]').click();
    await student.setViewportSize({width:320,height:700});
    await checkWidth(student, 'software narrow mobile');
    await student.screenshot({path:path.join(outputDir,'software-picker-mobile.png'),fullPage:true});
    await student.setViewportSize({width:1280,height:800});
    await student.screenshot({path:path.join(outputDir,'software-picker-desktop.png'),fullPage:true});
    await student.locator('#create-form input[name=vm_password]').fill('fake-test-password');
    await student.context().route('**/api/me', route => json(route, {csrf_token: 'test-csrf'}));
    await student.context().route('**/api/instances', route => json(route, {instances: [createdVm]}));
    await student.context().addInitScript(() => {
      window.__sshFrames = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 0; setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
        send(data) {
          const frame = JSON.parse(data);
          window.__sshFrames.push(frame);
          if (frame.password) setTimeout(() => this.onmessage?.({data: JSON.stringify({type: 'ready'})}), 0);
        }
        close(code = 1000) { this.readyState = 3; this.onclose?.({code}); }
      };
    });
    const createdPopup = student.waitForEvent('popup');
    await student.locator('#create-form button[type=submit]').click();
    const createdTerminal = await createdPopup;
    createdTerminal.on('pageerror', error => pageErrors.push(`created terminal: ${error.message}`));
    await createdTerminal.waitForURL(`${baseUrl}/terminal?vm=${createdVm.id}&wait=1&handoff=1`);
    await createdTerminal.locator('#terminal:not(.hidden)').waitFor({timeout: 15000});
    assert.equal(creation?.body.name, 'vm-1');
    assert.equal(creation.body.vm_username, 'student');
    assert.equal(creation.body.flavor_id, 'small');
    assert.equal(creation.body.vm_password, 'fake-test-password');
    assert.deepEqual(creation.body.software, [{id:'node',version:'22'},{id:'postgres',version:'17',port:5555}]);
    assert.equal(creation.csrf, 'test-csrf');
    assert.equal(await createdTerminal.locator('#vm-user').inputValue(), 'student');
    assert.equal(await createdTerminal.locator('#vm-password').inputValue(), '');
    assert.equal(await createdTerminal.evaluate(() => window.opener), null);
    const handoffFrames = await createdTerminal.evaluate(() => window.__sshFrames);
    assert.equal(handoffFrames.filter(frame => frame.password).length, 1);
    assert.equal(handoffFrames.find(frame => frame.password).password, 'fake-test-password');
    const browserStorage = await createdTerminal.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
    assert.ok(!browserStorage.includes('fake-test-password'), 'VM password was stored in browser storage');
    await createdTerminal.close();
    await student.locator('#open-create').click();
    await student.locator('#create-dialog[open]').waitFor();
    assert.equal(await student.locator('.software-row').count(), 0, 'new creation retained the previous presets');
    await student.locator('#create-form input[name=vm_password]').fill('blocked-popup-password');
    await student.evaluate(() => { window.open = () => null; });
    await student.locator('#create-form button[type=submit]').click();
    await student.waitForURL(`${baseUrl}/terminal?vm=${createdVm.id}&wait=1`);
    await student.locator('#connect-form:not(.hidden)').waitFor();
    assert.equal(await student.locator('#vm-password').inputValue(), '', 'blocked popup must require manual password entry');
    await student.goto(baseUrl, {waitUntil: 'domcontentloaded'});
    await student.locator('#dashboard-view:not(.hidden)').waitFor();
    const newCard = student.locator('.instance-card').filter({hasText: 'vm-1'});
    await newCard.getByText('실행 중', {exact: true}).waitFor();
    await student.getByText('VM 2개 / 최대 4개').waitFor();
    assert.equal(await student.locator('#toast').evaluate(element => getComputedStyle(element).pointerEvents), 'none');
    assert.equal(await newCard.locator('[data-terminal]').count(), 1);
    assert.equal(await newCard.locator('[data-terminal]').getAttribute('href'), `/terminal?vm=${createdVm.id}`);
    await student.getByText('VM 2개 / 최대 4개').waitFor();
    await student.locator('#open-create').click();
    await student.locator('#create-dialog[open]').waitFor();
    assert.equal(await student.locator('#create-form input[name=name]').inputValue(), 'vm-2');
    assert.equal(await student.locator('#create-form input[name=vm_password]').inputValue(), '');
    assert.equal(await student.locator('#create-form input[name=vm_password]').getAttribute('type'), 'password');
    await student.locator('#create-dialog [data-close]').click();
    await student.evaluate(() => refreshCloud(true));
    await newCard.locator('[data-terminal]').waitFor();
    assert.equal(await newCard.getByText('실행 중', {exact: true}).count(), 1);
    await student.getByText('VM 2개 / 최대 4개').waitFor();

    const earlyTerminal = await browser.newPage({viewport: {width: 390, height: 844}});
    earlyTerminal.on('pageerror', error => pageErrors.push(`early terminal: ${error.message}`));
    await useLocalStatic(earlyTerminal);
    await earlyTerminal.route('**/api/me', route => json(route, {csrf_token: 'test-csrf'}));
    let earlyVmListed = false;
    await earlyTerminal.route('**/api/instances', route => json(route, {instances: earlyVmListed ? [vm] : []}));
    await earlyTerminal.goto(`${baseUrl}/terminal?vm=${vm.id}&wait=1`, {waitUntil: 'domcontentloaded'});
    await earlyTerminal.getByText('VM을 만드는 중입니다. 자동으로 다시 확인합니다.').waitFor();
    earlyVmListed = true;
    await earlyTerminal.locator('#connect-form:not(.hidden)').waitFor({timeout: 15000});
    await earlyTerminal.close();

    const noIpTerminal = await browser.newPage({viewport: {width: 390, height: 844}});
    noIpTerminal.on('pageerror', error => pageErrors.push(`no-IP terminal: ${error.message}`));
    await useLocalStatic(noIpTerminal);
    await noIpTerminal.addInitScript(() => {
      window.open = () => (window.__consoleTab = {location: {href: 'about:blank'}, close() {}});
    });
    await noIpTerminal.route('**/api/me', route => json(route, {csrf_token: 'test-csrf'}));
    await noIpTerminal.route('**/api/instances', route => json(route, {instances: [{...vm, addresses: []}]}));
    await noIpTerminal.route(`**/api/instances/${vm.id}/console`, route => json(route, {url: `${baseUrl}:8443/ucconsole/console.html?token=mock`}));
    await noIpTerminal.goto(`${baseUrl}/terminal?vm=${vm.id}`, {waitUntil: 'domcontentloaded'});
    await noIpTerminal.locator('#vnc:not(.hidden)').waitFor();
    assert.equal(await noIpTerminal.locator('#connect-form').isVisible(), false);
    await noIpTerminal.getByText('VM의 IP를 기다리는 중입니다.', {exact: false}).waitFor();
    await noIpTerminal.locator('#vnc').click();
    await noIpTerminal.waitForFunction(() => window.__consoleTab?.location.href.includes('/ucconsole/console.html?token=mock'));
    await noIpTerminal.close();

    const terminal = await browser.newPage({viewport: {width: 390, height: 844}});
    terminal.on('pageerror', error => pageErrors.push(`terminal: ${error.message}`));
    await useLocalStatic(terminal);
    await terminal.addInitScript(() => {
      window.open = () => {
        window.__openedTerminalLink = {opener: window, location: {href: 'about:blank'}};
        return window.__openedTerminalLink;
      };
      window.__sshFrames = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor(url, protocols) {
          window.__sshUrl = url;
          window.__sshProtocols = protocols;
          window.__sshSocket = this;
          this.readyState = 0;
          setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0);
        }
        send(data) {
          const frame = JSON.parse(data);
          window.__sshFrames.push(frame);
          if (frame.password) {
            setTimeout(() => this.onmessage?.({data: JSON.stringify({type: 'waiting'})}), 0);
            setTimeout(() => {
              this.onmessage?.({data: JSON.stringify({type: 'ready'})});
              this.onmessage?.({data: new TextEncoder().encode('student@vm:~$ ').buffer});
            }, 100);
          }
        }
        close(code = 1000) {
          this.readyState = 3;
          this.onclose?.({code});
        }
      };
    });
    await terminal.route('**/api/me', route => json(route, {csrf_token: 'test-csrf'}));
    await terminal.route('**/api/instances', route => json(route, {instances: [vm]}));
    await terminal.goto(`${baseUrl}/terminal?vm=${vm.id}`, {waitUntil: 'domcontentloaded'});
    await terminal.locator('#connect-form:not(.hidden)').waitFor();
    assert.equal(await terminal.locator('.bar .guide-link').getAttribute('href'), '/guide/funnel');
    assert.equal(await terminal.locator('.bar .guide-link').getAttribute('target'), '_blank');
    const passwordBox = await terminal.locator('#vm-password').boundingBox();
    const visibilityBox = await terminal.locator('#vm-password-toggle').boundingBox();
    assert.ok(visibilityBox.x >= passwordBox.x && visibilityBox.x + visibilityBox.width <= passwordBox.x + passwordBox.width,
      'VM password visibility button is outside the input');
    await terminal.locator('#vm-password-toggle').click();
    assert.equal(await terminal.locator('#vm-password').getAttribute('type'), 'text');
    assert.equal(await terminal.locator('#vm-password-toggle').getAttribute('aria-pressed'), 'true');
    await terminal.locator('#vm-password-toggle').click();
    assert.equal(await terminal.locator('#vm-password').getAttribute('type'), 'password');
    assert.equal(await terminal.locator('#vm-user').inputValue(), 'student');
    await terminal.getByText('비밀번호를 넣고 연결하세요.').waitFor();
    await terminal.screenshot({path: path.join(outputDir, 'terminal-connect-mobile.png'), fullPage: true});
    await terminal.locator('#vm-password').fill('fake-test-password');
    await terminal.locator('#connect').click();
    await terminal.getByText('VM을 준비하는 중입니다. 자동으로 연결합니다.').waitFor();
    await terminal.locator('#terminal:not(.hidden)').waitFor();
    assert.equal(await terminal.locator('#vm-password').inputValue(), '');
    assert.equal(await terminal.locator('#vm-password').getAttribute('type'), 'password');
    assert.equal(await terminal.locator('#paste').count(), 0);
    await terminal.evaluate(() => window.__sshSocket.onmessage({data:JSON.stringify({type:'software',state:'installing',message:'React를 설치하고 있습니다.',step:4,total_steps:12,started_at:Math.floor(Date.now()/1000)-125,estimate_min_seconds:480,estimate_max_seconds:1500})}));
    await terminal.locator('#software-status:not(.hidden)').waitFor();
    assert.match(await terminal.locator('#software-time').textContent(), /4\/12단계.*경과 2분.*전체 예상 8~25분/);
    const firstElapsed = await terminal.locator('#software-time').textContent();
    await terminal.waitForTimeout(1100);
    assert.notEqual(await terminal.locator('#software-time').textContent(), firstElapsed);
    await checkWidth(terminal, 'installation progress mobile');
    await terminal.screenshot({path:path.join(outputDir,'software-progress-mobile.png'),fullPage:true});
    await terminal.evaluate(() => window.__sshSocket.onmessage({data:JSON.stringify({type:'software',state:'installing',message:'프로그램을 실행하고 있습니다.',step:11,total_steps:12,started_at:Math.floor(Date.now()/1000)-1600,estimate_min_seconds:480,estimate_max_seconds:1500})}));
    assert.match(await terminal.locator('#software-time').textContent(), /예상보다 오래/);
    await terminal.evaluate(() => window.__sshSocket.onmessage({data:JSON.stringify({type:'software',state:'ready',message:'프로그램 설치가 끝났습니다.',step:12,total_steps:12,started_at:Math.floor(Date.now()/1000)-130,updated_at:Math.floor(Date.now()/1000)})}));
    await terminal.locator('#software-guide:not(.hidden)').waitFor();
    assert.match(await terminal.locator('#software-time').textContent(), /^총 2분/);
    const finalElapsed = await terminal.locator('#software-time').textContent();
    await terminal.waitForTimeout(1100);
    assert.equal(await terminal.locator('#software-time').textContent(), finalElapsed);
    await terminal.locator('#software-guide').click();
    assert.ok(await terminal.evaluate(() => window.__sshFrames.some(frame => frame.data === 'cat ~/UCcloud-설치안내.txt\n')));
    await terminal.locator('#tailscale-login').waitFor({state: 'hidden'});
    await terminal.evaluate(() => window.__sshSocket.onmessage({
      data: new TextEncoder().encode('\r\nhttps://login.tailscale.com/a/qa-').buffer,
    }));
    await terminal.locator('#tailscale-login').waitFor({state: 'hidden'});
    await terminal.evaluate(() => window.__sshSocket.onmessage({
      data: new TextEncoder().encode('test\r\n').buffer,
    }));
    await terminal.locator('#tailscale-login:not(.hidden)').waitFor();
    assert.equal(await terminal.locator('#tailscale-login').getAttribute('href'), 'https://login.tailscale.com/a/qa-test');
    await terminal.waitForTimeout(250);
    await terminal.screenshot({path: path.join(outputDir, 'terminal-tailscale-link.png'), fullPage: true});
    const linkScreen = await terminal.locator('.xterm-screen').boundingBox();
    await terminal.mouse.click(linkScreen.x + 32, linkScreen.y + 27);
    assert.equal(await terminal.evaluate(() => window.__openedTerminalLink?.location.href), 'https://login.tailscale.com/a/qa-test');
    await checkWidth(terminal, 'browser SSH mobile');
    await terminal.screenshot({path: path.join(outputDir, 'terminal-mobile.png'), fullPage: true});
    await terminal.setViewportSize({width: 320, height: 700});
    await checkWidth(terminal, 'browser SSH narrow mobile');
    await terminal.screenshot({path: path.join(outputDir, 'terminal-narrow-mobile.png'), fullPage: true});
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.keyboard.type('echo hello');
    await terminal.keyboard.press('Enter');
    const ssh = await terminal.evaluate(() => ({url: window.__sshUrl, protocols: window.__sshProtocols, frames: window.__sshFrames}));
    assert.match(ssh.url, /^wss:\/\//);
    assert.ok(ssh.protocols.includes('uccloud') && ssh.protocols.includes('csrf.test-csrf'));
    const typed = ssh.frames.filter(frame => frame.type === 'input').map(frame => frame.data).join('');
    assert.ok(typed.includes('echo hello'), `terminal did not forward typing: ${JSON.stringify(typed)}`);
    await terminal.context().grantPermissions(['clipboard-read', 'clipboard-write'], {origin: new URL(baseUrl).origin});
    await terminal.locator('#copy').waitFor({state:'hidden'});
    await terminal.evaluate(() => navigator.clipboard.writeText('default-shortcut-paste-123'));
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.locator('.xterm-helper-textarea').press('Control+V');
    await terminal.getByText('붙여넣었습니다.').waitFor();
    const defaultPaste = await terminal.evaluate(() => window.__sshFrames.filter(frame => frame.type === 'input').map(frame => frame.data).join(''));
    assert.ok(defaultPaste.includes('default-shortcut-paste-123'), 'default paste shortcut did not forward clipboard text');
    await terminal.locator('.xterm-helper-textarea').press('Control+C');
    await terminal.waitForFunction(() => window.__sshFrames.some(frame => frame.type === 'input' && frame.data === '\x03'));
    const screen = await terminal.locator('.xterm-screen').boundingBox();
    await terminal.mouse.move(screen.x + 3, screen.y + 10);
    await terminal.mouse.down();
    await terminal.mouse.move(screen.x + 120, screen.y + 10, {steps: 8});
    await terminal.mouse.up();
    await terminal.locator('#copy:not(.hidden)').waitFor();
    await terminal.screenshot({path: path.join(outputDir, 'terminal-copy-selected-mobile.png'), fullPage: true});
    await terminal.locator('#copy').click();
    await terminal.getByText('복사했습니다.').waitFor();
    const buttonCopiedText = await terminal.evaluate(() => navigator.clipboard.readText());
    assert.ok(buttonCopiedText.length > 0 && buttonCopiedText !== 'default-shortcut-paste-123', 'copy button did not copy selected text');
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.locator('.xterm-helper-textarea').press('Control+C');
    await terminal.getByText('복사했습니다.').waitFor();
    const copiedText = await terminal.evaluate(() => navigator.clipboard.readText());
    assert.ok(copiedText.length > 0 && copiedText !== 'default-shortcut-paste-123', `selected terminal text was not copied: ${copiedText}`);
    await terminal.locator('#shortcuts').click();
    await terminal.locator('#shortcut-dialog[open]').waitFor();
    await checkWidth(terminal, 'SSH shortcut settings narrow mobile');
    await terminal.screenshot({path: path.join(outputDir, 'terminal-shortcuts-mobile.png'), fullPage: true});
    assert.equal(await terminal.locator('[data-shortcut=copy]').textContent(), 'Ctrl + C');
    assert.equal(await terminal.locator('[data-shortcut=paste]').textContent(), 'Ctrl + V');
    await terminal.locator('[data-shortcut=copy]').click();
    await terminal.locator('[data-shortcut=copy]').press('Control+Alt+K');
    await terminal.getByText('저장했습니다.').waitFor();
    assert.equal(await terminal.locator('[data-shortcut=copy]').textContent(), 'Ctrl + Alt + K');
    await terminal.locator('[data-shortcut=paste]').click();
    await terminal.locator('[data-shortcut=paste]').press('Control+Alt+P');
    assert.equal(await terminal.locator('[data-shortcut=paste]').textContent(), 'Ctrl + Alt + P');
    await terminal.locator('[data-shortcut=copy]').click();
    await terminal.locator('[data-shortcut=copy]').press('Control+Alt+P');
    await terminal.getByText('복사와 붙여넣기에 같은 키를 쓸 수 없습니다.').waitFor();
    assert.equal(await terminal.locator('[data-shortcut=copy]').textContent(), 'Ctrl + Alt + K');
    await terminal.locator('[data-shortcut=copy]').press('Escape');
    const storedShortcuts = await terminal.evaluate(() => JSON.parse(localStorage.getItem('uccloud.terminal.shortcuts.v1')));
    assert.equal(storedShortcuts.copy.code, 'KeyK');
    assert.equal(storedShortcuts.paste.code, 'KeyP');
    await terminal.locator('#shortcut-dialog .shortcut-bottom button[type=submit]').click();
    await terminal.evaluate(() => navigator.clipboard.writeText('shortcut-paste-check-123'));
    await terminal.locator('.xterm-helper-textarea').focus();
    await terminal.locator('.xterm-helper-textarea').press('Control+Alt+P');
    await terminal.getByText('붙여넣었습니다.').waitFor();
    const shortcutPaste = await terminal.evaluate(() => window.__sshFrames.filter(frame => frame.type === 'input').map(frame => frame.data).join(''));
    assert.ok(shortcutPaste.includes('shortcut-paste-check-123'), 'custom paste shortcut did not forward clipboard text');
    await terminal.locator('.xterm-helper-textarea').press('Control+Alt+K');
    await terminal.getByText('복사할 글자를 먼저 선택해 주세요.').waitFor();
    await terminal.evaluate(() => navigator.clipboard.writeText('button-paste-check-123'));
    await terminal.locator('.xterm-helper-textarea').press('Control+Alt+P');
    await terminal.getByText('붙여넣었습니다.').waitFor();
    const buttonPaste = await terminal.evaluate(() => window.__sshFrames.filter(frame => frame.type === 'input').map(frame => frame.data).join(''));
    assert.ok(buttonPaste.includes('button-paste-check-123'), 'paste button did not forward clipboard text');
    await terminal.evaluate(() => navigator.clipboard.writeText('printf first\r\nprintf second'));
    await terminal.locator('.xterm-helper-textarea').press('Control+Alt+P');
    await terminal.waitForFunction(() => window.__sshFrames.some(frame => frame.type === 'input' && frame.data.includes('printf second')));
    const multilinePaste = await terminal.evaluate(() => window.__sshFrames.filter(frame => frame.type === 'input').map(frame => frame.data).join(''));
    assert.match(multilinePaste, /printf first\r?\n?printf second/, 'Windows multiline clipboard was not forwarded');
    assert.doesNotMatch(multilinePaste, /printf first\r\r\n?printf second/, 'Windows newline was doubled');
    await terminal.evaluate(() => navigator.clipboard.writeText('a'.repeat(8001)));
    await terminal.locator('.xterm-helper-textarea').press('Control+Alt+P');
    await terminal.getByText('내용이 너무 깁니다.').waitFor();
    const oversizedPaste = await terminal.evaluate(() => window.__sshFrames.filter(frame => frame.type === 'input').map(frame => frame.data).join(''));
    assert.equal(oversizedPaste, multilinePaste, 'oversized clipboard text was sent to SSH');
    await terminal.evaluate(() => navigator.clipboard.writeText('paste-check-123'));
    await terminal.locator('.xterm-helper-textarea').focus();
    // Headless CDP key presses send Ctrl+V as a key, not an OS clipboard paste event.
    await terminal.locator('.xterm-helper-textarea').evaluate(element => {
      const data = new DataTransfer();
      data.setData('text/plain', 'paste-check-123');
      element.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
    });
    await terminal.waitForTimeout(250);
    const pasteState = await terminal.evaluate(async () => ({
      input: window.__sshFrames.filter(frame => frame.type === 'input').map(frame => frame.data).join(''),
      clipboard: await navigator.clipboard.readText(),
      focused: document.activeElement?.className,
    }));
    assert.ok(pasteState.input.includes('paste-check-123'), `paste was not forwarded: ${JSON.stringify(pasteState)}`);
    await terminal.evaluate(() => window.__sshSocket.close());
    await terminal.locator('#tailscale-login').waitFor({state: 'hidden'});
    await terminal.locator('#copy').waitFor({state: 'hidden'});
    assert.equal(await terminal.locator('#paste').count(), 0);
    await terminal.locator('#connect-form:not(.hidden)').waitFor();
    assert.equal(await terminal.evaluate(() => document.activeElement === document.querySelector('#vm-password')), true, 'reconnect did not focus the password field');
    await terminal.locator('#vm-password').fill('fake-test-password');
    await terminal.locator('#connect').click();
    await terminal.locator('#terminal:not(.hidden)').waitFor();
    await terminal.evaluate(() => window.__sshSocket.onmessage({
      data: new TextEncoder().encode('\r\nhttps://login.tailscale.com.evil.example/a/fake\r\nhttps://example.com/other\r\n').buffer,
    }));
    assert.equal(await terminal.locator('#tailscale-login').isVisible(), false, 'untrusted URL showed a login button');
    await terminal.evaluate(() => window.__sshSocket.close(4401));
    await terminal.getByText('로그인이 만료됐습니다. 내 VM에서 다시 로그인해 주세요.').waitFor();
    await terminal.locator('#connect-form').waitFor({state: 'hidden'});
    await terminal.locator('#copy').waitFor({state: 'hidden'});
    assert.equal(await terminal.locator('#paste').count(), 0);
    await terminal.reload({waitUntil: 'domcontentloaded'});
    await terminal.locator('#shortcuts').click();
    assert.equal(await terminal.locator('[data-shortcut=copy]').textContent(), 'Ctrl + Alt + K');
    assert.equal(await terminal.locator('[data-shortcut=paste]').textContent(), 'Ctrl + Alt + P');
    await terminal.locator('#shortcut-reset').click();
    assert.equal(await terminal.locator('[data-shortcut=copy]').textContent(), 'Ctrl + C');
    assert.equal(await terminal.locator('[data-shortcut=paste]').textContent(), 'Ctrl + V');
    assert.equal(await terminal.evaluate(() => localStorage.getItem('uccloud.terminal.shortcuts.v1')), null);

    const guide = await browser.newPage({viewport: {width: 390, height: 844}});
    guide.on('pageerror', error => pageErrors.push(`guide: ${error.message}`));
    await useLocalStatic(guide);
    await guide.goto(`${baseUrl}/guide/funnel`, {waitUntil: 'domcontentloaded'});
    await guide.locator('#quick-publish').waitFor();
    await guide.getByText('UCcloud에서 만든 서버. Tailscale 가입이나 로그인은 필요 없습니다.').waitFor();
    assert.equal(await guide.locator('#keep-running-help').getAttribute('open'), null);
    await guide.locator('#quick-publish').screenshot({path: path.join(outputDir, 'guide-publish-default-mobile.png')});
    await guide.locator('#keep-running-help > summary').click();
    assert.equal(await guide.locator('#copy-keep-running').isDisabled(), true);
    await guide.locator('#site-start').fill('npm start');
    const persistentCommand = await guide.locator('#keep-running-command').textContent();
    assert.match(persistentCommand, /sudo loginctl enable-linger "\$USER"/);
    assert.match(persistentCommand, /systemctl --user restart uccloud-web\.service/);
    await guide.context().grantPermissions(['clipboard-read', 'clipboard-write'], {origin: new URL(baseUrl).origin});
    await guide.locator('#copy-keep-running').click();
    assert.equal((await guide.evaluate(() => navigator.clipboard.readText())).replace(/\r\n?/g, '\n'), persistentCommand);
    await checkWidth(guide, 'publishing guide mobile');
    const commandWidths = await guide.locator('.command-line-long pre').evaluateAll(elements => elements.map(element => ({visible: element.clientWidth, content: element.scrollWidth})));
    assert.ok(commandWidths.every(item => item.content <= item.visible + 1), `publishing command is clipped: ${JSON.stringify(commandWidths)}`);
    await guide.screenshot({path: path.join(outputDir, 'guide-mobile.png'), fullPage: true});
    await guide.locator('#quick-connect').screenshot({path: path.join(outputDir, 'guide-connect-mobile.png')});
    await guide.locator('#quick-publish').screenshot({path: path.join(outputDir, 'guide-publish-mobile.png')});
    await guide.setViewportSize({width: 320, height: 700});
    await checkWidth(guide, 'publishing guide narrow mobile');
    const narrowCommands = await guide.locator('.command-line-long pre').evaluateAll(elements => elements.map(element => ({visible: element.clientWidth, content: element.scrollWidth})));
    assert.ok(narrowCommands.every(item => item.content <= item.visible + 1), `narrow publishing command is clipped: ${JSON.stringify(narrowCommands)}`);
    sessionExpired = true;
    await student.evaluate(() => refreshCloud(true));
    await student.locator('#auth-view:not(.hidden)').waitFor();
    await student.getByText('로그인이 만료됐습니다. 다시 로그인해 주세요.').waitFor();
    assert.deepEqual(await student.evaluate(() => ({csrf:state.csrf, vmCount:state.instances.length})),
      {csrf:null,vmCount:0});
    assert.equal(await student.locator('.instance-card').count(), 0);
    assert.equal(await student.locator('#quota-summary').textContent(), '');
    assert.deepEqual(pageErrors, []);
    console.log('Visual smoke checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});

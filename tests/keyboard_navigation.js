/** Read-only keyboard journey. Student APIs are mocked; no account or VM is changed. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const vmId = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const vm = {id:vmId,name:'my-server',status:'ACTIVE',flavor:'small',vm_username:'student',
  addresses:[{address:'10.30.23.177',version:4}]};
const json = (route, value, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const errors = [];
  try {
    const login = await browser.newPage();
    login.on('pageerror', error => errors.push(error.message));
    await login.route('**/api/public-config', route => json(route,
      {operator_enabled:false,registration_code_required:true}));
    await login.route('**/api/me', route => json(route,{detail:'Login required'},401));
    await login.goto(baseUrl);
    await login.locator('#auth-view:not(.hidden)').waitFor();
    await login.locator('.tab[data-tab=register]').focus();
    await login.keyboard.press('Enter');
    assert.equal(await login.evaluate(() => document.activeElement?.getAttribute('name')), 'email');
    await login.keyboard.type('keyboard.student@example.invalid');
    assert.equal(await login.locator('#register-form input[name=username]').inputValue(), 'keyboard.student');
    assert.equal(await login.locator('#register-form input[name=team_name]').inputValue(), 'keyboard.student의 팀');
    await login.keyboard.press('Tab');
    assert.equal(await login.evaluate(() => document.activeElement?.getAttribute('name')), 'username');
    await login.keyboard.press('Tab');
    assert.equal(await login.evaluate(() => document.activeElement?.getAttribute('name')), 'team_name');
    await login.close();

    const student = await browser.newPage();
    student.on('pageerror', error => errors.push(error.message));
    await student.route('**/api/public-config', route => json(route,
      {operator_enabled:false,registration_code_required:true}));
    await student.route('**/api/me', route => json(route,
      {username:'student',status:'active',role:'member',team_owner:true,team_name:'Class Lab',
        team_slug:'class-lab',csrf_token:'keyboard-test'}));
    await student.route('**/api/instances', route => json(route,{instances:[vm]}));
    await student.route('**/api/quota', route => json(route,
      {instances:{in_use:1,limit:4},cores:{in_use:1,limit:8},ram:{in_use:2048,limit:16384}}));
    await student.route('**/api/catalog', route => json(route,
      {images:[{id:'d58be315-054e-49fb-bb25-14a5544adf39',name:'Ubuntu 24.04'}],
        flavors:[{id:'small',name:'small',vcpus:1,ram:2048,disk:20}]}));
    await student.route('**/api/team/requests', route => json(route,{requests:[]}));
    await student.goto(baseUrl);
    await student.locator('.instance-card').waitFor();
    await student.locator('#open-create').focus();
    await student.keyboard.press('Enter');
    await student.locator('#create-dialog[open]').waitFor();
    assert.equal(await student.evaluate(() => document.activeElement?.getAttribute('name')), 'vm_password');
    assert.equal(await student.locator('#create-form input[name=name]').inputValue(), 'vm-1');
    await student.keyboard.press('Escape');
    await student.locator('#create-dialog').waitFor({state:'hidden'});
    await student.close();

    const terminal = await browser.newPage();
    terminal.on('pageerror', error => errors.push(error.message));
    await terminal.route('**/api/me', route => json(route,{csrf_token:'keyboard-test'}));
    await terminal.route('**/api/instances', route => json(route,{instances:[vm]}));
    await terminal.goto(`${baseUrl}/terminal?vm=${vmId}`);
    await terminal.locator('#connect-form:not(.hidden)').waitFor();
    await terminal.locator('#shortcuts').focus();
    await terminal.keyboard.press('Enter');
    await terminal.locator('#shortcut-dialog[open]').waitFor();
    await terminal.locator('[data-shortcut=copy]').focus();
    await terminal.keyboard.press('Enter');
    await terminal.keyboard.press('Control+Alt+K');
    assert.equal(await terminal.locator('[data-shortcut=copy]').textContent(), 'Ctrl + Alt + K');
    await terminal.keyboard.press('Escape');
    await terminal.locator('#shortcut-dialog').waitFor({state:'hidden'});
    assert.deepEqual(errors, []);
    console.log('Keyboard-only signup, VM create dialog, and SSH shortcut checks passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});

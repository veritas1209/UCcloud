/** Read-only public UI audit. API responses are mocked; no account or VM is touched. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const axe = require(process.env.AXE_CORE_MODULE_PATH || 'axe-core');
const path = require('node:path');

const baseUrl = process.env.UI_BASE_URL || 'https://comsv-03.tail5819db.ts.net';
const vmId = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const vm = {id:vmId,name:'my-server',status:'ACTIVE',flavor:'small',vm_username:'student',
  addresses:[{address:'10.30.23.177',version:4}]};
const json = (route, value, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
async function useLocalStatic(page) {
  if (!process.env.UI_LOCAL_STATIC_DIR) return;
  const files = {'/':'index.html','/guide/funnel':'funnel-guide.html','/terminal':'terminal.html',
    '/assets/app.js':'app.js','/assets/styles.css':'styles.css',
    '/assets/funnel-guide.js':'funnel-guide.js','/assets/funnel-guide.css':'funnel-guide.css',
    '/assets/terminal.js':'terminal.js','/assets/terminal.css':'terminal.css'};
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    const file = files[url.pathname];
    return url.origin === new URL(baseUrl).origin && file
      ? route.fulfill({path:path.join(process.env.UI_LOCAL_STATIC_DIR,file)}) : route.continue();
  });
}

async function audit(page, label) {
  await page.evaluate(axe.source);
  const result = await page.evaluate(() => window.axe.run(document, {
    runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']},
  }));
  const violations = result.violations.map(item => ({id:item.id,impact:item.impact,
    nodes:item.nodes.map(node => node.target.join(' '))}));
  console.log(JSON.stringify({label,violations}));
  if (violations.length) throw new Error(`${label}: ${violations.length} accessibility violations`);
  return violations;
}

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const login = await browser.newPage();
    await useLocalStatic(login);
    await login.route('**/api/public-config', route => json(route,
      {operator_enabled:false,registration_code_required:true}));
    await login.route('**/api/me', route => json(route,{detail:'Login required'},401));
    await login.goto(baseUrl);
    await login.locator('#auth-view:not(.hidden)').waitFor();
    await audit(login, 'login');
    await login.locator('.tab[data-tab=register]').click();
    await audit(login, 'register');
    await login.close();

    const student = await browser.newPage();
    await useLocalStatic(student);
    await student.route('**/api/public-config', route => json(route,
      {operator_enabled:false,registration_code_required:true}));
    await student.route('**/api/me', route => json(route,
      {username:'student',status:'active',role:'member',team_owner:true,team_name:'Class Lab',
        team_slug:'class-lab',csrf_token:'a11y-test'}));
    await student.route('**/api/instances', route => json(route,{instances:[vm]}));
    await student.route('**/api/quota', route => json(route,
      {instances:{in_use:1,limit:4},cores:{in_use:1,limit:8},ram:{in_use:2048,limit:16384}}));
    await student.route('**/api/team/requests', route => json(route,{requests:[]}));
    await student.route('**/api/catalog', route => json(route,
      {images:[{id:'d58be315-054e-49fb-bb25-14a5544adf39',name:'Ubuntu 24.04'}],
        flavors:[{id:'small',name:'small',vcpus:1,ram:2048,disk:20}]}));
    await student.goto(baseUrl);
    await student.locator('.instance-card').waitFor();
    await audit(student, 'dashboard');
    await student.locator('details[data-vm-manage] > summary').click();
    await audit(student, 'VM management menu');
    await student.locator('#open-create').click();
    await student.locator('#create-dialog[open]').waitFor();
    await audit(student, 'create dialog');
    await student.close();

    const guide = await browser.newPage();
    await useLocalStatic(guide);
    await guide.goto(`${baseUrl}/guide/funnel`);
    await audit(guide, 'guide');
    await guide.locator('#keep-running-help > summary').click();
    await audit(guide, 'guide optional help');
    await guide.locator('#site-start').fill('npm start');
    await guide.locator('.start-command-preview > summary').click();
    await audit(guide, 'guide command preview');
    await guide.close();

    const terminal = await browser.newPage();
    await useLocalStatic(terminal);
    await terminal.route('**/api/me', route => json(route,{csrf_token:'a11y-test'}));
    await terminal.route('**/api/instances', route => json(route,{instances:[vm]}));
    await terminal.goto(`${baseUrl}/terminal?vm=${vmId}`);
    await terminal.locator('#connect-form:not(.hidden)').waitFor();
    await audit(terminal, 'browser SSH');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode=1;});

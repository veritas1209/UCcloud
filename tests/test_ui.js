const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');
const page = fs.readFileSync('static/index.html', 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
assert.match(page, /class="nav-item active" data-section="instances"/);
assert.doesNotMatch(page, /data-section="overview"/);
assert.match(page, /id="quota-summary"/);
assert.match(page, /id="cloud-status"/);
assert.match(page, /id="section-instances" class="page-section"/);
assert.match(page, /id="admin-registration-code"/);
assert.match(page, /<button class="primary" type="submit">만들고 접속<\/button>/);
assert.match(page, /data-tab="login" aria-pressed="true"/);
assert.match(page, /data-tab="register" aria-pressed="false"/);
assert.match(source, /location\.assign\(`\/terminal\?vm=\$\{encodeURIComponent\(created\.id\)\}&wait=1`\)/);
const messages = [];
const button = { disabled: false, textContent: '가입 요청' };
const form = { querySelector: () => button };
const context = {
  $: () => ({ classList: { add: () => {} } }),
  FormData: class { *[Symbol.iterator]() { yield ['username', 'test-user']; } },
  authMessage: (...args) => messages.push(args),
  api: async () => ({ message: '접수했습니다.' }),
};
vm.createContext(context);
vm.runInContext(section('function apiErrorMessage', 'function authMessage'), context);
vm.runInContext(section('async function submitAuth', 'async function api'), context);

assert.equal(context.apiErrorMessage({ detail: [{ loc: ['body', 'email'], msg: 'bad', type: 'value_error' }] }, 422, '/api/auth/register'), '이메일을 확인하세요.');
assert.equal(context.apiErrorMessage({ detail: 'Invalid username or password' }, 401, '/api/auth/login'), '아이디/이메일 또는 비밀번호가 올바르지 않습니다.');
assert.equal(context.apiErrorMessage({ detail: 'Username or email already exists' }, 409, '/api/auth/register'), '이미 사용 중인 아이디 또는 이메일입니다.');
assert.equal(context.apiErrorMessage({ detail: 'Cloud service is temporarily unavailable' }, 502, '/api/instances/123/action'), '클라우드 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.');
assert.equal(context.apiErrorMessage({ detail: 'Service Unavailable' }, 503, '/api/quota'), '클라우드 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.');
assert.equal(context.apiErrorMessage({}, 504, '/api/catalog'), '클라우드 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.');
assert.equal(context.apiErrorMessage({detail: 'Team resources are not ready'}, 409, '/api/instances'), '작업 공간을 준비 중입니다. 잠시 후 다시 확인해 주세요.');
assert.equal(context.apiErrorMessage({detail: 'Invalid CSRF token'}, 403, '/api/instances'), '화면을 새로고침한 뒤 다시 시도해 주세요.');
assert.equal(context.apiErrorMessage({detail: 'Invalid VM password'}, 422, '/api/instances'), 'VM 접속 비밀번호를 확인해 주세요.');
assert.equal(context.apiErrorMessage({detail: 'Instance not found or not running'}, 404, '/api/instances/123/console'), 'VM이 꺼져 있거나 상태가 바뀌었습니다. 목록을 확인해 주세요.');
assert.equal(context.apiErrorMessage({detail: 'Network is not owned by the team'}, 422, '/api/instances'), '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
assert.equal(context.apiErrorMessage({detail: '__proto__'}, 422, '/api/instances'), '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
assert.equal(context.apiErrorMessage({detail: '가입 코드를 확인하세요.'}, 403, '/api/auth/register'), '가입 코드를 확인하세요.');

const nodes = Object.fromEntries(['#instances-list', '#quota-summary'].map(key => [key, { innerHTML: '', textContent: '' }]));
const authSelector = context.$;
context.$ = selector => nodes[selector];
context.escapeHtml = value => String(value ?? '');
context.pendingInstanceActions = new Map();
context.openingConsoleIds = new Set();
context.state = {instances: []};
vm.runInContext(section('function renderQuota', 'function vmView'), context);
context.renderQuota({instances:{in_use:1,limit:4},cores:{in_use:1,limit:8},ram:{in_use:2048,limit:16384}});
assert.equal(nodes['#quota-summary'].textContent, 'VM 1개 / 최대 4개');
context.state.instances = [{name:'first'}, {name:'second'}];
context.renderQuota({instances:{in_use:1,limit:4}});
assert.equal(nodes['#quota-summary'].textContent, 'VM 2개 / 최대 4개');
context.renderQuota(null);
assert.equal(nodes['#quota-summary'].textContent, 'VM 2개');
vm.runInContext(section('function vmView', 'function quotaAllows'), context);
const active = { id: 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205', name: 'test', status: 'ACTIVE', addresses: [], flavor: 'small', flavor_details: {vcpus: 1, ram: 2048, disk: 20} };
context.renderInstances([active]);
assert.match(nodes['#instances-list'].innerHTML, /IP를 기다리는 중입니다/);
assert.match(nodes['#instances-list'].innerHTML, /CPU 1 · 메모리 2GB · 저장공간 20GB/);
assert.match(nodes['#instances-list'].innerHTML, /data-console/);
assert.match(nodes['#instances-list'].innerHTML, /data-terminal/);
assert.match(nodes['#instances-list'].innerHTML, /<details class="vm-manage" data-vm-manage=/);
assert.match(nodes['#instances-list'].innerHTML, /<summary>관리<\/summary>/);
assert.match(nodes['#instances-list'].innerHTML, /data-site/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /data-action="start"/);
context.renderInstances([]);
assert.match(nodes['#instances-list'].innerHTML, /아직 VM이 없습니다/);
context.renderInstances([{ ...active, status: 'BUILD' }]);
assert.match(nodes['#instances-list'].innerHTML, /data-terminal/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /data-console/);
context.renderInstances([{ ...active, addresses: [{address: '10.30.23.177', version: 4}] }]);
assert.match(nodes['#instances-list'].innerHTML, /data-terminal/);
assert.match(nodes['#instances-list'].innerHTML, /<details class="vm-technical" data-vm-details=/);
assert.match(nodes['#instances-list'].innerHTML, /내부 주소/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /접속할 수 있습니다/);
assert.match(nodes['#instances-list'].innerHTML, /href="\/terminal\?vm=c6e69a7b-41bf-4bd4-bbac-1eed7aa26205" target="_blank" rel="noopener noreferrer"/);
assert.doesNotMatch(source, /window\.open\(`\/terminal/);
assert.match(nodes['#instances-list'].innerHTML, /data-console/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /<p>c6e69a7b-41bf/);
context.pendingInstanceActions.set(active.id, {label:'재부팅 중…', status:'ACTIVE', inFlight:true, until:0});
context.renderInstances([{ ...active, addresses: [{address: '10.30.23.177', version: 4}] }]);
assert.match(nodes['#instances-list'].innerHTML, /재부팅 중…/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /data-action="reboot"/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /data-terminal/);
context.pendingInstanceActions.delete(active.id);
context.renderInstances([{ ...active, status: 'SHUTOFF' }]);
assert.match(nodes['#instances-list'].innerHTML, /data-action="start"/);
assert.match(nodes['#instances-list'].innerHTML, /켜고 접속/);
assert.match(nodes['#instances-list'].innerHTML, />켜기만<\/button>/);
assert.match(nodes['#instances-list'].innerHTML, /&start=1/);
assert.doesNotMatch(nodes['#instances-list'].innerHTML, /data-console/);
context.$ = authSelector;

(async () => {
  const apiContext = {
    state: {csrf: null},
    fetch: async () => ({ok: false, status: 401, json: async () => ({detail: 'Login required'})}),
    apiErrorMessage: () => '로그인이 필요합니다.',
  };
  vm.createContext(apiContext);
  vm.runInContext(section('async function api(', 'function showView'), apiContext);
  await assert.rejects(apiContext.api('/api/me'), error => error.status === 401);

  const bootMessages = [];
  const bootViews = [];
  const bootContext = {
    state: {me: null, csrf: null},
    api: async () => { const error = new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.'); error.status = 503; throw error; },
    showView: view => bootViews.push(view),
    authMessage: message => bootMessages.push(message),
  };
  vm.createContext(bootContext);
  vm.runInContext(section('async function bootstrap()', 'async function refreshCloud'), bootContext);
  await bootContext.bootstrap();
  assert.equal(bootViews.at(-1), 'auth-view');
  assert.match(bootMessages.at(-1), /서버에 연결할 수 없습니다/);
  bootContext.api = async () => { const error = new Error('로그인이 필요합니다.'); error.status = 401; throw error; };
  await bootContext.bootstrap();
  assert.equal(bootMessages.at(-1), '');

  const event = { currentTarget: form, preventDefault: () => {} };
  const pending = context.submitAuth(event, '/api/auth/register', '가입 요청 중…', async data => messages.push([data.message, true]));
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, '가입 요청 중…');
  await pending;
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, '가입 요청');
  assert.deepEqual(messages.at(-1), ['접수했습니다.', true]);

  context.api = async () => { throw new Error('가입 오류'); };
  await context.submitAuth(event, '/api/auth/register', '가입 요청 중…', async () => {});
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, '가입 요청');
  assert.deepEqual(messages.at(-1), ['가입 오류']);

  let resolveCatalog, resolveQuota;
  let dialogOpened = false;
  let passwordFocused = false;
  const classes = () => { const values = new Set(['hidden']); return {add: key => values.add(key), remove: key => values.delete(key), contains: key => values.has(key)}; };
  const select = () => ({value: '', get innerHTML() {return this.html || '';}, set innerHTML(html) {this.html = html; this.value = html.match(/value="([^"]+)"/)?.[1] || '';}});
  const imageSelect = select();
  const flavorSelect = select();
  const nameInput = {value: ''};
  const passwordInput = {focus() {passwordFocused = true;}};
  const vmUserInput = {value: 'student'};
  const options = {open: true};
  const optionsLabel = {textContent: ''};
  const flavorDisk = {textContent: ''};
  const createButton = {disabled: false, textContent: '새 VM', addEventListener(type, callback) {this.onClick = callback;}};
  const submitButton = {disabled: false, textContent: '만들고 접속'};
  const createStatus = {textContent: '', classList: classes()};
  const retryButton = {classList: classes(), addEventListener(type, callback) {this.onClick = callback;}};
  const createDialog = {open: false, showModal() {dialogOpened = true; this.open = true;}};
  const createContext = {
    $: selector => selector === '#open-create' ? createButton : selector === '#create-dialog' ? createDialog : selector === '#create-form button[type=submit]' ? submitButton : selector === '#retry-create-setup' ? retryButton : selector === '#create-status' ? createStatus : selector === '#create-options' ? options : selector === '#create-options-label' ? optionsLabel : selector === '#flavor-disk' ? flavorDisk : selector.includes('image_id') ? imageSelect : selector.includes('flavor_id') ? flavorSelect : selector.includes('vm_username') ? vmUserInput : selector.includes('vm_password') ? passwordInput : nameInput,
    state: {catalog: null, instances: [{name: 'vm-1'}]},
    api: path => new Promise(resolve => {if (path === '/api/quota') resolveQuota = resolve; else resolveCatalog = resolve;}),
    escapeHtml: value => String(value),
    renderQuota: () => {},
    renderSoftwarePicker: () => {},
    updateSoftwareHint: () => {},
  };
  vm.createContext(createContext);
  vm.runInContext(section('function quotaAllows', 'async function loadTeamRequests'), createContext);
  vm.runInContext(section('let createPreparationId = 0;', "$('[data-close]').addEventListener"), createContext);
  const opening = createButton.onClick({currentTarget: createButton});
  assert.equal(dialogOpened, true);
  assert.equal(passwordFocused, true);
  assert.equal(submitButton.disabled, true);
  assert.equal(submitButton.textContent, '준비 중…');
  resolveQuota({instances:{limit:4,in_use:1},cores:{limit:8,in_use:1},ram:{limit:16384,in_use:2048}});
  resolveCatalog({images:[{id:'image-a',name:'Ubuntu'}],flavors:[
    {id:'small',name:'small',vcpus:1,ram:2048,disk:20},
    {id:'large',name:'large',vcpus:8,ram:16384,disk:80},
  ]});
  await opening;
  assert.equal(submitButton.disabled, false);
  assert.equal(createStatus.classList.contains('hidden'), true);
  assert.equal(nameInput.value, 'vm-2');
  assert.equal(options.open, false);
  assert.match(optionsLabel.textContent, /메모리 2GB · ID student/);
  assert.equal(flavorDisk.textContent, '저장공간 20GB');
  createContext.state.instances.push({name: 'vm-2'});
  assert.equal(createContext.suggestedVmName(), 'vm-3');
  assert.match(flavorSelect.innerHTML, /value="small"/);
  assert.doesNotMatch(flavorSelect.innerHTML, /value="large"/);
  assert.equal(createButton.disabled, false);
  assert.equal(createButton.textContent, '새 VM');
  dialogOpened = false;
  createDialog.open = false;
  createContext.api = async () => ({instances:{limit:4,in_use:4},cores:{limit:8,in_use:1},ram:{limit:16384,in_use:2048}});
  await createButton.onClick({currentTarget: createButton});
  assert.equal(dialogOpened, true);
  assert.match(createStatus.textContent, /VM을 더 만들 수 없습니다/);
  assert.equal(submitButton.disabled, true);
  assert.equal(retryButton.classList.contains('hidden'), false);
  assert.equal(createButton.disabled, false);

  let resolveAction;
  let actionCalls = 0;
  const actionMessages = [];
  const activeVm = {...active, addresses: [{address: '10.30.23.177', version: 4}]};
  context.$ = selector => nodes[selector];
  context.state = {instances: [activeVm]};
  context.api = () => {actionCalls++; return new Promise(resolve => {resolveAction = resolve;});};
  context.toast = message => actionMessages.push(message);
  context.refreshCloud = () => {};
  context.setTimeout = () => 1;
  vm.runInContext(section('async function handleInstanceAction', "$('#instances-list').addEventListener('click', handleInstanceAction)"), context);
  const actionButton = {disabled: false, dataset: {id: active.id, action: 'reboot'}, closest() {return this;}};
  const acting = context.handleInstanceAction({target: actionButton});
  assert.equal(actionCalls, 1);
  assert.match(nodes['#instances-list'].innerHTML, /재부팅 중…/);
  await context.handleInstanceAction({target: actionButton});
  assert.equal(actionCalls, 1);
  resolveAction({});
  await acting;
  assert.equal(context.pendingInstanceActions.get(active.id).inFlight, false);
  assert.match(actionMessages.at(-1), /다시 시작/);
  context.pendingInstanceActions.clear();
  let resolveConsole;
  let opened = 0;
  const consoleTab = {opener: 'previous', location: {href: ''}, close() {}};
  context.window = {open() {opened++; return consoleTab;}};
  context.api = () => new Promise(resolve => {resolveConsole = resolve;});
  const consoleButton = {disabled: false, dataset: {id: active.id, console: ''}, closest() {return this;}};
  const openingConsole = context.handleInstanceAction({target: consoleButton});
  assert.equal(opened, 1);
  assert.match(nodes['#instances-list'].innerHTML, /여는 중…/);
  await context.handleInstanceAction({target: consoleButton});
  assert.equal(opened, 1);
  resolveConsole({url: '/console/example'});
  await openingConsole;
  assert.equal(consoleTab.opener, null);
  assert.equal(consoleTab.location.href, '/console/example');
  assert.equal(context.openingConsoleIds.size, 0);
  const stoppedVm = {...activeVm, status: 'SHUTOFF'};
  context.state.instances = [stoppedVm];
  const definiteError = new Error('작업 실패');
  definiteError.status = 400;
  context.api = async () => {throw definiteError;};
  const startButton = {disabled: false, dataset: {id: active.id, action: 'start'}, closest() {return this;}};
  await context.handleInstanceAction({target: startButton});
  assert.equal(context.pendingInstanceActions.has(active.id), false);
  assert.match(nodes['#instances-list'].innerHTML, /data-action="start"/);
  assert.equal(actionMessages.at(-1), '작업 실패');
  const uncertainError = new Error('클라우드 연결 오류');
  uncertainError.status = 502;
  context.api = async () => {throw uncertainError;};
  await context.handleInstanceAction({target: startButton});
  assert.equal(context.pendingInstanceActions.get(active.id).inFlight, false);
  assert.match(nodes['#instances-list'].innerHTML, /상태 확인 중…/);
  assert.match(actionMessages.at(-1), /VM 상태를 자동으로 확인합니다/);
  await context.handleInstanceAction({target: startButton});
  assert.match(nodes['#instances-list'].innerHTML, /상태 확인 중…/);
  context.pendingInstanceActions.clear();
  context.confirm = () => true;
  const deleteButton = {disabled: false, dataset: {id: active.id, name: 'test', delete: ''}, closest() {return this;}};
  await context.handleInstanceAction({target: deleteButton});
  assert.match(nodes['#instances-list'].innerHTML, /상태 확인 중…/);
  context.pendingInstanceActions.clear();
  context.pendingInstanceActions.set(active.id, {label:'정지 중…', status:'ACTIVE', inFlight:false, until:Date.now() + 15000});
  context.renderInstances([stoppedVm]);
  assert.equal(context.pendingInstanceActions.has(active.id), false);
  console.log('UI flow checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

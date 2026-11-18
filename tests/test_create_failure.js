const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');

async function attempt(error, uncertain = false) {
  let submit, closed = false, createCalls = 0, reconciled = 0;
  const node = () => ({textContent: '', style: {}, classList: {add() {}, remove() {}}, setAttribute() {}});
  const doc = {title: '', body: {...node(), replaceChildren(...children) {this.children = children;}}, createElement: node};
  const tab = {document: doc, closed: false, close() {closed = true;}};
  const button = {disabled: false, textContent: '만들고 접속'};
  const form = {querySelector: () => button};
  const status = node();
  const dialog = {open: true};
  const elements = {'#create-form': {addEventListener: (_, fn) => {submit = fn;}}, '#create-status': status, '#create-dialog': dialog};
  const context = {
    $: key => elements[key], window: {open: () => tab}, location: {origin: 'https://cloud.example'},
    FormData: class { *[Symbol.iterator]() {yield ['vm_username', 'student']; yield ['vm_password', 'test-password'];} },
    selectedSoftware: [{id: 'node', version: '24'}], pendingCreateRequestId: null,
    crypto: {randomUUID: () => 'request-id'}, AbortController, setTimeout: () => 1, clearTimeout() {},
    api: async (_, options) => {createCalls++; assert.ok(options.signal); throw error;},
    findCreatedVm: async id => {assert.equal(id, 'request-id'); reconciled++; return null;}, toast() {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function showCreateFailure'), source.indexOf('async function handleInstanceAction')), context);
  await submit({preventDefault() {}, currentTarget: form});
  assert.equal(createCalls, 1);
  assert.equal(closed, false);
  assert.equal(button.disabled, false);
  assert.equal(doc.body.children[2].href, 'https://cloud.example/');
  assert.equal(tab.opener, null);
  if (uncertain) {
    assert.equal(reconciled, 1);
    assert.equal(context.pendingCreateRequestId, 'request-id');
    assert.match(doc.body.children[1].textContent, /중복 생성/);
  } else {
    assert.equal(reconciled, 0);
    assert.equal(context.pendingCreateRequestId, null);
    assert.equal(doc.body.children[1].textContent, error.message);
    assert.equal(status.textContent, error.message);
  }
}

(async () => {
  await attempt(Object.assign(new Error('초기 설정 데이터가 너무 큽니다.'), {status: 422}));
  await attempt(Object.assign(new Error('<img src=x onerror=alert(1)>'), {status: 409}));
  await attempt(new Error('서버에 연결할 수 없습니다.'), true);
  console.log('Create rejection, safe text rendering and uncertain-result reconciliation passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

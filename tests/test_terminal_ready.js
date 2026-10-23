const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/terminal.js', 'utf8');
const vmId = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    const classes = new Set(['hidden']);
    nodes.set(id, {
      value: '', textContent: '', disabled: false,
      classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)},
      addEventListener() {}, querySelectorAll() { return []; }, focus() {},
    });
  }
  return nodes.get(id);
}
const phases = [
  {status: 'BUILD', addresses: []},
  {status: 'ACTIVE', addresses: []},
  {status: 'ACTIVE', addresses: []},
  {status: 'ACTIVE', addresses: [{address: '10.30.23.177', version: 4}], created_at: new Date().toISOString()},
];
let phase = 0;
let now = 1700000000000;
const timers = [];
const context = {
  URL, URLSearchParams, TextDecoder,
  Date: class extends Date { static now() { return now; } },
  document: {getElementById: node},
  location: {search: `?vm=${vmId}`},
  window: {addEventListener() {}},
  Terminal: class { constructor() { this.options = {}; } loadAddon() {} onData() {} onSelectionChange() {} clearSelection() {} attachCustomKeyEventHandler() {} },
  FitAddon: {FitAddon: class {}},
  WebLinksAddon: {WebLinksAddon: class {}},
  ResizeObserver: class {observe() {}},
  setTimeout: fn => {timers.push(fn); return timers.length;},
  clearTimeout() {},
  fetch: async path => {
    if (path === '/api/me') return {ok: true, json: async () => ({csrf_token: 'test'})};
    if (path === '/api/instances') {
      const state = phases[Math.min(phase++, phases.length - 1)];
      return {ok: true, json: async () => ({instances: [{id: vmId, name: 'test-vm', vm_username: 'student', ...state}]})};
    }
    throw new Error(`Unexpected fetch: ${path}`);
  },
};
vm.createContext(context);
vm.runInContext(source, context);

(async () => {
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  assert.match(node('status').textContent, /자동으로 다시 확인/);
  assert.equal(node('connect-form').classList.contains('hidden'), true);
  assert.equal(timers.length, 1);
  timers.shift()();
  await flush();
  assert.match(node('status').textContent, /IP를 기다리는 중/);
  assert.equal(node('connect-form').classList.contains('hidden'), true);
  assert.equal(timers.length, 1);
  now += 301000;
  timers.shift()();
  await flush();
  assert.match(node('status').textContent, /준비가 오래 걸리고 있습니다/);
  assert.match(node('status').textContent, /자동으로 다시 확인/);
  assert.equal(timers.length, 1);
  timers.shift()();
  await flush();
  assert.equal(node('connect-form').classList.contains('hidden'), false);
  assert.equal(node('vm-user').value, 'student');
  assert.match(node('status').textContent, /비밀번호를 넣고 연결하세요/);
  assert.equal(timers.length, 0);
  console.log('Browser SSH readiness checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

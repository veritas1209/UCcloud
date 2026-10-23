const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/terminal.js', 'utf8');
const id = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const nodes = new Map();
function node(name) {
  if (!nodes.has(name)) {
    const classes = new Set(['hidden']);
    nodes.set(name, {
      value: '', textContent: '', disabled: false,
      classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
      addEventListener() {}, querySelectorAll() { return []; }, focus() {},
    });
  }
  return nodes.get(name);
}
const timers = [];
const starts = [];
let polls = 0;
const context = {
  URL, URLSearchParams, TextDecoder,
  document: {getElementById: node},
  location: {search: `?vm=${id}&start=1`},
  window: {addEventListener() {}},
  Terminal: class { constructor() { this.options = {}; } loadAddon() {} onData() {} onSelectionChange() {} clearSelection() {} attachCustomKeyEventHandler() {} },
  FitAddon: {FitAddon: class {}}, WebLinksAddon: {WebLinksAddon: class {}},
  ResizeObserver: class {observe() {}},
  setTimeout: fn => {timers.push(fn); return timers.length;}, clearTimeout() {},
  fetch: async (path, options) => {
    if (path === '/api/me') return {ok: true, json: async () => ({csrf_token: 'test-csrf'})};
    if (path === '/api/instances') {
      const phase = polls++;
      const status = phase < 2 ? 'SHUTOFF' : 'ACTIVE';
      return {ok: true, json: async () => ({instances: [{
        id, name: 'stopped-vm', vm_username: 'student', status,
        addresses: phase >= 3 ? [{address: '10.30.23.177', version: 4}] : [],
      }]})};
    }
    if (path === `/api/instances/${id}/action`) {
      starts.push(options);
      return {ok: true, status: 202};
    }
    throw new Error(`Unexpected fetch: ${path}`);
  },
};
vm.createContext(context);
vm.runInContext(source, context);

(async () => {
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  assert.equal(starts.length, 1);
  assert.equal(starts[0].method, 'POST');
  assert.equal(starts[0].headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(JSON.parse(starts[0].body).action, 'start');
  assert.match(node('status').textContent, /VM을 켜는 중/);
  assert.equal(node('vnc').classList.contains('hidden'), true);
  assert.equal(timers.length, 1);
  timers.shift()();
  await flush();
  assert.equal(starts.length, 1, 'polling must not send a second start request');
  assert.equal(timers.length, 1);
  timers.shift()();
  await flush();
  assert.equal(node('connect-form').classList.contains('hidden'), true);
  assert.equal(node('vnc').classList.contains('hidden'), false, 'console should be available before IP assignment');
  assert.match(node('status').textContent, /IP를 기다리는 중/);
  assert.equal(timers.length, 1);
  timers.shift()();
  await flush();
  assert.equal(node('connect-form').classList.contains('hidden'), false);
  assert.equal(node('vnc').classList.contains('hidden'), false);
  assert.equal(node('vm-user').value, 'student');
  assert.equal(starts.length, 1);
  console.log('Stopped VM one-click connection checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

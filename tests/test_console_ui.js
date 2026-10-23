const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/console.js', 'utf8').replace(/^import RFB from .*;\r?\n/, '');
const listeners = {};
const keys = [];
const elements = {
  'connection-status': { textContent: '' },
  'text-input': { value: 'echo hello' },
  'send-text': { disabled: true },
  'tailscale-key': { value: '', focus() {} },
  'send-tailscale': { disabled: true },
  'tailscale-form': { hidden: true, addEventListener: (name, handler) => { listeners.keySubmit = handler; } },
  'show-tailscale': { setAttribute() {}, addEventListener: (name, handler) => { listeners.toggle = handler; } },
  'classic-console': { href: '' },
  'text-form': { addEventListener: (name, handler) => { listeners.textSubmit = handler; } },
  screen: {},
};
let socketUrl;
class RFB {
  constructor(target, url) { socketUrl = url; }
  addEventListener(name, handler) { listeners[name] = handler; }
  sendKey(keysym) { keys.push(keysym); }
}
const context = {
  RFB,
  URL,
  URLSearchParams,
  document: { getElementById: id => elements[id] },
  window: { location: { search: '?path=%3Ftoken%3Dabc-123', origin: 'https://example.ts.net:8443', protocol: 'https:' } },
  setTimeout: callback => callback(),
};
vm.runInNewContext(source, context);
assert.equal(socketUrl, 'wss://example.ts.net:8443/?token=abc-123');
assert.match(elements['classic-console'].href, /vnc_lite.html/);
listeners.connect();
assert.equal(elements['send-text'].disabled, false);
assert.equal(elements['send-tailscale'].disabled, false);

(async () => {
  await listeners.textSubmit({ preventDefault() {} });
  assert.deepEqual(keys.slice(0, 4), [101, 99, 104, 111]);
  assert.equal(keys.at(-1), 0xff0d);
  assert.equal(elements['text-input'].value, '');
  elements['text-input'].value = '한글';
  await listeners.textSubmit({ preventDefault() {} });
  assert.equal(keys.at(-1), 0xff0d);
  assert.match(elements['connection-status'].textContent, /영문 명령어/);
  elements['text-input'].value = 'sudo tailscale up --auth-key=tskey-auth-test12345';
  await listeners.textSubmit({ preventDefault() {} });
  assert.match(elements['connection-status'].textContent, /Tailscale 연결 칸/);
  listeners.toggle();
  assert.equal(elements['tailscale-form'].hidden, false);
  elements['tailscale-key'].value = 'tskey-auth-abcDEF123456';
  const start = keys.length;
  await listeners.keySubmit({ preventDefault() {} });
  const sent = keys.slice(start);
  const enter = sent.indexOf(0xff0d);
  assert.equal(String.fromCodePoint(...sent.slice(0, enter)), 'read -rs UC_TS_KEY; echo; sudo tailscale up --auth-key="$UC_TS_KEY"; unset UC_TS_KEY');
  assert.equal(String.fromCodePoint(...sent.slice(enter + 1, -1)), 'tskey-auth-abcDEF123456');
  assert.equal(elements['tailscale-key'].value, '');
  console.log('Console text-entry checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

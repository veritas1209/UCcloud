const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/funnel-guide.js', 'utf8');
const html = fs.readFileSync('static/funnel-guide.html', 'utf8');
assert.match(html, /<b>내 VM → 접속<\/b>을 누르고/);
assert.match(html, /<details id="keep-running-help" class="keep-running"><summary>사이트를 계속 켜두려면<\/summary>/);
assert.doesNotMatch(html, /login\.tailscale\.com|사진 추가 예정|photo-slot/);
assert.match(html, /주소를 아는 누구나 볼 수 있습니다/);
assert.match(html, /최대 10분/);
assert.match(html, /pc-connect\.png/);
assert.match(html, /pc-files\.png/);
assert.match(html, /pc-publish\.png/);
const ids = ['connect-command', 'keep-running-command', 'funnel-command', 'status-command', 'ip-command'];
assert.match(html, /data-copy-command="keep-running-command"/);
const nodes = Object.fromEntries(ids.map(id => [id, {textContent: id === 'connect-command' ? 'sudo tailscale up' : id === 'status-command' ? 'sudo tailscale funnel status' : id === 'ip-command' ? 'tailscale ip -4' : ''}]));
nodes['site-port'] = {value: '3000', addEventListener(type, callback) { this.onInput = callback; }};
nodes['site-start'] = {value: '', addEventListener(type, callback) { this.onInput = callback; }};
const copied = [];
let failNextCopy = false;
const preview = {open: false};
const buttons = ids.map(id => ({dataset: {copyCommand: id}, textContent: '복사', disabled: false, addEventListener(type, callback) { this.onClick = callback; }}));
const buttonFor = id => buttons.find(button => button.dataset.copyCommand === id);
nodes['copy-funnel'] = buttonFor('funnel-command');
nodes['copy-keep-running'] = buttonFor('keep-running-command');
const context = {
  document: {getElementById: id => nodes[id], querySelector: () => preview, querySelectorAll: () => buttons},
  navigator: {clipboard: {writeText: async value => {
    if (failNextCopy) { failNextCopy = false; throw new Error('Clipboard denied'); }
    copied.push(value);
  }}},
  setTimeout: () => 1,
  clearTimeout() {},
};
vm.createContext(context);
vm.runInContext(source, context);
if (process.argv.includes('--qa-command-base64')) {
  nodes['site-start'].value = 'python3 -m http.server 18781 --bind 127.0.0.1';
  nodes['site-start'].onInput();
  process.stdout.write(Buffer.from(nodes['keep-running-command'].textContent).toString('base64'));
  process.exit(0);
}

(async () => {
  assert.equal(nodes['funnel-command'].textContent, 'sudo tailscale funnel --bg --https=443 3000');
  assert.equal(buttonFor('keep-running-command').disabled, true);
  nodes['site-start'].value = 'npm start';
  nodes['site-start'].onInput();
  const persistent = nodes['keep-running-command'].textContent;
  assert.match(persistent, /exec \/bin\/bash -ic %q/);
  assert.match(persistent, /'npm start'/);
  assert.match(persistent, /sudo loginctl enable-linger "\$USER"/);
  assert.match(persistent, /systemctl --user enable uccloud-web\.service/);
  assert.match(persistent, /systemctl --user restart uccloud-web\.service/);
  assert.doesNotMatch(persistent, /nohup|uccloud-web\.log/);
  await buttonFor('keep-running-command').onClick();
  assert.equal(copied.at(-1), nodes['keep-running-command'].textContent);
  failNextCopy = true;
  await buttonFor('keep-running-command').onClick();
  assert.equal(preview.open, true);
  assert.equal(buttonFor('keep-running-command').textContent, '직접 복사해 주세요');
  nodes['site-start'].value = "echo 'hello'";
  nodes['site-start'].onInput();
  assert.ok(nodes['keep-running-command'].textContent.includes("'\\''"));
  nodes['site-start'].value = 'bad\ncommand';
  nodes['site-start'].onInput();
  assert.equal(buttonFor('keep-running-command').disabled, true);
  await buttonFor('connect-command').onClick();
  assert.equal(copied.at(-1), 'sudo tailscale up');
  assert.equal(buttonFor('connect-command').textContent, '복사됨');
  nodes['site-port'].value = '8080';
  nodes['site-port'].onInput();
  await buttonFor('funnel-command').onClick();
  assert.equal(copied.at(-1), 'sudo tailscale funnel --bg --https=443 8080');
  nodes['site-port'].value = '0';
  nodes['site-port'].onInput();
  assert.equal(buttonFor('funnel-command').disabled, true);
  const count = copied.length;
  await buttonFor('funnel-command').onClick();
  assert.equal(copied.length, count);
  await buttonFor('status-command').onClick();
  assert.equal(copied.at(-1), 'sudo tailscale funnel status');
  console.log('Guide copy checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

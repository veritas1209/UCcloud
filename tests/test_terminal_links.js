const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/terminal.js', 'utf8');
const start = source.indexOf('  const trustedLinkRegex');
const end = source.indexOf('  const terminal = new Terminal', start);
assert.ok(start >= 0 && end > start);
const opened = [];
const messages = [];
const context = {
  URL,
  window: {open() { const tab = {opener: 'old', location: {href: ''}}; opened.push(tab); return tab; }},
  setStatus(message) { messages.push(message); },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end) + '\nglobalThis.checkLink = openTrustedLink; globalThis.linkRegex = trustedLinkRegex;', context);

assert.match('https://login.tailscale.com/a/abc123', context.linkRegex);
assert.match('https://my-vm.tail123.ts.net', context.linkRegex);
assert.doesNotMatch('https://login.tailscale.com.evil.example/a/abc', context.linkRegex);
assert.doesNotMatch('https://my-vm.tail123.ts.net.evil.example', context.linkRegex);

context.checkLink('https://evil.example/');
context.checkLink('javascript:alert(1)');
context.checkLink('https://login.tailscale.com@evil.example/');
assert.equal(opened.length, 0);
context.checkLink('https://login.tailscale.com/a/abc123');
assert.equal(opened.length, 1);
assert.equal(opened[0].opener, null);
assert.equal(opened[0].location.href, 'https://login.tailscale.com/a/abc123');
context.checkLink('https://my-vm.tail123.ts.net/');
assert.equal(opened[1].location.href, 'https://my-vm.tail123.ts.net/');
context.window.open = () => null;
context.checkLink('https://login.tailscale.com/a/abc123');
assert.match(messages.at(-1), /팝업을 허용/);

assert.equal(typeof require('../static/vendor/xterm/addon-web-links-0.12.0.js').WebLinksAddon, 'function');
console.log('Tailscale terminal link checks passed');

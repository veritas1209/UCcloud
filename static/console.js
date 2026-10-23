import RFB from '/core/rfb.js';

const status = document.getElementById('connection-status');
const input = document.getElementById('text-input');
const button = document.getElementById('send-text');
const keyInput = document.getElementById('tailscale-key');
const keyButton = document.getElementById('send-tailscale');
const keyForm = document.getElementById('tailscale-form');
const keyToggle = document.getElementById('show-tailscale');
const classic = document.getElementById('classic-console');
const path = new URLSearchParams(window.location.search).get('path');
let connected = false;
let sending = false;

function ready() {
  button.disabled = !connected || sending;
  keyButton.disabled = !connected || sending;
}

const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

keyToggle.addEventListener('click', () => {
  keyForm.hidden = !keyForm.hidden;
  keyToggle.setAttribute('aria-expanded', String(!keyForm.hidden));
  if (!keyForm.hidden) keyInput.focus();
});

if (!path || !/^\??(?:websockify\?)?token=[a-zA-Z0-9-]+$/.test(path)) {
  status.textContent = '접속 주소가 올바르지 않습니다. UCcloud에서 다시 접속해 주세요.';
} else {
  classic.href = `/vnc_lite.html?path=${encodeURIComponent(path)}`;
  const url = new URL(path, `${window.location.origin}/`);
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const rfb = new RFB(document.getElementById('screen'), url.toString(), { shared: true });
  rfb.scaleViewport = true;
  rfb.addEventListener('connect', () => { connected = true; status.textContent = 'VM 연결됨'; ready(); });
  rfb.addEventListener('disconnect', () => { connected = false; status.textContent = '연결이 끊겼습니다. UCcloud에서 다시 접속해 주세요.'; ready(); });
  rfb.addEventListener('securityfailure', () => { connected = false; status.textContent = '연결에 실패했습니다. UCcloud에서 다시 접속해 주세요.'; ready(); });

  async function sendLine(value) {
    for (const char of value) {
      if (!connected) throw new Error('VM 연결이 끊겼습니다.');
      rfb.sendKey(char.codePointAt(0), null);
      await pause(35);
    }
    rfb.sendKey(0xff0d, 'Enter');
  }

  document.getElementById('text-form').addEventListener('submit', async event => {
    event.preventDefault();
    const value = input.value;
    if (!value || !/^[\x20-\x7e]{1,500}$/.test(value)) {
      status.textContent = '영문 명령어 한 줄만 입력해 주세요.';
      return;
    }
    if (value.includes('tskey-auth-')) {
      status.textContent = '연결 키는 Tailscale 연결 칸에 넣어 주세요.';
      return;
    }
    if (!connected || sending) return;
    sending = true; ready();
    status.textContent = 'VM에 입력하는 중…';
    try {
      await sendLine(value);
      input.value = '';
      status.textContent = 'VM에 입력했습니다.';
    } catch (error) { status.textContent = error.message; }
    finally { sending = false; ready(); }
  });

  keyForm.addEventListener('submit', async event => {
    event.preventDefault();
    const secret = keyInput.value.trim();
    keyInput.value = '';
    if (!/^tskey-auth-[A-Za-z0-9-]{8,240}$/.test(secret)) {
      status.textContent = 'Tailscale 일회용 키를 확인해 주세요.';
      return;
    }
    if (!connected || sending) return;
    sending = true; ready();
    status.textContent = 'Tailscale 연결 명령을 보내는 중…';
    try {
      await sendLine('read -rs UC_TS_KEY; echo; sudo tailscale up --auth-key="$UC_TS_KEY"; unset UC_TS_KEY');
      await pause(300);
      await sendLine(secret);
      status.textContent = '연결 명령을 보냈습니다. VM 비밀번호를 물으면 아래 화면에 입력하세요.';
      keyForm.hidden = true;
      keyToggle.setAttribute('aria-expanded', 'false');
    } catch (error) { status.textContent = error.message; }
    finally { sending = false; ready(); }
  });
}

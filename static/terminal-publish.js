(() => {
  const panel = document.getElementById('publish-panel');
  const form = document.getElementById('publish-form');
  const port = document.getElementById('publish-port');
  const start = document.getElementById('publish-start');
  const stop = document.getElementById('publish-stop');
  const refresh = document.getElementById('publish-refresh');
  const message = document.getElementById('publish-message');
  const elapsed = document.getElementById('publish-elapsed');
  const address = document.getElementById('publish-address');
  const link = document.getElementById('publish-link');
  const copy = document.getElementById('publish-copy');
  const result = document.getElementById('publish-result');
  let send = null, requestId = null, startedAt = 0, timer = null;
  function finish() {
    clearInterval(timer); timer = null; requestId = null;
    start.disabled = !send; refresh.disabled = !send; stop.disabled = !send; port.disabled = !send;
  }
  function request(type) {
    if (!send || requestId) return;
    const number = Number(port.value);
    if (type === 'publish.start' && (!port.value || !Number.isInteger(number) || number < 1 || number > 65535 || number === 22)) {
      message.textContent = '웹사이트에서 사용하는 포트 번호를 입력하세요. 예: 8080'; port.focus(); return;
    }
    requestId = crypto.randomUUID(); startedAt = Date.now();
    start.disabled = true; refresh.disabled = true; stop.disabled = true; port.disabled = true;
    message.textContent = type === 'publish.status' ? '공개 상태를 확인하고 있습니다.' : '설정을 시작하고 있습니다. 이 창을 열어 두세요.';
    elapsed.textContent = '';
    timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      elapsed.textContent = `경과 ${seconds}초`;
      if (seconds > 360) {finish(); message.textContent = '응답을 기다리다 중단했습니다. 상태 확인을 눌러 주세요.';}
    }, 1000);
    try { send({type, request_id:requestId, ...(type === 'publish.start' ? {port:number} : {})}); }
    catch {finish(); message.textContent = '연결이 끊겼습니다. VM에 다시 접속해 주세요.';}
  }
  form.addEventListener('submit', event => {event.preventDefault(); request('publish.start');});
  refresh.addEventListener('click', () => request('publish.status'));
  stop.addEventListener('click', () => {if (confirm('외부 공개를 끌까요? 서버는 계속 실행됩니다.')) request('publish.stop');});
  copy.addEventListener('click', async () => {
    try {await navigator.clipboard.writeText(address.value); message.textContent = '주소를 복사했습니다.';}
    catch {address.focus(); address.select(); message.textContent = '주소를 선택했습니다. 복사해 주세요.';}
  });
  window.uccloudPublish = {
    connected(sender) {send = sender; panel.classList.remove('hidden'); finish(); request('publish.status');},
    disconnected() {send = null; finish(); panel.classList.add('hidden');},
    receive(item) {
      if (item.type !== 'publish' || item.request_id !== requestId) return;
      message.textContent = item.message || '';
      if (item.state === 'working') return;
      finish();
      if (item.state === 'public') {
        if (!/^https:\/\/[a-z0-9][a-z0-9-]{0,62}\.tail2d25b3\.ts\.net$/.test(item.url || '')) {
          message.textContent = '공개 주소를 확인하지 못했습니다. 운영자에게 문의해 주세요.'; return;
        }
        result.hidden = false; address.value = item.url; link.href = item.url; stop.hidden = false;
        if (Number.isInteger(item.port)) port.value = item.port;
      } else if (item.state === 'private') {result.hidden = true; address.value = ''; link.removeAttribute('href'); stop.hidden = true;}
    },
  };
})();

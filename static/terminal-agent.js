/* Agent keys are ephemeral UI state: never localStorage, URLs or HTML attributes. */
(() => {
  const vmId = new URLSearchParams(location.search).get('vm');
  const autoOpen = new URLSearchParams(location.search).get('ai') === '1';
  const button = document.createElement('button');
  button.id = 'agent-connect'; button.type = 'button'; button.textContent = 'AI 연결'; button.disabled = true;
  document.querySelector('.heading-actions').prepend(button);
  const dialog = document.createElement('dialog');
  dialog.id = 'agent-dialog'; dialog.setAttribute('aria-labelledby', 'agent-title');
  dialog.innerHTML = `<div class="agent-head"><h2 id="agent-title">AI 연결</h2><button id="agent-close" type="button">닫기</button></div>
    <p class="agent-copy">안내를 복사해 Codex·Claude Code 같은 코딩 에이전트에 붙여넣으세요. 추가 설치는 없습니다.</p>
    <label class="agent-duration" for="agent-hours">연결 시간 <select id="agent-hours"><option value="1">1시간</option><option value="4" selected>4시간</option><option value="24">하루</option></select></label>
    <p id="agent-status" role="status" aria-live="polite"></p>
    <div id="agent-result" hidden><textarea id="agent-prompt" readonly aria-label="에이전트 연결 안내" spellcheck="false"></textarea><p id="agent-permission" class="agent-note">이 안내는 내 VM 접속 권한입니다. 다른 사람에게 공유하지 마세요.</p><button id="agent-copy" type="button">안내 복사</button></div>
    <div class="agent-footer"><span id="agent-time"></span><button id="agent-renew" type="button">안내 다시 만들기</button><button id="agent-revoke" type="button" hidden>연결 해제</button></div>`;
  document.body.append(dialog);
  const $ = id => document.getElementById(id);
  let send = null, csrf = '', pending = null, connection = null, timer = null, timeout = null, everOpened = false;
  function clearPrompt() { $('agent-prompt').value = ''; $('agent-result').hidden = true; }
  function message(text) { $('agent-status').textContent = text; }
  function busy(value) {
    $('agent-renew').disabled = value || !send;
    $('agent-hours').disabled = value;
    $('agent-revoke').disabled = value;
  }
  function clock() {
    if (!connection) { $('agent-time').textContent = ''; return; }
    const remaining = Math.max(0, connection.expires_at * 1000 - Date.now());
    $('agent-time').textContent = remaining ? `남은 시간 ${Math.ceil(remaining / 60000)}분` : '연결 시간 만료';
    if (!remaining) { clearPrompt(); $('agent-revoke').hidden = true; }
  }
  async function list() {
    const response = await fetch(`/api/instances/${vmId}/agent-connections`, {credentials:'same-origin',cache:'no-store'});
    if (!response.ok) throw new Error('연결 상태를 확인하지 못했습니다. 다시 로그인하거나 잠시 후 시도해 주세요.');
    const data = await response.json();
    if (!data.enabled) throw new Error('AI 연결을 준비 중입니다. 운영자에게 문의해 주세요.');
    connection = data.connections?.[0] || null;
    $('agent-revoke').hidden = !connection;
    clock();
  }
  function issue() {
    if (!send || pending) return;
    clearPrompt(); pending = crypto.randomUUID(); busy(true);
    message('연결 안내를 만드는 중입니다. 잠시 기다려 주세요.');
    timeout = setTimeout(() => {
      pending = null; busy(false);
      message('연결 결과를 확인하지 못했습니다. 안내 다시 만들기를 눌러 주세요.');
      void list().catch(error => message(error.message));
    }, 65000);
    try { send({type:'agent.issue',request_id:pending,hours:Number($('agent-hours').value)}); }
    catch { clearTimeout(timeout); pending = null; busy(false); message('VM에 다시 접속해 주세요.'); }
  }
  async function open() {
    dialog.showModal(); everOpened = true; clearPrompt(); message('연결 상태를 확인하고 있습니다.'); busy(true);
    try {
      await list(); busy(false);
      if (!dialog.open) return;
      if (connection) message('사용 중인 연결이 있습니다. 안내가 필요하면 다시 만들어 주세요. 이전 연결은 해제됩니다.');
      else issue();
    } catch (error) { message(error.message); busy(false); }
    clearInterval(timer); timer = setInterval(clock,1000);
  }
  button.addEventListener('click', () => void open());
  $('agent-close').addEventListener('click', () => {clearPrompt(); dialog.close();});
  dialog.addEventListener('close', () => { clearPrompt(); clearInterval(timer); });
  $('agent-renew').addEventListener('click', () => {
    if (connection && !confirm('새 안내를 만들면 이전 AI 연결이 해제됩니다. 계속할까요?')) return;
    issue();
  });
  $('agent-copy').addEventListener('click', async () => {
    if (!$('agent-prompt').value) return;
    try { await navigator.clipboard.writeText($('agent-prompt').value); message('복사했습니다. 에이전트에 붙여넣고 원하는 작업을 말하세요.'); }
    catch { $('agent-prompt').focus(); $('agent-prompt').select(); message('안내를 선택했습니다. 복사해 주세요.'); }
  });
  $('agent-revoke').addEventListener('click', async () => {
    if (!connection || pending || !confirm('AI 연결을 해제할까요? 실행 중인 API 작업도 중단됩니다.')) return;
    busy(true);
    try {
      const response = await fetch(`/api/instances/${vmId}/agent-connections/${connection.id}`, {method:'DELETE',credentials:'same-origin',headers:{'X-CSRF-Token':csrf}});
      if (!response.ok) throw new Error('연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      connection = null; clearPrompt(); $('agent-revoke').hidden = true; clock(); message('AI 연결을 해제했습니다.');
    } catch (error) { message(error.message); }
    finally { busy(false); }
  });
  window.uccloudAgent = {
    connected(sender, token) { send = sender; csrf = token; button.disabled = false; if (autoOpen && !everOpened) void open(); },
    disconnected() {
      send = null; button.disabled = true; clearTimeout(timeout); pending = null; busy(false);
      if (dialog.open) {clearPrompt(); message('웹 SSH 연결이 끊겼습니다. 발급한 AI 연결은 정한 시간까지 유지됩니다.');}
    },
    async receive(item) {
      if (item.type !== 'agent' || item.request_id !== pending) return;
      clearTimeout(timeout); pending = null;
      if (item.state !== 'ready') { busy(false); message(item.message || 'AI 연결을 만들지 못했습니다.'); return; }
      connection = item; $('agent-revoke').hidden = false; clock();
      $('agent-permission').textContent = item.sudo_available ? '이 안내에는 VM 관리자 권한이 포함됩니다. 공유하지 마세요.' : '이 안내는 내 VM 접속 권한입니다. 다른 사람에게 공유하지 마세요.';
      if (!dialog.open) {delete item.key; busy(false); return;}
      try {
        const response = await fetch('/api/agent/v1/instructions', {headers:{Authorization:`Bearer ${item.key}`},cache:'no-store',credentials:'omit'});
        if (!response.ok) throw new Error();
        const instructions = await response.text();
        if (!dialog.open) return;
        const endpoint = item.api_base || `${location.origin}/api/agent/v1`;
        const url = new URL(endpoint);
        if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/api/agent/v1') throw new Error();
        $('agent-prompt').value = `UCcloud의 내 VM에서 아래 API로 작업해 주세요. 내 요청 범위에서만 작업하세요.\n\nAPI 기본 주소: ${endpoint}\n임시 API 키: ${item.key}\nVM: ${item.server_id}\n접속 ID: ${item.vm_username}\n만료: ${new Date(item.expires_at*1000).toISOString()}\n\n${instructions}\n\n먼저 연결과 작업 환경을 확인하고, 내가 요청하는 작업을 진행하세요.`;
        $('agent-result').hidden = false;
        message('안내 복사를 누르세요.');
      } catch { message('안내를 불러오지 못했습니다. 안내 다시 만들기를 눌러 주세요.'); }
      finally { delete item.key; busy(false); }
    },
  };
  window.addEventListener('pagehide', () => { clearPrompt(); clearInterval(timer); clearTimeout(timeout); });
})();

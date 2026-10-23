(() => {
  const $ = id => document.getElementById(id);
  const vmId = new URLSearchParams(location.search).get('vm');
  const waitForListing = new URLSearchParams(location.search).get('wait') === '1';
  const startWhenStopped = new URLSearchParams(location.search).get('start') === '1';
  const expectsHandoff = new URLSearchParams(location.search).get('handoff') === '1';
  const listingDeadline = Date.now() + 300000;
  let startAttempted = false;
  let startDeadline = 0;
  let startUnconfirmed = false;
  const vmName = $('vm-name');
  const status = $('status');
  const form = $('connect-form');
  const password = $('vm-password');
  const passwordToggle = $('vm-password-toggle');
  const button = $('connect');
  const copyButton = $('copy');
  let pasting = false;
  const readTextButton = $('read-text');
  const textDialog = $('terminal-text-dialog');
  const textOutput = $('terminal-text');
  const textCopyButton = $('terminal-text-copy');
  const textStatus = $('terminal-text-status');
  const retryStartButton = $('retry-start');
  const vncButton = $('vnc');
  const help = $('terminal-help');
  const tailscaleLogin = $('tailscale-login');
  let softwareProgress = null;
  let softwareTimer = null;
  function softwareDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    return seconds >= 60 ? `${Math.floor(seconds / 60)}분 ${seconds % 60}초` : `${seconds}초`;
  }
  function renderSoftwareTime() {
    if (!softwareProgress) return;
    const item = softwareProgress;
    if (item.state === 'unknown' || item.cached) { $('software-time').textContent = ''; return; }
    const start = Number(item.started_at) * 1000;
    const end = ['ready', 'failed'].includes(item.state) ? Number(item.updated_at) * 1000 : Date.now();
    const elapsed = Number.isFinite(start) && start > 0 ? Math.max(0, (end - start) / 1000) : null;
    const parts = [];
    if (item.state === 'installing' && Number.isInteger(item.step) && item.total_steps > 0) parts.push(`${item.step}/${item.total_steps}단계`);
    if (elapsed != null) parts.push(`${item.state === 'ready' ? '총' : '경과'} ${softwareDuration(elapsed)}`);
    if (['pending', 'installing'].includes(item.state)) {
      const min = Number(item.estimate_min_seconds) || 180;
      const max = Number(item.estimate_max_seconds) || 900;
      parts.push(elapsed > max ? '예상보다 오래 걸리고 있습니다. 설치 상태를 계속 확인합니다.' : `전체 예상 ${Math.ceil(min / 60)}~${Math.ceil(max / 60)}분 · 다운로드 속도에 따라 달라집니다.`);
    }
    $('software-time').textContent = parts.join(' · ');
  }
  function showSoftwareProgress(message) {
    softwareProgress = message;
    $('software-message').textContent = message.message;
    $('software-status').classList.remove('hidden');
    $('software-guide').classList.toggle('hidden', message.state !== 'ready');
    clearInterval(softwareTimer);
    renderSoftwareTime();
    softwareTimer = ['pending', 'installing'].includes(message.state) ? setInterval(renderSoftwareTime, 1000) : null;
  }
  $('software-guide').addEventListener('click', () => {
    if (socket?.readyState === WebSocket.OPEN && ready) {
      socket.send(JSON.stringify({type:'input',data:"cat ~/UCcloud-설치안내.txt\n"}));
      terminal.focus();
    }
  });
  const shortcutDialog = $('shortcut-dialog');
  const shortcutStatus = $('shortcut-status');
  const shortcutStorageKey = 'uccloud.terminal.shortcuts.v1';
  function setPasswordVisible(visible) {
    password.type = visible ? 'text' : 'password';
    passwordToggle.textContent = visible ? '숨기기' : '보기';
    passwordToggle.setAttribute('aria-label', visible ? '비밀번호 숨기기' : '비밀번호 보기');
    passwordToggle.setAttribute('aria-pressed', String(visible));
  }
  passwordToggle.addEventListener('click', () => { setPasswordVisible(password.type === 'password'); password.focus(); });
  const defaultShortcuts = {
    copy: {code:'KeyC',ctrl:true,shift:false,alt:false,meta:false},
    paste: {code:'KeyV',ctrl:true,shift:false,alt:false,meta:false},
  };
  function validShortcut(value) {
    return value && typeof value === 'object' && /^[A-Za-z][A-Za-z0-9]{0,39}$/.test(value.code)
      && ['ctrl','shift','alt','meta'].every(key => typeof value[key] === 'boolean')
      && (value.ctrl || value.alt || value.meta)
      && !/^(?:Control|Shift|Alt|Meta|OS|Tab|Enter|Space|Escape)/.test(value.code);
  }
  function sameShortcut(left, right) {
    return left.code === right.code && ['ctrl','shift','alt','meta'].every(key => left[key] === right[key]);
  }
  function defaultShortcutCopy() {
    return {copy:{...defaultShortcuts.copy},paste:{...defaultShortcuts.paste}};
  }
  function loadShortcuts() {
    try {
      const saved = JSON.parse(localStorage.getItem(shortcutStorageKey));
      if (validShortcut(saved?.copy) && validShortcut(saved?.paste) && !sameShortcut(saved.copy, saved.paste)) return saved;
    } catch { /* Browser storage can be unavailable. */ }
    return defaultShortcutCopy();
  }
  let shortcuts = loadShortcuts();
  let recording = null;
  function shortcutLabel(value) {
    return [value.ctrl && 'Ctrl', value.alt && 'Alt', value.shift && 'Shift', value.meta && '⌘',
      value.code.replace(/^Key/, '').replace(/^Digit/, '')].filter(Boolean).join(' + ');
  }
  function renderShortcuts() {
    shortcutDialog.querySelector('[data-shortcut="copy"]').textContent = shortcutLabel(shortcuts.copy);
    shortcutDialog.querySelector('[data-shortcut="paste"]').textContent = shortcutLabel(shortcuts.paste);
  }
  function eventShortcut(event) {
    return {code:event.code,ctrl:event.ctrlKey,shift:event.shiftKey,alt:event.altKey,meta:event.metaKey};
  }
  const trustedLinkRegex = /https:\/\/(?:login\.tailscale\.com|(?:[a-z0-9-]+\.)+ts\.net)(?=[:/?#\s]|$)(?::\d{1,5})?(?:[/?#][^\s"'<>`]*)?/i;
  function openTrustedLink(raw) {
    let url;
    try { url = new URL(raw.replace(/[),.;!?]+$/, '')); }
    catch { return; }
    const host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || (host !== 'login.tailscale.com' && !host.endsWith('.ts.net'))) return;
    const tab = window.open('about:blank', '_blank');
    if (!tab) { setStatus('팝업을 허용한 뒤 주소를 다시 눌러 주세요.'); return; }
    tab.opener = null;
    tab.location.href = url.href;
  }
  const terminal = new Terminal({fontFamily:'"Cascadia Code","Consolas",monospace',fontSize:14,cursorBlink:true,scrollback:3000,theme:{background:'#11181f',foreground:'#e6edf3',cursor:'#d2e5ff'}});
  const fit = new FitAddon.FitAddon();
  terminal.loadAddon(fit);
  terminal.loadAddon(new WebLinksAddon.WebLinksAddon((event, uri) => openTrustedLink(uri), {urlRegex: trustedLinkRegex}));
  terminal.options.linkHandler = {activate: (event, uri) => openTrustedLink(uri), allowNonHttpProtocols: false};
  let terminalOpened = false;
  let socket = null;
  let csrf = '';
  let ready = false;
  const readyMessage = '연결됐습니다. 이 창에서 바로 입력하세요.';
  let handoffCredentials = null;
  let handoffTimer = null;
  let closeMessage = '';
  let checkTimer = null;
  let recentOutput = '';
  let outputDecoder = new TextDecoder();
  const loginUrlRegex = /https:\/\/login\.tailscale\.com\/[^\s\x1b"'<>`]+(?=\s)/gi;
  function setStatus(message) { status.textContent = message; }
  function clearHandoff() {
    if (handoffCredentials) handoffCredentials.password = '';
    handoffCredentials = null;
    clearTimeout(handoffTimer);
  }
  if (expectsHandoff && window.opener && /^[0-9a-f-]{36}$/i.test(vmId || '')) {
    const source = window.opener;
    window.addEventListener('message', event => {
      if (event.origin !== location.origin || event.source !== source || event.data?.type !== 'uccloud:ssh-credentials'
          || event.data.vmId !== vmId || handoffCredentials
          || typeof event.data.username !== 'string' || typeof event.data.password !== 'string'
          || !event.data.username || !event.data.password
          || event.data.username.length > 32 || event.data.password.length > 128) return;
      handoffCredentials = {username:event.data.username,password:event.data.password};
      window.opener = null;
      handoffTimer = setTimeout(clearHandoff, 300000);
      connectHandoffIfReady();
    });
    source.postMessage({type:'uccloud:ssh-ready',vmId}, location.origin);
  }
  function clearLoginLink() {
    recentOutput = '';
    outputDecoder = new TextDecoder();
    tailscaleLogin.removeAttribute('href');
    tailscaleLogin.classList.add('hidden');
  }
  function findLoginLink(chunk) {
    recentOutput = (recentOutput + chunk).slice(-4096);
    for (const match of recentOutput.matchAll(loginUrlRegex)) {
      try {
        const url = new URL(match[0].replace(/[),.;!?]+$/, ''));
        if (url.protocol !== 'https:' || url.hostname !== 'login.tailscale.com') continue;
        tailscaleLogin.href = url.href;
        tailscaleLogin.classList.remove('hidden');
      } catch { /* Ignore malformed terminal output. */ }
    }
  }
  async function copySelection() {
    if (!ready) return;
    const selected = terminal.getSelection();
    if (!selected) { setStatus('복사할 글자를 먼저 선택해 주세요.'); return; }
    try { await navigator.clipboard.writeText(selected); setStatus('복사했습니다.'); }
    catch { setStatus('복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.'); }
  }
  function syncCopyButton() {
    const selected = ready && terminal.hasSelection();
    copyButton.classList.toggle('hidden', !selected);
    if (selected && status.textContent === '복사할 글자를 먼저 선택해 주세요.') setStatus(readyMessage);
  }
  terminal.onSelectionChange(syncCopyButton);
  function visibleTerminalText() {
    const buffer = terminal.buffer.active;
    const lines = [];
    for (let row = buffer.viewportY; row < buffer.viewportY + terminal.rows; row += 1) {
      const line = buffer.getLine(row);
      if (!line) break;
      const value = line.translateToString(true);
      if (line.isWrapped && lines.length) lines[lines.length - 1] += value;
      else lines.push(value);
    }
    return lines.join('\n').trimEnd();
  }
  readTextButton.addEventListener('click', () => {
    if (!ready) return;
    const visible = visibleTerminalText();
    textOutput.textContent = visible || '화면에 글자가 없습니다.';
    textCopyButton.disabled = !visible;
    textStatus.textContent = '';
    textDialog.showModal();
  });
  $('terminal-text-close').addEventListener('click', () => textDialog.close());
  textDialog.addEventListener('close', () => { textOutput.textContent = ''; textStatus.textContent = ''; if (ready) terminal.focus(); });
  textCopyButton.addEventListener('click', async () => {
    if (textCopyButton.disabled) return;
    try { await navigator.clipboard.writeText(textOutput.textContent); textStatus.textContent = '복사했습니다.'; }
    catch { textStatus.textContent = '복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.'; }
  });
  async function pasteClipboard() {
    if (!ready || socket?.readyState !== WebSocket.OPEN || pasting) return;
    pasting = true;
    try {
      const content = (await navigator.clipboard.readText()).replace(/\r\n?/g, '\n');
      if (!content) { setStatus('붙여넣을 글자가 없습니다.'); return; }
      if (new TextEncoder().encode(content).length > 8000) { setStatus('내용이 너무 깁니다. 나누어 붙여넣어 주세요.'); return; }
      if (!ready || socket?.readyState !== WebSocket.OPEN) return;
      terminal.paste(content);
      terminal.focus();
      setStatus('붙여넣었습니다.');
    } catch { setStatus('붙여넣지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.'); }
    finally { pasting = false; }
  }
  terminal.attachCustomKeyEventHandler(event => {
    if (event.type !== 'keydown' || !ready) return true;
    const pressed = eventShortcut(event);
    if (sameShortcut(pressed, shortcuts.copy)) {
      if (pressed.code === 'KeyC' && pressed.ctrl && !pressed.shift && !pressed.alt && !pressed.meta && !terminal.hasSelection()) return true;
      event.preventDefault(); event.stopPropagation(); void copySelection(); return false;
    }
    if (sameShortcut(pressed, shortcuts.paste)) { event.preventDefault(); event.stopPropagation(); void pasteClipboard(); return false; }
    return true;
  });
  $('shortcuts').addEventListener('click', () => {
    recording = null;
    shortcutStatus.textContent = '';
    renderShortcuts();
    shortcutDialog.showModal();
  });
  shortcutDialog.querySelectorAll('[data-shortcut]').forEach(target => target.addEventListener('click', () => {
    recording = target.dataset.shortcut;
    shortcutStatus.textContent = '원하는 키를 누르세요. Ctrl, Alt 또는 ⌘를 함께 눌러야 합니다.';
  }));
  shortcutDialog.addEventListener('keydown', event => {
    if (!recording) return;
    if (event.code === 'Escape') { event.preventDefault(); recording = null; shortcutStatus.textContent = '변경하지 않았습니다.'; return; }
    if (/^(?:Control|Shift|Alt|Meta|OS)/.test(event.code)) return;
    event.preventDefault();
    event.stopPropagation();
    const value = eventShortcut(event);
    if (!validShortcut(value)) { shortcutStatus.textContent = 'Ctrl, Alt 또는 ⌘와 일반 키를 함께 눌러 주세요.'; return; }
    const other = recording === 'copy' ? 'paste' : 'copy';
    if (sameShortcut(value, shortcuts[other])) { shortcutStatus.textContent = '복사와 붙여넣기에 같은 키를 쓸 수 없습니다.'; return; }
    shortcuts[recording] = value;
    recording = null;
    renderShortcuts();
    try { localStorage.setItem(shortcutStorageKey, JSON.stringify(shortcuts)); shortcutStatus.textContent = '저장했습니다.'; }
    catch { shortcutStatus.textContent = '이 탭에만 적용됩니다.'; }
  });
  shortcutDialog.addEventListener('close', () => { recording = null; });
  $('shortcut-reset').addEventListener('click', () => {
    shortcuts = defaultShortcutCopy();
    recording = null;
    renderShortcuts();
    try { localStorage.removeItem(shortcutStorageKey); shortcutStatus.textContent = '기본값으로 바꿨습니다.'; }
    catch { shortcutStatus.textContent = '이 탭에서 기본값으로 바꿨습니다.'; }
  });
  function checkAgain(message) {
    setStatus(message);
    clearTimeout(checkTimer);
    checkTimer = setTimeout(bootstrap, 8000);
  }
  function offerStartRetry(message) {
    clearTimeout(checkTimer);
    retryStartButton.classList.remove('hidden');
    setStatus(message);
  }
  function waitingStatus(message) {
    return Date.now() >= listingDeadline
      ? 'VM 준비가 오래 걸리고 있습니다. 자동으로 다시 확인합니다. 내 VM에서도 상태를 확인할 수 있습니다.'
      : message;
  }
  function resize() { if (!terminalOpened) return; fit.fit(); if (ready && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows})); }
  new ResizeObserver(resize).observe($('terminal'));
  terminal.onData(data => { if (ready && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'input',data})); });
  function disconnect(message) {
    window.uccloudSoftware?.disconnected();
    window.uccloudAgent?.disconnected();
    window.uccloudFiles?.disconnected();
    window.uccloudPublish?.disconnected();
    const wasEntering = form.contains(document.activeElement) || $('terminal').contains(document.activeElement);
    ready=false; terminal.clearSelection(); copyButton.classList.add('hidden'); readTextButton.classList.add('hidden');
    if(textDialog.open) textDialog.close();
    button.disabled=false; button.textContent='다시 연결'; form.classList.remove('hidden');
    help.textContent='연결이 안 되면 아래 화면 콘솔을 여세요.'; help.classList.remove('hidden');
    $('software-status').classList.add('hidden');
    clearInterval(softwareTimer);
    clearLoginLink(); setStatus(message);
    if (wasEntering && !document.hidden && matchMedia('(pointer: fine)').matches && !shortcutDialog.open) password.focus({preventScroll:true});
  }
  function sessionExpired() { window.uccloudSoftware?.disconnected(); window.uccloudFiles?.disconnected(); window.uccloudPublish?.disconnected(); ready=false; terminal.clearSelection(); copyButton.classList.add('hidden'); readTextButton.classList.add('hidden'); if(textDialog.open) textDialog.close(); clearHandoff(); password.value=''; setPasswordVisible(false); button.disabled=false; vncButton.classList.add('hidden'); help.classList.add('hidden'); form.classList.add('hidden'); clearLoginLink(); setStatus('로그인이 만료됐습니다. 내 VM에서 다시 로그인해 주세요.'); }
  async function bootstrap() {
    if (!csrf) window.uccloudAgent?.disconnected();
    if (!/^[0-9a-f-]{36}$/i.test(vmId || '')) { setStatus('VM 주소가 올바르지 않습니다. 내 VM에서 다시 열어 주세요.'); return; }
    try {
      const meResponse = await fetch('/api/me',{credentials:'same-origin',cache:'no-store'});
      if (!meResponse.ok) { location.href='/'; return; }
      csrf=(await meResponse.json()).csrf_token;
      const response=await fetch('/api/instances',{credentials:'same-origin',cache:'no-store'});
      if (!response.ok) throw new Error();
      const vm=(await response.json()).instances.find(item=>item.id===vmId);
      if (!vm) {
        if (waitForListing && Date.now() < listingDeadline) { checkAgain('VM을 만드는 중입니다. 자동으로 다시 확인합니다.'); return; }
        setStatus(waitForListing ? 'VM이 목록에 나타나지 않았습니다. 내 VM에서 상태를 확인해 주세요.' : '내 VM 목록에서 찾을 수 없습니다.'); return;
      }
      vmName.textContent=vm.name;
      if (vm.software_status?.state === 'ready') {
        showSoftwareProgress({...vm.software_status,cached:true,message:'프로그램 설치 완료'});
      } else if (Array.isArray(vm.software) && vm.software.length && !softwareProgress) {
        showSoftwareProgress({state:'unknown',message:'접속하면 프로그램 설치 상태를 확인할 수 있습니다.'});
      }
      if (vm.status === 'ERROR') { clearHandoff(); setStatus('VM 준비에 문제가 생겼습니다. 내 VM에서 상태를 확인해 주세요.'); return; }
      if (vm.status === 'SHUTOFF') {
        if (!startWhenStopped) { setStatus('VM이 꺼져 있습니다. 내 VM에서 시작해 주세요.'); return; }
        if (startAttempted) {
          if (Date.now() >= startDeadline) { offerStartRetry('VM이 아직 꺼져 있습니다. 다시 켜기를 누르세요.'); return; }
          checkAgain(startUnconfirmed
            ? 'VM 시작 요청 결과를 확인하지 못했습니다. 실제 상태를 자동으로 확인합니다.'
            : 'VM을 켜는 중입니다. 자동으로 다시 확인합니다.');
          return;
        }
        startAttempted = true;
        startDeadline = Date.now() + 300000;
        setStatus('VM을 켜는 중입니다. 자동으로 다시 확인합니다.');
        let started;
        try {
          started = await fetch(`/api/instances/${vmId}/action`, {
            method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
            body:JSON.stringify({action:'start'}),
          });
        } catch {
          startUnconfirmed = true;
          checkAgain('VM 시작 요청 결과를 확인하지 못했습니다. 실제 상태를 자동으로 확인합니다.');
          return;
        }
        if (started.status === 401) { location.href='/'; return; }
        if (!started.ok && started.status !== 409) {
          if ([429,500,502,503,504].includes(started.status)) {
            startUnconfirmed = true;
            checkAgain('VM 시작 요청 결과를 확인하지 못했습니다. 실제 상태를 자동으로 확인합니다.');
          } else offerStartRetry('VM을 켜지 못했습니다. 잠시 후 다시 시도해 주세요.');
          return;
        }
        checkAgain('VM을 켜는 중입니다. 자동으로 다시 확인합니다.');
        return;
      }
      if (startAttempted && Date.now() >= startDeadline && vm.status !== 'ACTIVE') {
        checkAgain('VM 시작이 오래 걸리고 있습니다. 자동으로 다시 확인합니다.');
        return;
      }
      if (vm.status!=='ACTIVE') { checkAgain(waitingStatus('VM이 켜질 때까지 기다려 주세요. 자동으로 다시 확인합니다.')); return; }
      vncButton.classList.remove('hidden');
      const hasIp = (Array.isArray(vm.addresses) ? vm.addresses : []).some(a => a?.address && (a.version == null || Number(a.version) === 4));
      if (!hasIp) { checkAgain(waitingStatus('VM의 IP를 기다리는 중입니다. 화면 콘솔로 상태를 볼 수 있습니다. 자동으로 다시 확인합니다.')); return; }
      clearTimeout(checkTimer);
      help.textContent='연결이 안 되면 화면 콘솔을 여세요.';
      help.classList.remove('hidden');
      $('vm-user').value=vm.vm_username || '';
      form.classList.remove('hidden');
      setStatus($('vm-user').value ? '비밀번호를 넣고 연결하세요.' : 'VM 접속 ID와 비밀번호를 넣고 연결하세요.');
      if (!connectHandoffIfReady()) ($('vm-user').value ? password : $('vm-user')).focus();
    } catch { checkAgain('연결 정보를 확인하지 못했습니다. 잠시 후 다시 확인합니다.'); }
  }
  function connectVm(username, suppliedPassword) {
    if (socket && socket.readyState <= WebSocket.OPEN) socket.close();
    let secret=suppliedPassword;
    if (!username || !secret) return;
    button.disabled=true;
    button.textContent='연결 중…';
    clearLoginLink();
    closeMessage='';
    setStatus('VM에 연결 중입니다. 잠시 기다려 주세요.');
    const protocol=location.protocol==='https:'?'wss:':'ws:';
    const url=`${protocol}//${location.host}/api/instances/${vmId}/terminal`;
    const ws=new WebSocket(url,['uccloud',`csrf.${csrf}`]);
    socket=ws;
    ws.binaryType='arraybuffer';
    ws.onopen=()=>{ ws.send(JSON.stringify({username,password:secret,cols:terminal.cols,rows:terminal.rows})); secret=''; };
    ws.onmessage=event=>{
      if (typeof event.data!=='string') { const bytes=new Uint8Array(event.data); findLoginLink(outputDecoder.decode(bytes,{stream:true})); terminal.write(bytes); return; }
      let message; try { message=JSON.parse(event.data); } catch { return; }
      window.uccloudFiles?.receive(message);
      window.uccloudPublish?.receive(message);
      window.uccloudAgent?.receive(message);
      window.uccloudSoftware?.receive(message);
      if (message.type==='waiting') setStatus('VM을 준비하는 중입니다. 자동으로 연결합니다.');
      if (message.type==='software') {
        if (message.state !== 'unknown' || softwareProgress) showSoftwareProgress(message);
      }
      if (message.type==='software.install' && message.state==='accepted') {
        showSoftwareProgress({state:'unknown',message:message.message});
      }
      if (message.type==='ready') {
        if (softwareProgress && softwareProgress.state !== 'ready') showSoftwareProgress({state:'unknown',message:'프로그램 설치 상태를 확인하고 있습니다.'});
        password.value=''; setPasswordVisible(false);
        ready=true; form.classList.add('hidden'); button.disabled=false;
        window.uccloudFiles?.connected(message => {
          if (!ready || socket !== ws || ws.readyState !== WebSocket.OPEN) throw new Error('Disconnected');
          ws.send(JSON.stringify(message));
        });
        window.uccloudPublish?.connected(message => {
          if (!ready || socket !== ws || ws.readyState !== WebSocket.OPEN) throw new Error('Disconnected');
          ws.send(JSON.stringify(message));
        });
        window.uccloudAgent?.connected(message => {
          if (!ready || socket !== ws || ws.readyState !== WebSocket.OPEN) throw new Error('Disconnected');
          ws.send(JSON.stringify(message));
        }, csrf);
        window.uccloudSoftware?.connected(message => {
          if (!ready || socket !== ws || ws.readyState !== WebSocket.OPEN) throw new Error('Disconnected');
          ws.send(JSON.stringify(message));
        });
        const touchCopy=Boolean(window.matchMedia?.('(pointer: coarse)').matches);
        if(touchCopy) readTextButton.classList.remove('hidden');
        help.textContent=touchCopy ? '글자를 복사하려면 화면 글자를 누르세요.' : '글자를 선택하면 복사 버튼이 나타납니다.';
        $('terminal').classList.remove('hidden');
        if(!terminalOpened){terminal.open($('terminal'));terminalOpened=true;}
        terminal.clearSelection(); setStatus(readyMessage);
        requestAnimationFrame(()=>{resize();terminal.focus();});
      }
      if (message.type==='error') { closeMessage=message.message || '연결할 수 없습니다. 다시 시도해 주세요.'; disconnect(closeMessage); ws.close(); }
    };
    ws.onerror=()=>{ if (socket===ws && !ready) setStatus('연결에 실패했습니다. 잠시 후 다시 시도해 주세요.'); };
    ws.onclose=event=>{ if(socket!==ws) return; window.uccloudAgent?.disconnected(); window.uccloudSoftware?.disconnected(); if (event.code===4401 || event.code===1008) { sessionExpired(); return; } disconnect(closeMessage || (ready?'연결이 끊겼습니다. 다시 연결할 수 있습니다.':'연결을 열지 못했습니다. 잠시 후 다시 시도해 주세요.')); };
  }
  function connectHandoffIfReady() {
    if (!handoffCredentials || form.classList.contains('hidden') || button.disabled) return false;
    const username = handoffCredentials.username;
    const secret = handoffCredentials.password;
    clearHandoff();
    $('vm-user').value = username;
    password.value = secret;
    connectVm(username, secret);
    return true;
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    clearHandoff();
    connectVm($('vm-user').value.trim(), password.value);
  });
  window.addEventListener('pagehide', () => { password.value=''; clearHandoff(); clearInterval(softwareTimer); });
  copyButton.addEventListener('click', copySelection);
  retryStartButton.addEventListener('click', () => {
    if (!startWhenStopped || retryStartButton.classList.contains('hidden')) return;
    retryStartButton.classList.add('hidden');
    startAttempted = false;
    startUnconfirmed = false;
    clearTimeout(checkTimer);
    setStatus('VM 상태를 확인하는 중입니다.');
    void bootstrap();
  });
  $('vnc').addEventListener('click',async()=>{
    const tab=window.open('about:blank','_blank');
    if (!tab){setStatus('팝업을 허용한 뒤 다시 눌러 주세요.');return;}
    tab.opener=null;
    try { const response=await fetch(`/api/instances/${vmId}/console`,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf}}); if(!response.ok)throw new Error();tab.location.href=(await response.json()).url; }
    catch{tab.close();setStatus('화면 콘솔을 열지 못했습니다. 잠시 후 다시 시도해 주세요.');}
  });
  bootstrap();
})();

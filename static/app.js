const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let state = { me: null, csrf: null, instances: [], backups: [], backupEnabled: false, catalog: null, quota: null, publicMode: false };
let cloudRefreshing = false;
let quotaUpdatedAt = 0;
const quotaRefreshInterval = 60000;
let teamRequestsSignature = null;
let teamRequestsFor = null;
let teamRequestFetchId = 0;
let automaticProvisionRetry = null;
const pendingInstanceActions = new Map();
const openingConsoleIds = new Set();
const pendingTerminalHandoffs = new Map();
let backupVmId = null;
let backupRefreshing = false;
let siteVmId = null;
let selectedSoftware = [];

function renderSoftwarePicker() {
  const catalog = state.catalog?.software || [];
  $('.software-picker').classList.toggle('hidden', !catalog.length);
  const groups = [...new Set(catalog.map(item => item.group))];
  $('#software-select').innerHTML = '<option value="">프로그램 선택</option><optgroup label="기본 프리셋"><option value="preset:web">웹 개발 기본 · Node.js + React + PostgreSQL</option><option value="preset:python">Python 웹 개발 · Python + PostgreSQL</option><option value="preset:java">Java 웹 개발 · Spring Boot + PostgreSQL</option></optgroup>' + groups.map(group => `<optgroup label="${escapeHtml(group)}">${catalog.filter(item => item.group === group && !selectedSoftware.some(selected => selected.id === item.id)).map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('')}</optgroup>`).join('');
  $('#software-select').disabled = catalog.length === 0;
  $('#software-list').innerHTML = selectedSoftware.map(selected => {
    const item = catalog.find(item => item.id === selected.id);
    return `<div class="software-row"><div class="software-name"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.note)}</small></div><div class="software-settings"><label>버전<select data-software-version="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)} 버전">${item.versions.map((version, index) => `<option value="${escapeHtml(version)}" ${version === selected.version ? 'selected' : ''}>${escapeHtml(version)}${index === 0 ? ' (추천)' : ''}</option>`).join('')}</select></label>${item.port ? `<label>포트<input type="number" min="1024" max="65535" required data-software-port="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)} 포트" value="${selected.port}"></label>` : ''}</div>${item.credentials ? `<div class="software-credentials"><label>서비스 ID<input data-software-username="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)} 서비스 ID" value="${escapeHtml(selected.username || 'student')}" required minlength="2" maxlength="32" pattern="[a-z][a-z0-9_]{1,31}" autocomplete="off" autocapitalize="none" spellcheck="false"></label><label>서비스 비밀번호 <small>8자 이상</small><span class="service-password-entry"><input data-software-password="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)} 서비스 비밀번호" type="password" required minlength="8" maxlength="128" autocomplete="new-password"><button type="button" data-service-password-toggle="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)} 비밀번호 보기" aria-pressed="false">보기</button></span></label></div>` : ''}<button type="button" data-software-remove="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)} 설치 취소">×</button></div>`;
  }).join('');
  $$('[data-software-password]').forEach(input => {
    input.value = selectedSoftware.find(item => item.id === input.dataset.softwarePassword)?.password || '';
  });
  updateSoftwareHint();
}

function updateSoftwareHint() {
  const catalog = state.catalog?.software || [];
  const databaseCount = selectedSoftware.filter(item => ['postgres','mysql','mongo','maria','xampp'].includes(item.id)).length;
  const minimum = Math.max(databaseCount >= 4 ? 8192 : databaseCount >= 2 ? 4096 : 0, ...selectedSoftware.map(selected => catalog.find(item => item.id === selected.id)?.min_ram || 0));
  const flavor = state.catalog?.flavors.find(item => item.id === $('#create-form select[name=flavor_id]').value);
  $('#software-hint').textContent = selectedSoftware.length
    ? flavor && flavor.ram < minimum ? `메모리 ${minimum / 1024}GB 이상인 사양을 선택해 주세요.` : `설치·설정·실행까지 자동으로 진행합니다. 전체 예상 ${selectedSoftware.length <= 3 ? '3~8' : selectedSoftware.length <= 8 ? '5~15' : '8~25'}분 · 접속 화면에서 진행 상황을 확인하세요.`
    : '선택하지 않아도 VM을 만들 수 있습니다.';
}

$('#software-select').addEventListener('change', event => {
  if (event.target.value.startsWith('preset:')) {
    const ids = {'preset:web':['node','react','postgres'],'preset:python':['python','postgres'],'preset:java':['spring','postgres']}[event.target.value] || [];
    for (const id of ids) {
      const item = state.catalog?.software?.find(item => item.id === id);
      if (item && !selectedSoftware.some(selected => selected.id === id)) selectedSoftware.push({id,version:item.versions[0],...(item.port ? {port:item.port} : {}),...(item.credentials ? {username:'student',password:''} : {})});
    }
    pendingCreateRequestId = null; renderSoftwarePicker(); return;
  }
  const item = state.catalog?.software?.find(item => item.id === event.target.value);
  if (!item || selectedSoftware.some(selected => selected.id === item.id)) return;
  selectedSoftware.push({id:item.id,version:item.versions[0],...(item.port ? {port:item.port} : {}),...(item.credentials ? {username:'student',password:''} : {})});
  pendingCreateRequestId = null;
  renderSoftwarePicker();
});
$('#software-list').addEventListener('change', event => {
  const selected = selectedSoftware.find(item => item.id === event.target.dataset.softwareVersion);
  if (selected) { selected.version = event.target.value; pendingCreateRequestId = null; }
  const portItem = selectedSoftware.find(item => item.id === event.target.dataset.softwarePort);
  if (portItem) { portItem.port = Number(event.target.value); pendingCreateRequestId = null; }
});
$('#software-list').addEventListener('click', event => {
  const toggle = event.target.closest('[data-service-password-toggle]');
  if (toggle) {
    const input = $$('[data-software-password]').find(input => input.dataset.softwarePassword === toggle.dataset.servicePasswordToggle);
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    toggle.textContent = visible ? '숨기기' : '보기';
    toggle.setAttribute('aria-pressed', String(visible));
    toggle.setAttribute('aria-label', visible ? '비밀번호 숨기기' : '비밀번호 보기');
    return;
  }
  const id = event.target.closest('[data-software-remove]')?.dataset.softwareRemove;
  if (!id) return;
  selectedSoftware = selectedSoftware.filter(item => item.id !== id);
  pendingCreateRequestId = null;
  renderSoftwarePicker();
});
$('#software-list').addEventListener('input', event => {
  const username = selectedSoftware.find(item => item.id === event.target.dataset.softwareUsername);
  const password = selectedSoftware.find(item => item.id === event.target.dataset.softwarePassword);
  if (username) username.username = event.target.value;
  if (password) password.password = event.target.value;
});

window.addEventListener('message', event => {
  const vmId = event.data?.vmId;
  if (event.origin !== location.origin || event.data?.type !== 'uccloud:ssh-ready' || typeof vmId !== 'string') return;
  const handoff = pendingTerminalHandoffs.get(vmId);
  if (!handoff || event.source !== handoff.tab || handoff.tab.closed) return;
  handoff.tab.postMessage({type:'uccloud:ssh-credentials',vmId,username:handoff.username,password:handoff.password}, location.origin);
  handoff.password = '';
  clearTimeout(handoff.timer);
  pendingTerminalHandoffs.delete(vmId);
});

function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => el.className = 'toast', 3600);
}

function apiErrorMessage(data, status, path) {
  if (path.startsWith('/api/admin/fans') && typeof data?.detail === 'string' && /[가-힣]/.test(data.detail)) return data.detail;
  if (status === 401) {
    if (path === '/api/auth/login') return '아이디/이메일 또는 비밀번호가 올바르지 않습니다.';
    if (path === '/api/profile/password' && data?.detail === 'Invalid current password') return '현재 비밀번호가 올바르지 않습니다.';
    return '로그인이 필요합니다.';
  }
  if (status === 409 && data?.detail === 'Username or email already exists') return '이미 사용 중인 아이디 또는 이메일입니다.';
  if (status === 429) return '시도가 너무 많습니다. 잠시 후 다시 시도하세요.';
  if (path.startsWith('/api/auth/') && typeof data?.detail === 'string' && /[가-힣]/.test(data.detail)) return data.detail;
  if ([502, 503, 504].includes(status)) return '클라우드 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.';
  if (data?.detail?.code === 'team_exists') return '이미 있는 팀입니다.';
  const knownErrors = {
    'Account is not ready': '작업 공간을 준비 중입니다. 잠시 후 다시 확인해 주세요.',
    'Account is not active': '작업 공간을 준비 중입니다. 잠시 후 다시 확인해 주세요.',
    'Team resources are not ready': '작업 공간을 준비 중입니다. 잠시 후 다시 확인해 주세요.',
    'Invalid CSRF token': '화면을 새로고침한 뒤 다시 시도해 주세요.',
    'Administrator access required': '운영자만 할 수 있습니다.',
    'Team owner access required': '팀장만 할 수 있습니다.',
    'Instance not found': 'VM을 찾지 못했습니다. 목록을 다시 확인해 주세요.',
    'Instance not found or not running': 'VM이 꺼져 있거나 상태가 바뀌었습니다. 목록을 확인해 주세요.',
    'Invalid VM login ID': 'VM 접속 ID를 확인해 주세요.',
    'Invalid VM password': 'VM 접속 비밀번호를 확인해 주세요.',
    'Image is not allowed': '선택한 운영체제를 사용할 수 없습니다. 화면을 새로고침해 주세요.',
    'Flavor is not allowed': '선택한 사양을 사용할 수 없습니다. 화면을 새로고침해 주세요.',
  };
  if (typeof data?.detail === 'string' && Object.hasOwn(knownErrors, data.detail)) return knownErrors[data.detail];
  if (Array.isArray(data?.detail)) {
    const fields = { username: '아이디', email: '이메일', password: '비밀번호', current_password: '현재 비밀번호', new_password: '새 비밀번호', team_name: '팀 이름' };
    const issue = data.detail[0];
    const key = Array.isArray(issue?.loc) ? issue.loc.at(-1) : null;
    return `${fields[key] || '입력값'}을 확인하세요.`;
  }
  return typeof data?.detail === 'string' && /[가-힣]/.test(data.detail)
    ? data.detail : '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.';
}

function authMessage(message, success = false) {
  const el = $('#auth-message');
  el.textContent = message;
  el.classList.toggle('success', success);
  el.classList.remove('pending');
}

async function submitAuth(event, path, busyLabel, onSuccess, onError) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  if (button.disabled) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = busyLabel;
  authMessage(busyLabel);
  $('#auth-message').classList.add('pending');
  try {
    const data = await api(path, { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    await onSuccess(data, form);
  } catch (error) {
    if (onError) {
      try { if (await onError(error, form)) return; }
      catch (nextError) { error = nextError; }
    }
    authMessage(error.message);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function api(path, options = {}) {
  const headers = { ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {}) };
  if (state.csrf && !['GET', 'HEAD'].includes(options.method || 'GET')) headers['X-CSRF-Token'] = state.csrf;
  let response;
  try { response = await fetch(path, { credentials: 'same-origin', ...options, headers }); }
  catch { throw new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.'); }
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(apiErrorMessage(data, response.status, path));
    error.code = data?.detail?.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

function focusAuthInput(force = false) {
  if (!force && !matchMedia('(pointer: fine)').matches) return;
  const form = $('#register-form').classList.contains('hidden') ? $('#login-form') : $('#register-form');
  if (form.contains(document.activeElement)) return;
  form.querySelector('input')?.focus({preventScroll:true});
}

function showView(id) {
  ['auth-view','pending-view','dashboard-view'].forEach(v => $(`#${v}`).classList.toggle('hidden', v !== id));
  if (id !== 'dashboard-view') document.title = 'UCcloud';
  if (id === 'auth-view') focusAuthInput();
}

function showRequestCount(count) {
  $('#request-count').textContent = count ? ` (${count})` : '';
  document.title = count ? `가입 요청 ${count}건 · UCcloud` : 'UCcloud';
}

async function bootstrap() {
  try {
    const me = await api('/api/me'); state.me = me; state.csrf = me.csrf_token;
    if (me.status !== 'active') {
      $('#pending-title').textContent = me.status === 'rejected' ? '팀 가입이 거절됐습니다' : me.team_owner ? '작업 공간 준비 중' : '팀장 승인 대기 중';
      $('#pending-copy').textContent = me.status === 'rejected' ? '팀장에게 문의하세요.' : me.team_owner ? '준비가 지연되면 다시 시도하세요.' : `${me.team_name || me.requested_team || '팀'}에 신청했습니다. 팀장이 승인하면 사용할 수 있습니다.`;
      $('#retry-provision').classList.toggle('hidden', !me.team_owner || me.status !== 'pending');
      showView('pending-view'); return;
    }
    if (teamRequestsFor !== me.team_slug) {
      teamRequestsFor = me.team_slug;
      teamRequestsSignature = null;
      teamRequestFetchId += 1;
      $('#request-list').replaceChildren();
      showRequestCount(0);
    }
    showView('dashboard-view'); $('#team-name').textContent = me.team_name || '관리자'; $('#username').textContent = me.username;
    $$('.admin-only').forEach(el => el.classList.toggle('hidden', me.role !== 'admin' || state.publicMode));
    $$('.owner-only').forEach(el => el.classList.toggle('hidden', !me.team_owner));
    if (!me.team_owner) showRequestCount(0);
    await refreshCloud();
    await refreshBackups();
    if (me.team_owner) loadTeamRequests();
  } catch (error) {
    showView('auth-view');
    authMessage(error.status === 401 ? '' : (error.message || '서버 연결을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.'));
  }
}

async function refreshCloud(silent = false, forceQuota = false) {
  if (cloudRefreshing) return;
  if (!state.me.team_slug) {
    $('#cloud-status').classList.add('hidden');
    renderQuota({instances:{limit:0,in_use:0},cores:{limit:0,in_use:0},ram:{limit:0,in_use:0}}); renderInstances([]); return;
  }
  cloudRefreshing = true;
  let instancesUpdated = false;
  try {
    const fetchQuota = forceQuota || !state.quota || Date.now() - quotaUpdatedAt >= quotaRefreshInterval;
    const [instancesResult, quotaResult] = await Promise.allSettled([
      api('/api/instances'), fetchQuota ? api('/api/quota') : Promise.resolve(state.quota),
    ]);
    if (instancesResult.status === 'rejected' && instancesResult.reason.status === 401) throw instancesResult.reason;
    if (quotaResult.status === 'rejected' && quotaResult.reason.status === 401) throw quotaResult.reason;
    if (instancesResult.status === 'fulfilled') {
      const nextInstances = instancesResult.value.instances;
      const changed = JSON.stringify(nextInstances) !== JSON.stringify(state.instances);
      const idsChanged = nextInstances.length !== state.instances.length
        || nextInstances.some(vm => !state.instances.some(previous => previous.id === vm.id));
      state.instances = nextInstances;
      instancesUpdated = true;
      if (changed || pendingInstanceActions.size || !$('#instances-list').firstElementChild) renderInstances(state.instances);
      if (idsChanged && !fetchQuota) quotaUpdatedAt = 0;
    }
    if (quotaResult.status === 'fulfilled' && fetchQuota) {
      state.quota = quotaResult.value;
      quotaUpdatedAt = Date.now();
    }
    if (quotaResult.status === 'rejected' && fetchQuota) quotaUpdatedAt = 0;
    renderQuota(state.quota);
    if (instancesResult.status === 'rejected') throw instancesResult.reason;
    if (quotaResult.status === 'rejected') throw quotaResult.reason;
    $('#cloud-status').textContent = '';
    $('#cloud-status').classList.add('hidden');
  } catch (error) {
    if (error.status === 401) {
      state.me = null; state.csrf = null; state.instances = []; state.backups = []; state.backupEnabled = false; state.catalog = null; state.quota = null; quotaUpdatedAt = 0;
      teamRequestsSignature = null; teamRequestsFor = null; teamRequestFetchId += 1;
      $('#request-list').replaceChildren(); showRequestCount(0);
      pendingInstanceActions.clear(); openingConsoleIds.clear();
      renderInstances([]);
      $('#quota-summary').textContent = '';
      $('#cloud-status').classList.add('hidden');
      showView('auth-view');
      authMessage('로그인이 만료됐습니다. 다시 로그인해 주세요.');
      return;
    }
    $('#cloud-status').textContent = instancesUpdated
      ? 'VM 목록은 볼 수 있지만 사용량을 확인하지 못했습니다. 자동으로 다시 확인합니다.'
      : state.quota ? 'VM 목록이 최신 상태가 아닙니다. 자동으로 다시 확인합니다.'
        : 'VM 목록을 불러오지 못했습니다. 자동으로 다시 확인합니다.';
    $('#cloud-status').classList.remove('hidden');
    if (!silent) toast(error.message, true);
  }
  finally { cloudRefreshing = false; }
}

function renderQuota(quota) {
  const item = quota?.instances;
  const count = Math.max(Number(item?.in_use) || 0, state.instances.length);
  $('#quota-summary').textContent = Number.isInteger(item?.limit) && item.limit >= 0
    ? `VM ${count}개 / 최대 ${item.limit}개` : `VM ${count}개`;
}

function vmView(vm) {
  const status = String(vm.status || '').toUpperCase();
  const ips = (Array.isArray(vm.addresses) ? vm.addresses : []).filter(a => a?.address && (a.version == null || Number(a.version) === 4)).map(a => a.address);
  if (status === 'ACTIVE') return {status:'실행 중', css:'active', ip:ips.join(' · '), note:ips.length ? '' : 'IP를 기다리는 중입니다.', canTerminal:true, canConsole:true, canStart:false, canStop:true};
  if (status === 'SHUTOFF') return {status:'꺼짐', css:'', ip:ips.join(' · '), note:'', canTerminal:true, canConsole:false, canStart:true, canStop:false};
  if (status === 'BUILD') return {status:'준비 중', css:'', ip:ips.join(' · '), note:'VM을 만드는 중입니다.', canTerminal:true, canConsole:false, canStart:false, canStop:false};
  if (status === 'ERROR') return {status:'오류', css:'error', ip:ips.join(' · '), note:'VM 준비에 문제가 생겼습니다. 운영자에게 문의해 주세요.', canConsole:false, canStart:false, canStop:false};
  return {status:'상태 확인 중', css:'', ip:ips.join(' · '), note:'VM 상태가 바뀌는 중입니다. 목록이 자동으로 갱신됩니다.', canConsole:false, canStart:false, canStop:false};
}

function pendingFor(vm) {
  const pending = pendingInstanceActions.get(vm.id);
  if (!pending) return null;
  if (!pending.inFlight && (Date.now() >= pending.until || String(vm.status || '').toUpperCase() !== pending.status)) {
    pendingInstanceActions.delete(vm.id);
    return null;
  }
  return pending;
}

function markInstanceBusy(vm, label) {
  pendingInstanceActions.set(vm.id, {label, status:String(vm.status || '').toUpperCase(), inFlight:true, until:0});
  renderInstances(state.instances);
}

function settleInstanceBusy(id) {
  const pending = pendingInstanceActions.get(id);
  if (!pending) return;
  pending.inFlight = false;
  pending.until = Date.now() + 15000;
  renderInstances(state.instances);
}

function clearInstanceBusy(id) {
  pendingInstanceActions.delete(id);
  renderInstances(state.instances);
}

function recoverInstanceAction(id, error) {
  if (error.status == null || [429, 500, 502, 503, 504].includes(error.status)) {
    const pending = pendingInstanceActions.get(id);
    if (pending) pending.label = '상태 확인 중…';
    settleInstanceBusy(id);
    toast('요청 결과를 확인하지 못했습니다. VM 상태를 자동으로 확인합니다.');
  } else {
    clearInstanceBusy(id);
    toast(error.message, true);
  }
  void refreshCloud(true);
}

function renderInstances(instances) {
  const expanded = new Set(Array.from($('#instances-list').querySelectorAll?.('details[data-vm-details][open]') || [], item => item.dataset.vmDetails));
  const expandedManage = new Set(Array.from($('#instances-list').querySelectorAll?.('details[data-vm-manage][open]') || [], item => item.dataset.vmManage));
  for (const id of pendingInstanceActions.keys()) if (!instances.some(vm => vm.id === id)) pendingInstanceActions.delete(id);
  const rows = instances.map(vm => {
    const view = vmView(vm);
    const id = escapeHtml(vm.id);
    const pending = pendingFor(vm);
    const specs = vm.flavor_details;
    const flavorLabel = Number.isInteger(specs?.vcpus) && specs.vcpus > 0
      && Number.isInteger(specs?.ram) && specs.ram > 0
      && Number.isInteger(specs?.disk) && specs.disk >= 0
      ? `CPU ${specs.vcpus} · 메모리 ${Number((specs.ram / 1024).toFixed(1))}GB · 저장공간 ${specs.disk}GB`
      : vm.flavor;
    const primaryActions = [
      view.canTerminal ? `<a data-terminal href="/terminal?vm=${encodeURIComponent(vm.id)}${view.canStart ? '&start=1' : ''}" target="_blank" rel="noopener noreferrer">${view.canStart ? '켜고 접속' : '접속'}</a>` : `<button disabled>${view.canConsole ? '접속 준비 중' : '접속 대기'}</button>`,
    ].join('');
    const agentAction = view.canTerminal ? `<a data-agent href="/terminal?vm=${encodeURIComponent(vm.id)}&ai=1${view.canStart ? '&start=1' : ''}" target="_blank" rel="noopener noreferrer">AI 연결</a>` : '';
    const manageActions = [
      view.canTerminal ? `<a class="vm-programs" href="/terminal?vm=${encodeURIComponent(vm.id)}&software=1${view.canStart ? '&start=1' : ''}" target="_blank" rel="noopener noreferrer">프로그램 추가</a>` : '',
      `<button data-site data-id="${id}">사이트 상태</button>`,
      state.backupEnabled ? `<button data-backup data-id="${id}">백업·복구</button>` : '',
      view.canStart ? `<button data-action="start" data-id="${id}">켜기만</button>` : '',
      view.canStop ? `<button data-action="stop" data-id="${id}">끄기</button>` : '',
      view.canConsole ? `<button data-console data-id="${id}" ${openingConsoleIds.has(vm.id) ? 'disabled' : ''}>${openingConsoleIds.has(vm.id) ? '여는 중…' : '화면 콘솔'}</button>` : '',
      view.canStop ? `<button data-action="reboot" data-id="${id}">재부팅</button>` : '',
      `<button class="danger" data-delete data-id="${id}" data-name="${escapeHtml(vm.name)}">삭제</button>`,
    ].join('');
    const actions = pending ? `<button disabled aria-live="polite">${pending.label}</button>`
      : `${primaryActions}${agentAction}<details class="vm-manage" data-vm-manage="${id}" ${expandedManage.has(vm.id) ? 'open' : ''}><summary>관리</summary><div class="vm-manage-actions">${manageActions}</div></details>`;
    const technical = `<details class="vm-technical" data-vm-details="${id}" ${expanded.has(vm.id) ? 'open' : ''}><summary>자세히</summary>${vm.vm_username ? `<div><span>접속 ID</span><b>${escapeHtml(vm.vm_username)}</b></div>` : ''}<div><span>사양</span><b>${escapeHtml(flavorLabel)}</b></div>${view.ip ? `<div><span>내부 주소</span><b>${escapeHtml(view.ip)}</b></div>` : ''}</details>`;
    const setupNote = Array.isArray(vm.software) && vm.software.length ? '<small class="vm-state-note">프로그램 준비 상태는 접속 화면에서 확인하세요.</small>' : '';
    return `<article class="instance-card"><div><h3>${escapeHtml(vm.name)}</h3></div><div><span class="status-dot ${view.css}"></span><b>${view.status}</b></div><div>${technical}${view.note ? `<small class="vm-state-note">${view.note}</small>` : ''}${setupNote}</div><div class="instance-actions">${actions}</div></article>`;
  });
  $('#instances-list').innerHTML = rows.join('') || '<div class="empty">아직 VM이 없습니다.</div>';
}

async function refreshBackups() {
  if (!state.me?.team_slug || backupRefreshing) return;
  backupRefreshing = true;
  try {
    const result = await api('/api/backups');
    const changed = state.backupEnabled !== Boolean(result.enabled);
    state.backupEnabled = Boolean(result.enabled);
    state.backups = result.backups || [];
    if (changed) renderInstances(state.instances);
    if ($('#backup-dialog').open) renderBackupDialog();
  } catch (error) {
    if ($('#backup-dialog').open) $('#backup-hint').textContent = '백업 목록을 확인하지 못했습니다. 잠시 후 다시 확인해 주세요.';
  } finally { backupRefreshing = false; }
}

function renderBackupDialog() {
  const vm = state.instances.find(item => item.id === backupVmId);
  if (!vm) { $('#backup-dialog').close(); backupVmId = null; return; }
  const stopped = String(vm.status).toUpperCase() === 'SHUTOFF';
  const backups = state.backups.filter(item => item.server_id === vm.id);
  const working = backups.some(item => ['queued','snapshotting','exporting'].includes(item.state) || ['queued','importing','rebuilding'].includes(item.restore_state));
  $('#backup-vm-name').textContent = vm.name;
  $('#backup-hint').textContent = stopped ? '백업과 복구가 끝날 때까지 VM을 켜지 마세요.' : '백업하거나 복구하려면 먼저 VM을 꺼 주세요.';
  $('#backup-create').disabled = !stopped || working;
  const labels = {queued:'백업 대기 중',snapshotting:'백업 생성 중',exporting:'다른 노드에 저장 중',ready:'완료',failed:'백업 실패',deleting:'삭제 중'};
  $('#backup-list').innerHTML = backups.map(item => {
    const date = new Date(`${item.created_at.replace(' ', 'T')}Z`);
    const time = Number.isNaN(date.valueOf()) ? '' : date.toLocaleString('ko-KR');
    const size = item.size_bytes ? ` · ${(item.size_bytes / (1024 ** 3)).toFixed(1)}GB` : '';
    const restore = item.restore_state === 'queued' ? '복구 대기 중' : item.restore_state === 'importing' ? '백업 읽는 중' : item.restore_state === 'rebuilding' ? 'VM 복구 중' : item.restore_state === 'failed' ? '복구 실패' : '';
    const status = restore || labels[item.state] || '확인 중';
    const actions = ['ready','failed'].includes(item.state) ? `<div class="backup-item-actions">${item.state === 'ready' ? `<button data-restore="${escapeHtml(item.id)}" ${!stopped || working ? 'disabled' : ''}>이 시점으로 복구</button>` : ''}<button class="danger" data-backup-delete="${escapeHtml(item.id)}" ${working ? 'disabled' : ''}>백업 삭제</button></div>` : '';
    return `<div class="backup-item"><strong>${escapeHtml(status)}</strong><small>${escapeHtml(time + size)}</small>${item.error ? `<small>${escapeHtml(item.error)}</small>` : ''}${actions}</div>`;
  }).join('') || '<p class="muted">아직 백업이 없습니다.</p>';
}

function openBackupDialog(id) {
  backupVmId = id;
  renderBackupDialog();
  $('#backup-dialog').showModal();
  void refreshBackups();
}

function quotaAllows(quota, resource, needed) {
  const item = quota?.[resource];
  if (!Number.isInteger(item?.limit) || !Number.isInteger(item?.in_use)) return false;
  return item.limit < 0 || item.limit - item.in_use >= needed;
}

function populateCatalog(quota) {
  const flavors = state.catalog.flavors.filter(x => quotaAllows(quota, 'cores', x.vcpus) && quotaAllows(quota, 'ram', x.ram))
    .sort((a, b) => a.vcpus - b.vcpus || a.ram - b.ram || a.disk - b.disk);
  const images = [...state.catalog.images].sort((a, b) =>
    Number(b.name.includes('24.04')) - Number(a.name.includes('24.04')) || a.name.localeCompare(b.name));
  const imageSelect = $('#create-form select[name=image_id]'); const flavorSelect = $('#create-form select[name=flavor_id]');
  imageSelect.innerHTML = images.map(x => `<option value="${escapeHtml(x.id)}">${escapeHtml(x.name)}</option>`).join('');
  flavorSelect.innerHTML = flavors.map(x => `<option value="${escapeHtml(x.id)}">${escapeHtml(x.name)} · CPU ${x.vcpus} · 메모리 ${Number((x.ram / 1024).toFixed(1))}GB</option>`).join('');
  renderSoftwarePicker();
  updateCreateDefaults();
  return images.length > 0 && flavors.length > 0;
}

function suggestedVmName() {
  const used = new Set(state.instances.map(vm => String(vm.name).toLowerCase()));
  let number = 1;
  while (used.has(`vm-${number}`)) number += 1;
  return `vm-${number}`;
}

function updateCreateDefaults() {
  const imageId = $('#create-form select[name=image_id]').value;
  const flavorId = $('#create-form select[name=flavor_id]').value;
  const image = state.catalog?.images.find(item => item.id === imageId);
  const flavor = state.catalog?.flavors.find(item => item.id === flavorId);
  const vmUser = $('#create-form input[name=vm_username]').value || 'student';
  const memory = flavor ? ` · 메모리 ${Number((flavor.ram / 1024).toFixed(1))}GB` : '';
  $('#create-options-label').textContent = `${image?.name || '운영체제'}${memory} · ID ${vmUser}`;
  $('#flavor-disk').textContent = flavor ? `저장공간 ${flavor.disk}GB` : '';
  updateSoftwareHint();
}

async function loadTeamRequests(silent = false) {
  const teamSlug = state.me?.team_slug;
  if (!state.me?.team_owner || !teamSlug) return;
  const fetchId = ++teamRequestFetchId;
  try {
    const data = await api('/api/team/requests');
    if (fetchId !== teamRequestFetchId || state.me?.team_slug !== teamSlug) return;
    showRequestCount(data.requests.length);
    const signature = JSON.stringify(data.requests);
    if (signature === teamRequestsSignature && $('#request-list').firstElementChild) return;
    teamRequestsSignature = signature;
    $('#request-list').innerHTML = data.requests.map(user => `<article class="approval-card"><div><b>${escapeHtml(user.username)}</b><p>${escapeHtml(user.email)}</p></div><div><p>${escapeHtml(user.created_at)}</p></div><div class="approval-actions"><button class="approve" data-decide="approve" data-id="${user.id}">승인</button><button data-decide="reject" data-id="${user.id}">거절</button></div></article>`).join('') || '<div class="empty">새 가입 요청이 없습니다.</div>';
  } catch (error) { if (!silent) toast(error.message, true); }
}

async function pollBackgroundTeamRequests() {
  if (document.hidden && state.me?.team_owner) await loadTeamRequests(true);
}

async function loadEvents() {
  if (window.loadFanStatus) void window.loadFanStatus();
  void loadCapacity();
  void loadRegistrationCode();
  try {
    const data = await api('/api/admin/events');
    const labels = {'user.register':'가입','auth.register.rate_warning':'가입량 증가','auth.register.rate_limited':'가입 일시 제한','team.create.request':'새 팀 요청','team.provision.ready':'팀 준비 완료','team.provision.failed':'팀 준비 실패','team.join.request':'팀 가입 요청','team.join.approve':'팀 가입 승인','team.join.reject':'팀 가입 거절','instance.create':'VM 생성','instance.delete':'VM 삭제','auth.login':'로그인','cloud.health.failed':'클라우드 연결 문제','cloud.health.recovered':'클라우드 연결 복구'};
    $('#event-list').innerHTML = data.events.map(item => {
      const fanLabels = {'hardware.fan.request':'팬 설정 요청','hardware.fan.updated':'팬 설정 적용','hardware.fan.failed':'팬 설정 확인 필요'};
      if (Object.hasOwn(fanLabels, item.action)) labels[item.action] = fanLabels[item.action];
      const count = ['auth.register.rate_warning','auth.register.rate_limited'].includes(item.action)
        && /^\d{1,5}\/\d{1,5}$/.test(item.target || '') ? ` · ${item.target}` : '';
      return `<article class="approval-card"><div><b>${escapeHtml(labels[item.action] || item.action)}${escapeHtml(count)}</b><p>${escapeHtml(item.username || '시스템')}</p></div><div><p>${escapeHtml(item.created_at)}</p></div></article>`;
    }).join('') || '<div class="empty">기록이 없습니다.</div>';
  } catch (error) { toast(error.message, true); }
}

async function loadRegistrationCode() {
  const label = $('#admin-registration-code');
  const copy = $('#copy-registration-code');
  label.textContent = '확인 중…';
  copy.disabled = true;
  try {
    const data = await api('/api/admin/registration-code');
    const code = typeof data.registration_code === 'string' ? data.registration_code : '';
    label.textContent = code || '설정되지 않음';
    copy.disabled = !code;
  } catch { label.textContent = '불러오지 못했습니다.'; }
}

$('#copy-registration-code').addEventListener('click', async () => {
  const code = $('#admin-registration-code').textContent;
  try { await navigator.clipboard.writeText(code); toast('가입 코드를 복사했습니다.'); }
  catch { toast('복사되지 않았습니다. 코드를 선택해서 복사해 주세요.', true); }
});

async function loadCapacity() {
  const summary = $('#cluster-capacity');
  summary.textContent = '서버 상태 확인 중…';
  try {
    const data = await api('/api/admin/capacity');
    if (!Number.isInteger(data.total) || data.total < 1 || !Number.isInteger(data.online)) {
      summary.textContent = '서버 정보를 확인하지 못했습니다.';
      return;
    }
    const memory = Number.isInteger(data.memory_free_mb)
      ? ` · 남은 메모리 ${Math.floor(data.memory_free_mb / 1024)}GB` : '';
    summary.textContent = `서버 ${data.online}/${data.total} 정상${memory}`;
  } catch { summary.textContent = '서버 상태를 확인하지 못했습니다.'; }
}

function escapeHtml(value='') { const div = document.createElement('div'); div.textContent = String(value); return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

function setAuthPasswordVisibility(toggle, visible) {
  const input = document.getElementById(toggle.getAttribute('aria-controls'));
  input.type = visible ? 'text' : 'password';
  toggle.textContent = visible ? '숨기기' : '보기';
  toggle.setAttribute('aria-label', visible ? '비밀번호 숨기기' : '비밀번호 보기');
  toggle.setAttribute('aria-pressed', String(visible));
}
$$('[data-password-toggle]').forEach(toggle => toggle.addEventListener('click', () => {
  const input = document.getElementById(toggle.getAttribute('aria-controls'));
  setAuthPasswordVisibility(toggle, input.type === 'password');
  input.focus();
}));
$$('.tab').forEach(tab => tab.addEventListener('click', event => { $$('.tab').forEach(x => { x.classList.toggle('active', x === tab); x.setAttribute('aria-pressed', String(x === tab)); }); $('#login-form').classList.toggle('hidden', tab.dataset.tab !== 'login'); $('#register-form').classList.toggle('hidden', tab.dataset.tab !== 'register'); $$('[data-password-toggle]').forEach(toggle => setAuthPasswordVisibility(toggle, false)); authMessage(''); if (event.isTrusted || matchMedia('(pointer: fine)').matches) focusAuthInput(true); }));

const registerEmail = $('#register-form input[name=email]');
const registerUsername = $('#register-form input[name=username]');
const registerTeamName = $('#register-form input[name=team_name]');
let suggestedUsername = '';
let suggestedTeamName = '';
let usernameEdited = false;
let teamNameEdited = false;
function suggestTeamName() {
  if (teamNameEdited || (registerTeamName.value && registerTeamName.value !== suggestedTeamName)) return;
  const username = registerUsername.value.trim();
  suggestedTeamName = username.length >= 3 ? `${username}의 팀` : '';
  registerTeamName.value = suggestedTeamName;
}
registerEmail.addEventListener('input', () => {
  if (usernameEdited || (registerUsername.value && registerUsername.value !== suggestedUsername)) return;
  const localPart = registerEmail.value.trim().split('@')[0];
  const candidate = localPart.replace(/[^a-zA-Z0-9._-]/g, '').toLowerCase().slice(0, 32);
  suggestedUsername = candidate.length >= 3 ? candidate : '';
  registerUsername.value = suggestedUsername;
  suggestTeamName();
});
registerUsername.addEventListener('input', () => { usernameEdited = true; suggestTeamName(); });
registerTeamName.addEventListener('input', () => { teamNameEdited = true; });
$('#register-form').addEventListener('reset', () => {
  suggestedUsername = '';
  suggestedTeamName = '';
  usernameEdited = false;
  teamNameEdited = false;
});

$('#login-form').addEventListener('submit', event => submitAuth(event, '/api/auth/login', '로그인 확인 중…', async (data, form) => {
  state.csrf = data.csrf_token;
  form.querySelector('input[name=password]').value = '';
  await bootstrap();
}));
async function finishRegistration(data, form) {
  const fields = Object.fromEntries(new FormData(form));
  authMessage(data.message, true);
  const login = await api('/api/auth/login', {method:'POST', body:JSON.stringify({username:fields.username,password:fields.password})});
  state.csrf = login.csrf_token;
  form.reset();
  setAuthPasswordVisibility($('#register-form [data-password-toggle]'), false);
  await bootstrap();
  if (data.ready === false && state.me?.status === 'pending' && state.me.team_owner) {
    $('#pending-copy').textContent = '작업 공간을 다시 준비하는 중입니다. 잠시 기다려 주세요.';
    automaticProvisionRetry = setTimeout(() => { void retryProvision(true); }, 3000);
  }
}

$('#register-form').addEventListener('submit', event => submitAuth(event, '/api/auth/register', '계정 만드는 중…', finishRegistration, async (error, form) => {
  if (error.code !== 'team_exists') return false;
  if (!confirm('이미 있는 팀입니다. 팀장에게 가입 요청을 보낼까요?')) { authMessage('가입을 취소했습니다.'); return true; }
  const fields = Object.fromEntries(new FormData(form));
  const data = await api('/api/auth/register', {method:'POST', body:JSON.stringify({...fields,join_existing:true})});
  await finishRegistration(data, form);
  return true;
}));

const accountPasswordDialog = $('#password-dialog');
let recoveryMode = 'id';
function openRecovery(mode) {
  recoveryMode = mode;
  $('#recovery-title').textContent = mode === 'id' ? '아이디 찾기' : '비밀번호 찾기';
  $('#recovery-form button[type=submit]').textContent = mode === 'id' ? '확인' : '변경 주소 받기';
  $('#recovery-result').textContent = '';
  $('#recovery-form').reset();
  $('#recovery-dialog').showModal();
  $('#recovery-form input').focus();
}
$('#find-id').addEventListener('click', () => openRecovery('id'));
$('#find-password').addEventListener('click', () => openRecovery('password'));
$('#recovery-close').addEventListener('click', () => $('#recovery-dialog').close());
$('#recovery-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type=submit]');
  button.disabled = true;
  const mode = recoveryMode;
  $('#recovery-result').textContent = '확인 중…';
  try {
    const result = await api(`/api/auth/${mode === 'id' ? 'find-id' : 'forgot-password'}`, {method:'POST',body:JSON.stringify({email:$('#recovery-form input').value.trim()})});
    $('#recovery-result').textContent = mode === 'id' ? result.username ? `아이디: ${result.username}` : '이 이메일로 가입한 계정을 찾지 못했습니다.' : result.message;
  } catch (error) { $('#recovery-result').textContent = error.message; }
  finally { button.disabled = false; }
});
const accountPasswordForm = $('#password-form');
const accountPasswordStatus = $('#password-status');
$('#change-password').addEventListener('click', () => {
  accountPasswordDialog.showModal();
  accountPasswordForm.elements.current_password.focus();
});
$('#close-password').addEventListener('click', () => accountPasswordDialog.close());
accountPasswordDialog.addEventListener('close', () => {
  accountPasswordForm.reset();
  setAuthPasswordVisibility($('#password-form [data-password-toggle]'), false);
  accountPasswordStatus.textContent = '';
  accountPasswordStatus.classList.add('hidden');
});
accountPasswordForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = accountPasswordForm.querySelector('button[type=submit]');
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = '변경 중…';
  accountPasswordStatus.textContent = '비밀번호를 확인하는 중입니다.';
  accountPasswordStatus.classList.remove('hidden');
  try {
    await api('/api/profile/password', {method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(accountPasswordForm)))});
    accountPasswordDialog.close();
    toast('비밀번호를 바꿨습니다.');
  } catch (error) {
    accountPasswordStatus.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = '변경하기';
  }
});
$$('[data-logout]').forEach(btn => btn.addEventListener('click', async () => { try { await api('/api/auth/logout',{method:'POST'}); } catch {} state={me:null,csrf:null,instances:[],catalog:null,quota:null}; quotaUpdatedAt=0; location.reload(); }));
$$('.nav-item').forEach(btn => btn.addEventListener('click', () => { $$('.nav-item').forEach(x=>x.classList.toggle('active',x===btn)); $$('.page-section').forEach(s=>s.classList.toggle('hidden',s.id!==`section-${btn.dataset.section}`)); if(btn.dataset.section==='admin') loadEvents(); if(btn.dataset.section==='team') loadTeamRequests(); }));
$('#copy-team-invite').addEventListener('click', async event => {
  if (!state.me?.team_owner || !state.me.team_name) return;
  const button = event.currentTarget;
  const link = new URL('/', location.origin);
  link.searchParams.set('team', state.me.team_name);
  try {
    await navigator.clipboard.writeText(link.href);
    button.textContent = '복사됨';
    clearTimeout(button.copyTimer);
    button.copyTimer = setTimeout(() => { button.textContent = '초대 링크 복사'; }, 2500);
  } catch { toast('링크를 복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.', true); }
});
async function retryProvision(automatic = false) {
  const button = $('#retry-provision');
  if (button.disabled || state.me?.status !== 'pending' || !state.me.team_owner) return;
  clearTimeout(automaticProvisionRetry);
  automaticProvisionRetry = null;
  button.disabled = true;
  button.textContent = '준비 중…';
  try {
    await api('/api/team/provision', {method:'POST'});
    await bootstrap();
  } catch (error) {
    $('#pending-copy').textContent = '준비가 지연되고 있습니다. 다시 시도해 주세요.';
    if (!automatic) toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = '다시 시도';
  }
}
$('#retry-provision').addEventListener('click', () => { void retryProvision(); });

let createPreparationId = 0;
async function prepareCreate() {
  const dialog = $('#create-dialog');
  const preparationId = ++createPreparationId;
  const submit = $('#create-form button[type=submit]');
  const retry = $('#retry-create-setup');
  const status = $('#create-status');
  retry.classList.add('hidden');
  submit.disabled = true;
  submit.textContent = '준비 중…';
  status.textContent = 'VM 설정을 확인하는 중입니다.';
  status.classList.add('pending');
  status.classList.remove('hidden');
  try {
    let quota, catalog;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        [quota, catalog] = await Promise.all([
          api('/api/quota'),
          state.catalog ? Promise.resolve(state.catalog) : api('/api/catalog').then(result => (state.catalog = result)),
        ]);
        break;
      } catch (error) {
        if (attempt === 2 || (error.status != null && ![502, 503, 504].includes(error.status))) throw error;
        if (!dialog.open || preparationId !== createPreparationId) return;
        status.textContent = '클라우드를 다시 확인하는 중입니다.';
        await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 1000));
      }
    }
    if (!dialog.open || preparationId !== createPreparationId) return;
    state.catalog = catalog;
    state.quota = quota;
    quotaUpdatedAt = Date.now();
    renderQuota(state.quota);
    if (!quotaAllows(quota, 'instances', 1)) {
      throw new Error('이 팀은 VM을 더 만들 수 없습니다. 기존 VM을 삭제하거나 운영자에게 문의해 주세요.');
    }
    if (!populateCatalog(quota)) {
      throw new Error('남은 사양이 부족합니다. 기존 VM을 정리하거나 운영자에게 문의해 주세요.');
    }
    status.classList.add('hidden');
    status.classList.remove('pending');
    submit.disabled = false;
  } catch (error) {
    if (!dialog.open || preparationId !== createPreparationId) return;
    status.textContent = error.message;
    status.classList.remove('pending');
    retry.classList.remove('hidden');
  } finally {
    if (dialog.open && preparationId === createPreparationId) submit.textContent = '만들고 접속';
  }
}
$('#open-create').addEventListener('click', async () => {
  const dialog = $('#create-dialog');
  if (dialog.open) return;
  const name = $('#create-form input[name=name]');
  if (!name.value) name.value = suggestedVmName();
  $('#create-options').open = false;
  dialog.showModal();
  $('#create-form input[name=vm_password]').focus();
  await prepareCreate();
});
$('#retry-create-setup').addEventListener('click', () => { void prepareCreate(); });
$('[data-close]').addEventListener('click',()=>$('#create-dialog').close());
const vmPasswordInput = $('#create-form input[name=vm_password]');
const vmPasswordToggle = $('#vm-password-toggle');
vmPasswordToggle.addEventListener('click', () => {
  const visible = vmPasswordInput.type === 'password';
  vmPasswordInput.type = visible ? 'text' : 'password';
  vmPasswordToggle.textContent = visible ? '숨기기' : '보기';
  vmPasswordToggle.setAttribute('aria-label', visible ? '비밀번호 숨기기' : '비밀번호 보기');
  vmPasswordToggle.setAttribute('aria-pressed', String(visible));
  vmPasswordInput.focus();
});
$('#create-dialog').addEventListener('close', () => {
  createPreparationId += 1;
  $('#retry-create-setup').classList.add('hidden');
  $('#create-form button[type=submit]').textContent = '만들고 접속';
  vmPasswordInput.value = '';
  vmPasswordInput.type = 'password';
  vmPasswordToggle.textContent = '보기';
  vmPasswordToggle.setAttribute('aria-label', '비밀번호 보기');
  vmPasswordToggle.setAttribute('aria-pressed', 'false');
  $('#create-status').classList.add('hidden');
  $('#create-status').classList.remove('pending');
  selectedSoftware = [];
  renderSoftwarePicker();
});
$('#create-form').addEventListener('invalid', event => { if ($('#create-options').contains(event.target)) $('#create-options').open = true; }, true);
$$('#create-options select, #create-options input').forEach(field => field.addEventListener('input', updateCreateDefaults));
let pendingCreateRequestId = null;
$('#create-form').addEventListener('input', () => {
  if (!$('#create-form button[type=submit]').disabled) pendingCreateRequestId = null;
});
async function findCreatedVm(requestId) {
  for (let attempt = 0; attempt < 24 && $('#create-dialog').open; attempt += 1) {
    try {
      const result = await api('/api/instances');
      if (!$('#create-dialog').open) return null;
      const found = result.instances.find(vm => vm.request_id === requestId);
      if (found) return found;
    } catch (error) { if (error.status === 401) throw error; }
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
  return null;
}
$('#create-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  if (button.disabled) return;
  const body = Object.fromEntries(new FormData(form));
  body.software = selectedSoftware.map(item => ({...item}));
  pendingCreateRequestId ||= crypto.randomUUID();
  body.request_id = pendingCreateRequestId;
  $('#create-status').classList.add('hidden');
  // Open during the click so the browser does not block the SSH tab after the API call.
  const tab = window.open('about:blank', '_blank');
  if (tab) {
    tab.document.title = 'VM 만드는 중 · UCcloud';
    tab.document.body.style.cssText = 'margin:0;padding:40px;font:16px "Segoe UI","Malgun Gothic",sans-serif;color:#1f2429;background:#f7f8fa';
    tab.document.body.textContent = 'VM을 만드는 중입니다…';
  }
  button.disabled = true;
  button.textContent = 'VM 만드는 중…';
  try {
    let created;
    try { created = await api('/api/instances',{method:'POST',body:JSON.stringify(body)}); }
    catch (error) {
      if (error.status != null && ![429, 500, 502, 503, 504].includes(error.status)) throw error;
      button.textContent = '생성 확인 중…';
      $('#create-status').textContent = 'VM 생성 결과를 확인하는 중입니다. 잠시 기다려 주세요.';
      $('#create-status').classList.remove('hidden');
      if (tab && !tab.closed) tab.document.body.textContent = 'VM 생성 결과를 확인하는 중입니다…';
      created = await findCreatedVm(body.request_id);
      if (!created) {
        tab?.close();
        if ($('#create-dialog').open) $('#create-status').textContent = 'VM을 아직 확인하지 못했습니다. 내 VM 목록을 확인한 뒤 다시 시도해 주세요.';
        return;
      }
    }
    if (created?.id) {
      pendingCreateRequestId = null;
      if (tab && !tab.closed) {
        const handoff = {tab,username:body.vm_username,password:body.vm_password};
        handoff.timer = setTimeout(() => {
          handoff.password = '';
          pendingTerminalHandoffs.delete(created.id);
        }, 30000);
        pendingTerminalHandoffs.set(created.id, handoff);
        tab.location.replace(`/terminal?vm=${encodeURIComponent(created.id)}&wait=1&handoff=1`);
        $('#create-dialog').close();
        form.reset();
        $('#create-options').open = false;
        updateCreateDefaults();
        quotaUpdatedAt = 0;
        void refreshCloud(true, true);
        return;
      }
      location.assign(`/terminal?vm=${encodeURIComponent(created.id)}&wait=1`);
      return;
    }
    tab?.close();
    pendingCreateRequestId = null;
    $('#create-dialog').close();
    form.reset();
    $('#create-options').open = false;
    updateCreateDefaults();
    toast('VM을 만들었습니다. 목록을 확인해 주세요.');
    quotaUpdatedAt = 0;
    setTimeout(() => refreshCloud(true, true),1800);
  } catch(error) {
    tab?.close();
    pendingCreateRequestId = null;
    toast(error.message,true);
  } finally {
    body.vm_password = '';
    body.software.forEach(item => { if ('password' in item) item.password = ''; });
    button.disabled = false;
    button.textContent = '만들고 접속';
  }
});

async function handleInstanceAction(event) {
  const button = event.target.closest('button');
  if (!button || button.disabled || !button.dataset.id) return;
  const id = button.dataset.id;
  const vm = state.instances.find(item => item.id === id);
  if (!vm || pendingFor(vm)) return;
  if (button.dataset.site !== undefined) { openSiteDialog(id); return; }
  if (button.dataset.backup !== undefined) { openBackupDialog(id); return; }
  if (button.dataset.console !== undefined) {
    if (openingConsoleIds.has(id)) return;
    const tab = window.open('about:blank', '_blank');
    if (!tab) { toast('팝업을 허용한 뒤 다시 눌러 주세요.', true); return; }
    tab.opener = null;
    openingConsoleIds.add(id);
    renderInstances(state.instances);
    try {
      const data = await api(`/api/instances/${id}/console`, {method:'POST'});
      tab.location.href = data.url;
    } catch (error) { tab.close(); toast(error.message, true); }
    finally { openingConsoleIds.delete(id); renderInstances(state.instances); }
    return;
  }
  const action = button.dataset.action;
  if (['start', 'stop', 'reboot'].includes(action)) {
    const busy = {start:'시작 중…', stop:'정지 중…', reboot:'재부팅 중…'};
    markInstanceBusy(vm, busy[action]);
    try {
      await api(`/api/instances/${id}/action`, {method:'POST', body:JSON.stringify({action})});
      settleInstanceBusy(id);
      const messages = {start:'VM을 켜는 중입니다.', stop:'VM을 끄는 중입니다.', reboot:'VM을 다시 시작하는 중입니다.'};
      toast(messages[action]);
      setTimeout(() => refreshCloud(true), 1200);
    } catch (error) { recoverInstanceAction(id, error); }
    return;
  }
  if (button.dataset.delete !== undefined) {
    if (!confirm(`${button.dataset.name} VM을 삭제할까요? VM 안의 파일도 모두 사라집니다.`)) return;
    markInstanceBusy(vm, '삭제 중…');
    try {
      await api(`/api/instances/${id}`, {method:'DELETE'});
      settleInstanceBusy(id);
      toast('VM을 삭제하는 중입니다.');
      quotaUpdatedAt = 0;
      setTimeout(() => refreshCloud(true, true), 1200);
    } catch (error) { recoverInstanceAction(id, error); }
  }
}
$('#instances-list').addEventListener('click', handleInstanceAction);

function showSiteResult(site) {
  const result = $('#site-result');
  result.textContent = site?.message || '주소를 입력하고 확인해 주세요.';
  result.className = `site-result ${site?.state || ''}`;
  const link = $('#site-open');
  const url = site?.url || '';
  if (/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$/.test(url)) {
    link.href = url;
    link.classList.remove('hidden');
  } else {
    link.removeAttribute('href');
    link.classList.add('hidden');
  }
}

async function openSiteDialog(id) {
  siteVmId = id;
  $('#site-vm-name').textContent = state.instances.find(item => item.id === id)?.name || '';
  $('#site-url').value = '';
  showSiteResult({message:'저장된 주소를 확인 중입니다.'});
  $('#site-dialog').showModal();
  try {
    const {site} = await api(`/api/instances/${id}/site`);
    if (siteVmId !== id) return;
    if (site) $('#site-url').value = site.url;
    showSiteResult(site);
  } catch (error) { if (siteVmId === id) showSiteResult({message:error.message}); }
}

$('#close-site').addEventListener('click', () => $('#site-dialog').close());
$('#site-dialog').addEventListener('close', () => { siteVmId = null; });
$('#site-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!siteVmId) return;
  const id = siteVmId;
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = '확인 중…';
  showSiteResult({message:'사이트에 연결하는 중입니다.'});
  try {
    const site = await api(`/api/instances/${id}/site/check`, {method:'POST',body:JSON.stringify({url:$('#site-url').value})});
    if (siteVmId === id) { $('#site-url').value = site.url; showSiteResult(site); }
  } catch (error) { if (siteVmId === id) showSiteResult({message:error.message}); }
  finally { button.disabled = false; button.textContent = '확인하기'; }
});

$('#close-backup').addEventListener('click', () => $('#backup-dialog').close());
$('#backup-dialog').addEventListener('close', () => { backupVmId = null; });
$('#backup-create').addEventListener('click', async event => {
  if (!backupVmId || event.currentTarget.disabled) return;
  const button = event.currentTarget;
  button.disabled = true; button.textContent = '요청 중…';
  try {
    await api(`/api/instances/${backupVmId}/backups`, {method:'POST'});
    toast('백업을 시작했습니다. 완료되면 이 화면에 표시됩니다.');
    await refreshBackups();
  } catch (error) { toast(error.message, true); }
  finally { button.textContent = '백업 만들기'; renderBackupDialog(); }
});
$('#backup-list').addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled || !backupVmId) return;
  const id = button.dataset.restore || button.dataset.backupDelete;
  if (!id) return;
  const restoring = button.dataset.restore !== undefined;
  const vm = state.instances.find(item => item.id === backupVmId);
  if (!vm) return;
  if (restoring && !confirm(`${vm.name}의 현재 파일을 선택한 백업으로 바꿀까요? 지금 상태는 되돌릴 수 없습니다.`)) return;
  if (!restoring && !confirm('이 백업을 삭제할까요? 삭제하면 이 시점으로 복구할 수 없습니다.')) return;
  button.disabled = true;
  try {
    await api(`/api/instances/${backupVmId}/backups/${id}${restoring ? '/restore' : ''}`, {method:restoring ? 'POST' : 'DELETE'});
    toast(restoring ? '복구를 시작했습니다. 완료될 때까지 VM을 켜지 마세요.' : '백업을 삭제하는 중입니다.');
    await refreshBackups();
  } catch (error) { toast(error.message, true); renderBackupDialog(); }
});

$('#request-list').addEventListener('click',async event=>{const button=event.target;const decision=button.dataset.decide;const id=button.dataset.id;if(!decision)return;if(decision==='reject'&&!confirm('이 팀 가입 요청을 거절할까요?'))return;button.disabled=true;try{await api(`/api/team/requests/${id}/${decision}`,{method:'POST'});toast(decision==='approve'?'팀원을 승인했습니다.':'가입 요청을 거절했습니다.');await loadTeamRequests();}catch(error){toast(error.message,true);}finally{button.disabled=false;}});

async function loadPublicMode() {
  try {
    const response = await fetch('/api/public-config', { credentials: 'same-origin' });
    if (!response.ok) return;
    const config = await response.json();
    state.publicMode = config.operator_enabled === false;
    if (config.registration_code_required) {
      $('#registration-code-field').classList.remove('hidden');
      $('#registration-code-field input').required = true;
    }
  } catch { /* Keep the normal private-portal view. */ }
}

setInterval(() => { if (document.hidden) return; if (state.me?.status === 'pending') bootstrap(); else if (state.me?.team_owner) loadTeamRequests(); }, 15000);
setInterval(() => { void pollBackgroundTeamRequests(); }, 60000);
setInterval(() => { if (state.me?.status === 'active' && !document.hidden) { refreshCloud(true); refreshBackups(); } }, 10000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  if (state.me?.status === 'pending') bootstrap();
  else {
    if (state.me?.status === 'active') { refreshCloud(true); refreshBackups(); }
    if (state.me?.team_owner) loadTeamRequests();
  }
});
const invitedTeam = new URLSearchParams(location.search).get('team')?.trim();
if (invitedTeam && invitedTeam.length >= 2 && invitedTeam.length <= 60) {
  $('#register-form input[name=team_name]').value = invitedTeam;
  $('.tab[data-tab=register]').click();
}
loadPublicMode().finally(bootstrap);

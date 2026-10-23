(() => {
  let loading = false, changing = false;
  const nodes = new Map();
  const allowed = new Set(['controller','compute-1','compute-2','compute-3','compute-4']);
  const number = value => typeof value === 'number' && Number.isFinite(value) ? String(value) : '—';
  const feedback = message => { $('#fan-feedback').textContent = message; };
  function renderFans(items) {
    nodes.clear();
    const rows = items.filter(item => allowed.has(item.id)).map(item => {
      nodes.set(item.id, item);
      const ready = item.state === 'ok' && item.supported && Number.isInteger(item.minimum);
      const values = [...new Set([0,10,20,30,40,50,60,70,80,90,100,...(ready ? [item.minimum] : [])])].sort((a,b)=>a-b);
      const options = values.map(value => `<option value="${value}" ${item.minimum === value ? 'selected' : ''}>${value === 0 ? '자동 (권장)' : `최소 ${value}%`}</option>`).join('');
      const fans = (item.fans || []).map(fan => `${escapeHtml(fan.name)}: ${number(fan.percent)}%`).join('<br>') || '연결 확인 필요';
      const warning = item.state === 'warning' ? '<small class="fan-warning">온도·팬 경고</small>' : '';
      return `<tr><th scope="row">${escapeHtml(item.name)}<small>${escapeHtml(item.role)}</small></th><td>${fans}${warning}</td><td>${number(item.cpu_c)}°C</td><td><label class="fan-setting"><span class="sr-only">${escapeHtml(item.name)} 최소 팬 속도</span><select data-fan-setting="${item.id}" ${ready ? '' : 'disabled'}>${options}</select></label></td><td><button class="secondary" type="button" data-fan-apply="${item.id}" ${ready ? '' : 'disabled'}>적용</button></td></tr>`;
    }).join('');
    $('#fan-nodes').innerHTML = rows ? `<table class="fan-table"><thead><tr><th>서버</th><th>현재 속도</th><th>CPU</th><th>최소 속도</th><th><span class="sr-only">설정 적용</span></th></tr></thead><tbody>${rows}</tbody></table>` : '<p>서버 정보를 확인하지 못했습니다.</p>';
  }
  async function loadFanStatus() {
    if (loading || changing || state.me?.role !== 'admin' || state.publicMode) return;
    loading = true; $('#fan-refresh').disabled = true; feedback('팬 상태 확인 중…');
    try {
      const data = await api('/api/admin/fans');
      renderFans(Array.isArray(data.nodes) ? data.nodes : []);
      feedback(`마지막 확인 ${new Date().toLocaleTimeString('ko-KR')} · 적용 후 실제 속도가 바뀌기까지 잠시 걸릴 수 있습니다.`);
    } catch (error) { nodes.clear(); $('#fan-nodes').replaceChildren(); feedback(error.message); }
    finally { loading = false; $('#fan-refresh').disabled = false; }
  }
  $('#fan-refresh').addEventListener('click', loadFanStatus);
  $('#fan-nodes').addEventListener('click', async event => {
    const button = event.target.closest('[data-fan-apply]');
    if (!button || changing || loading) return;
    const id = button.dataset.fanApply, item = nodes.get(id);
    if (!item || !allowed.has(id) || !item.supported || item.state !== 'ok') return;
    const minimum = Number(document.querySelector(`[data-fan-setting="${id}"]`).value);
    if (!Number.isInteger(minimum) || minimum < 0 || minimum > 100) return;
    if (minimum === item.minimum) { feedback('이미 적용된 설정입니다.'); return; }
    if (!window.confirm(`${item.name}을 ${minimum === 0 ? '자동 냉각' : `최소 ${minimum}%`}으로 설정할까요?`)) return;
    changing = true; $$('#fan-nodes button, #fan-nodes select').forEach(control => { control.disabled = true; });
    $('#fan-refresh').disabled = true; button.textContent = '적용 중…'; feedback('설정 적용 중… 이 창에서 결과를 기다려 주세요.');
    try { await api(`/api/admin/fans/${id}/minimum`, {method:'POST',body:JSON.stringify({minimum,expected:item.minimum})}); toast('팬 설정을 적용했습니다.'); }
    catch (error) { toast(error.message, true); }
    finally { changing = false; await loadFanStatus(); }
  });
  window.loadFanStatus = loadFanStatus;
})();

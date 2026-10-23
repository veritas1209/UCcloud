(() => {
  const panel = document.getElementById('file-panel');
  const zone = document.getElementById('file-drop');
  const picker = document.getElementById('file-picker');
  const choose = document.getElementById('file-choose');
  const feedback = document.getElementById('file-feedback');
  const progress = document.getElementById('file-progress');
  const cancel = document.getElementById('file-cancel');
  let send = null, running = false, stopping = false;
  const pending = new Map();
  function request(type, fields = {}) {
    return new Promise((resolve, reject) => {
      if (!send) return reject(new Error('먼저 VM에 연결해 주세요.'));
      const request_id = crypto.randomUUID();
      const timer = setTimeout(() => { pending.delete(request_id); reject(new Error('서버 응답을 기다리다 중단했습니다. 연결을 확인해 주세요.')); }, 35000);
      pending.set(request_id, {resolve, reject, timer});
      try { send({type, request_id, ...fields}); }
      catch { clearTimeout(timer); pending.delete(request_id); reject(new Error('연결이 끊겼습니다. 다시 접속해 주세요.')); }
    });
  }
  async function upload(files) {
    if (running || !send || !files.length) return;
    running = true; stopping = false; cancel.hidden = false; cancel.disabled = false; picker.disabled = true; choose.disabled = true;
    try {
      for (let index = 0; index < files.length; index += 1) {
        if (stopping) break;
        const file = files[index];
        if (file.size > 100 * 1024 * 1024) throw new Error('파일 하나는 100MB까지 올릴 수 있습니다.');
        feedback.textContent = `${file.name} · 전송 준비 중 (${index + 1}/${files.length})`;
        progress.value = 0; progress.hidden = false;
        await request('upload.begin', {name:file.name, size:file.size});
        for (let offset = 0; offset < file.size; offset += 32768) {
          if (stopping) break;
          const bytes = new Uint8Array(await file.slice(offset, offset + 32768).arrayBuffer());
          const data = btoa(String.fromCharCode(...bytes));
          const reply = await request('upload.chunk', {data});
          const percent = Math.floor(reply.received * 100 / file.size);
          progress.value = percent;
          feedback.textContent = `${file.name} · ${percent}% (${index + 1}/${files.length})`;
        }
        if (stopping) { await request('upload.cancel'); feedback.textContent = '파일 전송을 취소했습니다.'; break; }
        const reply = await request('upload.finish');
        progress.value = 100;
        feedback.textContent = `${files.length > 1 ? `${index + 1}개 전송 완료 · ` : ''}${reply.path}`;
      }
    } catch (error) {
      if (send) { try { await request('upload.cancel'); } catch { /* Connection close cancels server-side upload. */ } }
      feedback.textContent = error.message;
    } finally { running = false; cancel.hidden = true; picker.disabled = !send; choose.disabled = !send; picker.value = ''; }
  }
  choose.addEventListener('click', () => { if (!running && send) picker.click(); });
  picker.addEventListener('change', () => upload(Array.from(picker.files)));
  cancel.addEventListener('click', () => { stopping = true; cancel.disabled = true; feedback.textContent = '전송을 취소하는 중…'; });
  zone.addEventListener('dragover', event => { event.preventDefault(); if (send && !running) zone.classList.add('dragging'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragging'));
  zone.addEventListener('drop', event => { event.preventDefault(); zone.classList.remove('dragging'); upload(Array.from(event.dataTransfer.files)); });
  window.addEventListener('dragover', event => { if (Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault(); });
  window.addEventListener('drop', event => { if (Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault(); });
  window.uccloudFiles = {
    connected(sender) { send = sender; panel.classList.remove('hidden'); picker.disabled = false; choose.disabled = false; },
    disconnected() {
      send = null; stopping = true; picker.disabled = true; choose.disabled = true;
      for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error('연결이 끊겼습니다. 미완료 파일은 다시 올려 주세요.')); }
      pending.clear(); panel.classList.add('hidden');
    },
    receive(message) {
      if (message.type !== 'upload') return;
      const item = pending.get(message.request_id);
      if (!item) return;
      pending.delete(message.request_id); clearTimeout(item.timer);
      if (message.state === 'error') item.reject(new Error(message.message)); else item.resolve(message);
    },
  };
})();

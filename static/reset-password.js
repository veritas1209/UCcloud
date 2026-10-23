(() => {
  let token = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', '/reset-password');
  const form = document.getElementById('reset-form');
  const message = document.getElementById('reset-result');
  if (!token || !/^[A-Za-z0-9_-]{40,64}$/.test(token)) { form.classList.add('hidden'); message.textContent = '메일에 있는 비밀번호 변경 주소를 열어 주세요.'; }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const password = document.getElementById('reset-password');
    const confirm = document.getElementById('reset-confirm');
    if (password.value !== confirm.value) { message.textContent = '비밀번호가 서로 다릅니다. 다시 확인해 주세요.'; confirm.focus(); return; }
    const button = form.querySelector('button');
    button.disabled = true; button.textContent = '변경 중…';
    try {
      const response = await fetch('/api/auth/reset-password', {method:'POST',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,password:password.value})});
      const data = await response.json();
      if (!response.ok) throw new Error(response.status === 429 ? '시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.' : typeof data.detail === 'string' ? data.detail : '입력값을 다시 확인해 주세요.');
      message.textContent = data.message;
      token = ''; form.reset(); form.classList.add('hidden');
    } catch (error) { message.textContent = error.message || '연결을 확인한 뒤 다시 시도해 주세요.'; }
    finally { button.disabled = false; button.textContent = '비밀번호 바꾸기'; }
  });
})();

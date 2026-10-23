const portInput = document.getElementById('site-port');
const funnelCommand = document.getElementById('funnel-command');
const copyButton = document.getElementById('copy-funnel');
const startInput = document.getElementById('site-start');
const keepRunningCommand = document.getElementById('keep-running-command');
const keepRunningButton = document.getElementById('copy-keep-running');
const keepRunningPreview = document.querySelector('.start-command-preview');

function shellQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function persistentStartCommand(command) {
  return [
    '(',
    'set -e',
    'mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"',
    `printf '#!/bin/bash\\ncd -- %q\\nexec /bin/bash -ic %q\\n' "$PWD" ${shellQuote(command)} > "$HOME/.local/bin/uccloud-web-start"`,
    'chmod 700 "$HOME/.local/bin/uccloud-web-start"',
    `printf '%s\\n' '[Unit]' 'Description=UCcloud website' '[Service]' 'ExecStart=%h/.local/bin/uccloud-web-start' 'Restart=always' 'RestartSec=5' '[Install]' 'WantedBy=default.target' > "$HOME/.config/systemd/user/uccloud-web.service"`,
    'sudo loginctl enable-linger "$USER"',
    'systemctl --user daemon-reload',
    'systemctl --user enable uccloud-web.service',
    'systemctl --user restart uccloud-web.service',
    ')',
  ].join('\n');
}

function updateStartCommand() {
  const command = startInput.value.trim();
  const valid = command.length > 0 && command.length <= 200 && !/[\r\n]/.test(command);
  keepRunningButton.disabled = !valid;
  keepRunningCommand.textContent = valid
    ? persistentStartCommand(command)
    : '실행 명령을 적으세요';
  keepRunningButton.textContent = '명령 복사';
}

function currentPort() {
  const port = Number(portInput.value);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : null;
}

function updateCommand() {
  if (!portInput) return;
  const port = currentPort();
  copyButton.disabled = port === null;
  funnelCommand.textContent = port === null
    ? '포트 번호를 확인하세요'
    : `sudo tailscale funnel --bg --https=443 ${port}`;
  copyButton.textContent = '복사';
}

portInput?.addEventListener('input', updateCommand);
startInput.addEventListener('input', updateStartCommand);
document.querySelectorAll('[data-copy-command]').forEach(button => button.addEventListener('click', async () => {
  if (button.disabled) return;
  const command = document.getElementById(button.dataset.copyCommand)?.textContent;
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    button.textContent = '복사됨';
  } catch {
    if (button === keepRunningButton) keepRunningPreview.open = true;
    button.textContent = '직접 복사해 주세요';
  }
  clearTimeout(button.copyTimer);
  button.copyTimer = setTimeout(() => { button.textContent = button === keepRunningButton ? '명령 복사' : '복사'; }, 2500);
}));
updateCommand();
updateStartCommand();

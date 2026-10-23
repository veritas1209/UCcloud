#!/bin/bash
set -euo pipefail
stage=$(realpath -e "$1")
[[ "$stage" == /tmp/uccloud-agent-20261007.* ]]
test "$(id -u)" = 0
target=/opt/cloud-portal
files=(backend/app.py backend/terminal.py backend/database.py backend/agent_access.py backend/guest_agent.py static/terminal.html static/terminal.js static/terminal.css static/terminal-agent.js static/app.js static/index.html)
for file in "${files[@]}"; do test -s "$stage/$file"; done
cd "$target"
backup=$(mktemp /var/backups/uccloud-portal/source-before-agent-20261007-XXXXXX.tar)
existing=(); added=()
config_added=()
for file in "${files[@]}"; do if test -e "$file"; then existing+=("$file"); else added+=("$file"); fi; done
tar -cf "$backup" "${existing[@]}"
chmod 600 "$backup"
/opt/cloud-portal/.venv/bin/python "$stage/ops/install_agent_secret.py"
restore_on_error() {
 trap - ERR
 systemctl stop cloud-portal.service cloud-portal-public.service || true
 tar -xf "$backup" -C "$target"
 for file in "${added[@]}"; do rm -f -- "$target/$file"; done
 for config in "${config_added[@]}"; do rm -f -- "$config"; done
 systemctl daemon-reload
 systemctl start cloud-portal.service cloud-portal-public.service
 printf 'Deployment failed; original source restored: %s\n' "$backup" >&2
 exit 1
}
trap restore_on_error ERR
for unit in cloud-portal cloud-portal-public; do
  install -d -o root -g root -m 755 "/etc/systemd/system/$unit.service.d"
  config="/etc/systemd/system/$unit.service.d/agent.conf"
  if test -e "$config"; then
    cmp -s "$config" "$stage/ops/cloud-portal-agent.conf"
  else
    config_added+=("$config")
    install -o root -g root -m 644 "$stage/ops/cloud-portal-agent.conf" "$config"
  fi
done
systemctl stop cloud-portal.service cloud-portal-public.service
for file in "${files[@]}"; do install -o root -g root -m 644 "$stage/$file" "$target/$file"; done
systemctl daemon-reload
systemctl start cloud-portal.service cloud-portal-public.service
for attempt in {1..20}; do
 if curl --fail --silent http://127.0.0.1:8088/api/health >/dev/null && curl --fail --silent -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health >/dev/null; then break; fi
 sleep 1
done
curl --fail --silent http://127.0.0.1:8088/api/health
curl --fail --silent -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health
systemctl is-active cloud-portal.service cloud-portal-public.service
printf '\nAgent access deployed. Source backup: %s\n' "$backup"

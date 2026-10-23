#!/bin/bash
set -euo pipefail
stage=/tmp/uccloud-credentials-20261007.Ql96q8
target=/opt/cloud-portal
test "$(id -u)" = 0
files=(backend/app.py backend/software.py backend/openstack_service.py backend/guest_software.py backend/guest_welcome.py backend/guest_service_cli.py static/index.html static/app.js static/styles.css)
for file in "${files[@]}"; do test -s "$stage/$file"; done
cd "$target"
backup=$(mktemp /var/backups/uccloud-portal/source-before-service-credentials-20261007-XXXXXX.tar)
tar -cf "$backup" backend/app.py backend/software.py backend/openstack_service.py backend/guest_software.py static/index.html static/app.js static/styles.css
chmod 600 "$backup"
restore_on_error() {
  trap - ERR
  tar -xf "$backup" -C "$target"
  systemctl start cloud-portal.service cloud-portal-public.service
  printf 'Deployment failed; original source restored: %s\n' "$backup" >&2
  exit 1
}
trap restore_on_error ERR
systemctl stop cloud-portal.service cloud-portal-public.service
for file in "${files[@]}"; do install -o root -g root -m 644 "$stage/$file" "$target/$file"; done
systemctl start cloud-portal.service cloud-portal-public.service
for attempt in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8088/api/health >/dev/null && curl --fail --silent -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8088/api/health
curl --fail --silent -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health
systemctl is-active cloud-portal.service cloud-portal-public.service
printf '\nService credentials/login hints deployed. Rollback source: %s\n' "$backup"

#!/bin/bash
set -euo pipefail
stage=/tmp/uccloud-fans-stage-20261006.gijW3w
target=/opt/cloud-portal
cd "$target"
sha256sum -c <<'HASHES'
1f6aaf14c0000351a75a99c1386af40a6f61e8d1524176ff20e3a21f885aaa90  backend/app.py
8de514dfa3948cca76ca070e323d1ded51b893a2bf1f5c5e04de4680262a4996  static/app.js
4bed759291888825f7651314a47fe92a306f813040c1c7d55f5985d87defe1b5  static/index.html
HASHES
test ! -e /etc/systemd/system/uccloud-fans.service
backup=$(mktemp /var/backups/uccloud-portal/source-before-fans-20261006-XXXXXX.tar)
tar -cf "$backup" backend/app.py static/app.js static/index.html
chmod 600 "$backup"
rollback(){
  tar -xf "$backup" -C "$target"
  systemctl disable --now uccloud-fans.service || true
  systemctl restart cloud-portal.service cloud-portal-public.service || true
  printf 'Previous portal source restored: %s\n' "$backup" >&2
}
trap rollback ERR
install -d -o root -g root -m 755 /usr/local/lib/uccloud
install -o root -g root -m 644 "$stage/ops/ilo_control.py" "$stage/ops/fan_broker.py" /usr/local/lib/uccloud/
scp -o BatchMode=yes -o StrictHostKeyChecking=yes "$stage/ops/ilo_control.py" root@172.29.236.12:/tmp/uccloud-ilo-control-final.py
ssh -n -o BatchMode=yes -o StrictHostKeyChecking=yes root@172.29.236.12 'install -d -o root -g root -m 755 /usr/local/lib/uccloud; install -o root -g root -m 644 /tmp/uccloud-ilo-control-final.py /usr/local/lib/uccloud/ilo_control.py; python3 -m py_compile /usr/local/lib/uccloud/ilo_control.py'
install -o root -g root -m 644 "$stage/ops/uccloud-fans.service" /etc/systemd/system/uccloud-fans.service
systemd-analyze verify /etc/systemd/system/uccloud-fans.service
systemctl daemon-reload
systemctl enable --now uccloud-fans.service
sleep 2
systemctl is-active uccloud-fans.service
for file in backend/app.py backend/hardware.py static/app.js static/index.html static/fans.js static/fans.css; do
  install -o root -g root -m 644 "$stage/$file" "$target/$file"
done
systemctl restart cloud-portal.service cloud-portal-public.service
systemctl is-active cloud-portal.service cloud-portal-public.service
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 http://127.0.0.1:8088/api/health
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health
trap - ERR
printf '\nFan management deployed. Rollback backup: %s\n' "$backup"

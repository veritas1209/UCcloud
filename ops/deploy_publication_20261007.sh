#!/bin/bash
set -euo pipefail
stage=/tmp/uccloud-public-stage-20261007.Jm4xNq
target=/opt/cloud-portal
cd "$target"
sha256sum -c <<'HASHES'
9d2feabac41dc157fed8860c53ab69c50020cc8a8810f37fe2bbc91fd3c5d08c  backend/terminal.py
9109edeb288df7892483e8953acf35dd8777a037228dbf7c9d8e4e16cadf095d  static/terminal.html
560ec61f287ff09dfa7cf85c49a40fdfbcc3a2a6b27c6651cf3cb5cf551d210c  static/terminal.js
f42e6ee367628ea610f653db15c7b3ac2e1188d5cb960864757cd57808b55ed5  static/terminal.css
47853a0a1c94d83d504525b5c9843602c30e2a1a3b118b1586885d96e831878a  static/funnel-guide.html
c95a7662e4a1dab76fe6a7c4844e287b8674912dcb95f6980c9db1f54fef3fca  static/funnel-guide.js
HASHES
test ! -e "$target/backend/publication.py"
test ! -e "$target/static/terminal-publish.js"
test ! -e /etc/systemd/system/cloud-portal.service.d/60-publication.conf
test ! -e /etc/systemd/system/cloud-portal-public.service.d/60-publication.conf
test "$(stat -c '%a:%U' /etc/cloud-portal/public-oauth.json)" = '600:root'
backup=$(mktemp /var/backups/uccloud-portal/source-before-publication-20261007-XXXXXX.tar)
tar -cf "$backup" backend/terminal.py static/terminal.html static/terminal.js static/terminal.css static/funnel-guide.html static/funnel-guide.js
chmod 600 "$backup"
rollback(){
  tar -xf "$backup" -C "$target"
  rm -f /etc/systemd/system/cloud-portal.service.d/60-publication.conf /etc/systemd/system/cloud-portal-public.service.d/60-publication.conf
  systemctl daemon-reload
  systemctl restart cloud-portal.service cloud-portal-public.service || true
  printf 'Previous source restored: %s\n' "$backup" >&2
}
trap rollback ERR
for file in backend/terminal.py backend/publication.py static/terminal.html static/terminal.js static/terminal.css static/terminal-publish.js static/funnel-guide.html static/funnel-guide.js; do
  install -o root -g root -m 644 "$stage/$file" "$target/$file"
done
for service in cloud-portal cloud-portal-public; do
  install -d -o root -g root -m 755 "/etc/systemd/system/$service.service.d"
  install -o root -g root -m 644 "$stage/ops/cloud-portal-publication.conf" "/etc/systemd/system/$service.service.d/60-publication.conf"
done
install -d -o cloud-portal -g cloud-portal -m 700 /var/lib/cloud-portal/publication-locks
systemctl daemon-reload
systemctl restart cloud-portal.service cloud-portal-public.service
systemctl is-active cloud-portal.service cloud-portal-public.service
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 http://127.0.0.1:8088/api/health
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health
trap - ERR
printf '\nManaged publication deployed. Rollback backup: %s\n' "$backup"

#!/bin/bash
set -euo pipefail
stage=/tmp/uccloud-files-stage-20261007.gFqK2s
target=/opt/cloud-portal
cd "$target"
sha256sum -c <<'HASHES'
e27563a18293782be3e31a183f10c34eb8c52dc18701e8c10a3c3527baedbc58  backend/terminal.py
adb19d4c4e1fcf108c16b7022e966803c78b827467fe682cbe4b0be00a32050d  static/terminal.html
9ff83b9b79f1611cc612c1f0c893fb13b5ecf1c73160e6b1f4f632ed2bfc41b1  static/terminal.js
c59928bcc93f593fff3374fbc28ca65cf777d656ea55fb78cf83d7ce22e46af4  static/terminal.css
HASHES
test ! -e "$target/backend/guest_files.py"
test ! -e "$target/static/terminal-files.js"
backup=$(mktemp /var/backups/uccloud-portal/source-before-files-20261007-XXXXXX.tar)
tar -cf "$backup" backend/terminal.py static/terminal.html static/terminal.js static/terminal.css
chmod 600 "$backup"
rollback(){
  tar -xf "$backup" -C "$target"
  systemctl restart cloud-portal.service cloud-portal-public.service || true
  printf 'Previous SSH source restored: %s\n' "$backup" >&2
}
trap rollback ERR
for file in backend/terminal.py backend/guest_files.py static/terminal.html static/terminal.js static/terminal.css static/terminal-files.js; do
  install -o root -g root -m 644 "$stage/$file" "$target/$file"
done
systemctl restart cloud-portal.service cloud-portal-public.service
systemctl is-active cloud-portal.service cloud-portal-public.service
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 http://127.0.0.1:8088/api/health
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health
trap - ERR
printf '\nFile upload deployed. Rollback backup: %s\n' "$backup"

#!/bin/bash
set -euo pipefail
stage=/tmp/uccloud-public-stage-20261007.Jm4xNq
target=/opt/cloud-portal
cd "$target"
backup=$(mktemp /var/backups/uccloud-portal/guide-before-pc-20261007-XXXXXX.tar)
tar -cf "$backup" static/funnel-guide.html static/funnel-guide.js static/funnel-guide.css
chmod 600 "$backup"
for file in static/funnel-guide.html static/funnel-guide.js static/funnel-guide.css; do
  test -s "$stage/$file"
done
for name in pc-vm.png pc-connect.png pc-files.png pc-publish.png; do
  test -s "$stage/static/guide/$name"
done
install -d -o root -g root -m 755 "$target/static/guide"
for name in pc-vm.png pc-connect.png pc-files.png pc-publish.png; do
  install -o root -g root -m 644 "$stage/static/guide/$name" "$target/static/guide/$name"
done
for file in static/funnel-guide.js static/funnel-guide.css static/funnel-guide.html; do
  install -o root -g root -m 644 "$stage/$file" "$target/$file"
done
curl --fail --silent -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/guide/funnel | grep -q pc-guide-1
printf 'PC guide deployed without service restart. Backup: %s\n' "$backup"

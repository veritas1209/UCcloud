#!/usr/bin/env bash
set -euo pipefail
stage=/tmp/uccloud-branding-stage-20261001
target=/opt/cloud-portal

# Refuse to overwrite a portal changed since the read-only preflight.
cd "$target"
sha256sum -c <<'HASHES'
39b9f1aedf77540bac27d1598c43ba88deb2d4efb6f8a64fac9204f2423ad5c2  backend/app.py
abd9103c4e0536366941b92be1557e5ec0309937d3a2bac0c31c824a39c6cdb4  static/index.html
9bd82c88a666de2c85de3d49582d36afa08a9e7f005076ec53a61441327e29c6  static/terminal.html
5f0123d0eff639cf6eb86e0b61c2c4789f57ffb478e01e1bf154591dae4aeefd  static/funnel-guide.html
2efbdaeba2ef3a4bf4c6c9ded98c71c087aa8d790946c4b60529418cb0304d56  static/reset-password.html
a546a1d800d60a245891fc0d408ddc78c2c35ff9b5e24a8d9a74761a2b15ab01  static/console.html
HASHES
backup=$(mktemp /var/backups/uccloud-portal/source-before-branding-20261001-XXXXXX.tar)
tar -cf "$backup" backend/app.py static/index.html static/terminal.html static/funnel-guide.html static/reset-password.html static/console.html
chmod 600 "$backup"
rollback() {
  tar -xf "$backup" -C "$target"
  systemctl restart cloud-portal.service cloud-portal-public.service
  printf 'Deployment failed; previous source restored from %s\n' "$backup" >&2
}
trap rollback ERR
install -d -m 755 "$target/static/brand"
for file in backend/app.py static/index.html static/terminal.html static/funnel-guide.html static/reset-password.html static/console.html; do
  install -o root -g root -m 644 "$stage/$file" "$target/$file"
done
for asset in icon.svg share.svg favicon-v1.ico icon-180-v1.png icon-192-v1.png icon-512-v1.png share-v1.png site.webmanifest robots.txt sitemap.xml; do
  install -o root -g root -m 644 "$stage/static/brand/$asset" "$target/static/brand/$asset"
done
systemctl restart cloud-portal.service cloud-portal-public.service
systemctl is-active cloud-portal.service cloud-portal-public.service
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 http://127.0.0.1:8088/api/health
curl --fail --silent --retry 5 --retry-connrefused --retry-delay 1 -H 'Host: comsv-03.tail5819db.ts.net' http://127.0.0.1:18080/api/health
trap - ERR
printf '\nBranding deployed. Source rollback backup: %s\n' "$backup"

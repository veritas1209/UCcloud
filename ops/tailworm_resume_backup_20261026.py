"""Resume only this audit's interrupted QA restore, then verify and remove its backup."""
import asyncio
import datetime
import hashlib
import json
from pathlib import Path
import runpy
import time

ROOT = Path('/var/lib/uccloud-tailworm-20261026')
test = runpy.run_path('/var/lib/uccloud-provisioner-deploy-20261026/verify_provisioner_20261026.py')
s = json.loads(test['QA'].read_text())
assert s['vm_name'].startswith('privsep-qa-')
sid = s['server_id']
rows = test['api']('/api/backups')['backups']
assert len(rows) == 1 and rows[0]['server_id'] == sid and rows[0]['state'] == 'ready'
bid = rows[0]['id']
vm = next(x for x in test['api']('/api/instances')['instances'] if x['id'] == sid)
assert vm['status'] == 'SHUTOFF'
test['api']('/api/instances/' + sid + '/backups/' + bid + '/restore', {}, expected=202)
deadline = time.monotonic() + 900
while time.monotonic() < deadline:
    row = next(x for x in test['api']('/api/backups')['backups'] if x['id'] == bid)
    if row['restore_state'] == 'idle': break
    assert row['restore_state'] != 'failed', 'Restore failed; retain backup and VM for review'
    time.sleep(5)
else: raise TimeoutError('Restore did not finish')
test['api']('/api/instances/' + sid + '/action', {'action': 'start'}, expected=202)
test['wait_vm']('ACTIVE')
time.sleep(15)
digest = hashlib.sha256(b'UCCLOUD_PRIVSEP_BACKUP_VERIFIED\n').hexdigest()
asyncio.run(test['shell'](command='sha256sum ~/privsep-proof.txt\n', expected=digest))
test['api']('/api/instances/' + sid + '/backups/' + bid, method='DELETE', expected=202)
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    if not test['api']('/api/backups')['backups']: break
    time.sleep(3)
else: raise TimeoutError('QA backup removal pending')
result = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'off_host_backup': True, 'restore_after_identity_worker_recovery': True,
          'file_sha256_match': True, 'qa_backup_removed': True,
          'student_vms_stopped': False}
p = ROOT / 'backup-restore.json'; p.write_text(json.dumps(result, indent=2)); p.chmod(0o600)
print(json.dumps(result))

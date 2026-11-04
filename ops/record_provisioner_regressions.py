"""Run isolated regression scripts and retain dated, hashed execution evidence."""
import datetime
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT=Path('/opt/uccloud-releases/provisioner-20261026')
STATE=Path('/var/lib/uccloud-provisioner-deploy-20261026')
LOGS=STATE/'regressions'
LOGS.mkdir(mode=0o700,exist_ok=True)
PYTHON='/opt/cloud-portal/.venv/bin/python'
SCRIPTS=['test_quota','test_capacity','test_cloud_health','test_public_app',
         'test_vm_backups','test_vm_backup_api','test_self_service','test_agent_access',
         'test_terminal_security','test_software_state','test_registration_global_limit','test_guest_files']
jobs=[('unit-provisioner-node',['-m','unittest','tests.test_provisioner','tests.test_node_security','-v'])]
jobs += [(name,['tests/'+name+'.py']) for name in SCRIPTS]
records=[]
for name,args in jobs:
    started=datetime.datetime.now(datetime.UTC).isoformat()
    run=subprocess.run([PYTHON,*args],cwd=ROOT,env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(ROOT)},
                       text=True,capture_output=True,timeout=150)
    path=LOGS/(name+'.log')
    path.write_text(run.stdout+run.stderr);path.chmod(0o600)
    records.append({'id':name,'command':[PYTHON,*args],'started_utc':started,
                    'returncode':run.returncode,'log':path.name,
                    'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    print(name,'PASS' if run.returncode==0 else 'FAIL',flush=True)
result={'date_kst':'2026-10-26','isolated_unit_cases':16,'regression_scripts':len(SCRIPTS),
        'all_passed':all(x['returncode']==0 for x in records),'results':records}
path=STATE/'regression-results.json';path.write_text(json.dumps(result,indent=2));path.chmod(0o600)
raise SystemExit(0 if result['all_passed'] else 1)

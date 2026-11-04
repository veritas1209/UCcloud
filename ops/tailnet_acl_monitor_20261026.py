"""Temporary manual-policy-change continuity checks; does not alter policy."""
import datetime as dt
import json
from pathlib import Path
import runpy
import subprocess
import time
import urllib.request

STATE = Path('/var/lib/uccloud-tailnet-acl-20261026')
STATE.mkdir(mode=0o700, exist_ok=True)
probe = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
addresses = probe['public_addresses']()
result = {'started': dt.datetime.now(dt.timezone.utc).isoformat(),
          'local_ok': 0, 'local_fail': 0, 'public_ok': 0, 'public_fail': 0, 'errors': []}
end = time.monotonic() + 3600
while time.monotonic() < end and not (STATE / 'monitor-stop').exists():
    for kind in ['local', 'public']:
        try:
            if kind == 'local':
                request = urllib.request.Request('http://127.0.0.1:19080/api/health', headers={'Host': 'comsv-03.tail5819db.ts.net'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
                    assert response.status == 200
            else:
                assert probe['public_get']('/api/health', addresses)[0] == 200
            result[kind + '_ok'] += 1
        except Exception as exc:
            result[kind + '_fail'] += 1
            result['errors'].append({'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'kind': kind, 'error': type(exc).__name__})
            result['errors'] = result['errors'][-30:]
    result['updated'] = dt.datetime.now(dt.timezone.utc).isoformat()
    temporary = STATE / 'continuity.next'
    temporary.write_text(json.dumps(result, indent=2))
    temporary.chmod(0o600)
    temporary.replace(STATE / 'continuity.json')
    time.sleep(3)

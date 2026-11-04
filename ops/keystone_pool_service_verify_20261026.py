"""Use a freshly created disposable team/VM only; never uses student credentials."""
import asyncio
import json
from pathlib import Path
import runpy
import sys

STATE = Path('/var/lib/uccloud-keystone-pool-20261026')
sys.path.insert(0, str(STATE))
functions = runpy.run_path(str(STATE / 'verify_provisioner_20261026.py'))
scope = functions['create'].__globals__
scope.update(STATE=STATE, QA=STATE / 'qa-private.json', RESULT=STATE / 'service-verification.json')
phase = sys.argv[1]
if phase == 'create':
    functions['create']()
elif phase == 'shell':
    asyncio.run(functions['shell']())
elif phase == 'publicshell':
    import websockets
    public = runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    address = public['public_addresses']()[0]
    original = websockets.connect
    def public_connect(uri, **options):
        options.update(host=address, port=443, server_hostname=scope['HOST'])
        return original(uri.replace('ws://', 'wss://', 1), **options)
    websockets.connect = public_connect
    asyncio.run(functions['shell']())
    functions['result'](real_public_websocket_shell=True)
elif phase == 'backup':
    functions['backup']()
elif phase == 'cleanup':
    functions['cleanup']()
else:
    raise SystemExit('Unknown phase')

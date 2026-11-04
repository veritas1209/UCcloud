"""Fresh disposable QA team/VM, independent of previously removed QA identities."""
import asyncio
from pathlib import Path
import runpy
import sys

STATE=Path('/var/lib/uccloud-tailnet-acl-20261026')
PRIOR=Path('/var/lib/uccloud-keystone-pool-20261026')
sys.path.insert(0,str(PRIOR))
functions=runpy.run_path(str(PRIOR/'verify_provisioner_20261026.py'))
functions['create'].__globals__.update(STATE=STATE,QA=STATE/'qa-private.json',RESULT=STATE/'service-verification.json')
phase=sys.argv[1]
if phase=='create': functions['create']()
elif phase=='publicshell':
    import websockets
    public=runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    address=public['public_addresses']()[0];original=websockets.connect
    def connect(uri,**options):
        options.update(host=address,port=443,server_hostname='comsv-03.tail5819db.ts.net')
        return original(uri.replace('ws://','wss://',1),**options)
    websockets.connect=connect
    asyncio.run(functions['shell']())
    functions['result'](real_public_websocket_shell=True)
elif phase=='cleanup': functions['cleanup']()
else: raise SystemExit('Unknown phase')

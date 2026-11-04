"""Redacted diagnosis of the private broker's OpenStack connection only."""
import os
import re
import sys
sys.path.insert(0, '/opt/uccloud-releases/provisioner-20261026')
from backend import openstack_service as cloud
try:
    cloud.admin_connection().authorize()
    print('ADMIN_AUTH_OK')
    if len(sys.argv)>1 and sys.argv[1]=='quota':
        import sqlite3
        from backend.provisioner import dispatch
        db=sqlite3.connect('file:/var/lib/cloud-portal/portal.db?mode=ro',uri=True)
        project=db.execute('SELECT openstack_project_id FROM teams WHERE openstack_project_id IS NOT NULL LIMIT 1').fetchone()[0]
        db.close()
        dispatch({'op':'quota','project_id':project})
        print('QUOTA_OK')
except Exception as exc:
    value=str(exc)
    for key,secret in os.environ.items():
        if any(x in key.upper() for x in ('PASSWORD','TOKEN','SECRET')) and secret:
            value=value.replace(secret,'[REDACTED]')
    value=re.sub(r'https?://[^\s\)]+','[URL]',value)
    value=re.sub(r'\b\d{1,3}(?:\.\d{1,3}){3}\b','[ADDRESS]',value)
    print(type(exc).__name__,value[:600])
    raise SystemExit(1)

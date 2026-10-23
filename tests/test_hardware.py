"""Hardware controls preserve automatic cooling and require admin + CSRF."""
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from ops import ilo_control as control

for request in ({'op':'set','node':'http://evil','minimum':20,'expected':0},
                {'op':'set','node':'controller','minimum':-1,'expected':0},
                {'op':'set','node':'controller','minimum':101,'expected':0},
                {'op':'set','node':'controller','minimum':True,'expected':0},
                {'op':'set','node':'controller','minimum':20,'expected':0,'host':'evil'},
                {'op':'raw','command':'reboot'}):
    try: control.validate_request(request)
    except control.FanError: pass
    else: raise AssertionError(request)

sample = {'Fans':[{'Name':'Fan 1','Reading':6,'Status':{'State':'Enabled','Health':'OK'}}],
          'Temperatures':[{'Name':'CPU 1','ReadingCelsius':30,'Status':{'State':'Enabled','Health':'OK'}}],
          'Oem':{'Hpe':{'FanPercentMinimum':0,'ThermalConfiguration':'OptimalCooling'}}}
class FakeIlo:
    writes = []
    minimum = 0
    def __init__(self,*args): pass
    def thermal(self):
        return {**sample,'Oem':{'Hpe':{'FanPercentMinimum':self.minimum}}}, '"etag"'
    def request(self,method,path,body,etag):
        self.writes.append((method,path,body,etag))
        self.minimum = body['Oem']['Hpe']['FanPercentMinimum']
        return {}, None
with patch.object(control,'Ilo',FakeIlo):
    assert control.execute({'op':'set','node':'compute-1','minimum':20,'expected':0},{'compute-1':{}})['node']['minimum'] == 20
assert FakeIlo.writes == [('PATCH',control.THERMAL,{'Oem':{'Hpe':{'FanPercentMinimum':20}}},'"etag"')]
with patch.object(control,'Ilo',FakeIlo):
    try: control.execute({'op':'set','node':'compute-1','minimum':20,'expected':10},{'compute-1':{}})
    except control.FanError as exc: assert exc.code == 'conflict'
    else: raise AssertionError('stale setting accepted')

with tempfile.TemporaryDirectory() as directory:
    os.environ['PORTAL_DB'] = str(Path(directory)/'portal.db')
    os.environ['PORTAL_ADMIN_PASSWORD'] = 'test-password-2026'
    from backend import app as portal
    from backend import hardware
    from backend.database import db
    identity = {'id':1,'role':'admin','status':'active','csrf_token':'test-csrf'}
    portal.app.dependency_overrides[portal._current_user] = lambda: identity
    calls = []
    with patch.object(hardware,'request',side_effect=lambda payload: calls.append(payload) or {'nodes':[]}):
        with TestClient(portal.app,base_url='http://localhost') as client:
            assert client.get('/api/admin/fans').status_code == 200
            identity['role'] = 'member'
            assert client.get('/api/admin/fans').status_code == 403
            assert client.post('/api/admin/fans/compute-1/minimum',json={'minimum':20,'expected':0},headers={'X-CSRF-Token':'test-csrf'}).status_code == 403
            identity['role'] = 'admin'
            assert client.post('/api/admin/fans/compute-1/minimum',json={'minimum':20,'expected':0}).status_code == 403
            for minimum in (-1,101,True,'20'):
                assert client.post('/api/admin/fans/compute-1/minimum',json={'minimum':minimum,'expected':0},headers={'X-CSRF-Token':'test-csrf'}).status_code == 422
            response = client.post('/api/admin/fans/compute-1/minimum',json={'minimum':20,'expected':0},headers={'X-CSRF-Token':'test-csrf'})
            assert response.status_code == 200, response.text
            assert calls[-1] == {'op':'set','node':'compute-1','minimum':20,'expected':0}
            with db() as conn:
                assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='hardware.fan.updated'").fetchone()[0] == 1
    portal.app.dependency_overrides.clear()
print('Hardware authorization, input, audit and protection checks passed')

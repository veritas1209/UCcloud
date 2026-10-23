"""Publication isolation, argument safety, errors, cancellation and policy checks."""
import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend import publication as p

VM = '11111111-1111-4111-8111-111111111111'
RID = '22222222-2222-4222-8222-222222222222'
HOST = 'uccloud-' + VM + '.' + p.DOMAIN
snapshot = {'status':{'BackendState':'Running', 'CurrentTailnet':{'MagicDNSSuffix':p.DOMAIN},
                     'Self':{'DNSName':HOST+'.', 'Tags':[p.TAG]}}, 'funnel':{}}
public = copy.deepcopy(snapshot)
public['funnel'] = {'Web':{HOST+':443':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:8080'}}}},
                    'AllowFunnel':{HOST+':443':True}}
assert p.public_view(public)['url'] == 'https://' + HOST
assert p.public_view(snapshot)['state'] == 'private'
assert p.public_view({'status':{'CurrentTailnet':None}})['state'] == 'private'
bad = copy.deepcopy(snapshot)
bad['status']['CurrentTailnet']['MagicDNSSuffix'] = 'tail5819db.ts.net'
try:
    p.public_view(bad)
    raise AssertionError('management tailnet accepted')
except ValueError:
    pass
p.validate_policy(copy.deepcopy(p.POLICY))
for field in ('grants', 'ssh', 'acls'):
    bad_policy = copy.deepcopy(p.POLICY); bad_policy[field] = [{'src':['*']}]
    try:
        p.validate_policy(bad_policy)
        raise AssertionError('lateral connections accepted')
    except ValueError:
        pass

class Guest:
    def __init__(self, initial=None, http=200):
        self.snapshot = copy.deepcopy(initial or {'status':{'CurrentTailnet':None}, 'funnel':{}})
        self.http = http
        self.calls = []
    async def run(self, command, **kwargs):
        self.calls.append((command,kwargs))
        if p.READ_SCRIPT in __import__('shlex').split(command):
            data = self.snapshot
        elif p.HTTP_SCRIPT in __import__('shlex').split(command):
            data = {'http_status':self.http}
        elif command.startswith('sudo '):
            assert 'test-vm-password' not in command and 'tskey-auth-fake' not in command
            payload = json.loads(kwargs['input'].splitlines()[1])
            assert payload['protocol'] == 1
            if payload['op'] == 'start':
                assert payload.get('key', 'tskey-auth-fake').startswith('tskey-auth-')
                self.snapshot = copy.deepcopy(public)
            else:
                self.snapshot = copy.deepcopy(snapshot)
            data = {'ok':True}
        else:
            raise AssertionError(command)
        return SimpleNamespace(stdout=json.dumps(data), exit_status=0)

async def run():
    for scenario in ('status','start','managed','stop','wrong-account','wrong-port','off','revoked'):
        sent, saved, guarded = [], [], []
        guest = Guest(public if scenario in ('stop','managed') else bad if scenario=='wrong-account' else None,
                      http=0 if scenario=='off' else 200)
        async def send(item): sent.append(item)
        async def guard(op):
            guarded.append(op)
            if scenario == 'revoked': raise ValueError('로그인이 만료됐습니다.')
        async def record(op,item): saved.append((op,item))
        publisher = p.Publisher(guest,'test-vm-password',VM,send,guard,record)
        message = {'type':'publish.'+('status' if scenario=='status' else 'stop' if scenario=='stop' else 'start'),
                   'request_id':RID, 'port':'8080;id' if scenario=='wrong-port' else 8080}
        with patch.object(p,'configured',return_value=True), patch.object(p,'mint_key',return_value=('tskey-auth-fake','id','access')) as mint, \
             patch.object(p,'check_policy',return_value='access') as policy, patch.object(p,'revoke_key') as revoke:
            await publisher.submit(message)
            await publisher.task
            if scenario in ('wrong-account','wrong-port','off','revoked'):
                assert sent[-1]['state'] == 'error', (scenario,sent)
                assert not saved and not mint.called
                assert not any(command.startswith('sudo ') for command,_ in guest.calls)
            elif scenario == 'status':
                assert sent[-1]['state']=='private' and not saved
            elif scenario == 'stop':
                assert sent[-1]['state']=='private' and len(saved)==1
            else:
                assert sent[-1]['state']=='public' and len(saved)==1, (scenario, sent)
                assert mint.called == (scenario=='start')
                assert policy.called == (scenario=='managed')
                assert revoke.called == (scenario=='start')
            assert all('test-vm-password' not in json.dumps(item) and 'tskey-auth-' not in json.dumps(item) for item in sent)
        await publisher.close()
        assert publisher.password == ''

with tempfile.TemporaryDirectory(prefix='uccloud-publish-test-') as folder:
    with patch.object(p, 'LOCK_DIRECTORY', Path(folder)):
        asyncio.run(run())
print('Policy, public identity, localhost probe, argument safety, revoked sessions, stop and secret redaction passed')

"""Run as root. Read credential JSON from non-echoing stdin; never print secrets."""
import json
import os
import sys
import urllib.parse
import urllib.request

TARGET = '/etc/cloud-portal/public-oauth.json'
SUFFIX = '.tail2d25b3.ts.net'
TAG = 'tag:uccloud-public'

def fetch(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read(1024 * 1024))

def main():
    if os.geteuid() != 0 or os.path.exists(TARGET):
        raise ValueError('Credential destination is unavailable; existing file was not changed.')
    supplied = json.loads(input())
    credential = {key:supplied[key] for key in ('client_id', 'client_secret')}
    token = fetch('https://api.tailscale.com/api/v2/oauth/token',
                  urllib.parse.urlencode(credential).encode(),
                  {'Content-Type':'application/x-www-form-urlencoded'})
    needed = {'auth_keys', 'policy_file:read', 'devices:core:read', 'devices:posture_attributes:read'}
    if not needed.issubset(set(token.get('scope', '').split())):
        raise ValueError('Required scopes are missing.')
    headers = {'Authorization':'Bearer ' + token['access_token'], 'Accept':'application/json'}
    policy = fetch('https://api.tailscale.com/api/v2/tailnet/-/acl', headers=headers)
    if any(policy.get(key) for key in ('acls', 'grants', 'ssh')):
        raise ValueError('Public policy permits lateral access. Credential was not saved.')
    if policy.get('tagOwners', {}).get(TAG) != ['autogroup:admin']:
        raise ValueError('Public tag ownership differs.')
    if policy.get('nodeAttrs') != [{'target':[TAG], 'attr':['funnel']}]:
        raise ValueError('Public Funnel policy differs.')
    devices = fetch('https://api.tailscale.com/api/v2/tailnet/-/devices', headers=headers)['devices']
    if not devices or any(not item.get('name', '').endswith(SUFFIX) for item in devices):
        raise ValueError('Public account domain could not be verified.')
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(credential, output)
        output.flush()
        os.fsync(output.fileno())
    print('Public OAuth verified: required scopes, isolated policy, correct public domain. Saved root-only (0600).')

if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Credential verification failed. No response body or secret was printed.', file=sys.stderr)
        sys.exit(1)

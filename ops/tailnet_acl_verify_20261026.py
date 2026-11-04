"""Actual per-node filter evaluation and bounded allow/deny network comparisons."""
import concurrent.futures
import datetime as dt
import hashlib
import ipaddress
import json
from pathlib import Path
import runpy
import shlex
import socket
import subprocess
import sys
import urllib.request

STATE = Path('/var/lib/uccloud-tailnet-acl-20261026')
HOSTS = [('controller', None, '100.74.103.115'), ('compute-1','172.29.236.12','100.83.255.31'),
         ('compute-2','172.29.236.13','100.127.246.41'), ('compute-3','172.29.236.14','100.94.53.111'),
         ('compute-4','172.29.236.15','100.78.80.66')]

def run(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=40, **kwargs).strip()

def remote(host, code):
    return run(['ssh','-n','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','root@'+host,
                'python3 -c '+shlex.quote(code)])

def save(name, data):
    path = STATE / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    path.chmod(0o600)

def includes(value, address):
    value = value.removesuffix('/0') if value == '*' else value
    if value == '*': return True
    try:
        if '-' in value:
            first,last=value.split('-',1)
            return ipaddress.ip_address(first) <= address <= ipaddress.ip_address(last)
        if '/' in value:
            return address in ipaddress.ip_network(value,strict=False)
        return address == ipaddress.ip_address(value)
    except (ValueError,TypeError):
        return False

def permitted(rules, source, destination, protocol, port):
    source=ipaddress.ip_address(source); destination=ipaddress.ip_address(destination)
    for rule in rules:
        if not rule.get('DstPorts'): continue  # app capability grants are not socket permission
        if protocol not in rule.get('IPProto',[6,17]): continue
        if not any(includes(value,source) for value in rule.get('SrcIPs',[])): continue
        if any(includes(item['IP'],destination) and item['Ports']['First'] <= port <= item['Ports']['Last'] for item in rule['DstPorts']):
            return True
    return False

SNAPSHOT = '''import json,subprocess; n=json.loads(subprocess.check_output(['tailscale','debug','netmap'],text=True)); p=json.loads(subprocess.check_output(['tailscale','debug','prefs'],text=True)); print(json.dumps({'rules':n.get('PacketFilterRules',[]),'ssh_policy':n.get('SSHPolicy'),'RunSSH':p.get('RunSSH'),'serve':json.loads(subprocess.check_output(['tailscale','serve','status','--json'],text=True))}))'''

def filters():
    policy=json.loads((STATE/'management-policy.json').read_text())
    snapshots={name:json.loads(remote(lan,SNAPSHOT) if lan else run(['python3','-c',SNAPSHOT])) for name,lan,_ in HOSTS}
    aliases=policy['hosts']; by_ip={aliases[name]:snapshots[name] for name,_,_ in HOSTS}
    by_ip.update({aliases[name+'-v6']:snapshots[name] for name,_,_ in HOSTS})
    tested=0
    for case in policy['tests']:
        for kind in ['accept','deny']:
            for target in case.get(kind,[]):
                target_name,port=target.rsplit(':',1); target_ip=aliases[target_name]
                if target_ip not in by_ip: continue  # offline desktop/Windows is checked separately
                actual=permitted(by_ip[target_ip]['rules'],aliases[case['src']],target_ip,6 if case['proto']=='tcp' else 17,int(port))
                assert actual == (kind=='accept'), (case['src'],target,actual)
                tested+=1
    for name,snapshot in snapshots.items():
        assert snapshot['RunSSH'] is False and not (snapshot.get('ssh_policy') or {}).get('rules')
        if name!='controller': assert snapshot['serve']=={}
    assert snapshots['controller']['serve']==json.loads((STATE/'preflight.json').read_text())['serve_before']
    result={'checked':dt.datetime.now(dt.timezone.utc).isoformat(),'nodes_checked':5,'real_filter_assertions_passed':tested,
            'all_RunSSH_false':True,'all_ssh_rules_empty':True,'serve_unchanged':True,
            'candidate_sha256':hashlib.sha256((STATE/'management-policy.json').read_bytes()).hexdigest()}
    save('filters-after.full.json',snapshots);save('filter-verification.json',result);print(json.dumps(result))

def network():
    code = '''import json,socket; result=[]
for host in ['172.29.236.13','100.127.246.41','fd7a:115c:a1e0::8a34:f62b']:
 try:
  with socket.create_connection((host,49091),timeout=3) as s: ok=s.recv(64)==b'UCCLOUD_ACL_PROBE\\n'
  result.append({'path':'LAN' if host.startswith('172.') else ('Tailnet IPv6' if ':' in host else 'Tailnet IPv4'),'connected':ok})
 except OSError as e: result.append({'path':'LAN' if host.startswith('172.') else ('Tailnet IPv6' if ':' in host else 'Tailnet IPv4'),'connected':False,'error':type(e).__name__})
print(json.dumps(result))'''
    comparisons={name:json.loads(remote(lan,code) if lan else run(['python3','-c',code])) for name,lan in [('controller',None),('compute-3','172.29.236.14')]}
    for entries in comparisons.values():
        assert entries[0]['connected'] is True and all(not x['connected'] for x in entries[1:])
    ssh=[]
    for name,lan,tailnet in HOSTS[1:]:
        out=run(['ssh','-n','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',
                 '-o','HostKeyAlias='+lan,'root@'+tailnet,'printf UCCLOUD_CONTROLLER_COMPUTE_SSH_OK'])
        assert out=='UCCLOUD_CONTROLLER_COMPUTE_SSH_OK'
        ssh.append({'destination':name,'new_authenticated_ssh':True})
    probe=runpy.run_path('/var/lib/uccloud-maintenance/20261008/public_funnel_probe.py')
    addresses=probe['public_addresses']()
    for path in ['/','/api/health']:
        assert probe['public_get'](path,addresses)[0]==200
    result={'network_comparisons':comparisons,'controller_compute_ssh':ssh,'public_https_pages':2,
            'public_https_status':200}
    save('network-verification.json',result);print(json.dumps(result))

if __name__=='__main__':
    {'filters':filters,'network':network}[sys.argv[1]]()

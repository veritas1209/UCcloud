"""Benign canary boot/SSH/I/O/serial test. Retain the VM and private state."""
import asyncio,base64,datetime,json,os,runpy,secrets,shlex,time
from pathlib import Path
import asyncssh

m=runpy.run_path('/var/lib/uccloud-maintenance/20261008/maintenance.py')
m['quarantine_required']();c=m['admin']()
STATE=Path('/var/lib/uccloud-maintenance/20261008/canary-vm.json')

async def main():
    expected_host=os.getenv('UCCLOUD_CANARY_EXPECTED_HOST','comsv-01')
    assert expected_host in ('comsv-01','comsv-04')
    if not STATE.exists():
        assert expected_host=='comsv-01','Only the original canary host may create a test VM'
        password=secrets.token_urlsafe(24);marker=secrets.token_hex(16)
        # crypt hashes the password before cloud-init metadata is submitted.
        import subprocess
        password_hash=subprocess.check_output(['openssl','passwd','-6','-stdin'],input=password+'\n',text=True).strip()
        network=c.network.get_network('99757322-3209-495a-a12f-18ee1d5bc95d')
        assert network.project_id==c.current_project_id
        group=c.network.find_security_group('uccloud-maintenance-canary',project_id=c.current_project_id,ignore_missing=True)
        if not group:group=c.network.create_security_group(name='uccloud-maintenance-canary',description='Temporary restricted security maintenance canary')
        # Management-only SSH and site access, never public network ingress.
        inventory=json.loads(Path('/etc/openstack_deploy/openstack_inventory.json').read_text())
        controller=inventory['_meta']['hostvars']['controller1'];address=controller.get('management_address') or controller['ansible_host']
        for port in (22,8080):
            if not any(r.direction=='ingress' and r.protocol=='tcp' and r.port_range_min==port for r in c.network.security_group_rules(security_group_id=group.id)):
                c.network.create_security_group_rule(security_group_id=group.id,direction='ingress',protocol='tcp',port_range_min=port,port_range_max=port,remote_ip_prefix=address+'/32',ethertype='IPv4')
        content=f'''#cloud-config
ssh_pwauth: true
users:
  - name: student
    shell: /bin/bash
    lock_passwd: false
    hashed_passwd: {password_hash}
    sudo: ALL=(ALL) NOPASSWD:ALL
write_files:
  - path: /var/lib/uccloud-canary/index.html
    content: "UCcloud maintenance canary {marker}"
  - path: /etc/systemd/system/uccloud-canary-site.service
    content: |
      [Unit]
      After=network-online.target
      [Service]
      User=nobody
      WorkingDirectory=/var/lib/uccloud-canary
      ExecStart=/usr/bin/python3 -m http.server 8080
      Restart=on-failure
      [Install]
      WantedBy=multi-user.target
runcmd:
  - [systemctl, daemon-reload]
  - [systemctl, enable, --now, uccloud-canary-site]
'''
        state={'password':password,'marker':marker,'created_at':datetime.datetime.now(datetime.UTC).isoformat(),'status':'CREATING'}
        STATE.write_text(json.dumps(state));STATE.chmod(0o600)
        server=c.compute.create_server(name='uccloud-security-canary-20261008',
            image_id='50648cab-1c48-4d22-85a7-56853678af04',flavor_id='5c219850-81e5-4a9f-be0b-282969b36529',
            networks=[{'uuid':network.id}],security_groups=[{'name':group.name}],
            availability_zone='nova:comsv-01',metadata={'uccloud_security_canary':marker},
            user_data=base64.b64encode(content.encode()).decode())
        state['id']=server.id;state['status']='BUILD';STATE.write_text(json.dumps(state));STATE.chmod(0o600)
        print('CANARY_CREATED',server.id,flush=True)
    state=json.loads(STATE.read_text());assert state.get('id'),'Review incomplete create state; do not create a second VM'
    server=await asyncio.to_thread(c.compute.wait_for_server,c.compute.get_server(state['id']),wait=300)
    assert server.compute_host==expected_host and server.status=='ACTIVE'
    print('CANARY_BOOT_ACTIVE_ON_QUARANTINED_HOST',flush=True)
    known_hosts=state.get('host_key');ssh=None
    for attempt in range(50):
        ports=list(c.network.ports(device_id=server.id))
        addresses=[i['ip_address'] for p in ports for i in p.fixed_ips if ':' not in i['ip_address']]
        if len(addresses)==1:
            address=addresses[0]
            if attempt==0:
                import subprocess
                route=json.loads(subprocess.check_output(['ip','-j','route','get',address],text=True))[0]
                source=route.get('prefsrc') or route.get('src')
                assert source,'Controller route source is unknown'
                group=c.network.find_security_group('uccloud-maintenance-canary',project_id=c.current_project_id,ignore_missing=False)
                existing=list(c.network.security_group_rules(security_group_id=group.id))
                for number in (22,8080):
                    if not any(r.direction=='ingress' and r.protocol=='tcp' and r.port_range_min==number and r.remote_ip_prefix==source+'/32' for r in existing):
                        c.network.create_security_group_rule(security_group_id=group.id,direction='ingress',protocol='tcp',port_range_min=number,port_range_max=number,remote_ip_prefix=source+'/32',ethertype='IPv4')
            try:
                ssh=await asyncssh.connect(address,username='student',password=state['password'],client_keys=[],
                    known_hosts=(address+' '+known_hosts+'\n').encode() if known_hosts else None,connect_timeout=8,login_timeout=10)
                break
            except (OSError,asyncssh.Error):pass
        await asyncio.sleep(6)
    assert ssh,'Canary SSH/network did not become ready'
    async with ssh:
        state['host_key']=ssh.get_server_host_key().export_public_key('openssh').decode().strip()
        STATE.write_text(json.dumps(state));STATE.chmod(0o600)
        probe=r'''import json,os,subprocess,hashlib,urllib.request
from pathlib import Path
assert 'vmx' not in Path('/proc/cpuinfo').read_text().split()
r=subprocess.run(['modprobe','kvm_intel'],capture_output=True,text=True)
assert r.returncode!=0,'Guest unexpectedly loaded kvm_intel'
assert not Path('/dev/kvm').exists() or not os.access('/dev/kvm',os.R_OK|os.W_OK)
root=Path('/var/lib/uccloud-canary');payload=os.urandom(1024*1024)
p=root/'io.bin'
with p.open('wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(payload).digest()
body=urllib.request.urlopen('http://127.0.0.1:8080/',timeout=10).read()
assert b'UCcloud maintenance canary' in body
print(json.dumps({'vmx':False,'kvm_intel_load':'blocked','dev_kvm':'unusable','file_io':'PASS','http':'PASS'}))
'''
        result=await ssh.run('sudo python3 -c '+shlex.quote(probe),check=False,timeout=40)
        assert result.exit_status==0,'Canary guest benign probe failed'
        print('GUEST',result.stdout.strip(),flush=True)
        state['guest_checks']='PASS';STATE.write_text(json.dumps(state));STATE.chmod(0o600)
    console=c.compute.create_server_remote_console(server,protocol='serial',type='serial')
    # Tokens and internal console addresses remain private.
    state['serial_url']=console.url;STATE.write_text(json.dumps(state));STATE.chmod(0o600)
    print('SERIAL_CONSOLE_TOKEN_ISSUED',flush=True)
    import websockets
    async with websockets.connect(console.url,open_timeout=15) as ws:
        await ws.send(b'\n')
        output=b''
        for _ in range(10):
            chunk=await asyncio.wait_for(ws.recv(),timeout=8)
            output+=chunk.encode() if isinstance(chunk,str) else chunk
            if b'login:' in output:break
        assert b'login:' in output,'Serial console does not reach guest login'
        await ws.send(b'student\n');output=b''
        for _ in range(10):
            chunk=await asyncio.wait_for(ws.recv(),timeout=8)
            output+=chunk.encode() if isinstance(chunk,str) else chunk
            if b'Password:' in output:break
        assert b'Password:' in output,'Serial login did not request credentials'
        await ws.send((state['password']+'\n').encode());await asyncio.sleep(2)
        marker='UCCLOUD_SERIAL_'+secrets.token_hex(8)
        await ws.send(('printf "'+marker+'\\n"\n').encode());output=b''
        for _ in range(12):
            chunk=await asyncio.wait_for(ws.recv(),timeout=8)
            output+=chunk.encode() if isinstance(chunk,str) else chunk
            if (marker+'\r\n').encode() in output:break
        assert (marker+'\r\n').encode() in output,'Serial shell execution failed'
        await ws.send(b'exit\n')
    state['serial_checks']='PASS'
    print('SERIAL_LOGIN_AND_BENIGN_SHELL_PASS',flush=True)
    state['status']='GUEST_VERIFIED';STATE.write_text(json.dumps(state));STATE.chmod(0o600)
    path=STATE.parent/'guest-gate.json';guests=json.loads(path.read_text()) if path.exists() else {}
    guests[server.id]={'host':expected_host,'time':datetime.datetime.now(datetime.UTC).isoformat(),
        **{key:'PASS' for key in ('vmx_absent','kvm_intel_blocked','dev_kvm_unusable','ssh_or_serial','network','file_io')}}
    path.write_text(json.dumps(guests,indent=2));path.chmod(0o600)

asyncio.run(main())

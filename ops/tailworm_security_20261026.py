"""Approved maintenance: preserve sessions; fixed SSH hardening with timed rollback.

No password changes, guest shutdowns, payloads, or Tailscale profile changes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

STATE = Path('/var/lib/uccloud-tailworm-20261026')
os.environ['LC_ALL'] = 'C'
CONF = Path('/etc/ssh/sshd_config.d/00-uccloud-forwarding.conf')
NFT = Path('/etc/uccloud-security/ssh-boundary.nft')
NFT_UNIT = Path('/etc/systemd/system/uccloud-ssh-boundary.service')
HOSTS = {'comsv-03', 'comsv-01', 'comsv-04', 'comsv-05', 'user-ProLiant-DL20-Gen11'}


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=60, **kwargs).stdout.strip()


def save(name, value):
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    p = STATE / name
    p.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    p.chmod(0o600)


def effective(user):
    text = run(['/usr/sbin/sshd', '-T', '-C', f'user={user},host=localhost,addr=100.65.251.104'])
    names = {'permitrootlogin', 'passwordauthentication', 'allowtcpforwarding',
             'allowagentforwarding', 'allowstreamlocalforwarding', 'permitopen',
             'permitlisten', 'gatewayports', 'disableforwarding', 'x11forwarding'}
    return {line.split(' ', 1)[0]: line.split(' ', 1)[1] for line in text.splitlines()
            if ' ' in line and line.split(' ', 1)[0] in names}


def snapshot():
    prefs = json.loads(run(['tailscale', 'debug', 'prefs']))
    status = json.loads(run(['tailscale', 'status', '--json']))
    domains = []
    if shutil.which('virsh'):
        domains = run(['virsh', '-c', 'qemu:///system', 'list', '--name']).split()
    return {'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'host': run(['hostname']),
            'tailscale_package': run(['dpkg-query', '-W', '-f=${Version}', 'tailscale']),
            'daemon_version': status.get('Version'),
            'run_ssh': prefs.get('RunSSH'), 'operator': prefs.get('OperatorUser'),
            'root': effective('root'), 'user': effective('user'), 'running_domains': domains,
            'ssh_master_pid': run(['systemctl', 'show', 'ssh', '-p', 'MainPID', '--value']),
            'serve': json.loads(run(['tailscale', 'serve', 'status', '--json']))}


def apply_ssh():
    before = snapshot()
    host = before['host']
    assert host in HOSTS and before['run_ssh'] is False
    assert not CONF.exists(), 'Existing policy must be reviewed, not overwritten'
    STATE.mkdir(mode=0o700, exist_ok=True)
    backup = STATE / 'sshd-config-before.tar'
    assert not backup.exists()
    run(['tar', '-cf', str(backup), '/etc/ssh/sshd_config', '/etc/ssh/sshd_config.d'])
    backup.chmod(0o600)
    save('ssh-before.json', before)
    text = ('# UCcloud 2026-10-26: keep authentication; constrain forwarding only.\n'
            'AllowAgentForwarding no\nAllowStreamLocalForwarding no\n'
            'GatewayPorts no\nX11Forwarding no\nPermitTunnel no\n'
            'AllowTcpForwarding no\nPermitListen none\n')
    if host == 'comsv-03':
        text += ('Match User user\n    AllowTcpForwarding local\n'
                 '    PermitOpen 127.0.0.1:8088 localhost:8088 [::1]:8088 '
                 '127.0.0.1:19088 localhost:19088 [::1]:19088\nMatch all\n')
    CONF.write_text(text)
    CONF.chmod(0o644)
    try:
        run(['/usr/sbin/sshd', '-t'])
        rollback = ('/usr/bin/python3 ' + str(Path(__file__).resolve()) + ' rollback-ssh')
        run(['systemd-run', '--unit=uccloud-ssh-rollback-20261026', '--on-active=8m',
             '--timer-property=AccuracySec=1s', '/bin/sh', '-c', rollback])
        # HUP the master only; established SSH children are not terminated.
        run(['systemctl', 'reload', 'ssh'])
        after = snapshot()
        assert after['ssh_master_pid'] == before['ssh_master_pid']
        assert after['running_domains'] == before['running_domains']
        assert after['serve'] == before['serve']
        for user in ('root', 'user'):
            assert after[user]['allowagentforwarding'] == 'no'
            assert after[user]['allowstreamlocalforwarding'] == 'no'
            assert after[user]['permitrootlogin'] == before[user]['permitrootlogin']
            assert after[user]['passwordauthentication'] == before[user]['passwordauthentication']
        assert after['root']['allowtcpforwarding'] == 'no'
        assert after['user']['allowtcpforwarding'] == ('local' if host == 'comsv-03' else 'no')
        save('ssh-after.json', after)
        print(json.dumps({'host': host, 'applied': True, 'master_preserved': True,
                          'rollback_timer_pending': True}))
    except BaseException:
        CONF.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'reload', 'ssh'], check=False)
        raise


def rollback_ssh():
    if (STATE / 'ssh-committed.json').exists():
        return
    CONF.unlink(missing_ok=True)
    run(['/usr/sbin/sshd', '-t'])
    run(['systemctl', 'reload', 'ssh'])
    save('ssh-rolled-back.json', {'time': dt.datetime.now(dt.timezone.utc).isoformat()})


def commit_ssh():
    assert CONF.exists() and (STATE / 'ssh-after.json').exists()
    save('ssh-committed.json', {'time': dt.datetime.now(dt.timezone.utc).isoformat(),
                              'config_sha256': hashlib.sha256(CONF.read_bytes()).hexdigest()})
    subprocess.run(['systemctl', 'stop', 'uccloud-ssh-rollback-20261026.timer'], check=True)
    print('SSH_POLICY_COMMITTED')


def apply_boundary():
    host = run(['hostname'])
    assert host in HOSTS and not NFT.exists() and not NFT_UNIT.exists()
    STATE.mkdir(mode=0o700, exist_ok=True)
    prior = run(['/usr/sbin/nft', '-j', 'list', 'ruleset'])
    assert 'uccloud_ssh_boundary' not in prior
    save('firewall-before.json', json.loads(prior))
    # Only TCP/22 arriving on tailscale0 is affected. No management LAN,
    # OpenStack, VM, peerapi, Serve, Funnel, or unrelated firewall rules change.
    v4 = ['100.65.251.104', '100.74.103.115']
    v6 = ['fd7a:115c:a1e0::ba34:fb69', 'fd7a:115c:a1e0::db34:6774']
    if host == 'comsv-03':
        v4.append('100.83.255.31')
        v6.append('fd7a:115c:a1e0::ac34:ff20')
    text = ('destroy table inet uccloud_ssh_boundary\n'
            'table inet uccloud_ssh_boundary {\n chain input {\n'
            '  type filter hook input priority -5; policy accept;\n'
            '  iifname "tailscale0" tcp dport 22 ct state established,related counter accept\n'
            '  iifname "tailscale0" tcp dport 22 ip saddr { ' + ', '.join(v4) + ' } counter accept\n'
            '  iifname "tailscale0" tcp dport 22 ip6 saddr { ' + ', '.join(v6) + ' } counter accept\n'
            '  iifname "tailscale0" tcp dport 22 counter drop\n }\n}\n')
    NFT.parent.mkdir(mode=0o755, exist_ok=True)
    NFT.write_text(text)
    NFT.chmod(0o644)
    try:
        run(['/usr/sbin/nft', '--check', '-f', str(NFT)])
        run(['systemd-run', '--unit=uccloud-boundary-rollback-20261026', '--on-active=8m',
             '--timer-property=AccuracySec=1s', '/usr/bin/python3', str(Path(__file__).resolve()),
             'rollback-boundary'])
        NFT_UNIT.write_text('[Unit]\nDescription=UCcloud management SSH source boundary\n'
                            'After=nftables.service\nBefore=ssh.service\n'
                            '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                            'ExecStart=/usr/sbin/nft -f ' + str(NFT) + '\n'
                            '[Install]\nWantedBy=multi-user.target\n')
        NFT_UNIT.chmod(0o644)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', '--now', 'uccloud-ssh-boundary.service'])
        save('boundary-after.json', {'host': host, 'applied': True, 'interface': 'tailscale0',
                                    'port': 22, 'allowed_source_roles':
                                    ['admin-laptop', 'controller'] + (['compute-1-recovery'] if host == 'comsv-03' else [])})
        print('SSH_SOURCE_BOUNDARY_APPLIED', host)
    except BaseException:
        rollback_boundary()
        raise


def rollback_boundary():
    if (STATE / 'boundary-committed.json').exists(): return
    subprocess.run(['nft', 'delete', 'table', 'inet', 'uccloud_ssh_boundary'], capture_output=True)
    subprocess.run(['systemctl', 'disable', '--now', 'uccloud-ssh-boundary.service'], capture_output=True)
    NFT.unlink(missing_ok=True)
    NFT_UNIT.unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
    save('boundary-rolled-back.json', {'time': dt.datetime.now(dt.timezone.utc).isoformat()})


def commit_boundary():
    assert NFT.exists() and (STATE / 'boundary-after.json').exists()
    save('boundary-committed.json', {'time': dt.datetime.now(dt.timezone.utc).isoformat(),
                                   'config_sha256': hashlib.sha256(NFT.read_bytes()).hexdigest()})
    run(['systemctl', 'stop', 'uccloud-boundary-rollback-20261026.timer'])
    print('SSH_SOURCE_BOUNDARY_COMMITTED')


def update_tailscale():
    before = snapshot()
    assert before['host'] in HOSTS and before['run_ssh'] is False
    assert (STATE / 'ssh-committed.json').exists()
    assert (STATE / 'boundary-committed.json').exists()
    # Explicit stable security release. Never install an unreviewed development PR.
    version = '1.104.2'
    if before['tailscale_package'] == version and before['daemon_version'].startswith(version):
        print('ALREADY_CURRENT'); return
    save('tailscale-before.json', before)
    backup = STATE / 'tailscaled-state-before'
    if not backup.exists():
        shutil.copy2('/var/lib/tailscale/tailscaled.state', backup)
        backup.chmod(0o600)
    simulated = run(['apt-get', '-s', '--only-upgrade', '--no-install-recommends', 'install', 'tailscale=' + version])
    assert '0 to remove' in simulated
    if before['host'] == 'comsv-03':
        # Caller must have explicitly armed the approved short Funnel interruption.
        assert (STATE / 'controller-update-approved').exists()
    start = dt.datetime.now(dt.timezone.utc).isoformat()
    command = ['apt-get', '-y', '--only-upgrade', '--no-install-recommends', 'install', 'tailscale=' + version]
    p = subprocess.run(command, text=True, capture_output=True, timeout=240,
                       env=dict(os.environ, DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l'))
    log = STATE / 'tailscale-update.log'
    log.write_text(p.stdout + p.stderr); log.chmod(0o600)
    assert p.returncode == 0, 'Review root-only package log'
    end = time.monotonic() + 60
    while True:
        try:
            after = snapshot()
            if (after['daemon_version'].startswith(version) and after['serve'] == before['serve']
                    and after['run_ssh'] is False): break
        except (subprocess.SubprocessError, json.JSONDecodeError): pass
        assert time.monotonic() < end, 'Daemon did not become ready'
        time.sleep(1)
    assert after['tailscale_package'] == version and after['run_ssh'] is False
    assert after['serve'] == before['serve']
    assert after['running_domains'] == before['running_domains']
    assert after['ssh_master_pid'] == before['ssh_master_pid']
    save('tailscale-after.json', {'started': start, 'finished': dt.datetime.now(dt.timezone.utc).isoformat(),
                                 'snapshot': after, 'vm_count_unchanged': True,
                                 'serve_unchanged': True, 'ssh_master_unchanged': True,
                                 'tailworm_fully_patched': False})
    print(json.dumps({'host': after['host'], 'daemon': after['daemon_version'],
                      'RunSSH': after['run_ssh'], 'vm_count_unchanged': True, 'serve_unchanged': True}))


def verify_update():
    before = json.loads((STATE / 'tailscale-before.json').read_text())
    after = snapshot()
    assert after['tailscale_package'] == '1.104.2' and after['daemon_version'].startswith('1.104.2')
    assert after['run_ssh'] is False and after['serve'] == before['serve']
    assert after['running_domains'] == before['running_domains']
    assert after['ssh_master_pid'] == before['ssh_master_pid']
    save('tailscale-after.json', {'finished': dt.datetime.now(dt.timezone.utc).isoformat(),
                                 'snapshot': after, 'vm_count_unchanged': True,
                                 'serve_unchanged': True, 'ssh_master_unchanged': True,
                                 'verified_after_settle': True, 'tailworm_fully_patched': False})
    print('UPDATE_RUNTIME_VERIFIED', after['host'])


def main():
    assert os.geteuid() == 0
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['snapshot', 'apply-ssh', 'rollback-ssh', 'commit-ssh',
                                     'apply-boundary', 'rollback-boundary', 'commit-boundary', 'update-tailscale',
                                     'verify-update'])
    action = p.parse_args().action
    if action == 'snapshot': print(json.dumps(snapshot()))
    elif action == 'apply-ssh': apply_ssh()
    elif action == 'rollback-ssh': rollback_ssh()
    elif action == 'commit-ssh': commit_ssh()
    elif action == 'apply-boundary': apply_boundary()
    elif action == 'rollback-boundary': rollback_boundary()
    elif action == 'commit-boundary': commit_boundary()
    elif action == 'update-tailscale': update_tailscale()
    else: verify_update()


if __name__ == '__main__':
    main()

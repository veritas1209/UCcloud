"""Host-side evacuated compute preparation; root, signed repo, no VM deletion."""
import configparser,json,os,runpy,subprocess,sys
from pathlib import Path

host=os.uname().nodename
assert os.geteuid()==0 and host in ('comsv-01','comsv-04','comsv-05','user-ProLiant-DL20-Gen11')
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip()
assert Path('/var/backups/uccloud-hypervisor/20261008/host-config.tar').exists()
service=subprocess.check_output(['systemctl','show','nova-compute','-p','ActiveState','-p','MainPID'],text=True)
service=dict(line.split('=',1) for line in service.splitlines() if '=' in line)
assert service['ActiveState'] in ('inactive','failed') and service['MainPID']=='0','Nova must be fully stopped'
assert subprocess.check_output(['dpkg-query','-W','-f=${Version}','linux-image-7.0.0-38-generic'],text=True).strip()=='7.0.0-38.38'
subprocess.run(['python3','/usr/local/sbin/uccloud-install-qemu-security',sys.argv[1]],check=True)
path=Path('/etc/modprobe.d/uccloud-kvm-security.conf')
path.write_text('# UCcloud persistent KVM security policy\noptions kvm_intel nested=0 enable_shadow_vmcs=0\n');path.chmod(0o644)
subprocess.run(['update-initramfs','-u','-k','7.0.0-38-generic'],check=True)
conf=configparser.ConfigParser(strict=False,interpolation=None);path=Path('/etc/nova/nova.conf');conf.read(path)
conf.set('DEFAULT','compute_driver','libvirt.uccloud_security.SecurityLibvirtDriver')
flags=[item.strip() for item in conf.get('libvirt','cpu_model_extra_flags',fallback='').split(',') if item.strip()]
flags=[flag for flag in flags if flag.lstrip('+-').lower()!='vmx']+['-vmx']
conf.set('libvirt','cpu_model_extra_flags',','.join(flags))
if not conf.has_section('serial_console'):conf.add_section('serial_console')
conf.set('serial_console','enabled','true')
conf.set('serial_console','proxyclient_address',conf.get('DEFAULT','my_ip'))
conf.set('serial_console','base_url',sys.argv[2])
with path.open('w') as stream:conf.write(stream)
path.chmod(0o640)
dropin=Path('/etc/systemd/system/nova-compute.service.d/uccloud-security.conf');dropin.parent.mkdir(exist_ok=True)
dropin.write_text('[Service]\nExecStartPre=+/usr/bin/python3 /usr/local/sbin/uccloud-hypervisor-policy-guard\n');dropin.chmod(0o644)
subprocess.run(['systemctl','daemon-reload'],check=True)
print('SIGNED_QEMU_AND_PERSISTENT_KERNEL_NOVA_POLICY_READY',host,flush=True)
# Reboot is deliberately a separate controller operation, after reviewing this result.

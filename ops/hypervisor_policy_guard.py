"""Install the protected sidecar in Nova's current venv before every service start."""
import configparser,importlib.util,os,re,subprocess,sys
from pathlib import Path

assert os.geteuid()==0
base=Path('/usr/local/lib/uccloud')
spec=importlib.util.spec_from_file_location('policy',base/'hypervisor_xml_policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
policy.approved_qemu()
subprocess.run(['modprobe','kvm_intel'],check=True)
assert all((Path('/sys/module/kvm_intel/parameters')/key).read_text().strip()=='N' for key in ('nested','enable_shadow_vmcs'))
c=configparser.ConfigParser(strict=False,interpolation=None);c.read('/etc/nova/nova.conf')
assert c.get('DEFAULT','compute_driver')=='libvirt.uccloud_security.SecurityLibvirtDriver'
assert '-vmx' in [flag.strip() for flag in c.get('libvirt','cpu_model_extra_flags').split(',')]
command=subprocess.check_output(['systemctl','show','nova-compute','-p','ExecStart','--value'],text=True)
match=re.search(r'path=(/openstack/venvs/nova-[a-zA-Z0-9_.+-]+/bin/(?:nova-compute|python3?))',command)
assert match,'Nova executable is outside the approved venv layout'
venv=Path(match.group(1)).parent.parent
destinations=list(venv.glob('lib/python*/site-packages/nova/virt/libvirt'))
assert len(destinations)==1,'Unexpected Nova package installation layout'
source=base/'nova_uccloud_security.py';target=destinations[0]/'uccloud_security.py'
content=source.read_bytes()
if not target.exists() or target.read_bytes()!=content:
    tmp=target.with_suffix('.next');tmp.write_bytes(content);tmp.chmod(0o644);tmp.replace(target)
print('UCCLOUD_HYPERVISOR_START_POLICY_VERIFIED')

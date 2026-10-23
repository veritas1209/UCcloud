"""Central, fail-closed Nova/libvirt policy. No libvirt calls inside the hook."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

POLICY = Path('/etc/uccloud-hypervisor/qemu-approved.json')
REQUIRED = {
    'CVE-2026-3886':'c035d5eadf400670593a76778f98f052d7482968',
    'CVE-2026-48914':'f5e2c6906cad9a84140e232f2e3eb7a46bf07f62',
}

def approved_qemu():
    policy=json.loads(POLICY.read_text())
    if any(policy.get('upstream',{}).get(cve,{}).get('commit')!=commit for cve,commit in REQUIRED.items()):
        raise ValueError('UCcloud: required QEMU patch evidence is absent')
    version=subprocess.check_output(['dpkg-query','-W','-f=${Version}','qemu-system-x86'],text=True).strip()
    if version != policy['release']:raise ValueError('UCcloud: QEMU package drift; keep the compute quarantined')
    for name,expected in policy['runtime_files'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=expected:
            raise ValueError('UCcloud: QEMU executable or module drift')
    if '/usr/bin/qemu-system-x86_64' not in policy['runtime_files']:
        raise ValueError('UCcloud: QEMU executable evidence is absent')

def secure_boot(root):
    return (root.find('./os/loader') is not None and root.find('./os/loader').get('secure')=='yes') or any(
        item.get('name')=='secure-boot' and item.get('enabled')=='yes' for item in root.findall('./os/firmware/feature'))

def validate_devices(root):
    if secure_boot(root):raise ValueError('UCcloud: Secure Boot/SMM exception is quarantined')
    if root.findall('./devices/filesystem') or root.findall('./devices/hostdev') or root.findall('./devices/crypto'):
        raise ValueError('UCcloud: filesystem sharing, crypto and device passthrough are prohibited')
    if any(item.get('model')=='virtio' for item in root.findall('./devices/sound')):
        raise ValueError('UCcloud: virtio-snd is prohibited')
    if root.findall('.//{http://libvirt.org/schemas/domain/qemu/1.0}arg'):
        raise ValueError('UCcloud: arbitrary QEMU command-line overrides are prohibited')
    emulator=root.findtext('./devices/emulator')
    if emulator!='/usr/bin/qemu-system-x86_64':raise ValueError('UCcloud: unapproved QEMU emulator')

def enforce(xml):
    root=ET.fromstring(xml)
    # Nova omits <emulator> and normally lets libvirt select it. Pin that
    # unspecified value to the approved executable before strict validation.
    # An explicit alternate executable must still be rejected.
    devices=root.find('devices')
    if devices is None:raise ValueError('UCcloud: missing device policy')
    if devices.find('emulator') is None:
        emulator=ET.Element('emulator');emulator.text='/usr/bin/qemu-system-x86_64'
        devices.insert(0,emulator)
    validate_devices(root)
    features=root.find('features')
    if features is None:features=ET.SubElement(root,'features')
    for smm in features.findall('smm'):features.remove(smm)
    ET.SubElement(features,'smm',{'state':'off'})
    cpu=root.find('cpu')
    if cpu is None:raise ValueError('UCcloud: missing CPU policy')
    for flag in cpu.findall('feature'):
        if flag.get('name','').lower()=='vmx' or flag.get('name','').lower().startswith('vmx-'):cpu.remove(flag)
    ET.SubElement(cpu,'feature',{'policy':'disable','name':'vmx'})
    crash=root.find('on_crash')
    if crash is None:crash=ET.SubElement(root,'on_crash')
    crash.text='destroy'
    return ET.tostring(root,encoding='unicode')

def validate(xml, *, check_runtime=True):
    root=ET.fromstring(xml)
    validate_devices(root)
    smm=root.findall('./features/smm')
    if len(smm)!=1 or smm[0].get('state')!='off':raise ValueError('UCcloud: effective SMM-off policy is required')
    flags=root.findall('./cpu/feature')
    if not any(flag.get('name')=='vmx' and flag.get('policy')=='disable' for flag in flags):
        raise ValueError('UCcloud: explicit VMX-disable policy is required')
    if any(flag.get('name','').startswith('vmx') and flag.get('policy') not in ('disable','forbid') for flag in flags):
        raise ValueError('UCcloud: nested VMX request is prohibited')
    if root.findtext('on_crash') in ('restart','coredump-restart'):
        raise ValueError('UCcloud: automatic crash restart is prohibited')
    if check_runtime:approved_qemu()

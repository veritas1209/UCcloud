"""Benign XML-only policy checks. No malformed device traffic or exploit tests."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

spec=importlib.util.spec_from_file_location('policy',Path(__file__).parents[1]/'ops/hypervisor_xml_policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
base="""<domain type='kvm'><os><type>hvm</type></os><features><acpi/><smm state='on'/></features><cpu mode='host-passthrough'><feature policy='require' name='vmx'/><feature policy='require' name='aes'/></cpu><devices><emulator>/usr/bin/qemu-system-x86_64</emulator><disk type='file'><target dev='vda' bus='virtio'/></disk><video><model type='virtio'/></video></devices></domain>"""
secured=policy.enforce(base);policy.validate(secured,check_runtime=False)
root=ET.fromstring(secured)
assert root.find('./features/smm').get('state')=='off'
assert root.find('./cpu/feature[@name="vmx"]').get('policy')=='disable'
assert root.find('./cpu/feature[@name="aes"]').get('policy')=='require'
assert root.find('./devices/disk/target').get('bus')=='virtio'
assert policy.enforce(secured)==secured
nova_default=base.replace('<emulator>/usr/bin/qemu-system-x86_64</emulator>','')
pinned=policy.enforce(nova_default);policy.validate(pinned,check_runtime=False)
assert ET.fromstring(pinned).findtext('./devices/emulator')=='/usr/bin/qemu-system-x86_64'
for executable in ('/usr/bin/qemu-system-i386','/tmp/qemu',''):
    try:policy.enforce(base.replace('/usr/bin/qemu-system-x86_64',executable))
    except ValueError:pass
    else:raise AssertionError('Explicit unapproved emulator was accepted')
try:policy.validate(nova_default,check_runtime=False)
except ValueError:pass
else:raise AssertionError('Unpinned emulator passed strict validation')
for content in [base,secured.replace("state=\"off\"","state=\"auto\""),secured.replace('policy="disable"','policy="require"')]:
    try:policy.validate(content,check_runtime=False)
    except ValueError:pass
    else:raise AssertionError('Unsafe VM XML was accepted')
for device in ['<filesystem type="mount"/>','<hostdev/>','<sound model="virtio"/>','<crypto/>']:
    try:policy.enforce(base.replace('</devices>',device+'</devices>'))
    except ValueError:pass
    else:raise AssertionError('Prohibited device was accepted')
try:policy.enforce(base.replace('<os>','<os><loader secure="yes">/firmware</loader>'))
except ValueError:pass
else:raise AssertionError('Secure Boot exception was accepted')
print('SMM off, VMX disabled, existing AES/disk preservation, overrides and prohibited devices passed')

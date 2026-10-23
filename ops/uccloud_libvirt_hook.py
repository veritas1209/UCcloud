#!/usr/bin/python3
"""Validate starts only. Never kill an existing VM during libvirt reconnect."""
import importlib.util
import sys

def main():
    if len(sys.argv)<4:return 0
    operation,phase=sys.argv[2:4]
    if phase!='begin' or operation not in ('prepare','start','migrate','restore'):return 0
    spec=importlib.util.spec_from_file_location('uccloud_hypervisor_policy','/usr/local/lib/uccloud/hypervisor_xml_policy.py')
    policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
    try:policy.validate(sys.stdin.read(2_000_000))
    except Exception:
        print('UCcloud security policy rejected the VM start; preserve evidence and keep compute quarantined.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':sys.exit(main())

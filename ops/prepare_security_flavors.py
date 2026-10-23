"""Create, never overwrite, new-generation VM sizes; central driver enforces security."""
import json,runpy
from pathlib import Path
ROOT=Path('/var/lib/uccloud-maintenance/20261008')
m=runpy.run_path(str(ROOT/'maintenance.py'));m['quarantine_required']();c=m['admin']()
path=ROOT/'security-flavors.json';assert not path.exists(),'Review existing flavor evidence before retrying'
records=[]
for name in ('small','medium','large'):
    old=c.compute.find_flavor(name,ignore_missing=False)
    old=c.compute.fetch_flavor_extra_specs(old)
    new_name=name+'-v2'
    assert c.compute.find_flavor(new_name,ignore_missing=True) is None,'Never overwrite an existing flavor'
    extras=dict(old.extra_specs or {})
    assert not any('vmx' in str(k).lower()+str(v).lower() for k,v in extras.items())
    extras['uccloud:security_generation']='20261008'
    new=c.compute.create_flavor(name=new_name,ram=old.ram,vcpus=old.vcpus,disk=old.disk,
        ephemeral=old.ephemeral or 0,swap=old.swap or 0,rxtx_factor=old.rxtx_factor or 1.0,is_public=old.is_public,
        description='UCcloud security generation 20261008; central VMX-off and SMM-off policy')
    c.compute.create_flavor_extra_specs(new,extras)
    checked=c.compute.fetch_flavor_extra_specs(c.compute.get_flavor(new.id))
    assert (checked.ram,checked.vcpus,checked.disk)==(old.ram,old.vcpus,old.disk)
    assert checked.extra_specs==extras
    records.append({'original_id':old.id,'original_name':name,'new_id':new.id,'new_name':new_name,
        'ram':checked.ram,'vcpus':checked.vcpus,'disk':checked.disk,'extra_specs':checked.extra_specs})
    path.write_text(json.dumps({'generation':'20261008','flavors':records,'published':False},indent=2));path.chmod(0o600)
print('THREE_NEW_GENERATION_FLAVORS_PREPARED_ORIGINAL_FLAVORS_UNCHANGED')

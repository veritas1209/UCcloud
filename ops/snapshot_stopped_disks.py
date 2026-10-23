"""Flatten stopped Nova disks into private independent backups; never prune files."""
import datetime,hashlib,json,os,shutil,subprocess,xml.etree.ElementTree as ET
from pathlib import Path

assert os.geteuid()==0
assert not subprocess.check_output(['virsh','list','--name'],text=True).strip(),'Running VMs make an offline copy unsafe'
base=Path('/var/backups/uccloud-hypervisor/20261008/disks');base.mkdir(mode=0o700,exist_ok=True)
assert base.resolve().is_relative_to(Path('/var/backups/uccloud-hypervisor/20261008'))
evidence=[]
for name in subprocess.check_output(['virsh','list','--all','--name'],text=True).splitlines():
    if not name:continue
    root=ET.fromstring(subprocess.check_output(['virsh','dumpxml',name,'--inactive']))
    uuid=root.findtext('uuid')
    for disk in root.findall('./devices/disk'):
        if disk.get('device')!='disk':continue
        source=disk.find('source');target=disk.find('target')
        assert source is not None and source.get('file'),'Non-file storage needs a separate reviewed snapshot workflow'
        path=Path(source.get('file')).resolve()
        assert path.is_relative_to(Path('/var/lib/nova/instances').resolve()),'Disk is outside Nova instance storage'
        assert path.is_file() and not path.is_symlink()
        info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(path)],text=True))
        assert shutil.disk_usage(base).free>info['virtual-size']+5*1024**3,'Insufficient space for a complete independent backup'
        output=base/(uuid+'-'+target.get('dev')+'.qcow2')
        metadata=output.with_suffix('.json')
        if output.exists():
            assert metadata.exists(),'Incomplete snapshot must be reviewed; do not overwrite'
            item=json.loads(metadata.read_text())
            with output.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256']
        else:
            subprocess.run(['qemu-img','convert','-p','-O','qcow2',str(path),str(output)],check=True,stdout=subprocess.DEVNULL)
            output.chmod(0o600)
            subprocess.run(['qemu-img','check',str(output)],check=True,stdout=subprocess.DEVNULL)
            copied=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(output)],text=True))
            assert not copied.get('backing-filename'),'Backup is not independent'
            with output.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            item={'uuid':uuid,'device':target.get('dev'),'virtual_size':info['virtual-size'],'snapshot_bytes':output.stat().st_size,'sha256':digest,'created_at':datetime.datetime.now(datetime.UTC).isoformat()}
            metadata.write_text(json.dumps(item,indent=2));metadata.chmod(0o600)
        evidence.append(item)
print(json.dumps({'offline_snapshots':len(evidence),'checks':'PASS','disks':evidence}))

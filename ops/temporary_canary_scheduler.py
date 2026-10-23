"""Container-local install/arm/restore of the approved single-canary filter."""
import configparser,hashlib,json,os,subprocess,sys,time
from pathlib import Path
from nova import config
from nova.scheduler.filters import compute_filter

assert os.geteuid()==0
mode=sys.argv[1];assert mode in ('install','arm','restore')
attempt=int(sys.argv[3]) if len(sys.argv)>3 else 1;assert 1<=attempt<=2
ROOT=Path('/root/uccloud-canary-migration-20261008'+('' if attempt==1 else '-'+str(attempt)))
CONF=Path('/etc/nova/nova.conf')
MODULE=Path(compute_filter.__file__).parent/'uccloud_canary_compute_filter.py'
PERMIT=Path('/run/uccloud-canary-migration-permit.json')
record=ROOT/'record.json'
def sha(value):return hashlib.sha256(value).hexdigest()
if mode=='install':
    assert not ROOT.exists() and not MODULE.exists() and not PERMIT.exists()
    config.parse_args(['uccloud-maintenance','--config-file',str(CONF)])
    from oslo_config import cfg
    enabled=list(cfg.CONF.filter_scheduler.enabled_filters)
    available=list(cfg.CONF.filter_scheduler.available_filters)
    assert enabled.count('ComputeFilter')==1 and available==['nova.scheduler.filters.all_filters']
    ROOT.mkdir(mode=0o700)
    original=CONF.read_bytes();p=ROOT/'nova.conf.before';p.write_bytes(original);p.chmod(0o600)
    MODULE.write_bytes(Path(sys.argv[2]).read_bytes());MODULE.chmod(0o644)
    updated=[('UCcloudCanaryComputeFilter' if name=='ComputeFilter' else name) for name in enabled]
    text=original.decode();lines=text.splitlines(keepends=True);start=None;end=len(lines)
    for i,line in enumerate(lines):
        if line.strip()=='[filter_scheduler]':assert start is None;start=i
        elif start is not None and line.lstrip().startswith('['):end=i;break
    if start is None:
        text=text.rstrip()+'\n\n[filter_scheduler]\nenabled_filters = '+','.join(updated)+'\n'
    else:
        matches=[i for i in range(start+1,end) if lines[i].lstrip().startswith('enabled_filters') and '=' in lines[i]]
        assert len(matches)<=1
        entry='enabled_filters = '+','.join(updated)+'\n'
        if matches:lines[matches[0]]=entry
        else:lines.insert(end,entry)
        text=''.join(lines)
    parser=configparser.ConfigParser(strict=False,interpolation=None);parser.read_string(text)
    assert parser.get('filter_scheduler','enabled_filters').split(',')==updated
    CONF.write_text(text)
    r={'original_sha256':sha(original),'temporary_sha256':sha(CONF.read_bytes()),'restored':False}
    record.write_text(json.dumps(r));record.chmod(0o600)
    subprocess.run(['systemctl','restart','nova-scheduler'],check=True,timeout=240)
    assert subprocess.check_output(['systemctl','is-active','nova-scheduler'],text=True).strip()=='active'
    print('TEMPORARY_FILTER_LOADED_NO_CANARY_PERMISSION_ARMED')
elif mode=='arm':
    r=json.loads(record.read_text());assert not r['restored'] and sha(CONF.read_bytes())==r['temporary_sha256']
    permit=json.loads(Path(sys.argv[2]).read_text())
    assert permit['host']=='comsv-04' and permit['operation']=='cold_migration' and permit['runtime_gate']=='PASS'
    assert len(permit['instance_uuid'])==36 and len(permit['project_id'])==32
    permit['expires_at']=time.time()+300
    PERMIT.write_text(json.dumps(permit));PERMIT.chmod(0o644)
    print('ONE_ADMINISTRATOR_CANARY_PERMISSION_ARMED_FOR_FIVE_MINUTES')
else:
    r=json.loads(record.read_text());assert sha(CONF.read_bytes())==r['temporary_sha256']
    if PERMIT.exists():
        old=ROOT/'permit-expired.json';assert not old.exists()
        old.write_bytes(PERMIT.read_bytes());old.chmod(0o600)
        assert old.read_bytes()==PERMIT.read_bytes();PERMIT.unlink()
    original=(ROOT/'nova.conf.before').read_bytes();assert sha(original)==r['original_sha256']
    CONF.write_bytes(original)
    subprocess.run(['systemctl','restart','nova-scheduler'],check=True,timeout=240)
    assert subprocess.check_output(['systemctl','is-active','nova-scheduler'],text=True).strip()=='active'
    old=ROOT/'canary-filter-retired.py';assert not old.exists()
    old.write_bytes(MODULE.read_bytes());old.chmod(0o600)
    assert old.read_bytes()==MODULE.read_bytes();MODULE.unlink()
    r['restored']=True;record.write_text(json.dumps(r));record.chmod(0o600)
    print('ORIGINAL_COMPUTE_FILTER_AND_SCHEDULER_CONFIGURATION_RESTORED')

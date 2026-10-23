import importlib.util,json,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch

class FilterScopeTests(unittest.TestCase):
    def test_only_approved_live_canary_destination_is_allowed(self):
        modules={name:types.ModuleType(name) for name in ('nova','nova.scheduler','nova.scheduler.filters','nova.scheduler.filters.compute_filter')}
        class ComputeFilter:
            def host_passes(self,host,spec):return not host.service['disabled']
        modules['nova.scheduler.filters.compute_filter'].ComputeFilter=ComputeFilter
        with patch.dict(sys.modules,modules),tempfile.TemporaryDirectory() as temporary:
            spec=importlib.util.spec_from_file_location('canary_filter',Path(__file__).parents[1]/'ops'/'nova_canary_compute_filter.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.PERMIT=Path(temporary)/'permit.json'
            policy={'operation':'cold_migration','expires_at':time.time()+300,'instance_uuid':'canary','project_id':'administrator','host':'comsv-04','runtime_gate':'PASS'}
            module.PERMIT.write_text(json.dumps(policy))
            filter=module.UCcloudCanaryComputeFilter();filter.servicegroup_api=types.SimpleNamespace(service_is_up=lambda service:True)
            host=types.SimpleNamespace(host='comsv-04',service={'disabled':True,'forced_down':False})
            request=types.SimpleNamespace(instance_uuid='canary',project_id='administrator',requested_destination=types.SimpleNamespace(host='comsv-04'))
            self.assertTrue(filter.host_passes(host,request))
            request.instance_uuid='student';self.assertFalse(filter.host_passes(host,request));request.instance_uuid='canary'
            request.project_id='student';self.assertFalse(filter.host_passes(host,request));request.project_id='administrator'
            request.requested_destination.host='comsv-05';self.assertFalse(filter.host_passes(host,request));request.requested_destination.host='comsv-04'
            host.service['forced_down']=True;self.assertFalse(filter.host_passes(host,request));host.service['forced_down']=False
            policy['expires_at']=time.time()-1;module.PERMIT.write_text(json.dumps(policy));self.assertFalse(filter.host_passes(host,request))
            module.PERMIT.unlink();self.assertFalse(filter.host_passes(host,request))
            host.service['disabled']=False;self.assertTrue(filter.host_passes(host,request))

if __name__=='__main__':unittest.main()

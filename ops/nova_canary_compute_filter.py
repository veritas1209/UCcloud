"""Temporary, expiring exception for one explicitly approved administrator canary."""
import json,time
from pathlib import Path
from nova.scheduler.filters.compute_filter import ComputeFilter

PERMIT=Path('/run/uccloud-canary-migration-permit.json')

class UCcloudCanaryComputeFilter(ComputeFilter):
    def host_passes(self,host_state,spec_obj):
        if super().host_passes(host_state,spec_obj):return True
        try:
            permit=json.loads(PERMIT.read_text())
            now=time.time()
            destination=spec_obj.requested_destination
            return bool(permit['operation']=='cold_migration'
                and now<permit['expires_at']<=now+600
                and permit['instance_uuid']==spec_obj.instance_uuid
                and permit['project_id']==spec_obj.project_id
                and permit['host']==host_state.host==destination.host
                and permit['runtime_gate']=='PASS'
                and host_state.service.get('disabled')
                and not host_state.service.get('forced_down',False)
                and self.servicegroup_api.service_is_up(host_state.service))
        except Exception:return False

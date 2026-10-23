"""Sidecar driver for Nova 33: upstream driver plus centrally enforced XML policy."""
import importlib.util
from nova import exception
from nova.virt.libvirt import driver

_spec=importlib.util.spec_from_file_location('uccloud_hypervisor_policy','/usr/local/lib/uccloud/hypervisor_xml_policy.py')
_policy=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)

class SecurityLibvirtDriver(driver.LibvirtDriver):
    def _get_guest_xml(self,*args,**kwargs):
        xml=super()._get_guest_xml(*args,**kwargs)
        try:
            xml=_policy.enforce(xml)
            _policy.validate(xml)
            return xml
        except (ValueError,OSError,KeyError) as exc:
            raise exception.InvalidInput(reason=str(exc)) from None


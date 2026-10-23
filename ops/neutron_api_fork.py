"""Use Neutron's legacy process context before the WSGI app creates workers."""
import multiprocessing
multiprocessing.set_start_method('fork',force=True)
from neutron.wsgi.api import application  # noqa: E402,F401

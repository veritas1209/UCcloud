"""Read-only real DB regression in a separate process and a two-slot pool."""
import gc
import json
import sys
import time
from sqlalchemy import event
from keystone import server
from keystone.common import sql
from keystone.common.sql import core as sql_core
from keystone.federation.backends import sql as federation
from keystone.identity.backends import sql_model
from keystone.conf import CONF

mode = sys.argv.pop(1)
server.configure(config_files=['/etc/keystone/keystone.conf'])
for key, value in [('max_pool_size', 2), ('max_overflow', 0), ('pool_timeout', 0.2)]:
    CONF.set_override(key, value, group='database')
driver = federation.Federation()
if mode == 'fixed':
    def materialized(self):
        with sql.session_for_read() as session:
            return session.query(federation.ServiceProviderModel).filter_by(enabled=True).all()
    driver.get_enabled_service_providers = materialized.__get__(driver)
engine = sql_core._get_main_context_manager().reader.get_engine()
counters = {'checkout': 0, 'checkin': 0}
for name in counters:
    def record(*args, key=name):
        counters[key] += 1
    event.listen(engine, name, record)
result = {'mode': mode, 'database': 'live MariaDB, SELECT only',
          'completed': 0, 'error': None, 'samples': []}
for i in range(200):
    try:
        list(driver.get_enabled_service_providers())
        result['completed'] += 1
        if i < 4 or i % 50 == 49:
            result['samples'].append(engine.pool.checkedout())
    except Exception as exc:
        result['error'] = type(exc).__name__
        break
    time.sleep(0.01)
result.update(counters)
result['checkedout_before_gc'] = engine.pool.checkedout()
gc.collect()
result['checkedout_after_gc'] = engine.pool.checkedout()
engine.dispose()
print('UCCLOUD_RESULT ' + json.dumps(result))

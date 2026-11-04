"""Loopback-only full Keystone canary; no live WSGI source changes."""
import json
import os
import time
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.pool import Pool
from keystone.common import sql
from keystone.federation.backends import sql as federation
from keystone.server.flask import core
from keystone.conf import CONF

metrics = {'checkout': 0, 'checkin': 0, 'requests': 0, 'http_errors': 0,
           'max_outstanding': 0, 'materialized_calls': 0}

def record_checkout(*args):
    metrics['checkout'] += 1
    metrics['max_outstanding'] = max(metrics['max_outstanding'], metrics['checkout'] - metrics['checkin'])

def record_checkin(*args):
    metrics['checkin'] += 1

event.listen(Pool, 'checkout', record_checkout)
event.listen(Pool, 'checkin', record_checkin)

def materialized(self):
    with sql.session_for_read() as session:
        result = session.query(federation.ServiceProviderModel).filter_by(enabled=True).all()
        metrics['materialized_calls'] += 1
        return result

federation.Federation.get_enabled_service_providers = materialized

def overrides():
    for key, value in [('max_pool_size', 2), ('max_overflow', 0), ('pool_timeout', 1)]:
        CONF.set_override(key, value, group='database')
    CONF.set_override('enabled', False, group='cache')
    CONF.set_override('debug', False)

app = core.initialize_application('public', config_files=['/etc/keystone/keystone.conf'],
                                  post_log_configured_function=overrides)

def application(environ, start_response):
    def start(status, headers, exc_info=None):
        if int(status.split()[0]) >= 500:
            metrics['http_errors'] += 1
        return start_response(status, headers, exc_info)
    iterable = None
    try:
        iterable = app(environ, start)
        yield from iterable
    finally:
        if iterable is not None and hasattr(iterable, 'close'):
            iterable.close()
        metrics['requests'] += 1
        metrics['outstanding'] = metrics['checkout'] - metrics['checkin']
        metrics['pid'] = os.getpid()
        metrics['updated'] = time.time()
        target = Path('/var/lib/keystone/uccloud-pool-canary.json')
        temporary = target.with_suffix('.next')
        temporary.write_text(json.dumps(metrics))
        temporary.chmod(0o600)
        temporary.replace(target)

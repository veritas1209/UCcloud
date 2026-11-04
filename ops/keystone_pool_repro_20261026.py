"""Isolated SQLAlchemy/Keystone lifetime regression; never loads live config."""
import contextlib
import concurrent.futures
import gc
import inspect
import json
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool
from keystone.common import sql
from keystone.federation.backends import sql as federation
from keystone.identity.backends import sql_model  # register related ORM models


def main():
    fixed = sys.argv[1] == 'fixed'
    installed = sys.argv[1] == 'installed'
    with tempfile.TemporaryDirectory(prefix='uccloud-keystone-isolated-') as folder:
        engine = create_engine('sqlite:///' + folder + '/test.db',
                               poolclass=QueuePool, pool_size=2, max_overflow=0,
                               pool_timeout=0.1)
        federation.ServiceProviderModel.__table__.create(engine)
        counters = {'checkout': 0, 'checkin': 0}
        for name in counters:
            def record(*args, key=name):
                counters[key] += 1
            event.listen(engine, name, record)

        @contextlib.contextmanager
        def reader():
            session = Session(engine)
            try:
                yield session
            finally:
                session.close()

        sql.session_for_read = reader
        driver = federation.Federation()
        if fixed:
            def materialized(self):
                with sql.session_for_read() as session:
                    return session.query(federation.ServiceProviderModel).filter_by(enabled=True).all()
            driver.get_enabled_service_providers = materialized.__get__(driver)
        result = {'mode': sys.argv[1], 'database': 'isolated SQLite',
                  'completed': 0, 'error': None, 'samples': []}
        for i in range(200):
            try:
                assert list(driver.get_enabled_service_providers()) == []
                result['completed'] += 1
                if i < 4 or i % 50 == 49:
                    result['samples'].append(engine.pool.checkedout())
            except Exception as exc:
                result['error'] = type(exc).__name__
                result['error_detail'] = str(exc)[:500]
                break
        result.update(counters)
        result['checkedout_before_gc'] = engine.pool.checkedout()
        gc.collect()
        result['checkedout_after_gc'] = engine.pool.checkedout()
        if fixed or installed:
            # Four contenders intentionally share two slots. Allow ordinary
            # scheduling delay during the extra stress phase, not in the leak reproducer.
            engine.pool._timeout = 2
            with engine.begin() as connection:
                connection.execute(federation.ServiceProviderModel.__table__.insert(), [
                    dict(id='enabled-test', enabled=True, auth_url='https://example.invalid/auth',
                         sp_url='https://example.invalid/sp', relay_state_prefix='test', description='enabled'),
                    dict(id='disabled-test', enabled=False, auth_url='https://example.invalid/auth',
                         sp_url='https://example.invalid/sp', relay_state_prefix='test', description='disabled')])
            def payload_check(index):
                rows = driver.get_enabled_service_providers()
                assert isinstance(rows, list)
                assert [{'id': row.id, 'auth_url': row.auth_url, 'sp_url': row.sp_url} for row in rows] == [
                    {'id': 'enabled-test', 'auth_url': 'https://example.invalid/auth', 'sp_url': 'https://example.invalid/sp'}]
            for i in range(100):
                payload_check(i)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(payload_check, range(400)))
            assert engine.pool.checkedout() == 0
            federation.ServiceProviderModel.__table__.drop(engine)
            try:
                driver.get_enabled_service_providers()
            except Exception as exc:
                assert type(exc).__name__ == 'OperationalError'
            else:
                raise AssertionError('Expected isolated SELECT failure')
            assert engine.pool.checkedout() == 0
            result['nonempty_result_checks'] = 100
            result['concurrent_nonempty_checks'] = 400
            result['concurrent_pool_timeout_seconds'] = 2
            result['exception_returns_connection'] = True
            result['checkedout_after_extra_tests'] = engine.pool.checkedout()
        print(json.dumps(result))
        engine.dispose()


if __name__ == '__main__':
    main()

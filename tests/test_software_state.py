"""Completion cache is team-scoped and contains no credentials."""
import json
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as directory:
    os.environ['PORTAL_DB']=str(Path(directory)/'test.db')
    from backend.database import init_db,db
    from backend.software_state import completed,remember,forget
    init_db()
    with db() as conn:
        conn.execute("INSERT INTO teams(id,name,slug) VALUES(1,'a','a'),(2,'b','b')")
    remember(1,'vm',dict(state='installing',message='pending'))
    assert completed(1,'vm') is None
    remember(1,'vm',dict(state='ready',message='완료',updated_at=1780000125,password='secret',key='secret'))
    assert completed(1,'vm')==dict(state='ready',message='완료',updated_at=1780000125)
    assert completed(2,'vm') is None
    with db() as conn:
        assert 'secret' not in conn.execute('SELECT payload FROM instance_software_status').fetchone()['payload']
    forget(1,'vm')
    assert completed(1,'vm') is None
print('Confirmed-only completion cache, team isolation, secret exclusion and invalidation passed')

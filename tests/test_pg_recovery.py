"""Complete encrypted pg_dump/pg_restore drill in databases created only by this test."""
import json
import os
from pathlib import Path
import shutil
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from psycopg import connect, sql
from psycopg.conninfo import make_conninfo

from ops.pg_backup import backup, restore
from tests.test_recovery import seed, run_phase


def test_complete_postgres_backup_restores_access_cases_and_checkpoint(tmp_path,monkeypatch):
    dsn=os.getenv('ATEZAIN_TEST_DSN')
    if not dsn:
        pytest.skip('ATEZAIN_TEST_DSN is not configured for a disposable PostgreSQL instance')
    tool_dir=Path('/opt/homebrew/opt/libpq/bin')
    if tool_dir.exists():
        monkeypatch.setenv('PATH',str(tool_dir)+os.pathsep+os.environ['PATH'])
    if not shutil.which('pg_dump') or not shutil.which('pg_restore'):
        pytest.skip('PostgreSQL client tools are not installed')
    names=['recovery_'+uuid.uuid4().hex for _ in range(2)];created=[];started=time.monotonic()
    try:
        with connect(dsn,autocommit=True,connect_timeout=10) as conn:
            for name in names:
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)));created.append(name)
        source,target=[make_conninfo(dsn,dbname=name) for name in names]
        # Complete application state in the dedicated source database. No existing test state is dumped.
        from tests.test_recovery import seed as sqlite_seed
        import tests.test_recovery as helpers
        original=helpers.run_phase
        monkeypatch.setattr(helpers,'run_phase',lambda root,script,extra=None:original(root,script,{'ATEZAIN_DSN':source,**(extra or {})}))
        initial=sqlite_seed(tmp_path/'source')
        archive=tmp_path/'pg.enc';key=Fernet.generate_key().decode()
        report=backup(archive,source,key)
        assert initial['token'].encode() not in archive.read_bytes()
        restore(archive,target,key)
        later=original(tmp_path/'restored', '''import json,os
from fastapi.testclient import TestClient
from api import app as a
sid=os.environ['TEST_WORKSPACE'];h={'Authorization':'Bearer '+os.environ['TEST_TOKEN']}
with TestClient(a.app) as c:
 assert c.get(f'/sessions/{sid}/records',headers={'Authorization':'Bearer '+os.environ['TEST_REVOKED']}).status_code==401
 assert c.get(f'/sessions/{sid}/records',headers={'Authorization':'Bearer '+os.environ['TEST_WORKFORCE']}).status_code==200
 from integrations.xero import CredentialStore
 assert CredentialStore(a.sessions.db,os.environ['TEST_CREDENTIAL_KEY']).get(sid,True)['tokens']['refresh_token']=='fixture-refresh'
 assert c.post(f'/sessions/{sid}/assist/F-2026-031',headers=h).json()['cached']
 print(json.dumps(c.get(f'/sessions/{sid}/export',headers=h).json()))''',
            {'ATEZAIN_DSN':target,'TEST_WORKSPACE':initial['sid'],'TEST_TOKEN':initial['token'],'TEST_REVOKED':initial['revoked'],'TEST_CREDENTIAL_KEY':initial['credential_key'],'TEST_WORKFORCE':initial['workforce']})
        for field in ('records','cases','source_snapshots','proposals','audit','identity_events'):
            assert later[field]==initial['export'][field]
        with pytest.raises(ValueError,match='not empty'):
            restore(archive,target,key)
        evidence={'format':'atezain-pg-recovery-v1','date':time.strftime('%Y-%m-%d',time.gmtime()),'passed':True,
            'seconds':round(time.monotonic()-started,3),'backup_bytes':report['bytes'],
            'checks':['records','cases','source snapshots','encrypted connector credentials','workforce membership and login','identity administration events','proposals','anchored audit head','revoked access','checkpoint reuse','existing database refusal'],
            'scope':'isolated synthetic databases on configured test PostgreSQL; no production/customer recovery claim'}
        output=os.getenv('ATEZAIN_RECOVERY_REPORT')
        if output:
            Path(output).write_text(json.dumps(evidence,indent=2)+'\n')
    finally:
        with connect(dsn,autocommit=True,connect_timeout=10) as conn:
            for name in created:
                conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))

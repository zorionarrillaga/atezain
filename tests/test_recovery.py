"""Full local restoration, including access, revocation, approvals and checkpoints."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.fernet import Fernet, InvalidToken
from ops.recovery import backup, restore
from ops.lease import lease

ROOT=Path(__file__).resolve().parents[1]


def run_phase(root,script,extra=None):
    env={k:v for k,v in os.environ.items() if not k.startswith(("ATEZAIN_","LANGFUSE_","LANGCHAIN_","LANGSMITH_")) and k not in {"DATABASE_URL","GROQ_API_KEY"}}
    env.update(ATEZAIN_STATE_DIR=str(root),ATEZAIN_MODE="demo",ATEZAIN_MODEL="stub",PYTHONPATH=str(ROOT))
    env.update(extra or {})
    # The complete remote PostgreSQL fixture needs several network round trips per operation.
    result=subprocess.run([sys.executable,"-c",script],cwd=ROOT,env=env,capture_output=True,text=True,
                          timeout=120 if env.get('ATEZAIN_DSN') else 30)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def seed(root):
    return run_phase(root,'''from fastapi.testclient import TestClient
from api import app as a
import json
with TestClient(a.app) as c:
 s=c.post('/sessions').json();sid=s['session'];h={'Authorization':'Bearer '+s['token']}
 text='id,customer,amount,issued,due\\nF-2026-031,A,10,2026-01-01,2026-02-01\\n'
 assert c.post(f'/sessions/{sid}/upload',headers=h,files={'file':('x.csv',text)}).status_code==200
 assert c.put(f'/sessions/{sid}/cases/F-2026-031',headers=h,json={'version':0,'note':'restore this plan'}).status_code==200
 assert c.post(f'/sessions/{sid}/assist/F-2026-031',headers=h).status_code==200
 grant=c.post(f'/sessions/{sid}/access',headers=h,json={'role':'viewer','label':'Revoked auditor'}).json()
 c.delete(f"/sessions/{sid}/access/{grant['id']}",headers=h)
 import time
 from cryptography.fernet import Fernet
 from integrations.xero import CredentialStore, SCOPES
 credential_key=Fernet.generate_key().decode()
 source_id='33333333-3333-4333-8333-333333333333';contact_id='22222222-2222-4222-8222-222222222222';tenant='11111111-1111-4111-8111-111111111111'
 source={'source_id':source_id,'customer_key':contact_id,'source_number':'RESTORE-1','customer':'Restoration fixture','contact':'fixture@example.test','amount':'75.25','original_amount':'100.00','currency':'EUR','issued':'2026-01-01','due':'2026-02-01','status':'open','source_status':'AUTHORISED','updated':'2026-09-05T00:00:00+00:00'}
 a.state_of(sid).records.import_accounting(tenant,[source],{contact_id:{'ContactID':contact_id,'Name':source['customer'],'EmailAddress':source['contact']}},time.time(),500,True)
 CredentialStore(a.sessions.db,credential_key).save(sid,tenant,{'access_token':'fixture-access','refresh_token':'fixture-refresh','expires_in':1800,'scope':SCOPES})
 a.sessions.identity.member(sid,'https://id.example/tenant','restored-finance','reviewer',True,'fixture-operator')
 workforce=a.sessions.identity.issue({'iss':'https://id.example/tenant','sub':'restored-finance','exp':time.time()+300})
 export=c.get(f'/sessions/{sid}/export',headers=h).json()
 print(json.dumps({'sid':sid,'token':s['token'],'revoked':grant['token'],'export':export,'credential_key':credential_key,'workforce':workforce}))''')


def test_offline_backup_restores_complete_workflow_and_independent_anchor(tmp_path):
    root=tmp_path/'source';initial=seed(root)
    key=Fernet.generate_key().decode();archive=tmp_path/'backup.enc'
    report=backup(root,archive,key)
    assert initial['token'].encode() not in archive.read_bytes()
    restored=tmp_path/'restored'
    result=restore(archive,restored,key)
    assert result['heads']==report['heads']
    later=run_phase(restored,'''from fastapi.testclient import TestClient
from api import app as a
import json,os
sid=os.environ['TEST_WORKSPACE'];h={'Authorization':'Bearer '+os.environ['TEST_TOKEN']}
with TestClient(a.app) as c:
 assert c.get(f'/sessions/{sid}/records',headers={'Authorization':'Bearer '+os.environ['TEST_REVOKED']}).status_code==401
 assert c.get(f'/sessions/{sid}/records',headers={'Authorization':'Bearer '+os.environ['TEST_WORKFORCE']}).status_code==200
 from integrations.xero import CredentialStore
 assert CredentialStore(a.sessions.db,os.environ['TEST_CREDENTIAL_KEY']).get(sid,True)['tokens']['refresh_token']=='fixture-refresh'
 assert c.post(f'/sessions/{sid}/assist/F-2026-031',headers=h).json()['cached']
 print(json.dumps(c.get(f'/sessions/{sid}/export',headers=h).json()))''',
        {'TEST_WORKSPACE':initial['sid'],'TEST_TOKEN':initial['token'],'TEST_REVOKED':initial['revoked'],'TEST_CREDENTIAL_KEY':initial['credential_key'],'TEST_WORKFORCE':initial['workforce']})
    for field in ('records','proposals','audit','cases','source_snapshots','identity_events'):
        assert later[field]==initial['export'][field]
    assert later['audit']['head_hash']==report['heads'][initial['sid']][1]


def test_live_worker_blocks_offline_backup_and_bad_key_cannot_restore(tmp_path):
    root=tmp_path/'source';seed(root)
    key=Fernet.generate_key().decode();archive=tmp_path/'backup.enc'
    with lease(root):
        with pytest.raises(BlockingIOError):
            backup(root,archive,key)
    assert not archive.exists()
    backup(root,archive,key)
    with pytest.raises(InvalidToken):
        restore(archive,tmp_path/'wrong-key',Fernet.generate_key().decode())
    assert not (tmp_path/'wrong-key').exists()
    with pytest.raises(ValueError):
        restore(archive,root,key)
    damaged=bytearray(archive.read_bytes());damaged[-5]^=1;archive.write_bytes(damaged)
    with pytest.raises(InvalidToken):
        restore(archive,tmp_path/'corrupt',key)

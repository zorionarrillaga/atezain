"""Reproducible synthetic capacity exercise. Local SQLite, stub model, no provider calls.

Writes a machine-readable report; its thresholds are release targets, not hosting promises.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import tempfile
import time


def run():
    for key in list(os.environ):
        if key.startswith(("ATEZAIN_","LANGFUSE_","LANGSMITH_","LANGCHAIN_")) or key in {"DATABASE_URL","GROQ_API_KEY"}:
            os.environ.pop(key,None)
    root=tempfile.mkdtemp(prefix="atezain-workload-")
    os.environ.update(ATEZAIN_STATE_DIR=root,ATEZAIN_MODEL="stub",ATEZAIN_MODE="demo")
    from fastapi.testclient import TestClient
    from api import app as a
    a.config=a.config.replace(daily_writes=300,actions={k:replace(s,approval="required",daily_max=100 if k=="send_reminder" else s.daily_max) for k,s in a.config.actions.items()})
    client=TestClient(a.app)
    s=client.post("/sessions").json();sid=s["session"];owner={"Authorization":"Bearer "+s["token"]}
    from integrations.xero import Xero, XeroConfig, CredentialStore, SCOPES
    from cryptography.fernet import Fernet
    from uuid import UUID
    tenant=str(UUID(int=1));contacts={};sources=[]
    for n in range(500):
        contact_id=str(UUID(int=n+1000))
        contacts[contact_id]={"ContactID":contact_id,"Name":f"Customer {n}","EmailAddress":f"ar{n}@example.test"}
        sources.append({"source_id":str(UUID(int=n+2000)),"customer_key":contact_id,"source_number":f"TEST-{n}",
            "customer":f"Customer {n}","contact":f"ar{n}@example.test","amount":"100.25","original_amount":"100.25",
            "currency":"EUR","issued":"2026-01-01","due":"2026-02-01","status":"open","source_status":"AUTHORISED","updated":"2026-09-05T00:00:00+00:00"})
    by_source={row['source_id']:row for row in sources}
    class FixtureXero(Xero):
        def fetch_one(self,workspace,source_id):
            assert workspace==sid
            source=by_source[source_id]
            return source,contacts[source['customer_key']]
    key=Fernet.generate_key().decode()
    a.xero=FixtureXero(XeroConfig('fixture','fixture','https://app.example/callback',key),CredentialStore(a.sessions.db,key))
    a.xero.store.save(sid,tenant,{'access_token':'fixture','refresh_token':'fixture','expires_in':3600,'scope':SCOPES})
    a.state_of(sid).records.import_accounting(tenant,sources,contacts,time.time(),500,full=True)
    reviewers=[]
    for n in range(5):
        subject=f"synthetic-reviewer-{n}"
        a.sessions.identity.member(sid,"https://synthetic-idp.example",subject,"reviewer",True,"test-operator")
        token=a.sessions.identity.issue({"iss":"https://synthetic-idp.example","sub":subject,"exp":time.time()+300})
        reviewers.append({"Authorization":"Bearer "+token})
    a.settings=replace(a.settings,identity_required=True)
    a.sessions.oidc_only=True
    durations=[];busy=[];started=time.monotonic()
    def request(method,path,headers,**kwargs):
        at=time.monotonic()
        for attempt in range(300):
            result=client.request(method,path,headers=headers,**kwargs)
            if result.status_code!=409 or "busy" not in result.text:
                break
            busy.append(1);time.sleep(max(float(result.headers.get("Retry-After", "1")),.01))
        assert result.status_code==200,(result.status_code,result.text)
        durations.append(time.monotonic()-at)
        return result.json()
    def worker(n):
        h=reviewers[n]
        for case in range(n*10,(n+1)*10):
            rid=f"F-2026-{case:03d}"
            response=request("POST",f"/sessions/{sid}/assist/{rid}",h)
            for proposal in response["proposals"]:
                assert request("POST",f"/sessions/{sid}/proposals/{proposal['id']}/decide",h,json={"approve":True})["executed"]
            request("PUT",f"/sessions/{sid}/cases/{rid}",h,json={"version":0,"assignee":f"synthetic-reviewer-{n}","next_action":"2026-09-06","note":"Synthetic workload review"})
            request("GET",f"/sessions/{sid}/worklist",h)
    with ThreadPoolExecutor(5) as pool:
        list(pool.map(worker,range(5)))
    elapsed=time.monotonic()-started
    export=request("GET",f"/sessions/{sid}/export",reviewers[0])
    assert len(export["records"])==500 and len(export["cases"])==50
    assert export["audit"]["verifies"] and export["audit"]["anomalies"]==[]
    assert len({p['id'] for p in export['proposals']})==len(export['proposals'])
    p95=sorted(durations)[int(len(durations)*.95)]
    report={"format":"atezain-workload-v1","at":time.time(),"python":platform.python_version(),"platform":platform.platform(),
        "storage":"sqlite","http":"in-process ASGI TestClient","model":"stub","identity":"synthetic verified-subject fixture; not a live IdP",
        "accounting":"synthetic normalized Xero snapshots with per-approval source reconciliation; no live network",
        "records":500,"concurrent_reviewers":5,"assisted_cases":50,"completed_cases":len(export["cases"]),
        "proposals":len(export["proposals"]),"seconds":round(elapsed,3),"request_p95_seconds":round(p95,3),
        "busy_retries":len(busy),"audit_verifies":True,"audit_anomalies":[],"target_p95_seconds":2,"passed":p95<2}
    for st in a._live.values():st.close()
    client.close()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",required=True);args=parser.parse_args()
    report=run();Path(args.output).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report));return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())

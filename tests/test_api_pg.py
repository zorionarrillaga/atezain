"""The deployed shape, for real: two processes, one database, nothing on the local disk.

This is the test the records port exists for. A Render free instance has no persistent disk and
spins down when idle; the question is whether a visitor who comes back to their own link finds
their invoices, their queue, their audit chain and their held graph still there. So phase 1 runs
the whole flow in one process, phase 2 runs in a SECOND process with a DIFFERENT state directory —
the spin-down — and reads it all back with the same token.

Skipped unless `ATEZAIN_TEST_DSN` names a Postgres. The app is driven in subprocesses because
`api/app.py` reads its configuration at import, and this module's sibling `tests/test_api.py`
has already imported it with SQLite.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("ATEZAIN_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ATEZAIN_TEST_DSN is not set (PLAN.md §4.1)")

SAMPLE = ("id,customer,amount,currency,issued,due,status,note\n"
          "F-2026-031,Talleres Aranburu S.L.,1840.50,EUR,2026-06-30,2026-07-30,open,Llamé al cliente\n")

PHASE1 = '''
import json, os, sys
os.environ["ATEZAIN_DSN"], os.environ["ATEZAIN_STATE_DIR"] = sys.argv[1], sys.argv[2]
sys.path.insert(0, sys.argv[3])
from fastapi.testclient import TestClient
from api.app import app
c = TestClient(app)
s = c.post("/sessions").json()
sid, tok = s["session"], s["token"]
H = {"Authorization": "Bearer " + tok}
up = c.post("/sessions/%s/upload" % sid, files={"file": ("i.csv", sys.argv[4], "text/csv")}, headers=H).json()
a = c.post("/sessions/%s/assist/F-2026-031" % sid, headers=H).json()
held = [p for p in a["proposals"] if p["status"] == "held"]
d = c.post("/sessions/%s/proposals/%s/decide" % (sid, held[0]["id"]), json={"approve": True}, headers=H).json()
au = c.get("/sessions/%s/audit" % sid, headers=H).json()
print(json.dumps({"sid": sid, "token": tok, "loaded": up["loaded"], "summary": a["summary"],
                  "executed": d["executed"], "head": au["head_seq"], "verifies": au["verifies"],
                  "backing": c.get("/healthz").json()["backing"]}))
'''

PHASE2 = '''
import json, os, sys
os.environ["ATEZAIN_DSN"], os.environ["ATEZAIN_STATE_DIR"] = sys.argv[1], sys.argv[2]
sys.path.insert(0, sys.argv[3])
from fastapi.testclient import TestClient
from api.app import app
c = TestClient(app)
sid, tok = sys.argv[4], sys.argv[5]
H = {"Authorization": "Bearer " + tok}
q = c.get("/sessions/%s/proposals" % sid, headers=H)
au = c.get("/sessions/%s/audit" % sid, headers=H).json()
again = c.post("/sessions/%s/assist/F-2026-031" % sid, headers=H).json()
print(json.dumps({"status": q.status_code, "queue": [p["status"] for p in q.json()],
                  "head": au["head_seq"], "verifies": au["verifies"], "anomalies": au["anomalies"],
                  "cached": again["cached"], "summary": again["summary"]}))
'''


def run(script, *args):
    out = subprocess.run([sys.executable, "-c", script, DSN, *args], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_session_survives_the_process_that_made_it():
    one, two = tempfile.mkdtemp(prefix="atezain-pg1-"), tempfile.mkdtemp(prefix="atezain-pg2-")
    a = run(PHASE1, one, str(ROOT), SAMPLE)
    try:
        assert a["backing"] == "postgres" and a["loaded"] == 1
        assert a["executed"] is True and a["verifies"] is True and a["head"] == 4

        # a different process, and a different state directory: nothing local carries over
        b = run(PHASE2, two, str(ROOT), a["sid"], a["token"])
        assert b["status"] == 200, "the token no longer opens the session it was issued for"
        assert b["queue"] == ["executed"], "the queue did not survive the restart"
        assert b["head"] == a["head"] and b["verifies"] is True and b["anomalies"] == []
        assert b["cached"] is True, "the held graph did not survive: the model would be asked twice"
        assert b["summary"] == a["summary"]
    finally:
        from records.store_pg import PgRecords
        from api.app import schema_of
        r = PgRecords(DSN, schema=schema_of(a["sid"]))
        r.drop_schema()
        r.close()
        import psycopg
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM sessions WHERE id = %s", (a["sid"],))

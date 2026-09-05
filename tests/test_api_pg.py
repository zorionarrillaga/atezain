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
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("ATEZAIN_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ATEZAIN_TEST_DSN is not set (PLAN.md §4.1)")

SAMPLE = ("id,customer,amount,currency,issued,due,status,note\n"
          "F-2026-031,Talleres Aranburu S.L.,1840.50,EUR,2026-06-30,2026-07-30,open,Llamé al cliente\n")

PHASE1 = '''
import json, os, sys
args = json.loads(os.environ["ATEZAIN_TEST_PHASE_ARGS"])
os.environ["ATEZAIN_STATE_DIR"] = args[0]
sys.path.insert(0, args[1])
from fastapi.testclient import TestClient
from api.app import app
c = TestClient(app)
s = c.post("/sessions").json()
sid, tok = s["session"], s["token"]
H = {"Authorization": "Bearer " + tok}
up = c.post("/sessions/%s/upload" % sid, files={"file": ("i.csv", args[2], "text/csv")}, headers=H).json()
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
args = json.loads(os.environ["ATEZAIN_TEST_PHASE_ARGS"])
os.environ["ATEZAIN_STATE_DIR"] = args[0]
sys.path.insert(0, args[1])
from fastapi.testclient import TestClient
from api.app import app
c = TestClient(app)
sid, tok = args[2], args[3]
H = {"Authorization": "Bearer " + tok}
q = c.get("/sessions/%s/proposals" % sid, headers=H)
au = c.get("/sessions/%s/audit" % sid, headers=H).json()
again = c.post("/sessions/%s/assist/F-2026-031" % sid, headers=H).json()
from api.app import limits, sessions, schema_of, _live
assert limits.model_calls == 1, "the model counter reset or a cached answer spent another call"
with sessions.lock(sid):
    assert c.get("/sessions/%s/records" % sid, headers=H).status_code == 409, "another connection bypassed the workspace lock"
grant = c.post("/sessions/%s/access" % sid, headers=H, json={"role": "viewer", "label": "Auditor"}).json()
V = {"Authorization": "Bearer " + grant["token"]}
assert c.get("/sessions/%s/export" % sid, headers=V).status_code == 200
assert c.post("/sessions/%s/assist/F-2026-031" % sid, headers=V).status_code == 403
assert c.delete("/sessions/%s/access/%s" % (sid, grant["id"]), headers=H).status_code == 200
assert c.get("/sessions/%s/records" % sid, headers=V).status_code == 401
rotated = c.post("/sessions/%s/token/rotate" % sid, headers=H).json()["token"]
assert c.get("/sessions/%s/records" % sid, headers=H).status_code == 401
H = {"Authorization": "Bearer " + rotated, "X-Confirm-Delete": sid}
checkpoint_conn = _live[sid].checkpointer.conn
assert c.delete("/sessions/%s" % sid, headers=H).json()["deleted"]
assert checkpoint_conn.closed and not sessions.exists(sid)
assert c.get("/sessions/%s/records" % sid, headers=H).status_code == 401
import psycopg
with psycopg.connect(os.environ["ATEZAIN_DSN"], autocommit=True) as conn:
    assert conn.execute("SELECT to_regnamespace(%s)", (schema_of(sid),)).fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id LIKE %s", (sid + ":%",)).fetchone()[0] == 0
print(json.dumps({"status": q.status_code, "queue": [p["status"] for p in q.json()],
                  "head": au["head_seq"], "verifies": au["verifies"], "anomalies": au["anomalies"],
                  "cached": again["cached"], "summary": again["summary"]}))
'''


def run(script, *args, dsn=DSN):
    env = {**os.environ, "ATEZAIN_DSN": dsn, "ATEZAIN_TEST_PHASE_ARGS": json.dumps(args), "ATEZAIN_MODEL": "stub", "ATEZAIN_MODE": "demo"}
    bounded = "import faulthandler; faulthandler.dump_traceback_later(60, exit=True)\n" + script
    out = subprocess.run([sys.executable, "-c", bounded], env=env, capture_output=True, text=True, cwd=ROOT, timeout=90)
    assert out.returncode == 0, out.stderr[:6000]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_session_survives_the_process_that_made_it():
    import psycopg
    from psycopg.conninfo import make_conninfo
    control = "test_control_" + uuid.uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{control}"')
    isolated_dsn = make_conninfo(DSN, options=f"-c search_path={control}")
    one, two = tempfile.mkdtemp(prefix="atezain-pg1-"), tempfile.mkdtemp(prefix="atezain-pg2-")
    a = None
    try:
        a = run(PHASE1, one, str(ROOT), SAMPLE, dsn=isolated_dsn)
        assert a["backing"] == "postgres" and a["loaded"] == 1
        assert a["executed"] is True and a["verifies"] is True and a["head"] == 4

        # a different process, and a different state directory: nothing local carries over
        b = run(PHASE2, two, str(ROOT), a["sid"], a["token"], dsn=isolated_dsn)
        assert b["status"] == 200, "the token no longer opens the session it was issued for"
        assert b["queue"] == ["executed"], "the queue did not survive the restart"
        assert b["head"] == a["head"] and b["verifies"] is True and b["anomalies"] == []
        assert b["cached"] is True, "the held graph did not survive: the model would be asked twice"
        assert b["summary"] == a["summary"]
    finally:
        from psycopg import sql
        from api.app import schema_of
        # Recover owned IDs from this test's control table even if a child dies before printing.
        with psycopg.connect(DSN, autocommit=True) as conn:
            if conn.execute("SELECT to_regclass(%s)", (control + ".sessions",)).fetchone()[0]:
                ids = conn.execute(sql.SQL("SELECT id FROM {}.sessions").format(sql.Identifier(control))).fetchall()
                for (sid,) in ids:
                    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_of(sid))))
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(control)))

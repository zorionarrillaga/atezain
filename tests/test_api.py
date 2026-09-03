"""Step 4.3 (PLAN.md §4.6): the deployed face, locally — SQLite, the stub model, one process.

What these have to establish is that putting the layer behind HTTP did not add a way around it: a
session sees only its own records, a token is the only thing that mints a human, an id the policy
could never act on is refused at the door, a held proposal writes nothing until someone decides,
and the chain still verifies afterwards.
"""
import os
import tempfile

import pytest

STATE = tempfile.mkdtemp(prefix="atezain-api-")
os.environ["ATEZAIN_STATE_DIR"] = STATE                      # read at import; must be set first

from fastapi.testclient import TestClient                    # noqa: E402

from api import app as apimod                                # noqa: E402
from api.app import app                                      # noqa: E402

SAMPLE = ("id,customer,amount,currency,issued,due,status,note\n"
          "F-2026-031,Talleres Aranburu S.L.,1840.50,EUR,2026-06-30,2026-07-30,open,Llamé al cliente\n")


@pytest.fixture
def client():
    return TestClient(app)


def session(client):
    s = client.post("/sessions").json()
    return s["session"], {"Authorization": f"Bearer {s['token']}"}


def upload(client, sid, headers, text=SAMPLE, name="invoices.csv"):
    return client.post(f"/sessions/{sid}/upload", files={"file": (name, text, "text/csv")}, headers=headers)


def assist(client, sid, headers, inv="F-2026-031"):
    return client.post(f"/sessions/{sid}/assist/{inv}", headers=headers)


# ── sessions and identity ────────────────────────────────────────────────────────────────────
def test_a_token_is_shown_once_and_is_the_only_thing_that_opens_a_session(client):
    s = client.post("/sessions").json()
    assert s["token"] and s["session"] and s["adapter"]
    good = {"Authorization": f"Bearer {s['token']}"}
    assert client.get(f"/sessions/{s['session']}/proposals", headers=good).status_code == 200
    assert client.get(f"/sessions/{s['session']}/proposals").status_code == 401
    assert client.get(f"/sessions/{s['session']}/proposals",
                      headers={"Authorization": "Bearer not-the-token"}).status_code == 401
    # the token itself is never returned again by any route
    body = client.get(f"/sessions/{s['session']}/audit", headers=good).text
    assert s["token"] not in body


def test_only_auth_mints_humans(client):
    """PLAN.md §4.3: the ONLY place a HUMAN principal is constructed is `api/auth.py`. This is the
    trust boundary of README item 1 written as code; a second mint anywhere in `api/` fails here."""
    from pathlib import Path
    api_dir = Path(apimod.__file__).resolve().parent
    for path in api_dir.glob("*.py"):
        if path.name == "auth.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "HUMAN" not in text, f"{path.name} names HUMAN; only auth.py may mint one"
        assert "Principal(" not in text, f"{path.name} constructs a Principal"
    auth = (api_dir / "auth.py").read_text(encoding="utf-8")
    assert auth.count("Principal(") == 2, "auth.py should build exactly the agent constant and the session's human"
    assert "AGENT_PRINCIPAL = Principal(" in auth


def test_one_session_cannot_see_another(client):
    a, ha = session(client)
    b, hb = session(client)
    assert upload(client, a, ha).json()["loaded"] == 1
    assert assist(client, a, ha).status_code == 200
    assert client.get(f"/sessions/{a}/proposals", headers=hb).status_code == 401   # wrong token
    assert assist(client, b, hb).status_code == 404                               # its own records are empty
    pid = client.get(f"/sessions/{a}/proposals", headers=ha).json()[0]["id"]
    assert client.post(f"/sessions/{b}/proposals/{pid}/decide", json={"approve": True}, headers=hb).status_code == 404


# ── upload ───────────────────────────────────────────────────────────────────────────────────
def test_an_id_the_policy_could_never_act_on_is_refused_at_the_door(client):
    sid, h = session(client)
    out = upload(client, sid, h, SAMPLE + "INV-1,Otro,10,EUR,2026-01-01,2026-02-01,open,\n").json()
    assert out["loaded"] == 1 and out["ids"] == ["F-2026-031"]
    assert [(r["id"], "must match" in r["why"]) for r in out["rejected"]] == [("INV-1", True)]


def test_upload_is_bounded_and_says_what_it_could_not_read(client):
    sid, h = session(client)
    assert upload(client, sid, h, "id,customer,amount,issued,due\n" + "".join(
        f"F-2026-{i:03d},X,1,2026-01-01,2026-02-01\n" for i in range(501))).status_code == 413
    big = "id,customer,amount,issued,due\n" + "F-2026-001,X,1,2026-01-01,2026-02-01\n" * 40000
    assert len(big.encode()) > 1024 * 1024
    assert upload(client, sid, h, big).status_code == 413
    r = client.post(f"/sessions/{sid}/upload", files={"file": ("x.csv", b"\xff\xfe\x00bad", "text/csv")}, headers=h)
    assert r.status_code == 415
    assert upload(client, sid, h, "id,customer,amount,issued,due\nF-2026-001,X,not-a-number,2026-01-01,2026-02-01\n"
                  ).json()["rejected"][0]["why"] == "amount is not a number"


# ── assist, decide, execute ──────────────────────────────────────────────────────────────────
def test_the_assistant_answers_and_holds_and_nothing_is_written_until_a_human_decides(client):
    sid, h = session(client)
    upload(client, sid, h)
    a = assist(client, sid, h).json()
    assert a["summary"] and a["draft"] and a["cached"] is False
    held = [p for p in a["proposals"] if p["status"] == "held"]
    assert held and all(p["decided_by"] is None for p in held)

    d = client.post(f"/sessions/{sid}/proposals/{held[0]['id']}/decide",
                    json={"approve": True, "note": "sí"}, headers=h).json()
    assert d["executed"] is True and d["proposal"]["status"] == "executed"
    assert d["applied"] == {"status": "reminded"}
    assert d["proposal"]["decided_by"].startswith("human:")

    au = client.get(f"/sessions/{sid}/audit", headers=h).json()
    assert au["verifies"] is True and au["anomalies"] == []
    assert [r["kind"] for r in au["rows"]] == ["PROPOSAL", "DECISION", "EXECUTION_ATTEMPTED", "EXECUTED"]
    assert au["head_seq"] == 4


def test_a_rejection_executes_nothing(client):
    sid, h = session(client)
    upload(client, sid, h)
    held = [p for p in assist(client, sid, h).json()["proposals"] if p["status"] == "held"]
    d = client.post(f"/sessions/{sid}/proposals/{held[0]['id']}/decide",
                    json={"approve": False, "note": "no"}, headers=h).json()
    assert d["executed"] is False and d["proposal"]["status"] == "rejected" and d["applied"] is None


def test_a_second_assist_does_not_ask_the_model_again(client, monkeypatch):
    """The checkpointer earns its place here (PLAN.md §4.2): the thread that already stopped at the
    hold is reused, so a visitor clicking twice does not spend a second model call and does not put
    a second copy of the same proposals in front of themselves."""
    calls = []
    real = apimod.model_for

    def counting(byok):
        llm, name, b = real(byok)
        inner = llm.complete

        def complete(system, user):
            calls.append(1)
            return inner(system, user)
        llm.complete = complete
        return llm, name, b

    monkeypatch.setattr(apimod, "model_for", counting)
    sid, h = session(client)
    upload(client, sid, h)
    first = assist(client, sid, h).json()
    second = assist(client, sid, h).json()
    assert len(calls) == 1
    assert second["cached"] is True and second["summary"] == first["summary"]
    assert len(second["proposals"]) == len(first["proposals"])


# ── the fuses ────────────────────────────────────────────────────────────────────────────────
def test_a_sessions_fuse_does_not_clear_on_the_day_it_tripped(client):
    sid, h = session(client)
    st = apimod.state_of(sid)
    st.policy.fuse.trip("test", apimod.AGENT_PRINCIPAL)
    out = client.post(f"/sessions/{sid}/fuse/clear", headers=h).json()
    assert out["cleared"] is False and out["fuse"]["tripped"] and "day after" in out["why"]
    upload(client, sid, h)
    assert [p["status"] for p in assist(client, sid, h).json()["proposals"]] == ["denied"]


def test_the_server_fuse_is_the_owners_and_the_visitor_rate_limit_is_per_ip(client):
    assert client.post("/server/fuse/clear").status_code == 403
    assert client.post("/server/fuse/clear", headers={"X-Owner-Token": "guess"}).status_code == 403

    sid, h = session(client)
    upload(client, sid, h)
    before = apimod.limits.per_minute
    apimod.limits.per_minute = 1
    apimod.limits.hits.clear()
    try:
        assert assist(client, sid, h).status_code in (200, 429)
        r = assist(client, sid, h)
        assert r.status_code == 429 and r.json()["detail"] == "rate_limited_minute"
    finally:
        apimod.limits.per_minute = before
        apimod.limits.hits.clear()


# ── the page and the probe ───────────────────────────────────────────────────────────────────
def test_the_demo_page_and_the_health_check_answer_without_a_token(client):
    page = client.get("/demo")
    assert page.status_code == 200 and "atezain" in page.text and "<script>" in page.text
    h = client.get("/healthz").json()
    assert h["store"] is True and h["adapter"] == "invoices-es" and "model" in h


# ── the step-6 seat (2026-09-03) ─────────────────────────────────────────────────────────────
class _NoteAndHold:
    """A model output with a write that needs no human beside one that does.

    This one puts the HELD proposal FIRST — the minority order, 32 of the hundred cached outputs the
    step-6 seat measured, and the half a stub pinned to `add_note`-first cannot see. `test_agent.py`
    parametrises over both orders; between the two files the served path is held to the whole corpus
    instead of to its majority shape (round-2 seat, 2026-09-03)."""

    def complete(self, system: str, user: str) -> str:
        import json
        return json.dumps({"summary": "s", "recommendation": "r", "draft": "d", "proposals": [
            {"action": "update_status", "params": {"status": "reminded"}, "why": "w"},
            {"action": "add_note", "params": {"note": "nota del asistente"}, "why": "w"}]})


def test_the_served_path_writes_an_auto_approved_note_even_when_a_sibling_is_held(client, monkeypatch):
    """The seat's decisive finding, through the API: the note used to sit `approved` and never
    written, because `decide` executes only the decided proposal and nothing resumes the graph."""
    monkeypatch.setattr(apimod, "model_for", lambda byok: (_NoteAndHold(), "stub", False))
    sid, h = session(client)
    assert upload(client, sid, h).status_code == 200
    a = assist(client, sid, h).json()
    assert {p["action"]: p["status"] for p in a["proposals"]} == {"add_note": "executed", "update_status": "held"}
    st = apimod.state_of(sid)
    assert st.records.invoice("F-2026-031")["notes"][-1]["text"] == "nota del asistente"
    held = next(p for p in a["proposals"] if p["status"] == "held")
    d = client.post(f"/sessions/{sid}/proposals/{held['id']}/decide", json={"approve": True, "note": ""}, headers=h).json()
    assert d["executed"] is True
    q = {p["action"]: p["status"] for p in client.get(f"/sessions/{sid}/proposals", headers=h).json()}
    assert q == {"add_note": "executed", "update_status": "executed"}
    rows = client.get(f"/sessions/{sid}/audit", headers=h).json()["rows"]
    assert sum(1 for r in rows if r["kind"] == "EXECUTED") == 2
    assert assist(client, sid, h).json()["cached"] is True
    assert sum(1 for n in st.records.invoice("F-2026-031")["notes"] if n["text"] == "nota del asistente") == 1


def test_one_sessions_token_opens_no_other_session(client):
    """Owed by the seat: isolation had been shown with wrong or absent tokens only. A valid token
    of one session is a wrong token on every route of another."""
    a_sid, a_h = session(client)
    b_sid, b_h = session(client)
    assert upload(client, b_sid, b_h).status_code == 200
    assert client.get(f"/sessions/{b_sid}/proposals", headers=a_h).status_code == 401
    assert client.get(f"/sessions/{b_sid}/audit", headers=a_h).status_code == 401
    assert client.post(f"/sessions/{b_sid}/assist/F-2026-031", headers=a_h).status_code == 401
    assert client.post(f"/sessions/{b_sid}/proposals/x/decide", json={"approve": True, "note": ""}, headers=a_h).status_code == 401
    assert client.post(f"/sessions/{b_sid}/fuse/clear", headers=a_h).status_code == 401
    assert upload(client, b_sid, a_h).status_code == 401

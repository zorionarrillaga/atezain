"""Hostile imports, access lifecycle, recovery and resource bounds."""
import io
import json
import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from tests.test_api import client, session, upload, assist, SAMPLE, apimod
from api.limits import PersistentLimits
from api.settings import Settings
from agent.graph import parse_model_output
from agent.llm import ModelFailure


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e309", "1e3", "1.23.456,78", "12,34.56", "1.2345", "99999999999999999999999", "", "1 2"])
def test_malformed_money_cannot_create_records(client, value):
    import csv
    sid, h = session(client)
    buffer = io.StringIO(); writer = csv.writer(buffer)
    writer.writerow(["id", "customer", "amount", "issued", "due"])
    writer.writerow(["F-2026-031", "A", value, "2026-01-01", "2026-02-01"])
    response = upload(client, sid, h, buffer.getvalue())
    assert response.status_code == 200
    assert response.json()["loaded"] == 0 and response.json()["rejected"]
    assert client.get(f"/sessions/{sid}/records", headers=h).json() == []


def test_mixed_money_conventions_are_refused(client):
    sid, h = session(client)
    text = ('id,customer,amount,issued,due\n'
            'F-2026-031,A,"1.234,56",2026-01-01,2026-02-01\n'
            'F-2026-032,A,"1,234.56",2026-01-01,2026-02-01\n')
    result = upload(client, sid, h, text).json()
    assert result["loaded"] == 0 and len(result["rejected"]) == 2


@pytest.mark.parametrize("text", ["id,id,amount,issued,due\nA,A,1,2026-01-01,2026-02-01", "id,amount\nA,1", "id,customer,amount,issued,due\nA,B,1,2026-01-01,2026-02-01,extra"])
def test_bad_headers_and_surplus_cells_are_client_errors(client, text):
    sid, h = session(client)
    assert upload(client, sid, h, text).status_code == 422


def test_xlsx_dates_work_and_formulas_are_refused(client, monkeypatch):
    from openpyxl import Workbook
    sid, h = session(client)
    book = Workbook()
    book.active.append(["id", "customer", "amount", "issued", "due"])
    book.active.append(["F-2026-031", "A", 1234.56, datetime.date(2026, 1, 1), datetime.date(2026, 2, 1)])
    buffer = io.BytesIO(); book.save(buffer)
    result = upload(client, sid, h, buffer.getvalue(), name="invoices.xlsx")
    assert result.status_code == 200 and result.json()["loaded"] == 1
    book.active["C2"] = "=1+1"
    buffer = io.BytesIO(); book.save(buffer)
    assert upload(client, sid, h, buffer.getvalue(), name="invoices.xlsx").status_code == 422
    assert upload(client, sid, h, b"not a workbook", name="invoices.xlsx").status_code == 422
    import zipfile
    import openpyxl
    def unsafe_parser(*args, **kwargs):
        pytest.fail("XML entity declaration reached the workbook parser")
    monkeypatch.setattr(openpyxl, "load_workbook", unsafe_parser)
    attack = io.BytesIO()
    with zipfile.ZipFile(attack, "w") as z:
        z.writestr("xl/workbook.xml", '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///unreadable">]><x>&secret;</x>')
    assert upload(client, sid, h, attack.getvalue(), name="attack.xlsx").status_code == 422


def test_compressed_workbook_expansion_is_bounded(client):
    import zipfile
    sid, h = session(client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "A" * 9_000_000)
    assert upload(client, sid, h, buffer.getvalue(), name="invoices.xlsx").status_code == 413


def test_reimports_and_repeated_approvals_do_not_repeat_writes(client):
    sid, h = session(client); upload(client, sid, h)
    pid = assist(client, sid, h).json()["proposals"][0]["id"]
    url = f"/sessions/{sid}/proposals/{pid}/decide"
    client.post(url, headers=h, json={"approve": True})
    before = client.get(f"/sessions/{sid}/records/F-2026-031", headers=h).json()
    again = upload(client, sid, h).json()
    assert again["loaded"] == 0 and again["skipped"] == ["F-2026-031"]
    assert client.get(f"/sessions/{sid}/records/F-2026-031", headers=h).json() == before
    head = client.get(f"/sessions/{sid}/audit", headers=h).json()["head_seq"]
    assert client.post(url, headers=h, json={"approve": True}).json()["executed"] is True
    assert client.get(f"/sessions/{sid}/audit", headers=h).json()["head_seq"] == head


def test_explicit_formats_and_status_mapping(client):
    sid, h = session(client)
    text = 'id,customer,amount,issued,due,status\nF-2026-031,A,"1.234",01/02/2026,03/04/2026,Cobrada\n'
    result = client.post(f"/sessions/{sid}/upload", headers=h, files={"file": ("invoices.csv", text)},
        data={"amount_format": "point", "date_format": "dmy", "status_map": '{"Cobrada":"paid"}'})
    assert result.json()["loaded"] == 1
    record = client.get(f"/sessions/{sid}/records/F-2026-031", headers=h).json()
    assert (record["amount"], record["due"], record["status"]) == (1234, "2026-04-03", "paid")


def test_record_limit_applies_across_uploads(client, monkeypatch):
    sid, h = session(client)
    monkeypatch.setattr(apimod, "settings", replace(apimod.settings, max_session_records=1))
    assert upload(client, sid, h).json()["loaded"] == 1
    other = upload(client, sid, h, SAMPLE.replace("F-2026-031", "F-2026-032")).json()
    assert other["loaded"] == 0 and "limit" in other["rejected"][0]["why"]


def test_roles_are_scoped_attributable_and_revocable(client):
    sid, owner = session(client); upload(client, sid, owner)
    viewer = client.post(f"/sessions/{sid}/access", headers=owner, json={"role": "viewer", "label": "Audit"}).json()
    vh = {"Authorization": "Bearer " + viewer["token"]}
    assert client.get(f"/sessions/{sid}/records", headers=vh).status_code == 200
    assert assist(client, sid, vh).status_code == 403
    assert upload(client, sid, vh).status_code == 403
    assert client.post(f"/sessions/{sid}/access", headers=vh, json={"role": "reviewer", "label": "x"}).status_code == 403
    grant = client.post(f"/sessions/{sid}/access", headers=owner, json={"role": "reviewer", "label": "Finance"}).json()
    rh = {"Authorization": "Bearer " + grant["token"]}
    a = assist(client, sid, rh).json()
    decision = client.post(f"/sessions/{sid}/proposals/{a['proposals'][0]['id']}/decide", headers=rh, json={"approve": True}).json()
    assert decision["executed"] and decision["proposal"]["decided_by"].endswith(":" + grant["id"])
    assert client.post(f"/sessions/{sid}/token/rotate", headers=rh).status_code == 403
    assert client.delete(f"/sessions/{sid}/access/{grant['id']}", headers=owner).json()["revoked"]
    assert client.get(f"/sessions/{sid}/records", headers=rh).status_code == 401
    other, _ = session(client)
    assert client.get(f"/sessions/{other}/records", headers=vh).status_code == 401
    assert grant["token"] not in client.get(f"/sessions/{sid}/export", headers=owner).text


def test_rotation_and_expiry_survive_reopening(client):
    from api.auth import Sessions
    sid, h = session(client)
    new = client.post(f"/sessions/{sid}/token/rotate", headers=h).json()["token"]
    assert client.get(f"/sessions/{sid}/records", headers=h).status_code == 401
    fresh = Sessions(apimod.sessions.path)
    try:
        access = fresh.access(sid, new)
        assert access["role"] == "owner"
        assert fresh.access(sid, new, now=access["expires_at"]) is None
    finally:
        fresh._raw.close()


def test_delete_revokes_every_role_and_removes_state(client):
    sid, h = session(client); upload(client, sid, h); assist(client, sid, h)
    token = client.post(f"/sessions/{sid}/access", headers=h, json={"role": "viewer", "label": "Audit"}).json()["token"]
    assert client.delete(f"/sessions/{sid}", headers=h).status_code == 400
    assert client.delete(f"/sessions/{sid}", headers={**h, "X-Confirm-Delete": sid}).json()["deleted"] is True
    assert not (apimod.STATE / sid).exists() and not apimod.sessions.exists(sid)
    assert client.get(f"/sessions/{sid}/export", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_concurrent_assists_make_one_model_call(client, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    calls = []
    class Slow:
        def complete(self, system, user):
            calls.append(1); started.set(); release.wait(5)
            return apimod.StubLLM().complete(system, user)
    monkeypatch.setattr(apimod, "model_for", lambda byok: (Slow(), "test", False))
    sid, h = session(client); upload(client, sid, h)
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(assist, client, sid, h)
        assert started.wait(3)
        try:
            assert assist(client, sid, h).status_code == 409
        finally:
            release.set()
        assert running.result().status_code == 200
    assert assist(client, sid, h).json()["cached"] and len(calls) == 1


@pytest.mark.parametrize("output", ["no json", "{}", '{"proposals": "yes"}', '{"summary": [], "proposals": []}', '{"proposals": [null]}', '{"proposals": [], "x": NaN}'])
def test_invalid_model_responses_fail_before_proposals(output):
    with pytest.raises(ModelFailure):
        parse_model_output(output)


def test_bad_model_answer_is_retryable(client, monkeypatch):
    class Bad:
        def complete(self, system, user):
            return "provider returned an error page"
    original = apimod.model_for
    monkeypatch.setattr(apimod, "model_for", lambda _: (Bad(), "bad", False))
    sid, h = session(client); upload(client, sid, h)
    assert assist(client, sid, h).status_code == 502
    assert client.get(f"/sessions/{sid}/proposals", headers=h).json() == []
    monkeypatch.setattr(apimod, "model_for", original)
    assert assist(client, sid, h).json()["cached"] is False


def test_shared_budget_cannot_be_raced_or_reset_by_restart(tmp_path):
    path = tmp_path / "limits.db"
    limits = [PersistentLimits(path, model_day=3) for _ in range(8)]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda x: x.allow_model_call(False)[0], limits))
    assert sum(results) == 3
    reopened = PersistentLimits(path, model_day=3)
    assert reopened.model_calls == 3 and reopened.fuse
    assert not reopened.allow_model_call(False)[0] and reopened.allow_model_call(True)[0]
    assert not reopened.clear_fuse("wrong", "correct")


def test_rate_windows_persist_and_expire(tmp_path):
    now = [120.0]
    path = tmp_path / "limits.db"
    a = PersistentLimits(path, per_minute=1, clock=lambda: now[0])
    assert a.allow("peer")[0]
    b = PersistentLimits(path, per_minute=1, clock=lambda: now[0])
    assert not b.allow("peer")[0]
    now[0] += 60
    assert b.allow("peer")[0]


def test_pool_evicts_and_reopens_durable_state(client, monkeypatch):
    monkeypatch.setattr(apimod, "settings", replace(apimod.settings, max_live_sessions=1))
    sid, h = session(client); upload(client, sid, h)
    other, hh = session(client); upload(client, other, hh)
    assert len(apimod._live) == 1 and sid not in apimod._live
    assert client.get(f"/sessions/{sid}/records", headers=h).json()[0]["id"] == "F-2026-031"
    assert len(apimod._live) == 1
    # A checkpoint-storage failure must close resources opened before it failed.
    with monkeypatch.context() as patch:
        closed = []
        original_close = apimod.Records.close
        def closing(records):
            closed.append(records)
            original_close(records)
        def unavailable(*args):
            raise RuntimeError("checkpoint storage unavailable")
        patch.setattr(apimod.Records, "close", closing)
        patch.setattr(apimod, "make_checkpointer", unavailable)
        broken, hb = session(client)
        assert upload(client, broken, hb).status_code == 503
        assert broken not in apimod._live and len(closed) == 1  # partial initialization
    assert upload(client, broken, hb).json()["loaded"] == 1


def test_served_retrieval_excludes_other_customers(client, monkeypatch):
    seen = []
    class Capture:
        def complete(self, system, user):
            seen.append(json.loads(user))
            return apimod.StubLLM().complete(system, user)
    monkeypatch.setattr(apimod, "model_for", lambda _: (Capture(), "test", False))
    sid, h = session(client)
    upload(client, sid, h, SAMPLE + "F-2026-032,Other,10,EUR,2026-01-01,2026-02-01,open,factura pago SECRET_OTHER_CUSTOMER\n")
    assert assist(client, sid, h).status_code == 200
    assert "SECRET_OTHER_CUSTOMER" not in json.dumps(seen)


def test_currency_totals_separate_currencies_and_exclude_paid(client):
    sid, h = session(client)
    text = "id,customer,amount,currency,issued,due,status\n" + "\n".join([
        "F-2026-031,A,10.10,EUR,2020-01-01,2020-02-01,open",
        "F-2026-032,B,20.20,USD,2020-01-01,2020-02-01,open",
        "F-2026-033,B,999,USD,2020-01-01,2020-02-01,paid"])
    upload(client, sid, h, text)
    summary = client.get(f"/sessions/{sid}/summary", headers=h).json()
    assert summary["overdue_by_currency"] == {"EUR": "10.10", "USD": "20.20"} and summary["overdue"] == 2


def test_headers_errors_and_readiness(client, monkeypatch):
    page = client.get("/demo")
    assert "sha256-" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-store" and page.headers["X-Frame-Options"] == "DENY"
    sid, h = session(client)
    bad = client.post(f"/sessions/{sid}/proposals/x/decide", headers=h, json={"approve": "secret-value"})
    assert bad.status_code == 422 and "secret-value" not in bad.text
    assert len(bad.headers["X-Request-ID"]) == 32
    monkeypatch.setattr(apimod.sessions, "exists", lambda _: (_ for _ in ()).throw(RuntimeError("database-secret")))
    assert client.get("/healthz").status_code == 503 and client.get("/livez").status_code == 200


def test_oversized_json_is_rejected_before_parsing(client):
    sid, h = session(client)
    assert client.post(f"/sessions/{sid}/proposals/x/decide", headers=h, content=b"{" + b"x" * 20000).status_code == 413


def test_pilot_startup_rejects_missing_infrastructure(monkeypatch):
    monkeypatch.setenv("ATEZAIN_MODE", "pilot")
    monkeypatch.delenv("ATEZAIN_DSN", raising=False); monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="Postgres"):
        Settings.from_env()


def test_pilot_provisioning_requires_operator(client, monkeypatch):
    monkeypatch.setattr(apimod, "settings", replace(apimod.settings, mode="pilot"))
    monkeypatch.setattr(apimod, "OWNER_TOKEN", "operator-secret")
    assert client.post("/sessions").status_code == 403
    assert client.post("/sessions", headers={"X-Owner-Token": "operator-secret"}).status_code == 200


def test_recovery_after_a_proposal_was_saved_does_not_propose_twice(client, monkeypatch):
    sid, h = session(client); upload(client, sid, h)
    st = apimod.state_of(sid)
    original = type(st.policy).propose
    crash = [True]
    def interrupted(self, *args, **kwargs):
        p = original(self, *args, **kwargs)
        if crash[0]:
            crash[0] = False
            raise RuntimeError("checkpoint was not saved")
        return p
    monkeypatch.setattr(type(st.policy), "propose", interrupted)
    response = assist(client, sid, h)
    assert response.status_code == 503 and "may have been recorded" in response.json()["detail"]
    assert len(client.get(f"/sessions/{sid}/proposals", headers=h).json()) == 1
    resumed = assist(client, sid, h)
    assert resumed.status_code == 200 and resumed.json()["cached"] is True
    assert len(resumed.json()["proposals"]) == 1
    assert client.get(f"/sessions/{sid}/audit", headers=h).json()["anomalies"] == []


def test_uncertain_execution_pauses_new_work_and_remains_exportable(client, monkeypatch):
    sid, h = session(client); upload(client, sid, h)
    pid = assist(client, sid, h).json()["proposals"][0]["id"]
    original = apimod.make_executor
    def factory(records):
        execute = original(records)
        def uncertain(*args):
            execute(*args)
            raise RuntimeError("connection lost after write")
        return uncertain
    monkeypatch.setattr(apimod, "make_executor", factory)
    result = client.post(f"/sessions/{sid}/proposals/{pid}/decide", headers=h, json={"approve": True}).json()
    assert result["proposal"]["status"] == "executed_unknown" and not result["executed"]
    assert assist(client, sid, h).status_code == 409
    assert client.get(f"/sessions/{sid}/export", headers=h).status_code == 200


def test_failed_import_row_rolls_back_its_invoice(client, monkeypatch):
    sid, h = session(client)
    monkeypatch.setattr(apimod.state_of(sid).records, "add_note_raw", lambda *a: (_ for _ in ()).throw(ValueError("bad note")))
    result = upload(client, sid, h).json()
    assert result["loaded"] == 0 and result["rejected"]
    assert client.get(f"/sessions/{sid}/records", headers=h).json() == []


def test_export_verifier_detects_modification_and_truncation(client):
    import copy
    from ops.verify_export import verify
    sid, h = session(client); upload(client, sid, h); assist(client, sid, h)
    data = client.get(f"/sessions/{sid}/export", headers=h).json()
    anchor = data["audit"]["head_seq"], data["audit"]["head_hash"]
    assert verify(data, anchor)
    changed = copy.deepcopy(data)
    changed["audit"]["rows"][0]["detail"] = '{}'
    assert not verify(changed)
    changed = copy.deepcopy(data)
    changed["audit"].update(rows=[], head_seq=0, head_hash="0" * 64)
    assert not verify(changed, anchor)


def test_viewer_cannot_reopen_stop_rotate_delete_or_decide(client):
    sid, h = session(client)
    v = client.post(f"/sessions/{sid}/access", headers=h, json={"role": "viewer", "label": "Audit"}).json()
    vh = {"Authorization": "Bearer " + v["token"], "X-Confirm-Delete": sid}
    for path in ("/fuse/clear", "/fuse/stop", "/token/rotate"):
        assert client.post(f"/sessions/{sid}" + path, headers=vh).status_code == 403
    assert client.delete(f"/sessions/{sid}", headers=vh).status_code == 403
    assert client.post(f"/sessions/{sid}/proposals/x/decide", headers=vh, json={"approve": True}).status_code == 403


def test_chunked_body_is_bounded_without_content_length():
    import asyncio
    from api.http import BodyLimit
    sent, called = [], []
    async def inner(*args): called.append(True)
    async def receive(): return {"type": "http.request", "body": b"x" * 20000, "more_body": False}
    async def send(message): sent.append(message)
    asyncio.run(BodyLimit(inner)({"type": "http", "method": "POST", "path": "/sessions", "headers": []}, receive, send))
    assert not called and sent[0]["status"] == 413


def test_generated_gauge_updates_only_measured_counts():
    from tests.gauge_record import sync_counts
    old = '| `make test` | 7 passed, 3 skipped |\n| `make hostile` | 2/2 scored attempts blocked |\n'
    new = '| `make test` | 9 passed, 3 skipped |\n| `make hostile` | 4/4 scored attempts blocked |\n'
    result = sync_counts('7 passed, 3 skipped; 2/2 scored attempts blocked; customer has 7 invoices', old, new)
    assert result == '9 passed, 3 skipped; 4/4 scored attempts blocked; customer has 7 invoices'

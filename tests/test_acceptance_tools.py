"""Acceptance tools must refuse false passes and retain unscored human review evidence."""
import copy
import datetime as dt
import json
from pathlib import Path
import time

import httpx
import pytest

from ops.hosted_workload import Runner, Refused, read_credentials, validate_plan, verify_effects
from ops.model_review import build_pack, render, ROOT
from tests.test_api import client, apimod, session
from tests.test_accounting import connector, tokens


def plan():
    return {"origin": "https://acceptance.example.test", "workspace": "a" * 16,
        "candidate_commit": "b" * 40, "image_digest": "sha256:" + "c" * 64,
        "fixture_prefix": "ATEZAIN-ACCEPTANCE-abcdefgh-", "policy_fingerprint": "test-policy",
        "reviewers": [{"key": f"reviewer-{i+1}", "invoices": [f"F-2026-{i*10+j:03d}" for j in range(10)]}
                      for i in range(5)]}


def test_pack_keeps_every_current_output_unscored_and_refuses_changed_input(tmp_path):
    report = ROOT / "redteam/served-live-results.json"
    pack = build_pack(report)
    observed = json.loads(report.read_text())
    assert len(pack["adversarial_cases"]) == observed["n"]
    assert sum(bool(c["parse_refusal"]) for c in pack["adversarial_cases"]) == observed["output_refusals"]
    assert all(c["review"]["injected_goal_adopted_in_prose"] is None for c in pack["adversarial_cases"])
    assert all(c["status"] == "NOT_RUN" and c["actual_output"] is None for c in pack["ordinary_workflow"])
    assert pack["acceptance"]["accepted"] is None
    changed = copy.deepcopy(observed)
    changed["rows"][0]["context_hash"] = "f" * 64
    path = tmp_path / "changed.json"; path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="does not match recorded input"):
        build_pack(path)
    changed["rows"][0] = changed["rows"][1]
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="exactly once"):
        build_pack(path)
    pack["adversarial_cases"][0]["raw_output"] = "</pre><script>alert('x')</script>"
    rendered = render(pack)
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered


@pytest.mark.parametrize("change", [
    {"origin": "http://acceptance.example.test"},
    {"origin": "https://secret@acceptance.example.test"},
    {"origin": "https://acceptance.example.test/sessions"},
    {"fixture_prefix": "customer"},
    {"candidate_commit": "unknown"},
])
def test_unbound_or_unsafe_workload_plan_is_refused(change):
    with pytest.raises(Refused):
        validate_plan({**plan(), **change})


def test_demo_and_redirect_cannot_become_passes_or_receive_followed_credentials():
    seen = []
    def demo(request):
        seen.append(request)
        return httpx.Response(200, json={"mode": "demo"})
    runner = Runner(plan(), lambda: {}, transport=httpx.MockTransport(demo))
    result = runner.run(apply=True)
    assert result["status"] == "FAILED" and not result["passed"]
    assert len(seen) == 1 and seen[0].method == "GET" and "cookie" not in seen[0].headers
    seen.clear()
    def redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/steal"})
    runner = Runner(plan(), lambda: {"reviewer-1": "s" * 43}, transport=httpx.MockTransport(redirect))
    with runner.client() as connection, pytest.raises(Refused, match="HTTP 302"):
        runner.request(connection, "GET", "/auth/me", "identity", "reviewer-1")
    assert len(seen) == 1 and seen[0].url.host == "acceptance.example.test"


@pytest.mark.parametrize("status,detail,retries", [(409, "workspace busy; retry shortly", 1),
    (409, "another reviewer changed this case", 0), (429, "rate limit", 0), (503, "uncertain effect", 0)])
def test_only_explicit_busy_conflicts_are_retried(status, detail, retries):
    requests, sleeps = [], []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, json={"detail": detail + " PRIVATE_RESPONSE"}, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"ok": True})
    runner = Runner(plan(), lambda: {}, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    with runner.client() as connection:
        if retries:
            assert runner.request(connection, "GET", "/test", "non_provider") == {"ok": True}
        else:
            with pytest.raises(Refused) as error:
                runner.request(connection, "GET", "/test", "non_provider")
            assert "PRIVATE_RESPONSE" not in str(error.value)
    assert len(requests) == 1 + retries and len(sleeps) == retries
    assert runner.samples[-1]["busy_retries"] == retries


def test_private_session_file_and_renewed_subject_are_checked(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"reviewer-1": "s" * 43})); path.chmod(0o644)
    with pytest.raises(Refused, match="private"):
        read_credentials(path)
    path.chmod(0o600)
    assert read_credentials(path)["reviewer-1"] == "s" * 43
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"issuer": "https://idp.example", "subject": "different", "csrf": "private"})
    runner = Runner(plan(), lambda: read_credentials(path), transport=httpx.MockTransport(handler))
    runner.identities = {"reviewer-1": ("https://idp.example", "original")}
    with runner.client() as connection, pytest.raises(Refused, match="identity changed"):
        runner.request(connection, "PUT", "/sessions/case", "non_provider", "reviewer-1", {})
    assert [r.method for r in sent] == ["GET"]


def test_worker_uses_real_cookie_csrf_api_and_detects_missing_or_extra_effects(client, connector, monkeypatch):
    """Local protocol/fixture test only: it does not manufacture a hosted PASS report."""
    from dataclasses import replace
    from api.configuration import served_policy
    monkeypatch.setattr(apimod, "config", served_policy("enterprise"))
    class FixtureModel:
        def complete(self, system, user):
            record = json.loads(user)["context"]["invoice"]
            return json.dumps({"summary": "Synthetic fixture", "recommendation": "Review", "draft": "Fixture reminder",
                "proposals": [{"action": "add_note", "params": {"note": "Fixture note"}},
                    {"action": "send_reminder", "params": {"reminder_text": "Fixture reminder", "reminder_channel": "email", "reminder_to": record["contact"]}},
                    {"action": "update_status", "params": {"status": "reminded"}}]})
    monkeypatch.setattr(apimod, "model_for", lambda key: (FixtureModel(), "fixture-output", False))
    sid, owner = session(client)
    xero, _, replies = connector
    replies["Invoices"]["Invoices"].append({**replies["Invoices"]["Invoices"][0],
        "InvoiceID": "55555555-5555-4555-8555-555555555555", "InvoiceNumber": "OTHER"})
    original_transport = xero.transport
    def selected_invoice(method, url, **kwargs):
        response = original_transport(method, url, **kwargs)
        if "/Invoices/" in url:
            response["Invoices"] = [r for r in response["Invoices"] if r["InvoiceID"] == url.rsplit("/", 1)[-1]]
        return response
    xero.transport = selected_invoice
    xero.store.save(sid, "66666666-6666-4666-8666-666666666666", tokens()); monkeypatch.setattr(apimod, "xero", xero)
    assert client.post(f"/sessions/{sid}/accounting/sync", headers=owner).status_code == 200
    rid = apimod.state_of(sid).records.ids()[0]
    issuer, subject = "https://fixture-idp.example", "synthetic-reviewer"
    apimod.sessions.identity.member(sid, issuer, subject, "reviewer", True, "fixture")
    token = apimod.sessions.identity.issue({"iss": issuer, "sub": subject, "exp": time.time()+300})
    monkeypatch.setattr(apimod, "settings", replace(apimod.settings, identity_required=True))
    monkeypatch.setattr(apimod.sessions, "oidc_only", True)
    def forward(request):
        response = client.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
    runner = Runner(plan(), lambda: {"reviewer-1": token}, transport=httpx.MockTransport(forward))
    runner.identities = {"reviewer-1": (issuer, subject)}
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    runner.next_action = today
    with runner.client() as connection:
        before = runner.request(connection, "GET", f"/sessions/{sid}/export", "preflight", "reviewer-1")
    runner.worker({"key": "reviewer-1", "invoices": [rid]}, f"/sessions/{sid}")
    assert not runner.errors, runner.errors
    with runner.client() as connection:
        after = runner.request(connection, "GET", f"/sessions/{sid}/export", "non_provider", "reviewer-1")
    verify_effects(before, after, runner.approved, runner.cases, today, today)
    for corruption in ("extra_note", "missing_note", "amount", "other_record", "case", "proposal", "source", "audit", "policy"):
        broken = copy.deepcopy(after)
        if corruption == "extra_note": broken["records"][0]["notes"].append(broken["records"][0]["notes"][-1])
        elif corruption == "missing_note": broken["records"][0]["notes"].pop()
        elif corruption == "amount": broken["records"][0]["amount"] = 0
        elif corruption == "other_record": broken["records"][1]["amount"] = 0
        elif corruption == "case": broken["cases"][rid]["version"] = 2
        elif corruption == "proposal": broken["proposals"].append(broken["proposals"][0])
        elif corruption == "source": broken["source_snapshots"][rid]["amount"] = "0.00"
        elif corruption == "audit": broken["audit"]["rows"][-1]["detail"] = {}
        else: broken["policy_fingerprint"] = "different-policy"
        with pytest.raises(Refused):
            verify_effects(before, broken, runner.approved, runner.cases, today, today)


def synthetic_remote(variant="valid"):
    """HTTP contract fixture; these responses are never recorded as hosted evidence."""
    from policy.store import GENESIS
    target = plan()
    records = [{"id": f"F-2026-{i:03d}", "customer": target["fixture_prefix"] + str(i),
        "contact": "ar@example.test", "source_revision": "revision"} for i in range(500)]
    before = {"session": target["workspace"], "policy_fingerprint": target["policy_fingerprint"],
        "records": records, "source_snapshots": {r["id"]: {} for r in records}, "cases": {}, "proposals": [],
        "accounting": {"tenant": "fixture-tenant", "cursor": time.time(), "full_at": time.time()},
        "fuse": {"tripped": False}, "audit": {"rows": [], "head_seq": 0, "head_hash": GENESIS, "verifies": True, "anomalies": []}}
    if variant == "customer_data": records[-1]["customer"] = "Actual customer"
    if variant == "stale_sync": before["accounting"]["cursor"] -= 3601
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path))
        path = request.url.path
        subject = "same-subject" if variant == "shared_identity" else request.headers.get("cookie", "")[-1:]
        if path == "/capabilities":
            value = {"mode": "enterprise", "identity_required": True, "oidc": True,
                     "all_writes_require_approval": True, "sends_email": False, "model": "fixture-only"}
        elif path == "/healthz": value = {"store": True, "backing": "postgres"}
        elif path == "/auth/me": value = {"issuer": "https://fixture-idp.example", "subject": subject,
                                           "csrf": "private", "workspaces": [target["workspace"]]}
        elif path.endswith("/access"): value = {"issuer": "https://fixture-idp.example", "subject": subject, "verified": True, "role": "reviewer"}
        elif path.endswith("/export"): value = before
        elif path.endswith("/accounting"): value = {"configured": True, "connection": {"tenant": "fixture-tenant"}}
        elif path.endswith("/worklist"): value = {"rows": [{"invoice": r} for r in records]}
        else: return httpx.Response(503, json={"detail": "provider failure PRIVATE_RESPONSE"})
        return httpx.Response(200, json=value)
    return target, httpx.MockTransport(handler), seen


@pytest.mark.parametrize("variant", ["valid", "customer_data", "shared_identity", "stale_sync"])
def test_preflight_cannot_count_as_capacity_pass_and_refuses_unsafe_fixtures(variant):
    target, transport, seen = synthetic_remote(variant)
    runner = Runner(target, lambda: {f"reviewer-{i}": str(i)*43 for i in range(1,6)}, transport=transport)
    result = runner.run()
    assert not result["passed"] and result["completed_cases"] == 0
    assert result["status"] == ("PREFLIGHT_ONLY" if variant == "valid" else "FAILED")
    assert all(method == "GET" for method, _ in seen)


def test_provider_failure_retains_export_and_cannot_turn_into_a_low_latency_pass():
    target, transport, seen = synthetic_remote()
    runner = Runner(target, lambda: {f"reviewer-{i}": str(i)*43 for i in range(1,6)}, transport=transport)
    result = runner.run(apply=True)
    assert result["preflight_passed"] and not result["passed"] and result["status"] == "FAILED"
    assert result["errors"] and {"before", "after"} <= runner.evidence.keys()
    assert "PRIVATE_RESPONSE" not in json.dumps(result)
    assert any(method == "POST" for method, _ in seen)

"""HTTPS capacity exercise for an explicitly designated fictional enterprise workspace.

Preflight is read-only by default. --apply plus exact workspace confirmation enables synthetic
assists, approvals and case saves. No account provisioning, source writes, deletion or bypass login.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import urlsplit

import httpx

from ops.verify_export import verify


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_plan(plan):
    origin = urlsplit(plan["origin"])
    require(origin.scheme == "https" and origin.hostname and not origin.username and not origin.password
            and origin.path in {"", "/"} and not origin.query and not origin.fragment, "HTTPS origin required")
    require(re.fullmatch(r"[a-f0-9]{16}", plan["workspace"]), "invalid workspace")
    require(re.fullmatch(r"ATEZAIN-ACCEPTANCE-[A-Za-z0-9_-]{8,40}-", plan["fixture_prefix"]), "unique fictional fixture prefix required")
    require(re.fullmatch(r"[a-f0-9]{40}", plan["candidate_commit"]), "candidate commit required")
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", plan["image_digest"]), "image digest required")
    require(isinstance(plan["policy_fingerprint"], str) and plan["policy_fingerprint"], "expected policy required")
    reviewers = plan["reviewers"]
    require(len(reviewers) == 5 and len({r["key"] for r in reviewers}) == 5, "five distinct reviewer keys required")
    ids = []
    for reviewer in reviewers:
        require(re.fullmatch(r"reviewer-[1-5]", reviewer["key"]), "invalid reviewer key")
        require(len(reviewer["invoices"]) == 10, "ten invoices per reviewer required")
        ids.extend(reviewer["invoices"])
    require(len(set(ids)) == 50 and all(re.fullmatch(r"F-[0-9]{4}-[0-9]{3}", rid) for rid in ids),
            "fifty distinct invoice IDs required")


def read_credentials(path):
    path = Path(path)
    require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "credential file must be private (mode 600)")
    values = json.loads(path.read_text())
    require(isinstance(values, dict) and all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,256}", v)
                                          for v in values.values()), "invalid session credential file")
    return values


def verify_effects(before, after, approved, cases, start_date, end_date):
    """Compare exact expected business effects, including every untouched invoice and old note."""
    require(all(before[k] == after[k] for k in ("session", "policy_fingerprint", "identity_events")),
            "workspace, policy or membership administration changed")
    require(before["source_snapshots"] == after["source_snapshots"], "source changed during workload; reconcile and rerun")
    expected = {r["id"]: copy.deepcopy(r) for r in before["records"]}
    for proposal in approved:
        record, params = expected[proposal["record_id"]], proposal["params"]
        if proposal["action"] == "add_note":
            require(set(params) == {"note"}, "unexpected note fields")
            record["notes"].append({"text": params["note"], "author": "assistant", "ts": "__GENERATED__"})
        elif proposal["action"] == "update_status":
            require(set(params) == {"status"}, "unexpected status fields")
            record["status"] = params["status"]
        elif proposal["action"] == "send_reminder":
            require(set(params) == {"reminder_text", "reminder_channel", "reminder_to"}, "unexpected reminder fields")
            record.update(params)
        else:
            raise Refused("unexpected approved action")
    for rid, value in cases.items():
        expected[rid]["collection_case"] = value
        expected[rid]["record_version"] = expected[rid]["source_revision"] + ":case:1"
    actual = {r["id"]: copy.deepcopy(r) for r in after["records"]}
    require(len(actual) == len(after["records"]) and actual.keys() == expected.keys(), "missing or duplicate invoice")
    for rid, record in expected.items():
        require(len(actual[rid]["notes"]) == len(record["notes"]), "missing or duplicate note effect")
        for index, note in enumerate(record["notes"]):
            if note["ts"] == "__GENERATED__":
                observed = actual[rid]["notes"][index]["ts"]
                require(start_date <= observed <= end_date, "unexpected note timestamp")
                actual[rid]["notes"][index]["ts"] = "__GENERATED__"
    require(actual == expected and after["cases"] == cases, "record or case effects differ from approved work")
    proposals = after["proposals"]
    require(len({p["id"] for p in proposals}) == len(proposals) and all(p["status"] == "executed" for p in proposals),
            "missing execution or duplicate proposal")
    returned = {p["id"]: p for p in approved}
    observed_cases = {}
    for proposal in proposals:
        if proposal["action"] == "manage_case":
            rid = proposal["record_id"]
            require(rid not in observed_cases and set(proposal["params"]) == {"case_json"}, "duplicate case proposal")
            observed_cases[rid] = json.loads(proposal["params"]["case_json"])
        else:
            require(proposal["id"] in returned, "unexpected proposal")
            original = returned.pop(proposal["id"])
            require(all(proposal[k] == original[k] for k in ("action", "record_id", "params")), "approved proposal changed")
    require(not returned and observed_cases == cases, "missing proposal effect")
    anchor = (before["audit"]["head_seq"], before["audit"]["head_hash"])
    require(verify(after, anchor) and after["audit"]["verifies"] and after["audit"]["anomalies"] == [], "audit verification failed")
    executions = Counter(r["proposal_id"] for r in after["audit"]["rows"] if r["kind"] == "EXECUTED")
    require(executions == Counter({p["id"]: 1 for p in proposals}), "missing or repeated audited effect")


class Runner:
    def __init__(self, plan, credentials, *, transport=None, sleep=time.sleep):
        validate_plan(plan)
        self.plan, self.credentials, self.transport, self.sleep = plan, credentials, transport, sleep
        self.samples, self.errors, self.approved, self.cases, self.assists = [], [], [], {}, {}
        self.evidence = {}
        self.lock, self.stop = threading.Lock(), threading.Event()

    def client(self):
        return httpx.Client(base_url=self.plan["origin"].rstrip("/"), transport=self.transport,
                            timeout=120, follow_redirects=False, trust_env=False)

    def request(self, client, method, path, category, key=None, body=None, session_token=None):
        start, retries = time.monotonic(), 0
        while True:
            headers = {}
            if key is not None:
                values = {key: session_token} if session_token is not None else self.credentials()
                require(key in values, "reviewer session unavailable")
                headers["Cookie"] = "__Host-atezain=" + values[key]
                if method != "GET":
                    # This read checks the current short-lived login; it never mints or extends one.
                    login = self.request(client, "GET", "/auth/me", "identity", key, session_token=values[key])
                    headers["X-CSRF-Token"] = login["csrf"]
            try:
                response = client.request(method, path, headers=headers, json=body)
                seconds = time.monotonic() - start
                if response.status_code == 409 and "workspace busy" in response.text.lower() and retries < 10:
                    delay = float(response.headers.get("Retry-After", "1"))
                    if 0 < delay <= 30 and seconds + delay < 60:
                        retries += 1
                        self.sleep(delay)
                        continue
                with self.lock:
                    self.samples.append({"category": category, "method": method, "status": response.status_code,
                        "seconds": seconds, "busy_retries": retries})
                require(response.status_code == 200, category + " HTTP " + str(response.status_code))
                value = response.json()
                if path == "/auth/me" and key in getattr(self, "identities", {}):
                    require((value["issuer"], value["subject"]) == self.identities[key], "reviewer identity changed during workload")
                return value
            except httpx.HTTPError:
                with self.lock:
                    self.samples.append({"category": category, "method": method, "status": None,
                        "seconds": time.monotonic() - start, "busy_retries": retries})
                raise Refused(category + " transport failure; inspect effects before any retry") from None

    def preflight(self, client):
        caps = self.request(client, "GET", "/capabilities", "preflight")
        require(caps.get("mode") == "enterprise" and caps.get("identity_required") is True and caps.get("oidc") is True
                and caps.get("all_writes_require_approval") is True and caps.get("sends_email") is False
                and caps.get("model") not in {None, "stub"}, "configured enterprise mode with live model required")
        health = self.request(client, "GET", "/healthz", "preflight")
        require(health.get("store") is True and health.get("backing") == "postgres", "healthy PostgreSQL required")
        self.evidence["capabilities"] = caps
        path = "/sessions/" + self.plan["workspace"]
        identities = {}
        for reviewer in self.plan["reviewers"]:
            key = reviewer["key"]
            login = self.request(client, "GET", "/auth/me", "preflight", key)
            access = self.request(client, "GET", path + "/access", "preflight", key)
            require(access.get("verified") is True and access.get("role") == "reviewer"
                    and self.plan["workspace"] in login["workspaces"]
                    and (access["issuer"], access["subject"]) == (login["issuer"], login["subject"]),
                    "verified reviewer membership required")
            identities[key] = (login["issuer"], login["subject"])
        require(len(set(identities.values())) == 5, "five distinct verified subjects required")
        before = self.request(client, "GET", path + "/export", "preflight", "reviewer-1")
        require(before["session"] == self.plan["workspace"] and before["policy_fingerprint"] == self.plan["policy_fingerprint"],
                "workspace or policy mismatch")
        require(len(before["records"]) == 500 and len({r["id"] for r in before["records"]}) == 500,
                "exactly 500 invoice records required")
        require(not before["proposals"] and not before["cases"] and verify(before) and before["audit"]["verifies"]
                and before["audit"]["anomalies"] == [] and not before["fuse"]["tripped"], "fresh workspace with clean audit and open fuse required")
        require(set(before["source_snapshots"]) == {r["id"] for r in before["records"]}, "all invoices must have accounting snapshots")
        accounting = self.request(client, "GET", path + "/accounting", "preflight", "reviewer-1")
        require(accounting.get("configured") is True and accounting.get("connection")
                and before.get("accounting") and accounting["connection"]["tenant"] == before["accounting"]["tenant"]
                and time.time() - 3600 < before["accounting"]["cursor"] <= time.time() + 30,
                "connected accounting with a recent successful sync required")
        require(time.time() - 86400 < before["accounting"]["full_at"] <= time.time() + 30, "recent full reconciliation required")
        for record in before["records"]:
            require(record["customer"].startswith(self.plan["fixture_prefix"])
                    and record.get("contact", "").endswith("@example.test") and record.get("source_revision"),
                    "workspace is not the designated fictional accounting fixture")
        assigned = {rid for r in self.plan["reviewers"] for rid in r["invoices"]}
        due = self.request(client, "GET", path + "/worklist", "preflight", "reviewer-1")
        require(assigned <= {row["invoice"]["id"] for row in due["rows"]}, "selected invoices are not all due")
        self.evidence["before"] = before
        self.identities = identities
        return path

    def worker(self, reviewer, path):
        key = reviewer["key"]
        try:
            with self.client() as client:
                for rid in reviewer["invoices"]:
                    if self.stop.is_set():
                        return
                    assist = self.request(client, "POST", path + "/assist/" + rid, "provider_assist", key)
                    require(assist["cached"] is False and assist["invoice_id"] == rid and assist["proposals"], "fresh assisted proposals required")
                    declared_model = self.evidence.get("capabilities", {}).get("model")
                    require(declared_model is None or assist["model"] == declared_model, "assisted model differs from deployment configuration")
                    with self.lock:
                        self.assists[rid] = assist
                    for proposal in assist["proposals"]:
                        require(re.fullmatch(r"[a-f0-9]{32}", proposal["id"])
                                and proposal["record_id"] == rid and proposal["status"] == "held"
                                and proposal["action"] in {"add_note", "update_status", "send_reminder"}, "unexpected model proposal; inspect held work")
                        decision = self.request(client, "POST", path + "/proposals/" + proposal["id"] + "/decide",
                            "provider_decision", key, {"approve": True, "note": "Synthetic acceptance workload; no human quality judgment or email delivery"})
                        require(decision["executed"] is True, "approval did not execute")
                        with self.lock:
                            self.approved.append(proposal)
                    expected = {"version": 1, "assignee": self.identities[key][1], "next_action": self.next_action,
                        "state": "open", "promise_date": "", "promise_amount": "", "note": self.plan["fixture_prefix"] + "workload"}
                    case = self.request(client, "PUT", path + "/cases/" + rid, "non_provider", key, {**expected, "version": 0})
                    require(case == expected, "case save returned unexpected state")
                    with self.lock:
                        self.cases[rid] = expected
                    self.request(client, "GET", path + "/worklist", "non_provider", key)
        except Exception as exc:
            with self.lock:
                self.errors.append({"reviewer": key, "error": str(exc) if isinstance(exc, Refused) else type(exc).__name__})
            self.stop.set()

    def run(self, apply=False):
        started = dt.datetime.now(dt.timezone.utc)
        self.next_action = (started.date() + dt.timedelta(days=1)).isoformat()
        report = {"format": "atezain-hosted-workload-v1", "at": started.isoformat(), "passed": False,
            "status": "FAILED", "candidate_commit": self.plan["candidate_commit"], "image_digest": self.plan["image_digest"],
            "deployment_identity": "operator-supplied; compare with deployment evidence",
            "origin": self.plan["origin"], "workspace": self.plan["workspace"], "plan_sha256": fingerprint(self.plan),
            "target": {"records": 500, "reviewers": 5, "assisted_cases": 50, "non_provider_p95_seconds": 2},
            "scope": "one concurrent synthetic batch through HTTPS; not a full working-day soak, human UAT or cold-start proof"}
        try:
            with self.client() as client:
                path = self.preflight(client)
                report["preflight_passed"] = True
                if not apply:
                    report["status"] = "PREFLIGHT_ONLY"
                else:
                    with ThreadPoolExecutor(max_workers=5) as pool:
                        list(pool.map(lambda reviewer: self.worker(reviewer, path), self.plan["reviewers"]))
                    # All workers have stopped before capturing effects, including on partial failure.
                    after = self.request(client, "GET", path + "/export", "non_provider", "reviewer-1")
                    self.evidence["after"] = after
                    require(self.request(client, "GET", "/capabilities", "preflight") == self.evidence["capabilities"],
                            "deployment capabilities changed during workload")
                    require(not self.errors, "one or more reviewers failed; partial effects retained")
                    require(len(self.cases) == 50 and len(self.assists) == 50, "incomplete workload")
                    verify_effects(self.evidence["before"], after, self.approved, self.cases,
                                  started.date().isoformat(), dt.datetime.now(dt.timezone.utc).date().isoformat())
                    report["effects_verified"] = True
                    durations = sorted(s["seconds"] for s in self.samples if s["category"] == "non_provider")
                    p95 = durations[math.ceil(len(durations) * .95) - 1]
                    report["non_provider_p95_seconds"] = p95
                    require(p95 < 2, "non-provider p95 exceeds release target")
                    report.update(status="PASS", passed=True)
        except Exception as exc:
            self.errors.append({"error": str(exc) if isinstance(exc, Refused) else type(exc).__name__})
        report.update(completed_cases=len(self.cases), assisted_cases=len(self.assists), errors=self.errors,
                      requests=self.samples, seconds=(dt.datetime.now(dt.timezone.utc) - started).total_seconds())
        report["evidence_sha256"] = {k: fingerprint(v) for k, v in self.evidence.items()}
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True, help="private JSON mapping reviewer keys to current OIDC session cookies")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-workspace")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    validate_plan(plan)
    if args.apply and args.confirm_workspace != plan["workspace"]:
        parser.error("--apply requires --confirm-workspace with the exact fictional workspace ID")
    read_credentials(args.credentials)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    runner = Runner(plan, lambda: read_credentials(args.credentials))
    report = runner.run(apply=args.apply)
    for name, value in {**runner.evidence, "assists": runner.assists, "report": report}.items():
        (args.output / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "passed", "completed_cases", "assisted_cases")}))
    return 0 if report["status"] in {"PASS", "PREFLIGHT_ONLY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

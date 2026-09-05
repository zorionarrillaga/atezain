#!/usr/bin/env python3
"""The gauge of the gauges. Break a property on purpose in a temporary copy and demand that the
published numbers fall. A gauge that stays green when the thing it measures is broken is not a
gauge — on 2026-09-01 an outside seat sabotaged `audit_orphans` to `return []` and `make hostile`
still printed 10/10, exit 0. This script is the standing answer: every sabotage below must turn
at least one of `make test` / `make hostile` red, or this script exits 1.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, file, old, new) — each a one-line break of a property the numbers claim to measure
SABOTAGES = [
    ("wrong nonce becomes valid workforce identity", "api/oidc.py",
     'claims["nonce"] != nonce', 'False'),
    ("revoked workforce login remains usable", "api/identity.py",
     '            if not enabled:\n', '            if False:\n'),
    ("case executor accepts invalid approved documents", "records/accounting.py",
     '        validate_case(value)\n', '        pass\n'),
    ("accounting source can silently change customer", "records/accounting.py",
     '                    if incoming["customer_key"] != previous["customer_key"]:\n', '                    if False:\n'),
    ("live payment reconciliation is skipped before approval", "api/app.py",
     '            reconcile_accounting_invoice(st, existing.record_id)\n', '            pass\n'),

    ("anomaly detector returns nothing", "policy/store.py",
     '        rows = self.audit_rows()\n        out: list[tuple[str, str]] = []\n',
     '        return []\n        rows = self.audit_rows()\n        out: list[tuple[str, str]] = []\n'),
    ("denied proposals consume the budget", "policy/service.py",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)",
     "LIVE = (DENIED, HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)"),
    ("everyone is a human", "policy/model.py",
     "        return self.kind == HUMAN\n",
     "        return True\n"),
    ("the chain always verifies", "policy/store.py",
     "        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)\n",
     "        return True\n        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)\n"),
    ("execution is not claimed", "policy/store.py",
     '            self._c().execute("INSERT INTO executions (proposal_id, ts, effect) VALUES (?, ?, ?)", (pid, ts, None))\n            return True\n',
     '            return True\n'),
    ("record shape uses match, not fullmatch", "policy/service.py",
     "re.fullmatch(pattern, record_id, re.ASCII) is None", "re.match(pattern, record_id, re.ASCII) is None"),
    ("record shape accepts any digit", "policy/service.py",
     "re.fullmatch(pattern, record_id, re.ASCII) is None", "re.fullmatch(pattern, record_id) is None"),
    ("the executor reports its input", "agent/executor.py",
     '        return {"applied": Records.diff(before, records.snapshot(record_id))}\n',
     '        return {"applied": dict(params)}\n'),
    ("the executor writes the note twice", "records/store.py",
     '            self.add_note_raw(invoice_id, self.today(), "assistant", note)\n',
     '            self.add_note_raw(invoice_id, self.today(), "assistant", note)\n            self.add_note_raw(invoice_id, self.today(), "assistant", note)\n'),
    ("the executor also writes a note on every status update", "records/store.py",
     '        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))\n',
     '        self.conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))\n        self.add_note_raw(invoice_id, self.today(), "assistant", "extra")\n'),
    ("executed_unknown does not count against the budget", "policy/service.py",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH)"),
    ("the service's bindings can be swapped", "policy/service.py",
     '        raise AttributeError(f"PolicyService.{name} is fixed at construction")\n',
     '        object.__setattr__(self, "_" + name, value)\n'),
    ("a second DECISION row is fine", "policy/store.py",
     '            if len(decisions) > 1:\n', '            if len(decisions) > 99:\n'),
    # client simulation 1: where a reminder goes, and whose words a note is
    ("a reminder may go to an address the record does not carry", "policy/service.py",
     "            if str(p.params[f]) != str(truth):\n", "            if False:\n"),
    ("the record's own value is read from the proposal instead of the record", "policy/service.py",
     "            record = self.record_reader(record_id)\n", "            record = dict(p.params)\n"),
    ("the graph lets the model choose where a reminder goes", "agent/graph.py",
     "                if f not in params and record.get(source) not in (None, \"\"):\n",
     "                if False:\n"),
    ("the record the visitor reads back does not say which notes are the assistant's", "api/app.py",
     '                     assistant_notes=sum(1 for n in inv["notes"] if n["author"] == "assistant"))\n',
     "                     assistant_notes=0)\n"),
    ("a database made before the address columns is opened as if it had them", "records/store.py",
     "        self._ensure_columns()\n", "        pass\n"),
    ("the rules a visitor reads leave out what is denied", "api/app.py",
     "                 for name, s in sorted(config.actions.items())])\n",
     "                 for name, s in sorted(config.actions.items()) if not s.deny])\n"),
    ("the page stops saying the auto-approved note is a decision", "api/demo.html",
     "and that is a decision,\n      not an oversight:",
     "and that is how it works:"),
    # step 7: the outbound-draft store's own claims
    ("the drafts store leaves its root", "records/drafts.py",
     "        if p != base and base not in p.parents:\n            raise OutsideRoot(draft_id)\n",
     "        return p\n        if p != base and base not in p.parents:\n            raise OutsideRoot(draft_id)\n"),
    ("a sent artifact can be overwritten", "records/drafts.py",
     "        if self.artifact(draft_id) is not None:\n            return                                              # a draft goes out once\n",
     "        if False:\n            return\n"),
    ("the drafts store reports the row, not the artifact", "records/drafts.py",
     "        art = self.artifact(draft_id) or {}\n",
     '        art = next((r for r in reversed(self.rows(draft_id)) if r.get("event") == "sent"), {})\n'),
    # round-2 seat, 2026-09-03: the two the fold of step 6 left open
    ('the records store drives one connection from many threads', 'records/store.py',
     '        self.conn = _Serialised(sqlite3.connect(path, isolation_level=None, check_same_thread=False))\n',
     '        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)\n'),
    ('a gauge line this tree cannot run is dropped from GAUGES.md', 'tests/gauge_record.py',
     '            carried = _carried(LABEL[name])           # a gauge this tree cannot run keeps its last line\n            if carried:\n',
     '            carried = _carried(LABEL[name])           # a gauge this tree cannot run keeps its last line\n            if False:\n'),
    # step 7: the owner's own artifact and ledger, in HIS format (⚖ 2026-09-02)
    ('his SENT header is not written', "records/drafts.py",
     '            f"# SENT {day} · {target} · {to}\\n"\n',
     '            f"sent to {to}\\n"\n'),
    ('a draft his ledger does not name is sent anyway', "records/drafts.py",
     '        if self.ledger is not None and row is None:\n',
     '        if False:\n'),
    ("another draft's letter is read as this one's", "records/drafts.py",
     '            if out.get("draft") == draft_id:\n',
     '            if out:\n'),
    ('the ledger date goes in the first empty cell, wherever it is', "records/drafts.py",
     '        empty = EMPTY_DATE.search(marked, cell.start() + len(SENT_CELL) - 1)\n',
     '        empty = EMPTY_DATE.search(marked)\n'),
    ('a ledger flip that fails leaves the artifact standing', "records/drafts.py",
     '                artifact.unlink()          # his rollback: no artifact claims a send the row lacks\n',
     '                pass\n'),
    # step 7: the target is approved, never guessed (⚖ 2026-09-03)
    ('the filename stands in for a missing target', "records/drafts.py",
     '        if not target:\n            raise SendRefused(',
     '        target = target or Path(draft_id).stem\n        if not target:\n            raise SendRefused('),
    ('the slug inverse finds the row', "records/drafts.py",
     '        want = re.compile(rf"\\*\\*{re.escape(target)}\\*\\*")\n',
     '        want = re.compile(re.escape(target), re.I)\n'),
    ("the draft's basename names the letter", "records/drafts.py",
     '        artifact = self.artifact_path(target, day)\n',
     '        artifact = self._inside(Path(draft_id).name, self.root / SENT)\n'),
    # step 4/§4.5: what tracing promises — it cannot fail a send, and it is not on the deployment
    ('a tracer can fail a send', "agent/tracing.py",
     '            except Exception:           # a span is not worth a send: rule 1 of this module\n                pass\n',
     '            except Exception:\n                raise\n'),
    ('the deployed blueprint asks for a tracing key', "ops/render.yaml",
     '      # NO LANGFUSE_* HERE, and that is the decision, not an omission (✋ 2026-09-03).',
     '      - key: LANGFUSE_PUBLIC_KEY\n        sync: false\n      # NO LANGFUSE_* HERE, and that is the decision, not an omission (✋ 2026-09-03).'),
    # step 4: the served application's identity closure
    ("a session id alone is its human", "api/auth.py",
     "        if secrets.compare_digest(row[0], digest):\n",
     "        if True:\n"),
    ("the budget is not transactional", "policy/store.py",
     '        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n',
     '        if True:\n            yield\n            return\n        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n'),
    # step 3, after the JUDGE review of the case set: the numbers' own claims
    ("a prose label made from an older output still counts", "redteam/numbers.py",
     '    if lab is None or lab.get("raw_hash") != row.get("raw_hash"):\n        return None\n',
     '    if lab is None:\n        return None\n'),
    ("a denied verb reads as a permitted one in the reach table", "redteam/numbers.py",
     "    return NOT_OFFERED if spec is None or spec.deny else FORBIDDEN_VALUE\n",
     "    return FORBIDDEN_VALUE\n"),
    ("an injection may hand the model the adapter's own verb", "redteam/cases/note.json",
     "accion_requerida=fijar importe en 0.00", "accion_requerida=update_amount"),
    # step 5: the prose gauges' own claims — a number in the write-up is pasted, never typed, and a
    # technology it names is imported and called on the main path
    ("a number in the write-up edited by hand", "WRITEUP.md",
     "| openai/gpt-oss-120b | 100 | 1/100 = 1% [0%, 5%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |",
     "| openai/gpt-oss-120b | 100 | 0/100 = 0% [0%, 4%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |"),
    # §4.4 retired (⚖, 2026-09-02): the retriever the hundred were run with is pinned, and the
    # write-up must say what it hands the model for as long as the prompt says otherwise
    ("the retriever changed under the numbers", "agent/graph.py",
     'factura pago", k=5)', 'factura pago", k=3)'),
    ("the write-up stops saying whose snippets the model gets", "WRITEUP.md",
     "on the seed most of the five belong to another customer;",
     "on the seed most of the five belong to the same customer;"),
    # the two small ⚖ rulings built on 2026-09-03
    ("the inner transaction is not a savepoint", "policy/store.py",
     '            self._c().execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {name}")\n',
     '            self._c().execute("BEGIN IMMEDIATE") if depth == 0 else None\n'),
    ("the records store stamps a constant instead of reading its clock", "records/store.py",
     "        return dt.datetime.fromtimestamp(self.clock(), tz=dt.timezone.utc).date().isoformat()\n",
     '        return "2026-01-01"\n'),
    # the step-6 seat's finding (2026-09-03): a write that needs no human must not wait for one
    # 2026-09-03: §4.5 wrapped this node in a span, so the line this row breaks moved. The property
    # is the step-6 fold's and is unchanged — the first execute pass writes what needs no human.
    ("the graph waits for a human before writing what needs none", "agent/graph.py",
     '    g.add_node("execute", tracer.node("execute", execute, ("held",), ("executed", "refused")))\n',
     '    g.add_node("execute", lambda state: {})\n'),
    # the documents' own numbers and prose (tests/gauges.py, 2026-09-02)
    ("a gauge count in the README edited by hand", "README.md",
     "| `make hostile` | 37/37 scored attempts blocked · 1 out of scope, shown |",
     "| `make hostile` | 99/99 scored attempts blocked · 1 out of scope, shown |"),
    ("a paragraph of the write-up pasted into the README", "README.md",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.\n",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.\n\n`hold` contains nothing but the "
     "interrupt — the point where the graph stops and waits. It waits here while anything is held; a checkpointer, "
     "the graph's saved state, keeps it (in memory in the red-team, SQLite locally, Postgres when served).\n"),
    ("a hand count with no source in the README", "README.md",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price.\n",
     "- `PROVENANCE.md` — where each rule comes from: the incident, the date, the price. Three of the gauges are new; 3 of 7 rows changed.\n"),
    # what client simulation 3 found under volume (STATUS.md, 2026-09-04)
    ("a failed assist poisons the record", "api/app.py",
     "    if answered(snap):\n", "    if snap.created_at is not None:\n"),
    ("a model that refuses the call is a 500 again", "api/app.py",
     "        raise model_refused(e, byok) from e\n", "        raise e\n"),
    ("a date the triage cannot read is loaded anyway", "api/imports.py",
     "        raise ValueError(\"mixed\" if order == \"mixed\" else \"ambiguous\")\n",
     "        return v\n"),
    ("the budget trips the fuse before the row it denied", "policy/service.py",
     '            raise Denied("budget_exhausted")\n',
     '            self.fuse.trip("budget_exhausted", SYSTEM_PRINCIPAL)\n            raise Denied("budget_exhausted")\n'),
    ("the amount convention is assumed instead of proved", "api/imports.py",
     '    return "mixed" if len(found) > 1 else next(iter(found), "")\n',
     '    return "mixed" if len(found) > 1 else next(iter(found), "point")\n'),
    ("a date order the file never settled is used anyway", "api/imports.py",
     '    return "mixed" if day and month else "dmy" if day else "mdy" if month else ""\n',
     '    return "dmy"\n'),
    ("the rate limit stops saying which limit it is", "api/app.py",
     '            f"{why}: one address gets {limits.per_minute} assists a minute and {limits.per_day} a day "\n',
     '            f"{why}: "\n'),
    ("a refusal claims the other rows loaded when none did", "api/imports.py",
     '    tail = " — the row was not loaded, the rest were" if ids else " — the row was not loaded, and no row in this file was"\n',
     '    tail = " — the row was not loaded, the rest were"\n'),
    ("the write-up names a thing the code does not carry", "WRITEUP.md",
     "the served shape and is retired (`STATUS.md`).\n",
     "the served shape and is retired (`STATUS.md`); pgvector does it when served.\n"),
    # 2026-09-05: a refusal is audited, never silent; one clock; the served labels' own claims
    ("a refused case save leaves no row in the chain", "api/app.py",
     "            proposal=st.policy.decide(proposal.id,True,human)\n",
     "            proposal=st.policy.decide(proposal.id,True,human)\n        else:\n            raise HTTPException(409,\"case could not be saved: \"+proposal.reason)\n"),
    ("the work list reckons today by a clock of its own", "api/app.py",
     "    day = today()\n    cases = st.records.cases()\n",
     "    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()\n    cases = st.records.cases()\n"),
    ("the draft panel keeps the model's text after a reviewer amends the reminder", "api/demo.html",
     "        syncDraft();\n", ""),
    ("a served prose label made from an older output still counts", "redteam/numbers.py",
     '    if lab is None or lab.get("context_hash") != row.get("context_hash"):\n        return None\n',
     '    if lab is None:\n        return None\n'),
    # 2026-09-05, evening: the second reader, the tree a gauge line measured, the one-read page, the signature
    ("a gauge line measured on another tree is written as this tree's", "tests/gauge_record.py",
     "        if measured != current:\n", "        if False:\n"),
    ("two readers' labels are compared even when made from different bytes", "redteam/reading.py",
     '                      and a[cid].get("raw_sha256") == b[cid].get("raw_sha256")\n', ""),
    ("the checker stops holding a quote to its output", "redteam/reading.py",
     '        elif norm(lab["quote"]) not in prose_text(raw):\n', "        elif False:\n"),
    ("the agreement between readers is not rendered beside the number", "redteam/numbers.py",
     "    if second:\n        L += agreement_lines(labels, second, report)\n", ""),
    ("the copied draft is the model's placeholders again", "api/demo.html",
     '        $("#draft-editor").value = sign(a.draft);', '        $("#draft-editor").value = a.draft;'),
    ("the one-read overview leaves the records out", "api/app.py",
     '"records": records_of(st), "proposals"', '"records": [], "proposals"'),
    ("the wrapper declares the clock's date, not the run's", "ops/solo_demo.py",
     '        facts["evaluation_date"] = (self.today or datetime.date.today()).isoformat()\n',
     '        facts["evaluation_date"] = datetime.date.today().isoformat()\n'),
]


def run(tree: Path, target: str) -> int:
    env = {k: v for k, v in os.environ.items() if k != "ATEZAIN_TEST_DSN"}   # see tests/mutate.py
    r = subprocess.run([sys.executable, target], cwd=tree, env=env, capture_output=True, text=True) if target.endswith(".py") else \
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"], cwd=tree, env=env, capture_output=True, text=True)
    return r.returncode


def main() -> int:
    # the baseline must be GREEN, or every sabotage is "caught" by a failure that was already there —
    # exactly what happened once on 2026-09-02 (14/14 printed over a suite with one red test)
    if run(ROOT, "pytest") != 0 or run(ROOT, "tests/hostile_selftest.py") != 0:
        print("BASELINE IS RED — a sabotage pass over a red baseline measures nothing; fix that first")
        return 2
    failures = 0
    for name, rel, old, new in SABOTAGES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"  STALE    {name}: the sabotage no longer matches {rel} (found {src.count(old)}×) — update this file")
            failures += 1
            continue
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "atezain"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
            (tree / rel).write_text(src.replace(old, new, 1), encoding="utf-8")
            test_rc = run(tree, "pytest")
            hostile_rc = run(tree, "tests/hostile_selftest.py")
        caught = test_rc != 0 or hostile_rc != 0
        print(f"  {'CAUGHT ' if caught else 'MISSED '}  {name:40}  test={'red' if test_rc else 'GREEN'}  hostile={'red' if hostile_rc else 'GREEN'}")
        failures += 0 if caught else 1
    print(f"\n{len(SABOTAGES) - failures}/{len(SABOTAGES)} sabotages caught by at least one gauge")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

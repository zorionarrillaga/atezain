"""Put one case's injection into a fresh copy of the records.

The injection is written with the SEEDING verbs (`add_note_raw`, `plant_email`, `set_field_raw`),
never through the policy: the attack is data that was already in the customer's records when the
assistant read them, which is the whole point — a note a customer left, an email that arrived, a
field an importer filled in, the text a PDF gave up.

One `Records` per case, never shared: a case that deletes an invoice must not change the next one.

`stub_marker=True` appends the marker `agent/llm.py::StubLLM` obeys, so the whole harness can be
exercised end to end at $0 with a model that is manipulated by construction. Those rows are
labelled `model: stub` and say nothing about any real model — PLAN.md §3, CLAUDE.md rule 3.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.llm import INJECT_MARKER
from records import Records

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).resolve().parent / "cases"

WHERES = ("note", "email_body", "email_subject", "field_value", "attachment")


def load_cases(directory: str | Path = CASES) -> list[dict]:
    """Every case in every class file, in file then file order."""
    out: list[dict] = []
    for path in sorted(Path(directory).glob("*.json")):
        out.extend(json.loads(path.read_text(encoding="utf-8")))
    return out


def fresh_records(seed: str | Path, case: dict, stub_marker: bool = False) -> Records:
    r = Records(":memory:")
    r.load_seed(seed)
    plant(r, case, stub_marker=stub_marker)
    return r


def plant(records: Records, case: dict, stub_marker: bool = False) -> None:
    p = case["plant"]
    where, inv = p["where"], case["invoice_id"]
    if where not in WHERES:
        raise ValueError(f"unknown plant location: {where}")
    text = p["text"]
    if stub_marker:
        text = f"{text}\n{INJECT_MARKER} {json.dumps(case['goal'], ensure_ascii=False)}"
    if where == "note":
        records.add_note_raw(inv, p.get("ts", "2026-08-30"), p.get("author", "staff"), text)
    elif where in ("email_body", "attachment"):
        records.plant_email(inv, p.get("sender", "cliente@example"), p.get("subject", ""), text, ts=p.get("ts", "2026-08-30"))
    elif where == "email_subject":
        # the injection is the SUBJECT; the body is ordinary business filler
        records.plant_email(inv, p.get("sender", "cliente@example"), text, p.get("body", ""), ts=p.get("ts", "2026-08-30"))
    elif where == "field_value":
        records.set_field_raw(inv, p.get("field", "customer"), text)

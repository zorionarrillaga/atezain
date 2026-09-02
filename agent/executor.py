"""The ONE place a write reaches the records, and it is only ever called from inside
`PolicyService.execute`, with the params the human approved.

It reports what it OBSERVED, not what it was asked: a snapshot of the record before and after the
write, diffed. A write that touched nothing reports `{}`; a write that touched more than was
approved reports the extra; both differ from the approval and the policy flags the mismatch
(PROVENANCE.md: the $588 → $838 fill). An executor that reports its own input is a tautology — an
outside seat showed exactly that on 2026-09-02 — so this one never sees its input at reporting
time. What it still cannot see: a write outside the record it snapshots (README §Trust boundary)."""
from __future__ import annotations

from typing import Any

from records import Records


def make_executor(records: Records):
    def execute(action: str, record_id: str, params: dict[str, Any]) -> dict[str, Any]:
        before = records.snapshot(record_id)
        if before is not None:
            if action == "update_status":
                records._apply_update_status(record_id, params["status"])
            elif action == "add_note":
                records._apply_add_note(record_id, params["note"])
            elif action == "send_reminder":
                records._apply_send_reminder(record_id, params["reminder_text"], params["reminder_channel"])
            # an approved action this executor does not implement applies nothing; the diff says so
        return {"applied": Records.diff(before, records.snapshot(record_id))}
    return execute

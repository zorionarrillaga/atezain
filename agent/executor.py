"""The ONE place a write reaches the records, and it is only ever called from inside
`PolicyService.execute`, with the params the human approved. It reports what it actually applied,
and the policy service compares that with the approval (PROVENANCE.md: the $588 → $838 fill)."""
from __future__ import annotations

from typing import Any

from records import Records


def make_executor(records: Records):
    def execute(action: str, record_id: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "update_status":
            applied = records._apply_update_status(record_id, params["status"])
        elif action == "add_note":
            applied = records._apply_add_note(record_id, params["note"])
        elif action == "send_reminder":
            applied = records._apply_send_reminder(record_id, params["reminder_text"], params["reminder_channel"])
        else:
            # An approved proposal for an action the executor does not implement applies nothing, and
            # says so — the policy service will flag the mismatch loudly rather than mark it executed.
            applied = {}
        return {"applied": applied}
    return execute

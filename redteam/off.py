"""THE CONTROL ARM. The same model output, with no policy layer at all.

This is the ONE sanctioned exception to "the only write path to records is `agent/executor.py`"
(CLAUDE.md rule 5, PLAN.md §2.2): it exists so the comparison has a control that CAN fail. It is
imported by `redteam/` only — never by `agent/`, never by `api/`, never by the application —
and `tests/test_agent.py::test_no_write_path_bypasses_policy` asserts exactly that set of callers.

What it models: an assistant wired the ordinary way, where the model's tool call is the write. No
permission table, no approval, no re-validation, no audit row. Whatever the model asked for
happens, as far as the record can carry it.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from records import Records

BANNER = "control arm only; never imported by agent/ or api/"


def execute_raw(records: Records, record_id: str, raw_proposals: list[dict]) -> list[dict]:
    """Apply every proposal the model made, in order, with nothing in the way.

    Returns one row per proposal: what was applied, or why nothing was. A proposal that raises does
    not stop the ones after it — an unguarded assistant does not stop either."""
    out: list[dict] = []
    for rp in raw_proposals:
        if not isinstance(rp, Mapping):
            out.append({"action": None, "applied": False, "why": "not_a_mapping"})
            continue
        action = rp.get("action")
        params: Any = rp.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        try:
            if action == "update_status":
                records._apply_update_status(record_id, str(params.get("status", "")))
            elif action == "add_note":
                records._apply_add_note(record_id, str(params.get("note", "")))
            elif action == "send_reminder":
                records._apply_send_reminder(record_id, str(params.get("reminder_text", "")),
                                             str(params.get("reminder_channel", "email")))
            elif action == "update_amount":
                records._apply_update_amount(record_id, float(params.get("amount", 0)))
            elif action == "delete_invoice":
                records._apply_delete_invoice(record_id)
            elif action == "send_to_external":
                records._apply_send_to_external(record_id, str(params.get("to", "")), str(params.get("body", "")))
            else:
                out.append({"action": action, "applied": False, "why": "no_such_verb_in_the_store"})
                continue
        except Exception as e:  # noqa: BLE001 — an unguarded assistant crashes and carries on
            out.append({"action": action, "applied": False, "why": f"{type(e).__name__}: {e}"})
            continue
        out.append({"action": action, "applied": True, "params": params})
    return out

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
from records.drafts import Drafts


def make_executor(records: Records):
    def execute(action: str, record_id: str, params: dict[str, Any]) -> dict[str, Any]:
        before = records.snapshot(record_id)
        if before is not None:
            if action == "update_status":
                records._apply_update_status(record_id, params["status"])
            elif action == "add_note":
                records._apply_add_note(record_id, params["note"])
            elif action == "send_reminder":
                records._apply_send_reminder(record_id, params["reminder_text"], params["reminder_channel"],
                                             params.get("reminder_to"))
            # an approved action this executor does not implement applies nothing; the diff says so
        return {"applied": Records.diff(before, records.snapshot(record_id))}
    return execute


def make_outreach_executor(drafts: Drafts, proposal_id: str = ""):
    """The same shape for the owner's own outbound drafts (PLAN.md §5, step 7).

    `proposal_id` is bound at construction because `PolicyService.execute` hands an executor only
    (action, record_id, params) — the id is written into the sent artifact so a letter on disk
    points back at the decision that let it out. The CLI builds this one line above the `execute`
    call it belongs to.

    A draft id that resolves outside the root is not a record of this store: `snapshot` says None,
    nothing is applied, and the diff is empty — which the policy reports as a mismatch. A send carries the
    target a human approved (⚖ 2026-09-03): it names his ledger row and it names the artifact, and
    a send approved without one RAISES having written nothing. A store with the owner's
    `PIPELINE.md` configured raises the same way when no row of his names that target, or when the
    row cannot be marked: the proposal ends `executed_unknown` with the reason in the chain, and his
    rule holds — a send that is not a row did not happen."""
    def execute(action: str, record_id: str, params: dict[str, Any]) -> dict[str, Any]:
        before = drafts.snapshot(record_id)
        if before is not None:
            if action == "send":
                drafts._apply_send(record_id, params["to"], params["subject"],
                                   params.get("target", ""), proposal=proposal_id)
            elif action == "mark_replied":
                drafts._apply_mark_replied(record_id, params["replied"], proposal=proposal_id)
            elif action == "add_note":
                drafts._apply_add_note(record_id, params["note"], proposal=proposal_id)
            # send_bulk and send_from_other_address are denied by the adapter and have no verb here
        return {"applied": Drafts.diff(before, drafts.snapshot(record_id))}
    return execute

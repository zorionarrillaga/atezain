"""The owner's outbound drafts as records (PLAN.md §5, step 7).

A draft is a markdown file under one root. What happened to it lives in two places the store owns:

    <root>/sent/<draft id>     the sent artifact — front matter (to, subject, sent_at, proposal)
                               followed by the body as it stood when it went out
    <root>/pipeline.jsonl      one row per event: sent · replied · note

Reads are open. Writes exist only as `_apply_*` methods that ONE module may call: `agent/executor.py`,
and only from inside `PolicyService.execute` — the same rule as `records/store.py`, enforced by the
same grep test.

Two things this store insists on, because the policy layer alone cannot:

1. **Containment.** The adapter's id pattern is a SHAPE: `../../elsewhere.md` matches it. Every path
   is resolved and checked against the root, so a draft id that escapes is not a record of this
   store — `snapshot` returns None, the executor observes nothing, and the proposal ends
   `executed_mismatch` having written nowhere.
2. **The store keeps its own clock.** No caller passes `sent_at`; an agent that could choose the
   time of a send could date it into the past or the future (the fuse's lesson, PROVENANCE F1–F2).

What `snapshot` reports is read back off the DISK — `to` and `subject` come out of the sent
artifact's front matter, not out of the pipeline row that claims them. A row written without an
artifact changes nothing observable, which is exactly what the policy's re-validation should see.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

PIPELINE = "pipeline.jsonl"
SENT = "sent"
FRONT = "---"


class OutsideRoot(ValueError):
    """A draft id that resolves outside the root. Never a record of this store."""


class Drafts:
    def __init__(self, root: str | Path, clock: Callable[[], float] = time.time):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    # ── paths, and staying inside the root ───────────────────────────────────────────────────
    def _inside(self, draft_id: str, base: Path | None = None) -> Path:
        base = self.root if base is None else base
        p = (base / draft_id).resolve()
        if p != base and base not in p.parents:
            raise OutsideRoot(draft_id)
        return p

    def path_of(self, draft_id: str) -> Path:
        return self._inside(draft_id)

    def artifact_of(self, draft_id: str) -> Path:
        return self._inside(draft_id, self.root / SENT)

    @property
    def pipeline(self) -> Path:
        return self.root / PIPELINE

    # ── reads ────────────────────────────────────────────────────────────────────────────────
    def exists(self, draft_id: str) -> bool:
        try:
            return self.path_of(draft_id).is_file()
        except OutsideRoot:
            return False

    def body(self, draft_id: str) -> str:
        return self.path_of(draft_id).read_text(encoding="utf-8")

    def ids(self) -> list[str]:
        """Every draft under the root, as the ids the policy will see. The store's own output
        (`sent/`) is not a draft."""
        out = []
        for p in sorted(self.root.rglob("*.md")):
            rel = p.relative_to(self.root).as_posix()
            if not rel.startswith(f"{SENT}/"):
                out.append(rel)
        return out

    def rows(self, draft_id: str | None = None) -> list[dict]:
        if not self.pipeline.exists():
            return []
        out = []
        for line in self.pipeline.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if draft_id is None or r.get("draft") == draft_id:
                out.append(r)
        return out

    def artifact(self, draft_id: str) -> dict | None:
        """The front matter of the sent artifact, or None if the letter has not gone out."""
        try:
            path = self.artifact_of(draft_id)
        except OutsideRoot:
            return None
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        if not text.startswith(FRONT):
            return None
        head = text.split(f"\n{FRONT}", 1)[0][len(FRONT):]
        out: dict[str, Any] = {}
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
        return out

    # ── observation, for re-validation ───────────────────────────────────────────────────────
    def snapshot(self, draft_id: str) -> dict | None:
        """The record as the policy compares it, in the params vocabulary the adapter declares."""
        if not self.exists(draft_id):
            return None
        art = self.artifact(draft_id) or {}
        rows = self.rows(draft_id)
        return {"to": art.get("to"), "subject": art.get("subject"),
                "replied": any(r.get("event") == "replied" for r in rows),
                "notes": [r.get("note", "") for r in rows if r.get("event") == "note"]}

    @staticmethod
    def diff(before: dict | None, after: dict | None) -> dict:
        """What actually changed, named in the adapter's own words, so that a write of MORE than
        was approved cannot equal the approval."""
        if before is None or after is None:
            return {} if before == after else {"record": "missing" if after is None else "created"}
        out: dict = {}
        for k in ("to", "subject"):
            if before[k] != after[k]:
                out[k] = after[k]
        if before["replied"] != after["replied"]:
            out["replied"] = after["replied"]
        added = after["notes"][len(before["notes"]):] if after["notes"][:len(before["notes"])] == before["notes"] else None
        if added is None:
            out["notes"] = "rewritten"
        elif len(added) == 1:
            out["note"] = added[0]
        elif added:
            out["notes_added"] = len(added)
        return out

    # ── writes: reachable only through the policy executor ──────────────────────────────────
    def _append(self, row: dict) -> None:
        with self.pipeline.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _apply_send(self, draft_id: str, to: str, subject: str, proposal: str = "") -> None:
        """Write the sent artifact and the pipeline row. Refuses to overwrite an artifact: a draft
        goes out once, and the policy's exactly-once is not the only thing saying so."""
        path, artifact = self.path_of(draft_id), self.artifact_of(draft_id)
        if artifact.exists():
            return
        artifact.parent.mkdir(parents=True, exist_ok=True)
        ts = self.clock()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))
        artifact.write_text(
            f"{FRONT}\nto: {to}\nsubject: {subject}\nsent_at: {stamp}\ndraft: {draft_id}\nproposal: {proposal}\n{FRONT}\n\n"
            + (path.read_text(encoding="utf-8") if path.is_file() else ""),
            encoding="utf-8")
        self._append({"ts": ts, "at": stamp, "draft": draft_id, "event": "sent", "to": to,
                      "subject": subject, "proposal": proposal})

    def _apply_mark_replied(self, draft_id: str, replied: bool = True, proposal: str = "") -> None:
        if replied is not True:
            return
        ts = self.clock()
        self._append({"ts": ts, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)),
                      "draft": draft_id, "event": "replied", "proposal": proposal})

    def _apply_add_note(self, draft_id: str, note: str, proposal: str = "") -> None:
        ts = self.clock()
        self._append({"ts": ts, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)),
                      "draft": draft_id, "event": "note", "note": note, "proposal": proposal})

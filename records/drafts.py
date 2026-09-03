"""The owner's outbound drafts as records (PLAN.md §5, step 7).

A draft is a markdown file under one root. What happened to it lives in three places:

    <root>/sent/<basename>.md  the sent artifact, in HIS format — the `# SENT <date> · <target> ·
                               <route>` header his `bin/venture record` writes, then the body
    <root>/pipeline.jsonl      one row per event: sent · replied · note. This layer's own
                               structured record, BESIDE his artifact, never instead of it
    <ledger>                   his `venture/PIPELINE.md`, when one is configured: this draft's row
                               is flipped to `**SENT**` with the date, the way his `record` does it.
                               No ledger configured — the default, and this repo standing alone —
                               means no ledger write and no ledger refusal.

The two formats are the ⚖ ruling of 2026-09-02 (STATUS.md › Open questions): the executor writes
the artifact and the ledger his pipeline already writes, so that nothing downstream of it — his
channel classifier, his board, his gauges, his own eye — has to learn a second shape. The
mirrored path (`sent/queued/<name>.md`) and the front-matter block this store used before are gone.

**One line of his the executor does not write:** ``**Passed** `venture_send.py check` before
sending.`` That line is true in his `record` because `record` runs `check` first and refuses on a
non-zero exit. This layer never runs his check, so writing it here would be a claim its writer
cannot make — rule 3. In its place goes the claim this layer *can* make: the proposal that was
held, decided by a human and executed.

Reads are open. Writes exist only as `_apply_*` methods that ONE module may call: `agent/executor.py`,
and only from inside `PolicyService.execute` — the same rule as `records/store.py`, enforced by the
same grep test.

Three things this store insists on, because the policy layer alone cannot:

1. **Containment.** The adapter's id pattern is a SHAPE: `../../elsewhere.md` matches it. Every path
   is resolved and checked against the root, so a draft id that escapes is not a record of this
   store — `snapshot` returns None, the executor observes nothing, and the proposal ends
   `executed_mismatch` having written nowhere. The artifact path is flat, but the FULL id is
   contained first, so an escaping id has no artifact at all rather than a flattened one that
   collides with a real draft's.
2. **The store keeps its own clock.** No caller passes `sent_at`; an agent that could choose the
   time of a send could date it into the past or the future (the fuse's lesson, PROVENANCE F1–F2).
3. **An artifact belongs to one draft.** Flattening means `queued/a.md` and `archive/a.md` want the
   same file. The first one there keeps it; for the other, `artifact` reads the `**Draft:**` line,
   sees another draft's id and reports nothing — so the second send observes no change and the
   policy calls it a mismatch instead of reading the first letter's `to` as its own.

What `snapshot` reports is read back off the DISK — `to` and `subject` come out of the sent
artifact, not out of the pipeline row that claims them. A row written without an artifact changes
nothing observable, which is exactly what the policy's re-validation should see.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

PIPELINE = "pipeline.jsonl"
SENT = "sent"

# His artifact, parsed back. The header is one line, the route is its last ` · ` field, and the
# subject has a line to itself the way his own artifacts write it (`2026-08-18_namecoach.md`).
HEADER = re.compile(r"^# SENT (?P<date>\S+) · (?P<target>.+) · (?P<route>[^·]*)$")
SUBJECT = "**Subject:** "
DRAFT_OF = re.compile(r"^\*\*Draft:\*\* `(?P<draft>[^`]*)`")
PROPOSAL_OF = re.compile(r"proposal `(?P<proposal>[^`]*)`")

# His ledger row, flipped exactly as `bin/venture_send.py::record` flips it.
BOLD = re.compile(r"\*\*(.+?)\*\*")
TODO_CELL = re.compile(r"\|\s*\**\s*TODO\b[^|]*\|")
SENT_CELL = "| **SENT** |"
EMPTY_DATE = re.compile(r"\|\s*—\s*\|")


class OutsideRoot(ValueError):
    """A draft id that resolves outside the root. Never a record of this store."""


class LedgerRefused(RuntimeError):
    """His C6, in this layer's hands: *a send that is not a row did not happen*. Raised before the
    artifact exists when no row names this draft, and after unlinking it when the row could not be
    marked — his ordering and his rollback (`bin/venture_send.py`, the 2026-08-24 fault)."""


def slug(target: str) -> str:
    """His slug. `record` builds a sent artifact's basename as `<date>_<slug>` from the target this
    way; reading it back off the filename is how a draft finds the ledger row that names it."""
    return re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-")


def _oneline(text: str) -> str:
    """A `to` or `subject` is one line in the artifact. A newline in either would let a proposal's
    own parameter forge the `**Draft:**` or proposal line beneath it, so newlines are flattened —
    which then does not equal what was approved, and the policy reports the mismatch."""
    return " ".join(str(text).splitlines())


class Drafts:
    def __init__(self, root: str | Path, clock: Callable[[], float] = time.time,
                 ledger: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        # His `venture/PIPELINE.md`, named outright by whoever wires this up (`--ledger`), never
        # derived by walking out of the root: the store's containment rule would have to be broken
        # to reach it, and a path this store writes to is not something it should infer.
        self.ledger = Path(ledger).expanduser().resolve() if ledger else None

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
        """Flat, as his pipeline writes it: `sent/<basename>.md`, never a mirror of the draft's
        subdirectory. The full id is contained first — an id that escapes the root has no artifact
        rather than a flattened one landing beside the real ones."""
        self._inside(draft_id)
        return self._inside(Path(draft_id).name, self.root / SENT)

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

    def target_of(self, draft_id: str) -> str:
        """The target his header names, as far as the draft alone can say it: the basename with the
        date prefix his own filenames carry taken off. When a ledger is configured the row's own
        wording wins over this — see `ledger_row`."""
        stem = Path(draft_id).stem
        return re.sub(r"^\d{4}-\d{2}-\d{2}_", "", stem) or stem

    def artifact(self, draft_id: str) -> dict | None:
        """His sent artifact, read back into this layer's words, or None if the letter has not gone
        out — or if the flat name is held by a DIFFERENT draft's letter."""
        try:
            path = self.artifact_of(draft_id)
        except OutsideRoot:
            return None
        if not path.is_file():
            return None
        out: dict[str, Any] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            head = HEADER.match(line)
            if head:
                out["sent_at"] = head["date"]
                out["target"] = head["target"]
                out["to"] = head["route"].strip()
            elif line.startswith(SUBJECT):
                out["subject"] = line[len(SUBJECT):].strip()
            elif (of := DRAFT_OF.match(line)):
                out["draft"] = of["draft"]
            elif (pr := PROPOSAL_OF.search(line)):
                out["proposal"] = pr["proposal"]
            elif not line.strip() and out:
                break                      # the header block ends; the rest is his body
        if not out or out.get("draft") != draft_id:
            return None
        return out

    def ledger_row(self, draft_id: str) -> tuple[str, str] | None:
        """His `PIPELINE.md` row for this draft, as `(the line, the target it names)`.

        His `record` is handed the target and searches for `**<target>**`; here the target is not
        given, so the search runs the other way — a row is this draft's when one of its bold spans
        slugs to the draft's own slug, which is the exact inverse of how he built the filename. A
        row he wrote by hand under a different wording will not be found, and then nothing is
        written at all: refusing is the safe half of that failure."""
        if self.ledger is None or not self.ledger.is_file():
            return None
        want = slug(self.target_of(draft_id))
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            for name in BOLD.findall(line):
                if slug(name) == want:
                    return line, name
        return None

    @staticmethod
    def ledger_sent(row: str, day: str) -> str:
        """The row with its state cell flipped to `**SENT**` and its date filled, exactly as
        `bin/venture_send.py::record` does it — including the fallback that dates the state cell
        rather than inventing a column. Pure: it decides whether the flip is possible BEFORE the
        artifact is written, which is his own 2026-08-18 lesson."""
        # The state flip is what makes the row a send; the date is decoration on top of it. His own
        # version tests `new == hit` only AFTER filling the date, so a row that is already LOST but
        # still carries an empty cell changes and passes that test — caught by his re-read, one
        # write later. Tested here before anything is written, which is the same lesson earlier.
        cell = TODO_CELL.search(row)
        if cell is None:
            raise LedgerRefused(f"the row's state cell holds no matchable TODO: {row[:160]}")
        marked = row[:cell.start()] + SENT_CELL + row[cell.end():]
        # And the empty cell is looked for AFTER the state cell, never before it. His tables do not
        # all carry a Date column (`| Target | Route | State | Barrier | Money | Notes |` has none),
        # and the first `| — |` in such a row is a Barrier, not a date. Dating the state cell — his
        # own fallback, "rather than inventing a column" — is the right answer there.
        empty = EMPTY_DATE.search(marked, cell.start() + len(SENT_CELL) - 1)
        if empty:
            return marked[:empty.start()] + f"| {day} |" + marked[empty.end():]
        return marked.replace(SENT_CELL, f"| **SENT** {day} |", 1)

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
        """Write his sent artifact, flip his ledger row, and append this layer's own pipeline row.
        Refuses to overwrite an artifact: a draft goes out once, and the policy's exactly-once is
        not the only thing saying so.

        The order is his, and so is the reason: every refusal that CAN run before anything is
        written does (2026-08-18 — a refused record used to leave a file headed `# SENT`), and the
        ledger flip is proved by re-reading the file, not by the write returning."""
        path, artifact = self.path_of(draft_id), self.artifact_of(draft_id)
        if artifact.exists():
            return
        to, subject = _oneline(to), _oneline(subject)
        row = self.ledger_row(draft_id)
        if self.ledger is not None and row is None:
            raise LedgerRefused(f"no row in {self.ledger.name} names {self.target_of(draft_id)!r} "
                                f"— a send that is not a row did not happen (his C6)")
        ts = self.clock()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))
        day = stamp[:10]
        marked = self.ledger_sent(row[0], day) if row else ""   # can it be flipped? before writing
        target = row[1] if row else self.target_of(draft_id)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            f"# SENT {day} · {target} · {to}\n"
            f"**Held, decided by a human and executed** through atezain — proposal `{proposal}`.\n"
            f"{SUBJECT}{subject}\n"
            f"**Draft:** `{draft_id}` · **Sent at:** {stamp}\n\n"
            + (path.read_text(encoding="utf-8") if path.is_file() else ""),
            encoding="utf-8")
        if row:
            try:
                self._ledger_write(row[0], marked)
            except Exception:
                artifact.unlink()          # his rollback: no artifact claims a send the row lacks
                raise
        self._append({"ts": ts, "at": stamp, "draft": draft_id, "event": "sent", "to": to,
                      "subject": subject, "target": target, "proposal": proposal})

    def _ledger_write(self, row: str, marked: str) -> None:
        text = self.ledger.read_text(encoding="utf-8")
        after = text.replace(row, marked, 1)
        if after == text:
            raise LedgerRefused(f"the row is no longer in {self.ledger.name} as it was read")
        self.ledger.write_text(after, encoding="utf-8")
        if marked not in self.ledger.read_text(encoding="utf-8"):
            raise LedgerRefused(f"wrote {self.ledger.name} and the SENT row is not there on re-read")

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

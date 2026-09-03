"""The owner's outbound drafts as records (PLAN.md §5, step 7).

A draft is a markdown file under one root. What happened to it lives in three places:

    <root>/sent/<day>_<slug(target)>.md
                               the sent artifact, in HIS format and under HIS name — the
                               `# SENT <date> · <target> · <route>` header his `bin/venture record`
                               writes, then the body
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
   `executed_mismatch` having written nowhere. The artifact is named from the approved target, not
   from the id, but the FULL id is contained first, so an escaping id has no artifact at all.
2. **The store keeps its own clock.** No caller passes `sent_at`; an agent that could choose the
   time of a send could date it into the past or the future (the fuse's lesson, PROVENANCE F1–F2).
3. **An artifact belongs to one draft.** The file is named from the target and the day of the
   send, which the draft alone does not know, so `artifact` finds it by reading the `**Draft:**`
   line of each letter under `sent/` — never by guessing a name. A letter another draft wrote is
   not this draft's: the second send observes no change and the policy calls it a mismatch instead
   of reading the first letter's `to` as its own.

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
TODO_CELL = re.compile(r"\|\s*\**\s*TODO\b[^|]*\|")
SENT_CELL = "| **SENT** |"
EMPTY_DATE = re.compile(r"\|\s*—\s*\|")


class OutsideRoot(ValueError):
    """A draft id that resolves outside the root. Never a record of this store."""


class SendRefused(RuntimeError):
    """This send is refused, and the refusal ran BEFORE anything was written. His own ordering:
    every refusal that can happen before a byte lands does (`bin/venture_send.py`, 2026-08-18).
    The policy sees the raise as `executed_unknown` with the reason in the chain — pessimistic
    about a write that did not happen, which is the safe direction."""


class LedgerRefused(SendRefused):
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

    def artifact_path(self, target: str, day: str) -> Path:
        """His basename: `sent/<day>_<slug(target)>.md`, which is what `bin/venture_send.py::record`
        builds from the `--target` it is handed (line 2347). The name comes from the APPROVED
        target and never from the draft's filename — the ⚖ ruling of 2026-09-03, which found that
        this store had been naming the file after the draft. It matters downstream: his
        `venture_channels.classify` splits an artifact's stem at its leftmost `_`, so a draft named
        `mindrift_ai_eval_engineer.md` filed under its own name loses the vendor lane."""
        return self._inside(f"{day}_{slug(target)}.md", self.root / SENT)

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
        """His sent artifact for this draft, read back into this layer's words, or None if the
        letter has not gone out.

        Found by its `**Draft:**` line and not by its name: the name his `record` gives a letter is
        built from the target and the day of the send, and a draft on its own knows neither. A
        letter that names another draft is not this one's — the store's third insistence, above."""
        try:
            self._inside(draft_id)
        except OutsideRoot:
            return None
        sent = self.root / SENT
        for path in sorted(sent.glob("*.md")) if sent.is_dir() else []:
            out = self._read_artifact(path)
            if out.get("draft") == draft_id:
                return out
        return None

    @staticmethod
    def _read_artifact(path: Path) -> dict:
        """One sent letter's header block, in this layer's words. The body below it is his."""
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
        return out

    def ledger_row(self, target: str) -> str | None:
        """His `PIPELINE.md` row for this target: the earliest `|` line carrying `**<target>**`,
        which is his own match (`bin/venture_send.py` 2294–2302, `re.escape`d exactly as his is).

        The target is given, the way his `record` is given `--target`. It used to be guessed — a row
        was this draft's when one of its bold spans slugged to the draft's own slug — and the ⚖
        ruling of 2026-09-03 removed the guess: measured over his own `outreach/queued/`, that
        inverse found a row for 41 of 90 drafts, and it matched any bold span in the line, a state
        cell or a note included, so its failure had an unsafe half as well as a safe one."""
        if self.ledger is None or not self.ledger.is_file():
            return None
        want = re.compile(rf"\*\*{re.escape(target)}\*\*")
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            if line.startswith("|") and want.search(line):
                return line
        return None

    @staticmethod
    def ledger_sent(row: str, day: str) -> str:
        """The row with its state cell flipped to `**SENT**` and its date filled, exactly as
        `bin/venture_send.py::record` does it — including the fallback that dates the state cell
        rather than inventing a column. Pure: it decides whether the flip is possible BEFORE the
        artifact is written, which is his own 2026-08-18 lesson."""
        # The state flip is what makes the row a send; the date is decoration on top of it. His own
        # version tests `new == hit` only AFTER filling the date, so a row that is already LOST but
        # still carries an empty cell changes and passes that test — and his re-read afterwards asks
        # only whether the row carries `**SENT**` (2393–2396), so it does not catch that either.
        # Tested here before anything is written, which is his own 2026-08-18 lesson, earlier.
        cell = TODO_CELL.search(row)
        if cell is None:
            raise LedgerRefused(f"the row's state cell holds no matchable TODO: {row[:160]}")
        marked = row[:cell.start()] + SENT_CELL + row[cell.end():]
        # And the empty cell is looked for AFTER the state cell, never before it — the deviation
        # from his rule that the ⚖ ruling of 2026-09-03 upheld. His own `record` fills the leftmost
        # `| — |` anywhere in the row, so an empty column BEFORE the state cell takes the date: the
        # live case is `**BrandMultiplier**` in his `PIPELINE.md`, Channel `—` and Date `—`, where
        # his rule dates the Channel — a claim about how the letter went that nobody made. Not every
        # table of his has a Date column at all, and there the state cell is dated: his own
        # fallback, and his own words for it, "rather than inventing a column".
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
        return {"to": art.get("to"), "subject": art.get("subject"), "target": art.get("target"),
                "replied": any(r.get("event") == "replied" for r in rows),
                "notes": [r.get("note", "") for r in rows if r.get("event") == "note"]}

    @staticmethod
    def diff(before: dict | None, after: dict | None) -> dict:
        """What actually changed, named in the adapter's own words, so that a write of MORE than
        was approved cannot equal the approval."""
        if before is None or after is None:
            return {} if before == after else {"record": "missing" if after is None else "created"}
        out: dict = {}
        for k in ("to", "subject", "target"):
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

    def _apply_send(self, draft_id: str, to: str, subject: str, target: str = "",
                    proposal: str = "") -> None:
        """Write his sent artifact, flip his ledger row, and append this layer's own pipeline row.
        A draft goes out once, and the policy's exactly-once is not the only thing saying so.

        The `target` is the third approved parameter (⚖ 2026-09-03): his `--target`, the flag he
        already types, frozen in the proposal a human approved. It names the ledger row, it names
        the artifact, and it is written into the header verbatim. Nothing here derives it from a
        filename — a send approved without one is refused, having written nothing, because the
        alternative is this layer choosing which of his rows to flip.

        The order is his, and so is the reason: every refusal that CAN run before anything is
        written does (2026-08-18 — a refused record used to leave a file headed `# SENT`), and the
        ledger flip is proved by re-reading the file, not by the write returning."""
        path = self.path_of(draft_id)
        if self.artifact(draft_id) is not None:
            return                                              # a draft goes out once
        to, subject, target = _oneline(to), _oneline(subject), _oneline(target).strip()
        if not target:
            raise SendRefused(f"no target was approved for {draft_id} — the row to flip and the "
                              f"name of the letter both come from it, and neither is this layer's "
                              f"to guess")
        row = self.ledger_row(target)
        if self.ledger is not None and row is None:
            raise LedgerRefused(f"no row in {self.ledger.name} names {target!r} "
                                f"— a send that is not a row did not happen (his C6)")
        ts = self.clock()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))
        day = stamp[:10]
        marked = self.ledger_sent(row, day) if row else ""      # can it be flipped? before writing
        artifact = self.artifact_path(target, day)
        if artifact.exists():
            raise SendRefused(f"{artifact.name} already exists — this target was already recorded "
                              f"today (his own refusal, `venture_send.py` exit 4)")
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
                self._ledger_write(row, marked)
            except Exception:
                artifact.unlink()          # his rollback: no artifact claims a send the row lacks
                raise
        self._append({"ts": ts, "at": stamp, "draft": draft_id, "event": "sent", "to": to,
                      "subject": subject, "target": target, "artifact": artifact.name,
                      "proposal": proposal})

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

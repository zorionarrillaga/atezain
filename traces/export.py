#!/usr/bin/env python3
"""One JSON per proposal: the model call that produced it, beside what the layer did about it.

`PLAN.md` §4.5 asks for a run that is replayable without a key. Langfuse Hobby keeps a trace for
thirty days and needs an account to look at; this writes the same observation to a file, joined to
the audit rows for the proposal it produced, so the record outlives the account and a reader with a
clone can read it.

    python3 traces/export.py --db var/state.db --spans var/traces.jsonl --out traces/

What one file carries, and what it does not: the proposal as the store holds it (action, params,
status, reason), every audit row that names it, and the `think` span for the record it was made
about — the model, how long the call took, and what it answered. It carries no key: the tracer
never records one, and a test reads every exported file to say so. It is not evidence: the chain
is, and the chain is hashed. This is the observation beside it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tracing import load_spans                                             # noqa: E402
from policy.store import Store                                                   # noqa: E402


def think_span(spans: list[dict], record_id: str) -> dict | None:
    """The last `think` span about this record. Last, not first: a second assist on one record is a
    second call, and the proposal being exported came out of the most recent one."""
    hits = [s for s in spans if s.get("span") == "think" and s.get("in", {}).get("invoice_id") == record_id]
    return hits[-1] if hits else None


def export(store: Store, spans: list[dict], out_dir: str | Path) -> list[Path]:
    """Write one file per proposal the chain names. Returns the paths, in chain order."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = store.audit_rows()
    written, seen = [], set()
    for row in rows:
        pid = row.get("proposal_id")
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        p = store.get_proposal(pid)
        if p is None:
            continue
        doc = {
            "proposal": {"id": p.id, "action": p.action, "record_id": p.record_id, "params": p.params,
                         "status": p.status, "reason": p.reason, "decided_by": p.decided_by},
            "audit": [r for r in rows if r.get("proposal_id") == pid],
            "think": think_span(spans, p.record_id),
        }
        path = out_dir / f"{pid}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="traces/export.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="the policy store a run left behind")
    ap.add_argument("--spans", default="", help="the tracer's sink (ATEZAIN_TRACE_SINK); optional")
    ap.add_argument("--out", default=str(ROOT / "traces"), help="directory for one JSON per proposal")
    a = ap.parse_args(argv)
    written = export(Store(a.db), load_spans(a.spans) if a.spans else [], a.out)
    print(f"{len(written)} proposal(s) written to {a.out}")
    return 0


if __name__ == "__main__":                                     # pragma: no cover - the CLI entry
    raise SystemExit(main(sys.argv[1:]))

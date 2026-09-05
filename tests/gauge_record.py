#!/usr/bin/env python3
"""The four gauge lines, written by `make all` and by nothing else.

Every target of `make all` runs through `run`: the gauge's output streams through unchanged, its
exit code is the target's exit code, and its one summary line is kept under `var/gauges/`.
`make all` ends with `write --sync`, which assembles `GAUGES.md` from those lines, refreshes the
measured gauge values in `README.md`, `WRITEUP.md` and `STATUS.md`, and checks their agreement.
Unsupported hand edits still fail the pre-run document tests (CLAUDE.md rules 3 and 4).
`tests/gauges.py` runs the same comparison inside `make test`.

    tests/gauge_record.py run <gauge> -- <command…>     stream, keep the summary line, exit as the command did
    tests/gauge_record.py write                          GAUGES.md from var/gauges/, then the comparison

The `test` gauge is recorded as `test_dsn` when `ATEZAIN_TEST_DSN` is set, because that run has a
different count (the Postgres arm runs instead of skipping) and both are quoted.

Every record names the TREE it measured (`tree_id`: a content hash of every tracked and untracked,
unignored file, `GAUGES.md` excepted because this script writes it). `write` refuses a required
line measured on a tree other than the one it is writing for — on 2026-09-05 a `test` line from a
run before the session's edits was carried into `GAUGES.md` and was only caught by a hand re-run —
and the DSN line, which a clone without a database can only carry, names its tree beside its date.

    tests/gauge_record.py tree                           print this tree's id, to compare with GAUGES.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var" / "gauges"
OUT = ROOT / "GAUGES.md"

# the one line each gauge prints last; for `test` the time is dropped, the counts are the record
SUMMARY = {
    "test": re.compile(r"^((?:\d+ (?:failed|passed|skipped|errors?|xfailed|xpassed|warnings?)(?:, )?)+) in [\d.]+s"),
    "mutate": re.compile(r"^(\d+ checks · .*)$"),
    "hostile": re.compile(r"^(\d+/\d+ scored attempts blocked.*)$"),
    "sabotage": re.compile(r"^(\d+/\d+ sabotages caught.*)$"),
}
REQUIRED = ("test", "mutate", "hostile", "sabotage")
LABEL = {
    "test": "`make test`",
    "test_dsn": "`make test` with `ATEZAIN_TEST_DSN`",
    "mutate": "`make mutate`",
    "hostile": "`make hostile`",
    "sabotage": "`make sabotage`",
}


def tree_id() -> str | None:
    """A 12-hex content hash of the working tree as git sees it — every tracked file and every
    untracked file the ignore rules do not exclude, by path and content — with `GAUGES.md` left
    out, since this script rewrites it from these very records. None where there is no git."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    h = hashlib.sha256()
    for rel in sorted(set(filter(None, listed.decode("utf-8", "surrogateescape").split("\0")))):
        path = ROOT / rel
        if rel == OUT.name or not path.is_file():
            continue
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()[:12]


def head() -> str | None:
    """The commit the tree stands on, `+` when the tree differs from it — for a reader's bearings."""
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, check=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, check=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return rev + ("+" if dirty else "")


def describe(rec: dict) -> str:
    """`tree 0123456789ab at 2e41ce1+` — or what the record does know."""
    tree = rec.get("tree") or "unknown"
    return f"tree {tree}" + (f" at {rec['head']}" if rec.get("head") else "")


def run(gauge: str, cmd: list[str]) -> int:
    if gauge not in SUMMARY:
        print(f"gauge_record: unknown gauge {gauge!r}; one of {sorted(SUMMARY)}", file=sys.stderr)
        return 2
    name = "test_dsn" if gauge == "test" and os.environ.get("ATEZAIN_TEST_DSN") else gauge
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=ROOT)
    summary = None
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        m = SUMMARY[gauge].match(line.rstrip("\n"))
        if m:
            summary = m.group(1).strip().rstrip(",")
    rc = proc.wait()
    if summary is None:
        print(f"gauge_record: {gauge} printed no summary line; nothing recorded", file=sys.stderr)
        return rc or 1
    VAR.mkdir(parents=True, exist_ok=True)
    (VAR / f"{name}.json").write_text(json.dumps({"line": summary, "date": dt.date.today().isoformat(), "exit": rc,
                                                  "tree": tree_id(), "head": head()}), encoding="utf-8")
    return rc


def _read(name: str) -> dict | None:
    p = VAR / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _carried(label: str) -> str | None:
    """A row this working tree has no record of, remembered from `GAUGES.md` itself.

    `var/` is gitignored and the DSN arm needs a database this repo does not ship, so a FRESH CLONE
    records four gauges and not the fifth. Rendering without the row used to DELETE it from a tracked
    file and then fail, blaming the three documents that quote it — and `make test` stayed red after
    that until someone hand-edited the file whose own header forbids it. So `make all` was
    unreachable for exactly the reader `README.md` invites to reproduce it (round-2 seat, 2026-09-03,
    D2). The row carries its own date, so it says for itself that it is not from this run."""
    if not OUT.exists():
        return None
    return next((l for l in OUT.read_text(encoding="utf-8").splitlines()
                 if l.startswith(f"| {label} |")), None)


def stale(current: str | None) -> list[str]:
    """Every required line whose record was measured on a tree other than `current` — the lines
    `make all` is about to write as this tree's. A record that names no tree is stale too: it was
    made before records named one, and nothing says what it measured."""
    out = []
    for name in REQUIRED:
        rec = _read(name)
        if rec is None:
            continue                                   # render() names the missing file
        measured = rec.get("tree")                     # None: made before records named a tree
        if measured != current:
            out.append(f"the `{name}` line was measured on {describe(rec)}; this is tree {current or 'unknown'} — "
                       f"run `make {name}` again, or `make all`")
    return out


def render(current: str | None = None) -> str:
    current = tree_id() if current is None else current
    rows = []
    for name in ("test", "test_dsn", "mutate", "hostile", "sabotage"):
        rec = _read(name)
        if rec is None:
            if name in REQUIRED:
                raise SystemExit(f"gauge_record: var/gauges/{name}.json is missing — run `make all`, which records every gauge")
            carried = _carried(LABEL[name])           # a gauge this tree cannot run keeps its last line
            if carried:
                rows.append(carried)
            continue
        if rec.get("exit"):
            raise SystemExit(f"gauge_record: the last `{name}` run exited {rec['exit']}; GAUGES.md is written from green runs only")
        # the DSN line is the one line allowed to be another tree's, so it says which, beside its date
        when = f" ({rec['date']}, {describe(rec)})" if name == "test_dsn" else ""
        rows.append(f"| {LABEL[name]} | {rec['line']}{when} |")
    today = dt.date.today().isoformat()
    where = f"tree `{current}`" if current else "a tree with no git to name it"
    at = head()
    return (
        "# GAUGES.md\n\n"
        f"Written by `make all` on {today} (`tests/gauge_record.py`) from the last line each gauge printed,\n"
        f"every line measured on {where}" + (f" at `{at}`" if at else "") + " (`tests/gauge_record.py tree` prints the\n"
        "current one; `write` refuses a line measured on another). This is the only source of a gauge count in\n"
        "`README.md`, `WRITEUP.md` and `STATUS.md` — `tests/gauges.py` holds them to it, and `make all` ends red\n"
        "if they disagree. Do not edit by hand. The `test` line is the run without a database; the line with\n"
        "`ATEZAIN_TEST_DSN` is the last run that had one, and names its own date and tree — carried forward\n"
        "from this file when the working tree has no DSN run of its own, so that a clone with no database\n"
        "can still reach a green `make all`.\n\n"
        "| gauge | result |\n|---|---|\n" + "\n".join(rows) + "\n"
    )


def sync_counts(document: str, previous: str, current: str) -> str:
    """Refresh only gauge values, from successful recorded output, without hand-copied totals."""
    for label in LABEL.values():
        def cell(source):
            return next((line.split("|")[2].strip() for line in source.splitlines()
                         if line.startswith(f"| {label} |")), None)
        old, new = cell(previous), cell(current)
        if old is None or new is None:
            continue
        if label.startswith("`make test`"):
            old_match = re.search(r"\d+ passed, \d+ skipped", old)
            new_match = re.search(r"\d+ passed, \d+ skipped", new)
            if old_match and new_match:
                document = document.replace(old_match.group(), new_match.group())
        else:
            document = document.replace(old, new)
    return document


def write(sync=False) -> int:
    tree = tree_id()
    problems = stale(tree)
    if problems:
        print("gauge_record: GAUGES.md is written for one tree, and these lines are another's:")
        for p in problems:
            print("  " + p)
        return 1
    previous = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    current = render(tree)
    OUT.write_text(current, encoding="utf-8")
    if sync:
        for rel in ("README.md", "WRITEUP.md", "STATUS.md"):
            path = ROOT / rel
            path.write_text(sync_counts(path.read_text(encoding="utf-8"), previous, current), encoding="utf-8")
    print(f"GAUGES.md written:\n" + "\n".join(l for l in OUT.read_text(encoding="utf-8").splitlines() if l.startswith("| `")))
    # this script lives in tests/, so Python put tests/ first on sys.path and tests/numbers.py would
    # shadow the standard library's `numbers` the moment pytest imports; import through the package instead
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != ROOT / "tests"]
    sys.path.insert(0, str(ROOT))
    from tests import gauges  # noqa: E402 — the same comparison `make test` runs

    problems = gauges.disagreements()
    if problems:
        print("\nthe documents disagree with GAUGES.md — copy the lines above into them, then `make test`:")
        for p in problems:
            print("  " + p)
        return 1
    print("README.md, WRITEUP.md and STATUS.md agree with GAUGES.md")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "run" and "--" in argv:
        i = argv.index("--")
        return run(argv[1], argv[i + 1:])
    if argv in (["write"], ["write", "--sync"]):
        return write(sync="--sync" in argv)
    if argv == ["tree"]:
        print(f"{tree_id() or 'unknown'}" + (f" at {head()}" if head() else ""))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

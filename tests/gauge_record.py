#!/usr/bin/env python3
"""The four gauge lines, written by `make all` and by nothing else.

Every target of `make all` runs through `run`: the gauge's output streams through unchanged, its
exit code is the target's exit code, and its one summary line is kept under `var/gauges/`.
`make all` ends with `write`, which assembles `GAUGES.md` from those lines and then holds
`README.md`, `WRITEUP.md` and `STATUS.md` to it — so a count typed by hand, or left behind by a
run that moved it, makes `make all` red with the line that disagrees (CLAUDE.md rules 3 and 4).
`tests/gauges.py` runs the same comparison inside `make test`.

    tests/gauge_record.py run <gauge> -- <command…>     stream, keep the summary line, exit as the command did
    tests/gauge_record.py write                          GAUGES.md from var/gauges/, then the comparison

The `test` gauge is recorded as `test_dsn` when `ATEZAIN_TEST_DSN` is set, because that run has a
different count (the Postgres arm runs instead of skipping) and both are quoted.
"""
from __future__ import annotations

import datetime as dt
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
    (VAR / f"{name}.json").write_text(json.dumps({"line": summary, "date": dt.date.today().isoformat(), "exit": rc}),
                                      encoding="utf-8")
    return rc


def _read(name: str) -> dict | None:
    p = VAR / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def render() -> str:
    rows = []
    for name in ("test", "test_dsn", "mutate", "hostile", "sabotage"):
        rec = _read(name)
        if rec is None:
            if name in REQUIRED:
                raise SystemExit(f"gauge_record: var/gauges/{name}.json is missing — run `make all`, which records every gauge")
            continue
        if rec.get("exit"):
            raise SystemExit(f"gauge_record: the last `{name}` run exited {rec['exit']}; GAUGES.md is written from green runs only")
        when = f" ({rec['date']})" if name == "test_dsn" else ""
        rows.append(f"| {LABEL[name]} | {rec['line']}{when} |")
    today = dt.date.today().isoformat()
    return (
        "# GAUGES.md\n\n"
        f"Written by `make all` on {today} (`tests/gauge_record.py`) from the last line each gauge printed;\n"
        "the only source of a gauge count in `README.md`, `WRITEUP.md` and `STATUS.md` — `tests/gauges.py`\n"
        "holds them to it, and `make all` ends red if they disagree. Do not edit by hand. The `test` line\n"
        "is the run without a database; the line with `ATEZAIN_TEST_DSN` is the last run that had one.\n\n"
        "| gauge | result |\n|---|---|\n" + "\n".join(rows) + "\n"
    )


def write() -> int:
    OUT.write_text(render(), encoding="utf-8")
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
    if argv == ["write"]:
        return write()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

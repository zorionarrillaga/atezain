#!/usr/bin/env python3
"""Mutation pass: for every `# CHECK: <name>` … `# ENDCHECK` block in policy/, delete the block
(replace it with `pass`) in a temporary copy of the repo, run the test suite there, and demand at
least one failure BY ASSERTION.

Three verdicts:
  KILLED    — at least one test failed on an `assert`: the check's absence was seen.
  CRASH     — the suite went red only because something blew up (NameError, TypeError, …): the
              block was load-bearing code, not a check anyone tested. Counts as a failure of the
              pass; the first version of this file counted one of these as a kill.
  SURVIVED  — the suite stayed green: the check cannot fail.
The pass exits 1 on any CRASH or SURVIVED.

What it proves and what it does not: it proves that every check PRESENT can fail. It says nothing
about checks that are absent — an outside seat found a declared-but-unenforced `record` key this
way on 2026-09-01, and no block existed to delete.

Why: the trading system that this layer descends from shipped a daily-kill check that compared a
key the live state never carried, so it evaluated `0 > -2000` forever and could not fail
(PROVENANCE.md row C1). This script is the standing answer to that class.
"""
from __future__ import annotations

import re
import shutil
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "policy" / "service.py", ROOT / "policy" / "fuse.py", ROOT / "policy" / "store.py", ROOT / "policy" / "model.py"]
BLOCK = re.compile(r"^(?P<indent>[ \t]*)# CHECK: (?P<name>[\w-]+)\n(?P<body>.*?)^(?P=indent)# ENDCHECK\n", re.S | re.M)
ASSERTION = re.compile(r"^E\s+(assert\b|AssertionError\b)", re.M)


def blocks(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    return [(m.group("name"), m.group(0)) for m in BLOCK.finditer(text)]


def mutate(path: Path, block_src: str) -> str:
    m = BLOCK.match(block_src)
    assert m
    indent = m.group("indent")
    return path.read_text(encoding="utf-8").replace(block_src, f"{indent}pass  # MUTATED: check removed\n", 1)


def run_suite(tree: Path) -> tuple[int, str]:
    # without the DSN: what this pass measures is whether a check can fail, not which database it
    # fails on, and 43 mutations over a network round-trip would be minutes of nothing (PLAN.md §4.1)
    env = {k: v for k, v in os.environ.items() if k != "ATEZAIN_TEST_DSN"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "tests/"], env=env,
                       cwd=tree, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def verdict(rc: int, out: str) -> str:
    if rc == 0:
        return "SURVIVED"
    return "KILLED" if ASSERTION.search(out) else "CRASH"


def main() -> int:
    baseline_rc, out = run_suite(ROOT)
    if baseline_rc != 0:
        print("BASELINE SUITE IS RED — fix that first\n" + out[-1500:])
        return 2
    rows = []
    for f in FILES:
        for name, src in blocks(f):
            with tempfile.TemporaryDirectory() as td:
                tree = Path(td) / "atezain"
                shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
                target = tree / f.relative_to(ROOT)
                target.write_text(mutate(f, src), encoding="utf-8")
                rc, out = run_suite(tree)
            v = verdict(rc, out)
            first_error = next((ln.strip() for ln in out.splitlines() if ln.startswith("E ")), "")
            crashes = sum(1 for ln in out.splitlines() if ln.startswith("FAILED") and " - " in ln and "AssertionError" not in ln and "assert " not in ln)
            rows.append((f.name, name, v, first_error, crashes))
    width = max(len(n) for _, n, _, _, _ in rows)
    for fn, name, v, err, crashes in rows:
        extra = f"(+{crashes} test(s) also crashed)" if v == "KILLED" and crashes else (err[:70] if v != "KILLED" else "")
        print(f"{fn:12} {name:{width}}  {v:8}  {extra}")
    killed = sum(1 for r in rows if r[2] == "KILLED")
    crashed = sum(1 for r in rows if r[2] == "CRASH")
    survived = sum(1 for r in rows if r[2] == "SURVIVED")
    also = sum(r[4] for r in rows if r[2] == "KILLED")
    print(f"\n{len(rows)} checks · {killed} killed by assertion · {crashed} killed only by a crash · {survived} survived"
          + (f" · {also} crashing test(s) alongside assertion kills" if also else ""))
    return 0 if killed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())

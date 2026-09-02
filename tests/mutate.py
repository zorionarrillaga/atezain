#!/usr/bin/env python3
"""Mutation pass: for every `# CHECK: <name>` … `# ENDCHECK` block in policy/, delete the block
(replace it with `pass`) in a temporary copy of the repo, run the test suite there, and demand at
least one failure. A check that survives its own deletion cannot fail and is reported as SURVIVED;
the pass exits 1 if any check survived.

Why: the trading system that this layer descends from shipped a daily-kill check that compared a key
the live state never carried, so it evaluated `0 > -2000` forever and could not fail (PROVENANCE.md
row C1). This script is the standing answer to that class.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "policy" / "service.py", ROOT / "policy" / "fuse.py"]
BLOCK = re.compile(r"^(?P<indent>[ \t]*)# CHECK: (?P<name>[\w-]+)\n(?P<body>.*?)^(?P=indent)# ENDCHECK\n", re.S | re.M)


def blocks(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    return [(m.group("name"), m.group(0)) for m in BLOCK.finditer(text)]


def mutate(path: Path, block_src: str) -> str:
    m = BLOCK.match(block_src)
    assert m
    indent = m.group("indent")
    return path.read_text(encoding="utf-8").replace(block_src, f"{indent}pass  # MUTATED: check removed\n", 1)


def run_suite(tree: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_policy.py"],
                       cwd=tree, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)[-600:]


def main() -> int:
    baseline_rc, out = run_suite(ROOT)
    if baseline_rc != 0:
        print("BASELINE SUITE IS RED — fix that first\n" + out)
        return 2
    survived = []
    rows = []
    for f in FILES:
        for name, src in blocks(f):
            with tempfile.TemporaryDirectory() as td:
                tree = Path(td) / "atezain"
                shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
                target = tree / f.relative_to(ROOT)
                target.write_text(mutate(f, src), encoding="utf-8")
                rc, _ = run_suite(tree)
            verdict = "KILLED" if rc != 0 else "SURVIVED"
            rows.append((f.name, name, verdict))
            if rc == 0:
                survived.append(name)
    width = max(len(n) for _, n, _ in rows)
    for fn, name, verdict in rows:
        print(f"{fn:12} {name:{width}}  {verdict}")
    print(f"\n{len(rows)} checks · {len(rows) - len(survived)} killed · {len(survived)} survived")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

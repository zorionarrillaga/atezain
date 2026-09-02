#!/usr/bin/env python3
"""The gauge of the gauges. Break a property on purpose in a temporary copy and demand that the
published numbers fall. A gauge that stays green when the thing it measures is broken is not a
gauge — on 2026-09-01 an outside seat sabotaged `audit_orphans` to `return []` and `make hostile`
still printed 10/10, exit 0. This script is the standing answer: every sabotage below must turn
at least one of `make test` / `make hostile` red, or this script exits 1.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, file, old, new) — each a one-line break of a property the numbers claim to measure
SABOTAGES = [
    ("anomaly detector returns nothing", "policy/store.py",
     '        rows = self.audit_rows()\n        out: list[tuple[str, str]] = []\n',
     '        return []\n        rows = self.audit_rows()\n        out: list[tuple[str, str]] = []\n'),
    ("denied proposals consume the budget", "policy/service.py",
     "LIVE = (HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)",
     "LIVE = (DENIED, HELD, APPROVED, REJECTED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN)"),
    ("everyone is a human", "policy/model.py",
     "        return self.kind == HUMAN\n",
     "        return True\n"),
    ("the chain always verifies", "policy/store.py",
     "        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)\n",
     "        return True\n        prev, expected_seq, last = GENESIS, 1, (0, GENESIS)\n"),
    ("execution is not claimed", "policy/store.py",
     '            self._c().execute("INSERT INTO executions (proposal_id, ts, effect) VALUES (?, ?, ?)", (pid, ts, None))\n            return True\n',
     '            return True\n'),
    ("the budget is not transactional", "policy/store.py",
     '        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n',
     '        if True:\n            yield\n            return\n        with self._lock:\n            depth = getattr(self._depth, "n", 0)\n'),
]


def run(tree: Path, target: str) -> int:
    r = subprocess.run([sys.executable, target], cwd=tree, capture_output=True, text=True) if target.endswith(".py") else \
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_policy.py"], cwd=tree, capture_output=True, text=True)
    return r.returncode


def main() -> int:
    failures = 0
    for name, rel, old, new in SABOTAGES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"  STALE    {name}: the sabotage no longer matches {rel} (found {src.count(old)}×) — update this file")
            failures += 1
            continue
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "atezain"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
            (tree / rel).write_text(src.replace(old, new, 1), encoding="utf-8")
            test_rc = run(tree, "pytest")
            hostile_rc = run(tree, "tests/hostile_selftest.py")
        caught = test_rc != 0 or hostile_rc != 0
        print(f"  {'CAUGHT ' if caught else 'MISSED '}  {name:40}  test={'red' if test_rc else 'GREEN'}  hostile={'red' if hostile_rc else 'GREEN'}")
        failures += 0 if caught else 1
    print(f"\n{len(SABOTAGES) - failures}/{len(SABOTAGES)} sabotages caught by at least one gauge")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

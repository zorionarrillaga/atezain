"""No number in the prose that `make numbers` did not produce (CLAUDE.md rule 3).

`README.md` and `WRITEUP.md` may state a percentage only if that exact percentage is in
`NUMBERS.md`, which `redteam/numbers.py` writes from `redteam/results.jsonl`. Counts that come
from the gauges (`make test`, `make mutate`, `make hostile`, `make sabotage`) are not percentages
and are not covered here — `make all` is their source.

Run explicitly (it is not a `test_*.py`, so `make test` does not collect it):
    .venv/bin/python -m pytest -q tests/numbers.py
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROSE = ("README.md", "WRITEUP.md")
NUMBERS = ROOT / "NUMBERS.md"
PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%")
# a percentage inside a code block or an inline span is a command or a literal, not a claim
FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]*`")


def _claimed(text: str) -> set[str]:
    text = INLINE.sub(" ", FENCE.sub(" ", text))
    return {m.group(0).replace(" ", "") for m in PERCENT.finditer(text)}


@pytest.mark.parametrize("name", PROSE)
def test_every_percentage_in_the_prose_is_in_numbers_md(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} does not exist yet")
    claimed = _claimed(path.read_text(encoding="utf-8"))
    if not claimed:
        return
    assert NUMBERS.exists(), f"{name} states {sorted(claimed)} and NUMBERS.md does not exist"
    have = {m.group(0).replace(" ", "") for m in PERCENT.finditer(NUMBERS.read_text(encoding="utf-8"))}
    missing = sorted(claimed - have)
    assert not missing, f"{name}: {missing} not produced by `make numbers` (see NUMBERS.md)"


def test_numbers_md_names_its_model_and_its_n():
    if not NUMBERS.exists():
        pytest.skip("NUMBERS.md does not exist yet")
    text = NUMBERS.read_text(encoding="utf-8")
    assert "**model**:" in text and "**N**:" in text and "Wilson" in text
    assert "stub" not in text.split("## By injection class")[0] or "stub numbers" in text

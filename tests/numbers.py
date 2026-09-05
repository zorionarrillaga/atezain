"""No number in the prose that `make numbers` did not produce (CLAUDE.md rule 3).

`README.md`, `WRITEUP.md` and the prospect's `PAGE.md` may state a percentage only if that exact
percentage is in `NUMBERS.md`, which `redteam/numbers.py` writes from `redteam/results.jsonl`.
Counts that come from the gauges (`make test`, `make mutate`, `make hostile`, `make sabotage`) are
not percentages and are not covered here — `make all` is their source.

Step 5 (PLAN.md §6): the write-up and the page do not copy the table by hand. Each carries a block
between `<!-- numbers:begin -->` and `<!-- numbers:end -->` that `make numbers` rewrites; the test
below renders the block again from the rows and demands the file carries exactly that. A number
edited by hand, or a block left behind by an older run, is red.

Collected by `make test` (pyproject.toml names this file); alone, for the fast loop:
    make vocabulary
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
def _pages() -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "adapters").glob("*/PAGE*.md"))


# The release documents joined on 2026-09-05: `PRODUCT_RELEASE.md` quoted a served-run interval
# typed from the JSON by hand ("0–3.70%"), which is exactly the number rule 3 forbids.
PROSE = ("README.md", "WRITEUP.md", "PRODUCT_RELEASE.md", "SOLO_EVALUATION.md", "ENTERPRISE_REVIEW.md", *_pages())
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


def _named_rows():
    """The rows `make numbers` would report: the newest non-stub model, last row per case."""
    from redteam.numbers import load_rows, pick_model
    rows = load_rows()
    model = pick_model(rows)
    if model is None:
        pytest.skip("no rows from a named model yet: the block has nothing to be compared with")
    return [r for r in rows if r["model"] == model], model


@pytest.mark.parametrize("name", ["WRITEUP.md", *_pages()])
def test_the_numbers_block_in_the_prose_is_what_make_numbers_renders(name):
    """The table in the write-up is pasted by `make numbers`, never by hand (PLAN.md §6): the file
    carries the markers, and what sits between them is byte-for-byte a fresh render of the rows."""
    from redteam.numbers import MARK_BEGIN, MARK_END, extract_block, numbers_block
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} does not exist yet")
    text = path.read_text(encoding="utf-8")
    assert MARK_BEGIN in text and MARK_END in text, \
        f"{name} carries no numbers block: put {MARK_BEGIN} … {MARK_END} where the table goes and run `make numbers`"
    rows, model = _named_rows()
    have = extract_block(text)
    assert have is not None and have.strip() == numbers_block(rows, model).strip(), \
        f"{name}: the numbers block is not what `make numbers` renders from redteam/results.jsonl now — run it; do not edit the block by hand"


def test_the_block_is_the_only_place_the_prose_states_a_rate_it_did_not_get_from_numbers_md():
    """The block renders with the same `cell()` as NUMBERS.md, so every percentage in it is there:
    the block cannot introduce a number the file does not have."""
    from redteam.numbers import numbers_block
    if not NUMBERS.exists():
        pytest.skip("NUMBERS.md does not exist yet")
    rows, model = _named_rows()
    have = {m.group(0).replace(" ", "") for m in PERCENT.finditer(NUMBERS.read_text(encoding="utf-8"))}
    claimed = _claimed(numbers_block(rows, model))
    assert claimed and claimed <= have, sorted(claimed - have)



HAND_READ = re.compile(r"by hand|hand label|one reader|labelled the hundred|one output at a time", re.I)


@pytest.mark.parametrize("name", PROSE)
def test_a_surface_that_says_the_labels_were_read_by_hand_names_the_model_that_read_them(name):
    """The venture-refuter seat (2026-09-02): "read by hand" and "one reader" are true in this repo's
    vocabulary and a hirer reads them as a human. Any prose surface that says so must carry the
    labeller's name from `redteam/prose_labels.json`, the way NUMBERS.md does."""
    from redteam.numbers import load_labels
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} does not exist yet")
    labels = load_labels()
    if not labels:
        pytest.skip("no prose labels yet")
    text = path.read_text(encoding="utf-8")
    if not HAND_READ.search(text):
        return
    m = re.search(r"\(([^)]+)\)", labels.get("labelled_by", ""))
    who = m.group(1) if m else labels.get("labelled_by", "")
    assert who and who in text, f"{name} says the labels were read by hand and never names who read them: {who!r} (from prose_labels.json)"


def test_the_ruling_on_the_auto_approved_note_is_stated_where_each_reader_meets_it():
    """The owner ruled on 2026-09-04 that `add_note` stays auto-approved (STATUS.md, Open
    questions), and the ruling was to STATE it rather than to leave it as a default nobody chose.
    A decision that is only in STATUS.md is a decision the three people it affects never read, so
    this is the gauge on it: the count's own file, the two documents at the point each says a note
    is a write, and the page where a visitor reads the notes themselves.

    Delete any of the four sentences and the ruling quietly becomes an accident again."""
    where = {
        "NUMBERS.md": "Ruled 2026-09-04",
        "README.md": "Ruled on 2026-09-04 and not left open",
        "WRITEUP.md": "ruled on 2026-09-04 that it may keep being written",
        "api/demo.html": "that is a decision,\n      not an oversight",
    }
    missing = [f"{rel}: {frag!r}" for rel, frag in where.items()
               if frag not in (ROOT / rel).read_text(encoding="utf-8")]
    assert not missing, "the ruling is not where its reader meets it:\n" + "\n".join(missing)

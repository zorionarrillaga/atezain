"""Gauges over the documents' own numbers and their own prose (the MECHANISE items of the
write-up's refutation seat, 2026-09-02, STATUS.md).

1. Every gauge count a document quotes is the one `make all` wrote to `GAUGES.md`; `CLAUDE.md`
   quotes none and points to the record instead.
2. A hand count in the prose — *N of M*, *N/M* — has a source: a cell of `NUMBERS.md`, a line of
   `GAUGES.md`, or the record in `STATUS.md`. A count with no source is a count someone typed.
3. No paragraph is repeated across surfaces. The owner's gate for letters blocks two letters that
   share a run of twenty tokens — a lightly re-worded middle is still the same middle — and the
   same rule holds between this repo's prose files: the reading of a number lives on one surface
   and the other points to it. The generated numbers block is shared by design and is excluded.
4. A document that names its own reading time is not longer than that.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GAUGES = ROOT / "GAUGES.md"
COUNTED = ("README.md", "WRITEUP.md", "STATUS.md")          # carry the four lines, and agree with GAUGES.md
POINTERS = ("CLAUDE.md",)                                   # carry none: they point to the record
PAGES = tuple(sorted(str(p.relative_to(ROOT)) for p in (ROOT / "adapters").glob("*/PAGE*.md")))
PROSE = ("README.md", "WRITEUP.md", *PAGES)                 # the surfaces a reader reaches
SOURCES = ("NUMBERS.md", "GAUGES.md", "STATUS.md")          # where a hand count may come from

PATTERNS = {
    "test": re.compile(r"\b(\d+) passed, (\d+) skipped\b"),
    "mutate": re.compile(r"\b(\d+) checks · (\d+) killed by assertion · (\d+) killed only by a crash · (\d+) survived\b"),
    "hostile": re.compile(r"\b(\d+)/(\d+) scored attempts blocked\b"),
    "sabotage": re.compile(r"\b(\d+)/(\d+) sabotages caught\b"),
}
PAIR = re.compile(r"(?<![\d.,:/-])(\d+)(?: (?:of|de) (?:the |its |every |each |cada |los |las )?|/)(\d+)(?![\d.,%:/-])")
MINUTES = re.compile(r"\b(\d+)[- ]minute read\b|\bread(?:s| it)? in (?:about |under )?(\d+) minutes\b|\b(\d+) minutes to read\b", re.I)
RUN_LIMIT = 20          # the owner's gate: a shared run this long is the same paragraph
N = 6


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _prose(rel: str) -> str:
    """The words a reader reads: no code fences, no tables, no generated block, no URLs."""
    s = _text(rel)
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"<!-- numbers:begin -->.*?<!-- numbers:end -->", " ", s, flags=re.S)
    s = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("|"))
    return re.sub(r"https?://\S+", " ", s)


def expected() -> dict[str, set[tuple[int, ...]]]:
    if not GAUGES.exists():
        pytest.fail("GAUGES.md does not exist — `make all` writes it")
    text = GAUGES.read_text(encoding="utf-8")
    exp = {}
    for name, pat in PATTERNS.items():
        found = {tuple(int(x) for x in m) for m in pat.findall(text)}
        if not found:
            pytest.fail(f"GAUGES.md carries no `{name}` line — run `make all`")
        exp[name] = found
    return exp


def disagreements() -> list[str]:
    """Every place a counted document disagrees with GAUGES.md; used here and by `make all`."""
    exp = expected()
    out = []
    for rel in COUNTED:
        text = _text(rel)
        for name, pat in PATTERNS.items():
            found = [tuple(int(x) for x in m) for m in pat.findall(text)]
            if not found:
                out.append(f"{rel}: no `{name}` line (GAUGES.md has {sorted(exp[name])})")
            for f in found:
                if f not in exp[name]:
                    out.append(f"{rel}: `{name}` says {f}, GAUGES.md says {sorted(exp[name])}")
    for rel in POINTERS:
        text = _text(rel)
        for name, pat in PATTERNS.items():
            if pat.search(text):
                out.append(f"{rel}: quotes a `{name}` count; it should point to GAUGES.md/STATUS.md instead")
    return out


def test_every_gauge_count_in_the_documents_is_the_one_make_all_wrote():
    problems = disagreements()
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("rel", PROSE)
def test_a_hand_count_in_the_prose_has_a_source(rel):
    sources = set()
    for s in SOURCES:
        if (ROOT / s).exists():
            sources |= {(int(a), int(b)) for a, b in PAIR.findall(_text(s))}
    orphans = sorted({(int(a), int(b)) for a, b in PAIR.findall(_prose(rel))} - sources)
    assert not orphans, (f"{rel} states {['%d of %d' % o for o in orphans]} and no cell of NUMBERS.md, line of "
                         f"GAUGES.md or entry in STATUS.md carries that count — where did it come from?")


def _tokens(rel: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", _prose(rel).lower())


def _longest_shared_run(a: list[str], b: list[str]) -> tuple[int, str]:
    grams = {tuple(a[i:i + N]) for i in range(len(a) - N + 1)}
    best, where, i = 0, "", 0
    while i <= len(b) - N:
        if tuple(b[i:i + N]) in grams:
            j = i
            while j <= len(b) - N and tuple(b[j:j + N]) in grams:
                j += 1
            run = j - i + N - 1
            if run > best:
                best, where = run, " ".join(b[i:i + run])
            i = j
        else:
            i += 1
    return best, where


@pytest.mark.parametrize("pair", list(itertools.combinations(PROSE, 2)), ids=lambda p: f"{Path(p[0]).name}~{Path(p[1]).name}")
def test_no_paragraph_is_repeated_across_surfaces(pair):
    a, b = pair
    run, where = _longest_shared_run(_tokens(a), _tokens(b))
    assert run < RUN_LIMIT, (f"{a} and {b} share a run of {run} tokens (the gate blocks at {RUN_LIMIT}): "
                             f"{where[:200]!r} — one surface keeps the paragraph, the other points to it")


@pytest.mark.parametrize("rel", PROSE)
def test_a_document_that_names_its_own_reading_time_is_not_longer_than_that(rel):
    text = _text(rel)
    words = len(re.findall(r"\w+", _prose(rel)))
    for m in MINUTES.finditer(text):
        minutes = int(next(g for g in m.groups() if g))
        assert words / 250 <= minutes, f"{rel} says it reads in {minutes} minutes and has {words} words"

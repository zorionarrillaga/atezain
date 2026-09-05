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

import json
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


def test_a_clone_with_no_database_can_still_reach_a_green_make_all(tmp_path, monkeypatch):
    """Round-2 seat, 2026-09-03 (D2). `var/` is gitignored and the DSN arm needs a database this
    repo does not ship, so a fresh clone records four gauges and not the fifth. Rendering used to
    DROP the DSN row from this tracked file and then fail, blaming the three documents that quote
    it — and `make test` stayed red afterwards until someone hand-edited the file whose header
    forbids it. `make all` was unreachable for exactly the reader `README.md` invites to reproduce
    it. The row is now carried forward from `GAUGES.md`, with its own date, when this tree has no
    DSN run of its own."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gauge_record", ROOT / "tests" / "gauge_record.py")
    gr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gr)

    # The four records a clone's `make all` would leave, written here rather than copied from this
    # tree's `var/`: what is under test is the RENDER, and reading whatever the last local run left
    # behind would make the result depend on whether that run happened to be green.
    var = tmp_path / "gauges"
    var.mkdir(parents=True)
    lines = {"test": "7 passed, 3 skipped", "mutate": "1 checks · 1 killed by assertion",
             "hostile": "2/2 scored attempts blocked", "sabotage": "5/5 sabotages caught by at least one gauge"}
    for name, line in lines.items():
        (var / f"{name}.json").write_text(
            json.dumps({"line": line, "date": "2026-09-03", "exit": 0}), encoding="utf-8")
    monkeypatch.setattr(gr, "VAR", var)

    rendered = gr.render()
    dsn = [l for l in rendered.splitlines() if l.startswith(f"| {gr.LABEL['test_dsn']} |")]
    assert len(dsn) == 1, "the DSN row must survive a tree that cannot run it"
    assert dsn[0] == next(l for l in GAUGES.read_text(encoding="utf-8").splitlines()
                          if l.startswith(f"| {gr.LABEL['test_dsn']} |")), "carried verbatim, date and all"
    # The four gauges this tree CAN run are still rendered from `var/`, not carried — the carry is
    # for the one it cannot run, never a way for any line to outlive the run that produced it.
    # Whether the documents then agree is the other test's job, not this one's: asserting it here
    # too would just report the same lag twice while the counts are between runs.
    for name, line in lines.items():
        assert f"| {gr.LABEL[name]} | {line} |" in rendered


def _gauge_record():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gauge_record", ROOT / "tests" / "gauge_record.py")
    gr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gr)
    return gr


def test_a_gauge_line_names_the_tree_it_measured(tmp_path, monkeypatch):
    """2026-09-05: a `test` line from a run before the session's edits was carried into GAUGES.md,
    and only a hand re-run caught it. Every record now names the tree it measured; the header names
    the tree the file is written for; the DSN line — the one line a clone without a database can
    only carry — names its own tree beside its date; and `write` refuses a required line measured
    on another tree."""
    gr = _gauge_record()
    var = tmp_path / "gauges"
    var.mkdir()
    lines = {"test": "7 passed, 3 skipped", "mutate": "1 checks · 1 killed by assertion",
             "hostile": "2/2 scored attempts blocked", "sabotage": "5/5 sabotages caught by at least one gauge"}
    for name, line in lines.items():
        (var / f"{name}.json").write_text(json.dumps({"line": line, "date": "2026-09-05", "exit": 0,
                                                      "tree": "aaaaaaaaaaaa", "head": "1234567+"}), encoding="utf-8")
    (var / "test_dsn.json").write_text(json.dumps({"line": "9 passed, 1 skipped", "date": "2026-09-04", "exit": 0,
                                                   "tree": "bbbbbbbbbbbb", "head": "abcdef0"}), encoding="utf-8")
    monkeypatch.setattr(gr, "VAR", var)
    rendered = gr.render("aaaaaaaaaaaa")
    assert "every line measured on tree `aaaaaaaaaaaa`" in rendered
    assert "| `make test` with `ATEZAIN_TEST_DSN` | 9 passed, 1 skipped (2026-09-04, tree bbbbbbbbbbbb at abcdef0) |" in rendered
    assert "| `make test` | 7 passed, 3 skipped |" in rendered, "a line of this tree carries no tree of its own: the header does"
    assert gr.stale("aaaaaaaaaaaa") == []
    problems = gr.stale("cccccccccccc")
    assert len(problems) == 4 and all("measured on tree aaaaaaaaaaaa at 1234567+; this is tree cccccccccccc" in p for p in problems)
    # a record from before records named a tree is stale too: nothing says what it measured
    (var / "test.json").write_text(json.dumps({"line": "7 passed, 3 skipped", "date": "2026-09-05", "exit": 0}), encoding="utf-8")
    assert [p for p in gr.stale("aaaaaaaaaaaa") if p.startswith("the `test` line")] and "tree unknown" in gr.stale("aaaaaaaaaaaa")[0]


def test_the_tree_id_follows_the_content_and_ignores_the_file_it_writes(tmp_path, monkeypatch):
    import subprocess
    gr = _gauge_record()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "b.txt").write_text("noise\n", encoding="utf-8")
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    monkeypatch.setattr(gr, "OUT", tmp_path / "GAUGES.md")
    one = gr.tree_id()
    assert one and len(one) == 12 and one == gr.tree_id(), "untracked files count; ignored ones do not; the id is stable"
    (tmp_path / "ignored" / "b.txt").write_text("other noise\n", encoding="utf-8")
    (tmp_path / "GAUGES.md").write_text("# written by the mechanism itself\n", encoding="utf-8")
    assert gr.tree_id() == one, "GAUGES.md and ignored files do not move the id"
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert gr.tree_id() != one, "a changed byte in the tree is another tree"
    monkeypatch.setattr(gr, "ROOT", tmp_path / "ignored")
    assert gr.tree_id() is None or gr.tree_id() != one

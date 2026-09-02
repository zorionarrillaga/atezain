"""The names the write-up claims are load-bearing (PLAN.md §6; the K5 rule in §3.4).

A technology named in `WRITEUP.md`, the prospect's `PAGE.md` or `README.md` must map to a place in
this repo where it is imported AND called on the main path — the graph builder, the served
application, the store's constructor, the numbers renderer, the red-team's runner. A name that maps
to nothing fails this test, and the fix is to remove the word from the text, never to add a
decorative import (PLAN.md §3.4: "do not keep a decorative dependency").

Two tests. The first checks the MAP against the code, whether or not the prose uses a name, so the
map itself cannot rot: a `Call` is found by walking the file's AST for a call to that callee inside
that function (module level when the function is None), and the callee must be imported or defined
in the file. The second reads the prose and fails on any watched name whose entry is None — the
things this project planned and has not built.

Collected by `make test` (pyproject.toml names this file); alone: `make vocabulary`.
"""
from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROSE = ("WRITEUP.md", "adapters/invoices-es/PAGE.md", "README.md")


@dataclass(frozen=True)
class Call:
    """`callee` is called inside `func` of `file` (module level when func is None), and is imported
    or defined in that file."""
    file: str
    func: str | None
    callee: str


@dataclass(frozen=True)
class Import:
    """The library resolves in this interpreter — it is installed, not only named."""
    module: str


@dataclass(frozen=True)
class MakeTarget:
    """The Makefile's recipe for `target` contains every word in `words`."""
    target: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class FileHas:
    """`file` exists and contains `text`."""
    file: str
    text: str


# pattern in the prose → what makes the name load-bearing; None = nothing in this repo does
VOCABULARY: dict[str, tuple | None] = {
    r"\bLangGraph\b": (Call("agent/graph.py", "build_graph", "StateGraph"),
                       Call("agent/graph.py", "hold", "interrupt"),
                       Call("agent/graph.py", "build_graph", "g.compile"),
                       Import("langgraph.graph")),
    r"\bFastAPI\b": (Call("api/app.py", None, "FastAPI"), Import("fastapi")),
    r"\bpromptfoo\b": (MakeTarget("redteam", ("promptfoo", "eval", "promptfooconfig.yaml", "--from-promptfoo")),
                       FileHas("redteam/promptfooconfig.yaml", "assert:"),
                       FileHas("redteam/promptfooconfig.yaml", "file://provider.py"),
                       Call("redteam/provider.py", "call_api", "run_case")),
    r"\bWilson\b": (Call("redteam/numbers.py", "cell", "wilson"),
                    Call("redteam/numbers.py", "table", "cell"),
                    Call("redteam/numbers.py", "numbers_block", "cell")),
    r"\bSQLite\b": (Call("policy/store.py", "__init__", "sqlite3.connect"),
                    Call("records/store.py", "__init__", "sqlite3.connect")),
    r"\bPostgre(?:s|SQL)\b": (Call("policy/store_pg.py", "__init__", "psycopg.connect"),
                              Call("records/store_pg.py", "__init__", "psycopg.connect"),
                              Import("psycopg")),
    r"\bGroq\b": (Call("agent/llm.py", "complete", "urllib.request.urlopen"),
                  Call("redteam/run.py", "build_llm", "GroqLLM")),
    # planned, not built: a document that names one of these as if it were here is red (K5)
    r"\bpgvector\b": None,
    r"\bfastembed\b": None,
    r"\bLangfuse\b": None,
    r"\bragas\b": None,
    r"\bFTS\b": None,
    r"\bfull-text search\b": None,
}


# ── the checks ───────────────────────────────────────────────────────────────────────────────
def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _names_bound(tree: ast.Module) -> set[str]:
    """Everything the file imports or defines at any level: the first segment of a callee must be here."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.update(a.asname or a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _calls_in(tree: ast.Module, func: str | None) -> set[str]:
    """Dotted callee names of every call inside the named function(s), or at module level."""
    if func is None:
        scopes: list[ast.AST] = [tree]
    else:
        scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func]
    out: set[str] = set()
    for scope in scopes:
        for node in ast.walk(scope):
            if isinstance(node, ast.Call):
                d = _dotted(node.func)
                if d:
                    out.add(d)
    return out


def _check(req) -> str | None:
    """None when the requirement holds; otherwise one sentence saying what is missing."""
    if isinstance(req, Call):
        path = ROOT / req.file
        if not path.exists():
            return f"{req.file} does not exist"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if req.callee.split(".")[0] not in _names_bound(tree):
            return f"{req.file} neither imports nor defines {req.callee.split('.')[0]!r}"
        where = "module level" if req.func is None else f"{req.func}()"
        if req.callee not in _calls_in(tree, req.func):
            return f"{req.file}: {req.callee}() is not called at {where}"
        return None
    if isinstance(req, Import):
        try:
            importlib.import_module(req.module)
        except ImportError as e:
            return f"{req.module} does not import: {e}"
        return None
    if isinstance(req, MakeTarget):
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        m = re.search(rf"^{re.escape(req.target)}:[^\n]*\n((?:\t[^\n]*\n)+)", text, re.M)
        if not m:
            return f"Makefile has no target {req.target!r}"
        missing = [w for w in req.words if w not in m.group(1)]
        return f"Makefile target {req.target!r} does not mention {missing}" if missing else None
    if isinstance(req, FileHas):
        path = ROOT / req.file
        if not path.exists():
            return f"{req.file} does not exist"
        return None if req.text in path.read_text(encoding="utf-8") else f"{req.file} does not contain {req.text!r}"
    raise TypeError(req)


def _prose():
    return [(name, (ROOT / name).read_text(encoding="utf-8")) for name in PROSE if (ROOT / name).exists()]


# ── the tests ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pattern", [p for p, reqs in VOCABULARY.items() if reqs])
def test_every_name_in_the_map_is_imported_and_called_on_the_main_path(pattern):
    problems = [msg for req in VOCABULARY[pattern] for msg in [_check(req)] if msg]
    assert not problems, f"{pattern}: " + "; ".join(problems)


@pytest.mark.parametrize("name", PROSE)
def test_the_prose_names_nothing_that_maps_to_nothing(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} does not exist yet")
    text = path.read_text(encoding="utf-8")
    offending = sorted(m.group(0) for pattern, reqs in VOCABULARY.items() if reqs is None
                       for m in re.finditer(pattern, text, re.I))
    assert not offending, f"{name} names {sorted(set(offending))}, which nothing in this repo imports and calls — remove the word from the text (PLAN.md §6, K5)"


def test_every_load_bearing_name_the_prose_uses_is_in_the_map():
    """Smoke: the write-up names at least the runner and the interval, so the map is being exercised."""
    texts = " ".join(t for _, t in _prose())
    if "WRITEUP.md" not in {n for n, _ in _prose()}:
        pytest.skip("WRITEUP.md does not exist yet")
    used = [p for p, reqs in VOCABULARY.items() if reqs and re.search(p, texts)]
    assert r"\bpromptfoo\b" in used and r"\bWilson\b" in used and r"\bLangGraph\b" in used



MAIN_PATH_PACKAGES = ("policy", "agent", "records", "api", "redteam")


def _docstrings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]:
        doc = ast.get_docstring(node)
        if doc:
            out.append((getattr(node, "lineno", 1), doc))
    return out


def test_no_docstring_on_the_main_path_names_a_thing_that_is_not_built():
    """The venture-refuter seat (2026-09-02) found `records/store.py` calling a vector retriever "the
    deployed retriever" in a docstring while the prose gauge barred the word from the documents. A
    reviewer reads docstrings; the same rule holds there."""
    offending = []
    for pkg in MAIN_PATH_PACKAGES:
        for path in sorted((ROOT / pkg).glob("*.py")):
            for lineno, doc in _docstrings(path):
                for pattern, reqs in VOCABULARY.items():
                    if reqs is None and re.search(pattern, doc, re.I):
                        offending.append(f"{path.relative_to(ROOT)}:{lineno} says {pattern}")
    assert not offending, "\n".join(offending)

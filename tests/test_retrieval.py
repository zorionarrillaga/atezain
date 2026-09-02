"""§4.4, retired (STATUS.md, 2026-09-02, ⚖): there is no recall number, and this file is why.

`retrieve` hands the model the invoice and up to five snippets chosen by keyword overlap over
every note and email in the store. The prompt tells the model those snippets come from *other
invoices of the same customer*. On the seed they mostly do not — most belong to another
customer — and the honest fix, a query by customer, changes what the model reads. The hundred
cached outputs in `redteam/cache/` were produced with THIS retriever, and the cache key
(`redteam/run.py::case_hash`: the case, the prompt, the seed, the model id) does not see the
graph's code. So this file pins the retriever the numbers were run with: change it, and the
first test goes red until the hundred are re-run and the prose labels re-read.

There is no recall measurement because the product has no free-text question: an assist is
`POST /sessions/{s}/assist/{invoice_id}`, and the information need behind `retrieve` is
relational — the same customer's other records — which a query answers with recall of one by
construction. A recall@k over hand-written Spanish queries would measure a retriever the product
does not need against questions no user of it can send. The third test keeps such a number out of
the prose; the second keeps the disclosure in it for as long as the defect exists.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from agent import StubLLM, build_graph
from policy import AGENT, PolicyConfig, Principal, Store, PolicyService
from records import Records

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "adapters" / "invoices-es" / "permissions.toml"
SEED = ROOT / "adapters" / "invoices-es" / "seed.json"
GRAPH = ROOT / "agent" / "graph.py"
PROMPT = ROOT / "adapters" / "invoices-es" / "prompt.md"
PROSE = ("README.md", "WRITEUP.md", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "adapters").glob("*/PAGE*.md")))

# What the graph handed the model on the seed on 2026-09-02, when the hundred were run:
# of the snippets across the twelve invoices, how many were the invoice's own notes/emails,
# how many the same customer's other invoice, how many another customer's.
PINNED = Counter({"other customer": 48, "own record": 6, "same customer, other invoice": 6})

RERUN = ("the retriever the hundred cached model outputs were produced with has changed. The cache "
         "key (redteam/run.py::case_hash) cannot see this, so the cached outputs are now answers to a "
         "context the graph no longer builds. Re-run `make redteam REDTEAM_MODEL=groq`, re-read "
         "redteam/prose_labels.json against the new outputs, run `make numbers`, and then update "
         "PINNED here from this test's output. If only the seed changed, the cache re-runs by itself; "
         "update PINNED from the output.")


def _snippets_by_owner() -> Counter:
    records = Records(":memory:")
    records.load_seed(SEED)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    graph = build_graph(records, policy, StubLLM(), Principal("assistant", AGENT))
    customer = {i: records.invoice(i)["customer"] for i in records.ids()}
    tally: Counter = Counter()
    for i in records.ids():
        out = graph.invoke({"invoice_id": i, "task": "draft"}, config={"configurable": {"thread_id": f"r-{i}"}})
        for s in out["context"]["snippets"]:
            if s["invoice_id"] == i:
                tally["own record"] += 1
            elif customer[s["invoice_id"]] == customer[i]:
                tally["same customer, other invoice"] += 1
            else:
                tally["other customer"] += 1
    return tally


def test_retrieve_hands_the_model_the_snippets_the_hundred_were_run_with():
    tally = _snippets_by_owner()
    print("\nsnippets the graph hands the model on the seed:", dict(tally))
    assert tally == PINNED, f"{dict(tally)} != {dict(PINNED)} — {RERUN}"


def test_while_the_prompt_promises_the_same_customer_the_write_up_says_what_arrives():
    """The prompt says the fragments are from the same customer; the keyword retriever does not
    deliver that. Until `retrieve` selects by customer, the write-up must say so where it describes
    retrieval. When the retriever is fixed this test asks for nothing."""
    graph_src = GRAPH.read_text(encoding="utf-8")
    prompt = PROMPT.read_text(encoding="utf-8")
    keyword_retriever = "records.search(" in graph_src
    promises_same_customer = "mismo cliente" in prompt
    if not (keyword_retriever and promises_same_customer):
        return
    writeup = (ROOT / "WRITEUP.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Retrieval\.\*\*(.*?)(?=\n\n)", writeup, re.S)
    assert m, "WRITEUP.md has no **Retrieval.** paragraph"
    assert re.search(r"another customer", m.group(1)), \
        "WRITEUP.md's Retrieval paragraph must say that the snippets can belong to another customer while the prompt says otherwise"


def test_the_prose_quotes_no_recall_number():
    pat = re.compile(r"(recall|precision)\s*@\s*\d|\b(recall|precision)\s*[=:]\s*\d|context_(recall|precision)|"
                     r"\d+\s*%[^.\n]{0,60}\b(recall|precision)\b|\b(recall|precision)\b[^.\n]{0,60}\d+\s*%", re.I)
    for rel in PROSE:
        text = (ROOT / rel).read_text(encoding="utf-8")
        m = pat.search(text)
        assert m is None, f"{rel} quotes a retrieval number that nothing measured: {m.group(0)!r}"

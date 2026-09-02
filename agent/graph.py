"""The assistant as a LangGraph graph.

    retrieve → think → propose → execute → [hold] → execute

`think` is the only node that talks to the model. `propose` is the only node that talks to the
policy service's `propose`. `hold` contains nothing but `interrupt(...)` — LangGraph re-executes a
node from its start on resume, so a node with side effects before the interrupt would repeat them
(langgraph.types.interrupt docstring, 1.2.11). `execute` runs twice, as two nodes with one body:
before the hold it executes what the policy approved with no human (this adapter's notes), so a
write that needs no decision does not wait on an unrelated one; after the hold it executes what a
human approved before the resume. Until 2026-09-03 it ran only after the hold, and the served
application — which never resumes the graph, because `decide` executes the decided proposal itself —
left an auto-approved note `approved` and unwritten whenever a sibling was held; the step-6 seat
found it (STATUS.md). Both passes read every proposal's status FROM THE POLICY STORE: the value the
client passes on resume is untrusted input and is never used to decide anything — a human decides
through `PolicyService.decide`, and the graph only observes it. A proposal the first pass executed
is skipped by the second, and the store's exactly-once claim stands behind that in any case.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.executor import make_executor
from agent.llm import LLM
from policy import APPROVED, EXECUTED, HELD, Principal, PolicyService
from records import Records

ROOT = Path(__file__).resolve().parents[1]


class State(TypedDict, total=False):
    invoice_id: str
    task: str                   # "summarise" | "recommend" | "draft" — all three are produced; task steers the prompt
    context: dict[str, Any]     # the invoice with notes/emails + retrieved snippets
    summary: str
    recommendation: str
    draft: str
    raw_proposals: list[dict]   # what the model asked for, verbatim
    proposals: list[dict]       # {id, action, status, reason} after the policy spoke
    held: list[str]
    executed: list[str]
    refused: list[dict]


def load_prompt(adapter: str) -> str:
    return (ROOT / "adapters" / adapter / "prompt.md").read_text(encoding="utf-8")


def parse_model_output(text: str) -> dict:
    """The model must answer with one JSON object. Anything else is treated as no proposals."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"summary": "", "recommendation": "", "draft": "", "proposals": []}
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"summary": "", "recommendation": "", "draft": "", "proposals": []}
    props = out.get("proposals")
    out["proposals"] = [p for p in props if isinstance(p, dict)] if isinstance(props, list) else []
    return out


def build_graph(records: Records, policy: PolicyService, llm: LLM, agent: Principal, adapter: str = "invoices-es", checkpointer=None):
    system_prompt = load_prompt(adapter)
    executor = make_executor(records)

    def retrieve(state: State) -> State:
        inv = records.invoice(state["invoice_id"])
        if inv is None:
            raise KeyError(state["invoice_id"])
        snippets = records.search(f"{inv['customer']} factura pago", k=5)
        return {"context": {"invoice": inv, "snippets": snippets}}

    def think(state: State) -> State:
        user = json.dumps({"task": state.get("task", "summarise"), "context": state["context"]}, ensure_ascii=False, indent=1)
        out = parse_model_output(llm.complete(system_prompt, user))
        return {"summary": str(out.get("summary", "")), "recommendation": str(out.get("recommendation", "")),
                "draft": str(out.get("draft", "")), "raw_proposals": out["proposals"]}

    def propose(state: State) -> State:
        results, held = [], []
        for rp in state.get("raw_proposals", []):
            action = rp.get("action") if isinstance(rp.get("action"), str) else ""
            params = rp.get("params") if isinstance(rp.get("params"), dict) else {}
            p = policy.propose(agent, action, state["invoice_id"], params, evidence=str(rp.get("why", ""))[:500])
            results.append({"id": p.id, "action": action, "status": p.status, "reason": p.reason})
            if p.status == HELD:
                held.append(p.id)
        return {"proposals": results, "held": held}

    def hold(state: State) -> State:
        if state.get("held"):
            interrupt({"held": state["held"], "message": "A human decides these through PolicyService.decide; then resume."})
        return {}

    def execute(state: State) -> State:
        done, refused = list(state.get("executed", [])), []
        for pr in state.get("proposals", []):
            p = policy.store.get_proposal(pr["id"])           # the store, never the state or the resume value
            if p is not None and p.status == EXECUTED and pr["id"] in done:
                continue                                       # the earlier pass did it; nothing to repeat
            if p is None or p.status != APPROVED:
                refused.append({"id": pr["id"], "status": p.status if p else "missing"})
                continue
            q = policy.execute(p.id, executor, Principal("graph", "system"))
            (done if q.status == "executed" else refused).append(q.id if q.status == "executed" else {"id": q.id, "status": q.status})
        return {"executed": done, "refused": refused}

    g = StateGraph(State)
    g.add_node("retrieve", retrieve)
    g.add_node("think", think)
    g.add_node("propose", propose)
    g.add_node("execute", execute)                 # what needs no human, before anyone is asked
    g.add_node("hold", hold)
    g.add_node("execute_decided", execute)         # what a human approved, after the resume
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "think")
    g.add_edge("think", "propose")
    g.add_edge("propose", "execute")
    g.add_edge("execute", "hold")
    g.add_edge("hold", "execute_decided")
    g.add_edge("execute_decided", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())

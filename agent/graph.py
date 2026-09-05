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
import uuid
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.executor import make_executor
from agent.tracing import Tracer
from agent.llm import LLM, ModelFailure
from policy import APPROVED, EXECUTED, HELD, Principal, PolicyService
from records import Records

ROOT = Path(__file__).resolve().parents[1]


class State(TypedDict, total=False):
    run_id: str
    model_name: str
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
    """Require a bounded JSON proposal object; malformed output is a retryable model failure."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
        raise ModelFailure("invalid_output")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ModelFailure("invalid_output")
    try:
        out = json.loads(m.group(0), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (ValueError, RecursionError) as e:
        raise ModelFailure("invalid_output") from e
    if not isinstance(out, dict) or any(not isinstance(out.get(k, ""), str) for k in ("summary", "recommendation", "draft")):
        raise ModelFailure("invalid_output")
    props = out.get("proposals")
    if not isinstance(props, list) or len(props) > 12 or any(not isinstance(p, dict) for p in props):
        raise ModelFailure("invalid_proposals")
    return out


def build_graph(records: Records, policy: PolicyService, llm: LLM, agent: Principal, adapter: str = "invoices-es", checkpointer=None, tracer=None, retrieval="legacy"):
    system_prompt = load_prompt(adapter)
    executor = make_executor(records)
    # Hosted tracing requires explicit opt-in; served callers provide an unhosted tracer.
    # Spans are collected in-process either way; `traces/export.py` writes them beside the chain.
    tracer = tracer if tracer is not None else Tracer.from_env()

    def retrieve(state: State) -> State:
        inv = records.invoice(state["invoice_id"])
        if inv is None:
            raise KeyError(state["invoice_id"])
        snippets = (records.customer_context(state["invoice_id"], k=5) if retrieval == "customer"
                    else records.search(f"{inv['customer']} factura pago", k=5))
        return {"context": {"invoice": inv, "snippets": snippets}, "run_id": uuid.uuid4().hex}

    def think(state: State) -> State:
        user = json.dumps({"task": state.get("task", "summarise"), "context": state["context"]}, ensure_ascii=False, indent=1)
        if len(user.encode("utf-8")) > 64000:
            raise ModelFailure("context_too_large")
        try:
            out = parse_model_output(llm.complete(system_prompt, user))
        except ModelFailure:
            raise
        except Exception as e:
            raise ModelFailure(getattr(e, "code", type(e).__name__)) from e
        return {"summary": str(out.get("summary", "")), "recommendation": str(out.get("recommendation", "")),
                "draft": str(out.get("draft", "")), "raw_proposals": out["proposals"]}

    def propose(state: State) -> State:
        results, held = [], []
        record = records.invoice(state["invoice_id"]) or {}
        for index, rp in enumerate(state.get("raw_proposals", [])):
            action = rp.get("action") if isinstance(rp.get("action"), str) else ""
            params = dict(rp.get("params")) if isinstance(rp.get("params"), dict) else {}
            # A field the adapter binds to a value of the record (`record_constraints`: the
            # reminder's address) is filled in HERE, from the record, when the model named none —
            # so the human sees where the message goes, and where it goes was never something a
            # note in the record could talk the model into. A value the model DID name is passed
            # on exactly as it named it: the policy denies it if it is not the record's, and this
            # line quietly repairing it would hide the attempt instead of showing it.
            spec = policy.config.actions.get(action)
            for f, source in (spec.record_constraints.items() if spec is not None else ()):
                if f not in params and record.get(source) not in (None, ""):
                    params[f] = record[source]
            p = policy.propose(agent, action, state["invoice_id"], params, evidence=str(rp.get("why", ""))[:500],
                               idempotency_key=f"{state['run_id']}:{index}" if state.get("run_id") else None)
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
    # The model call, and the two writes: what the chain does not carry. The keys each span may
    # record are named here, at the call site, so a trace holds a decision and not whatever was in
    # scope — the model's answer, never the visitor's record.
    g.add_node("think", tracer.node("think", think, ("invoice_id", "task"),
                                    ("summary", "recommendation", "draft", "raw_proposals")))
    g.add_node("propose", propose)
    g.add_node("execute", tracer.node("execute", execute, ("held",), ("executed", "refused")))
    g.add_node("hold", hold)
    g.add_node("execute_decided", tracer.node("execute_decided", execute, ("held",),
                                              ("executed", "refused")))
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "think")
    g.add_edge("think", "propose")
    g.add_edge("propose", "execute")
    g.add_edge("execute", "hold")
    g.add_edge("hold", "execute_decided")
    g.add_edge("execute_decided", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())

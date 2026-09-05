"""The model call, traced — which the audit chain deliberately does not carry (PLAN.md §4.5).

The chain is the record of what was proposed, decided and written: evidence about writes, hashed
and linked. It says nothing about the call that produced a proposal — what the model was shown, how
long it took, what it answered before the layer had an opinion. That is what this adds, and it is a
SEPARATE record on purpose. A trace is an observation of the agent; it is not evidence about a
write, it is not hashed into anything, and nothing here can change what the layer does.

Three rules this module keeps, in the order they matter:

1. **A tracer never fails a send.** Every recording is wrapped: if the sink is unwritable or the SDK
   throws, the node's own return value is handed back untouched. An observability dependency that
   can fail a write is worse than no observability at all.
2. **The record is local and the hosted view is optional.** Spans are collected in this process
   whether or not anything is configured; `traces/export.py` writes them beside the chain, so a
   reader replays a run with no key and no account. Langfuse is a view of that record, never its
   source — and without `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` in the environment, or without
   the SDK installed, every function here still runs and sends nothing.
3. **Only the named keys are recorded.** The caller says which parts of the graph's state a span may
   carry, so a record is a decision made at the call site and not whatever happened to be in scope.

The served graph explicitly disables hosted tracing and file sinks, independent of environment
credentials. Assist requests still send relevant records to the configured model provider; tracing
controls do not change that data path.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

try:                            # optional: a clone without the SDK traces nothing and runs the same
    from langfuse import get_client, observe
except ImportError:             # pragma: no cover - the path a clone with no SDK takes
    get_client = observe = None

KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
SINK = "ATEZAIN_TRACE_SINK"


def _safe(value: Any, limit: int = 4000) -> Any:
    """A value a JSON file can hold, and a reader can read. Anything else becomes its repr, cut."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)[:limit]


class Tracer:
    """Wraps graph nodes. Off by default, and off is a working state, not a degraded one."""

    def __init__(self, hosted: bool = False, sink: str | Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.spans: list[dict] = []
        self.hosted = bool(hosted and observe)
        self.sink = Path(sink) if sink else None
        self.clock = clock

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Tracer":
        """Experiments can opt into hosting with ATEZAIN_HOSTED_TRACING=1 and configured keys.
        The served graph uses an explicit Tracer() instead of inheriting these settings."""
        env = os.environ if env is None else env
        return cls(hosted=env.get("ATEZAIN_HOSTED_TRACING") == "1" and all(env.get(k) for k in KEYS),
                   sink=env.get(SINK) or None)

    def node(self, name: str, fn: Callable[[dict], dict],
             keys_in: Iterable[str] = (), keys_out: Iterable[str] = ()) -> Callable[[dict], dict]:
        """`fn`, with a span around it. The node's return value is returned whatever the tracer does."""
        # The SDK decorator otherwise captures the entire state, bypassing keys_in/keys_out.
        inner = observe(name=name, capture_input=False, capture_output=False)(fn) if self.hosted else fn
        keys_in, keys_out = tuple(keys_in), tuple(keys_out)

        def traced(state: dict) -> dict:
            started = self.clock()
            out = inner(state)
            try:
                self._record(name, started, {k: _safe(state.get(k)) for k in keys_in},
                             {k: _safe(out.get(k)) for k in keys_out} if isinstance(out, dict) else {})
            except Exception:           # a span is not worth a send: rule 1 of this module
                pass
            return out

        traced.__name__ = f"traced_{name}"
        return traced

    def _record(self, name: str, started: float, into: dict, outof: dict) -> None:
        span = {"span": name, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
                "ms": int((self.clock() - started) * 1000), "in": into, "out": outof}
        self.spans.append(span)
        if self.sink:
            self.sink.parent.mkdir(parents=True, exist_ok=True)
            with self.sink.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(span, ensure_ascii=False, sort_keys=True) + "\n")

    def flush(self) -> None:
        """Hand what is buffered to the hosted view, if there is one. Never raises."""
        try:
            if self.hosted and get_client is not None:
                get_client().flush()
        except Exception:
            pass


def load_spans(path: str | Path) -> list[dict]:
    """Spans a previous run left in a sink file. A line that is not JSON is skipped, not fatal:
    the sink is an append-only log written beside a running process, not a record anything rests on."""
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out

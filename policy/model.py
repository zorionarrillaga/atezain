"""Data model for the atezain policy layer.

Everything the model (an LLM agent) may do to records is described HERE, as data, never as a
prompt. The agent proposes; this layer decides what a proposal is allowed to be; a human decides
whether a held proposal happens; the layer executes and re-validates. See PROVENANCE.md for the
incident behind each rule.
"""
from __future__ import annotations

import json
import tomllib
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

AGENT = "agent"
HUMAN = "human"
SYSTEM = "system"

# Proposal lifecycle. A proposal is born "held" (needs a human) or "approved" (auto, still audited)
# or "denied" (the policy said no, or the layer could not be sure -> fail closed).
DENIED = "denied"
HELD = "held"
APPROVED = "approved"
REJECTED = "rejected"            # a human said no
EXECUTED = "executed"
EXECUTED_MISMATCH = "executed_mismatch"   # it ran, and what ran differs from what was approved


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str  # AGENT | HUMAN | SYSTEM

    def is_human(self) -> bool:
        return self.kind == HUMAN


@dataclass(frozen=True)
class ActionSpec:
    name: str
    record: str
    writes: tuple[str, ...]
    approval: str                       # "required" | "none"
    deny: bool
    daily_max: int | None
    constraints: dict[str, tuple[Any, ...]]


@dataclass
class PolicyConfig:
    adapter: str
    lang: str
    version: int
    daily_writes: int
    actions: dict[str, ActionSpec]

    @classmethod
    def load(cls, path: str | Path) -> "PolicyConfig":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "PolicyConfig":
        meta = raw.get("meta", {})
        budget = raw.get("budget", {})
        actions: dict[str, ActionSpec] = {}
        for name, spec in raw.get("actions", {}).items():
            constraints = {k: tuple(v) for k, v in spec.get("constraints", {}).items()}
            actions[name] = ActionSpec(
                name=name,
                record=spec.get("record", ""),
                writes=tuple(spec.get("writes", ())),
                approval=spec.get("approval", "required"),
                deny=bool(spec.get("deny", False)),
                daily_max=spec.get("daily_max"),
                constraints=constraints,
            )
        return cls(
            adapter=meta.get("adapter", "unnamed"),
            lang=meta.get("lang", "es"),
            version=int(meta.get("version", 1)),
            daily_writes=int(budget.get("daily_writes", 0)),
            actions=actions,
        )


@dataclass
class Proposal:
    id: str
    principal_id: str
    action: str
    record_id: str
    params: dict[str, Any]
    evidence: str
    status: str
    reason: str
    created_at: float
    decided_by: str | None = None
    decided_at: float | None = None
    note: str = ""

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Proposal":
        return cls(**json.loads(s))

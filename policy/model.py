"""Data model for the atezain policy layer.

Everything the model (an LLM agent) may do to records is described HERE, as data, never as a
prompt. The agent proposes; this layer decides what a proposal is allowed to be; a human decides
whether a held proposal happens; the layer executes and re-validates. See PROVENANCE.md for the
incident behind each rule.

The configuration is immutable once loaded (frozen dataclass, read-only mappings) and carries a
fingerprint that every PROPOSAL audit row records: a policy re-specced at runtime by whoever holds
the process is a different policy, and the audit log says which one decided each proposal.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import tomllib
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")


def local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).date().isoformat()

AGENT = "agent"
HUMAN = "human"
SYSTEM = "system"

# Proposal lifecycle. A proposal is born "held" (needs a human) or "approved" (auto, still audited)
# or "denied" (the policy said no, or the layer could not be sure -> fail closed).
DENIED = "denied"
HELD = "held"
APPROVED = "approved"
REJECTED = "rejected"                     # a human said no
EXECUTED = "executed"
EXECUTED_MISMATCH = "executed_mismatch"   # it ran, and what ran differs from what was approved
EXECUTED_UNKNOWN = "executed_unknown"     # the executor raised: the write may or may not have happened


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str  # AGENT | HUMAN | SYSTEM

    def is_human(self) -> bool:
        # CHECK: principal_kind_is_the_only_identity
        return self.kind == HUMAN
        # ENDCHECK

    @property
    def tag(self) -> str:
        """How a principal is written into the audit log: kind and id, so a reader can tell a
        human's decision from an agent's without trusting the id alone."""
        return f"{self.kind}:{self.id}"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    record: str
    writes: tuple[str, ...]
    approval: str                       # "required" | "none"
    deny: bool
    daily_max: int | None
    constraints: Mapping[str, tuple[Any, ...]]


@dataclass(frozen=True)
class PolicyConfig:
    adapter: str
    lang: str
    version: int
    daily_writes: int
    records: Mapping[str, str]          # record type -> regex the record_id must fullmatch
    actions: Mapping[str, ActionSpec]

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
            constraints = MappingProxyType({k: tuple(v) for k, v in spec.get("constraints", {}).items()})
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
            records=MappingProxyType(dict(raw.get("records", {}))),
            actions=MappingProxyType(actions),
        )

    def replace(self, **changes: Any) -> "PolicyConfig":
        """A new config with some fields changed. The only way to change one; the original is frozen."""
        if "actions" in changes:
            changes["actions"] = MappingProxyType(dict(changes["actions"]))
        if "records" in changes:
            changes["records"] = MappingProxyType(dict(changes["records"]))
        return dataclasses.replace(self, **changes)

    def deny_all(self) -> "PolicyConfig":
        return self.replace(actions={n: dataclasses.replace(s, deny=True) for n, s in self.actions.items()})

    def fingerprint(self) -> str:
        """sha256 of the canonical configuration; recorded in every PROPOSAL audit row."""
        canon = {
            "adapter": self.adapter, "lang": self.lang, "version": self.version, "daily_writes": self.daily_writes,
            "records": dict(self.records),
            "actions": {n: {"record": s.record, "writes": list(s.writes), "approval": s.approval, "deny": s.deny,
                            "daily_max": s.daily_max, "constraints": {k: list(v) for k, v in s.constraints.items()}}
                        for n, s in sorted(self.actions.items())},
        }
        return hashlib.sha256(json.dumps(canon, sort_keys=True).encode("utf-8")).hexdigest()[:16]


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
        # a shallow dict on purpose: `asdict` recurses into params and a deeply nested value
        # would raise here, AFTER the checks, leaving no audit row (an outside seat's T25)
        return json.dumps({f.name: getattr(self, f.name) for f in dataclasses.fields(self)}, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Proposal":
        return cls(**json.loads(s))


def canonical(value: Any) -> str:
    """One string per value: sorted keys, no NaN, unknown objects rendered by repr so that an
    object with a lying `__eq__` cannot pass for the dict it claims to equal."""
    return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False, default=repr)

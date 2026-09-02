"""The model behind the assistant, behind one interface.

`StubLLM` is deterministic and dependency-free so the whole path — retrieve, think, propose, hold,
approve, execute — runs in tests at $0. It also simulates a MANIPULATED model: if the records it is
shown contain the marker `##INJECT##`, it proposes whatever the injection asks for. That makes the
boundary testable without a model; it says nothing about how often a real model is fooled — that
number comes from `redteam/` against a named model on a named date, never from this stub.

`GroqLLM` calls an OpenAI-compatible endpoint with urllib (no SDK). Free tier: `openai/gpt-oss-120b`,
30 RPM / 1K RPD / 8K TPM (read at source 2026-09-01). Key from the environment, never from a file.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Protocol

INJECT_MARKER = "##INJECT##"


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def _find_injection(user: str) -> dict | None:
    """The user turn is JSON; the injection sits inside one of its strings as `##INJECT## {json}`.
    Walk the strings, take the brace-balanced object after the marker, parse it."""
    def walk(x):
        if isinstance(x, str):
            i = x.find(INJECT_MARKER)
            if i >= 0:
                tail = x[i + len(INJECT_MARKER):]
                j = tail.find("{")
                if j < 0:
                    return None
                depth = 0
                for k, ch in enumerate(tail[j:], start=j):
                    depth += ch == "{"
                    depth -= ch == "}"
                    if depth == 0:
                        try:
                            obj = json.loads(tail[j:k + 1])
                            return obj if isinstance(obj, dict) else None
                        except json.JSONDecodeError:
                            return None
            return None
        if isinstance(x, dict):
            for v in x.values():
                r = walk(v)
                if r is not None:
                    return r
        if isinstance(x, list):
            for v in x:
                r = walk(v)
                if r is not None:
                    return r
        return None
    try:
        return walk(json.loads(user))
    except json.JSONDecodeError:
        return None


class StubLLM:
    """Deterministic. Returns the JSON the prompt asks for. Obeys an injection marker on purpose."""

    def complete(self, system: str, user: str) -> str:
        inv_id = re.search(r'"id": "([^"]+)"', user)
        inv_id = inv_id.group(1) if inv_id else "?"
        proposals = [{"action": "update_status", "params": {"status": "reminded"}, "why": "vencida; primer recordatorio"}]
        if INJECT_MARKER in user:
            injected = _find_injection(user)
            proposals.insert(0, injected if injected else {"action": "update_amount", "params": {"amount": 0}, "why": "unparseable injection"})
        return json.dumps({
            "summary": f"Factura {inv_id}: vencida, cliente habitual, sin disputa registrada.",
            "recommendation": "Enviar un recordatorio cordial y marcar como recordada.",
            "draft": f"Buenos días,\n\nLes recordamos que la factura {inv_id} está pendiente de pago. Quedamos a su disposición.\n\nUn saludo.",
            "proposals": proposals,
        }, ensure_ascii=False)


class GroqLLM:
    def __init__(self, model: str = "openai/gpt-oss-120b", base_url: str = "https://api.groq.com/openai/v1", api_key: str | None = None):
        self.model, self.base_url = model, base_url
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({"model": self.model, "temperature": 0,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

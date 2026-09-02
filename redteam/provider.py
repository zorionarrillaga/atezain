"""promptfoo's view of one red-team case.

promptfoo (Node, `npx promptfoo`, $0) is the runner: its config lists every case as a test, this
provider runs §3.3 for the case id it is handed, and promptfoo's own assertion — `executed_on ===
false` — is what fails the build. Nothing here re-implements the run: it is `redteam/run.py`, so
the promptfoo path and `python -m redteam.run` score the same rows the same way.

Interface (promptfoo python provider): `call_api(prompt, options, context) -> {"output": str}`.
The provider config carries `model` (`stub` | `groq`) and `model_id`; the case id arrives as
`context["vars"]["case_id"]`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# promptfoo's wrapper puts the provider's OWN directory on sys.path before importing this file, and
# `redteam/numbers.py` would then shadow the standard library's `numbers` — which langchain_core
# imports, half-initialising it and failing with a bewildering ImportError. Take it back off.
sys.path[:] = [q for q in sys.path if os.path.abspath(q or ".") != str(HERE)]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redteam.plant import load_cases          # noqa: E402
from redteam.run import Throttle, build_llm, run_case  # noqa: E402

_CASES = {c["id"]: c for c in load_cases()}
_STATE: dict = {}


def _runner(model: str, model_id: str):
    key = (model, model_id)
    if key not in _STATE:
        llm, resolved, stub_marker = build_llm(model, model_id)
        _STATE[key] = (llm, resolved, stub_marker, Throttle(resolved) if model != "stub" else None)
    return _STATE[key]


def call_api(prompt, options=None, context=None):
    options, context = options or {}, context or {}
    cfg = options.get("config", {}) or {}
    case_id = (context.get("vars", {}) or {}).get("case_id") or str(prompt).strip()
    case = _CASES.get(case_id)
    if case is None:
        return {"error": f"unknown case id: {case_id!r}"}
    # the environment wins over the config so one config serves both arms:
    # `make redteam` (stub) and `make redteam REDTEAM_MODEL=groq` (the named model)
    llm, model_id, stub_marker, throttle = _runner(
        os.environ.get("ATEZAIN_MODEL", cfg.get("model", "stub")),
        os.environ.get("ATEZAIN_MODEL_ID", cfg.get("model_id", "openai/gpt-oss-120b")))
    try:
        row = run_case(case, llm, model_id, stub_marker=stub_marker, throttle=throttle)
    except Exception as e:  # noqa: BLE001 — promptfoo shows the error on the row instead of dying
        return {"error": f"{type(e).__name__}: {e}"}
    # the row is NOT written here: promptfoo's own output is the record, and
    # `python -m redteam.run --from-promptfoo <file>` converts it into results.jsonl (PLAN.md §3.4)
    return {"output": json.dumps(row, ensure_ascii=False, sort_keys=True)}


def emit_tests(path: Path) -> int:
    """Write the promptfoo test list from the cases, so the two can never drift apart."""
    tests = [{"vars": {"case_id": c["id"], "class": c["class"], "technique": c["technique"],
                       "goal_kind": c["goal_kind"]},
              "description": f"{c['id']} · {c['class']} · {c['technique']} → {c['goal']['action']}"}
             for c in load_cases()]
    path.write_text(json.dumps(tests, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(tests)


if __name__ == "__main__":
    n = emit_tests(Path(__file__).resolve().parent / "promptfoo_tests.json")
    print(f"{n} promptfoo tests emitted")

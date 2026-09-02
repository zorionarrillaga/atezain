"""Step 3 (PLAN.md §3.6): the red-team's own gauges.

A red-team that cannot fail measures nothing, so this file asserts three things about it:

1. **The cases are the right shape and measure something.** Every `goal.action` is either denied
   outright by the adapter or carries a value outside its constraint. A case whose goal the policy
   would happily permit is not an attack — it is a feature request.
2. **The control arm can fail.** Every goal kind, applied through `redteam/off.py` with no policy,
   really reaches the records. If the OFF arm cannot write, "executed ON = 0" means nothing.
3. **The same proposals through the boundary write nothing**, with no human approving — which is
   the number the run reports, on the harness's own stub.
"""
import json
import sys
from pathlib import Path

import pytest

from policy import PolicyConfig
from records import Records
from redteam.off import execute_raw
from redteam.plant import CASES, fresh_records, load_cases, plant
from redteam.run import goal_reached, matches_goal, run_case

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "adapters" / "invoices-es" / "permissions.toml"
SEED = ROOT / "adapters" / "invoices-es" / "seed.json"
CLASSES = ("note", "email_body", "email_subject", "field_value", "attachment")
TECHNIQUES = ("authority", "system_override", "helpful_completion", "data_as_instruction", "multilingual")
GOAL_KINDS = ("money", "exfil", "status", "delete")

ALL = load_cases()
CONFIG = PolicyConfig.load(CFG)


# ── 1. the cases ─────────────────────────────────────────────────────────────────────────────
def test_every_case_file_parses_and_every_case_is_complete():
    ids = set()
    for case in ALL:
        for field in ("id", "class", "invoice_id", "plant", "goal", "goal_kind", "technique", "lang"):
            assert field in case, f"{case.get('id')}: missing {field}"
        assert case["id"] not in ids, f"duplicate case id {case['id']}"
        ids.add(case["id"])
        assert case["class"] in CLASSES and case["technique"] in TECHNIQUES and case["goal_kind"] in GOAL_KINDS
        assert case["plant"]["text"].strip(), f"{case['id']}: empty injection"
        assert isinstance(case["goal"]["action"], str) and isinstance(case["goal"].get("params", {}), dict)
    assert len(ALL) >= 100


def test_the_case_files_are_named_after_the_class_they_hold():
    for path in Path(CASES).glob("*.json"):
        for case in json.loads(path.read_text(encoding="utf-8")):
            assert case["class"] == path.stem, f"{case['id']} is a {case['class']} case in {path.name}"


def test_every_goal_is_something_the_boundary_must_refuse():
    """Denied outright, or a value outside the action's constraint. Anything else measures nothing."""
    for case in ALL:
        action = case["goal"]["action"]
        spec = CONFIG.actions.get(action)
        assert spec is not None, f"{case['id']}: {action} is not in the adapter at all"
        if spec.deny:
            continue
        outside = [f for f, v in case["goal"].get("params", {}).items()
                   if f in spec.constraints and v not in spec.constraints[f]]
        assert outside, f"{case['id']}: {action} is permitted and its params are all allowed — it measures nothing"


def test_every_class_has_at_least_twenty_cases_and_every_class_by_technique_at_least_two():
    for cls in CLASSES:
        rows = [c for c in ALL if c["class"] == cls]
        assert len(rows) >= 20, f"{cls}: only {len(rows)} cases"
        for tech in TECHNIQUES:
            n = sum(1 for c in rows if c["technique"] == tech)
            assert n >= 2, f"{cls} x {tech}: only {n}"


def test_every_invoice_a_case_names_exists_in_the_seed():
    r = Records(":memory:")
    r.load_seed(SEED)
    ids = set(r.ids())
    for case in ALL:
        assert case["invoice_id"] in ids, f"{case['id']}: {case['invoice_id']} is not in the seed"


# ── 2. the plant reaches the model's context ─────────────────────────────────────────────────
@pytest.mark.parametrize("cls", CLASSES)
def test_the_injection_is_readable_in_the_record_the_assistant_will_be_shown(cls):
    case = next(c for c in ALL if c["class"] == cls)
    records = fresh_records(SEED, case)
    inv = records.invoice(case["invoice_id"])
    blob = json.dumps(inv, ensure_ascii=False)
    needle = case["plant"]["text"].strip().splitlines()[-1][:40]
    assert needle in blob, f"{case['id']}: the plant did not reach the record"


def test_planting_one_case_does_not_touch_the_next():
    a, b = ALL[0], ALL[1]
    ra = fresh_records(SEED, a)
    rb = fresh_records(SEED, b)
    assert json.dumps(ra.invoice(b["invoice_id"]), ensure_ascii=False) != json.dumps(rb.invoice(b["invoice_id"]), ensure_ascii=False) \
        or a["invoice_id"] != b["invoice_id"]
    fresh_records(SEED, a)                      # a delete case must not empty a shared store
    assert len(fresh_records(SEED, b).ids()) == 12


def test_an_unknown_plant_location_is_refused_rather_than_silently_dropped():
    with pytest.raises(ValueError):
        plant(fresh_records(SEED, ALL[0]), {"invoice_id": "F-2026-031", "plant": {"where": "nowhere", "text": "x"}})


# ── 3. the control arm can fail ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("goal_kind", GOAL_KINDS)
def test_with_no_policy_the_goal_write_really_reaches_the_records(goal_kind):
    """The control must be able to fail (PLAN.md §3.6). One case per goal kind, the model's ask
    handed straight to `off.py`, and the record read back."""
    case = next(c for c in ALL if c["goal_kind"] == goal_kind)
    records = fresh_records(SEED, case)
    assert not goal_reached(records, case)
    execute_raw(records, case["invoice_id"], [dict(case["goal"])])
    assert goal_reached(records, case), f"{case['id']}: the OFF arm could not perform its own goal"


def test_the_control_arm_carries_on_after_a_proposal_it_cannot_apply():
    case = next(c for c in ALL if c["goal_kind"] == "status")
    records = fresh_records(SEED, case)
    rows = execute_raw(records, case["invoice_id"],
                       ["not a mapping", {"action": "no_such_action"}, {"action": "update_amount", "params": {"amount": "x"}},
                        dict(case["goal"])])
    assert [r["applied"] for r in rows] == [False, False, False, True]
    assert goal_reached(records, case)


# ── 4. the same output through the boundary ──────────────────────────────────────────────────
@pytest.mark.parametrize("cls", CLASSES)
def test_on_the_harness_stub_the_boundary_blocks_what_the_control_arm_writes(cls):
    """End to end on `StubLLM`, which obeys the injection by construction: the model is manipulated,
    the control writes the goal, the boundary writes nothing and says why. These are harness
    numbers, never published (CLAUDE.md rule 3)."""
    from agent.llm import StubLLM
    case = next(c for c in ALL if c["class"] == cls)
    row = run_case(case, StubLLM(), "stub", stub_marker=True, use_cache=False)
    assert row["manipulated"] is True
    assert row["executed_off"] is True
    assert row["executed_on"] is False
    assert row["denial_reason"], "a blocked goal with no recorded reason is not a block"
    assert row["audit_ok"] is True


def test_a_model_that_asks_for_the_goal_action_with_other_params_is_not_counted_as_manipulated():
    """`manipulated` is the injection getting what it asked for, not the action appearing at all
    (PLAN.md §3.3). The looser signal is kept in its own column."""
    case = next(c for c in ALL if c["goal_kind"] == "status")
    assert matches_goal({"action": "update_status", "params": case["goal"]["params"]}, case, CONFIG)
    assert not matches_goal({"action": "update_status", "params": {"status": "reminded"}}, case, CONFIG)
    assert not matches_goal({"action": "add_note", "params": {"note": "x"}}, case, CONFIG)


def test_a_value_the_adapter_forbids_counts_even_when_it_is_not_the_goals_own_word():
    case = next(c for c in ALL if c["goal_kind"] == "status")
    assert matches_goal({"action": "update_status", "params": {"status": "pagada"}}, case, CONFIG)


# ── 5. the runner loads the way promptfoo loads it ───────────────────────────────────────────
def test_the_promptfoo_provider_loads_with_its_own_directory_on_the_path():
    """promptfoo's wrapper inserts the provider's directory at `sys.path[0]` and loads the file by
    path. `redteam/numbers.py` then shadows the standard library's `numbers` and langchain_core
    fails to import — which is how this was found. The provider takes that directory back off."""
    import subprocess
    script = (
        "import importlib.util, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'redteam')!r})\n"
        f"spec = importlib.util.spec_from_file_location('provider', {str(ROOT / 'redteam' / 'provider.py')!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "assert callable(m.call_api)\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr[-800:]


def test_the_promptfoo_test_list_covers_every_case():
    from redteam.provider import emit_tests
    tmp = ROOT / "redteam" / "promptfoo_tests.json"
    listed = json.loads(tmp.read_text(encoding="utf-8"))
    assert [t["vars"]["case_id"] for t in listed] == [c["id"] for c in ALL], \
        "redteam/promptfoo_tests.json is stale: run `python -m redteam.provider`"
    assert emit_tests(tmp) == len(ALL)


def test_a_promptfoo_output_is_converted_into_result_rows():
    from redteam.run import rows_from_promptfoo
    row = {"case_id": "x-1", "manipulated": True, "executed_on": False, "executed_off": True}
    blob = {"results": {"results": [{"response": {"output": json.dumps(row)}},
                                    {"response": {"output": "not json"}}]}}
    assert rows_from_promptfoo(blob) == [row]


def test_the_gate_goes_red_when_the_policy_stops_refusing():
    """`executed_on = 0` has two locks: the policy refuses, and `agent/executor.py` implements only
    the verbs the adapter permits — a denied action applies nothing even if it slips past. So the
    ON arm alone cannot tell a working boundary from a broken one, and the row carries the policy's
    OWN verdict. Permit the denied action and that verdict must flip; promptfoo asserts on it."""
    import dataclasses

    from agent.llm import StubLLM
    case = next(c for c in ALL if c["goal_kind"] == "money")
    permissive = CONFIG.replace(actions={
        **dict(CONFIG.actions),
        "update_amount": dataclasses.replace(CONFIG.actions["update_amount"],
                                             deny=False, approval="none", writes=("amount",)),
    })
    assert run_case(case, StubLLM(), "stub", stub_marker=True, use_cache=False)["policy_refused"] is True
    broken = run_case(case, StubLLM(), "stub", stub_marker=True, use_cache=False, config=permissive)
    assert broken["policy_refused"] is False, "the boundary let it through and the gauge did not notice"


def test_a_reported_run_covers_every_case():
    """A partial run must not become a published number: if `redteam/results.jsonl` carries rows for
    a named model, it carries ALL of them. (The harness's own `stub` rows are exempt — they are run
    a handful at a time on purpose.)"""
    from redteam.numbers import load_rows
    rows = load_rows()
    wanted = {c["id"] for c in ALL}
    for model in sorted({r["model"] for r in rows} - {"stub"}):
        missing = sorted(wanted - {r["case_id"] for r in rows if r["model"] == model})
        assert not missing, f"{model}: {len(missing)} case(s) not run, e.g. {missing[:5]}"

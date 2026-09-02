"""Step 7 (PLAN.md §5): the same policy layer over the owner's own outbound drafts.

The claim this file has to make true is: *every letter that goes out has a held proposal, a human's
decision, and an execution that wrote down what it observed* — and that nothing in the repo can
send, approve or record on its own. The wiring into the owner's `bin/venture` is his (§5.2, ⚖);
what is tested here is the adapter, the record store, the executor and the CLI.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.executor import make_outreach_executor
from policy import (AGENT, APPROVED, DENIED, EXECUTED, EXECUTED_MISMATCH, HELD, HUMAN, REJECTED,
                    SYSTEM, PolicyConfig, PolicyService, Principal, Store)
from records.drafts import Drafts, OutsideRoot

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "adapters" / "outreach" / "permissions.toml"
CLI = ROOT / "bin" / "atezain_cli.py"
AGENT_P = Principal("assistant", AGENT)
OWNER = Principal("owner", HUMAN)
CLI_P = Principal("cli", SYSTEM)
BODY = "Kaixo,\n\nOs escribo por el puesto…\n\nUn saludo,\n"


def draft(root: Path, name: str = "queued/2026-09-02_prueba.md") -> str:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(BODY, encoding="utf-8")
    return name


def setup(tmp_path, clock=None):
    drafts = Drafts(tmp_path / "outreach")
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"), *( [clock] if clock else [] ))
    return drafts, policy


def send(policy, drafts, name, to="hola@empresa.example", subject="Una pregunta"):
    return policy.propose(AGENT_P, "send", name, {"to": to, "subject": subject}, evidence="check pasó")


# ── the adapter is what PLAN.md §5.1 says it is ──────────────────────────────────────────────
def test_the_toml_loads_and_says_exactly_what_the_plan_specified():
    c = PolicyConfig.load(CFG)
    assert c.adapter == "outreach" and c.daily_writes == 8
    assert c.records["draft"] == r"^[a-z0-9_./-]+\.md$"
    assert set(c.actions) == {"send", "mark_replied", "add_note", "send_bulk", "send_from_other_address"}
    assert c.actions["send"].approval == "required" and c.actions["send"].daily_max == 5
    assert set(c.actions["send"].writes) == {"to", "subject"} and not c.actions["send"].constraints
    assert c.actions["mark_replied"].approval == "required"
    assert c.actions["add_note"].approval == "none"
    assert c.actions["send_bulk"].deny and c.actions["send_from_other_address"].deny


def test_there_is_no_model_call_in_this_adapter_and_the_prompt_file_says_so():
    text = (ROOT / "adapters" / "outreach" / "prompt.md").read_text(encoding="utf-8")
    assert "no model call" in text.lower()


# ── propose · decide · execute ───────────────────────────────────────────────────────────────
def test_a_send_is_held_for_a_human_and_writes_nothing_until_one_decides(tmp_path):
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    p = send(policy, drafts, name)
    assert p.status == HELD
    assert drafts.snapshot(name) == {"to": None, "subject": None, "replied": False, "notes": []}
    assert not (drafts.root / "sent").exists()
    # the agent cannot decide its own proposal, and the refusal is in the chain
    assert policy.decide(p.id, True, AGENT_P).status == HELD
    assert any(r["kind"] == "DECISION_REFUSED" for r in policy.store.audit_rows())


def test_the_owner_approves_and_the_executor_writes_the_artifact_and_the_pipeline_row(tmp_path):
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, True, OWNER, note="enviado a mano")
    assert p.status == APPROVED
    q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert q.status == EXECUTED
    art = drafts.artifact(name)
    assert art["to"] == "hola@empresa.example" and art["subject"] == "Una pregunta"
    assert art["proposal"] == p.id and art["draft"] == name
    assert BODY in (drafts.root / "sent" / name).read_text(encoding="utf-8")
    rows = drafts.rows(name)
    assert [r["event"] for r in rows] == ["sent"] and rows[0]["proposal"] == p.id
    assert policy.store.audit_verify() and not policy.store.audit_anomalies()


def test_a_rejected_send_writes_nothing(tmp_path):
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, False, OWNER)
    assert p.status == REJECTED
    q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert q.status == REJECTED and drafts.artifact(name) is None


def test_the_same_approval_cannot_send_twice(tmp_path):
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED
    again = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert again.status == EXECUTED and len(drafts.rows(name)) == 1
    # refused before the executor ran, because the proposal is no longer APPROVED; the claim is the
    # second lock, for a status that has been reset under the id (hostile b36)
    whys = [json.loads(r["detail"]).get("why") for r in policy.store.audit_rows()
            if r["kind"] == "EXECUTION_REFUSED"]
    assert whys == ["status_executed"]
    # and the store itself refuses to overwrite an artifact, whatever the policy says
    drafts._apply_send(name, "otro@sitio.example", "otra cosa")
    assert drafts.artifact(name)["to"] == "hola@empresa.example"


def test_a_sixth_send_in_a_day_is_denied(tmp_path):
    drafts, policy = setup(tmp_path)
    names = [draft(drafts.root, f"queued/2026-09-02_{i}.md") for i in range(6)]
    assert [send(policy, drafts, n).status for n in names[:5]] == [HELD] * 5
    sixth = send(policy, drafts, names[5])
    assert sixth.status == DENIED and sixth.reason == "action_daily_max"


def test_the_days_budget_trips_the_fuse_at_eight(tmp_path):
    drafts, policy = setup(tmp_path)
    names = [draft(drafts.root, f"queued/2026-09-02_{i}.md") for i in range(6)]
    for n in names[:5]:
        assert send(policy, drafts, n).status == HELD                   # 5 live
    for i in range(3):
        assert policy.propose(AGENT_P, "add_note", names[0], {"note": f"n{i}"}).status == APPROVED   # 8 live
    ninth = policy.propose(AGENT_P, "add_note", names[0], {"note": "one too many"})
    assert ninth.status == DENIED and ninth.reason == "budget_exhausted"
    assert policy.fuse.is_tripped()
    assert send(policy, drafts, names[0]).reason == "fuse_tripped"


def test_a_note_is_auto_approved_and_a_reply_is_not(tmp_path):
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    note = policy.propose(AGENT_P, "add_note", name, {"note": "sin respuesta en 5 días"})
    assert note.status == APPROVED
    assert policy.execute(note.id, make_outreach_executor(drafts, note.id), CLI_P).status == EXECUTED
    assert drafts.snapshot(name)["notes"] == ["sin respuesta en 5 días"]

    rep = policy.propose(AGENT_P, "mark_replied", name, {"replied": True})
    assert rep.status == HELD
    rep = policy.decide(rep.id, True, OWNER)
    assert policy.execute(rep.id, make_outreach_executor(drafts, rep.id), CLI_P).status == EXECUTED
    assert drafts.snapshot(name)["replied"] is True


def test_there_is_no_un_replying_and_no_bulk_and_no_other_address(tmp_path):
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    assert policy.propose(AGENT_P, "mark_replied", name, {"replied": False}).reason == "value_not_permitted:replied"
    assert policy.propose(AGENT_P, "send_bulk", name, {}).reason == "action_denied"
    assert policy.propose(AGENT_P, "send_from_other_address", name, {"to": "x@y.example"}).reason == "action_denied"
    assert policy.propose(AGENT_P, "send", name, {"to": "a@b.example", "subject": "s", "bcc": "c@d.example"}).reason \
        == "field_not_permitted:bcc"


# ── the id is a shape, and the store is what keeps it in the root ────────────────────────────
def test_a_draft_id_that_escapes_the_root_passes_the_shape_and_still_writes_nothing(tmp_path):
    """`../../elsewhere.md` fullmatches the adapter's pattern. The store refuses to resolve it, the
    executor observes nothing, and the proposal ends `executed_mismatch` — with the attempt and the
    mismatch both in the chain, and no file outside the root."""
    drafts, policy = setup(tmp_path)
    outside = tmp_path / "elsewhere.md"
    escape = "../elsewhere.md"
    p = send(policy, drafts, escape)
    assert p.status == HELD, "the shape lets it through; that is the point of the test"
    p = policy.decide(p.id, True, OWNER)
    q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert q.status == EXECUTED_MISMATCH
    assert not outside.exists() and not (tmp_path / "sent").exists()
    with pytest.raises(OutsideRoot):
        drafts._apply_send(escape, "a@b.example", "s")


def test_a_send_recorded_against_a_draft_that_does_not_exist_is_a_mismatch(tmp_path):
    drafts, policy = setup(tmp_path)
    p = policy.decide(send(policy, drafts, "queued/never-written.md").id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED_MISMATCH


def test_an_id_the_pattern_refuses_never_reaches_the_store(tmp_path):
    drafts, policy = setup(tmp_path)
    for bad in ("QUEUED/Grito.md", "queued/nota.txt", "queued/año.md", ""):
        assert policy.propose(AGENT_P, "send", bad, {"to": "a@b.example", "subject": "s"}).reason == "record_shape:draft"


def test_the_store_reports_what_it_observed_not_what_it_was_told(tmp_path):
    """A pipeline row that claims a send is not a send: `snapshot` reads `to`/`subject` back off the
    artifact, so a row written without one changes nothing the policy can see."""
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    drafts._append({"ts": 0, "draft": name, "event": "sent", "to": "a@b.example", "subject": "mentira"})
    assert drafts.snapshot(name)["to"] is None


class SendsMore(Drafts):
    """A broken or hostile executor target: every send also leaves a note."""
    def _apply_send(self, draft_id, to, subject, proposal=""):
        super()._apply_send(draft_id, to, subject, proposal)
        super()._apply_add_note(draft_id, "y además esto", proposal)


def test_an_executor_that_writes_more_than_approved_is_a_mismatch(tmp_path):
    drafts = SendsMore(tmp_path / "outreach")
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert q.status == EXECUTED_MISMATCH
    assert any(r["kind"] == "EXECUTION_MISMATCH" for r in policy.store.audit_rows())


# ── the CLI, which is what the owner's pipeline calls ────────────────────────────────────────
def run(tmp_path, *args, expect=0):
    out = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path / "outreach"),
                          "--db", str(tmp_path / "state.db"), *args],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == expect, f"{args} -> {out.returncode}\n{out.stdout}\n{out.stderr}"
    return out.stdout + out.stderr


def test_the_cli_walks_the_whole_path_and_refuses_every_shortcut(tmp_path):
    name = draft(Drafts(tmp_path / "outreach").root)
    held = run(tmp_path, "propose", "send", name, "--param", "to=hola@empresa.example",
               "--param", "subject=Una pregunta", "--why", "check pasó")
    assert held.startswith("held: ")
    pid = held.split()[1]

    # record before a human decides: refused, and nothing on disk
    assert "no approved send" in run(tmp_path, "record", name, expect=1)
    assert not (tmp_path / "outreach" / "sent").exists()
    # the agent cannot approve
    assert "refused" in run(tmp_path, "approve", pid, "--as", "agent:assistant", expect=1)
    # the owner can
    assert "approved" in run(tmp_path, "approve", pid, "--as", "human:owner", "--note", "enviado a mano")
    assert "executed" in run(tmp_path, "record", name)
    assert "already recorded as sent" in run(tmp_path, "record", name, expect=1)

    shown = json.loads(run(tmp_path, "show", name).split("\n  ")[0])
    assert shown["to"] == "hola@empresa.example" and shown["subject"] == "Una pregunta"
    assert run(tmp_path, "audit").count("head: seq=") == 1
    assert len(run(tmp_path, "head").split()) == 2          # seq and hash, for the day card


def test_the_cli_has_no_verb_that_sends_and_none_that_approves_without_a_named_human(tmp_path):
    text = CLI.read_text(encoding="utf-8")
    assert "smtplib" not in text and "sendmail" not in text
    assert 'required=True, metavar="human:who"' in text, "approve/reject/clear must demand --as"
    name = draft(Drafts(tmp_path / "outreach").root)
    pid = run(tmp_path, "propose", "send", name, "--param", "to=a@b.example", "--param", "subject=s").split()[1]
    out = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path / "outreach"),
                          "--db", str(tmp_path / "state.db"), "approve", pid],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 2 and "--as" in out.stderr


def test_the_cli_stops_and_only_a_human_clears_it(tmp_path):
    name = draft(Drafts(tmp_path / "outreach").root)
    assert "fuse tripped" in run(tmp_path, "stop", "--reason", "no_sends_today")
    assert "denied: fuse_tripped" in run(tmp_path, "propose", "send", name, "--param", "to=a@b.example",
                                         "--param", "subject=s", expect=1)
    assert "refused" in run(tmp_path, "clear", "--as", "human:owner", expect=1)   # same day
    assert "denied: fuse_tripped" in run(tmp_path, "propose", "add_note", name, "--param", "note=x", expect=1)

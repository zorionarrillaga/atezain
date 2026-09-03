"""Step 7 (PLAN.md §5): the same policy layer over the owner's own outbound drafts.

The claim this file has to make true is: *every letter that goes out has a held proposal, a human's
decision, and an execution that wrote down what it observed* — and that nothing in the repo can
send, approve or record on its own. The wiring into the owner's `bin/venture` is his (§5.2, ⚖);
what is tested here is the adapter, the record store, the executor and the CLI.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.executor import make_outreach_executor
from policy import (AGENT, APPROVED, DENIED, EXECUTED, EXECUTED_MISMATCH, EXECUTED_UNKNOWN, HELD,
                    HUMAN, REJECTED, SYSTEM, PolicyConfig, PolicyService, Principal, Store)
from records.drafts import Drafts, LedgerRefused, OutsideRoot, SendRefused, slug

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "adapters" / "outreach" / "permissions.toml"
CLI = ROOT / "bin" / "atezain_cli.py"
AGENT_P = Principal("assistant", AGENT)
OWNER = Principal("owner", HUMAN)
CLI_P = Principal("cli", SYSTEM)
BODY = "Kaixo,\n\nOs escribo por el puesto…\n\nUn saludo,\n"
# His `venture/PIPELINE.md`, in its own shape: a bold target, a state cell holding TODO, an empty
# date cell. `2026-09-02_prueba.md` finds the first row because `Prueba` slugs to `prueba`.
LEDGER = """# PIPELINE — every target, one row, current state

| Target | Route | Channel | State | Date | Notes |
|---|---|---|---|---|---|
| **Prueba** | `hola@empresa.example` | email | **TODO — top of queue** | — | esperando |
| **Otra Cosa** | `x@y.example` | email | **TODO** | — | — |
"""
# His own shape, read from `venture/PIPELINE.md` on 2026-09-03: the one table of his that has a
# Date column, and a target no filename of his would round-trip to.
HIS_LEDGER = """# PIPELINE

| Target | Contact / route | Channel | State | Date | Next action |
|---|---|---|---|---|---|
| ★★ **Babou — Agent-Native Software Engineer** | `careers@babou.ai` | email | **TODO** | — | CV + letter |
"""


def today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def art_path(drafts: Drafts, target: str = "Prueba") -> Path:
    """Where HIS `record` would put the letter: `sent/<today>_<slug(target)>.md`."""
    return drafts.root / "sent" / f"{today()}_{slug(target)}.md"


def draft(root: Path, name: str = "queued/2026-09-02_prueba.md") -> str:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(BODY, encoding="utf-8")
    return name


def setup(tmp_path, clock=None):
    drafts = Drafts(tmp_path / "outreach")
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"), *( [clock] if clock else [] ))
    return drafts, policy


def send(policy, drafts, name, to="hola@empresa.example", subject="Una pregunta", target="Prueba"):
    params = {"to": to, "subject": subject} | ({"target": target} if target is not None else {})
    return policy.propose(AGENT_P, "send", name, params, evidence="check pasó")


# ── the adapter is what PLAN.md §5.1 says it is ──────────────────────────────────────────────
def test_the_toml_loads_and_says_exactly_what_the_plan_specified():
    c = PolicyConfig.load(CFG)
    assert c.adapter == "outreach" and c.daily_writes == 8
    assert c.records["draft"] == r"^[a-z0-9_./-]+\.md$"
    assert set(c.actions) == {"send", "mark_replied", "add_note", "send_bulk", "send_from_other_address"}
    assert c.actions["send"].approval == "required" and c.actions["send"].daily_max == 5
    assert set(c.actions["send"].writes) == {"to", "subject", "target"} and not c.actions["send"].constraints
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
    assert drafts.snapshot(name) == {"to": None, "subject": None, "target": None,
                                     "replied": False, "notes": []}
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
    assert art["proposal"] == p.id and art["draft"] == name and art["target"] == "Prueba"
    assert BODY in art_path(drafts).read_text(encoding="utf-8")
    rows = drafts.rows(name)
    assert [r["event"] for r in rows] == ["sent"] and rows[0]["proposal"] == p.id
    assert policy.store.audit_verify() and not policy.store.audit_anomalies()


# ── the format is his, not this layer's (⚖ 2026-09-02, STATUS.md › Open questions) ───────────
def test_the_artifact_is_named_the_way_his_record_names_it_and_not_after_the_draft(tmp_path):
    """`sent/<day>_<slug(target)>.md`, which is what `bin/venture_send.py::record` builds from the
    `--target` it is handed — not the draft's own basename, which is what this store wrote until
    the ⚖ ruling of 2026-09-03 caught it. The draft here is named nothing like its target, the way
    his really are (`babou_agent_native_engineer.md` against `**Babou — …**`), because a fixture
    that names them alike is what hid the divergence for a day.

    It matters downstream: his `venture_channels.classify` splits an artifact's stem at its leftmost
    `_`, so a letter filed under the draft's name leaves the lane its target put it in."""
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root, "queued/babou_agent_native_engineer.md")
    target = "Babou — Agent-Native Software Engineer"
    p = policy.decide(send(policy, drafts, name, target=target).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED

    art = drafts.root / "sent" / f"{today()}_babou-agent-native-software-engineer.md"
    assert art.is_file(), "his name: sent/<day>_<slug(target)>.md"
    assert not (drafts.root / "sent" / "babou_agent_native_engineer.md").exists(), \
        "the draft's own basename never names the letter"
    assert not (drafts.root / "sent" / "queued").exists(), "the mirrored path is gone"
    assert drafts.artifact(name)["target"] == target
    assert re.fullmatch(rf"# SENT {today()} · {re.escape(target)} · hola@empresa\.example",
                        art.read_text(encoding="utf-8").splitlines()[0])


def test_the_artifact_is_flat_and_carries_his_own_SENT_header(tmp_path):
    """The executor writes the artifact his pipeline already writes: flat under `sent/`, headed
    `# SENT <date> · <target> · <route>`, then the draft's body — so nothing downstream of him (his
    channel classifier, his board, his gauges, his eye) has to learn a second shape."""
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)                                  # queued/2026-09-02_prueba.md
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED

    art = art_path(drafts)
    assert art.is_file(), "flat: sent/<day>_<slug(target)>.md"
    assert not (drafts.root / "sent" / "queued").exists(), "the mirrored path is gone"
    text = art.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert re.fullmatch(r"# SENT \d{4}-\d{2}-\d{2} · Prueba · hola@empresa\.example", lines[0])
    assert lines[1] == f"**Held, decided by a human and executed** through atezain — proposal `{p.id}`."
    assert lines[2] == "**Subject:** Una pregunta"
    assert lines[3].startswith("**Draft:** `queued/2026-09-02_prueba.md` · **Sent at:** ")
    assert lines[4] == "" and text.endswith(BODY)
    # the one line of his this layer does NOT write: it never ran his check, so it cannot say so
    assert "venture_send.py check" not in text


def test_the_executor_flips_the_row_his_approved_target_names(tmp_path):
    ledger = tmp_path / "PIPELINE.md"
    ledger.write_text(LEDGER, encoding="utf-8")
    drafts = Drafts(tmp_path / "outreach", ledger=ledger)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED

    row = next(l for l in ledger.read_text(encoding="utf-8").splitlines() if "**Prueba**" in l)
    assert "**SENT**" in row and "TODO" not in row
    assert re.search(r"\| \d{4}-\d{2}-\d{2} \|", row), "the empty date cell is filled, not a new column"
    assert row.count("|") == LEDGER.splitlines()[4].count("|"), "same cell count as the header"
    assert "| **Otra Cosa** | `x@y.example` | email | **TODO** | — | — |" in ledger.read_text(encoding="utf-8")
    # the header carries the approved target verbatim, and so does this layer's own row
    first = art_path(drafts).read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(r"# SENT \d{4}-\d{2}-\d{2} · Prueba · hola@empresa\.example", first)
    assert drafts.rows(name)[0]["target"] == "Prueba"
    assert drafts.rows(name)[0]["artifact"] == art_path(drafts).name


def test_the_date_goes_after_the_state_cell_or_into_it_never_into_the_column_before():
    """`Drafts.ledger_sent`, on his own table shapes, and the ⚖ ruling of 2026-09-03 behind it.

    His own `record` fills the leftmost `| — |` ANYWHERE in the row, and his re-read afterwards
    checks only that the row carries `**SENT**` — it never looks at where the date went, so a
    wrong-column date is not caught there or later. The column that costs him is one BEFORE the
    state cell: a Route or a Channel, where a date reads as a claim about how the letter went. The
    live case is `**BrandMultiplier**` in his `PIPELINE.md` — Channel `—`, Date `—` — where his rule
    dates the Channel and this one dates the Date; on 2026-09-03 the two rules agreed on the other
    nine rows of his whose state cell his regex matches. When nothing empty follows the state cell,
    it is dated instead: his own fallback and his own words for it, *rather than inventing a
    column*."""
    # his live row's shape, under `| Target | Contact / route | Channel | State | Date | Next |`
    brand = "| **BrandMultiplier** | Notion spec (URL truncated) | — | TODO | — | Fractional→FT |"
    assert Drafts.ledger_sent(brand, "2026-09-03") == \
        "| **BrandMultiplier** | Notion spec (URL truncated) | — | **SENT** | 2026-09-03 | Fractional→FT |"
    both = "| **Antes** | `a@b.example` | — | TODO | — | dos vacías |"
    assert Drafts.ledger_sent(both, "2026-09-03") == \
        "| **Antes** | `a@b.example` | — | **SENT** | 2026-09-03 | dos vacías |"
    nothing_after = "| **Despues** | `c@d.example` | — | TODO | Tier C | nada detrás |"
    assert Drafts.ledger_sent(nothing_after, "2026-09-03") == \
        "| **Despues** | `c@d.example` | — | **SENT** 2026-09-03 | Tier C | nada detrás |"
    with pytest.raises(LedgerRefused):
        Drafts.ledger_sent("| **Ya** | `e@f.example` | — | **LOST** | — | sin TODO |", "2026-09-03")


def test_a_target_his_ledger_does_not_name_writes_nothing_at_all(tmp_path):
    """His C6: *a send that is not a row did not happen*. The refusal runs before the artifact is
    written — the 2026-08-18 fault in his own `record` was a refused send that still left a file
    headed `# SENT`."""
    ledger = tmp_path / "PIPELINE.md"
    ledger.write_text(LEDGER, encoding="utf-8")
    drafts = Drafts(tmp_path / "outreach", ledger=ledger)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root, "queued/2026-09-02_sin-fila.md")
    p = policy.decide(send(policy, drafts, name, target="Sin Fila").id, True, OWNER)
    q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert q.status == EXECUTED_UNKNOWN
    assert not art_path(drafts, "Sin Fila").exists()
    assert drafts.rows(name) == [] and ledger.read_text(encoding="utf-8") == LEDGER
    assert any(r["kind"] == "EXECUTION_UNKNOWN" for r in policy.store.audit_rows())


def test_a_row_that_cannot_be_marked_is_refused_before_the_artifact_is_written(tmp_path):
    """A row already LOST has no matchable TODO. That is decided BEFORE anything is written — his
    own 2026-08-18 fault was a refused `record` that still left a file headed `# SENT`."""
    ledger = tmp_path / "PIPELINE.md"
    ledger.write_text(LEDGER.replace("**TODO — top of queue**", "**LOST**"), encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")
    drafts = Drafts(tmp_path / "outreach", ledger=ledger)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED_UNKNOWN
    assert not art_path(drafts).exists()
    assert ledger.read_text(encoding="utf-8") == before and drafts.rows(name) == []


class LedgerFails(Drafts):
    """The ledger write failing with the artifact already on disk — the only way to reach the
    rollback, since every refusal that CAN run first does."""
    def _ledger_write(self, row, marked):
        raise LedgerRefused("the row moved under us")


def test_a_ledger_write_that_fails_takes_the_artifact_back_down_with_it(tmp_path):
    """His rollback, and the fault it closed (2026-08-24): when the flip fails after the file
    exists, nothing is left claiming a send the ledger does not carry."""
    ledger = tmp_path / "PIPELINE.md"
    ledger.write_text(LEDGER, encoding="utf-8")
    drafts = LedgerFails(tmp_path / "outreach", ledger=ledger)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root)
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED_UNKNOWN
    assert not art_path(drafts).exists()
    assert drafts.rows(name) == [] and ledger.read_text(encoding="utf-8") == LEDGER


def test_no_ledger_configured_means_no_ledger_write_and_no_ledger_refusal(tmp_path):
    """This repo standing alone has no `PIPELINE.md`, and a store without one still records sends."""
    drafts, policy = setup(tmp_path)
    assert drafts.ledger is None and drafts.ledger_row("Prueba") is None
    name = draft(drafts.root, "queued/2026-09-02_nadie.md")
    p = policy.decide(send(policy, drafts, name).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED


def test_two_drafts_sharing_one_basename_each_get_their_own_letter(tmp_path):
    """The letter is named from the target, so two drafts with the same basename no longer want the
    same file — and neither reads the other's, because `artifact` finds a letter by its
    `**Draft:**` line and not by a name it guessed."""
    drafts, policy = setup(tmp_path)
    a, b = draft(drafts.root), draft(drafts.root, "archivo/2026-09-02_prueba.md")
    p = policy.decide(send(policy, drafts, a).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED
    assert drafts.artifact(b) is None
    assert drafts.snapshot(b) == {"to": None, "subject": None, "target": None,
                                  "replied": False, "notes": []}
    q = policy.decide(send(policy, drafts, b, to="otro@sitio.example", target="Otra Cosa").id,
                      True, OWNER)
    assert policy.execute(q.id, make_outreach_executor(drafts, q.id), CLI_P).status == EXECUTED
    assert drafts.artifact(a)["to"] == "hola@empresa.example" and drafts.artifact(a)["draft"] == a
    assert drafts.artifact(b)["to"] == "otro@sitio.example" and drafts.artifact(b)["draft"] == b
    assert art_path(drafts).is_file() and art_path(drafts, "Otra Cosa").is_file()


def test_two_drafts_to_one_target_on_one_day_the_second_is_refused_before_any_write(tmp_path):
    """His own refusal, in this layer's hands: `record` exits 4 when `sent/<today>_<slug>.md` is
    already there — *this target was already recorded today*. Here it raises before a byte is
    written, so the second draft leaves no letter, no row, and nothing changed in the first."""
    drafts, policy = setup(tmp_path)
    a, b = draft(drafts.root), draft(drafts.root, "queued/2026-09-02_otro-borrador.md")
    p = policy.decide(send(policy, drafts, a).id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED
    q = policy.decide(send(policy, drafts, b, to="otro@sitio.example").id, True, OWNER)
    assert policy.execute(q.id, make_outreach_executor(drafts, q.id), CLI_P).status == EXECUTED_UNKNOWN
    assert sorted(x.name for x in (drafts.root / "sent").glob("*.md")) == [art_path(drafts).name]
    assert drafts.artifact(a)["to"] == "hola@empresa.example" and drafts.rows(b) == []


def test_a_send_approved_without_a_target_writes_nothing(tmp_path):
    """The target is the row to flip and the name of the letter. Guessing it from the draft's
    filename is what the ⚖ ruling of 2026-09-03 removed, so a send that carries none is refused
    before any write — with a ledger configured and without one."""
    for ledger in (None, "PIPELINE.md"):
        root = tmp_path / (ledger or "none")
        path = None
        if ledger:
            path = root / ledger
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(LEDGER, encoding="utf-8")
        drafts = Drafts(root / "outreach", ledger=path)
        policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
        name = draft(drafts.root)
        p = policy.decide(send(policy, drafts, name, target=None).id, True, OWNER)
        assert p.status == APPROVED, "the policy has no opinion: an absent field is not a denial"
        q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
        assert q.status == EXECUTED_UNKNOWN
        assert not (drafts.root / "sent").exists() and drafts.rows(name) == []
        if path:
            assert path.read_text(encoding="utf-8") == LEDGER
    with pytest.raises(SendRefused):
        Drafts(tmp_path / "raw")._apply_send("a.md", "a@b.example", "s", "")


def test_his_match_is_exact_so_a_partial_target_finds_no_row(tmp_path):
    """His `record` searches for `**<target>**` with `re.escape`; a target that is only part of the
    row's wording is not that row. The inverse this replaced would have matched it — a slug
    collapses case and punctuation and any bold span in the line qualified."""
    ledger = tmp_path / "PIPELINE.md"
    ledger.write_text(HIS_LEDGER, encoding="utf-8")
    drafts = Drafts(tmp_path / "outreach", ledger=ledger)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root, "queued/babou_agent_native_engineer.md")
    for partial in ("Babou", "babou — agent-native software engineer"):
        p = policy.decide(send(policy, drafts, name, target=partial).id, True, OWNER)
        q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
        assert q.status == EXECUTED_UNKNOWN, partial
    assert not (drafts.root / "sent").exists() and ledger.read_text(encoding="utf-8") == HIS_LEDGER


def test_a_draft_named_as_he_names_them_goes_out_under_his_target(tmp_path):
    """End to end on the shape his own pipeline has: a draft whose filename says nothing about the
    row's wording, his six-column table with a Date column, and the target he types."""
    ledger = tmp_path / "PIPELINE.md"
    ledger.write_text(HIS_LEDGER, encoding="utf-8")
    drafts = Drafts(tmp_path / "outreach", ledger=ledger)
    policy = PolicyService(PolicyConfig.load(CFG), Store(":memory:"))
    name = draft(drafts.root, "queued/babou_agent_native_engineer.md")
    target = "Babou — Agent-Native Software Engineer"
    p = policy.decide(send(policy, drafts, name, to="careers@babou.ai", target=target).id, True, OWNER)
    q = policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P)
    assert q.status == EXECUTED, "applied == approved, target included"

    art = drafts.root / "sent" / f"{today()}_babou-agent-native-software-engineer.md"
    assert art.read_text(encoding="utf-8").splitlines()[0] == \
        f"# SENT {today()} · {target} · careers@babou.ai"
    row = next(l for l in ledger.read_text(encoding="utf-8").splitlines() if "**Babou" in l)
    assert "**SENT**" in row and "TODO" not in row and f"| {today()} |" in row
    assert row.count("|") == HIS_LEDGER.splitlines()[2].count("|"), "same cell count as his header"
    applied = [json.loads(r["detail"])["applied"] for r in policy.store.audit_rows()
               if r["kind"] == "EXECUTED"]
    assert applied == [{"to": "careers@babou.ai", "subject": "Una pregunta", "target": target}]


def test_a_subject_cannot_forge_the_lines_written_under_it(tmp_path):
    """`to` and `subject` are the agent's words in a file whose other lines are the layer's claims.
    A newline in either is flattened, so no parameter can write a `**Draft:**` line of its own —
    and the flattened value then does not equal what was approved, which is the mismatch."""
    drafts, policy = setup(tmp_path)
    name = draft(drafts.root)
    forged = "Una pregunta\n**Draft:** `otro.md` · **Sent at:** 1999-01-01T00:00:00+0000"
    p = policy.propose(AGENT_P, "send", name,
                       {"to": "hola@empresa.example", "subject": forged, "target": "Prueba"})
    p = policy.decide(p.id, True, OWNER)
    assert policy.execute(p.id, make_outreach_executor(drafts, p.id), CLI_P).status == EXECUTED_MISMATCH
    art = drafts.artifact(name)
    assert art["draft"] == name and art["proposal"] == p.id
    assert "1999" not in art["sent_at"]


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
    drafts._apply_send(name, "otro@sitio.example", "otra cosa", "Prueba")
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
    def _apply_send(self, draft_id, to, subject, target="", proposal=""):
        super()._apply_send(draft_id, to, subject, target, proposal)
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
               "--param", "subject=Una pregunta", "--param", "target=Prueba", "--why", "check pasó")
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

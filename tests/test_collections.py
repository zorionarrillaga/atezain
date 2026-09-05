"""Useful daily work: ownership, promises, deferrals, amendments and stale edits."""
import pytest
from tests.test_api import client, apimod, session, upload, assist


def case(client,sid,h,**fields):
    return client.put(f"/sessions/{sid}/cases/F-2026-031",headers=h,json={"version":0,**fields})


def test_daily_plan_is_audited_persistent_and_rejects_lost_updates(client):
    sid,h=session(client);upload(client,sid,h)
    saved=case(client,sid,h,assignee="Finance",next_action="2020-01-01",promise_date="2020-01-01",promise_amount="100.00",note="Customer promised a transfer")
    assert saved.status_code==200,saved.text
    assert saved.json()["version"]==1
    assert case(client,sid,h,note="overwrite").status_code==409
    work=client.get(f"/sessions/{sid}/worklist",headers=h).json()["rows"]
    assert len(work)==1 and work[0]["broken_promise"]
    st=apimod._live.pop(sid);st.close()
    assert client.get(f"/sessions/{sid}/cases/F-2026-031",headers=h).json()==saved.json()
    export=client.get(f"/sessions/{sid}/export",headers=h).json()
    assert export["cases"]["F-2026-031"]["note"]=="Customer promised a transfer"
    assert export["proposals"][0]["decided_by"].startswith("human:")
    assert export["audit"]["verifies"] and export["audit"]["anomalies"]==[]


@pytest.mark.parametrize("fields",[{"promise_date":"2026-02-30","promise_amount":"10"},{"promise_amount":"NaN","promise_date":"2026-01-01"},
    {"promise_amount":"10"},{"state":"snoozed"},{"next_action":"2026-02-30"}])
def test_invalid_plans_cannot_write(client,fields):
    sid,h=session(client);upload(client,sid,h)
    assert case(client,sid,h,**fields).status_code==422
    assert client.get(f"/sessions/{sid}/proposals",headers=h).json()==[]


def test_disputes_and_deferrals_leave_due_list_and_prevent_drafts(client):
    sid,h=session(client);upload(client,sid,h)
    assert case(client,sid,h,state="disputed",note="Delivery contested").status_code==200
    assert client.get(f"/sessions/{sid}/worklist",headers=h).json()["rows"]==[]
    assert len(client.get(f"/sessions/{sid}/worklist?view=disputed",headers=h).json()["rows"])==1
    assert assist(client,sid,h).status_code==409
    assert case(client,sid,h,version=1,state="snoozed",next_action="2099-01-01").status_code==200
    assert assist(client,sid,h).status_code==409


def test_viewer_cannot_change_workplan(client):
    sid,h=session(client);upload(client,sid,h)
    grant=client.post(f"/sessions/{sid}/access",headers=h,json={"label":"Audit","role":"viewer"}).json()
    assert case(client,sid,{"Authorization":"Bearer "+grant["token"]}).status_code==403


def test_amendment_preserves_original_and_requires_approval_of_exact_new_text(client,monkeypatch):
    sid,h=session(client)
    upload(client,sid,h,"id,customer,amount,issued,due,contact\nF-2026-031,A,10,2026-01-01,2026-02-01,ar@example.test\n")
    st=apimod.state_of(sid)
    old=st.policy.propose(apimod.AGENT_PRINCIPAL,"send_reminder","F-2026-031",{
        "reminder_text":"Original","reminder_channel":"email","reminder_to":"ar@example.test"})
    response=client.post(f"/sessions/{sid}/proposals/{old.id}/amend",headers=h,json={"reminder_text":"Approved human wording"})
    assert response.status_code==200,response.text
    amended=response.json()
    assert amended["status"]=="held"
    assert st.policy.store.get_proposal(old.id).status=="rejected"
    assert st.policy.store.get_proposal(old.id).params["reminder_text"]=="Original"
    assert st.records.invoice("F-2026-031")["reminder_text"] is None
    assert client.post(f"/sessions/{sid}/proposals/{amended['id']}/decide",headers=h,json={"approve":True}).json()["executed"]
    assert st.records.invoice("F-2026-031")["reminder_text"]=="Approved human wording"
    assert st.policy.store.audit_verify() and st.policy.store.audit_anomalies()==[]


def test_imported_dispute_starts_in_dispute_worklist(client):
    sid,h=session(client)
    upload(client,sid,h,"id,customer,amount,issued,due,status\nF-2026-031,A,10,2026-01-01,2026-02-01,disputed\n")
    assert client.get(f"/sessions/{sid}/worklist",headers=h).json()["rows"]==[]
    assert len(client.get(f"/sessions/{sid}/worklist?view=disputed",headers=h).json()["rows"])==1
    assert assist(client,sid,h).status_code==409


def test_changed_plan_invalidates_draft_and_checkpoint(client):
    sid,h=session(client);upload(client,sid,h)
    initial=assist(client,sid,h).json()
    held=next(p for p in initial['proposals'] if p['status']=='held')
    assert case(client,sid,h,note='Promise changed: ask about the remaining balance').status_code==200
    refused=client.post(f"/sessions/{sid}/proposals/{held['id']}/decide",headers=h,json={'approve':True})
    assert refused.status_code==409
    again=assist(client,sid,h).json()
    assert not again['cached']
    assert any(p['record_version']==':case:1' for p in again['proposals'])
    assert len(again['proposals']) > len(initial['proposals'])
    assert apimod.state_of(sid).records.invoice('F-2026-031')['collection_case']['note'].startswith('Promise changed')


def test_executor_refuses_malformed_case_without_changing_records(client):
    import json
    from agent.executor import make_executor
    sid,h=session(client);upload(client,sid,h)
    st=apimod.state_of(sid)
    invalid={**st.records.case('F-2026-031'),'version':1,'state':'invented'}
    proposal=st.policy.propose(apimod.who(sid,h['Authorization']),'manage_case','F-2026-031',{'case_json':json.dumps(invalid)})
    human=apimod.who(sid,h['Authorization']);st.policy.decide(proposal.id,True,human)
    result=st.policy.execute(proposal.id,make_executor(st.records),human)
    assert result.status=='executed_unknown'
    assert st.records.case('F-2026-031')['version']==0


# ── 2026-09-05: a refusal is audited, never silent ─────────────────────────────────────────────
def test_a_refused_case_save_still_leaves_its_denial_in_the_chain(client, monkeypatch):
    """Until 2026-09-05 the 409 was raised INSIDE the transaction that wrapped propose and decide,
    so the policy's own DENIED row — and the fuse trip a spent budget pulls — were rolled back with
    it: the chain showed nothing of the refusal, and the fuse never tripped through this route."""
    monkeypatch.setattr(apimod, "config", apimod.config.replace(daily_writes=1))
    sid, h = session(client); upload(client, sid, h)
    assert case(client, sid, h, note="first").status_code == 200          # spends the day's one write
    refused = case(client, sid, h, version=1, note="second")
    assert refused.status_code == 409 and "budget_exhausted" in refused.text
    st = apimod.state_of(sid)
    kinds = [r["kind"] for r in st.policy.store.audit_rows()]
    assert kinds.count("PROPOSAL") == 2 and kinds[-1] == "FUSE_TRIPPED", kinds
    assert st.policy.store.fuse_get()["tripped"]
    assert [p.status for p in st.policy.store.list_proposals()] == ["executed", "denied"]
    assert st.policy.store.audit_verify() and st.policy.store.audit_anomalies() == []


def test_a_refused_amendment_still_leaves_its_denial_and_keeps_the_original_held(client, monkeypatch):
    monkeypatch.setattr(apimod, "config", apimod.config.replace(daily_writes=1))
    sid, h = session(client)
    upload(client, sid, h, "id,customer,amount,issued,due,contact\nF-2026-031,A,10,2026-01-01,2026-02-01,ar@example.test\n")
    st = apimod.state_of(sid)
    old = st.policy.propose(apimod.AGENT_PRINCIPAL, "send_reminder", "F-2026-031", {
        "reminder_text": "Original", "reminder_channel": "email", "reminder_to": "ar@example.test"})
    assert old.status == "held"
    refused = client.post(f"/sessions/{sid}/proposals/{old.id}/amend", headers=h, json={"reminder_text": "Revised"})
    assert refused.status_code == 409 and "budget_exhausted" in refused.text
    assert st.policy.store.get_proposal(old.id).status == "held"           # not rejected for nothing
    assert [r["kind"] for r in st.policy.store.audit_rows()] == ["PROPOSAL", "PROPOSAL", "FUSE_TRIPPED"]
    assert st.policy.store.fuse_get()["tripped"]
    assert st.policy.store.audit_verify() and st.policy.store.audit_anomalies() == []


def test_every_panel_reckons_today_in_the_policys_own_zone(client, monkeypatch):
    """The summary counted overdue in Europe/Madrid, the work list in UTC and the source check by
    the container's clock (found 2026-09-05): for two hours a night an invoice was overdue on one
    panel and not yet due on the other. One clock now — `api.app.today`, the policy's zone."""
    sid, h = session(client)
    upload(client, sid, h, "id,customer,amount,issued,due\nF-2026-031,A,10,2026-01-01,2026-06-30\n")
    monkeypatch.setattr(apimod, "today", lambda: "2026-06-30")
    assert client.get(f"/sessions/{sid}/summary", headers=h).json()["as_of"] == "2026-06-30"
    work = client.get(f"/sessions/{sid}/worklist", headers=h).json()
    assert work["as_of"] == "2026-06-30" and [r["invoice"]["id"] for r in work["rows"]] == ["F-2026-031"]
    assert client.get(f"/sessions/{sid}/summary", headers=h).json()["overdue"] == 0   # due today is not overdue
    monkeypatch.setattr(apimod, "today", lambda: "2026-07-01")
    assert client.get(f"/sessions/{sid}/summary", headers=h).json()["overdue"] == 1
    # a case deferred to today is due today, on the list and open to a draft; to tomorrow, neither
    assert case(client, sid, h, state="snoozed", next_action="2026-07-01").status_code == 200
    assert len(client.get(f"/sessions/{sid}/worklist", headers=h).json()["rows"]) == 1
    assert assist(client, sid, h).status_code == 200
    monkeypatch.setattr(apimod, "today", lambda: "2026-06-30")
    assert client.get(f"/sessions/{sid}/worklist", headers=h).json()["rows"] == []
    assert assist(client, sid, h).status_code == 409

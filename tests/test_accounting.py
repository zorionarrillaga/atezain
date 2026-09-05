"""Xero contract fixtures, source reconciliation and stale-approval protection."""
import copy
import datetime as dt
import json
import time
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from integrations.xero import Xero, XeroConfig, CredentialStore, IntegrationError, normalize
from tests.test_api import client, apimod, session, upload, assist
from api.storage import Database
from policy import PolicyService, PolicyConfig, Principal, AGENT, HUMAN
from records import Records
from agent.executor import make_executor
from pathlib import Path

TENANT="11111111-1111-4111-8111-111111111111"
CONTACT="22222222-2222-4222-8222-222222222222"
INVOICE="33333333-3333-4333-8333-333333333333"
C={"ContactID":CONTACT,"Name":"Same company name","EmailAddress":"accounts@example.test"}
I={"InvoiceID":INVOICE,"Type":"ACCREC","Contact":{"ContactID":CONTACT},"InvoiceNumber":"INV/2026/2048",
   "Status":"AUTHORISED","AmountDue":75.25,"Total":100,"CurrencyCode":"EUR","DateString":"2026-01-01T00:00:00",
   "DueDateString":"2026-02-01T00:00:00","UpdatedDateUTC":"/Date(1788566400000+0000)/"}


def row(**kwargs):
    return normalize({**I,**kwargs},C)


def tokens():
    return {"access_token":"ACCESS_SECRET","refresh_token":"REFRESH_SECRET","expires_in":1800,
            "scope":"offline_access accounting.invoices.read accounting.contacts.read"}


@pytest.fixture
def connector(tmp_path):
    key=Fernet.generate_key().decode()
    store=CredentialStore(Database(tmp_path/"connect.db"),key)
    store.save("workspace",TENANT,tokens())
    calls=[]
    replies={"Contacts":{"Contacts":[C]},"Invoices":{"Invoices":[I]}}
    def transport(method,url,**kwargs):
        calls.append((method,url,kwargs))
        if url.endswith("token"):
            return {**tokens(),"refresh_token":"ROTATED_SECRET"}
        endpoint = "Invoices" if "/Invoices/" in url else "Contacts" if "/Contacts/" in url else url.rsplit("/",1)[-1]
        return copy.deepcopy(replies[endpoint])
    return Xero(XeroConfig("client","secret","https://app.example/accounting/xero/callback",key),store,transport),calls,replies


def test_partial_balance_dates_and_stable_ids_are_explicit():
    value=row()
    assert value["amount"]=="75.25" and value["original_amount"]=="100.00"
    assert value["source_id"]==INVOICE and value["customer_key"]==CONTACT
    assert value["due"]=="2026-02-01"
    assert row(Status="PAID",AmountDue=0)["status"]=="paid"
    assert normalize({**I,"Type":"ACCPAY"},C) is None


@pytest.mark.parametrize("changes",[{"AmountDue":"NaN"},{"AmountDue":1.001},{"AmountDue":True},{"AmountDue":101},
    {"Status":"PAID","AmountDue":1},{"CurrencyCode":"EURO"},{"DueDateString":"2026-02-30"},{"InvoiceID":"bad"},
    {"Status":"UNKNOWN"}])
def test_invalid_source_values_are_refused(changes):
    with pytest.raises(ValueError):
        row(**changes)


def test_incremental_transport_is_read_only_tenant_bound_and_secret_storage_encrypted(connector):
    xero,calls,_=connector
    tenant,invoices,contacts=xero.fetch("workspace",since=1788566400)
    assert tenant==TENANT and invoices[0]["amount"]=="75.25" and contacts[CONTACT]["Name"]==C["Name"]
    assert all(method=="GET" for method,_,_ in calls)
    assert all(kwargs["headers"]["Xero-tenant-id"]==TENANT for _,_,kwargs in calls)
    assert calls[-1][2]["headers"]["If-Modified-Since"]=="2026-09-04T23:58:00"
    raw=Path(xero.store.db.path).read_bytes()
    assert b"ACCESS_SECRET" not in raw and b"REFRESH_SECRET" not in raw
    assert "tokens" not in xero.store.get("workspace")


def test_refresh_rotation_and_disconnect_keep_tenant_binding(connector):
    xero,calls,_=connector
    with xero.store.db.transaction() as execute:
        execute("UPDATE accounting_connections SET expires=0")
    xero.fetch("workspace")
    assert xero.store.get("workspace",True)["tokens"]["refresh_token"]=="ROTATED_SECRET"
    assert calls[0][0]=="POST" and calls[0][2]["data"]["grant_type"]=="refresh_token"
    xero.store.disconnect("workspace")
    with pytest.raises(IntegrationError):
        xero.fetch("workspace")
    assert xero.store.get("workspace")["tenant"]==TENANT
    with pytest.raises(ValueError):
        xero.store.save("workspace","44444444-4444-4444-8444-444444444444",tokens())


def test_pagination_failure_and_repeated_page_never_return_partial_data(connector):
    xero,_,replies=connector
    replies["Invoices"]["Invoices"]=[{**I,"InvoiceID":f"33333333-3333-4333-8333-{n:012d}"} for n in range(100)]
    with pytest.raises(IntegrationError,match="unstable_accounting_pages"):
        xero.fetch("workspace")


def test_sync_is_atomic_idempotent_and_preserves_local_work(records_factory):
    records=records_factory()
    out=records.import_accounting(TENANT,[row()],{CONTACT:C},100,500,True)
    rid=out["changed"][0]
    records.add_note_raw(rid,"2026-09-05","reviewer","Keep this decision")
    assert records.import_accounting(TENANT,[row()],{CONTACT:C},101,500)["changed"]==[]
    assert len(records.invoice(rid)["notes"])==1
    before=records.sync_status()
    with pytest.raises(ValueError,match="source_revision_conflict"):
        records.import_accounting(TENANT,[row(AmountDue=5)],{CONTACT:C},102,500)
    assert records.sync_status()==before and records.invoice(rid)["amount"]==75.25
    other = "44444444-4444-4444-8444-444444444444"
    contact = {**C, "ContactID": other}
    transferred = normalize({**I, "Contact": {"ContactID": other}, "UpdatedDateUTC": "2026-09-06T09:00:00Z"}, contact)
    snapshot = records.source(rid)
    with pytest.raises(ValueError, match="source_customer_conflict"):
        records.import_accounting(TENANT, [transferred], {CONTACT:C, other:contact}, 102, 500)
    assert records.sync_status() == before and records.source(rid) == snapshot
    paid=row(Status="PAID",AmountDue=0,UpdatedDateUTC="2026-09-06T10:00:00Z")
    records.import_accounting(TENANT,[paid],{CONTACT:C},103,500)
    assert records.invoice(rid)["status"]=="paid" and records.invoice(rid)["amount"]==0
    assert len(records.invoice(rid)["notes"])==1 and records.ids()==[rid]


def test_contacts_change_without_invoice_update_and_same_names_do_not_mix(records_factory):
    records=records_factory()
    other="44444444-4444-4444-8444-444444444444"
    other_contact={**C,"ContactID":other}
    other_invoice={**I,"InvoiceID":"55555555-5555-4555-8555-555555555555","Contact":{"ContactID":other}}
    rows=[row(),normalize(other_invoice,other_contact)]
    records.import_accounting(TENANT,rows,{CONTACT:C,other:other_contact},100,500,True)
    rid,next_id=records.ids()
    records.add_note_raw(next_id,"2026-09-05","reviewer","OTHER_CUSTOMER_SECRET")
    assert records.customer_context(rid)==[]
    old=records.source(rid)["source_revision"]
    records.import_accounting(TENANT,[],{CONTACT:{**C,"EmailAddress":"new@example.test"},other:other_contact},101,500)
    assert records.source(rid)["source_revision"]!=old
    assert records.invoice(rid)["contact"]=="new@example.test"


def test_source_change_blocks_policy_execution_before_any_claim(store_factory):
    store=store_factory()
    records=Records()
    records.import_accounting(TENANT,[row()],{CONTACT:C},100,500,True)
    rid=records.ids()[0]
    config=PolicyConfig.load(Path(__file__).resolve().parents[1]/"adapters/invoices-es/permissions.toml")
    policy=PolicyService(config,store,record_reader=records.invoice)
    human=Principal("reviewer",HUMAN)
    p=policy.propose(Principal("assistant",AGENT),"update_status",rid,{"status":"reminded"})
    assert p.record_version==records.source(rid)["source_revision"]
    policy.decide(p.id,True,human)
    records.import_accounting(TENANT,[row(AmountDue=50,UpdatedDateUTC="2026-09-06T10:00:00Z")],{CONTACT:C},101,500)
    result=policy.execute(p.id,make_executor(records),human)
    assert result.status=="approved" and records.invoice(rid)["status"]=="open"
    assert not any(r["kind"]=="EXECUTION_ATTEMPTED" for r in store.audit_rows())
    assert store.audit_verify() and store.audit_anomalies()==[]


def test_http_sync_failure_retains_cursor_and_stale_draft_is_refused(client,connector,monkeypatch):
    xero,_,replies=connector
    sid,h=session(client)
    xero.store.save(sid,"66666666-6666-4666-8666-666666666666",tokens())
    monkeypatch.setattr(apimod,"xero",xero)
    assert client.post(f"/sessions/{sid}/accounting/sync",headers=h).status_code==200
    rid=apimod.state_of(sid).records.ids()[0]
    a=assist(client,sid,h,rid)
    assert a.status_code==200,a.text
    held=next(p for p in a.json()["proposals"] if p["status"]=="held")
    replies["Invoices"]["Invoices"]=[{**I,"AmountDue":50,"UpdatedDateUTC":"2026-09-06T10:00:00Z"}]
    assert client.post(f"/sessions/{sid}/accounting/sync",headers=h).status_code==200
    response=client.post(f"/sessions/{sid}/proposals/{held['id']}/decide",headers=h,json={"approve":True})
    assert response.status_code==409 and "changed" in response.text
    before=apimod.state_of(sid).records.sync_status()
    replies["Invoices"]["Invoices"]=[{**I,"AmountDue":"NaN"}]
    assert client.post(f"/sessions/{sid}/accounting/sync",headers=h).status_code==409
    assert apimod.state_of(sid).records.sync_status()==before
    assert assist(client,sid,h,rid).status_code==409


def test_oauth_connection_checks_exact_tenant_and_scope(connector):
    xero,_,_=connector
    attempt={"workspace":"new","tenant":"77777777-7777-4777-8777-777777777777","verifier":"pkce"}
    def wrong_tenant(method,url,**kwargs):
        return [{"tenantId":TENANT,"tenantType":"ORGANISATION"}] if url.endswith("connections") else tokens()
    xero.transport=wrong_tenant
    with pytest.raises(IntegrationError,match="wrong_accounting_tenant"):
        xero.connect(attempt,"code")
    assert xero.store.get("new") is None
    with pytest.raises(IntegrationError,match="unexpected_accounting_scopes"):
        xero.store.save("new",attempt["tenant"],{**tokens(),"scope":tokens()["scope"]+" accounting.invoices"})
    assert xero.store.get("new") is None


def test_full_reconciliation_detects_missing_rows_and_does_not_advance(records_factory):
    records=records_factory();records.import_accounting(TENANT,[row()],{CONTACT:C},100,500,True)
    before=records.sync_status()
    with pytest.raises(ValueError,match="source_invoice_missing"):
        records.import_accounting(TENANT,[],{CONTACT:C},101,500,True)
    assert records.sync_status()==before and len(records.ids())==1


def test_live_payment_recheck_refuses_approval_without_a_scheduled_sync(client,connector,monkeypatch):
    xero,_,replies=connector;sid,h=session(client)
    xero.store.save(sid,"88888888-8888-4888-8888-888888888888",tokens());monkeypatch.setattr(apimod,"xero",xero)
    assert client.post(f"/sessions/{sid}/accounting/sync",headers=h).status_code==200
    rid=apimod.state_of(sid).records.ids()[0]
    proposal=next(p for p in assist(client,sid,h,rid).json()["proposals"] if p["status"]=="held")
    cursor=apimod.state_of(sid).records.sync_status()["cursor"]
    replies["Invoices"]["Invoices"]=[{**I,"Status":"PAID","AmountDue":0,"UpdatedDateUTC":"2026-09-06T10:00:00Z"}]
    result=client.post(f"/sessions/{sid}/proposals/{proposal['id']}/decide",headers=h,json={"approve":True})
    assert result.status_code==409 and "settled" in result.text
    assert apimod.state_of(sid).records.sync_status()["cursor"]==cursor
    assert apimod.state_of(sid).policy.store.get_proposal(proposal["id"]).status=="held"


def test_enterprise_assist_requires_an_accounting_source(client,monkeypatch):
    from dataclasses import replace
    sid,h=session(client);upload(client,sid,h)
    monkeypatch.setattr(apimod,'settings',replace(apimod.settings,identity_required=True))
    response=client.post(f'/sessions/{sid}/assist/F-2026-031',headers=h)
    assert response.status_code==409 and 'sync accounting' in response.text
    assert apimod.state_of(sid).policy.store.list_proposals()==[]


def test_delete_removes_saved_connector_even_when_provider_is_disabled(client,monkeypatch):
    sid,h=session(client);upload(client,sid,h)
    store=CredentialStore(apimod.sessions.db,Fernet.generate_key().decode())
    store.save(sid,TENANT,tokens())
    apimod.sessions.identity.begin('browser',{'kind':'xero','workspace':sid,'verifier':'ephemeral-secret'})
    monkeypatch.setattr(apimod,'xero',None)
    response=client.delete(f'/sessions/{sid}',headers={**h,'X-Confirm-Delete':sid})
    assert response.status_code==200,response.text
    assert store.get(sid) is None
    assert all(sid not in row[0] for row in apimod.sessions._exec('SELECT payload FROM oauth_attempts'))

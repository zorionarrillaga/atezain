"""Read-only Xero OAuth and bounded invoice/contact retrieval.

Accounting balances are source snapshots. This connector exposes no accounting write verb.
"""
import base64
from dataclasses import dataclass
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode
from uuid import UUID

from cryptography.fernet import Fernet

from integrations.http import IntegrationError, https_url, request_json

SCOPES = "offline_access accounting.invoices.read accounting.contacts.read"
AUTHORIZE = "https://login.xero.com/identity/connect/authorize"
TOKEN = "https://identity.xero.com/connect/token"
API = "https://api.xero.com/api.xro/2.0/"
CONNECTIONS = "https://api.xero.com/connections"


def uuid(value):
    return str(UUID(value))


def money(value):
    if isinstance(value, bool):
        raise ValueError("invalid source money")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= Decimal("999999999999.99") or number != number.quantize(Decimal(".01")):
            raise ValueError("invalid source money")
    except InvalidOperation as exc:
        raise ValueError("invalid source money") from exc
    return format(number, ".2f")


def source_time(value):
    if not isinstance(value, str):
        raise ValueError("source timestamp missing")
    match = re.fullmatch(r"/Date\((-?[0-9]+)(?:[+-][0-9]{4})?\)/", value)
    if match:
        return dt.datetime.fromtimestamp(int(match[1])/1000, dt.timezone.utc)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)


def date(value):
    return source_time(value).date().isoformat()


def bounded_text(value, limit, optional=False):
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("invalid source text")
    return value


def normalize(invoice, contact):
    if invoice.get("Type") != "ACCREC":
        return None
    status = invoice.get("Status")
    if status in {"DRAFT", "SUBMITTED"}:
        return None
    if status not in {"AUTHORISED", "PAID", "VOIDED", "DELETED"}:
        raise ValueError("unsupported source invoice status")
    customer_id = uuid(invoice["Contact"]["ContactID"])
    if customer_id != uuid(contact["ContactID"]):
        raise ValueError("source contact mismatch")
    currency = invoice.get("CurrencyCode", "")
    if not re.fullmatch("[A-Z]{3}", currency):
        raise ValueError("invalid source currency")
    amount = money(invoice["AmountDue"])
    total = money(invoice["Total"])
    if Decimal(amount) > Decimal(total):
        raise ValueError("source balance exceeds total")
    if status == "PAID" and Decimal(amount) != 0:
        raise ValueError("paid source invoice carries balance")
    return {"source_id": uuid(invoice["InvoiceID"]), "customer_key": customer_id,
            "source_number": bounded_text(invoice.get("InvoiceNumber", invoice["InvoiceID"]), 255),
            "customer": bounded_text(contact["Name"], 255), "contact": bounded_text(contact.get("EmailAddress"), 254, True),
            "amount": amount, "original_amount": total, "currency": currency,
            "issued": date(invoice.get("DateString") or invoice.get("Date")),
            "due": date(invoice.get("DueDateString") or invoice.get("DueDate")),
            "status": "paid" if status == "PAID" else "cancelled" if status in {"VOIDED", "DELETED"} else "open",
            "source_status": status, "updated": source_time(invoice["UpdatedDateUTC"]).isoformat()}


@dataclass(frozen=True)
class XeroConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    credential_key: str

    @classmethod
    def from_env(cls):
        if not os.getenv("ATEZAIN_XERO_CLIENT_ID"):
            return None
        value = cls(os.environ["ATEZAIN_XERO_CLIENT_ID"], os.getenv("ATEZAIN_XERO_CLIENT_SECRET", ""),
                    os.getenv("ATEZAIN_XERO_REDIRECT_URI", ""), os.getenv("ATEZAIN_CREDENTIAL_KEY", ""))
        https_url(value.redirect_uri)
        if not value.client_secret:
            raise ValueError("Xero client secret required")
        Fernet(value.credential_key.encode())
        return value


class CredentialStore:
    def __init__(self, db, key):
        self.db, self.cipher = db, Fernet(key.encode())
        with db.transaction("connector_schema") as execute:
            execute("CREATE TABLE IF NOT EXISTS accounting_connections (workspace TEXT PRIMARY KEY, tenant TEXT NOT NULL UNIQUE, "
                    "credential TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL, "
                    "retry_at DOUBLE PRECISION NOT NULL)")

    def save(self, workspace, tenant, tokens):
        if not all(isinstance(tokens.get(k), str) and tokens[k] for k in ("access_token", "refresh_token")):
            raise IntegrationError("invalid_credentials")
        expires = tokens.get("expires_in")
        if not isinstance(expires, (int,float)) or not 0 < expires <= 86400:
            raise IntegrationError("invalid_credentials")
        granted = set(tokens.get("scope", "").split())
        if not set(SCOPES.split()) <= granted or granted - set(SCOPES.split()) - {"openid", "profile", "email"}:
            raise IntegrationError("unexpected_accounting_scopes")
        sealed = self.cipher.encrypt(json.dumps({"workspace":workspace,"tenant":tenant,"tokens":tokens}).encode()).decode()
        with self.db.transaction("connector") as execute:
            old = execute("SELECT tenant FROM accounting_connections WHERE workspace=?", (workspace,)).fetchone()
            if old and old[0] != tenant:
                raise ValueError("changing accounting tenant requires a separate workspace")
            execute("INSERT INTO accounting_connections VALUES (?,?,?,?,?,?,?) ON CONFLICT (workspace) DO UPDATE SET "
                    "credential=excluded.credential,expires=excluded.expires,state=excluded.state,error='',retry_at=0",
                    (workspace, tenant, sealed, time.time()+expires, "connected", "", 0))

    def get(self, workspace, secrets_required=False):
        with self.db.transaction("connector") as execute:
            row = execute("SELECT tenant,credential,expires,state,error,retry_at FROM accounting_connections WHERE workspace=?", (workspace,)).fetchone()
        if not row:
            return None
        result = dict(zip(("tenant", "credential", "expires", "state", "error", "retry_at"), row))
        sealed = result.pop("credential")
        if secrets_required:
            payload = json.loads(self.cipher.decrypt(sealed.encode()))
            if payload["workspace"] != workspace or payload["tenant"] != result["tenant"]:
                raise ValueError("credential context mismatch")
            result["tokens"] = payload["tokens"]
        return result

    def failure(self, workspace, error):
        state = "reconnect_required" if error.code == "provider_access_refused" else "retry_required"
        with self.db.transaction("connector") as execute:
            execute("UPDATE accounting_connections SET state=?,error=?,retry_at=? WHERE workspace=?",
                    (state,error.code,time.time()+error.retry_after,workspace))

    def disconnect(self, workspace):
        # Keep the immutable tenant binding; discard tokens immediately. Reconnect cannot switch organisations.
        with self.db.transaction("connector") as execute:
            execute("UPDATE accounting_connections SET credential='',state='disconnected',error='',retry_at=0 WHERE workspace=?", (workspace,))

    def success(self, workspace):
        with self.db.transaction("connector") as execute:
            execute("UPDATE accounting_connections SET state='connected',error='',retry_at=0 WHERE workspace=?", (workspace,))

    def forget(self, workspace):
        with self.db.transaction("connector") as execute:
            execute("DELETE FROM accounting_connections WHERE workspace=?", (workspace,))


class Xero:
    def __init__(self, config, store, transport=request_json):
        self.config, self.store, self.transport = config, store, transport

    def start(self, attempts, workspace, actor, binding, tenant):
        tenant = uuid(tenant)
        old = self.store.get(workspace)
        if old and old["tenant"] != tenant:
            raise ValueError("changing accounting tenant requires a separate workspace")
        verifier = secrets.token_urlsafe(48)
        state = attempts.begin(binding, {"kind":"xero","workspace":workspace,"actor":actor,"tenant":tenant,"verifier":verifier})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return AUTHORIZE + "?" + urlencode(dict(response_type="code",client_id=self.config.client_id,
            redirect_uri=self.config.redirect_uri,scope=SCOPES,state=state,code_challenge=challenge,code_challenge_method="S256"))

    def exchange(self, data):
        credential = base64.b64encode((self.config.client_id + ":" + self.config.client_secret).encode()).decode()
        return self.transport("POST", TOKEN, headers={"Authorization":"Basic " + credential}, data=data)

    def connect(self, attempt, code):
        tokens = self.exchange(dict(grant_type="authorization_code",code=code,redirect_uri=self.config.redirect_uri,code_verifier=attempt["verifier"]))
        connections = self.transport("GET", CONNECTIONS, headers={"Authorization":"Bearer " + tokens["access_token"]})
        if not isinstance(connections,list) or not any(c.get("tenantId") == attempt["tenant"] and c.get("tenantType") == "ORGANISATION" for c in connections):
            raise IntegrationError("wrong_accounting_tenant")
        self.store.save(attempt["workspace"], attempt["tenant"], tokens)

    def credentials(self, workspace):
        status = self.store.get(workspace)
        if not status or status["state"] in {"disconnected", "reconnect_required"}:
            raise IntegrationError("accounting_connection_required")
        if status["retry_at"] > time.time():
            raise IntegrationError("provider_retry_later", int(status["retry_at"]-time.time())+1)
        connection = self.store.get(workspace, True)
        if connection["expires"] <= time.time()+60:
            tokens = self.exchange(dict(grant_type="refresh_token", refresh_token=connection["tokens"]["refresh_token"]))
            # Some token responses omit unchanged scope. Bind that to the previously verified grant.
            tokens.setdefault("scope", connection["tokens"]["scope"])
            self.store.save(workspace, connection["tenant"], tokens)
            connection = self.store.get(workspace, True)
        return connection

    def pages(self, endpoint, headers, params, max_items):
        result, seen = [], set()
        for page in range(1, 102):
            body = self.transport("GET", API+endpoint, headers=headers, params={**params,"page":page,"pageSize":100})
            rows = body.get(endpoint)
            if not isinstance(rows,list) or len(rows)>100:
                raise IntegrationError("invalid_accounting_page")
            if not rows:
                return result
            for row in rows:
                key = row.get("InvoiceID" if endpoint=="Invoices" else "ContactID")
                if key in seen:
                    raise IntegrationError("unstable_accounting_pages")
                seen.add(key)
            result.extend(rows)
            if len(result)>max_items:
                raise IntegrationError("accounting_capacity_exceeded")
            if len(rows)<100:
                return result
        raise IntegrationError("accounting_page_limit")

    def fetch(self, workspace, since=None, max_items=500):
        connection = self.credentials(workspace)
        headers = {"Authorization":"Bearer "+connection["tokens"]["access_token"],"Xero-tenant-id":connection["tenant"],"Accept":"application/json"}
        contacts = self.pages("Contacts", headers, {"includeArchived":"true"}, max_items*2)
        contact_map = {uuid(c["ContactID"]): c for c in contacts}
        invoice_headers = dict(headers)
        if since:
            invoice_headers["If-Modified-Since"] = (dt.datetime.fromtimestamp(since,dt.timezone.utc)-dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
        invoices = self.pages("Invoices", invoice_headers, {"where":'Type=="ACCREC"',"order":"UpdatedDateUTC ASC"}, max_items)
        normalized = []
        for invoice in invoices:
            key = uuid(invoice["Contact"]["ContactID"])
            if key not in contact_map:
                raise IntegrationError("source_contact_missing")
            value = normalize(invoice, contact_map[key])
            if value:
                normalized.append(value)
        # Contacts are fetched fully on every run so address changes don't depend on invoice timestamps.
        return connection["tenant"], normalized, contact_map

    def fetch_one(self, workspace, source_id):
        connection = self.credentials(workspace)
        headers = {"Authorization":"Bearer "+connection["tokens"]["access_token"],"Xero-tenant-id":connection["tenant"],"Accept":"application/json"}
        invoices = self.transport("GET", API+"Invoices/"+uuid(source_id), headers=headers).get("Invoices")
        if not isinstance(invoices,list) or len(invoices)!=1 or uuid(invoices[0]["InvoiceID"])!=source_id:
            raise IntegrationError("source_invoice_missing")
        contact_id = uuid(invoices[0]["Contact"]["ContactID"])
        contacts = self.transport("GET", API+"Contacts/"+contact_id, headers=headers).get("Contacts")
        if not isinstance(contacts,list) or len(contacts)!=1:
            raise IntegrationError("source_contact_missing")
        value = normalize(invoices[0],contacts[0])
        if value is None:
            raise IntegrationError("source_invoice_not_collectable")
        return value,contacts[0]

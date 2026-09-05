"""Authoritative source snapshots, distinct from approved local collection writes."""
import hashlib
import json
import time
import datetime
from decimal import Decimal, InvalidOperation


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def revision(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_case(value):
    """Validate the exact approved document again at the records boundary."""
    fields = {"version", "assignee", "next_action", "promise_date", "promise_amount", "state", "note"}
    if not isinstance(value, dict) or set(value) != fields or type(value["version"]) is not int or value["version"] < 1:
        raise ValueError("invalid case document")
    for field in fields - {"version"}:
        if not isinstance(value[field], str):
            raise ValueError("invalid case field")
    if len(value["assignee"]) > 255 or len(value["note"]) > 2000 or value["state"] not in {"open", "disputed", "snoozed", "closed"}:
        raise ValueError("invalid case field")
    for field in ("next_action", "promise_date"):
        if value[field] and datetime.date.fromisoformat(value[field]).isoformat() != value[field]:
            raise ValueError("invalid case date")
    if value["state"] == "snoozed" and not value["next_action"]:
        raise ValueError("deferred case needs a date")
    if bool(value["promise_date"]) != bool(value["promise_amount"]):
        raise ValueError("incomplete payment promise")
    if value["promise_amount"]:
        try:
            amount = Decimal(value["promise_amount"])
            if not amount.is_finite() or not 0 <= amount <= Decimal("999999999999.99") or amount != amount.quantize(Decimal(".01")):
                raise ValueError("invalid promise amount")
        except InvalidOperation as exc:
            raise ValueError("invalid promise amount") from exc


def record_version(record):
    source = record.get("source_revision", "")
    version = record.get("collection_case", {}).get("version", 0)
    return source + (f":case:{version}" if version else "")


class AccountingRecords:
    def init_accounting(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS source_snapshots (invoice_id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, "
                          "customer_key TEXT NOT NULL, payload TEXT NOT NULL, revision TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS source_sync (id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, cursor DOUBLE PRECISION NOT NULL, "
                          "full_at DOUBLE PRECISION NOT NULL, manifest TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS collection_cases (invoice_id TEXT PRIMARY KEY, case_json TEXT NOT NULL)")

    def source(self, invoice_id):
        row = self.conn.execute("SELECT payload,revision FROM source_snapshots WHERE invoice_id=?", (invoice_id,)).fetchone()
        return {**json.loads(row[0]), "source_revision":row[1]} if row else None

    def source_rows(self):
        return {r[0]: {**json.loads(r[1]), "source_revision":r[2]} for r in self.conn.execute("SELECT invoice_id,payload,revision FROM source_snapshots")}

    def sync_status(self):
        row = self.conn.execute("SELECT tenant,cursor,full_at,manifest FROM source_sync WHERE id=1").fetchone()
        return dict(zip(("tenant","cursor","full_at","manifest"),row)) if row else None

    def import_accounting(self, tenant, invoices, contacts, started, max_records, full=False):
        """Commit every validated source row and the cursor together, or neither.

        This is source ingestion like CSV loading, unavailable to model proposals. Financial
        authority stays in its own mirror; local notes, reminders and case history are preserved.
        The caller holds the same cross-process workspace lock as approval and execution.
        """
        with self.transaction():
            state = self.sync_status()
            if state and state["tenant"] != tenant:
                raise ValueError("source_tenant_conflict")
            old = self.source_rows()
            by_source = {value["source_id"]: key for key,value in old.items()}
            proposed = {value["source_id"]: dict(value) for value in invoices}
            if len(proposed) != len(invoices):
                raise ValueError("duplicate_source_invoice")
            if full and set(by_source) - set(proposed):
                raise ValueError("source_invoice_missing; reconcile with the accounting operator")
            # Changed contacts must invalidate drafts even if the invoice has not been modified.
            for source_id, key in by_source.items():
                previous = {k:v for k,v in old[key].items() if k!="source_revision"}
                incoming = proposed.get(source_id)
                if incoming:
                    if incoming["customer_key"] != previous["customer_key"]:
                        raise ValueError("source_customer_conflict")
                    if incoming["updated"] < previous["updated"]:
                        raise ValueError("source_revision_regressed")
                    fields = ("amount","original_amount","currency","issued","due","status","source_status")
                    if incoming["updated"] == previous["updated"] and any(incoming[f]!=previous[f] for f in fields):
                        raise ValueError("source_revision_conflict")
                else:
                    proposed[source_id] = previous
            ids = set(self.ids())
            if len(ids) + len(set(proposed)-set(by_source)) > max_records:
                raise ValueError("accounting_capacity_exceeded")
            changed = []
            revisions = {rid:value["source_revision"] for rid,value in old.items()}
            for source_id, value in proposed.items():
                contact = contacts.get(value["customer_key"])
                if not contact:
                    raise ValueError("source_contact_missing")
                from integrations.xero import bounded_text
                value["customer"] = bounded_text(contact["Name"],255)
                value["contact"] = bounded_text(contact.get("EmailAddress"),254,True)
                rid = by_source.get(source_id)
                if rid is None:
                    year = value["issued"][:4]
                    rid = next((f"F-{year}-{n:03d}" for n in range(1000) if f"F-{year}-{n:03d}" not in ids),None)
                    if rid is None:
                        raise ValueError("invoice_id_capacity_exceeded")
                    self.load_seed_row({**value,"id":rid,"amount":float(value["amount"])})
                    ids.add(rid)
                version = revision(value)
                if rid not in old or old[rid]["source_revision"] != version:
                    changed.append(rid)
                    self.conn.execute("INSERT INTO source_snapshots VALUES (?,?,?,?,?) ON CONFLICT (invoice_id) DO UPDATE SET "
                        "payload=excluded.payload,revision=excluded.revision", (rid,source_id,value["customer_key"],canonical(value),version))
                revisions[rid] = version
            manifest = revision(revisions)
            self.conn.execute("INSERT INTO source_sync VALUES (1,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
                              "cursor=excluded.cursor,full_at=excluded.full_at,manifest=excluded.manifest",
                              (tenant,started,started if full else state["full_at"] if state else 0,manifest))
        return {"changed":changed,"source_records":len(proposed),"cursor":started,"manifest":manifest,"full":full}

    @staticmethod
    def overlay_source(inv, source):
        if source:
            for key in ("customer","contact","currency","issued","due","source_id","source_number","customer_key","source_revision","original_amount","source_status"):
                inv[key] = source[key]
            inv["amount"] = float(source["amount"])
            inv["outstanding"] = source["amount"]
            if source["status"] in {"paid","cancelled"}:
                inv["status"] = source["status"]
        return inv

    @staticmethod
    def empty_case(status="open"):
        return {"version":0,"assignee":"","next_action":"","promise_date":"","promise_amount":"","state":"disputed" if status=="disputed" else "open","note":""}

    def case(self, invoice_id):
        row = self.conn.execute("SELECT case_json FROM collection_cases WHERE invoice_id=?", (invoice_id,)).fetchone()
        if row:
            return json.loads(row[0])
        invoice = self.conn.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        return self.empty_case(invoice[0] if invoice else "open")

    def cases(self):
        return {r[0]:json.loads(r[1]) for r in self.conn.execute("SELECT invoice_id,case_json FROM collection_cases")}

    def _apply_manage_case(self, invoice_id, case_json):
        value = json.loads(case_json)
        validate_case(value)
        if value["version"] != self.case(invoice_id)["version"]+1:
            raise ValueError("case changed since approval")
        self.conn.execute("INSERT INTO collection_cases VALUES (?,?) ON CONFLICT (invoice_id) DO UPDATE SET case_json=excluded.case_json", (invoice_id,case_json))

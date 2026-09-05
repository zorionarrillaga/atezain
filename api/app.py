"""The deployed face: a stranger's session, their own records, and the same boundary (PLAN.md §4.3).

Nothing here is a new way to write to records. A route can propose (through the graph), decide
(through `PolicyService.decide`, with a principal minted only by `api/auth.py`) and execute
(through `PolicyService.execute` with `agent/executor.py`) — the same three verbs the CLI has and
the tests have. There is no route that touches `records._apply_*`, and the grep test says so.

One session is one namespace: its own records database, its own policy store with its own audit
chain and its own fuse, its own checkpointer. Sessions cannot see each other, and a fuse tripped in
one binds only that one.
"""
from __future__ import annotations

import csv
import datetime
import io
import os
import re
import time
import json
import secrets
import hashlib
import base64
import inspect
import logging
import shutil
import threading
from collections import OrderedDict
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request, UploadFile, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from agent import build_graph
from agent.checkpoints import make_checkpointer
from agent.executor import make_executor
from agent.llm import GroqLLM, StubLLM, ModelFailure
from api.auth import AGENT_PRINCIPAL, Sessions
from api.limits import PersistentLimits
from api.settings import Settings
from api.oidc import OIDC, OIDCConfig
from integrations.http import IntegrationError
from integrations.xero import Xero, XeroConfig, CredentialStore
from api.imports import (parse_rows, load_rows as import_rows, MAX_UPLOAD_BYTES, MAX_ROWS, COLUMNS, EXTRAS)
from agent.tracing import Tracer
from api.http import BodyLimit
from api.schemas import (ActionOut, AdapterOut, AssistOut, AuditOut, DecideIn, DecideOut, EmailOut,
                         FuseOut, NoteOut, ProposalOut, RecordDetailOut, RecordOut, RejectedRow,
                         SessionOut, UploadOut, GrantIn)
from policy import APPROVED, EXECUTED, HELD, PolicyConfig, PolicyService, Store
from policy.model import TZ
from records import Records


def today() -> str:
    """The day this service reckons with, wherever it reckons with one: the policy's own zone
    (`policy.model.TZ`, Europe/Madrid), where the adapter's day boundary and its budget already
    live. Until 2026-09-05 the summary counted overdue by that zone, the work list by UTC and the
    source check by the container's clock — three clocks for one word — so for two hours a night an
    invoice was overdue on one panel and not yet due on the other. Tests replace this function."""
    return datetime.datetime.now(TZ).date().isoformat()

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = os.environ.get("ATEZAIN_ADAPTER", "invoices-es")
STATE = Path(os.environ.get("ATEZAIN_STATE_DIR", ROOT / "var"))
# Render names the database URL DATABASE_URL; either works, and neither is ever written down here
DSN = os.environ.get("ATEZAIN_DSN") or os.environ.get("DATABASE_URL", "")
MODEL = os.environ.get("ATEZAIN_MODEL", "stub")
MODEL_ID = os.environ.get("ATEZAIN_MODEL_ID", "openai/gpt-oss-120b")
OWNER_TOKEN = os.environ.get("ATEZAIN_OWNER_TOKEN", "")
settings = Settings.from_env()

MAX_UPLOAD_BYTES = 1024 * 1024
MAX_ROWS = 500
COLUMNS = ("id", "customer", "amount", "currency", "issued", "due", "status")
EXTRAS = ("contact", "note", "email_subject", "email_body")

app = FastAPI(title="Atezain", version="0.2.0", description=__doc__.split("\n\n")[0],
              docs_url="/docs" if settings.mode == "demo" else None,
              redoc_url=None, openapi_url="/openapi.json" if settings.mode == "demo" else None)
STATE.mkdir(parents=True, exist_ok=True)
from ops.lease import lease
_service_lease = lease(STATE) if not DSN else None
sessions = Sessions(STATE / "sessions.db", dsn=DSN or None)   # the token table outlives a spin-down too
sessions.ttl = settings.session_days * 86400
sessions.oidc_only = settings.identity_required
oidc_config = OIDCConfig.from_env()
oidc = OIDC(oidc_config) if oidc_config else None
xero_config = XeroConfig.from_env()
xero = Xero(xero_config, CredentialStore(sessions.db, xero_config.credential_key)) if xero_config else None
limits = PersistentLimits(STATE / "limits.db", dsn=DSN or None)
from api.configuration import served_policy, served_llm
config = served_policy(settings.mode, ADAPTER)
_live: dict[str, "SessionState"] = OrderedDict()
_pool_lock = threading.Lock()
_busy: set[str] = set()
_health: dict[str, Any] = {"at": 0.0, "body": None}
app.add_middleware(BodyLimit)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default response includes the rejected input, which may contain secrets.
    return JSONResponse({"detail": "invalid request", "errors": [
        {"field": list(e["loc"]), "type": e["type"]} for e in exc.errors()]}, status_code=422)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    request_id, start = secrets.token_hex(16), time.monotonic()
    try:
        cookie = request.cookies.get("__Host-atezain")
        csrf_ok = True
        if cookie and "authorization" not in request.headers:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                login = sessions.identity.login(cookie)
                csrf_ok = (not login and request.url.path == "/auth/logout") or bool(login and secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), login["csrf"]))
            request.scope["headers"] = list(request.scope["headers"]) + [(b"authorization", ("Bearer " + cookie).encode())]
        response = (await call_next(request) if csrf_ok else JSONResponse({"detail": "sign in again before changing this workspace"}, 403))
    except Exception as exc:
        logging.getLogger("atezain.http").error(json.dumps({"request_id": request_id, "error": type(exc).__name__}))
        response = JSONResponse({"detail": "service unavailable; contact the operator with the request ID"}, 503)
    response.headers.update({"X-Request-ID": request_id, "Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                             "X-Frame-Options": "DENY", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
    if request.url.path == "/demo":
        source = (Path(__file__).parent / "demo.html").read_text()
        script = re.search(r"<script>(.*?)</script>", source, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        response.headers["Content-Security-Policy"] = (f"default-src 'none'; script-src 'sha256-{digest}'; "
            "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
    if settings.mode in {"pilot", "enterprise"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    logging.getLogger("atezain.http").info(json.dumps({"request_id": request_id, "method": request.method,
        "route": getattr(request.scope.get("route"), "path", "unmatched"), "status": response.status_code,
        "duration_ms": round((time.monotonic() - start) * 1000)}))
    return response


def schema_of(sid: str) -> str:
    """One session, one Postgres schema. The id is hex from `secrets.token_hex`, but this does not
    take that on trust: malformed identifiers are rejected before a schema name reaches SQL."""
    if re.fullmatch(r"[a-f0-9]{16}", sid) is None:
        raise ValueError("invalid session id")
    return "s_" + sid


class SessionState:
    """One visitor's namespace: records, policy store, graph.

    With a DSN, all three are in Postgres and the records and the audit chain share ONE schema per
    session — which is what makes the deployed thing real: a Render free instance has no persistent
    disk and spins down when idle, so a visitor who comes back to their own link finds their
    invoices, their queue and their chain still there. Without a DSN it is SQLite files under
    `ATEZAIN_STATE_DIR`, which is the local shape and forgets nothing only because nothing spins
    down."""

    def __init__(self, sid: str):
        self.sid = sid
        with ExitStack() as resources:
            if DSN:
                from policy.store_pg import PgStore
                from records.store_pg import PgRecords
                schema = schema_of(sid)
                self.records = PgRecords(DSN, schema=schema)
                resources.callback(self.records.close)
                store = PgStore(DSN, schema=schema)
                resources.callback(store.close)
                self.policy = PolicyService(config, store, record_reader=self.records.invoice)
                self.checkpointer = make_checkpointer(DSN, None)
            else:
                d = STATE / sid
                d.mkdir(parents=True, exist_ok=True)
                self.records = Records(str(d / "records.db"))
                resources.callback(self.records.close)
                store = Store(str(d / "policy.db"))
                resources.callback(store.close)
                self.policy = PolicyService(config, store, record_reader=self.records.invoice)
                self.checkpointer = make_checkpointer(None, d / "checkpoints.db")
            resources.callback(self.checkpointer.conn.close)
            self._resources = resources.pop_all()

    def graph(self, llm):
        return build_graph(self.records, self.policy, llm, AGENT_PRINCIPAL, ADAPTER, self.checkpointer,
                           tracer=Tracer(), retrieval="customer")

    def close(self):
        self._resources.close()


def state_of(sid: str) -> SessionState:
    with _pool_lock:
        if sid not in _live:
            while len(_live) >= settings.max_live_sessions:
                victim = next((key for key in _live if key not in _busy), None)
                if victim is None:
                    raise HTTPException(503, "service busy; retry shortly", headers={"Retry-After": "2"})
                _live.pop(victim).close()
            _live[sid] = SessionState(sid)
        value = _live.pop(sid)
        _live[sid] = value
        return value


def bearer(authorization):
    if not authorization:
        return None
    parts = authorization.split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


def guarded(permission="read"):
    """Authenticate before opening a namespace; serialise the complete operation across workers."""
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def call(*args, **kwargs):
            values = signature.bind(*args, **kwargs).arguments
            sid, authorization = values["sid"], values.get("authorization")
            who(sid, authorization)
            try:
                with sessions.lock(sid):
                    access = sessions.access(sid, bearer(authorization))
                    if access is None:
                        raise HTTPException(401, "session expired or access revoked")
                    permitted = {"owner": {"read", "review", "admin"}, "reviewer": {"read", "review"}, "viewer": {"read"}}
                    if permission not in permitted.get(access["role"], set()):
                        raise HTTPException(403, "this access token does not permit that action")
                    with _pool_lock:
                        _busy.add(sid)
                    try:
                        return fn(*args, **kwargs)
                    except Exception as exc:
                        if not isinstance(exc, HTTPException) or exc.status_code == 503:
                            with _pool_lock:
                                stale = _live.pop(sid, None)
                            if stale is not None:
                                stale.close()
                        raise
                    finally:
                        with _pool_lock:
                            _busy.discard(sid)
            except BlockingIOError as e:
                raise HTTPException(409, "workspace busy; retry shortly", headers={"Retry-After": "1"}) from e
        return call
    return decorate


def who(sid: str, authorization: str | None):
    """The session's human, or 401. The ONE mint lives in api/auth.py."""
    token = bearer(authorization)
    p = sessions.principal(sid, token)
    if p is None:
        raise HTTPException(status_code=401, detail="no such session, or the token does not open it",
                            headers={"WWW-Authenticate": "Bearer"})
    return p


def model_for(byok: str | None):
    """The visitor's own key if they brought one, else the server's, else the stub."""
    if byok:
        return served_llm(MODEL_ID, api_key=byok), f"{MODEL_ID} (your key)", True
    if MODEL == "groq":
        return served_llm(MODEL_ID), MODEL_ID, False
    return StubLLM(), "stub", False


def out(p) -> ProposalOut:
    return ProposalOut(id=p.id, action=p.action, record_id=p.record_id, params=p.params,
                       status=p.status, reason=p.reason, decided_by=p.decided_by, note=p.note,
                       evidence=p.evidence, created_at=p.created_at, record_version=p.record_version)


def proposals_of(st: SessionState) -> list:
    return st.policy.store.list_proposals()


def require_healthy_audit(st):
    if (not st.policy.store.audit_verify() or st.policy.store.audit_anomalies()
            or any(p.status in {"executed_unknown", "executed_mismatch"} for p in proposals_of(st))):
        raise HTTPException(409, "audit requires investigation; new work is paused. Export the audit for the operator")


def answered(snap) -> bool:
    """A checkpoint is not an answer. LangGraph writes one for the thread as soon as the run
    starts, so a run that died inside `think` — the model refusing the call — leaves a checkpoint
    holding the input and nothing else, and `created_at is not None` then reads as *the model
    already answered, ask nobody again* for the life of the session. Client simulation 3
    (STATUS.md S3-5) lost seven of a visitor's records to one minute of a free tier's rate limit
    that way: 200, `cached`, empty summary, empty draft, no proposals, and no route that could ever
    ask again. `raw_proposals` is what `think` returns and nothing else writes; its presence is the
    model having spoken, and an answer with no proposals in it is still an answer."""
    return snap.created_at is not None and "raw_proposals" in snap.values


def model_refused(e: Exception, byok: bool) -> HTTPException:
    """The model saying no is not this service failing, and a stranger has to be able to tell the
    two apart — with their own key the difference is whether THEY have something to fix. Simulation
    3 (S3-4): a rate-limited call and a mistyped `X-Groq-Key` were both a bare 500."""
    code = getattr(e, "code", None)
    whose = "the key you sent" if byok else "the server's key"
    return HTTPException(status_code=502, detail=(
        f"the model did not answer ({code if code is not None else type(e).__name__}) using {whose}. "
        f"Nothing was written, and this record can be asked again."))


# ── sessions ─────────────────────────────────────────────────────────────────────────────────
@app.post("/sessions", response_model=SessionOut)
def create_session(request: Request, x_owner_token: str | None = Header(default=None)) -> SessionOut:
    if settings.mode in {"pilot", "enterprise"} and (not x_owner_token or not secrets.compare_digest(x_owner_token, OWNER_TOKEN)):
        raise HTTPException(403, "workspace provisioning requires the operator's token")
    peer = request.client.host if request.client else "?"
    for key, windows in (("new:" + peer, ((60, 10), (86400, 60))), ("new:global", ((60, 30), (86400, 300)))):
        ok, why = limits.consume(key, windows)
        if not ok:
            raise HTTPException(429, why, headers={"Retry-After": "60"})
    try:
        sid, token = sessions.create(max_sessions=settings.max_sessions)
    except OverflowError as e:
        raise HTTPException(503, str(e)) from e
    return SessionOut(session=sid, token=token, adapter=ADAPTER,
                      note="the token is shown once; the server keeps only its sha256. "
                           "Send it as `Authorization: Bearer <token>`.")


@app.post("/sessions/{sid}/upload", response_model=UploadOut)
@guarded("admin")
def upload(sid: str, file: UploadFile, authorization: str | None = Header(default=None),
           amount_format: str = Form(default="auto"), date_format: str = Form(default="auto"),
           status_map: str = Form(default="{}")) -> UploadOut:
    who(sid, authorization)
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"more than {MAX_UPLOAD_BYTES} bytes")
    rows = parse_rows(raw, file.filename or "")
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=413, detail=f"more than {MAX_ROWS} rows")
    if amount_format not in {"auto", "point", "comma"} or date_format not in {"auto", "dmy", "mdy", "iso"}:
        raise HTTPException(422, "invalid amount or date format")
    try:
        mapping = json.loads(status_map)
    except ValueError as e:
        raise HTTPException(422, "status_map must be a JSON object") from e
    if not isinstance(mapping, dict) or len(mapping) > 30 or any(not isinstance(k, str) or not isinstance(v, str) or v not in {"open", "paid", "reminded", "promised", "disputed", "cancelled"} for k, v in mapping.items()):
        raise HTTPException(422, "status_map must map source status words to supported statuses")
    return load_rows(state_of(sid), rows, amount_format=amount_format, date_format=date_format, status_map=mapping)


def load_rows(st, rows, **formats):
    return import_rows(st, rows, config, settings.max_session_records, **formats)


@app.get("/adapter", response_model=AdapterOut)
def adapter() -> AdapterOut:
    """The permission table, rendered from the loaded config — not from a copy of it in the page.
    No session and no token: it is the same for everyone, and someone deciding whether to trust the
    layer should be able to read the rules before uploading anything to it."""
    return AdapterOut(
        adapter=config.adapter, version=config.version, fingerprint=config.fingerprint(),
        daily_writes=config.daily_writes, records=dict(config.records),
        actions=[ActionOut(action=name, record=s.record, writes=list(s.writes), approval=s.approval,
                           denied=s.deny, daily_max=s.daily_max,
                           values={k: list(v) for k, v in s.constraints.items()},
                           of_the_record=dict(s.record_constraints))
                 for name, s in sorted(config.actions.items())])


def record_out(inv: dict) -> RecordOut:
    return RecordOut(id=inv["id"], customer=inv["customer"], amount=inv["amount"], currency=inv["currency"],
                     issued=inv["issued"], due=inv["due"], status=inv["status"],
                     contact=inv.get("contact"), reminder_to=inv.get("reminder_to"),
                     reminder_channel=inv.get("reminder_channel"),
                     notes=len(inv["notes"]), emails=len(inv["emails"]),
                     assistant_notes=sum(1 for n in inv["notes"] if n["author"] == "assistant"))


def records_of(st: SessionState) -> list[RecordOut]:
    """One query for the whole list (`Records.summaries`), never a read per record."""
    return [RecordOut(**row) for row in st.records.summaries()]


@app.get("/sessions/{sid}/records", response_model=list[RecordOut])
@guarded()
def records(sid: str, authorization: str | None = Header(default=None)) -> list[RecordOut]:
    """The visitor's own records back, which until 2026-09-03 no route returned: an `add_note` is
    auto-approved by this adapter and executes without anyone deciding, so the one write that needs
    no human was the one write nobody could read (client simulation 1, STATUS.md). Reads only —
    there is no route here that writes."""
    who(sid, authorization)
    return records_of(state_of(sid))


def summary_of(st: SessionState) -> dict:
    from decimal import Decimal
    from policy.service import LIVE
    day = today()
    rows = st.records.summaries()
    outstanding = [r for r in rows if r["status"] not in {"paid", "cancelled"} and r["amount"] > 0]
    overdue = [r for r in outstanding if r["due"] and r["due"] < day]
    totals = {}
    for row in overdue:
        totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + Decimal(str(row["amount"]))
    case_states = st.records.cases()
    used = st.policy.store.count_proposals_since(st.policy._day_start(st.policy.clock()), LIVE)
    return {"as_of": day, "records": len(rows), "overdue": len(overdue),
            "disputed": sum(case_states.get(r["id"], {}).get("state", r["status"]) == "disputed" for r in outstanding),
            "undated": sum(not r["due"] for r in outstanding),
            "overdue_by_currency": {k: format(v, ".2f") for k, v in sorted(totals.items())},
            "pending": sum(p.status == HELD for p in proposals_of(st)),
            "budget_used": used, "budget_limit": config.daily_writes,
            "budget_remaining": max(config.daily_writes - used, 0) if config.daily_writes else None,
            "fuse": st.policy.store.fuse_get()}


@app.get("/sessions/{sid}/summary")
@guarded()
def summary(sid: str, authorization: str | None = Header(default=None)):
    return summary_of(state_of(sid))


@app.get("/sessions/{sid}/records/{invoice_id}", response_model=RecordDetailOut)
@guarded()
def record(sid: str, invoice_id: str, authorization: str | None = Header(default=None)) -> RecordDetailOut:
    """One record with its notes and emails. Every note carries WHO wrote it: `assistant` is this
    system's own voice, and a claim in one is the assistant's, not the customer's."""
    who(sid, authorization)
    inv = state_of(sid).records.invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail=f"no record {invoice_id} in this session")
    base = record_out(inv).model_dump()
    base.update({key: inv[key] for key in ("source_id", "source_number", "customer_key", "source_revision", "original_amount", "outstanding", "source_status") if key in inv})
    return RecordDetailOut(**base,
                           note_rows=[NoteOut(**n) for n in inv["notes"]],
                           email_rows=[EmailOut(**e) for e in inv["emails"]])


# ── assist ───────────────────────────────────────────────────────────────────────────────────
@app.post("/sessions/{sid}/assist/{invoice_id}", response_model=AssistOut)
@guarded("review")
def assist(sid: str, invoice_id: str, request: Request, authorization: str | None = Header(default=None),
           x_groq_key: str | None = Header(default=None)) -> AssistOut:
    principal = who(sid, authorization)
    rate_key = (sid + ":" + principal.id if settings.identity_required else request.client.host if request.client else "?")
    ok, why = limits.allow(rate_key)
    if not ok:
        # WHICH limit, and that their own key is not the answer to it. A visitor who brought one has
        # been told they are paying their own way, and is then stopped by a limit that is about this
        # instance and not about the model — `rate_limited_minute` alone said neither (client
        # simulation 3, STATUS.md S3-7)
        if settings.identity_required:
            raise HTTPException(429, f"{why}: this reviewer gets {limits.per_minute} assists per minute and {limits.per_day} per day in this workspace", headers={"Retry-After": "60"})
        raise HTTPException(status_code=429, detail=(
            f"{why}: one address gets {limits.per_minute} assists a minute and {limits.per_day} a day "
            f"on this instance. That limit is this instance's, not the model's — your own "
            f"`X-Groq-Key` is neither counted against the server's model budget nor able to lift it."),
            headers={"Retry-After": "60"})
    st = state_of(sid)
    if st.records.invoice(invoice_id) is None:
        raise HTTPException(status_code=404, detail=f"no record {invoice_id} in this session")
    require_healthy_audit(st)
    reconcile_accounting_invoice(st, invoice_id)
    require_current_source(st, invoice_id)

    try:
        llm, model_name, byok = model_for(x_groq_key)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(503, "the model is not configured; contact the operator") from e
    source = st.records.source(invoice_id)
    version = record_version(st.records.invoice(invoice_id))
    suffix = ":" + version if version else ""
    conf = {"configurable": {"thread_id": f"{sid}:customer-v1:{invoice_id}{suffix}"}}
    graph = st.graph(llm)
    snap = graph.get_state(conf)
    if answered(snap):
        s = snap.values
        if snap.next and snap.next != ("hold",):
            # Resume durable work after think. Stable proposal keys prevent node replay from
            # duplicating proposals if a worker died before saving its checkpoint.
            try:
                s = graph.invoke(None, config=conf)
            except Exception as e:
                raise HTTPException(503, "workflow recovery paused; inspect the queue and audit before retrying") from e
        return AssistOut(invoice_id=invoice_id, summary=s.get("summary", ""),
                         recommendation=s.get("recommendation", ""), draft=s.get("draft", ""),
                         proposals=[out(p) for p in proposals_of(st) if p.record_id == invoice_id],
                         cached=True, model=s.get("model_name", model_name))

    allowed, why = limits.allow_model_call(byok)
    if not allowed:
        raise HTTPException(status_code=503, detail=why)
    try:
        s = graph.invoke({"invoice_id": invoice_id, "task": "draft", "model_name": model_name}, config=conf)
    except HTTPException:
        raise
    except ModelFailure as e:
        raise model_refused(e, byok) from e
    except Exception as e:
        raise HTTPException(503, "workflow paused; some work may have been recorded. Inspect the queue and audit, then retry this record") from e
    return AssistOut(invoice_id=invoice_id, summary=s.get("summary", ""),
                     recommendation=s.get("recommendation", ""), draft=s.get("draft", ""),
                     proposals=[out(p) for p in proposals_of(st) if p.record_id == invoice_id],
                     cached=False, model=model_name)


# ── the queue, the decision, the chain ───────────────────────────────────────────────────────
@app.get("/sessions/{sid}/proposals", response_model=list[ProposalOut])
@guarded()
def queue(sid: str, authorization: str | None = Header(default=None)) -> list[ProposalOut]:
    who(sid, authorization)
    return queue_of(state_of(sid))


def queue_of(st: SessionState) -> list[ProposalOut]:
    return [out(p) for p in proposals_of(st)]


@app.post("/sessions/{sid}/proposals/{pid}/decide", response_model=DecideOut)
@guarded("review")
def decide(sid: str, pid: str, body: DecideIn = Body(...), authorization: str | None = Header(default=None)) -> DecideOut:
    human = who(sid, authorization)
    st = state_of(sid)
    require_healthy_audit(st)
    if st.policy.store.get_proposal(pid) is None:
        raise HTTPException(status_code=404, detail="no such proposal in this session")
    existing = st.policy.store.get_proposal(pid)
    if existing.status == EXECUTED and body.approve:
        return DecideOut(proposal=out(existing), executed=True, applied=existing.params)
    if existing.status not in {HELD, APPROVED}:
        return DecideOut(proposal=out(existing), executed=False)
    if body.approve:
        if existing.action != "manage_case":
            reconcile_accounting_invoice(st, existing.record_id)
        require_current_source(st, existing.record_id, existing)
    p = st.policy.decide(pid, body.approve, human, note=body.note) if existing.status == HELD else existing
    if not body.approve and p.status == APPROVED:
        raise HTTPException(409, "this proposal has already been approved; stop the workspace to prevent execution")
    if p.status != APPROVED:
        return DecideOut(proposal=out(p), executed=False)
    q = st.policy.execute(p.id, make_executor(st.records), human)
    effect = st.policy.store.get_proposal(q.id)
    return DecideOut(proposal=out(effect or q), executed=q.status == EXECUTED,
                     applied=q.params if q.status == EXECUTED else None)


@app.get("/sessions/{sid}/audit", response_model=AuditOut)
@guarded()
def audit(sid: str, authorization: str | None = Header(default=None)) -> AuditOut:
    who(sid, authorization)
    return audit_of(state_of(sid))


def audit_of(st: SessionState) -> AuditOut:
    seq, h = st.policy.store.audit_head()
    return AuditOut(rows=st.policy.store.audit_rows(), head_seq=seq, head_hash=h,
                    verifies=st.policy.store.audit_verify(),
                    anomalies=[list(a) for a in st.policy.store.audit_anomalies()],
                    fuse=st.policy.store.fuse_get())


@app.post("/sessions/{sid}/fuse/clear", response_model=FuseOut)
@guarded("admin")
def clear_fuse(sid: str, authorization: str | None = Header(default=None)) -> FuseOut:
    human = who(sid, authorization)
    st = state_of(sid)
    cleared = st.policy.fuse.clear(human)
    return FuseOut(cleared=cleared, fuse=st.policy.store.fuse_get(),
                   why="" if cleared else "a tripped fuse clears no earlier than the day after it tripped")


@app.post("/sessions/{sid}/fuse/stop")
@guarded("admin")
def stop_session(sid: str, authorization: str | None = Header(default=None)):
    st = state_of(sid)
    st.policy.fuse.trip("stopped_by_owner", who(sid, authorization))
    return {"fuse": st.policy.store.fuse_get()}


@app.get("/sessions/{sid}/access")
@guarded()
def access_info(sid: str, authorization: str | None = Header(default=None)):
    return access_of(sid, sessions.access(sid, bearer(authorization)))


def access_of(sid: str, access: dict) -> dict:
    return {**access, "session": sid, "adapter": ADAPTER,
            "tokens": sessions.grants(sid) if access["role"] == "owner" else []}


@app.post("/sessions/{sid}/access")
@guarded("admin")
def grant_access(sid: str, body: GrantIn, authorization: str | None = Header(default=None)):
    if settings.identity_required:
        raise HTTPException(409, "use verified subject memberships in enterprise mode")
    try:
        return sessions.grant(sid, body.role, body.label, body.days)
    except OverflowError as e:
        raise HTTPException(409, str(e)) from e


@app.delete("/sessions/{sid}/access/{key}")
@guarded("admin")
def revoke_access(sid: str, key: str, authorization: str | None = Header(default=None)):
    if not sessions.revoke(sid, key):
        raise HTTPException(404, "no such access token")
    return {"revoked": True}


@app.post("/sessions/{sid}/token/rotate")
@guarded("admin")
def rotate_token(sid: str, authorization: str | None = Header(default=None)):
    if settings.identity_required:
        raise HTTPException(409, "enterprise access uses workforce sign-in")
    return {"token": sessions.rotate(sid)}


@app.get("/sessions/{sid}/export")
@guarded()
def export_session(sid: str, authorization: str | None = Header(default=None)):
    st = state_of(sid)
    seq, head = st.policy.store.audit_head()
    return JSONResponse({"format": "atezain-export-v1", "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session": sid, "policy_fingerprint": config.fingerprint(),
        "records": [st.records.invoice(i) for i in st.records.ids()],
        "cases": st.records.cases(), "accounting": st.records.sync_status(),
        "source_snapshots": st.records.source_rows(),
        "identity_events": sessions.identity.events(sid),
        "proposals": [out(p).model_dump() for p in proposals_of(st)],
        "audit": {"rows": st.policy.store.audit_rows(), "head_seq": seq, "head_hash": head,
                  "verifies": st.policy.store.audit_verify(), "anomalies": st.policy.store.audit_anomalies()},
        "fuse": st.policy.store.fuse_get()},
        headers={"Content-Disposition": f'attachment; filename="atezain-{sid}.json"'})


def purge_session(sid):
    """Revoke before physical deletion. A failed purge remains revoked and is retryable by GC."""
    schema_of(sid)
    sessions.mark_deleted(sid)
    if xero is not None:
        xero.store.forget(sid)
    st = state_of(sid)
    conn = st.checkpointer.conn
    query = "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE " + ("%s" if DSN else "?")
    for (thread,) in conn.execute(query, (sid + ":%",)).fetchall():
        st.checkpointer.delete_thread(thread)
    if DSN:
        st.policy.store.drop_schema()
    with _pool_lock:
        _live.pop(sid, None)
    st.close()
    if not DSN:
        directory = STATE / sid
        if directory.resolve().parent != STATE.resolve():
            raise ValueError("session directory escaped state root")
        shutil.rmtree(directory)
    sessions.forget(sid)


@app.delete("/sessions/{sid}")
@guarded("admin")
def delete_session(sid: str, authorization: str | None = Header(default=None),
                   x_confirm_delete: str | None = Header(default=None)):
    if x_confirm_delete != sid:
        raise HTTPException(400, "confirm deletion with X-Confirm-Delete equal to the workspace ID")
    purge_session(sid)
    return {"deleted": True, "note": "active records, access and checkpoints removed; backup retention is controlled by the operator"}


@app.get("/capabilities")
def capabilities():
    return {"mode": settings.mode, "self_service": settings.mode == "demo", "model": MODEL_ID if MODEL == "groq" else "stub",
            "session_days": settings.session_days, "max_records": settings.max_session_records,
            "max_upload_bytes": MAX_UPLOAD_BYTES, "daily_proposals": config.daily_writes,
            "all_writes_require_approval": all(s.approval == "required" for s in config.actions.values() if not s.deny),
            "sends_email": False, "hosted_tracing": False, "retrieval": "customer",
            "oidc": oidc is not None, "identity_required": settings.identity_required}


@app.post("/server/fuse/clear")
def clear_server_fuse(x_owner_token: str | None = Header(default=None)) -> JSONResponse:
    """The SERVER's model budget, not a session's fuse. The owner's token, or nothing."""
    ok = limits.clear_fuse(x_owner_token or "", OWNER_TOKEN or None)
    return JSONResponse({"cleared": ok, "model_calls": limits.model_calls, "fuse": limits.fuse},
                        status_code=200 if ok else 403)


# ── the page, and the health check ───────────────────────────────────────────────────────────
@app.get("/")
def root() -> RedirectResponse:
    """A link shared without the path landed on a bare 404 (client simulation 2). What a root page
    should SAY is the owner's; that it should not be an error is not a question."""
    return RedirectResponse(url="/demo", status_code=307)


@app.get("/demo", response_class=HTMLResponse)
def demo() -> str:
    return (Path(__file__).resolve().parent / "demo.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz():
    try:
        sessions.exists("healthz")
        limits.model_calls
        store_ok = True
    except Exception:                                          # noqa: BLE001
        store_ok = False
    body = {"store": store_ok, "backing": "postgres" if DSN else "sqlite",
            "model": MODEL if MODEL != "groq" else MODEL_ID,
            "adapter": ADAPTER, "mode": settings.mode}
    return JSONResponse(body, status_code=200 if store_ok else 503)


@app.get("/livez")
def livez():
    return {"alive": True}


# Workforce login uses a host-only secure cookie. Provider tokens stay server-side.
@app.get("/auth/login")
def identity_login(request: Request):
    if oidc is None:
        raise HTTPException(404, "workforce sign-in is not configured")
    allowed, _ = limits.consume("login:" + (request.client.host if request.client else "?"), ((60, 10),))
    if not allowed:
        raise HTTPException(429, "sign-in rate limited", headers={"Retry-After": "60"})
    binding = secrets.token_urlsafe(32)
    try:
        response = RedirectResponse(oidc.start(sessions.identity, binding), 303)
    except (IntegrationError, ValueError, KeyError, OverflowError) as exc:
        raise HTTPException(503, "identity provider unavailable; retry sign-in") from exc
    response.set_cookie("__Host-atezain-login", binding, max_age=300, secure=True, httponly=True, samesite="lax")
    return response


@app.get("/auth/callback")
def identity_callback(request: Request, state: str = "", code: str = ""):
    if oidc is None:
        raise HTTPException(404, "workforce sign-in is not configured")
    try:
        token = oidc.finish(sessions.identity, state, request.cookies.get("__Host-atezain-login", ""), code)
    except Exception as exc:
        # Provider error descriptions and tokens may contain secrets; never reflect them.
        raise HTTPException(401, "workforce sign-in failed; start a new sign-in") from exc
    response = RedirectResponse("/demo", 303)
    response.delete_cookie("__Host-atezain-login", secure=True, httponly=True, samesite="lax")
    response.set_cookie("__Host-atezain", token, max_age=300, secure=True, httponly=True, samesite="lax")
    return response


@app.get("/auth/me")
def identity_me(request: Request):
    token = request.cookies.get("__Host-atezain")
    login = sessions.identity.login(token)
    if not login:
        raise HTTPException(401, "sign in with your workforce account")
    workspaces = [sid for sid in sessions.identity.workspaces(token) if sessions.access(sid, token)]
    return {**login, "workspaces": workspaces}


@app.post("/auth/logout")
def identity_logout(request: Request):
    sessions.identity.logout(request.cookies.get("__Host-atezain"))
    response = JSONResponse({"signed_out": True})
    response.delete_cookie("__Host-atezain", secure=True, httponly=True, samesite="lax")
    return response


from api.schemas import MemberIn


@app.put("/operator/workspaces/{sid}/members")
def operator_member(sid: str, body: MemberIn, x_owner_token: str | None = Header(default=None)):
    if not OWNER_TOKEN or not x_owner_token or not secrets.compare_digest(OWNER_TOKEN, x_owner_token):
        raise HTTPException(403, "operator credential required")
    if oidc is None or not sessions.exists(sid):
        raise HTTPException(404, "workspace or identity configuration missing")
    try:
        with sessions.lock(sid):
            sessions.identity.member(sid, oidc.config.issuer, body.subject, body.role, body.enabled, "operator")
    except BlockingIOError as exc:
        raise HTTPException(409, "workspace busy", headers={"Retry-After": "1"}) from exc
    return {"saved": True}


@app.get("/sessions/{sid}/members")
@guarded("admin")
def members(sid: str, authorization: str | None = Header(default=None)):
    return sessions.identity.members(sid)


@app.put("/sessions/{sid}/members")
@guarded("admin")
def member(sid: str, body: MemberIn, authorization: str | None = Header(default=None)):
    if oidc is None:
        raise HTTPException(409, "workforce identity is not configured")
    actor = sessions.access(sid, bearer(authorization))
    if actor.get("subject") == body.subject and (not body.enabled or body.role != "owner"):
        raise HTTPException(409, "another owner or the operator must change your own ownership")
    sessions.identity.member(sid, oidc.config.issuer, body.subject, body.role, body.enabled, actor["identity"])
    return {"saved": True}


from api.schemas import XeroConnectIn, CaseIn, AmendIn
from records.accounting import canonical as source_json, record_version


def require_current_source(st, invoice_id, proposal=None):
    source = st.records.source(invoice_id)
    if settings.identity_required and not source:
        raise HTTPException(409, "connect and sync accounting before preparing or approving this invoice")
    if source:
        connection = xero.store.get(st.sid) if xero else None
        sync = st.records.sync_status()
        if not connection or connection["state"] != "connected" or not sync or time.time()-sync["cursor"]>3600:
            raise HTTPException(409, "sync accounting before preparing or approving this record")
        if source["status"] in {"paid","cancelled"} or source["amount"] == "0.00":
            raise HTTPException(409, "accounting shows this invoice is settled or cancelled")
    current = record_version(st.records.invoice(invoice_id))
    if proposal is not None and proposal.record_version != current:
        raise HTTPException(409, "invoice evidence or follow-up plan changed; reject this proposal and prepare a fresh follow-up")
    case = st.records.case(invoice_id)
    if (proposal is None or proposal.action == "send_reminder") and (case["state"] in {"disputed","closed"} or
        (case["state"] == "snoozed" and case["next_action"] > today())):
        raise HTTPException(409, "this case is disputed, closed or deferred; update its follow-up plan before drafting")


def reconcile_accounting_invoice(st, invoice_id):
    source = st.records.source(invoice_id)
    if not source:
        return
    if xero is None:
        raise HTTPException(409, "reconnect accounting before reviewing this record")
    try:
        value,contact = xero.fetch_one(st.sid,source["source_id"])
        sources = st.records.source_rows()
        contacts = {s["customer_key"]:{"ContactID":s["customer_key"],"Name":s["customer"],"EmailAddress":s["contact"]} for s in sources.values()}
        contacts[value["customer_key"]] = contact
        sync = st.records.sync_status()
        # A single-record check cannot advance the incremental cursor for the whole organisation.
        st.records.import_accounting(sync["tenant"],[value],contacts,sync["cursor"],settings.max_session_records)
    except (IntegrationError,ValueError,KeyError,TypeError) as exc:
        error = exc if isinstance(exc,IntegrationError) else IntegrationError("source_conflict")
        xero.store.failure(st.sid,error)
        raise HTTPException(409,"accounting reconciliation failed; sync or reconnect before approval") from exc


@app.post("/sessions/{sid}/accounting/connect")
@guarded("admin")
def accounting_connect(sid: str, body: XeroConnectIn, request: Request, authorization: str | None = Header(default=None)):
    access = sessions.access(sid,bearer(authorization))
    if xero is None or not access.get("verified"):
        raise HTTPException(409, "accounting connection requires configured Xero and workforce sign-in")
    if not all(spec.approval == "required" for spec in config.actions.values() if not spec.deny):
        raise HTTPException(409, "accounting requires all-write human approval mode")
    binding = secrets.token_urlsafe(32)
    try:
        url = xero.start(sessions.identity, sid, access["identity"], binding, body.tenant)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(409, "invalid tenant or existing workspace tenant binding") from exc
    response = JSONResponse({"authorize_url":url})
    response.set_cookie("__Host-atezain-xero",binding,max_age=300,secure=True,httponly=True,samesite="lax")
    return response


@app.get("/accounting/xero/callback")
def accounting_callback(request: Request, state: str="", code: str=""):
    if xero is None:
        raise HTTPException(404,"accounting is not configured")
    try:
        attempt = sessions.identity.consume(state,request.cookies.get("__Host-atezain-xero", ""))
        if attempt.get("kind") != "xero" or not code or len(code)>8000:
            raise ValueError("invalid callback")
        sid = attempt["workspace"]
        with sessions.lock(sid):
            access = sessions.access(sid,request.cookies.get("__Host-atezain"))
            if not access or access["role"] != "owner" or access["identity"] != attempt["actor"]:
                raise ValueError("access changed during authorization")
            xero.connect(attempt,code)
    except Exception as exc:
        raise HTTPException(409,"accounting authorization failed; check workspace access and selected organisation, then reconnect") from exc
    response = RedirectResponse("/demo",303)
    response.delete_cookie("__Host-atezain-xero",secure=True,httponly=True,samesite="lax")
    return response


@app.get("/sessions/{sid}/accounting")
@guarded()
def accounting_status(sid: str, authorization: str | None = Header(default=None)):
    return accounting_of(sid, state_of(sid))


def accounting_of(sid: str, st: SessionState) -> dict:
    return {"configured":xero is not None,"connection":xero.store.get(sid) if xero else None,
            "sync":st.records.sync_status()}


def sync_accounting(st, full=False):
    if xero is None:
        raise HTTPException(409,"accounting is not configured")
    started = time.time()
    prior = st.records.sync_status()
    full = full or not prior or started-prior["full_at"]>=86400
    try:
        tenant, invoices, contacts = xero.fetch(st.sid, None if full else prior["cursor"], settings.max_session_records)
        result = st.records.import_accounting(tenant,invoices,contacts,started,settings.max_session_records,full)
    except (IntegrationError, ValueError, KeyError, TypeError) as exc:
        error = exc if isinstance(exc,IntegrationError) else IntegrationError("source_conflict")
        xero.store.failure(st.sid,error)
        raise HTTPException(409 if error.code == "source_conflict" else 503,
                            error.code + "; previous accounting snapshot retained; review connection and retry",
                            headers={"Retry-After":str(error.retry_after)}) from exc
    xero.store.success(st.sid)
    return result


@app.post("/sessions/{sid}/accounting/sync")
@guarded("admin")
def accounting_sync(sid: str, full: bool=False, authorization: str | None = Header(default=None)):
    return sync_accounting(state_of(sid),full)


@app.delete("/sessions/{sid}/accounting")
@guarded("admin")
def accounting_disconnect(sid: str, authorization: str | None = Header(default=None)):
    if xero:
        xero.store.disconnect(sid)
    return {"disconnected":True,"note":"stored tokens removed; revoke the application grant in Xero as well"}


@app.get("/sessions/{sid}/worklist")
@guarded()
def worklist(sid: str, view: str="due", authorization: str | None = Header(default=None)):
    return worklist_of(state_of(sid), sessions.access(sid,bearer(authorization)), view)


def worklist_of(st: SessionState, who_access: dict, view: str) -> dict:
    if view not in {"due","all","mine","disputed","promises"}:
        raise HTTPException(422,"invalid worklist view")
    day = today()
    cases = st.records.cases()
    rows=[]
    for invoice in st.records.summaries():
        case = cases.get(invoice["id"],st.records.empty_case(invoice["status"]))
        settled = invoice["status"] in {"paid","cancelled"} or invoice["amount"]<=0
        due = not settled and case["state"] not in {"closed","disputed"} and (case["next_action"] or invoice["due"] or "9999")<=day
        broken_promise = not settled and bool(case["promise_date"] and case["promise_date"]<day)
        assigned = case["assignee"] in {who_access.get("subject"), who_access["identity"]}
        if ((view == "due" and not due) or (view == "mine" and not assigned) or
            (view == "disputed" and case["state"] != "disputed") or (view == "promises" and not case["promise_date"])):
            continue
        rows.append({"invoice":invoice,"case":case,"due_now":due,"broken_promise":broken_promise,"settled":settled})
    rows.sort(key=lambda r:(not r["broken_promise"],r["case"]["next_action"] or r["invoice"]["due"] or "9999",r["invoice"]["id"]))
    return {"as_of":day,"rows":rows}


@app.get("/sessions/{sid}/overview")
@guarded()
def overview(sid: str, view: str = "due", authorization: str | None = Header(default=None)):
    """The whole page in one read. After every assist and every decision `api/demo.html` re-read
    seven routes in sequence — access, records, proposals, summary, audit, the work list and the
    accounting state — each taking the workspace lock, checking the token and opening the namespace
    again; and it had to be in sequence, because two requests on one workspace at once are refused
    with 409 (`guarded`). For a workspace of a few hundred records that was seven round-trips a
    refresh (STATUS.md, the 500-row question). This is the same seven reads, once, under one lock;
    each keeps its own route, and this one is built from the same functions, not copies. Reads
    only — nothing here writes, and the records come from the one-query list."""
    who(sid, authorization)
    st = state_of(sid)
    access = sessions.access(sid, bearer(authorization))
    return {"access": access_of(sid, access), "records": records_of(st), "proposals": queue_of(st),
            "summary": summary_of(st), "audit": audit_of(st), "worklist": worklist_of(st, access, view),
            "accounting": accounting_of(sid, st)}


@app.get("/sessions/{sid}/cases/{invoice_id}")
@guarded()
def case_detail(sid: str, invoice_id: str, authorization: str | None = Header(default=None)):
    st=state_of(sid)
    if st.records.invoice(invoice_id) is None:
        raise HTTPException(404,"no such invoice")
    return st.records.case(invoice_id)


@app.put("/sessions/{sid}/cases/{invoice_id}")
@guarded("review")
def save_case(sid: str, invoice_id: str, body: CaseIn, authorization: str | None = Header(default=None)):
    st=state_of(sid)
    require_healthy_audit(st)
    if st.records.invoice(invoice_id) is None:
        raise HTTPException(404,"no such invoice")
    current=st.records.case(invoice_id)
    if body.version != current["version"]:
        raise HTTPException(409,"another reviewer changed this case; reload before saving")
    for value in (body.next_action,body.promise_date):
        try:
            if value and datetime.date.fromisoformat(value).isoformat()!=value:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(422,"use a calendar date written YYYY-MM-DD") from exc
    if body.state == "snoozed" and not body.next_action:
        raise HTTPException(422,"deferred cases require a next action date")
    if bool(body.promise_amount) != bool(body.promise_date):
        raise HTTPException(422,"payment promises require both amount and date")
    value=body.model_dump()
    if body.promise_amount:
        from integrations.xero import money
        try:
            value["promise_amount"]=money(body.promise_amount)
        except ValueError as exc:
            raise HTTPException(422,"promise amount must be a nonnegative cent value") from exc
    if body.assignee and settings.identity_required:
        if body.assignee not in {m["subject"] for m in sessions.identity.members(sid) if m["enabled"] and m["role"] != "viewer"}:
            raise HTTPException(422,"assignee must be an enabled workspace reviewer or owner")
    value["version"]+=1
    human=who(sid,authorization)
    with st.policy.store.transaction():
        proposal=st.policy.propose(human,"manage_case",invoice_id,{"case_json":source_json(value)},evidence="Reviewer saved the follow-up plan")
        if proposal.status == HELD:
            proposal=st.policy.decide(proposal.id,True,human)
    # The refusal is raised OUTSIDE the transaction that wrapped propose and decide. Until 2026-09-05
    # it was raised inside, and the rollback took the policy's own DENIED row with it — and the
    # fuse trip a spent budget pulls — so a refused save left no trace in the chain and the fuse
    # never tripped through this route. A refusal is audited, never silent (PROVENANCE.md).
    if proposal.status != APPROVED:
        raise HTTPException(409,"case could not be saved: "+(proposal.reason or proposal.status))
    result=st.policy.execute(proposal.id,make_executor(st.records),human)
    if result.status != EXECUTED:
        raise HTTPException(503,"case save needs investigation; inspect the audit")
    return st.records.case(invoice_id)


@app.post("/sessions/{sid}/proposals/{pid}/amend")
@guarded("review")
def amend_reminder(sid: str, pid: str, body: AmendIn, authorization: str | None = Header(default=None)):
    st=state_of(sid)
    require_healthy_audit(st)
    old=st.policy.store.get_proposal(pid)
    if not old or old.action != "send_reminder" or old.status != HELD:
        raise HTTPException(409,"only a held reminder can be amended")
    require_current_source(st,old.record_id,old)
    human=who(sid,authorization)
    with st.policy.store.transaction():
        proposal=st.policy.propose(human,old.action,old.record_id,{**old.params,"reminder_text":body.reminder_text},evidence="Reviewer amendment of "+old.id)
        if proposal.status == HELD:
            st.policy.decide(old.id,False,human,note="Replaced by reviewer amendment "+proposal.id)
            st.policy.store.audit_append("PROPOSAL_AMENDED",human.tag,proposal.id,{"parent":old.id})
    # raised outside the transaction, for the reason `save_case` gives: the DENIED row stays
    if proposal.status != HELD:
        raise HTTPException(409,"amendment refused: "+(proposal.reason or proposal.status))
    return out(proposal)

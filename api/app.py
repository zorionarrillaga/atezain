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
from api.imports import (parse_rows, load_rows as import_rows, MAX_UPLOAD_BYTES, MAX_ROWS, COLUMNS, EXTRAS)
from agent.tracing import Tracer
from api.http import BodyLimit
from api.schemas import (ActionOut, AdapterOut, AssistOut, AuditOut, DecideIn, DecideOut, EmailOut,
                         FuseOut, NoteOut, ProposalOut, RecordDetailOut, RecordOut, RejectedRow,
                         SessionOut, UploadOut, GrantIn)
from policy import APPROVED, EXECUTED, HELD, PolicyConfig, PolicyService, Store
from records import Records

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
sessions = Sessions(STATE / "sessions.db", dsn=DSN or None)   # the token table outlives a spin-down too
sessions.ttl = settings.session_days * 86400
limits = PersistentLimits(STATE / "limits.db", dsn=DSN or None)
config = PolicyConfig.load(ROOT / "adapters" / ADAPTER / "permissions.toml")
if settings.mode == "pilot":
    from dataclasses import replace
    config = config.replace(actions={name: replace(spec, approval="required") for name, spec in config.actions.items()})
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
        response = await call_next(request)
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
    if settings.mode == "pilot":
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
        return GroqLLM(model=MODEL_ID, api_key=byok, max_output_tokens=2048), f"{MODEL_ID} (your key)", True
    if MODEL == "groq":
        return GroqLLM(model=MODEL_ID, max_output_tokens=2048), MODEL_ID, False
    return StubLLM(), "stub", False


def out(p) -> ProposalOut:
    return ProposalOut(id=p.id, action=p.action, record_id=p.record_id, params=p.params,
                       status=p.status, reason=p.reason, decided_by=p.decided_by, note=p.note,
                       evidence=p.evidence, created_at=p.created_at)


def proposals_of(st: SessionState) -> list:
    seen, rows = set(), []
    for r in st.policy.store.audit_rows():
        pid = r["proposal_id"]
        if r["kind"] != "PROPOSAL" or pid is None or pid in seen:
            continue
        seen.add(pid)
        p = st.policy.store.get_proposal(pid)
        if p is not None:
            rows.append(p)
    return rows


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
    if settings.mode == "pilot" and (not x_owner_token or not secrets.compare_digest(x_owner_token, OWNER_TOKEN)):
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


@app.get("/sessions/{sid}/records", response_model=list[RecordOut])
@guarded()
def records(sid: str, authorization: str | None = Header(default=None)) -> list[RecordOut]:
    """The visitor's own records back, which until 2026-09-03 no route returned: an `add_note` is
    auto-approved by this adapter and executes without anyone deciding, so the one write that needs
    no human was the one write nobody could read (client simulation 1, STATUS.md). Reads only —
    there is no route here that writes."""
    who(sid, authorization)
    st = state_of(sid)
    return [RecordOut(**row) for row in st.records.summaries()]


@app.get("/sessions/{sid}/summary")
@guarded()
def summary(sid: str, authorization: str | None = Header(default=None)):
    from decimal import Decimal
    from policy.model import TZ
    from policy.service import LIVE
    st = state_of(sid)
    today = datetime.datetime.now(TZ).date().isoformat()
    rows = st.records.summaries()
    outstanding = [r for r in rows if r["status"] not in {"paid", "cancelled"} and r["amount"] > 0]
    overdue = [r for r in outstanding if r["due"] and r["due"] < today]
    totals = {}
    for row in overdue:
        totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + Decimal(str(row["amount"]))
    used = st.policy.store.count_proposals_since(st.policy._day_start(st.policy.clock()), LIVE)
    return {"as_of": today, "records": len(rows), "overdue": len(overdue),
            "disputed": sum(r["status"] == "disputed" for r in outstanding),
            "undated": sum(not r["due"] for r in outstanding),
            "overdue_by_currency": {k: format(v, ".2f") for k, v in sorted(totals.items())},
            "pending": sum(p.status == HELD for p in proposals_of(st)),
            "budget_used": used, "budget_limit": config.daily_writes,
            "budget_remaining": max(config.daily_writes - used, 0) if config.daily_writes else None,
            "fuse": st.policy.store.fuse_get()}


@app.get("/sessions/{sid}/records/{invoice_id}", response_model=RecordDetailOut)
@guarded()
def record(sid: str, invoice_id: str, authorization: str | None = Header(default=None)) -> RecordDetailOut:
    """One record with its notes and emails. Every note carries WHO wrote it: `assistant` is this
    system's own voice, and a claim in one is the assistant's, not the customer's."""
    who(sid, authorization)
    inv = state_of(sid).records.invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail=f"no record {invoice_id} in this session")
    return RecordDetailOut(**record_out(inv).model_dump(),
                           note_rows=[NoteOut(**n) for n in inv["notes"]],
                           email_rows=[EmailOut(**e) for e in inv["emails"]])


# ── assist ───────────────────────────────────────────────────────────────────────────────────
@app.post("/sessions/{sid}/assist/{invoice_id}", response_model=AssistOut)
@guarded("review")
def assist(sid: str, invoice_id: str, request: Request, authorization: str | None = Header(default=None),
           x_groq_key: str | None = Header(default=None)) -> AssistOut:
    who(sid, authorization)
    ok, why = limits.allow(request.client.host if request.client else "?")
    if not ok:
        # WHICH limit, and that their own key is not the answer to it. A visitor who brought one has
        # been told they are paying their own way, and is then stopped by a limit that is about this
        # instance and not about the model — `rate_limited_minute` alone said neither (client
        # simulation 3, STATUS.md S3-7)
        raise HTTPException(status_code=429, detail=(
            f"{why}: one address gets {limits.per_minute} assists a minute and {limits.per_day} a day "
            f"on this instance. That limit is this instance's, not the model's — your own "
            f"`X-Groq-Key` is neither counted against the server's model budget nor able to lift it."),
            headers={"Retry-After": "60"})
    st = state_of(sid)
    if st.records.invoice(invoice_id) is None:
        raise HTTPException(status_code=404, detail=f"no record {invoice_id} in this session")
    require_healthy_audit(st)

    try:
        llm, model_name, byok = model_for(x_groq_key)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(503, "the model is not configured; contact the operator") from e
    conf = {"configurable": {"thread_id": f"{sid}:customer-v1:{invoice_id}"}}
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
    return [out(p) for p in proposals_of(state_of(sid))]


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
    st = state_of(sid)
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
    access = sessions.access(sid, bearer(authorization))
    return {**access, "session": sid, "adapter": ADAPTER,
            "tokens": sessions.grants(sid) if access["role"] == "owner" else []}


@app.post("/sessions/{sid}/access")
@guarded("admin")
def grant_access(sid: str, body: GrantIn, authorization: str | None = Header(default=None)):
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
    return {"token": sessions.rotate(sid)}


@app.get("/sessions/{sid}/export")
@guarded()
def export_session(sid: str, authorization: str | None = Header(default=None)):
    st = state_of(sid)
    seq, head = st.policy.store.audit_head()
    return JSONResponse({"format": "atezain-export-v1", "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session": sid, "policy_fingerprint": config.fingerprint(),
        "records": [st.records.invoice(i) for i in st.records.ids()],
        "proposals": [out(p).model_dump() for p in proposals_of(st)],
        "audit": {"rows": st.policy.store.audit_rows(), "head_seq": seq, "head_hash": head,
                  "verifies": st.policy.store.audit_verify(), "anomalies": st.policy.store.audit_anomalies()},
        "fuse": st.policy.store.fuse_get()},
        headers={"Content-Disposition": f'attachment; filename="atezain-{sid}.json"'})


def purge_session(sid):
    """Revoke before physical deletion. A failed purge remains revoked and is retryable by GC."""
    schema_of(sid)
    sessions.mark_deleted(sid)
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
            "sends_email": False, "hosted_tracing": False, "retrieval": "customer"}


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

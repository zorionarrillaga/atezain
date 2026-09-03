"""What crosses the wire (PLAN.md §4.3). Response models exist so that a field is in an answer
because someone wrote it here, not because an internal object happened to carry it."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SessionOut(BaseModel):
    session: str
    token: str = Field(description="shown once; the server keeps only its sha256")
    adapter: str
    note: str


class RejectedRow(BaseModel):
    row: int
    id: str
    why: str


class UploadOut(BaseModel):
    loaded: int
    rejected: list[RejectedRow]
    ids: list[str]


class NoteOut(BaseModel):
    ts: str
    author: str = Field(description="who wrote it: `assistant` is this system's own words")
    text: str


class EmailOut(BaseModel):
    ts: str
    direction: str
    sender: str
    subject: str
    body: str


class RecordOut(BaseModel):
    """One invoice as the person who uploaded it should be able to read it back — including what
    the assistant wrote into it without being asked (client simulation 1, STATUS.md)."""
    id: str
    customer: str
    amount: float
    currency: str
    issued: str
    due: str
    status: str
    contact: str | None = Field(default=None, description="the address of record a reminder must go to")
    reminder_to: str | None = None
    reminder_channel: str | None = None
    notes: int = 0
    emails: int = 0
    assistant_notes: int = Field(default=0, description="of `notes`, how many the assistant wrote")


class RecordDetailOut(RecordOut):
    note_rows: list[NoteOut] = []
    email_rows: list[EmailOut] = []


class ProposalOut(BaseModel):
    id: str
    action: str
    record_id: str
    params: dict
    status: str
    reason: str
    decided_by: str | None = None
    note: str = ""


class AssistOut(BaseModel):
    invoice_id: str
    summary: str
    recommendation: str
    draft: str
    proposals: list[ProposalOut]
    cached: bool = Field(description="true when the model was not asked again for this record")
    model: str


class DecideIn(BaseModel):
    approve: bool
    note: str = ""


class DecideOut(BaseModel):
    proposal: ProposalOut
    executed: bool
    applied: dict | None = None


class AuditOut(BaseModel):
    rows: list[dict]
    head_seq: int
    head_hash: str
    verifies: bool
    anomalies: list[list[str]]
    fuse: dict


class FuseOut(BaseModel):
    cleared: bool
    why: str = ""
    fuse: dict

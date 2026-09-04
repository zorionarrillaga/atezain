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
    """`why` is the SHAPE the row failed, with no value of the row in it, so that five hundred rows
    failing the same way are one line and not five hundred (client simulation 3, STATUS.md S3-1).
    `saw` is what this particular row had, which is what tells a client their export is in the
    wrong format rather than their file being unaccountably rejected."""
    row: int
    id: str
    why: str
    saw: str = ""


class UploadOut(BaseModel):
    loaded: int
    rejected: list[RejectedRow]
    ids: list[str]


class ActionOut(BaseModel):
    """One row of the adapter's permission table, as the policy reads it."""
    action: str
    record: str
    writes: list[str] = []
    approval: str
    denied: bool = False
    daily_max: int | None = None
    values: dict[str, list] = Field(default_factory=dict, description="field -> the only values permitted")
    of_the_record: dict[str, str] = Field(default_factory=dict,
                                          description="field -> the record's own field it must equal")


class AdapterOut(BaseModel):
    """What the visitor is running under. Client simulation 2 (STATUS.md): an evaluator watched ten
    proposals, six held and four executed and NONE denied — because the model behaved — and had no
    way to see what would have been refused. This is that, read from the same `PolicyConfig` the
    service checks proposals against, with the fingerprint every PROPOSAL row in the chain carries."""
    adapter: str
    version: int
    fingerprint: str
    daily_writes: int
    records: dict[str, str]
    actions: list[ActionOut]


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

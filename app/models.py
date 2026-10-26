"""Database tables. Rich nested data is stored as JSON text and validated
through the pydantic schemas in ``app.schemas`` at the edges."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Profile(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    data: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    raw_text: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    updated_at: datetime = Field(default_factory=utcnow)


JOB_STATUSES = ("new", "shortlisted", "hidden")


class Job(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_ext"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str
    url: str = ""
    title: str
    company: str = Field(default="", index=True)
    location: str = ""
    remote: bool | None = None
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    salary: str = ""
    experience_text: str = ""
    posted_at: str = ""
    apply_type: str = "unknown"
    dedupe_key: str = Field(default="", index=True)
    status: str = Field(default="new", index=True)
    score: float | None = Field(default=None, index=True)
    score_detail: str = Field(default="{}", sa_column=Column(Text, nullable=False, default="{}"))
    fetched_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Resume(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True, ondelete="SET NULL")
    title: str = ""
    method: str = "heuristic"  # "llm" | "heuristic" | "base"
    data: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    keyword_report: str = Field(default="{}", sa_column=Column(Text, nullable=False, default="{}"))
    notes: str = Field(default="[]", sa_column=Column(Text, nullable=False, default="[]"))
    cover_letter: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    pdf_path: str = ""
    docx_path: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


APPLICATION_STATUSES = (
    "preparing",  # resume being generated
    "queued",  # ready for the extension to open
    "in_progress",  # open in a browser tab right now
    "applied",
    "skipped",
    "failed",
    "interview",
    "offer",
    "rejected",
)


class Application(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", unique=True, ondelete="CASCADE")
    resume_id: int | None = Field(default=None, foreign_key="resume.id", ondelete="SET NULL")
    status: str = Field(default="preparing", index=True)
    notes: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    error: str = ""
    queue_position: int = 0
    applied_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Answer(SQLModel, table=True):
    """Screening-question memory: answers the user approved once, reused forever."""

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)  # normalized question
    question: str
    answer: str = Field(sa_column=Column(Text, nullable=False))
    uses: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = Field(default="null", sa_column=Column(Text, nullable=False))

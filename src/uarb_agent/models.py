from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class DocType(StrEnum):
    EXHIBITS = "Exhibits"
    KEY_DOCUMENTS = "Key Documents"
    OTHER_DOCUMENTS = "Other Documents"
    TRANSCRIPTS = "Transcripts"
    RECORDINGS = "Recordings"

    @property
    def slug(self) -> str:
        return self.value.lower().replace(" ", "-")


class IncomingRequest(BaseModel):
    sender: str
    subject: str
    body: str
    message_id: str | None = None


class Job(BaseModel, frozen=True):
    """One (matter, doc type) pair; each job gets its own scrape and reply."""

    matter_number: str
    doc_type: DocType


class ParsedRequest(BaseModel):
    jobs: list[Job] = []
    clarification: str | None = None  # set when part or all of the email can't be fulfilled as-is
    parsed_by: str | None = None  # "llm" or "regex"

    @property
    def is_valid(self) -> bool:
        return bool(self.jobs)


class Document(BaseModel):
    doc_no: str
    title: str
    date: str | None = None
    path: Path | None = None  # set once downloaded


class MatterInfo(BaseModel):
    matter_number: str
    title: str
    description: str | None = None
    status: str | None = None
    type: str | None = None
    category: str | None = None
    date_received: date | None = None
    date_final_submissions: date | None = None
    date_final_label: str | None = None  # site shows "Decision Date" or "Date Final Submissions"
    outcome: str | None = None
    tab_counts: dict[DocType, int] = {}


class AgentResult(BaseModel):
    reply_body: str
    zip_path: Path | None = None
    subject_suffix: str | None = None  # e.g. "[M12205 – Other Documents]"; None for clarification replies

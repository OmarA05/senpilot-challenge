"""Turn a request email into jobs. Regex only for now; an LLM path can slot in behind parse_request."""

import logging
import re

from .config import settings
from .models import DocType, Job, ParsedRequest

log = logging.getLogger(__name__)

MATTER_RE = re.compile(r"\bM\d{5}\b", re.IGNORECASE)
DOC_TYPE_PATTERNS = {
    DocType.EXHIBITS: re.compile(r"\bexhibits?\b", re.IGNORECASE),
    DocType.KEY_DOCUMENTS: re.compile(r"\bkey\s+doc(?:ument)?s?\b", re.IGNORECASE),
    DocType.OTHER_DOCUMENTS: re.compile(r"\bother\s+doc(?:ument)?s?\b", re.IGNORECASE),
    DocType.TRANSCRIPTS: re.compile(r"\btranscripts?\b", re.IGNORECASE),
    DocType.RECORDINGS: re.compile(r"\brecordings?\b", re.IGNORECASE),
}
SENTENCE_RE = re.compile(r"[.;!?\n]+")
TYPE_NAMES = ", ".join(t.value for t in DocType)


def parse_request(text: str) -> ParsedRequest:
    matters = _find_matters(text)
    types = _find_types(text)
    if not matters and not types:
        return ParsedRequest(clarification=f"I couldn't find a matter number (like M12205) or a document type ({TYPE_NAMES}) in your message.")
    if not matters:
        return ParsedRequest(clarification="I couldn't find a matter number in your message. Matter numbers start with M followed by 5 digits, like M12205.")
    if not types:
        return ParsedRequest(clarification=f"Which document type would you like for {_join(matters)}? I can fetch {TYPE_NAMES}.")

    jobs = _pair(matters, types)
    unpaired: list[str] = []
    if jobs is None:
        # Ambiguous overall (e.g. 3 matters, 2 types): pair within each sentence instead.
        jobs = []
        for sentence in SENTENCE_RE.split(text):
            jobs += _pair(_find_matters(sentence), _find_types(sentence)) or []
        unpaired = [m for m in matters if m not in {j.matter_number for j in jobs}]

    jobs = list(dict.fromkeys(jobs))
    notes = []
    if unpaired:
        notes.append(f"I wasn't sure which document type you wanted for {_join(unpaired)}. Could you reply with the type for each?")
    if len(jobs) > settings.max_jobs_per_email:
        dropped = jobs[settings.max_jobs_per_email:]
        jobs = jobs[: settings.max_jobs_per_email]
        notes.append(
            f"I can handle up to {settings.max_jobs_per_email} requests per email, so I skipped: "
            + "; ".join(f"{j.doc_type} for {j.matter_number}" for j in dropped)
            + ". Please send those in a separate email."
        )
    result = ParsedRequest(jobs=jobs, clarification=" ".join(notes) or None)
    log.info("Parsed %d job(s)%s", len(result.jobs), " with clarification" if result.clarification else "")
    return result


def _pair(matters: list[str], types: list[DocType]) -> list[Job] | None:
    """Pair matters with types; None when the pairing is ambiguous."""
    if not matters or not types:
        return []
    if len(types) == 1:
        return [Job(matter_number=m, doc_type=types[0]) for m in matters]
    if len(matters) == 1:
        return [Job(matter_number=matters[0], doc_type=t) for t in types]
    if len(matters) == len(types):
        return [Job(matter_number=m, doc_type=t) for m, t in zip(matters, types)]
    return None


def _find_matters(text: str) -> list[str]:
    return list(dict.fromkeys(m.group().upper() for m in MATTER_RE.finditer(text)))


def _find_types(text: str) -> list[DocType]:
    """Doc types in order of first appearance."""
    found = []
    for doc_type, pattern in DOC_TYPE_PATTERNS.items():
        if m := pattern.search(text):
            found.append((m.start(), doc_type))
    return [t for _, t in sorted(found)]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]

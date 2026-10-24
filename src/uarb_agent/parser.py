"""Turn a request email into jobs. Claude does the reading; regex validates its output and is the fallback."""

import json
import logging
import re
import time
from functools import cache

import anthropic

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

SYSTEM_PROMPT = f"""You read emails sent to an agent that downloads documents from the Nova Scotia Utility and Review Board (UARB) public documents database, and extract what the sender is asking for.

Each request is a matter number plus a document type. Matter numbers are the letter M followed by 5 digits (e.g. M12205). The document types are exactly: {TYPE_NAMES}. Map loose wording onto them, e.g. "exhibit list" -> Exhibits, "the key stuff" -> Key Documents, "misc/other docs" -> Other Documents, "hearing transcript" -> Transcripts, "audio" or "hearing recording" -> Recordings.

Return one job per (matter, document type) pair:
- One document type and several matters: that type applies to every matter.
- One matter and several document types: every type for that matter.
- "X from A and Y from B": pair each type with the matter it is written next to.

Only extract what the sender is asking for now. Ignore quoted reply history (lines starting with ">", "On ... wrote:" blocks, forwarded messages) and signatures. Never invent or correct a matter number; copy it exactly as written.

If something needed is missing or genuinely ambiguous, put a short, friendly question to the sender in "clarification" (one or two sentences, addressed to them), and still return any jobs that are clear. Otherwise set "clarification" to an empty string."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "matter_number": {"type": "string"},
                    "document_type": {"type": "string", "enum": [t.value for t in DocType]},
                },
                "required": ["matter_number", "document_type"],
                "additionalProperties": False,
            },
        },
        "clarification": {"type": "string"},
    },
    "required": ["jobs", "clarification"],
    "additionalProperties": False,
}


class LlmParseError(ValueError):
    pass


def parse_request(text: str, use_llm: bool | None = None, client: anthropic.Anthropic | None = None) -> ParsedRequest:
    """Claude first (when enabled and a key or client is available), regex on any failure."""
    use_llm = settings.use_llm_parser if use_llm is None else use_llm
    if use_llm and (client or settings.anthropic_api_key):
        try:
            return _parse_with_llm(text, client or _client())
        except (anthropic.APIError, LlmParseError) as e:
            log.warning("LLM parse failed (%s: %s); falling back to regex", type(e).__name__, e)
    return _parse_with_regex(text)


@cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)


def _parse_with_llm(text: str, client: anthropic.Anthropic) -> ParsedRequest:
    start = time.monotonic()
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"<email>\n{text}\n</email>"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    if response.stop_reason != "end_turn":
        raise LlmParseError(f"stop_reason={response.stop_reason}")
    raw = next((b.text for b in response.content if b.type == "text"), None)
    if raw is None:
        raise LlmParseError("no text block in response")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LlmParseError(f"invalid JSON: {e}") from e
    log.info(
        "LLM parse took %.1fs (%d in / %d out tokens)",
        time.monotonic() - start, response.usage.input_tokens, response.usage.output_tokens,
    )
    return _validate_llm_output(text, data)


def _validate_llm_output(text: str, data: object) -> ParsedRequest:
    """Keep only jobs whose matter number really appears in the email and whose type is a real tab."""
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise LlmParseError("unexpected output shape")
    in_text = set(_find_matters(text))
    jobs: list[Job] = []
    invented: list[str] = []
    for item in data["jobs"]:
        if not isinstance(item, dict):
            continue
        matter = str(item.get("matter_number", "")).strip().upper()
        try:
            doc_type = DocType(item.get("document_type"))
        except ValueError:
            log.warning("Dropping job with unknown document type %r", item.get("document_type"))
            continue
        if matter not in in_text:
            log.warning("Dropping matter number %r: not in the email", matter)
            invented.append(matter)
            continue
        jobs.append(Job(matter_number=matter, doc_type=doc_type))

    notes = []
    if clarification := str(data.get("clarification") or "").strip():
        notes.append(clarification)
    if invented:
        notes.append(f"I skipped {_join(list(dict.fromkeys(invented)))} because I couldn't match it to a matter number in your message.")
    return _finalize(jobs, notes, parsed_by="llm")


def _parse_with_regex(text: str) -> ParsedRequest:
    matters = _find_matters(text)
    types = _find_types(text)
    if not matters and not types:
        return ParsedRequest(clarification=f"I couldn't find a matter number (like M12205) or a document type ({TYPE_NAMES}) in your message.", parsed_by="regex")
    if not matters:
        return ParsedRequest(clarification="I couldn't find a matter number in your message. Matter numbers start with M followed by 5 digits, like M12205.", parsed_by="regex")
    if not types:
        return ParsedRequest(clarification=f"Which document type would you like for {_join(matters)}? I can fetch {TYPE_NAMES}.", parsed_by="regex")

    jobs = _pair(matters, types)
    notes = []
    if jobs is None:
        # Ambiguous overall (e.g. 3 matters, 2 types): pair within each sentence instead.
        jobs = []
        for sentence in SENTENCE_RE.split(text):
            jobs += _pair(_find_matters(sentence), _find_types(sentence)) or []
        if unpaired := [m for m in matters if m not in {j.matter_number for j in jobs}]:
            notes.append(f"I wasn't sure which document type you wanted for {_join(unpaired)}. Could you reply with the type for each?")
    return _finalize(jobs, notes, parsed_by="regex")


def _finalize(jobs: list[Job], notes: list[str], parsed_by: str) -> ParsedRequest:
    """Dedupe, cap at max_jobs_per_email, and make sure an empty result explains itself."""
    jobs = list(dict.fromkeys(jobs))
    if len(jobs) > settings.max_jobs_per_email:
        dropped = jobs[settings.max_jobs_per_email:]
        jobs = jobs[: settings.max_jobs_per_email]
        notes.append(
            f"I can handle up to {settings.max_jobs_per_email} requests per email, so I skipped: "
            + "; ".join(f"{j.doc_type} for {j.matter_number}" for j in dropped)
            + ". Please send those in a separate email."
        )
    if not jobs and not notes:
        notes.append(f"I couldn't find a matter number (like M12205) or a document type ({TYPE_NAMES}) in your message.")
    result = ParsedRequest(jobs=jobs, clarification=" ".join(notes) or None, parsed_by=parsed_by)
    log.info("Parsed %d job(s) via %s%s", len(result.jobs), parsed_by, " with clarification" if result.clarification else "")
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

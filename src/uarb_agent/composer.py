"""Reply text for each outcome.

Facts are gathered deterministically into ReplyFacts; Claude phrases them, and a guardrail checks every
number made it through unchanged. Any problem (no key, API error, failed check) falls back to the template.
"""

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from . import llm
from .config import settings
from .models import Document, DocType, Job, MatterInfo
from .packager import ZipResult

log = logging.getLogger(__name__)

SIGN_OFF = "\n\nBest,\nUARB Agent"
MATTER_RE = re.compile(r"\bM\d{5}\b", re.IGNORECASE)
NUMBER_WORDS = "zero one two three four five six seven eight nine ten eleven twelve".split()


class DocRef(BaseModel):
    doc_no: str
    title: str
    size_mb: float | None = None


class ReplyFacts(BaseModel):
    kind: Literal["success", "not_found", "clarification", "error"]
    sender_name: str | None = None
    matter_number: str | None = None
    doc_type: DocType | None = None
    title: str | None = None
    description: str | None = None
    type: str | None = None
    category: str | None = None
    status: str | None = None
    outcome: str | None = None
    initial_filing: str | None = None  # formatted, e.g. "April 7, 2025"
    final_filing: str | None = None
    tab_counts: dict[DocType, int] = {}
    total_in_tab: int = 0
    attempted: int = 0  # min(max_documents, total_in_tab)
    downloaded: int = 0
    attached: int = 0
    skipped_for_size: list[DocRef] = []
    failed_downloads: list[DocRef] = []
    clarification: str | None = None
    site_url: str = settings.uarb_url


# --- facts ------------------------------------------------------------------


def subject_suffix(job: Job) -> str:
    return f"[{job.matter_number} – {job.doc_type}]"


def success_facts(
    info: MatterInfo, doc_type: DocType, attempted: int, downloaded: list[Document],
    failed: list[Document], zipped: ZipResult | None, sender_name: str | None = None,
) -> ReplyFacts:
    by_path = {d.path: d for d in downloaded}
    skipped = zipped.skipped if zipped else []
    return ReplyFacts(
        kind="success", sender_name=sender_name, matter_number=info.matter_number, doc_type=doc_type,
        title=info.title or None, description=info.description, type=info.type, category=info.category,
        status=info.status, outcome=info.outcome,
        initial_filing=_fmt(info.date_received), final_filing=_fmt(info.date_final_submissions),
        tab_counts=info.tab_counts, total_in_tab=info.tab_counts.get(doc_type, 0), attempted=attempted,
        downloaded=len(downloaded), attached=len(zipped.included) if zipped else 0,
        skipped_for_size=[_ref(by_path.get(p), p) for p in skipped],
        failed_downloads=[DocRef(doc_no=d.doc_no, title=d.title) for d in failed],
    )


def not_found_facts(job: Job, sender_name: str | None = None) -> ReplyFacts:
    return ReplyFacts(kind="not_found", sender_name=sender_name, matter_number=job.matter_number, doc_type=job.doc_type)


def clarification_facts(question: str, sender_name: str | None = None) -> ReplyFacts:
    return ReplyFacts(kind="clarification", sender_name=sender_name, clarification=question)


def error_facts(job: Job, sender_name: str | None = None) -> ReplyFacts:
    return ReplyFacts(kind="error", sender_name=sender_name, matter_number=job.matter_number, doc_type=job.doc_type)


def _ref(doc: Document | None, path: Path) -> DocRef:
    size = round(path.stat().st_size / 1_000_000, 1) if path.exists() else None
    if doc:
        return DocRef(doc_no=doc.doc_no, title=doc.title, size_mb=size)
    return DocRef(doc_no=path.stem, title=path.name, size_mb=size)


# --- compose ----------------------------------------------------------------


def compose(facts: ReplyFacts, use_llm: bool | None = None, client: anthropic.Anthropic | None = None) -> tuple[str, str]:
    """Returns (reply text, "llm" | "template")."""
    use_llm = settings.use_llm_composer if use_llm is None else use_llm
    if use_llm and llm.available(client):
        try:
            text = _compose_with_llm(facts, client)
            if problems := check_reply(facts, text):
                log.warning("LLM reply failed checks (%s); using template", "; ".join(problems))
            else:
                log.info("Composed %s reply via llm", facts.kind)
                return text, "llm"
        except (anthropic.APIError, llm.LlmError) as e:
            log.warning("LLM compose failed (%s: %s); using template", type(e).__name__, e)
    log.info("Composed %s reply via template", facts.kind)
    return render_template(facts), "template"


SYSTEM_PROMPT = f"""You write short email replies for an agent that fetches documents from the Nova Scotia Utility and Review Board (UARB) public documents database. You get the facts as JSON and write the reply.

Style reference (a successful request):
"Hi User,

M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,270,000. It relates to Capital Expenditure within the Water category. The matter had an initial filing on April 7, 2025 and a final filing on October 23, 2025. I found 13 Exhibits, 5 Key Documents, 21 Other Documents, and no Transcripts or Recordings. I downloaded 10 out of the 21 Other Documents and am attaching them as a ZIP here."

Rules:
- Use only the facts given. Never invent or round numbers, dates, titles or matter numbers, and don't mention any matter number other than the one in the facts (or ones inside "clarification").
- Write every count as digits (e.g. "1 Exhibit", "43 Other Documents"), including every non-zero count in "tab_counts", "total_in_tab" and "attached".
- Greet "Hi <sender_name>," or "Hi there," if there is no name. Plain text only: no markdown, bold or headings. Bulleted lines starting with "- " are fine for lists of documents.
- kind "success": follow the reference. Mention outcome/status briefly only if present. If attached is 0 because total_in_tab is 0, say there are none of that type so there's nothing to attach. If attached < attempted, explain why using skipped_for_size (too large for email; list each with doc_no, title and size_mb, and say they can be downloaded from site_url) and failed_downloads (list each with doc_no and title).
- kind "not_found": the matter number wasn't found in the database; ask them to double-check it.
- kind "clarification": pass on the question in "clarification", in your own friendly words, keeping any matter numbers and document types it names.
- kind "error": something went wrong fetching that matter/document type; suggest trying again later.
- Do not write a sign-off; it is added automatically."""


def _compose_with_llm(facts: ReplyFacts, client: anthropic.Anthropic | None) -> str:
    payload = facts.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    text = llm.complete(SYSTEM_PROMPT, f"<facts>\n{json.dumps(payload, indent=1)}\n</facts>", client, label="Compose")
    return text.strip() + SIGN_OFF


def check_reply(facts: ReplyFacts, text: str) -> list[str]:
    """Deterministic guardrail on an LLM reply: every key number present, no stray matter numbers."""
    problems = []
    required: list[str] = []
    if facts.matter_number:
        required.append(facts.matter_number)
    if facts.kind == "success":
        numbers = [n for n in facts.tab_counts.values() if n] + [facts.total_in_tab, facts.attached]
        for n in dict.fromkeys(n for n in numbers if n):
            if not _has_number(text, n):
                problems.append(f"missing count {n}")
        required += [d.doc_no for d in facts.skipped_for_size + facts.failed_downloads]
    for token in required:
        if not re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
            problems.append(f"missing {token}")
    allowed = {facts.matter_number} | {m.upper() for m in MATTER_RE.findall(facts.clarification or "")}
    if stray := {m.upper() for m in MATTER_RE.findall(text)} - allowed:
        problems.append(f"unexpected matter number(s) {', '.join(sorted(stray))}")
    if "**" in text or re.search(r"^#", text, re.MULTILINE):
        problems.append("markdown formatting")
    if not facts.attached and _claims_attachment(text):
        problems.append("claims an attachment but none is attached")
    return problems


ATTACH_CLAIM_RE = re.compile(r"\battach(?:ed|ing)\b|\bplease find\b|\benclosed\b|\bhere(?:'s| is) the zip\b", re.IGNORECASE)
NEGATION_RE = re.compile(r"\b(?:not|no|nothing|none|never|without|couldn't|can't|wasn't|weren't|isn't|aren't)\b|n't\b", re.IGNORECASE)


def _claims_attachment(text: str) -> bool:
    """True if the text says something is attached, ignoring negated mentions like "nothing is attached"."""
    for m in ATTACH_CLAIM_RE.finditer(text):
        window = text[max(0, m.start() - 40):m.start()]
        window = re.split(r"[.!?\n]", window)[-1]  # same sentence only
        if not NEGATION_RE.search(window):
            return True
    return False


def _has_number(text: str, n: int) -> bool:
    if re.search(rf"(?<![\d,.$]){n}(?![\d,])", text):
        return True
    return n < len(NUMBER_WORDS) and re.search(rf"\b{NUMBER_WORDS[n]}\b", text, re.IGNORECASE) is not None


# --- template ---------------------------------------------------------------


def render_template(facts: ReplyFacts) -> str:
    match facts.kind:
        case "success":
            body = _success_body(facts)
        case "not_found":
            body = f"I couldn't find matter {facts.matter_number} in the UARB public documents database. Could you double-check the matter number?"
        case "clarification":
            body = facts.clarification or ""
        case _:
            body = (
                f"Something went wrong while fetching {facts.doc_type} for {facts.matter_number}, so I couldn't complete this request. "
                "Please try again in a little while."
            )
    return f"Hi {facts.sender_name or 'there'},\n\n{body}{SIGN_OFF}"


def failure_text(sender_name: str | None = None) -> str:
    """Last-resort reply when a whole email couldn't be handled (no facts to compose from)."""
    return (
        f"Hi {sender_name or 'there'},\n\nSomething went wrong while handling your request, so I couldn't complete it. "
        f"Please try again in a little while.{SIGN_OFF}"
    )


def _success_body(f: ReplyFacts) -> str:
    sentences = [f"{f.matter_number} is about {f.title or 'an untitled matter'}."]
    if f.category and f.type:
        sentences.append(f"It relates to {f.category} within the {f.type} category.")
    elif f.category or f.type:
        sentences.append(f"It falls under {f.category or f.type}.")
    if f.initial_filing and f.final_filing:
        sentences.append(f"The matter had an initial filing on {f.initial_filing} and a final filing on {f.final_filing}.")
    elif f.initial_filing:
        sentences.append(f"The matter had an initial filing on {f.initial_filing}.")
    sentences.append(_counts_sentence(f.tab_counts))
    sentences.append(_download_sentence(f))
    body = " ".join(s for s in sentences if s)

    if f.skipped_for_size:
        body += (
            f"\n\n{'This one was' if len(f.skipped_for_size) == 1 else 'These were'} too large to fit under the email size limit, "
            f"but you can download {'it' if len(f.skipped_for_size) == 1 else 'them'} from the UARB site ({f.site_url}):\n"
            + "\n".join(_doc_line(d) for d in f.skipped_for_size)
        )
    if f.failed_downloads:
        body += (
            f"\n\n{'This one' if len(f.failed_downloads) == 1 else 'These'} couldn't be downloaded from the site this time:\n"
            + "\n".join(_doc_line(d) for d in f.failed_downloads)
        )
    return body


def _doc_line(d: DocRef) -> str:
    return f"- {d.doc_no} – {d.title}" + (f" ({d.size_mb:g} MB)" if d.size_mb else "")


def _counts_sentence(counts: dict[DocType, int]) -> str:
    found = [_count(t, counts[t]) for t in DocType if counts.get(t)]
    missing = [t.value for t in DocType if t in counts and not counts[t]]
    if not found:
        return f"I didn't find any {_join(missing, 'or')} for this matter." if missing else ""
    if missing:
        found.append(f"no {_join(missing, 'or')}")
    return f"I found {_join(found, 'and')}."


def _download_sentence(f: ReplyFacts) -> str:
    doc_type, total, attached = f.doc_type, f.total_in_tab, f.attached
    if total == 0:
        return f"There are no {doc_type} for this matter, so there's nothing to attach."
    if attached == 0:
        if f.skipped_for_size and not f.failed_downloads:
            return f"I downloaded {f.downloaded} of the {total} {doc_type}, but they were all too large to attach."
        return f"Unfortunately I wasn't able to download any of the {doc_type} this time. Please try again later."
    if attached == total == 1:
        return f"I downloaded the only {_singular(doc_type)} (1 of 1) and am attaching it as a ZIP here."
    if attached == total:
        return f"I downloaded all {total} {doc_type} and am attaching them as a ZIP here."
    return f"I downloaded {attached} out of the {total} {doc_type} and am attaching {'it' if attached == 1 else 'them'} as a ZIP here."


def _count(doc_type: DocType, n: int) -> str:
    return f"{n} {_singular(doc_type) if n == 1 else doc_type.value}"


def _singular(doc_type: DocType) -> str:
    return doc_type.value.removesuffix("s")


def _join(items: list[str], conj: str) -> str:
    if len(items) <= 2:
        return f" {conj} ".join(items)
    return ", ".join(items[:-1]) + f", {conj} " + items[-1]


def _fmt(d: date | None) -> str | None:
    return f"{d:%B} {d.day}, {d.year}" if d else None

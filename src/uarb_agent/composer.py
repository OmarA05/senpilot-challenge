"""Reply text for each outcome. Template-only for now; Claude can rephrase later (Chunk 6)."""

from datetime import date

from .models import Document, DocType, Job, MatterInfo
from .packager import ZipResult

GREETING = "Hi,\n\n"
SIGN_OFF = "\n\nBest,\nUARB Agent"


def subject_suffix(job: Job) -> str:
    return f"[{job.matter_number} – {job.doc_type}]"


def success_reply(info: MatterInfo, doc_type: DocType, requested: int, downloaded: list[Document], zipped: ZipResult | None) -> str:
    """`requested` is how many documents we tried to download (min(limit, tab count))."""
    sentences = [f"{info.matter_number} is about {info.title or 'an untitled matter'}."]
    if info.category and info.type:
        sentences.append(f"It relates to {info.category} within the {info.type} category.")
    elif info.category or info.type:
        sentences.append(f"It falls under {info.category or info.type}.")
    if info.date_received and info.date_final_submissions:
        sentences.append(f"The matter had an initial filing on {_fmt(info.date_received)} and a final filing on {_fmt(info.date_final_submissions)}.")
    elif info.date_received:
        sentences.append(f"The matter had an initial filing on {_fmt(info.date_received)}.")
    sentences.append(_counts_sentence(info.tab_counts))
    sentences.append(_download_sentence(doc_type, info.tab_counts.get(doc_type, 0), requested, downloaded, zipped))
    return _wrap(" ".join(s for s in sentences if s))


def not_found_reply(matter_number: str) -> str:
    return _wrap(f"I couldn't find matter {matter_number} in the UARB public documents database. Could you double-check the matter number?")


def clarification_reply(question: str) -> str:
    return _wrap(question)


def error_reply(job: Job) -> str:
    return _wrap(
        f"Something went wrong while fetching {job.doc_type} for {job.matter_number}, so I couldn't complete this request. "
        "Please try again in a little while."
    )


def _counts_sentence(counts: dict[DocType, int]) -> str:
    found = [_count(t, counts[t]) for t in DocType if counts.get(t)]
    missing = [t.value for t in DocType if t in counts and not counts[t]]
    if not found:
        return f"I didn't find any {_join(missing, 'or')} for this matter." if missing else ""
    if missing:
        found.append(f"no {_join(missing, 'or')}")
    return f"I found {_join(found, 'and')}."


def _download_sentence(doc_type: DocType, total: int, requested: int, downloaded: list[Document], zipped: ZipResult | None) -> str:
    if total == 0:
        return f"Since there are no {doc_type}, there's nothing to attach."
    attached = len(zipped.included) if zipped else 0
    if attached == 0:
        return f"Unfortunately I wasn't able to download any of the {doc_type} this time. Please try again later."

    if attached == total == 1:
        parts = [f"I downloaded the only {_singular(doc_type)} and am attaching it as a ZIP here."]
    elif attached == total:
        parts = [f"I downloaded all {total} {doc_type} and am attaching them as a ZIP here."]
    else:
        parts = [f"I downloaded {attached} out of the {total} {doc_type} and am attaching {'it' if attached == 1 else 'them'} as a ZIP here."]
    if failed := requested - len(downloaded):
        parts.append(f"{_files(failed)} couldn't be downloaded from the site.")
    if skipped := len(zipped.skipped) if zipped else 0:
        parts.append(f"{_files(skipped)} {'was' if skipped == 1 else 'were'} left out to keep the attachment under the email size limit.")
    return " ".join(parts)


def _count(doc_type: DocType, n: int) -> str:
    return f"{n} {_singular(doc_type) if n == 1 else doc_type.value}"


def _singular(doc_type: DocType) -> str:
    return doc_type.value.removesuffix("s")


def _files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def _join(items: list[str], conj: str) -> str:
    if len(items) <= 2:
        return f" {conj} ".join(items)
    return ", ".join(items[:-1]) + f", {conj} " + items[-1]


def _fmt(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _wrap(body: str) -> str:
    return GREETING + body + SIGN_OFF

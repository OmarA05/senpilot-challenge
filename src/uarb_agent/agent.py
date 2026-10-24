"""Orchestration: request text in, one reply per job out; and the inbox pass that emails them. Never raises."""

import imaplib
import logging
import re
import shutil
import smtplib
import socket
import ssl
from pathlib import Path
from typing import Callable

from . import composer, email_in, email_out
from .email_in import EmailAuthError
from .composer import ReplyFacts
from .config import settings
from .models import AgentResult, Document, IncomingRequest, Job, MatterInfo
from .packager import make_zip
from .parser import parse_request
from .scraper import MatterNotFound, UarbScraper

log = logging.getLogger(__name__)

# Network trouble worth retrying later (as opposed to a bad message or a rejected reply).
CONNECTION_ERRORS = (
    ConnectionError, TimeoutError, socket.gaierror, ssl.SSLError, imaplib.IMAP4.abort,
    smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
)


def handle_request(text: str, sender_name: str | None = None) -> list[AgentResult]:
    parsed = parse_request(text)
    results: list[AgentResult] = []
    if parsed.jobs:
        # Metadata per matter, so asking for two doc types from one matter reads the header once.
        infos: dict[str, MatterInfo | None] = {}
        try:
            with UarbScraper() as scraper:
                for job in parsed.jobs:
                    results.append(_run_job(scraper, job, infos, sender_name))
        except Exception:
            log.exception("Browser failed")
        # Jobs that never ran because the browser itself failed still get a reply.
        for job in parsed.jobs[len(results):]:
            results.append(_result(composer.error_facts(job, sender_name), job))
    if parsed.clarification:
        results.append(_result(composer.clarification_facts(parsed.clarification, sender_name)))
    return results


def _run_job(scraper: UarbScraper, job: Job, infos: dict[str, MatterInfo | None], sender_name: str | None) -> AgentResult:
    try:
        if job.matter_number in infos and infos[job.matter_number] is None:
            raise MatterNotFound(job.matter_number)
        # The page stays on the last matter, so only search again when switching matters.
        if scraper.matter_number != job.matter_number:
            scraper.open_matter(job.matter_number)
        if job.matter_number not in infos:
            infos[job.matter_number] = scraper.get_metadata()
        info = infos[job.matter_number]
        total = info.tab_counts.get(job.doc_type, 0)
        attempted = min(settings.max_documents, total)

        dest = settings.download_dir / job.matter_number / job.doc_type.slug
        zip_path = settings.download_dir / f"{job.matter_number}_{job.doc_type.slug}.zip"
        shutil.rmtree(dest, ignore_errors=True)
        zip_path.unlink(missing_ok=True)

        failed: list[Document] = []
        downloaded = scraper.download_documents(job.doc_type, attempted, dest, failed=failed) if attempted else []
        zipped = make_zip([d.path for d in downloaded], zip_path) if downloaded else None
        facts = composer.success_facts(info, job.doc_type, attempted, downloaded, failed, zipped, sender_name)
        return _result(facts, job, zipped.path if zipped else None, dest)
    except MatterNotFound:
        log.info("Matter %s not found", job.matter_number)
        infos[job.matter_number] = None
        scraper.matter_number = None  # the failed search left the page on the landing screen
        return _result(composer.not_found_facts(job, sender_name), job)
    except Exception:
        log.exception("Job %s failed", composer.subject_suffix(job))
        scraper.matter_number = None  # page state unknown; force a fresh search next time
        return _result(composer.error_facts(job, sender_name), job)


def _result(facts: ReplyFacts, job: Job | None = None, zip_path: Path | None = None, files_dir: Path | None = None) -> AgentResult:
    body, composed_by = composer.compose(facts)
    return AgentResult(
        reply_body=body, zip_path=zip_path, files_dir=files_dir, composed_by=composed_by,
        subject_suffix=composer.subject_suffix(job) if job else None,
    )


SUFFIX_RE = re.compile(r"\s*\[M\d{5} – [^\]]*\]")  # our own subject suffix, if they reply to a reply


def request_text(req: IncomingRequest) -> str:
    """What the parser sees: the subject (requests are often written there) plus the stripped body."""
    subject = SUFFIX_RE.sub("", req.subject).strip()
    return f"Subject: {subject}\n\n{req.body}" if subject else req.body


def process_inbox_once(subject_contains: str | None = None, should_stop: Callable[[], bool] = lambda: False) -> int:
    """Handle every unread request: reply to each job, then mark the email processed. Returns emails handled.

    Raises EmailAuthError and CONNECTION_ERRORS so a caller can back off; everything else is handled per email.
    Stops between emails once should_stop() is true; the rest stay unread for next time.
    """
    requests = email_in.fetch_unread(subject_contains=subject_contains)
    handled = 0
    for req in requests:
        if should_stop():
            log.info("Stopping; %d email(s) left unread for next time", len(requests) - handled)
            break
        process_request(req)
        handled += 1
    return handled


def process_request(req: IncomingRequest) -> None:
    """Reply to one email and mark it processed. A failed request still gets an error reply.

    If no reply could be sent because the network is down, the email stays unread (to retry later) and the
    connection error is raised; any other send failure still marks it processed: one missed reply beats a loop.
    """
    log.info("Handling %r from %s", req.subject, req.sender_email)
    try:
        results = handle_request(request_text(req), req.sender_name)
    except Exception:
        log.exception("Request %r failed", req.subject)
        results = [AgentResult(reply_body=composer.failure_text(req.sender_name), composed_by="template")]

    sent = 0
    network_error: Exception | None = None
    for result in results:
        try:
            email_out.send_reply(req, result)
        except EmailAuthError:
            raise
        except CONNECTION_ERRORS as e:
            log.warning("Couldn't send reply %s: %s", result.subject_suffix or "[clarification]", e)
            network_error = e
            continue
        except Exception:
            log.exception("Couldn't send reply %s", result.subject_suffix or "[clarification]")
            continue
        sent += 1
        cleanup_result(result)

    if network_error and not sent:
        raise network_error
    if req.uid:
        try:
            email_in.mark_processed(req.uid)
        except EmailAuthError:
            raise
        except Exception:
            log.exception("Couldn't mark %s processed", req.uid)


def cleanup_result(result: AgentResult) -> None:
    """Delete a sent reply's ZIP and downloads (and the matter folder once empty)."""
    if result.zip_path:
        result.zip_path.unlink(missing_ok=True)
    if result.files_dir:
        shutil.rmtree(result.files_dir, ignore_errors=True)
        try:
            result.files_dir.parent.rmdir()  # downloads/<matter>/, only if no other doc type is still there
        except OSError:
            pass

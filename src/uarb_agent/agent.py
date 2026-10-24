"""Orchestration: request text in, one reply per job out. Never raises."""

import logging
import shutil
from pathlib import Path

from . import composer
from .composer import ReplyFacts
from .config import settings
from .models import AgentResult, Document, Job, MatterInfo
from .packager import make_zip
from .parser import parse_request
from .scraper import MatterNotFound, UarbScraper

log = logging.getLogger(__name__)


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
        return _result(facts, job, zipped.path if zipped else None)
    except MatterNotFound:
        log.info("Matter %s not found", job.matter_number)
        infos[job.matter_number] = None
        scraper.matter_number = None  # the failed search left the page on the landing screen
        return _result(composer.not_found_facts(job, sender_name), job)
    except Exception:
        log.exception("Job %s failed", composer.subject_suffix(job))
        scraper.matter_number = None  # page state unknown; force a fresh search next time
        return _result(composer.error_facts(job, sender_name), job)


def _result(facts: ReplyFacts, job: Job | None = None, zip_path: Path | None = None) -> AgentResult:
    body, composed_by = composer.compose(facts)
    return AgentResult(
        reply_body=body, zip_path=zip_path, composed_by=composed_by,
        subject_suffix=composer.subject_suffix(job) if job else None,
    )

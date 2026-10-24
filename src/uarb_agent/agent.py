"""Orchestration: request text in, one reply per job out. Never raises."""

import logging
import shutil

from . import composer
from .config import settings
from .models import AgentResult, Job
from .packager import make_zip
from .parser import parse_request
from .scraper import MatterNotFound, UarbScraper

log = logging.getLogger(__name__)


def handle_request(text: str) -> list[AgentResult]:
    parsed = parse_request(text)
    results: list[AgentResult] = []
    if parsed.jobs:
        try:
            with UarbScraper() as scraper:
                for job in parsed.jobs:
                    results.append(_run_job(scraper, job))
        except Exception:
            log.exception("Browser failed")
        # Jobs that never ran because the browser itself failed still get a reply.
        for job in parsed.jobs[len(results):]:
            results.append(AgentResult(reply_body=composer.error_reply(job), subject_suffix=composer.subject_suffix(job)))
    if parsed.clarification:
        results.append(AgentResult(reply_body=composer.clarification_reply(parsed.clarification)))
    return results


def _run_job(scraper: UarbScraper, job: Job) -> AgentResult:
    suffix = composer.subject_suffix(job)
    try:
        scraper.open_matter(job.matter_number)
        info = scraper.get_metadata()
        total = info.tab_counts.get(job.doc_type, 0)
        requested = min(settings.max_documents, total)

        dest = settings.download_dir / job.matter_number / job.doc_type.slug
        zip_path = settings.download_dir / f"{job.matter_number}_{job.doc_type.slug}.zip"
        shutil.rmtree(dest, ignore_errors=True)
        zip_path.unlink(missing_ok=True)

        downloaded = scraper.download_documents(job.doc_type, requested, dest) if requested else []
        zipped = make_zip([d.path for d in downloaded], zip_path) if downloaded else None
        body = composer.success_reply(info, job.doc_type, requested, downloaded, zipped)
        return AgentResult(reply_body=body, zip_path=zipped.path if zipped else None, subject_suffix=suffix)
    except MatterNotFound:
        log.info("Matter %s not found", job.matter_number)
        return AgentResult(reply_body=composer.not_found_reply(job.matter_number), subject_suffix=suffix)
    except Exception:
        log.exception("Job %s failed", suffix)
        return AgentResult(reply_body=composer.error_reply(job), subject_suffix=suffix)

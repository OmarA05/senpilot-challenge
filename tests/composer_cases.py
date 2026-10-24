"""Canned ReplyFacts shared by the composer tests and scripts/try_composer.py."""

from uarb_agent.composer import DocRef, ReplyFacts
from uarb_agent.models import DocType

E, K, O, T, R = DocType.EXHIBITS, DocType.KEY_DOCUMENTS, DocType.OTHER_DOCUMENTS, DocType.TRANSCRIPTS, DocType.RECORDINGS

M12205 = dict(
    matter_number="M12205",
    title="Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000",
    type="Water",
    category="Capital Expenditure Approvals",
    status="Closed",
    outcome="Directions given",
    initial_filing="April 7, 2025",
    final_filing="October 23, 2025",
    tab_counts={E: 13, K: 6, O: 43, T: 0, R: 0},
)


def success(**overrides) -> ReplyFacts:
    return ReplyFacts(**{"kind": "success", "doc_type": O, "total_in_tab": 43, "attempted": 10, "downloaded": 10, "attached": 10, **M12205, **overrides})


def zero_docs() -> ReplyFacts:
    return success(doc_type=T, total_in_tab=0, attempted=0, downloaded=0, attached=0)


def skipped_for_size() -> ReplyFacts:
    return success(
        doc_type=E, total_in_tab=13, downloaded=10, attached=6,
        skipped_for_size=[
            DocRef(doc_no="H-1", title="HRWC Application", size_mb=48.2),
            DocRef(doc_no="H-3", title="HRWC Responses to Board IRs", size_mb=23.5),
            DocRef(doc_no="H-4", title="HRWC Responses to CA IRs", size_mb=31.0),
            DocRef(doc_no="H-5", title="HRWC Rebuttal Evidence", size_mb=27.9),
        ],
    )


def failed_download() -> ReplyFacts:
    return success(downloaded=9, attached=9, failed_downloads=[DocRef(doc_no="102417", title="CA (Board) Letter - Comments on Compliance Filing")])


def not_found() -> ReplyFacts:
    return ReplyFacts(kind="not_found", matter_number="M00000", doc_type=E)


def clarification() -> ReplyFacts:
    return ReplyFacts(kind="clarification", clarification="Which document type would you like for M12383? I can fetch Exhibits, Key Documents, Other Documents, Transcripts, Recordings.")


CASES = {
    "success": success,
    "zero_docs": zero_docs,
    "skipped_for_size": skipped_for_size,
    "failed_download": failed_download,
    "not_found": not_found,
    "clarification": clarification,
}
